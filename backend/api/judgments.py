"""REST endpoints for taste judgments (verdict / reason / seen)."""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.dependencies import get_db
from backend.services.judgments_service import JudgmentsService
from backend.ws.manager import ws_manager

router = APIRouter(prefix="/api", tags=["judgments"])


def _get_service() -> JudgmentsService:
    return JudgmentsService(get_db())


class SeenRequest(BaseModel):
    file_path: str


class JudgmentPatch(BaseModel):
    verdict: Optional[Literal["favorite", "pass"]] = None
    sticky_category_id: Optional[int] = None
    reason: Optional[str] = None
    on: Optional[bool] = None
    score_shown: Optional[float] = None


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Media not found")


@router.post("/judgments/seen")
async def mark_seen(
    body: SeenRequest, service: JudgmentsService = Depends(_get_service)
) -> Dict[str, Any]:
    state = await service.mark_seen(body.file_path)
    if state is None:
        raise _not_found()
    return state.to_dict()


@router.get("/judgments/{file_path:path}")
async def get_judgment(
    file_path: str, service: JudgmentsService = Depends(_get_service)
) -> Dict[str, Any]:
    state = await service.get(file_path)
    if state is None:
        raise _not_found()
    out = state.to_dict()
    out["batch"] = await service.batch_position(file_path)
    return out


@router.patch("/judgments/{file_path:path}")
async def patch_judgment(
    file_path: str,
    body: JudgmentPatch,
    service: JudgmentsService = Depends(_get_service),
) -> Dict[str, Any]:
    has_verdict = "verdict" in body.model_fields_set
    has_reason = body.reason is not None
    if has_verdict == has_reason:
        raise HTTPException(
            status_code=422,
            detail={"code": "verdict_xor_reason"},
        )
    try:
        if has_verdict:
            before = None
            if body.verdict is not None and body.sticky_category_id is not None:
                before = await service.get(file_path)
            state = await service.set_verdict(
                file_path,
                body.verdict,
                body.score_shown,
                body.sticky_category_id,
            )
            if (
                state is not None
                and before is not None
                and body.sticky_category_id is not None
                and body.sticky_category_id not in {c.id for c in before.categories}
                and body.sticky_category_id in {c.id for c in state.categories}
            ):
                ws_manager.broadcast_sync("categories", "categories_changed", {})
        else:
            if body.on is None:
                raise HTTPException(status_code=422, detail={"code": "missing_on"})
            assert body.reason is not None
            state = await service.set_reason(
                file_path, body.reason, body.on, body.score_shown
            )
    except ValueError as exc:
        code = str(exc)
        status = {"unknown_reason": 422, "no_verdict": 409, "unknown_category": 404}
        if code not in status:
            raise
        raise HTTPException(status_code=status[code], detail={"code": code})
    if state is None:
        raise _not_found()
    return state.to_dict()
