"""Media CRUD and streaming endpoints."""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.dependencies import get_db, get_thumbnail_cache
from backend.services.media_service import MediaService

router = APIRouter(prefix="/api", tags=["media"])


def _get_service() -> MediaService:
    return MediaService(get_db(), get_thumbnail_cache())


@router.get("/media")
async def list_media(
    sort: str = "date_added",
    favorites_only: bool = False,
    service: MediaService = Depends(_get_service),
):
    """List all media, optionally sorted and filtered."""
    all_media = await service.get_all_media()

    if favorites_only:
        all_media = [m for m in all_media if m.is_favorite]

    if sort == "file_name":
        all_media.sort(key=lambda m: m.file_name.lower())
    elif sort == "date_modified":
        all_media.sort(key=lambda m: m.modified_at or m.created_at, reverse=True)
    # date_added is the default DB order (created_at DESC)

    return [service.media_to_dict(m) for m in all_media]


@router.get("/media/{file_path:path}")
async def get_media(
    file_path: str,
    service: MediaService = Depends(_get_service),
):
    """Get a single media record by file path."""
    media = await service.get_media(file_path)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    return service.media_to_dict(media)


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
    return service.media_to_dict(media)


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
