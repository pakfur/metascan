"""REST endpoints for taste judgments: the ✓ pass / ✗ fail grade, ★
favorite, reasons and seen."""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.dependencies import get_db
from backend.services.judgments_service import JudgmentsService
from backend.services.scorecard import notify_scorecard_judgment
from backend.ws.manager import ws_manager
from metascan.core.judgments import Grade, JudgmentState

router = APIRouter(prefix="/api", tags=["judgments"])


def _get_service() -> JudgmentsService:
    return JudgmentsService(get_db())


class SeenRequest(BaseModel):
    file_path: str
    # The taste score on screen at dwell time (two decimals), if any.
    score_shown: Optional[float] = None


class JudgmentPatch(BaseModel):
    """Exactly one of ``{grade, score_shown?, sticky_category_id?}``,
    ``{favorite}`` or ``{reason, on, score_shown?}``; anything else is a
    422. Unknown keys are refused, so the pre-grades ``verdict`` field is
    too."""

    model_config = ConfigDict(extra="forbid")

    grade: Optional[Grade] = None
    favorite: Optional[bool] = None
    reason: Optional[str] = None
    on: Optional[bool] = None
    score_shown: Optional[float] = None
    sticky_category_id: Optional[int] = None


PatchKind = Literal["grade", "favorite", "reason"]
_GRADE_FIELDS = frozenset({"grade", "score_shown", "sticky_category_id"})
_REASON_FIELDS = frozenset({"reason", "on", "score_shown"})


def _patch_kind(body: JudgmentPatch) -> PatchKind:
    fields = body.model_fields_set
    if "grade" in fields and fields <= _GRADE_FIELDS:
        return "grade"
    if fields == {"favorite"} and body.favorite is not None:
        return "favorite"
    if "reason" in fields and fields <= _REASON_FIELDS and body.reason is not None:
        if body.on is None:
            raise HTTPException(status_code=422, detail={"code": "missing_on"})
        return "reason"
    raise HTTPException(status_code=422, detail={"code": "grade_favorite_or_reason"})


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Media not found")


@router.post("/judgments/seen")
async def mark_seen(
    body: SeenRequest, service: JudgmentsService = Depends(_get_service)
) -> Dict[str, Any]:
    state = await service.mark_seen(body.file_path, body.score_shown)
    if state is None:
        raise _not_found()
    # Seen is not a grade: it never judges a scorecard image, no check here.
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
    # A scorecard image: the viewer hides it as "judge to reveal" while
    # ``blind``. None for any other item.
    out["scorecard"] = await service.scorecard(file_path)
    return out


async def _set_grade(
    service: JudgmentsService, file_path: str, body: JudgmentPatch
) -> Optional[JudgmentState]:
    sticky = body.sticky_category_id
    before = None
    if body.grade is not None and sticky is not None:
        before = await service.get(file_path)
    state = await service.set_grade(file_path, body.grade, body.score_shown, sticky)
    if (
        state is not None
        and before is not None
        and sticky not in {c.id for c in before.categories}
        and sticky in {c.id for c in state.categories}
    ):
        ws_manager.broadcast_sync("categories", "categories_changed", {})
    return state


@router.patch("/judgments/{file_path:path}")
async def patch_judgment(
    file_path: str,
    body: JudgmentPatch,
    service: JudgmentsService = Depends(_get_service),
) -> Dict[str, Any]:
    kind = _patch_kind(body)
    try:
        if kind == "grade":
            state = await _set_grade(service, file_path, body)
        elif kind == "favorite":
            assert body.favorite is not None
            state = await service.set_favorite(file_path, body.favorite)
        else:
            assert body.reason is not None and body.on is not None
            state = await service.set_reason(
                file_path, body.reason, body.on, body.score_shown
            )
    except ValueError as exc:
        code = str(exc)
        status = {"unknown_reason": 422, "no_grade": 409, "unknown_category": 404}
        if code not in status:
            raise
        raise HTTPException(status_code=status[code], detail={"code": code})
    if state is None:
        raise _not_found()
    # The scorecard check runs after a grade only: ★ and reasons never reach it.
    if kind == "grade":
        await notify_scorecard_judgment(get_db(), file_path)
    return state.to_dict()
