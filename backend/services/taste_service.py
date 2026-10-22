"""Service layer for the taste scorer: status, manual and automatic
training, score reads, and the rescore that follows an embedding run.

``TasteRunner`` (metascan/core/taste_runner.py) owns the worker
subprocess; this service owns *when* it runs, keeps at most one training
task alive, turns every outcome (including exceptions) into a ``taste``
WebSocket event, and cancels in-flight work on shutdown."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from metascan.core.taste_labels import (
    RETRAIN_CHECK_S,
    RETRAIN_IDLE_S,
    RETRAIN_MIN_EVENTS,
    build_label_set,
    missing_for_training,
)
from metascan.utils.path_utils import to_native_path

logger = logging.getLogger(__name__)

Broadcast = Callable[[str, str, Any], None]

_MODEL_FIELDS = (
    "id",
    "created_at",
    "n_pos",
    "n_neg",
    "n_pairs",
    "holdout_auc",
    "embedding_model",
    "half_life_days",
)


class TrainConflict(Exception):
    """A train request that cannot start: ``code`` is ``already_training``
    or ``not_enough_data`` (then ``missing`` says how many more)."""

    def __init__(self, code: str, missing: Optional[Dict[str, int]] = None) -> None:
        super().__init__(code)
        self.code = code
        self.missing = missing


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class TasteService:
    def __init__(
        self,
        db: Any,
        runner: Any,
        get_config: Callable[[], Dict[str, Any]],
        broadcast: Optional[Broadcast] = None,
    ) -> None:
        if broadcast is None:
            from backend.ws.manager import ws_manager

            broadcast = ws_manager.broadcast_sync
        self.db = db
        self.runner = runner
        self._get_config = get_config
        self._broadcast = broadcast
        self._last_error: Optional[str] = None
        self._train_task: Optional[asyncio.Task] = None
        self._rescore_tasks: Set[asyncio.Task] = set()
        self._loop_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Inputs of the last automatic run that did not produce a model;
        # the loop does not retry the same inputs every tick.
        self._failed_auto_inputs: Optional[Tuple[Any, ...]] = None
        self._closed = False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _config(self) -> Dict[str, Any]:
        try:
            return dict(self._get_config())
        except Exception:
            logger.exception("reading the taste config failed; using defaults")
            return {}

    def _half_life(self) -> float:
        from metascan.core.taste_labels import DEFAULT_HALF_LIFE_DAYS

        return float(self._config().get("half_life_days") or DEFAULT_HALF_LIFE_DAYS)

    def _auto_retrain(self) -> bool:
        return bool(self._config().get("auto_retrain", True))

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            self._broadcast("taste", event, data)
        except Exception:
            logger.exception("taste broadcast %s failed", event)

    @property
    def is_training(self) -> bool:
        return bool(self.runner.training) or (
            self._train_task is not None and not self._train_task.done()
        )

    async def _usable_model(self) -> Optional[Dict[str, Any]]:
        """The active model, or None when there is none or it is stale."""
        model: Optional[Dict[str, Any]] = await asyncio.to_thread(
            self.db.get_active_taste_model
        )
        if model is None or await asyncio.to_thread(self.runner.is_stale, model):
            return None
        return model

    def _missing(self) -> Optional[Dict[str, int]]:
        """Sync (run in a thread): how many more labels a model needs."""
        from metascan.core.taste_labels import DEFAULT_HALF_LIFE_DAYS

        ls = build_label_set(
            self.db.taste_training_raw(),
            datetime.now(timezone.utc),
            DEFAULT_HALF_LIFE_DAYS,
        )
        return missing_for_training(ls.n_pos, ls.n_neg)

    def _events_since(
        self, model: Optional[Dict[str, Any]]
    ) -> Tuple[int, Optional[str]]:
        since = model["labels_as_of"] if model is not None else None
        count, newest = self.db.count_training_events_since(since)
        return int(count), newest

    # ------------------------------------------------------------------
    # status / scores / queue
    # ------------------------------------------------------------------

    async def status(self) -> Dict[str, Any]:
        model = await asyncio.to_thread(self.db.get_active_taste_model)
        stale = False
        if model is not None:
            stale = bool(await asyncio.to_thread(self.runner.is_stale, model))
        new_events, _ = await asyncio.to_thread(self._events_since, model)
        missing = await asyncio.to_thread(self._missing)
        last_error = self._last_error
        if last_error is None:
            latest = await asyncio.to_thread(self.db.list_taste_models, 1)
            if latest and latest[0]["status"] == "failed":
                last_error = latest[0].get("error") or "training failed"
        return {
            "active_model": (
                {k: model.get(k) for k in _MODEL_FIELDS} if model is not None else None
            ),
            "stale": stale,
            "training": self.is_training,
            "new_events": new_events,
            "missing": missing,
            "auto_retrain": self._auto_retrain(),
            "last_error": last_error,
        }

    async def scores(self, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        empty: Dict[str, Any] = {"model_id": None, "scores": {}}
        model = await self._usable_model()
        if model is None:
            return empty
        model_id, scores = await asyncio.to_thread(self.db.get_taste_scores, file_paths)
        if model_id != model["id"]:
            return empty
        return {
            "model_id": model_id,
            "scores": {to_native_path(p): s for p, s in scores.items()},
        }

    async def review_queue(self, limit: int = 200) -> List[str]:
        model = await self._usable_model()
        if model is None:
            return []
        model_id, _ = await asyncio.to_thread(self.db.get_taste_scores, [])
        if model_id != model["id"]:
            return []
        paths = await asyncio.to_thread(self.db.taste_review_queue, limit)
        return [to_native_path(p) for p in paths]

    # ------------------------------------------------------------------
    # training
    # ------------------------------------------------------------------

    async def start_training(self) -> None:
        """Start a background training run. Raises ``TrainConflict`` when
        one is already running or there are too few labels."""
        if self.is_training:
            raise TrainConflict("already_training")
        missing = await asyncio.to_thread(self._missing)
        if missing is not None:
            raise TrainConflict("not_enough_data", missing)
        self._spawn_training(auto_inputs=None)

    def _spawn_training(self, auto_inputs: Optional[Tuple[Any, ...]]) -> None:
        # Synchronous check-and-set: no await between the guard and the
        # task creation, so two concurrent requests cannot both pass.
        if self.is_training:
            raise TrainConflict("already_training")
        if self._closed:
            raise TrainConflict("already_training")
        self._train_task = asyncio.create_task(self._run_training(auto_inputs))

    async def _run_training(self, auto_inputs: Optional[Tuple[Any, ...]]) -> None:
        self._emit("training_started", {})
        try:
            result = await self.runner.train(self._half_life())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("taste training raised")
            result = {"status": "error", "error": str(e) or repr(e)}
        status = result.get("status")
        if status == "ok":
            self._last_error = None
            self._failed_auto_inputs = None
            model_id = result.get("model_id")
            self._emit(
                "training_complete",
                {
                    "model_id": model_id,
                    "holdout_auc": result.get("holdout_auc"),
                    "n_pos": result.get("n_pos"),
                    "n_neg": result.get("n_neg"),
                },
            )
            self._emit("scores_updated", {"model_id": model_id})
            return
        self._failed_auto_inputs = auto_inputs
        if status == "not_enough_data":
            # Labels dropped between the check and the snapshot; status
            # already reports what is missing, so this is not an error.
            self._emit("training_failed", {"error": "not_enough_data"})
            return
        error = str(result.get("error") or "taste training failed")
        self._last_error = error
        self._emit("training_failed", {"error": error})

    async def maybe_retrain(self, now: Optional[datetime] = None) -> bool:
        """One auto-retrain check; True when a training run was started."""
        if not self._auto_retrain() or self.is_training or self._closed:
            return False
        now = now or datetime.now(timezone.utc)
        model = await asyncio.to_thread(self.db.get_active_taste_model)
        stale = model is not None and bool(
            await asyncio.to_thread(self.runner.is_stale, model)
        )
        count, newest = await asyncio.to_thread(self._events_since, model)
        if not stale and count < RETRAIN_MIN_EVENTS:
            return False
        if newest is not None:
            newest_dt = _parse_ts(newest)
            if (
                newest_dt is not None
                and (now - newest_dt).total_seconds() < RETRAIN_IDLE_S
            ):
                return False
        inputs = (model["id"] if model else None, stale, count, newest)
        if inputs == self._failed_auto_inputs:
            return False
        if await asyncio.to_thread(self._missing) is not None:
            return False
        try:
            self._spawn_training(auto_inputs=inputs)
        except TrainConflict:
            return False
        logger.info(
            "Auto-retraining the taste scorer (%d new events, stale=%s)",
            count,
            stale,
        )
        return True

    async def retrain_loop(self) -> None:
        """Every ``RETRAIN_CHECK_S``: one ``maybe_retrain`` check. Errors
        are logged per tick; cancellation ends the loop."""
        while True:
            await asyncio.sleep(RETRAIN_CHECK_S)
            try:
                await self.maybe_retrain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("taste auto-retrain check failed")

    def start_retrain_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self.retrain_loop())

    # ------------------------------------------------------------------
    # rescore
    # ------------------------------------------------------------------

    async def rescore(self) -> Optional[int]:
        """Rescore every image with the active model; broadcasts
        ``scores_updated`` on success. Never raises (except cancellation)."""
        try:
            n = await self.runner.rescore()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("taste rescore failed")
            return None
        if n is None:
            return None
        try:
            model = await asyncio.to_thread(self.db.get_active_taste_model)
        except Exception:
            logger.exception("reading the active taste model failed")
            model = None
        self._emit("scores_updated", {"model_id": model["id"] if model else None})
        return int(n)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """The server loop; ``schedule_rescore_threadsafe`` posts onto it."""
        self._loop = loop

    def _spawn_rescore(self) -> None:
        if self._closed:
            return
        task = asyncio.create_task(self.rescore())
        self._rescore_tasks.add(task)
        task.add_done_callback(self._rescore_tasks.discard)

    def schedule_rescore_threadsafe(self) -> None:
        """Called from the embedding queue's ``on_complete`` (a worker
        thread): rescore on the server loop. Never raises."""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("taste rescore skipped: no server loop attached")
            return
        try:
            loop.call_soon_threadsafe(self._spawn_rescore)
        except RuntimeError:
            logger.debug("taste rescore skipped: server loop closed")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def wait_idle(self) -> None:
        """Await the current training and rescore tasks (tests, shutdown)."""
        tasks = [t for t in (self._train_task, *self._rescore_tasks) if t]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        """Cancel the retrain loop and any in-flight train / rescore, and
        await them (the runner kills its worker on cancellation)."""
        self._closed = True
        tasks = [
            t
            for t in (self._loop_task, self._train_task, *self._rescore_tasks)
            if t is not None and not t.done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
