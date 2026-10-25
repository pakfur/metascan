"""Background re-embedding of deleted graded images for the current CLIP
model (spec docs/superpowers/specs/2026-10-24-pass-fail-grades-design.md,
"Deleting keeps the signal", items 5 and 6).

A deleted graded image (a tombstone) keeps one CLIP vector per CLIP model in
``tombstone_vectors``; the taste scorer and the ✓ pass diversity read only
the one for the FAISS index's model. A tombstone gets it at deletion (from
the in-memory index), from the by-path backfill once the index loads
(``judgments_service.backfill_tombstone_embeddings``; never for a tombstone
with an archived thumbnail) or from here: after a CLIP switch -- or when it
was deleted with the index out of memory -- the vector is computed from the
image's archived thumbnail (``training_archive``; never from its old path,
which may hold another file now) through the live CLIP inference worker's
``encode_image``, which has no FAISS or DB side effects.

Rules
-----
* The model is the index's, while the live worker is configured for that
  model too (``reembed_model_key``). With no index, or mid-switch (the
  worker already runs the new model, the index is not rebuilt yet), nothing
  runs: the index build that follows the switch notifies again.
* It starts the CLIP worker itself (``ensure_started``) instead of waiting
  for a search. It is a GPU user like any other: ``blocked()`` -- writer
  training holds the GPU lock, a T2I batch or ComfyUI job is in flight, an
  index build runs (``reembed_blocked``) -- is checked before CLIP starts
  and between images. When it turns true the run stops (the image being
  encoded finishes, or is dropped and redone later if training unloaded
  CLIP) and resumes on the next notify, which it sends itself every
  ``retry_s`` while paused. It is NOT one of the training manager's
  ``gpu_busy`` reasons: a training start wins, and the next image finds the
  lock held or CLIP stopped.
* Single-flight and debounced: ``notify()`` (from any thread) schedules a
  run ``delay_s`` later; a notify during a run makes it look again after.
  The lifespan notifies once at startup and after every embedding run.
* Resumable: every run lists what is still missing.
* Only an archive that is gone (or not an archive path) is skipped until
  the server restarts. A CLIP error for an image (``InferenceError``: the
  worker reports any failure this way, a CUDA out-of-memory included)
  skips it for the rest of the pass only; images that failed before go to
  the back of the queue, so a few unreadable ones never hold up the rest.
  ``MAX_CONSECUTIVE_ERRORS`` errors in a row end the pass, and it tries
  again on its own after ``retry_s``, doubling up to ``MAX_ERROR_RETRY_S``
  while it keeps failing. A pass that skipped anything ends with one
  WARNING giving the counts.
* Any other CLIP failure (it did not start, it stopped while not blocked --
  an explicit stop is the user's -- or its model changed) ends the pass;
  the next notify tries again.
* When a pass ends with vectors added, ``on_done(n_added)`` -- the lifespan
  marks the taste scorer due (``TasteService.mark_due("reembed")``).
* ``status()`` is ``{running, done, total}`` while a pass is unfinished
  (``running`` False while it waits for the GPU), else None; ``on_progress``
  gets it on every change (at most once a second, plus every start, pause
  and end) and None when the pass ends.
* The CLIP worker stays loaded afterwards, as after a search.
* Only counts and model keys are logged at INFO / WARNING (no paths, no
  captions).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    AbstractSet,
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Union,
)

import numpy as np

from backend.services.auto_index import generation_busy
from metascan.core.gpu_guard import gpu_reserved
from metascan.core.inference_client import InferenceError
from metascan.core.training_archive import resolve_thumb

logger = logging.getLogger(__name__)

DEFAULT_DELAY_S = 15.0
RETRY_S = 30.0
PROGRESS_EVERY_S = 1.0
# CLIP errors in a row that end a pass (CLIP itself is failing, e.g. out of
# GPU memory), and the longest wait before it tries again.
MAX_CONSECUTIVE_ERRORS = 5
MAX_ERROR_RETRY_S = 900.0

Status = Dict[str, Union[int, bool]]

# _run_pass outcomes.
_DONE = "done"
_PAUSED = "paused"
_FAILING = "failing"
_ABORTED = "aborted"
_CLOSED = "closed"


def reembed_blocked(
    t2i_runner: Any, comfy_client: Any, is_indexing: Callable[[], bool]
) -> bool:
    """True while the re-embedder must leave the GPU alone: writer training
    holds it (``gpu_reserved``), generation is in flight
    (``generation_busy``: a T2I batch, any ComfyUI job) or an index build
    runs -- what ``AutoIndexScheduler`` waits out, plus the training lock."""
    return bool(
        gpu_reserved() or is_indexing() or generation_busy(t2i_runner, comfy_client)
    )


def reembed_model_key(
    index_model: Optional[str], clip_model: Optional[str]
) -> Optional[str]:
    """The CLIP model to re-embed for: the index's (the one the taste scorer
    reads) while the live worker is configured for it too. None without an
    index, or while the two differ (a switch before its rebuild): starting
    CLIP for the index's model then would fight every search for the
    worker."""
    if not index_model or index_model != clip_model:
        return None
    return index_model


@dataclass
class _Pass:
    """One unfinished pass over the tombstones missing a vector for a model."""

    model_key: str
    total: int
    done: int = 0
    added: int = 0
    running: bool = False
    # Images CLIP failed on in this pass: not tried again until the next.
    skipped: Set[int] = field(default_factory=set)
    # CLIP errors since the last image it encoded.
    errors: int = 0
    # Images whose archived thumbnail is gone.
    missing: int = 0


class TombstoneReembedder:
    def __init__(
        self,
        db: Any,
        client_factory: Callable[[], Any],
        model_key_provider: Callable[[], Optional[str]],
        blocked: Callable[[], bool],
        on_done: Callable[[int], None],
        *,
        device_provider: Optional[Callable[[], str]] = None,
        on_progress: Optional[Callable[[Optional[Status]], None]] = None,
        delay_s: float = DEFAULT_DELAY_S,
        retry_s: float = RETRY_S,
    ) -> None:
        """``client_factory`` returns the live ``InferenceClient`` (or None);
        ``model_key_provider`` and ``device_provider`` may block (they run
        in a worker thread); ``blocked``, ``on_done`` and ``on_progress``
        run on the loop and must not."""
        self._db = db
        self._client_factory = client_factory
        self._model_key_provider = model_key_provider
        self._blocked = blocked
        self._on_done = on_done
        self._device_provider = device_provider
        self._on_progress = on_progress
        self.delay_s = float(delay_s)
        self.retry_s = float(retry_s)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._task: Optional["asyncio.Task[None]"] = None
        self._retry: Optional[asyncio.TimerHandle] = None
        self._deadline = 0.0
        # Set by every notify; a notify during a run makes it run again.
        self._dirty = False
        self._closed = False
        self._pass: Optional[_Pass] = None
        # (tombstone id, model key) whose archived thumbnail is gone: skipped
        # until the server restarts. Nothing else is ever given up on.
        self._failed: Set[Tuple[int, str]] = set()
        # (tombstone id, model key) -> CLIP errors so far: those images are
        # tried after the others (cleared when one is encoded).
        self._attempts: Dict[Tuple[int, str], int] = {}
        # The wait before trying again after CLIP errors in a row.
        self._error_retry_s = self.retry_s
        self._last_emit = 0.0

    # -- public ---------------------------------------------------------

    def notify(self) -> None:
        """Schedule a run ``delay_s`` from now (notifies coalesce). Safe from
        any thread (the embedding-complete hook runs on a worker thread)."""
        if self._closed:
            return
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is None:
            self._loop = running
        loop = self._loop
        if loop is None:
            logger.debug("tombstone re-embed skipped: no event loop")
            return
        if running is loop:
            self._arm(self.delay_s)
            return
        try:
            loop.call_soon_threadsafe(self._arm, self.delay_s)
        except RuntimeError:
            logger.debug("tombstone re-embed skipped: event loop closed")

    def status(self) -> Optional[Status]:
        """``{running, done, total}`` while a pass is unfinished, else None."""
        p = self._pass
        if p is None:
            return None
        return {"running": p.running, "done": p.done, "total": p.total}

    async def wait_idle(self) -> None:
        """Wait until the current run has finished or paused (tests)."""
        while self._task is not None and not self._task.done():
            await asyncio.wait({self._task})

    async def aclose(self) -> None:
        """Shutdown: stop scheduling, cancel a run and wait for it. Only the
        run's own cancellation is absorbed."""
        self._closed = True
        self._cancel_retry()
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception:
            pass

    # -- scheduling -----------------------------------------------------

    def _arm(self, delay: float) -> None:
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        self._dirty = True
        self._deadline = loop.time() + delay
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run(), name="tombstone-reembed")

    def _schedule_retry(self, delay: float) -> None:
        self._cancel_retry()
        if self._closed:
            return
        loop = asyncio.get_running_loop()
        self._retry = loop.call_later(delay, self._arm, 0.0)

    def _cancel_retry(self) -> None:
        handle, self._retry = self._retry, None
        if handle is not None:
            handle.cancel()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._closed:
            remaining = self._deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
                continue
            self._dirty = False
            self._cancel_retry()
            try:
                outcome = await self._run_pass()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Re-embedding deleted images failed")
                self._end_pass()
                outcome = _ABORTED
            if outcome == _PAUSED:
                # Resume on the next notify: ours, after retry_s.
                self._schedule_retry(self.retry_s)
                return
            if outcome == _FAILING:
                # CLIP keeps failing: try again later, waiting longer each
                # time (a notify meanwhile does not hurry it).
                delay = self._error_retry_s
                self._error_retry_s = min(delay * 2, MAX_ERROR_RETRY_S)
                self._schedule_retry(delay)
                return
            if outcome == _CLOSED or not self._dirty:
                return

    # -- one run --------------------------------------------------------

    def _is_blocked(self) -> bool:
        try:
            return bool(self._blocked())
        except Exception:
            # A probe that cannot answer counts as busy: try again later.
            logger.debug("re-embed busy probe failed", exc_info=True)
            return True

    def _current_model_key(self) -> Optional[str]:
        """Blocking (worker thread)."""
        try:
            key = self._model_key_provider()
        except Exception:
            logger.warning("Could not tell the current CLIP model", exc_info=True)
            return None
        return str(key) if key else None

    def _device(self) -> str:
        """Blocking (worker thread)."""
        if self._device_provider is None:
            return "auto"
        try:
            return str(self._device_provider() or "auto")
        except Exception:
            logger.debug("reading the CLIP device failed", exc_info=True)
            return "auto"

    def _pending(
        self,
        model_key: str,
        skip: AbstractSet[int],
        failed: AbstractSet[Tuple[int, str]],
        attempts: Mapping[Tuple[int, str], int],
    ) -> List[Tuple[int, str]]:
        """Blocking (worker thread): what still needs a vector for the model,
        leaving out ``skip`` (handled in this run or skipped in this pass)
        and gone archives; images CLIP failed on before come last."""
        rows = [
            (tomb_id, thumb)
            for tomb_id, thumb in self._db.tombstones_needing_vectors(model_key)
            if tomb_id not in skip and (tomb_id, model_key) not in failed
        ]
        # Stable: newest first within each group.
        return sorted(rows, key=lambda row: attempts.get((row[0], model_key), 0))

    async def _run_pass(self) -> str:
        """Re-embed what is missing for the current model until nothing is
        (done), the GPU is wanted elsewhere (paused), CLIP keeps failing
        (failing) or CLIP stops (aborted)."""
        current = self._pass
        if current is not None and not current.running and self._is_blocked():
            return _PAUSED  # still waiting for the GPU: no need to list
        model_key = await asyncio.to_thread(self._current_model_key)
        if model_key is None:
            self._end_pass()
            return _DONE
        # Tombstones handled in this run (vector stored, or skipped): an
        # image that came back without a vector is not tried twice in one run.
        attempted: Set[int] = set()
        client: Any = None
        while not self._closed:
            pending = await asyncio.to_thread(
                self._pending,
                model_key,
                self._not_again(model_key, attempted),
                frozenset(self._failed),
                dict(self._attempts),
            )
            p = self._track(model_key, len(pending))
            if p is None:
                return _DONE
            if self._is_blocked():
                return self._pause()
            if client is None:
                client = await self._start_clip(model_key)
                if client is None:
                    if self._is_blocked():
                        return self._pause()
                    return self._abort("CLIP did not start")
            self._set_running(p)
            outcome = await self._embed_all(client, p, pending, attempted)
            if outcome is not None:
                return outcome
        return _CLOSED

    def _not_again(self, model_key: str, attempted: Set[int]) -> FrozenSet[int]:
        """The images not to list again: handled in this run, or skipped in
        this pass (a CLIP error) when the pass is for ``model_key``."""
        p = self._pass
        if p is not None and p.model_key == model_key:
            return frozenset(attempted | p.skipped)
        return frozenset(attempted)

    def _track(self, model_key: str, n_pending: int) -> Optional[_Pass]:
        """The pass for ``model_key`` with ``n_pending`` images still to do:
        a new one, or the current one with its total updated. None when
        nothing is pending (a current pass is ended then); a pass for
        another model (the index moved to another CLIP model) is ended
        first."""
        p = self._pass
        if p is not None and p.model_key != model_key:
            self._end_pass()
            p = None
        if n_pending == 0:
            self._end_pass()
            return None
        if p is None:
            p = self._pass = _Pass(model_key, total=n_pending)
            logger.info(
                "Re-embedding %d deleted image(s) for CLIP model %s",
                n_pending,
                model_key,
            )
        else:
            p.total = p.done + n_pending
        return p

    def _set_running(self, p: _Pass) -> None:
        if not p.running:
            p.running = True
            self._emit(force=True)

    async def _embed_all(
        self,
        client: Any,
        p: _Pass,
        pending: List[Tuple[int, str]],
        attempted: Set[int],
    ) -> Optional[str]:
        """Each pending image in turn, checking ``blocked()`` before each.
        None when all were handled, else the run's outcome."""
        for tomb_id, thumb in pending:
            if self._closed:
                return _CLOSED
            if self._is_blocked():
                return self._pause()
            attempted.add(tomb_id)
            outcome = await self._embed_one(client, p, tomb_id, thumb)
            if outcome is not None:
                return outcome
        return None

    async def _start_clip(self, model_key: str) -> Any:
        """The client with its worker (re)started for ``model_key`` on the
        configured device, or None when there is no client or it did not
        start (training took the GPU, the spawn failed)."""
        client = self._client_factory()
        if client is None:
            return None
        device = await asyncio.to_thread(self._device)
        try:
            await client.ensure_started(model_key=model_key, device=device)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._is_blocked():
                logger.warning(
                    "Re-embedding deleted images: CLIP did not start (%s)",
                    type(exc).__name__,
                )
            return None
        return client

    async def _embed_one(
        self, client: Any, p: _Pass, tomb_id: int, thumb: str
    ) -> Optional[str]:
        """Encode one archived thumbnail and store its vector. None to go
        on, else the run's outcome."""
        path = await asyncio.to_thread(resolve_thumb, Path(self._db.db_path), thumb)
        if path is None:
            # Gone for good (or not an archive path): until the restart.
            self._failed.add((tomb_id, p.model_key))
            logger.debug("Tombstone %d: its archived thumbnail is gone", tomb_id)
            p.missing += 1
            p.done += 1
            self._emit()
            return None
        try:
            vec = await client.encode_image(str(path))
        except asyncio.CancelledError:
            raise
        except InferenceError as exc:
            return self._encode_failed(p, tomb_id, exc)
        except Exception as exc:
            # The worker went away (training unloaded it, a stop, a crash)
            # or did not answer. Not this image's fault: it is redone later.
            if self._is_blocked():
                return self._pause()
            return self._abort(f"CLIP stopped ({type(exc).__name__})")
        if getattr(client, "model_key", None) != p.model_key:
            # Reloaded with another model while this image was encoded: the
            # vector is not one of p.model_key's.
            return self._abort("the CLIP model changed")
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        dim = getattr(client, "dim", None)
        if (
            arr.size == 0
            or (dim and arr.size != int(dim))
            or not np.isfinite(arr).all()
        ):
            return self._abort("CLIP returned an unusable vector")
        added = await asyncio.to_thread(
            self._db.add_tombstone_vectors, [(tomb_id, p.model_key, arr.tobytes())]
        )
        p.added += int(added)
        p.done += 1
        p.errors = 0
        self._attempts.pop((tomb_id, p.model_key), None)
        self._error_retry_s = self.retry_s
        self._emit()
        return None

    def _encode_failed(
        self, p: _Pass, tomb_id: int, exc: InferenceError
    ) -> Optional[str]:
        """CLIP answered with an error for this image. The worker reports any
        failure so (a CUDA out-of-memory too), so the image is never given
        up on: it is skipped for the rest of this pass and tried again on
        the next one, after the images that have not failed. Errors in a row
        mean CLIP itself is failing: the pass ends (``_FAILING``)."""
        key = (tomb_id, p.model_key)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        p.skipped.add(tomb_id)
        p.errors += 1
        p.done += 1
        logger.debug("Tombstone %d: CLIP could not encode it (%s)", tomb_id, exc)
        self._emit()
        if p.errors < MAX_CONSECUTIVE_ERRORS:
            return None
        self._end_pass(stopped=f"{p.errors} CLIP errors in a row, it tries again later")
        return _FAILING

    def _pause(self) -> str:
        p = self._pass
        if p is not None:
            if p.running:
                logger.info(
                    "Re-embedding deleted images paused at %d / %d: the GPU is busy",
                    p.done,
                    p.total,
                )
                p.running = False
            self._emit(force=True)
        return _PAUSED

    def _abort(self, why: str) -> str:
        if self._pass is None:
            logger.warning("Re-embedding deleted images stopped: %s", why)
        self._end_pass(stopped=why)
        return _ABORTED

    def _end_pass(self, stopped: Optional[str] = None) -> None:
        """End the pass: one log line (a WARNING with the counts when it
        stopped early or skipped any image), None to ``on_progress``, and
        ``on_done`` when it added vectors."""
        p, self._pass = self._pass, None
        if p is None:
            return
        if stopped or p.skipped or p.missing:
            logger.warning(
                "Re-embedded %d of %d deleted image(s) for CLIP model %s%s: "
                "%d could not be encoded (tried again on the next pass), "
                "%d archived thumbnail(s) gone",
                p.added,
                p.total,
                p.model_key,
                f" (stopped: {stopped})" if stopped else "",
                len(p.skipped),
                p.missing,
            )
        else:
            logger.info(
                "Re-embedded %d of %d deleted image(s) for CLIP model %s",
                p.added,
                p.total,
                p.model_key,
            )
        self._emit(force=True)
        if p.added > 0:
            try:
                self._on_done(p.added)
            except Exception:
                logger.exception("Re-embed completion hook failed")

    def _emit(self, force: bool = False) -> None:
        cb = self._on_progress
        if cb is None:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_EVERY_S:
            return
        self._last_emit = now
        try:
            cb(self.status())
        except Exception:
            logger.debug("re-embed progress callback failed", exc_info=True)
