"""Export and import of a library's own data (Configuration -> Import / Export).

``GET /export`` streams a bundle; ``POST /import/upload`` takes one as the raw
request body and stages it; ``POST /import/preview`` re-counts it for a folder
mapping; ``POST /import/apply`` starts the import (202) and ``GET /import/status``
says how it went; ``DELETE /import/{token}`` drops the staged file. Refusals are
409 ``{code, message}``, a file that is no bundle 422 ``{code, message}``.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, NoReturn, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from backend.dependencies import get_db
from backend.services import library_transfer_service as service
from backend.services.library_transfer_service import TransferRefused
from metascan.core.library_transfer import BundleError

router = APIRouter(prefix="/api/library-transfer", tags=["library-transfer"])


class ImportRequest(BaseModel):
    token: str
    mapping: Dict[str, Optional[str]] = {}


def _refused(refused: TransferRefused) -> NoReturn:
    raise HTTPException(
        status_code=409, detail={"code": refused.code, "message": refused.message}
    )


@router.get("/export")
async def export_library() -> FileResponse:
    try:
        bundle: Path = await asyncio.to_thread(service.build_export, get_db())
    except TransferRefused as refused:
        _refused(refused)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        bundle,
        media_type="application/zip",
        filename=f"metascan-library-data-{stamp}.zip",
        background=BackgroundTask(bundle.unlink, missing_ok=True),
    )


@router.post("/import/upload")
async def upload_bundle(request: Request) -> Dict[str, Any]:
    db = get_db()
    try:
        target = service.new_upload_path(db)
        with target.open("wb") as sink:
            try:
                async for chunk in request.stream():
                    await asyncio.to_thread(sink.write, chunk)
            except BaseException:
                sink.close()
                target.unlink(missing_ok=True)
                raise
        return await asyncio.to_thread(service.stage_upload, db, target)
    except TransferRefused as refused:
        _refused(refused)
    except BundleError as e:
        raise HTTPException(
            status_code=422, detail={"code": "not_a_bundle", "message": str(e)}
        )


@router.post("/import/preview")
async def preview_import(body: ImportRequest) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(
            service.preview, get_db(), body.token, body.mapping
        )
    except TransferRefused as refused:
        _refused(refused)


@router.post("/import/apply", status_code=202)
async def apply_import(body: ImportRequest) -> Dict[str, str]:
    try:
        service.start_import(get_db(), body.token, body.mapping)
    except TransferRefused as refused:
        _refused(refused)
    return {"status": "started"}


@router.get("/import/status")
async def import_status() -> Dict[str, Any]:
    return service.status()


@router.delete("/import/{token}")
async def discard_import(token: str) -> Dict[str, str]:
    try:
        service.discard(token)
    except TransferRefused as refused:
        _refused(refused)
    return {"status": "deleted"}
