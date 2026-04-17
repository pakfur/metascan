"""Media CRUD and streaming endpoints."""

import logging
import mimetypes
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["media"])


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


@router.get("/media")
async def list_media(
    sort: str = "date_added",
    favorites_only: bool = False,
    service: MediaService = Depends(_get_service),
):
    """List all media, optionally sorted and filtered.

    Returns a *very* lightweight summary per item — just enough for the
    grid and viewer status bar. File name, format, and timestamps are
    dropped from the wire payload (client derives ``file_name`` from the
    path; detail view fetches the rest on selection). Generation metadata
    (prompt, model, loras, tags, etc.) is only served by
    ``GET /api/media/{path}``.
    """
    t_start = time.perf_counter()
    try:
        return await service.get_all_media_summaries(
            sort=sort, favorites_only=favorites_only
        )
    finally:
        logger.info(
            "GET /api/media: total=%.1fms sort=%s favorites_only=%s",
            (time.perf_counter() - t_start) * 1000,
            sort,
            favorites_only,
        )


@router.get("/media/{file_path:path}")
async def get_media(
    file_path: str,
    service: MediaService = Depends(_get_service),
):
    """Get the full detail record for a single media file.

    Tags are taken from the ``indices`` table rather than the serialized
    ``media.data`` blob — CLIP-generated tags are only written to
    ``indices`` via ``add_tag_indices`` and would otherwise be invisible in
    the details panel for files that never had a prompt.
    """
    t_start = time.perf_counter()
    media = await service.get_media(file_path)
    t_media = time.perf_counter()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    tags = await service.get_tags_for_file(file_path)
    t_tags = time.perf_counter()
    result = {**service.media_to_dict(media), "tags": tags}
    t_end = time.perf_counter()
    logger.info(
        "GET /api/media/{path}: media=%.1fms tags=%.1fms serialize=%.1fms "
        "total=%.1fms tag_count=%d",
        (t_media - t_start) * 1000,
        (t_tags - t_media) * 1000,
        (t_end - t_tags) * 1000,
        (t_end - t_start) * 1000,
        len(tags),
    )
    return result


@router.delete("/media/{file_path:path}")
async def delete_media(
    file_path: str,
    service: MediaService = Depends(_get_service),
):
    """Delete a media file (moves to trash) and remove from database."""
    success = await service.delete_media(file_path)
    if not success:
        raise HTTPException(status_code=404, detail="Media not found or delete failed")
    return {"status": "deleted"}


@router.patch("/media/{file_path:path}")
async def update_media(
    file_path: str,
    body: dict,
    service: MediaService = Depends(_get_service),
):
    """Update media fields (favorite, playback_speed)."""
    if "is_favorite" in body:
        await service.set_favorite(file_path, body["is_favorite"])
    if "playback_speed" in body:
        await service.update_playback_speed(file_path, body["playback_speed"])
    media = await service.get_media(file_path)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    return service.media_to_summary_dict(media)


@router.get("/stream/{file_path:path}")
async def stream_file(file_path: str, request: Request):
    """Serve a media file with HTTP Range support for streaming."""
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    range_header = request.headers.get("range")
    if range_header:
        # Parse Range header
        range_str = range_header.replace("bytes=", "")
        range_parts = range_str.split("-")
        start = int(range_parts[0]) if range_parts[0] else 0
        end = int(range_parts[1]) if range_parts[1] else file_size - 1
        content_length = end - start + 1

        def iter_file():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_file(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
            },
        )

    return FileResponse(
        path,
        media_type=content_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )


@router.get("/thumbnails/{file_path:path}")
async def get_thumbnail(
    file_path: str,
    service: MediaService = Depends(_get_service),
):
    """Serve a cached thumbnail, generating it if needed."""
    thumbnail_path = await service.get_thumbnail_path(file_path)
    if not thumbnail_path or not thumbnail_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not available")
    return FileResponse(
        thumbnail_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
