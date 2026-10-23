"""REST endpoints for the taste scorer (status, training, scores, review
queue). Training runs in the background; its outcome goes out on the
``taste`` WebSocket channel."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.services.taste_service import TasteService, TrainConflict

router = APIRouter(prefix="/api", tags=["taste"])

_service: Optional[TasteService] = None


def set_taste_service(service: Optional[TasteService]) -> None:
    """Install the ``TasteService`` singleton (FastAPI lifespan)."""
    global _service
    _service = service


def get_taste_service() -> Optional[TasteService]:
    return _service


def _get_service() -> TasteService:
    if _service is None:
        raise HTTPException(status_code=503, detail="taste service not initialized")
    return _service


class ScoresRequest(BaseModel):
    file_paths: List[str]


@router.get("/taste/status")
async def taste_status(
    service: TasteService = Depends(_get_service),
) -> Dict[str, Any]:
    return await service.status()


@router.post("/taste/train", status_code=202)
async def taste_train(service: TasteService = Depends(_get_service)) -> Any:
    try:
        await service.start_training()
    except TrainConflict as exc:
        detail: Dict[str, Any] = {"code": exc.code}
        if exc.missing is not None:
            detail["missing"] = exc.missing
        raise HTTPException(status_code=409, detail=detail)
    return JSONResponse(status_code=202, content={"status": "started"})


@router.get("/taste/scores")
async def taste_scores(
    service: TasteService = Depends(_get_service),
) -> Dict[str, Any]:
    return await service.scores()


@router.post("/taste/scores")
async def taste_scores_subset(
    body: ScoresRequest, service: TasteService = Depends(_get_service)
) -> Dict[str, Any]:
    return await service.scores(body.file_paths)


@router.get("/taste/review-queue")
async def taste_review_queue(
    limit: int = Query(200, ge=1, le=5000),
    service: TasteService = Depends(_get_service),
) -> Dict[str, Any]:
    return {"paths": await service.review_queue(limit)}
