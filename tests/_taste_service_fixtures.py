"""Shared fakes for the taste scorer API and retrain-loop tests: a real
isolated DB, a fake ``TasteRunner`` with controllable results, and a
broadcast recorder in place of the WebSocket manager."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


def model_row(**over: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "created_at": T0.isoformat(),
        "labels_as_of": T0.isoformat(),
        "embedding_model": "small",
        "dim": 32,
        "categories": [],
        "n_pos": 20,
        "n_neg": 20,
        "n_pairs": 3,
        "holdout_auc": 0.81,
        "half_life_days": 180.0,
        "params_path": "/nonexistent/taste_1.npz",
        "error": None,
    }
    row.update(over)
    return row


def seed_labels(
    db: DatabaseManager, n_fav: int = 20, n_pass: int = 20, at: datetime = T0
) -> None:
    """``n_fav`` favorites and ``n_pass`` passes, every event logged at ``at``."""
    for i in range(n_fav):
        p = f"/lib/f{i:02d}.png"
        db.save_media(media(p))
        db.set_verdict(p, "favorite", None, at)
    for i in range(n_pass):
        p = f"/lib/p{i:02d}.png"
        db.save_media(media(p))
        db.set_verdict(p, "pass", None, at)


class FakeRunner:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self.training = False
        self.stale = False
        self.train_calls: List[float] = []
        self.rescore_calls = 0
        self.result: Dict[str, Any] = {"status": "ok"}
        self.raise_on_train: Optional[BaseException] = None
        self.raise_on_rescore: Optional[BaseException] = None
        self.rescore_result: Optional[int] = 3
        self.gate: Optional[asyncio.Event] = None
        self.started = asyncio.Event()
        self.record_model_at: Optional[datetime] = None
        self.rescore_gate: Optional[asyncio.Event] = None
        self.rescore_started = asyncio.Event()
        self.fingerprint: Any = ("small", 32, 1.0)
        # The embedding queue's is_indexing(), as the service sees it.
        self.indexing = False

    def index_fingerprint(self) -> Any:
        return self.fingerprint

    def is_stale(self, model: Dict[str, Any]) -> bool:
        return self.stale

    async def train(
        self, half_life_days: float, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        if self.training:
            raise RuntimeError("already_training")
        self.training = True
        try:
            self.train_calls.append(half_life_days)
            self.started.set()
            if self.raise_on_train is not None:
                raise self.raise_on_train
            result = dict(self.result)
            if self.record_model_at is not None:
                result["model_id"] = self.db.record_taste_model(
                    model_row(labels_as_of=self.record_model_at.isoformat())
                )
                result.setdefault("holdout_auc", 0.8)
                result.setdefault("n_pos", 20)
                result.setdefault("n_neg", 20)
            if self.gate is not None:
                await self.gate.wait()
            return result
        finally:
            self.training = False

    async def rescore(self) -> Optional[int]:
        self.rescore_calls += 1
        self.rescore_started.set()
        if self.rescore_gate is not None:
            await self.rescore_gate.wait()
        if self.raise_on_rescore is not None:
            raise self.raise_on_rescore
        return self.rescore_result


class Recorder:
    def __init__(self) -> None:
        self.events: List[Tuple[str, str, Any]] = []

    def __call__(self, channel: str, event: str, data: Any = None) -> None:
        self.events.append((channel, event, data))

    def names(self) -> List[str]:
        return [e for c, e, _ in self.events if c == "taste"]


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(tmp_path / "db")
    yield d
    d.close()


@pytest.fixture
def fake_runner(db):
    return FakeRunner(db)


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def taste_config():
    return {"half_life_days": 90.0, "auto_retrain": True}


@pytest.fixture
def service(db, fake_runner, recorder, taste_config):
    from backend.services.taste_service import TasteService

    return TasteService(
        db,
        fake_runner,
        get_config=lambda: dict(taste_config),
        broadcast=recorder,
        is_indexing=lambda: fake_runner.indexing,
    )


def after(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)
