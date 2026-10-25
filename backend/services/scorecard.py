"""Writer-adapter scorecard: does a trained prompt-writer adapter beat the
base writer on the owner's own judgments?

A scorecard run renders every developer-written brief of
``data/taste/scorecard_briefs.txt`` twice -- one prompt by the base writer,
one by the adapter, same seed -- as one Manual-style T2I batch per brief
(two supplied prompts = two one-image steps sharing the caption's seed).
Every image is tagged ``t2i_images.scorecard_id``. The order of the two
within a brief is randomized (seeded by the scorecard id and the brief
index) so position never gives the writer away, and the viewer / T2I tiles
hide the writer, the variant and the prompt of a scorecard image until it
has a grade (✓ pass or ✗ fail). ★ is no grade: it neither judges nor
reveals an image (the writer would show before the grade).

Results per adapter, over every scorecard of it, are computed from its
images -- live ones, and deleted ones that had a grade (from their
tombstones; see ``DatabaseManager.scorecard_image_rows``), so deleting an
image after grading it changes nothing:

* a **pair** is a brief's batch holding a base image and an adapter image;
* an image is **judged** only with a grade -- the same rule that lifts the
  blind. ★, a view or the pre-grades reject flag never judge one;
* ``n_judged`` counts pairs whose two images are both graded;
  ``pass_X`` = the ✓ among writer X's images of those pairs and
  ``pass_rate_X`` = ``pass_X / n_judged``; ``mean_score_X`` = the mean
  stored taste score of X's scorecard images that have one (None if none;
  deleted images have none).

An adapter goes ``untested -> ready`` once ``READY_PAIRS`` pairs are judged
(checked after every grade of a scorecard image and on the results GET; a
ready adapter stays ready) and ``training adapters_changed`` is broadcast.

Scorecard images are real generations: their grades are taste labels like
any other, but ``writer_dataset`` never uses them as writer training data.
A deleted adapter's scorecard images stay (real media) and stay blind
until graded -- nothing special-cases the deletion.

One run at a time; cancellable; it refuses (and stops) while writer
training holds the GPU, and training refuses to start while it runs
(``scorecard_busy`` feeds the lifespan's ``gpu_busy``). A server restart
marks a running scorecard ``interrupted``; starting it again renders only
the briefs that have no complete pair. WS ``training`` events:
``scorecard_started``, ``scorecard_progress {adapter_id, scorecard_id,
phase, done, total}``, ``scorecard_complete``, ``scorecard_failed {...,
code, error}``, ``scorecard_cancelled``. Briefs and prompts are never
logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from metascan.core.gpu_guard import GPU_BUSY_MESSAGE, GPU_BUSY_TRAINING, gpu_reserved
from metascan.core.judgments import GRADES, WRITER_BASE
from metascan.core.t2i_characters import has_tokens
from metascan.core.t2i_form import SEED_MAX
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.core.t2i_runner import BatchRequest
from metascan.core.t2i_wildcards import caption_reject_reason
from metascan.core.vlm_client import VlmError
from metascan.core.writer_adapters import (
    ADAPTER_LOADING,
    SERVABLE_STATUSES,
    STATUS_UNTESTED,
    WriterAdapterUnavailable,
    adapter_writer_id,
)

logger = logging.getLogger(__name__)

# The committed brief set (a source file, not user data).
SCORECARD_BRIEFS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "taste" / "scorecard_briefs.txt"
)
READY_PAIRS = 30
DEFAULT_TARGET_MODEL = "krea2"
SCORECARD_ASPECT = "1:1"

Broadcast = Callable[[str, Dict[str, Any]], None]

STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_INCOMPLETE = "incomplete"
STATUS_INTERRUPTED = "interrupted"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"


class ScorecardError(RuntimeError):
    """A refused or failed scorecard: ``status`` is the HTTP status the
    route answers, ``code`` + ``extra`` its detail."""

    def __init__(
        self,
        status: int,
        code: str,
        message: Optional[str] = None,
        **extra: Any,
    ) -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"code": self.code, **self.extra}
        if self.message is not None:
            out["message"] = self.message
        return out


# ---- briefs, seeds, order --------------------------------------------------------


def load_briefs(path: Path) -> Tuple[List[str], int]:
    """``(briefs, rejected)``: one brief per non-blank, non-``#`` line,
    whitespace collapsed, duplicates dropped. A line that fails the T2I
    caption screen (adult-only terms, parentheses) or carries a
    ``__TOKEN__`` is rejected and counted (never logged). A missing or
    unreadable file gives no briefs."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return [], 0
    briefs: List[str] = []
    seen: Set[str] = set()
    rejected = 0
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or line.startswith("#"):
            continue
        if caption_reject_reason(line) is not None or has_tokens(line):
            rejected += 1
            continue
        if line in seen:
            continue
        seen.add(line)
        briefs.append(line)
    if rejected:
        logger.warning(
            "scorecard briefs: %d line(s) failed the caption screen", rejected
        )
    return briefs, rejected


def _digest(scorecard_id: str, index: int, what: str) -> int:
    key = f"{scorecard_id}|{int(index)}|{what}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def brief_seed(scorecard_id: str, index: int) -> int:
    """The seed both images of brief ``index`` render with."""
    return _digest(scorecard_id, index, "seed") % (SEED_MAX + 1)


def adapter_first(scorecard_id: str, index: int) -> bool:
    """Whether the adapter's prompt is variant 0 of brief ``index``."""
    return _digest(scorecard_id, index, "order") % 2 == 1


# ---- results -----------------------------------------------------------------------


def _judged(row: Dict[str, Any]) -> bool:
    """A grade (✓ pass or ✗ fail): ★ and views never judge an image."""
    return row.get("grade") in GRADES


def _is_pass(row: Dict[str, Any]) -> bool:
    return row.get("grade") == "pass"


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _pairs(
    rows: List[Dict[str, Any]], adapter_id: int
) -> List[Tuple[Dict[str, Any], Dict[str, Any], Any]]:
    """``(base row, adapter row, brief_index)``: at most one pair per
    ``(scorecard_id, brief_index)`` -- the most recent batch (rows come in
    image-id order) holding both images -- so a brief rendered twice (a
    resume while its first batch was still queued) never counts twice. A
    batch whose brief is unknown is its own pair."""
    wid = adapter_writer_id(adapter_id)
    by_batch: Dict[str, Dict[str, Dict[str, Any]]] = {}
    last_seen: Dict[str, int] = {}
    for pos, r in enumerate(rows):
        batch = r.get("batch_id")
        writer = r.get("writer_id")
        if batch is None or writer not in (WRITER_BASE, wid):
            continue
        by_batch.setdefault(str(batch), {}).setdefault(str(writer), r)
        last_seen[str(batch)] = pos
    chosen: Dict[Any, Tuple[int, Dict[str, Any], Dict[str, Any]]] = {}
    for batch, writers in by_batch.items():
        if WRITER_BASE not in writers or wid not in writers:
            continue
        base = writers[WRITER_BASE]
        index = base.get("brief_index")
        key: Any = (
            ("brief", base.get("scorecard_id"), index)
            if index is not None
            else ("batch", batch)
        )
        pos = last_seen[batch]
        if key not in chosen or pos > chosen[key][0]:
            chosen[key] = (pos, base, writers[wid])
    return [
        (base, adapted, base.get("brief_index"))
        for _, base, adapted in sorted(chosen.values(), key=lambda t: t[0])
    ]


def compute_results(rows: List[Dict[str, Any]], adapter_id: int) -> Dict[str, Any]:
    """The scorecard result of ``adapter_id`` over ``rows`` (its scorecard
    images; see the module docstring for the definitions)."""
    wid = adapter_writer_id(adapter_id)
    pairs = _pairs(rows, adapter_id)
    judged = [(b, a) for b, a, _ in pairs if _judged(b) and _judged(a)]
    n = len(judged)
    pass_base = sum(1 for b, _ in judged if _is_pass(b))
    pass_adapter = sum(1 for _, a in judged if _is_pass(a))
    return {
        "adapter_id": adapter_id,
        "n_pairs": len(pairs),
        "n_judged": n,
        "pass_base": pass_base,
        "pass_adapter": pass_adapter,
        "pass_rate_base": pass_base / n if n else None,
        "pass_rate_adapter": pass_adapter / n if n else None,
        "mean_score_base": _mean(
            r.get("score") for r in rows if r.get("writer_id") == WRITER_BASE
        ),
        "mean_score_adapter": _mean(
            r.get("score") for r in rows if r.get("writer_id") == wid
        ),
    }


def _paired_briefs(rows: List[Dict[str, Any]], adapter_id: int) -> Set[int]:
    return {int(i) for _, _, i in _pairs(rows, adapter_id) if i is not None}


async def evaluate_adapter(
    db: Any, adapter_id: int, broadcast: Broadcast
) -> Dict[str, Any]:
    """The adapter's results; promotes ``untested -> ready`` (once) when
    ``READY_PAIRS`` pairs are judged and announces it."""
    rows = await asyncio.to_thread(db.scorecard_image_rows, adapter_id)
    result = compute_results(rows, adapter_id)
    if result["n_judged"] >= READY_PAIRS:
        if await asyncio.to_thread(db.promote_writer_adapter, adapter_id):
            logger.info(
                "writer adapter %d is ready: %d scorecard pairs judged",
                adapter_id,
                result["n_judged"],
            )
            broadcast("adapters_changed", {})
    return result


async def note_scorecard_judgment(
    db: Any, file_path: str, broadcast: Broadcast
) -> None:
    """After a grade change of ``file_path``: when it is a scorecard
    image, re-check its adapter's promotion (untested only) and announce
    ``scorecard_judged {adapter_id}`` (the training window refetches the
    results). Cheap for every other image (one indexed lookup); never
    raises."""
    try:
        info = await asyncio.to_thread(db.scorecard_for_path, file_path)
        if info is None or info.get("adapter_id") is None:
            return
        adapter_id = int(info["adapter_id"])
        row = await asyncio.to_thread(db.get_writer_adapter, adapter_id)
        if row is not None and row.get("status") == STATUS_UNTESTED:
            await evaluate_adapter(db, adapter_id, broadcast)
        broadcast("scorecard_judged", {"adapter_id": adapter_id})
    except Exception:
        logger.exception("Scorecard promotion check failed")


async def notify_scorecard_judgment(db: Any, file_path: str) -> None:
    """The grade route's hook: ``note_scorecard_judgment`` broadcasting on
    WS ``training``. ★, reasons and seen never call it (none is a grade)."""
    from backend.ws.manager import ws_manager

    await note_scorecard_judgment(
        db,
        file_path,
        lambda event, data: ws_manager.broadcast_sync("training", event, data),
    )


def _card_public(card: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if card is None:
        return None
    return {
        "id": card["id"],
        "status": card["status"],
        "target_model": card["target_model"],
        "done": card["done"],
        "total": card["total"],
        "error": card.get("error"),
        "created_at": card["created_at"],
        "updated_at": card["updated_at"],
    }


def scorecard_summary(
    db: Any,
    briefs_path: Path = SCORECARD_BRIEFS_PATH,
    with_empty: bool = True,
    running: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Every live T2I adapter's scorecard result (``with_empty=False``:
    only those with a pair), its latest scorecard, the base writer pooled
    over every judged pair (``{n_judged, pass_rate, mean_score}``), the
    brief count and ``running`` (the caller reads
    ``ScorecardService.running()`` on the event loop: this runs in a
    worker thread and must not touch the asyncio task)."""
    rows = db.scorecard_image_rows()
    cards = db.list_scorecards()
    adapters = [a for a in db.list_writer_adapters() if a.get("kind") == "t2i"]
    out_rows: List[Dict[str, Any]] = []
    for a in adapters:
        aid = int(a["id"])
        result = compute_results([r for r in rows if r["adapter_id"] == aid], aid)
        if not with_empty and result["n_pairs"] == 0:
            continue
        latest = [c for c in cards if int(c["adapter_id"]) == aid]
        out_rows.append(
            {
                **result,
                "name": a["name"],
                "status": a["status"],
                "latest": _card_public(latest[-1] if latest else None),
            }
        )
    # The base writer over every scorecard (deleted adapters' included).
    n_judged = 0
    n_pass = 0
    by_adapter: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_adapter.setdefault(int(r["adapter_id"]), []).append(r)
    for aid, own in by_adapter.items():
        res = compute_results(own, aid)
        n_judged += res["n_judged"]
        n_pass += res["pass_base"]
    return {
        "briefs": len(load_briefs(briefs_path)[0]),
        "ready_pairs": READY_PAIRS,
        "running": running,
        "base": {
            "n_judged": n_judged,
            "pass_rate": n_pass / n_judged if n_judged else None,
            "mean_score": _mean(
                r.get("score") for r in rows if r.get("writer_id") == WRITER_BASE
            ),
        },
        "adapters": out_rows,
    }


def adapter_scorecard(
    db: Any, adapter_id: int, running: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """One adapter's result plus its latest scorecard (sync; ``running`` as
    read on the loop by the caller, kept only when it is this adapter's)."""
    rows = db.scorecard_image_rows(adapter_id)
    cards = db.list_scorecards(adapter_id)
    return {
        **compute_results(rows, adapter_id),
        "ready_pairs": READY_PAIRS,
        "latest": _card_public(cards[-1] if cards else None),
        "running": running if running and running["adapter_id"] == adapter_id else None,
    }


# ---- the run ------------------------------------------------------------------------


class ScorecardService:
    """Runs one scorecard at a time through the T2I runner."""

    def __init__(
        self,
        db: Any,
        runner: Any,
        *,
        broadcast: Broadcast,
        get_config: Callable[[], Dict[str, Any]],
        briefs_path: Path = SCORECARD_BRIEFS_PATH,
        retry_delay_s: float = 5.0,
        adapter_retries: int = 6,
    ) -> None:
        self.db = db
        self.runner = runner
        self._broadcast = broadcast
        self._get_config = get_config
        self.briefs_path = Path(briefs_path)
        self.retry_delay_s = float(retry_delay_s)
        self.adapter_retries = int(adapter_retries)
        self._lock = asyncio.Lock()
        self._task: Optional["asyncio.Task[None]"] = None
        self._state: Optional[Dict[str, Any]] = None
        self._batch_id: Optional[str] = None
        self._closing = False

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            self._broadcast(event, data)
        except Exception:
            logger.warning("scorecard broadcast failed", exc_info=True)

    def running(self) -> Optional[Dict[str, Any]]:
        """``{adapter_id, scorecard_id, phase, done, total}`` of the run in
        progress, or None."""
        if self._task is None or self._task.done() or self._state is None:
            return None
        return dict(self._state)

    async def wait(self) -> None:
        """Wait for the run in progress (if any) to end."""
        task = self._task
        if task is not None and not task.done():
            await asyncio.wait({task})

    async def recover(self) -> int:
        """Startup: scorecards a stopped server left running are interrupted."""
        n = int(await asyncio.to_thread(self.db.mark_interrupted_scorecards))
        if n:
            logger.info("Marked %d interrupted scorecard run(s)", n)
        return n

    async def cancel(self) -> bool:
        task = self._task
        if task is None or task.done():
            return False
        state = dict(self._state or {})
        task.cancel()
        await asyncio.wait({task})
        card_id = state.get("scorecard_id")
        if card_id is not None:
            # A task cancelled before its first step never ran its own
            # handler: settle the row (and the frame) here.
            card = await asyncio.to_thread(self.db.get_scorecard, card_id)
            if card is not None and card["status"] == STATUS_RUNNING:
                await self._settle_cancelled(card_id, int(state["adapter_id"]))
        return True

    async def _settle_cancelled(self, card_id: str, adapter_id: int) -> None:
        status = STATUS_INTERRUPTED if self._closing else STATUS_CANCELLED
        await asyncio.to_thread(self.db.update_scorecard, card_id, status=status)
        logger.info("Scorecard %s for adapter %d %s", card_id, adapter_id, status)
        if not self._closing:
            self._emit(
                "scorecard_cancelled",
                {"adapter_id": adapter_id, "scorecard_id": card_id},
            )

    async def aclose(self) -> None:
        self._closing = True
        await self.cancel()

    async def on_training_completed(self, adapter_id: int, render: bool) -> None:
        """The training manager's post-run hook: start the new adapter's
        scorecard when the run asked for one (after the GPU lock dropped).
        A refusal is announced, never raised."""
        if not render:
            return
        if self._closing:
            # The app is shutting down: a scorecard would only be
            # interrupted at once (it is never auto-resumed).
            logger.info("Scorecard for adapter %d skipped: shutting down", adapter_id)
            return
        try:
            await self.start(adapter_id)
        except ScorecardError as exc:
            logger.info(
                "Scorecard for adapter %d not started: %s", adapter_id, exc.code
            )
            self._emit(
                "scorecard_failed",
                {
                    "adapter_id": adapter_id,
                    "scorecard_id": None,
                    "code": exc.code,
                    "error": exc.message or exc.code,
                },
            )

    # ---- start --------------------------------------------------------------

    async def start(
        self,
        adapter_id: int,
        *,
        target_model: Optional[str] = None,
        preset_id: Optional[int] = None,
        fresh: bool = False,
    ) -> Dict[str, Any]:
        """Start (or resume) adapter ``adapter_id``'s scorecard; returns
        ``{scorecard_id, adapter_id, total, todo, resumed, target_model}``.
        Resumes the adapter's latest scorecard when it lacks pairs (and the
        target model is not changed), else renders a new one."""
        async with self._lock:
            if self._closing:
                raise ScorecardError(503, "shutting_down")
            running = self.running()
            if running is not None:
                raise ScorecardError(
                    409, "scorecard_running", adapter_id=running["adapter_id"]
                )
            if gpu_reserved():
                raise ScorecardError(409, GPU_BUSY_TRAINING, GPU_BUSY_MESSAGE)
            row = await asyncio.to_thread(self.db.get_writer_adapter, adapter_id)
            if row is None or row.get("deleted_at") is not None:
                raise ScorecardError(404, "unknown_adapter")
            if row["kind"] != "t2i" or row["status"] not in SERVABLE_STATUSES:
                raise ScorecardError(409, "adapter_unavailable")
            if target_model is not None and target_model not in MODEL_PROFILES:
                raise ScorecardError(422, "unknown_model", model=target_model)
            briefs, _ = await asyncio.to_thread(load_briefs, self.briefs_path)
            if not briefs:
                raise ScorecardError(409, "no_briefs")
            cards = await asyncio.to_thread(self.db.list_scorecards, adapter_id)
            latest = cards[-1] if cards else None
            todo = list(range(len(briefs)))
            resumed = False
            if (
                latest is not None
                and not fresh
                and target_model in (None, latest["target_model"])
            ):
                rows = await asyncio.to_thread(
                    self.db.scorecard_image_rows, None, latest["id"]
                )
                missing = [i for i in todo if i not in _paired_briefs(rows, adapter_id)]
                if missing:
                    # A brief whose batch is still in the ComfyUI queue
                    # (e.g. it survived a restart) ingests on its own:
                    # rendering it again would pair it twice.
                    pending = set(
                        await asyncio.to_thread(
                            self.db.scorecard_pending_briefs, str(latest["id"])
                        )
                    )
                    todo = [i for i in missing if i not in pending]
                    if not todo:
                        raise ScorecardError(
                            409,
                            "scorecard_pending",
                            "The scorecard's remaining images are still "
                            "rendering; try again when they finish.",
                        )
                    resumed = True
            cfg = self._get_config()
            if resumed:
                assert latest is not None
                card_id = str(latest["id"])
                model = str(latest["target_model"])
                preset = preset_id or latest.get("preset_id")
            else:
                card_id = uuid.uuid4().hex
                model = target_model or DEFAULT_TARGET_MODEL
                preset = preset_id
            if preset is None:
                preset = (cfg.get("model_workflows") or {}).get(model)
            if preset is None:
                raise ScorecardError(409, "no_workflow", model=model)
            if resumed:
                await asyncio.to_thread(
                    self.db.update_scorecard,
                    card_id,
                    status=STATUS_RUNNING,
                    error=None,
                    total=len(briefs),
                    preset_id=preset,
                )
            else:
                await asyncio.to_thread(
                    self.db.create_scorecard,
                    card_id,
                    adapter_id,
                    model,
                    preset,
                    len(briefs),
                )
            self._state = {
                "adapter_id": adapter_id,
                "scorecard_id": card_id,
                "phase": "prompting",
                "done": 0,
                "total": len(todo),
            }
            self._task = asyncio.create_task(
                self._run(
                    card_id,
                    adapter_id,
                    model,
                    int(preset),
                    float(cfg.get("default_megapixels") or 1.0),
                    briefs,
                    todo,
                ),
                name=f"scorecard-{card_id[:8]}",
            )
        logger.info(
            "Scorecard %s for adapter %d %s: %d of %d brief(s) to render",
            card_id,
            adapter_id,
            "resumed" if resumed else "started",
            len(todo),
            len(briefs),
        )
        out = {
            "scorecard_id": card_id,
            "adapter_id": adapter_id,
            "total": len(briefs),
            "todo": len(todo),
            "resumed": resumed,
            "target_model": model,
        }
        self._emit("scorecard_started", dict(out))
        return out

    # ---- the background run ----------------------------------------------------

    def _check_gpu(self) -> None:
        if gpu_reserved():
            raise ScorecardError(409, GPU_BUSY_TRAINING, GPU_BUSY_MESSAGE)

    def _progress(self, phase: str, done: int, total: int) -> None:
        if self._state is None:
            return
        self._state.update(phase=phase, done=done, total=total)
        self._emit("scorecard_progress", dict(self._state))

    async def _draft(
        self, brief: str, seed: int, model: str, writer: str
    ) -> Dict[str, Any]:
        """One supplied prompts[] entry written by ``writer`` (variant index
        set by the caller). The adapter may still be loading right after
        training: that is retried; a fallback draft (no VLM) is a failure,
        since it would compare nothing."""
        attempt = 0
        while True:
            try:
                result = await self.runner.generate_prompt(
                    caption=brief,
                    seed=seed,
                    model=model,
                    directions=False,
                    variants=1,
                    writer=writer,
                )
                break
            except WriterAdapterUnavailable as exc:
                if exc.code != ADAPTER_LOADING or attempt >= self.adapter_retries:
                    raise ScorecardError(409, exc.code) from exc
            except (VlmError, TimeoutError) as exc:
                if attempt >= 1:
                    raise ScorecardError(
                        502, "writer_failed", type(exc).__name__
                    ) from exc
            attempt += 1
            await asyncio.sleep(self.retry_delay_s)
        draft = result.drafts[0]
        if draft.writer_id != writer:
            raise ScorecardError(
                409,
                "writer_unavailable",
                "No prompt writer is available: install the writer model in Models.",
            )
        return {
            "prompt": draft.prompt,
            "negative": draft.negative,
            "draft": draft.prompt,
            "writer_id": draft.writer_id,
            "writer_model": draft.writer_model,
            "resolved_caption": result.resolved_caption,
            "direction": result.direction,
            "categories": [],
        }

    async def _paired(self, card_id: str, adapter_id: int) -> Set[int]:
        rows = await asyncio.to_thread(self.db.scorecard_image_rows, None, card_id)
        return _paired_briefs(rows, adapter_id)

    async def _run(
        self,
        card_id: str,
        adapter_id: int,
        model: str,
        preset_id: int,
        megapixels: float,
        briefs: List[str],
        todo: List[int],
    ) -> None:
        total = len(briefs)
        writer = adapter_writer_id(adapter_id)
        try:
            written: List[Tuple[int, int, List[Dict[str, Any]]]] = []
            self._progress("prompting", 0, len(todo))
            for n, index in enumerate(todo):
                self._check_gpu()
                seed = brief_seed(card_id, index)
                base = await self._draft(briefs[index], seed, model, WRITER_BASE)
                self._check_gpu()
                adapted = await self._draft(briefs[index], seed, model, writer)
                entries = (
                    [adapted, base]
                    if adapter_first(card_id, index)
                    else [base, adapted]
                )
                for k, entry in enumerate(entries):
                    entry["variant_index"] = k
                written.append((index, seed, entries))
                self._progress("prompting", n + 1, len(todo))
            self._check_gpu()
            await self.runner.unload_vlms()
            done = len(await self._paired(card_id, adapter_id))
            self._progress("rendering", done, total)
            for index, seed, entries in written:
                self._check_gpu()
                started = await self.runner.start_batch(
                    BatchRequest(
                        mode="manual",
                        model=model,
                        preset_id=preset_id,
                        megapixels=megapixels,
                        seed=seed,
                        seed_policy="fixed",
                        batch_size=1,
                        count_per_batch=1,
                        caption=briefs[index],
                        aspect_ratio=SCORECARD_ASPECT,
                        directions=False,
                        variants=len(entries),
                        writer=WRITER_BASE,
                        prompts=entries,
                        scorecard_id=card_id,
                        scorecard_brief=index,
                    )
                )
                self._batch_id = started.batch_id
                await self.runner.wait_finished(started.batch_id)
                self._batch_id = None
                done = len(await self._paired(card_id, adapter_id))
                await asyncio.to_thread(self.db.update_scorecard, card_id, done=done)
                self._progress("rendering", done, total)
            status = STATUS_COMPLETE if done >= total else STATUS_INCOMPLETE
            await asyncio.to_thread(
                self.db.update_scorecard, card_id, status=status, done=done
            )
            logger.info(
                "Scorecard %s for adapter %d %s: %d of %d pairs",
                card_id,
                adapter_id,
                status,
                done,
                total,
            )
            self._emit(
                "scorecard_complete",
                {
                    "adapter_id": adapter_id,
                    "scorecard_id": card_id,
                    "status": status,
                    "done": done,
                    "total": total,
                    "missing": total - done,
                },
            )
        except asyncio.CancelledError:
            batch_id, self._batch_id = self._batch_id, None
            # A user cancel stops the brief rendering now; a shutdown leaves
            # its jobs to ComfyUI (they ingest later, still tagged).
            if batch_id is not None and not self._closing:
                try:
                    await asyncio.shield(self.runner.cancel_batch(batch_id))
                except Exception:
                    logger.warning("could not cancel scorecard batch %s", batch_id)
            await asyncio.shield(self._settle_cancelled(card_id, adapter_id))
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, ScorecardError) else "error"
            message = (
                (exc.message or exc.code)
                if isinstance(exc, ScorecardError)
                else type(exc).__name__
            )
            # The type / code only: a message could quote a brief or prompt.
            logger.warning(
                "Scorecard %s for adapter %d failed: %s (%s)",
                card_id,
                adapter_id,
                code,
                type(exc).__name__,
            )
            await asyncio.to_thread(
                self.db.update_scorecard,
                card_id,
                status=STATUS_FAILED,
                error=message,
            )
            self._emit(
                "scorecard_failed",
                {
                    "adapter_id": adapter_id,
                    "scorecard_id": card_id,
                    "code": code,
                    "error": message,
                },
            )


# ---- process-wide instance ---------------------------------------------------------

_SERVICE: Optional[ScorecardService] = None


def set_scorecard_service(service: Optional[ScorecardService]) -> None:
    global _SERVICE
    _SERVICE = service


def get_scorecard_service() -> Optional[ScorecardService]:
    return _SERVICE


def running_scorecard() -> Optional[Dict[str, Any]]:
    """The installed service's run in progress (call on the event loop)."""
    service = get_scorecard_service()
    return service.running() if service is not None else None


def scorecard_busy(service: Optional[ScorecardService] = None) -> bool:
    """True while a scorecard runs (training refuses to start meanwhile)."""
    current = service if service is not None else _SERVICE
    return current is not None and current.running() is not None
