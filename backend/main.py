"""FastAPI application factory for the metascan backend."""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import get_server_config
from backend.api import media, filters, scan, similarity, duplicates, upscale, config, embeddings, websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    server_config = get_server_config()

    app = FastAPI(
        title="Metascan API",
        description="Backend API for the Metascan media browser",
        version="1.0.0",
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
            # Skip auth for WebSocket upgrades (handled separately), docs, and health
            if (
                request.url.path in ("/docs", "/openapi.json", "/health", "/ws")
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
    app.include_router(websocket.router)

    logger.info(
        f"Metascan API ready on {server_config.host}:{server_config.port}"
    )

    return app


app = create_app()
