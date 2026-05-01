"""FastAPI application factory for the metascan backend."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict, Any

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
    folders,
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

        # Also warm the FAISS index in the main process. The first search
        # call otherwise triggers a synchronous import of faiss + torch +
        # open_clip + imagehash on the event loop thread (~20 s stall)
        # plus a read of the on-disk index, duplicating work the user
        # already opted into by enabling preload.
        async def _preload_faiss() -> None:
            try:
                t1 = time.perf_counter()
                await asyncio.to_thread(similarity.warm_faiss_index)
                logger.info(
                    "Warmed FAISS index in %.0fms",
                    (time.perf_counter() - t1) * 1000,
                )
            except Exception:
                logger.exception("FAISS index preload failed")

        asyncio.create_task(_preload_faiss())

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


def create_app() -> FastAPI:  # noqa: C901
    server_config = get_server_config()

    app = FastAPI(
        title="Metascan API",
        description="Backend API for the Metascan media browser",
        version="1.0.0",
        lifespan=lifespan,
    )

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
    app.include_router(folders.router)
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
