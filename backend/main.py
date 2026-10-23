"""FastAPI application factory for the metascan backend."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import (
    get_comfy_config,
    get_models_config,
    get_server_config,
    get_t2i_config,
    get_taste_config,
    load_app_config,
)
from backend.api import (
    media,
    filters,
    folders,
    judgments,
    categories,
    writers,
    scan,
    similarity,
    duplicates,
    upscale,
    config,
    embeddings,
    models,
    prompt as prompt_api,
    chat as chat_api,
    vlm,
    comfy as comfy_api,
    storyboard as storyboard_api,
    i2v,
    t2i,
    taste as taste_api,
    websocket,
)
from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.judgments_service import faiss_embedding_lookup
from backend.services.auto_index import AutoIndexScheduler, generation_busy
from backend.services.taste_examples_service import TasteExamplesService
from backend.services.taste_service import TasteService
from backend.services.taste_snapshot import apply_pending_taste_snapshot
from backend.ws.manager import ws_manager
from metascan.core.inference_client import InferenceClient
from metascan.core.prompt_store import get_prompt_store
from metascan.core.vlm_client import VlmClient
from backend.api.vlm import (
    get_all_vlm_clients,
    get_vlm_client,
    get_vlm_roles,
    get_writer_adapter_path,
    get_writer_vlm,
    set_vlm_roles,
)
from backend.services.vlm_roles import VlmRoles
from backend.api.comfy import set_comfy_client
from backend.api.storyboard import set_storyboard_runner
from metascan.core.comfy_client import ComfyClient
from metascan.core.storyboard_runner import StoryboardRunner
from metascan.core.i2v_runner import I2vRunner
from metascan.core.t2i_captions import CaptionStore
from metascan.core.t2i_runner import T2iRunner
from metascan.core.t2i_directions import DIRECTIONS_DIRNAME, SnippetCache
from metascan.core.t2i_wildcards import LibraryCache
from metascan.core.taste_runner import TasteRunner
from metascan.core.vlm_select import vlm_model_installed
from metascan.core.writer_adapters import servable_adapter_files
from metascan.core.scanner import Scanner
from metascan.utils.app_paths import get_data_dir
from metascan.utils.log_files import install_server_log

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs every outbound request at INFO. Our VlmClient polls
# llama-server's /health endpoint up to 10×/sec during model load, which
# floods the log with several hundred 503 lines per spawn. Promote to
# WARNING so only genuine HTTP problems show up at the default level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _inject_huggingface_token(token: str) -> None:
    """Expose ``token`` as HF_TOKEN / HUGGING_FACE_HUB_TOKEN so that
    huggingface_hub picks it up both in the server process and in any
    subprocess that inherits the environment."""
    if not token:
        return
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token


def _wire_vlm_client_status(client: VlmClient) -> None:
    """Bridge VlmClient state transitions onto the ``models`` WS channel.

    Each payload carries ``roles``: the roles (``writer`` / ``general``)
    this client serves right now, so the UI can tell two clients apart."""

    def _roles() -> List[str]:
        roles = get_vlm_roles()
        return list(roles.roles_of(client)) if roles is not None else []

    def on_status(_state: str, payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync(
            "models", "vlm_status", {**payload, "roles": _roles()}
        )

    def on_progress(payload: Dict[str, Any]) -> None:
        ws_manager.broadcast_sync(
            "models", "vlm_progress", {**payload, "roles": _roles()}
        )

    client.on_status(on_status)
    client.on_progress(on_progress)


def _new_vlm_client() -> VlmClient:
    """One llama-server supervisor per distinct role model."""
    client = VlmClient()
    _wire_vlm_client_status(client)
    return client


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


async def _apply_pending_taste_snapshot(db: Any, data_dir: Path) -> None:
    """Startup: put back ratings left pending by an interrupted full clean
    (backend/services/taste_snapshot.py). Never raises."""
    try:
        counts = await asyncio.to_thread(apply_pending_taste_snapshot, db, data_dir)
        if counts["restored"] or counts["missing"]:
            logger.info(
                "Pending taste snapshot at startup: %d restored, %d still pending",
                counts["restored"],
                counts["missing"],
            )
    except Exception:
        logger.exception("Applying the pending taste snapshot failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Until now the server logged to the console only, so anything that
    # scrolled away (a dropped ComfyUI socket, a stalled event loop) was
    # unrecoverable. Size-capped: see metascan/utils/log_files.py.
    server_log = install_server_log(get_data_dir().parent / "logs")
    if server_log is not None:
        logger.info("Logging to %s", server_log)

    ws_manager.attach_loop(asyncio.get_running_loop())
    similarity.set_server_loop(asyncio.get_running_loop())
    heartbeat_task = asyncio.create_task(_event_loop_heartbeat())

    # Load the YAML-backed prompt store eagerly and start a watcher so
    # edits to data/meta_prompt.yml hot-reload into the running process.
    # Tolerate a missing or malformed file so dev environments without
    # the YAML still boot; the first generate request will surface the
    # error if a prompt key is missing.
    prompt_store = None
    try:
        prompt_store = get_prompt_store()
        prompt_store.start_watching()
        logger.info("prompt-store loaded %d entries", len(prompt_store.keys()))
    except Exception:
        logger.exception("Failed to initialize prompt store; prompt endpoints may 500")

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

    # Tombstones snapshot a deleted item's CLIP vector from the in-memory
    # FAISS index (never loads it from disk under the DB lock).
    try:
        get_db().set_embedding_lookup(faiss_embedding_lookup)
    except Exception:
        logger.exception("Failed to install tombstone embedding lookup")

    await _apply_pending_taste_snapshot(get_db(), get_data_dir())

    app_config = load_app_config()
    models_cfg = get_models_config(app_config)
    _inject_huggingface_token(models_cfg["huggingface_token"])

    sim_cfg = app_config.get("similarity", {}) or {}
    clip_model_key = str(sim_cfg.get("clip_model") or "small")
    device = str(sim_cfg.get("device") or "auto")

    client = InferenceClient()
    _wire_inference_client_status(client)
    similarity.set_inference_client(client)

    # Writer (T2I / I2V prompts) and general (tagging, chat, storyboard)
    # roles; one client when both name the same model. Resolving defaults
    # probes the hardware and the installed weights, so off the loop.
    vlm_roles = await asyncio.to_thread(
        VlmRoles.from_config,
        app_config,
        client_factory=_new_vlm_client,
        adapter_source=lambda: servable_adapter_files(get_db(), get_data_dir()),
    )
    set_vlm_roles(vlm_roles)
    # Prompt-writer adapters: the writer's llama-server loads them all.
    await vlm_roles.refresh_adapters()

    comfy_cfg = get_comfy_config(app_config)
    comfy_client = ComfyClient(
        base_url=comfy_cfg["base_url"],
        output_root=Path(comfy_cfg["output_root"]),
        db=get_db(),
        scanner=Scanner(get_db(), thumbnail_cache=get_thumbnail_cache()),
        in_flight=comfy_cfg["in_flight"],
        request_timeout_s=comfy_cfg["request_timeout_s"],
    )
    comfy_client.on_job_event(
        lambda event, payload: ws_manager.broadcast_sync("comfy", event, payload)
    )
    set_comfy_client(comfy_client)
    # start() never blocks on an unreachable ComfyUI — the reader loop
    # retries with backoff, so a server that is not running yet simply
    # connects later.
    await comfy_client.start()

    storyboard_runner = StoryboardRunner(
        db=get_db(),
        comfy=comfy_client,
        get_vlm=get_vlm_client,
        get_all_vlms=get_all_vlm_clients,
        output_root=Path(comfy_cfg["output_root"]),
        unload_vlm_during_generation=comfy_cfg["unload_vlm_during_generation"],
    )
    storyboard_runner.on_event(
        lambda channel, event, data: ws_manager.broadcast_sync(channel, event, data)
    )
    comfy_client.on_job_event(storyboard_runner.handle_job_event)
    set_storyboard_runner(storyboard_runner)

    i2v_runner = I2vRunner(
        db=get_db(),
        comfy=comfy_client,
        get_vlm=get_writer_vlm,
        output_root=Path(comfy_cfg["output_root"]),
        vlm_installed=vlm_model_installed,
        writer_adapter=get_writer_adapter_path,
    )
    i2v_runner.on_event(
        lambda channel, event, data: ws_manager.broadcast_sync(channel, event, data)
    )
    comfy_client.on_job_event(i2v_runner.handle_job_event)
    i2v.set_i2v_runner(i2v_runner)

    # The caption CSV and the wildcard lists live side by side. Both are
    # lazy: nothing is read until the first request, and both reload on
    # change, so a missing CSV only turns Random mode off.
    t2i_dir = get_data_dir() / "t2i_captions"
    t2i_runner = T2iRunner(
        db=get_db(),
        comfy=comfy_client,
        get_vlm=get_writer_vlm,
        get_all_vlms=get_all_vlm_clients,
        vlm_installed=vlm_model_installed,
        captions=CaptionStore(t2i_dir / "t2i_captions.csv"),
        library=LibraryCache(t2i_dir),
        directions=SnippetCache(t2i_dir / DIRECTIONS_DIRNAME),
        output_root=Path(comfy_cfg["output_root"]),
        get_config=lambda: get_t2i_config(load_app_config()),
        unload_vlm_during_generation=comfy_cfg["unload_vlm_during_generation"],
        # "base+taste": favorite prompts as examples, CLIP only when ready.
        taste_examples=TasteExamplesService(get_db()).examples_for,
        # "adapter:<id>": the writer's llama-server serves the adapter.
        writer_adapter=get_writer_adapter_path,
    )
    t2i_runner.on_event(
        lambda channel, event, data: ws_manager.broadcast_sync(channel, event, data)
    )
    comfy_client.on_job_event(t2i_runner.handle_job_event)
    t2i.set_t2i_runner(t2i_runner)

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

    # Preload each distinct role client whose model is in the preload list
    # (a listed VLM no role uses is not loaded: nothing would talk to it).
    for vlm_client in vlm_roles.distinct_clients():
        preload_id = vlm_client.preferred_model_id
        if preload_id is None or preload_id not in preload_list:
            continue
        logger.info(
            "Preloading VLM at startup: %s (%s)",
            preload_id,
            "+".join(vlm_roles.roles_of(vlm_client)),
        )

        async def _preload_vlm(
            c: VlmClient = vlm_client, mid: str = preload_id
        ) -> None:
            try:
                await c.start(mid, wait_ready=False)
            except Exception:
                logger.exception("VLM preload failed")

        asyncio.create_task(_preload_vlm())

    upscale.init_upscale_queue()
    similarity.init_embedding_queue()

    # Taste scorer: the runner reads the same FAISS index the embedding
    # queue writes; the service retrains in the background and rescores
    # new images after every embedding run (the hook fires on a worker
    # thread and only posts onto this loop).
    taste_service = TasteService(
        get_db(),
        TasteRunner(
            get_db(),
            index_dir=similarity.embedding_index_dir(),
            work_dir=get_data_dir() / "taste",
        ),
        get_config=lambda: get_taste_config(load_app_config()),
        is_indexing=similarity.is_indexing,
    )
    taste_service.attach_loop(asyncio.get_running_loop())
    taste_api.set_taste_service(taste_service)
    similarity.set_embedding_complete_hook(taste_service.schedule_rescore_threadsafe)
    taste_service.start_retrain_loop()

    # Fresh T2I images need CLIP vectors before they can be scored: build
    # the index incrementally 15 s after the last ingest, once no
    # generation work is in flight (the embedding hook above rescores).
    auto_index = AutoIndexScheduler(
        start_build=similarity.start_auto_index_build,
        is_indexing=similarity.is_indexing,
        is_busy=lambda: generation_busy(t2i_runner, comfy_client),
    )
    t2i_runner.on_event(auto_index.on_t2i_event)
    # The post-embedding VLM tag drain waits for the same "no generation in
    # flight" so a 27B never loads into a render.
    similarity.set_generation_busy_probe(
        lambda: generation_busy(t2i_runner, comfy_client)
    )

    try:
        yield
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        await upscale.shutdown_upscale_queue()
        # The auto build starts embedding runs: stop it before the queue.
        try:
            await auto_index.aclose()
        except Exception:
            logger.exception("Auto index scheduler shutdown raised")
        # The embedding queue is the rescore trigger: stop it (and unhook)
        # before closing the taste service that consumes it.
        await similarity.shutdown_embedding_queue()  # cancels any VLM drain
        similarity.set_embedding_complete_hook(None)
        similarity.set_generation_busy_probe(None)
        similarity.set_server_loop(None)
        try:
            await taste_service.aclose()
        except Exception:
            logger.exception("Taste service shutdown raised")
        taste_api.set_taste_service(None)
        try:
            await client.shutdown()
        except Exception:
            logger.exception("Inference client shutdown raised")
        # Every distinct role client (shutdown_all logs its own failures).
        await vlm_roles.shutdown_all()
        # Shut down the event *source* (ComfyClient) before the runner that
        # consumes its events. storyboard_runner.handle_job_event schedules
        # a fire-and-forget ingest task for every job_outputs event; if the
        # runner closed first, a collect task completing in the window
        # between the two shutdowns would spawn an ingest task nobody ever
        # awaits (aclose() already returned). Closing comfy_client first
        # means no more job_outputs events can fire once we reach
        # storyboard_runner.aclose(), so it only has to drain tasks that
        # are already in flight.
        try:
            await comfy_client.shutdown()
        except Exception:
            logger.exception("Comfy client shutdown raised")
        try:
            await storyboard_runner.aclose()
        except Exception:
            logger.exception("Storyboard runner shutdown raised")
        try:
            await i2v_runner.aclose()
        except Exception:
            logger.exception("i2v runner close failed")
        try:
            await t2i_runner.aclose()
        except Exception:
            logger.exception("t2i runner close failed")
        if prompt_store is not None:
            try:
                prompt_store.stop_watching()
            except Exception:
                logger.exception("prompt-store shutdown raised")


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
    app.include_router(judgments.router)
    app.include_router(categories.router)
    app.include_router(writers.router)
    app.include_router(scan.router)
    app.include_router(similarity.router)
    app.include_router(duplicates.router)
    app.include_router(upscale.router)
    app.include_router(config.router)
    app.include_router(embeddings.router)
    app.include_router(models.router)
    app.include_router(vlm.router)
    app.include_router(prompt_api.router)
    app.include_router(chat_api.router)
    app.include_router(comfy_api.router)
    app.include_router(storyboard_api.router)
    app.include_router(i2v.router)
    app.include_router(t2i.router)
    app.include_router(taste_api.router)
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
