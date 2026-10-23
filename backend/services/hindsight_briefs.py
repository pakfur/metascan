"""Hindsight briefs job (taste learning project 4).

For every favorited generated image with a known prompt (and a known
target-model family -- the rule lives in
``writer_dataset.hindsight_candidates``) the GENERAL-role VLM looks at the
image and writes a one-sentence "what someone might have asked for" brief.
The brief is the user turn of a hindsight training example (brief -> the
prompt that produced the image); it is cached in ``hindsight_briefs``.

Rules
-----
* Background VLM work (``background_vlm_work``): when the roles take turns
  it never evicts the prompt writer.
* One image per request, sequential; resumable (images that already have a
  brief are skipped) and cancellable; one job at a time.
* Waits while ComfyUI generation is in flight (``generation_busy`` probe) or
  while ``blocked()`` says so (Task 6 passes the training GPU lock), and
  re-snapshots the client's ``_explicit_stops`` after such a wait. A VLM
  explicitly unloaded mid-run ends the job (``vlm_unloaded``): it is never
  restarted behind the user's back.
* Progress on WS ``training``: ``briefs_progress {done, total}``, then one of
  ``briefs_done`` / ``briefs_cancelled`` / ``briefs_error {code}``.
* Brief and prompt text are NEVER logged (counts only).

For Task 6 (training GPU lock): ``job.blocked`` (a callable) PAUSES a running
job between images and REFUSES new starts (409 ``gpu_busy_training``). A job
that is "running" while blocked is merely paused, so training must not be
refused just because ``status()["running"]`` is true: set ``blocked`` and/or
call ``cancel()`` instead.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from metascan.core.vlm_client import VlmError, background_vlm_work
from metascan.core.writer_dataset import hindsight_candidates

logger = logging.getLogger(__name__)

BRIEF_MAX_CHARS = 200
PROMPT_KEY = "HINDSIGHT_BRIEF_SYSTEM"
USER_PROMPT = "Write the request for this image."
POLL_S = 5.0
MAX_CONSECUTIVE_FAILURES = 5

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr st mt jr sr vs etc prof sgt capt gen col lt no fig".split()
)
_PREAMBLE = re.compile(
    r"^(here(?: is|'s| are)|sure(?=[,!.])|certainly(?=[,!.])|of course|below is|"
    r"the request|a possible request|possible request)\b",
    re.IGNORECASE,
)
_LEAD_INS = (
    re.compile(
        r"^(?:this|the)\s+(?:image|picture|photo|photograph|illustration|"
        r"artwork|render)\s+(?:shows|depicts|features|portrays|displays|"
        r"contains|captures)\s*(?:that\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:an?|the)\s+(?:image|picture|photo|photograph|illustration|"
        r"artwork|render)\s+(?:of|showing|depicting)\s+",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:request|prompt|brief|caption|description)\s*:\s*", re.IGNORECASE),
)
_WRAP_QUOTES = {'"': '"', "`": "`", "\u201c": "\u201d", "'": "'", "\u2018": "\u2019"}


def _unwrap(text: str) -> str:
    """Drop quotes wrapping the whole text (apostrophes inside words stay)."""
    text = text.strip()
    while len(text) >= 2 and _WRAP_QUOTES.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text.strip('"`\u201c\u201d')


def _first_sentence(text: str) -> str:
    start = 0
    for m in _SENTENCE_END.finditer(text):
        before = text[start : m.start()]
        last = before.split()[-1].rstrip(".!?").lower() if before.split() else ""
        if text[m.start() - 1] == "." and last in _ABBREVIATIONS:
            continue
        return text[: m.start()].strip()
    return text.strip()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def clean_brief(
    text: Any, limit: int = BRIEF_MAX_CHARS, reject: Optional[str] = None
) -> str:
    """One sentence, whitespace-collapsed, unquoted, at most ``limit`` chars
    (cut on a word boundary), with preamble lines and "This image shows"
    lead-ins removed. Empty when nothing usable, or when it is ``reject``
    (the system prompt's own example sentence echoed back)."""
    if not isinstance(text, str):
        return ""
    one = ""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or line.endswith(":") or _PREAMBLE.match(_unwrap(line)):
            continue
        one = line
        break
    one = _unwrap(one)
    changed = True
    while changed:
        changed = False
        for pat in _LEAD_INS:
            new = pat.sub("", one, count=1)
            if new != one:
                one, changed = _unwrap(new), True
    one = _unwrap(_first_sentence(one)).rstrip(".!? ")
    if len(one) > limit:
        cut = one[:limit]
        space = cut.rfind(" ")
        one = (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:-")
    if reject and _norm(one) == _norm(reject):
        return ""
    return one


class BriefsJobError(Exception):
    """A job cannot start; ``code`` is the API error code."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class HindsightBriefsJob:
    def __init__(
        self,
        *,
        db: Any,
        get_client: Callable[[], Any],
        get_store: Callable[[], Any],
        broadcast: Callable[[str, str, Dict[str, Any]], None],
        generation_busy: Optional[Callable[[], bool]] = None,
        blocked: Optional[Callable[[], bool]] = None,
        poll_s: float = POLL_S,
    ) -> None:
        self._db = db
        self._get_client = get_client
        self._get_store = get_store
        self._broadcast = broadcast
        self._generation_busy = generation_busy
        # Task 6 installs the training GPU lock here: truthy refuses a
        # start and pauses a running job until it clears.
        self.blocked = blocked
        self._poll_s = poll_s
        self._task: Optional["asyncio.Task[None]"] = None
        self._cancelled = False
        self._state: Dict[str, Any] = {
            "running": False,
            "done": 0,
            "total": 0,
            "failed": 0,
            "result": None,
        }

    # -- public --

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> Dict[str, Any]:
        return {**self._state, "running": self.running}

    def start(self) -> None:
        """Start the job on the running loop. Raises :class:`BriefsJobError`
        when one is running, the GPU is blocked or no VLM is available."""
        if self.running:
            raise BriefsJobError("briefs_running")
        if self._is_blocked():
            raise BriefsJobError("gpu_busy_training")
        if self._get_client() is None:
            raise BriefsJobError("vlm_unavailable")
        self._cancelled = False
        self._state = {
            "running": True,
            "done": 0,
            "total": 0,
            "failed": 0,
            "result": None,
        }
        self._task = asyncio.get_running_loop().create_task(self._run_bg())

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancelled = True
        return True

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            await asyncio.shield(task)

    async def aclose(self) -> None:
        """Shutdown hook: stop the job and wait for it. Only the job
        task's own cancellation is absorbed; if the caller is cancelled
        meanwhile that propagates."""
        self._cancelled = True
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

    # -- internals --

    def _is_blocked(self) -> bool:
        fn = self.blocked
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            return True

    def _generating(self) -> bool:
        fn = self._generation_busy
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            # A probe that cannot answer counts as busy.
            return True

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            self._broadcast("training", event, data)
        except Exception:
            logger.debug("briefs broadcast failed", exc_info=True)

    def _progress(self) -> None:
        self._emit(
            "briefs_progress",
            {"done": self._state["done"], "total": self._state["total"]},
        )

    async def _wait_clear(self) -> bool:
        """Wait while generation is in flight or the GPU is blocked. True
        when it had to wait."""
        waited = False
        while not self._cancelled and (self._generating() or self._is_blocked()):
            if not waited:
                logger.info("hindsight briefs paused: GPU busy")
            waited = True
            await asyncio.sleep(self._poll_s)
        return waited

    async def _run_bg(self) -> None:
        try:
            with background_vlm_work():
                result = await self._run()
        except asyncio.CancelledError:
            self._state["result"] = "cancelled"
            self._state["running"] = False
            raise
        except Exception:
            logger.exception("hindsight briefs job crashed")
            result = "error"
            self._emit("briefs_error", {"code": "internal_error"})
        self._state["running"] = False
        self._state["result"] = result

    def _finish(self, result: str, code: Optional[str] = None) -> str:
        data = {
            "done": self._state["done"],
            "total": self._state["total"],
            "failed": self._state["failed"],
        }
        if result == "done":
            self._emit("briefs_done", data)
        elif result == "cancelled":
            self._emit("briefs_cancelled", data)
        else:
            self._emit("briefs_error", {**data, "code": code})
        logger.info(
            "hindsight briefs %s: %d/%d done, %d failed",
            result,
            data["done"],
            data["total"],
            data["failed"],
        )
        return result if code is None else f"error:{code}"

    async def _run(self) -> str:
        db = self._db
        raw = await asyncio.to_thread(db.writer_dataset_raw, True)
        candidates: List[str] = hindsight_candidates(raw)
        have = await asyncio.to_thread(db.get_hindsight_briefs)
        pending = [p for p in candidates if p not in have]
        self._state["total"] = len(candidates)
        self._state["done"] = len(candidates) - len(pending)
        self._progress()
        if not pending:
            return self._finish("done")

        client = self._get_client()
        if client is None:
            return self._finish("error", "vlm_unavailable")
        model_id = getattr(client, "model_id", None) or getattr(
            client, "preferred_model_id", None
        )
        if not isinstance(model_id, str) or not model_id:
            return self._finish("error", "vlm_unavailable")
        system_prompt = self._get_store().get(PROMPT_KEY)
        quoted = re.search(r'"([^"]+)"', system_prompt)
        example = quoted.group(1) if quoted else None

        stops: Optional[int] = None
        failures = 0
        first = True
        for path in pending:
            if self._cancelled:
                return self._finish("cancelled")
            waited = await self._wait_clear()
            if self._cancelled:
                return self._finish("cancelled")
            if first or waited:
                # The start (or the render's own unload behind a wait) is a
                # deliberate (re)start; an unload after it is the user's.
                stops = getattr(client, "_explicit_stops", None)
                try:
                    if isinstance(stops, int):
                        await client.ensure_started(
                            model_id, unless_stopped_since=stops
                        )
                    else:
                        await client.ensure_started(model_id)
                except VlmError:
                    return self._finish("error", "vlm_unavailable")
                first = False
            image = Path(path)
            brief = ""
            try:
                if image.is_file():
                    text = await client.generate_text(
                        system_prompt=system_prompt,
                        user_prompt=USER_PROMPT,
                        image_path=image,
                        temperature=0.3,
                        max_tokens=100,
                    )
                    brief = clean_brief(text, reject=example)
            except Exception as exc:
                if self._cancelled:
                    return self._finish("cancelled")
                if (
                    isinstance(stops, int)
                    and getattr(client, "_explicit_stops", stops) != stops
                ):
                    return self._finish("error", "vlm_unloaded")
                logger.warning("hindsight brief failed: %s", type(exc).__name__)
            if self._cancelled:
                return self._finish("cancelled")
            stored = False
            if brief:
                stored = await asyncio.to_thread(
                    db.set_hindsight_brief, path, brief, model_id
                )
            if stored:
                failures = 0
                self._state["done"] += 1
                self._progress()
            else:
                failures += 1
                self._state["failed"] += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    return self._finish("error", "vlm_failing")
        return self._finish("done")


# ---- process singleton wired to the app -----------------------------------

_job: Optional[HindsightBriefsJob] = None


def get_briefs_job() -> HindsightBriefsJob:
    global _job
    if _job is None:
        from backend.api import similarity
        from backend.api.vlm import get_vlm_client
        from backend.dependencies import get_db
        from backend.ws.manager import ws_manager
        from metascan.core.prompt_store import get_prompt_store

        _job = HindsightBriefsJob(
            db=get_db(),
            get_client=get_vlm_client,
            get_store=get_prompt_store,
            broadcast=ws_manager.broadcast_sync,
            generation_busy=similarity.generation_in_flight,
        )
    return _job


def peek_briefs_job() -> Optional[HindsightBriefsJob]:
    """The job if one was ever created (shutdown must not build one)."""
    return _job


def set_briefs_job(job: Optional[HindsightBriefsJob]) -> None:
    global _job
    _job = job
