"""FastAPI application factory for the metascan backend."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import (
    get_models_config,
    get_server_config,
    load_app_config,
)
from backend.api import (
    media,
    filters,
    scan,
    similarity,
    duplicates,
    upscale,
    config,
    embeddings,
    models,
    websocket,
)
from backend.dependencies import get_db, get_thumbnail_cache
from backend.ws.manager import ws_manager
from metascan.core.inference_client import InferenceClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _inject_huggingface_token(token: str) -> None:
    """Expose ``token`` as HF_TOKEN / HUGGING_FACE_HUB_TOKEN so that
    huggingface_hub picks it up both in the server process and in any
    subprocess that inherits the environment."""
    if not token:
        return
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token


def _wire_inference_client_status(client: InferenceClient) -> None:
    """Bridge inference client state transitions onto the ``models`` WS
    channel so the frontend can render a loading indicator and gate
    content-search submit until the worker is ready."""

    def on_status(_state: str, payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync("models", "inference_status", payload)

    def on_progress(payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync("models", "inference_progress", payload)

    client.on_status(on_status)
    client.on_progress(on_progress)


async def _event_loop_heartbeat() -> None:
    """Warn when the asyncio loop stalls — a synchronous call on the loop
    thread (CPU-bound work not wrapped in ``to_thread``, a blocking I/O
    call, a GIL-holding C extension) will prevent this coroutine from
    waking on schedule. We ask for a 1 s sleep and report whenever we
    wake up >100 ms late.

    Catches the class of problem that makes every incoming HTTP request
    appear to hang for tens of seconds while the loop is frozen."""
    interval = 1.0
    threshold_ms = 100.0
    last = time.perf_counter()
    hb_logger = logging.getLogger(__name__ + ".heartbeat")
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        now = time.perf_counter()
        drift_ms = (now - last - interval) * 1000
        if drift_ms > threshold_ms:
            hb_logger.warning(
                "event loop stalled: slept %.0fms (%.0fms late)",
                (now - last) * 1000,
                drift_ms,
            )
        last = now


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ws_manager.attach_loop(asyncio.get_running_loop())
    heartbeat_task = asyncio.create_task(_event_loop_heartbeat())

    # Warm the singletons before we start accepting requests. Both do
    # synchronous work (SQLite schema migration + index builds for the
    # DB, directory creation + ffmpeg lookup for the thumbnail cache) so
    # running them via `asyncio.to_thread` keeps the lifespan free to
    # handle other startup steps in parallel and, crucially, keeps the
    # first incoming request from paying the init cost on the asyncio
    # thread.
    t0 = time.perf_counter()
    await asyncio.gather(
        asyncio.to_thread(get_db),
        asyncio.to_thread(get_thumbnail_cache),
    )
    logger.info(
        "Warmed DB + thumbnail cache singletons in %.0fms",
        (time.perf_counter() - t0) * 1000,
    )

    app_config = load_app_config()
    models_cfg = get_models_config(app_config)
    _inject_huggingface_token(models_cfg["huggingface_token"])

    sim_cfg = app_config.get("similarity", {}) or {}
    clip_model_key = str(sim_cfg.get("clip_model") or "small")
    device = str(sim_cfg.get("device") or "auto")

    client = InferenceClient()
    _wire_inference_client_status(client)
    similarity.set_inference_client(client)

    # Preload the inference worker eagerly when the user has opted in for
    # the currently-selected CLIP model. Non-blocking so the server comes
    # up immediately.
    preload_list = models_cfg["preload_at_startup"]
    if f"clip-{clip_model_key}" in preload_list:
        logger.info(
            "Preloading inference worker at startup (model=%s, device=%s)",
            clip_model_key,
            device,
        )

        async def _preload() -> None:
            try:
                await client.start(
                    model_key=clip_model_key, device=device, wait_ready=False
                )
            except Exception:
                logger.exception("Inference worker preload failed")

        asyncio.create_task(_preload())

    upscale.init_upscale_queue()
    similarity.init_embedding_queue()

    try:
        yield
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        await upscale.shutdown_upscale_queue()
        await similarity.shutdown_embedding_queue()
        try:
            await client.shutdown()
        except Exception:
            logger.exception("Inference client shutdown raised")


class _RequestTimingMiddleware:
    """ASGI middleware that times every /api/ request at three points:

    * arrive  — when uvicorn dispatches the request into the app,
    * to_start — handler returns and first response byte is queued,
    * flush   — response body fully written to the transport.

    Logs are emitted as ``asgi arrive`` / ``asgi done`` so they line up
    against the client-side ``[perf]`` timers. If ``to_start`` matches the
    handler self-time but the client sees a much larger TTFB, the delay
    is downstream (Vite proxy, WSL loopback, browser pool). If
    ``to_start`` itself is huge, the event loop is stalled — something
    synchronous is running on the asyncio thread.

    Non-API routes (static assets, /ws, thumbnails) skip logging to keep
    the signal/noise ratio high.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._log = logging.getLogger(__name__ + ".timing")

    async def __call__(
        self,
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        t_arrive = time.perf_counter()
        method = str(scope.get("method", ""))
        state: Dict[str, float] = {}

        async def timed_send(message: Dict[str, Any]) -> None:
            mtype = message.get("type")
            if mtype == "http.response.start" and "start" not in state:
                state["start"] = time.perf_counter()
            elif mtype == "http.response.body" and not message.get("more_body", False):
                state["end"] = time.perf_counter()
            await send(message)

        self._log.info("asgi arrive %s %s", method, path)
        try:
            await self.app(scope, receive, timed_send)
        finally:
            t_end = time.perf_counter()

            def fmt(a: float, b: float) -> str:
                return f"{(b - a) * 1000:.1f}ms"

            start = state.get("start")
            end = state.get("end")
            to_start = fmt(t_arrive, start) if start else "n/a"
            flush = fmt(start, end) if start and end else "n/a"
            total = (t_end - t_arrive) * 1000
            self._log.info(
                "asgi done %s %s: to_start=%s flush=%s total=%.1fms",
                method,
                path,
                to_start,
                flush,
                total,
            )


def create_app() -> FastAPI:
    server_config = get_server_config()

    app = FastAPI(
        title="Metascan API",
        description="Backend API for the Metascan media browser",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Request-phase timing (runs before other middleware so the `arrive`
    # log is as close to uvicorn dispatch as we can get).
    app.add_middleware(_RequestTimingMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=server_config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key authentication middleware
    if server_config.api_key:

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Skip auth for non-API routes (frontend, docs, health, WebSocket)
            path = request.url.path
            if (
                not path.startswith("/api/")
                or path in ("/docs", "/openapi.json", "/health", "/ws")
                or request.headers.get("upgrade") == "websocket"
            ):
                return await call_next(request)

            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid Authorization header"},
                )
            token = auth_header[7:]
            if token != server_config.api_key:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid API key"},
                )
            return await call_next(request)

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Register routers
    app.include_router(media.router)
    app.include_router(filters.router)
    app.include_router(scan.router)
    app.include_router(similarity.router)
    app.include_router(duplicates.router)
    app.include_router(upscale.router)
    app.include_router(config.router)
    app.include_router(embeddings.router)
    app.include_router(models.router)
    app.include_router(websocket.router)

    # Serve Vue frontend production build (npm run build -> frontend/dist/)
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        # Serve static assets (JS, CSS, fonts) at /assets/
        assets_dir = frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # Serve index.html for all non-API routes (SPA fallback)
        index_html = frontend_dist / "index.html"

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Serve actual files from dist/ if they exist (favicon, icons, etc.)
            file_path = frontend_dist / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index_html)

        logger.info(f"Serving frontend from {frontend_dist}")
    else:
        logger.info("No frontend/dist/ found — run 'cd frontend && npm run build'")

    logger.info(f"Metascan API ready on {server_config.host}:{server_config.port}")

    return app


app = create_app()
