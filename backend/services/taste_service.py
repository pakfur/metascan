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
    DEFAULT_HALF_LIFE_DAYS,
    MAX_NO_VECTOR_SHARE,
    RETRAIN_CHECK_S,
    RETRAIN_IDLE_S,
    RETRAIN_MIN_EVENTS,
    count_labels,
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
        is_indexing: Optional[Callable[[], bool]] = None,
    ) -> None:
        if broadcast is None:
            from backend.ws.manager import ws_manager

            broadcast = ws_manager.broadcast_sync
        self.db = db
        self.runner = runner
        self._get_config = get_config
        self._broadcast = broadcast
        # The embedding queue's is_indexing: no auto-retrain while the
        # index is being (re)written underneath the worker.
        self._is_indexing = is_indexing
        # What the last run said was missing when the labels were enough
        # but too few had a CLIP vector (status reason "vectors"). Cleared
        # by a successful run or a finished embedding run.
        self._vector_missing: Optional[Dict[str, int]] = None
        # An embedding run finished since the last training run started.
        self._embedding_ran = False
        # Active model id last announced stale (scores_updated with
        # model_id None), so the browser drops its now-unserved map once.
        self._stale_announced: Optional[int] = None
        self._last_error: Optional[str] = None
        self._train_task: Optional[asyncio.Task] = None
        self._rescore_tasks: Set[asyncio.Task] = set()
        self._loop_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Inputs of the last automatic run that did not produce a model;
        # the loop does not retry the same inputs every tick.
        self._failed_auto_inputs: Optional[Tuple[Any, ...]] = None
        self._rescoring = False
        self._rescore_pending = False
        self._closed = False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _read_config(self) -> Dict[str, Any]:
        try:
            return dict(self._get_config())
        except Exception:
            logger.exception("reading the taste config failed; using defaults")
            return {}

    async def _config(self) -> Dict[str, Any]:
        # get_config reads config.json from disk: keep it off the loop.
        return await asyncio.to_thread(self._read_config)

    async def _half_life(self) -> float:
        cfg = await self._config()
        return float(cfg.get("half_life_days") or DEFAULT_HALF_LIFE_DAYS)

    async def _auto_retrain(self) -> bool:
        return bool((await self._config()).get("auto_retrain", True))

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            self._broadcast("taste", event, data)
        except Exception:
            logger.exception("taste broadcast %s failed", event)

    def _indexing(self) -> bool:
        if self._is_indexing is None:
            return False
        try:
            return bool(self._is_indexing())
        except Exception:
            # The worker handle can vanish mid-poll: treat as indexing and
            # look again next tick.
            logger.debug("is_indexing probe failed", exc_info=True)
            return True

    @staticmethod
    def _under_vectored(model: Optional[Dict[str, Any]]) -> bool:
        """True when ``model`` was trained while more than
        ``MAX_NO_VECTOR_SHARE`` of its labeled rows had no vector."""
        if model is None:
            return False
        missing = int(model.get("n_no_vector") or 0)
        total = int(model.get("n_pos") or 0) + int(model.get("n_neg") or 0) + missing
        return total > 0 and missing / total > MAX_NO_VECTOR_SHARE

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
        """Sync (run in a thread): how many more labels a model needs. Uses
        the slim count query, never the full training snapshot."""
        n_pos, n_neg = count_labels(
            self.db.taste_label_counts_raw(), datetime.now(timezone.utc)
        )
        return missing_for_training(n_pos, n_neg)

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
        # "New since the last training" means nothing before the first one.
        new_events: Optional[int] = None
        if model is not None:
            new_events, _ = await asyncio.to_thread(self._events_since, model)
        missing = await asyncio.to_thread(self._missing)
        missing_reason: Optional[str] = "labels" if missing is not None else None
        if missing is None and self._vector_missing is not None:
            missing = dict(self._vector_missing)
            missing_reason = "vectors"
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
            "missing_reason": missing_reason,
            "auto_retrain": await self._auto_retrain(),
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
        # This run sees every vector there is now.
        self._embedding_ran = False
        self._train_task = asyncio.create_task(self._run_training(auto_inputs))

    async def _run_training(self, auto_inputs: Optional[Tuple[Any, ...]]) -> None:
        try:
            await self._train_once(auto_inputs)
        finally:
            self._run_pending_rescore()

    async def _train_once(self, auto_inputs: Optional[Tuple[Any, ...]]) -> None:
        self._emit("training_started", {})
        try:
            result = await self.runner.train(await self._half_life())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("taste training raised")
            result = {"status": "error", "error": str(e) or repr(e)}
        status = result.get("status")
        if status == "ok":
            if result.get("rescore_needed"):
                # The model is active but its scores failed to land: write
                # them with a rescore once this run is over.
                self._rescore_pending = True
            self._stale_announced = None
            self._last_error = None
            self._failed_auto_inputs = None
            self._vector_missing = None
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
            # already reports what is missing, so this is not an error --
            # and whatever failed before is no longer the latest news.
            # Reason "vectors": the labels are enough but too few have a
            # CLIP vector yet (partial index); status reports that shortfall.
            self._last_error = None
            if result.get("reason") == "vectors" and result.get("missing"):
                self._vector_missing = dict(result["missing"])
            self._emit("training_failed", {"error": "not_enough_data"})
            return
        error = str(result.get("error") or "taste training failed")
        self._last_error = error
        self._emit("training_failed", {"error": error})

    async def maybe_retrain(self, now: Optional[datetime] = None) -> bool:
        """One auto-retrain check; True when a training run was started."""
        if self.is_training or self._closed or not await self._auto_retrain():
            return False
        # The index is being written (every 100 files on a rebuild): its
        # fingerprint keeps changing and it holds a fraction of the vectors.
        if self._indexing():
            return False
        now = now or datetime.now(timezone.utc)
        model = await asyncio.to_thread(self.db.get_active_taste_model)
        stale = model is not None and bool(
            await asyncio.to_thread(self.runner.is_stale, model)
        )
        under_vectored = self._embedding_ran and self._under_vectored(model)
        count, newest = await asyncio.to_thread(self._events_since, model)
        # No model yet: train as soon as the labels suffice (checked below),
        # without waiting for RETRAIN_MIN_EVENTS verdict events.
        due = model is None or stale or under_vectored
        if not due and count < RETRAIN_MIN_EVENTS:
            return False
        if newest is not None:
            newest_dt = _parse_ts(newest)
            if (
                newest_dt is not None
                and (now - newest_dt).total_seconds() < RETRAIN_IDLE_S
            ):
                return False
        # The index fingerprint is part of the key: a run that failed while
        # the index was missing or mid-rebuild is retried once it changes.
        fingerprint = await asyncio.to_thread(self.runner.index_fingerprint)
        inputs = (model["id"] if model else None, stale, count, newest, fingerprint)
        if inputs == self._failed_auto_inputs:
            return False
        if await asyncio.to_thread(self._missing) is not None:
            return False
        try:
            self._spawn_training(auto_inputs=inputs)
        except TrainConflict:
            return False
        logger.info(
            "Auto-retraining the taste scorer (%d new events, stale=%s, "
            "under_vectored=%s)",
            count,
            stale,
            under_vectored,
        )
        return True

    async def retrain_loop(self) -> None:
        """Every ``RETRAIN_CHECK_S``: one ``maybe_retrain`` check. Errors
        are logged per tick; cancellation ends the loop."""
        while True:
            await asyncio.sleep(RETRAIN_CHECK_S)
            try:
                await self.check_stale()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("taste stale check failed")
            try:
                await self.maybe_retrain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("taste auto-retrain check failed")

    def start_retrain_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self.retrain_loop())

    async def check_stale(self) -> None:
        """Broadcast ``scores_updated`` when the active model turns stale
        (its scores stop being served, so the browser must drop its map)
        and again if it turns fresh (switched back to its CLIP model).
        Each transition is announced once."""
        model = await asyncio.to_thread(self.db.get_active_taste_model)
        if model is None:
            return
        stale = bool(await asyncio.to_thread(self.runner.is_stale, model))
        mid = int(model["id"])
        if stale and self._stale_announced != mid:
            self._stale_announced = mid
            self._emit("scores_updated", {"model_id": None})
        elif not stale and self._stale_announced == mid:
            self._stale_announced = None
            self._emit("scores_updated", {"model_id": mid})

    # ------------------------------------------------------------------
    # rescore
    # ------------------------------------------------------------------

    async def rescore(self) -> Optional[int]:
        """Rescore every image with the active model; broadcasts
        ``scores_updated`` on success. Never raises (except cancellation).
        Requested while a train or rescore runs, it is remembered and runs
        once when that work finishes (any number of requests coalesce)."""
        if self.is_training or self._rescoring:
            self._rescore_pending = True
            return None
        self._rescoring = True
        try:
            n = await self.runner.rescore()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("taste rescore failed")
            return None
        finally:
            self._rescoring = False
            self._run_pending_rescore()
        if n is None:
            # Possibly because the model just went stale (new index).
            try:
                await self.check_stale()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("taste stale check failed")
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

    def _run_pending_rescore(self) -> None:
        """Called as a train or rescore finishes. The spawned task's first
        step runs only after the caller's task has completed, so it no
        longer sees that work as in flight."""
        if self._rescore_pending and not self._closed:
            self._rescore_pending = False
            self._spawn_rescore()

    def _on_embedding_complete(self) -> None:
        # New vectors (possibly a whole new index): a failed auto-retrain
        # may succeed now, so let the loop try again.
        self._failed_auto_inputs = None
        self._vector_missing = None
        self._embedding_ran = True
        self._spawn_rescore()

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
            loop.call_soon_threadsafe(self._on_embedding_complete)
        except RuntimeError:
            logger.debug("taste rescore skipped: server loop closed")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def wait_idle(self) -> None:
        """Await training and rescore tasks, including follow-ups they
        spawn, until none is left (tests)."""
        while True:
            tasks = [
                t
                for t in (self._train_task, *self._rescore_tasks)
                if t is not None and not t.done()
            ]
            if not tasks:
                return
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
