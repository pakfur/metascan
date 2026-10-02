"""Tests for T2iRunner: prompt writing and batch planning.

Everything here is hand-written: the caption CSV, the wildcard lists, the
VLM replies. Nothing is read from the real caption file or the library.
The fakes stand in for ComfyClient and VlmClient (each has its own suite);
the database is a real temp DatabaseManager because preset rows and job
rows are part of what the runner reads and writes.
"""

from __future__ import annotations

import asyncio
import copy
import csv
import json
import os
import random
import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from unittest import mock

from backend.config import get_t2i_config
from metascan.core.comfy_bindings import BindingError, GenerationParams
from metascan.core.comfy_client import ComfyClient, ComfyError
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.core.t2i_captions import CaptionFilterError, CaptionPicker, CaptionStore
from metascan.core.t2i_characters import resolve_caption
from metascan.core.t2i_form import SEED_MAX, T2iFormError, t2i_dims
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.core.t2i_prompt import CONTENT_MODES, fallback_prompt
from metascan.core.t2i_runner import (
    BatchRequest,
    BatchStarted,
    PromptResult,
    T2iConflictError,
    T2iNotFoundError,
    T2iRequestError,
    T2iRunner,
    T2iUnavailableError,
    default_output_root,
    plan_seeds,
)
from metascan.core.t2i_wildcards import LibraryCache
from metascan.core.prompt_store import get_prompt_store
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError
from tests._fake_comfy_server import FakeComfy as FakeComfyServer

# ---- hand-written data ----------------------------------------------------


def _node(class_type: str, title: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow(negative: bool = False, lora_stack: bool = False) -> Dict[str, Any]:
    """A valid kind-t2i graph: the four required titles, plus the optional
    MS_NEGATIVE and MS_LORA_STACK nodes on request."""
    wf = {
        "1": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "2": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 8}),
        "3": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "4": _node("SaveImage", "MS_SAVE", {"filename_prefix": "ms"}),
    }
    if negative:
        wf["5"] = _node("CLIPTextEncode", "MS_NEGATIVE", {"text": ""})
    if lora_stack:
        wf["6"] = _node("Power Lora Loader (rgthree)", "MS_LORA_STACK", {})
    return wf


CSV_COLUMNS = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
]

# Six hand-written rows; the tests pick them apart with filters, so their
# aspect ratios and nudity values are all known.
CSV_ROWS = [
    [
        "__ALICE__ walks along a beach at dawn.",
        "3:2",
        "none",
        "0.90",
        "0.10",
        "0.00",
        "0",
        "1",
        "['dress']",
    ],
    [
        "__BELLA__ reads a book in a quiet library.",
        "2:3",
        "none",
        "0.80",
        "0.10",
        "0.00",
        "0",
        "1",
        "['scarf']",
    ],
    [
        "__ADAM__ repairs a bicycle in a sunny yard.",
        "1:1",
        "none",
        "0.70",
        "0.10",
        "0.00",
        "1",
        "0",
        "['shirt']",
    ],
    [
        "__CLARA__ paints a mural on a tall wall.",
        "16:9",
        "none",
        "0.60",
        "0.10",
        "0.00",
        "0",
        "1",
        "[]",
    ],
    [
        "__DIANNA__ waters plants on a balcony.",
        "4:5",
        "partial",
        "0.50",
        "0.30",
        "0.10",
        "0",
        "1",
        "['apron']",
    ],
    [
        "__ALICE__ and __ADAM__ share an umbrella in the rain.",
        "3:4",
        "none",
        "0.85",
        "0.20",
        "0.00",
        "1",
        "1",
        "['coat']",
    ],
]

LISTS = {
    "age.txt": "27-year-old\n34-year-old\n41-year-old\n",
    "ethnicity.txt": "East Asian\nLatina\nNordic\n",
    "skin.txt": "olive skin\nfair skin\ndeep brown skin\n",
    "eyes.txt": "green eyes\nbrown eyes\ngrey eyes\n",
    "face.txt": "a soft round face\nhigh cheekbones\na strong jaw\n",
    "hair.txt": (
        "auburn hair\njet-black hair\ncopper red hair\n"
        "platinum blonde hair\nchestnut brown hair\ndark blonde hair\n"
    ),
    "body.female.txt": "an athletic build\na curvy build\na slim build\n",
    "body.male.txt": "a lean build\na broad-shouldered build\n",
}

CAPTION = "__ALICE__ walks along a beach at dawn."
# What a well-behaved model answers: a prompt, then a Negative: block.
REPLY_WITH_NEGATIVE = "A calm beach scene at dawn.\n\nNegative: blurry, watermark"


# ---- fakes ----------------------------------------------------------------


class FakeVlm:
    """Stands in for VlmClient. ``replies`` is consumed one entry per
    generate_text call: a string is returned, an exception is raised; when
    it runs dry ``default_reply`` answers. ``gates`` holds one entry per
    call as well: an Event that call waits on (None lets it straight
    through). ``log`` is shared with the other fakes so a test can assert
    the order of calls across them. Like the real client, ``shutdown``
    leaves ``model_id`` set: it names the model to restart, not whether one
    is running."""

    def __init__(self, log: List[Tuple[Any, ...]]) -> None:
        self.model_id: Optional[str] = "qwen3vl-8b"
        self.log = log
        self.calls: List[Dict[str, Any]] = []
        self.ensure_calls: List[str] = []
        self.replies: Deque[Any] = deque()
        self.start_errors: Deque[BaseException] = deque()
        self.gates: Deque[Optional[asyncio.Event]] = deque()
        self.cancelled_calls = 0
        self.shutdowns = 0
        self.shutdown_error: Optional[BaseException] = None
        self.default_reply = REPLY_WITH_NEGATIVE

    async def ensure_started(self, model_id: str) -> None:
        self.log.append(("vlm", "ensure_started"))
        self.ensure_calls.append(model_id)
        if self.start_errors:
            raise self.start_errors.popleft()

    async def generate_text(self, **kwargs: Any) -> str:
        self.log.append(("vlm", "generate_text"))
        self.calls.append(kwargs)
        gate = self.gates.popleft() if self.gates else None
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                self.cancelled_calls += 1
                raise
        reply = self.replies.popleft() if self.replies else self.default_reply
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)

    async def shutdown(self) -> None:
        self.log.append(("vlm", "shutdown"))
        self.shutdowns += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


class FakeComfy:
    """Stands in for ComfyClient. ``submit`` writes the generation_jobs row
    the real one writes (state queued, carrying t2i_batch_id) and returns
    its id; ``cancel`` records the id, marks the row cancelled and emits the
    job_update the real one would. Tests drive the rest of a job's life with
    ``emit``: the runner listens to this exactly as it listens to
    ComfyClient."""

    def __init__(self, db: DatabaseManager, log: List[Tuple[Any, ...]]) -> None:
        self.db = db
        self.log = log
        # Every submit call, the failing ones too: (preset_id, params, kwargs).
        self.submits: List[Tuple[int, Any, Dict[str, Any]]] = []
        self.job_ids: List[int] = []  # the ids handed back, in order
        self.cancelled: List[int] = []
        self.listeners: List[Callable[[str, Dict[str, Any]], None]] = []
        self.submit_errors: Dict[int, BaseException] = {}  # nth call (0-based) raises
        self.cancel_errors: Dict[int, BaseException] = {}  # job id -> cancel raises
        self.gate: Optional[asyncio.Event] = None  # submit waits here first
        self.hold: Optional[asyncio.Event] = None  # ...and here after the row
        # A job that reaches this state on the very next loop iteration after
        # submit returns -- before the caller has necessarily resumed.
        self.finish_at_once: Optional[str] = None
        self.cancel_gate: Optional[asyncio.Event] = None  # cancel waits here

    def on_job_event(self, cb: Callable[[str, Dict[str, Any]], None]) -> None:
        self.listeners.append(cb)

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        for cb in list(self.listeners):
            cb(event, payload)

    async def submit(self, preset_id: int, params: Any, **kwargs: Any) -> int:
        self.log.append(("comfy", "submit"))
        call = len(self.submits)
        self.submits.append((preset_id, params, dict(kwargs)))
        if self.gate is not None:
            await self.gate.wait()
        error = self.submit_errors.get(call)
        if error is not None:
            raise error
        output_dir = kwargs.get("output_dir")
        job_id = self.db.create_generation_job(
            preset_id,
            params.to_json(),
            None,
            str(output_dir) if output_dir else None,
            None,
            None,
            None,
            kwargs.get("output_name"),
            kwargs.get("t2i_batch_id"),
        )
        if self.hold is not None:
            await self.hold.wait()
        self.job_ids.append(job_id)
        self.emit("job_update", {"job_id": job_id, "state": "queued", "error": None})
        if self.finish_at_once is not None:
            asyncio.get_running_loop().call_soon(
                self.emit,
                "job_update",
                {"job_id": job_id, "state": self.finish_at_once, "error": None},
            )
        return job_id

    async def cancel(self, job_id: int) -> None:
        self.log.append(("comfy", "cancel", job_id))
        self.cancelled.append(job_id)
        if self.cancel_gate is not None:
            await self.cancel_gate.wait()
        error = self.cancel_errors.get(job_id)
        if error is not None:
            raise error
        self.db.update_generation_job(
            job_id,
            state="cancelled",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        self.emit("job_update", {"job_id": job_id, "state": "cancelled", "error": None})


# ---- fixtures -------------------------------------------------------------


class RunnerCase(unittest.IsolatedAsyncioTestCase):
    """A runner over a temp DB, a temp caption CSV and temp wildcard lists."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = DatabaseManager(self.root / "db")
        self.addCleanup(self.db.close)

        self.plain = self._preset("plain", t2i_workflow())
        self.with_negative = self._preset("neg", t2i_workflow(negative=True))
        self.with_stack = self._preset("stack", t2i_workflow(lora_stack=True))

        self.csv_path = self.root / "captions.csv"
        self._write_csv(CSV_ROWS)
        lists_dir = self.root / "lists"
        lists_dir.mkdir()
        for name, text in LISTS.items():
            (lists_dir / name).write_text(text, encoding="utf-8")
        self.library = LibraryCache(lists_dir)
        self.captions = CaptionStore(self.csv_path)

        # One timeline for everything that happens: the fakes append their
        # calls, the event recorder appends ("event", name). A test can then
        # assert the order across the VLM, ComfyUI and the frames.
        self.log: List[Tuple[Any, ...]] = []
        self.vlm: Optional[FakeVlm] = FakeVlm(self.log)
        self.comfy = FakeComfy(self.db, self.log)
        self.out_root = self.root / "out"
        self.cfg = get_t2i_config({"t2i": {"window": 4}})
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.runner = T2iRunner(
            db=self.db,
            comfy=self.comfy,
            get_vlm=lambda: self.vlm,
            captions=self.captions,
            library=self.library,
            output_root=self.out_root,
            get_config=lambda: self.cfg,
        )
        self.runner.on_event(self._record)
        self.comfy.on_job_event(self.runner.handle_job_event)
        # Which model to start is not what these tests are about; the real
        # picker would probe this machine's GPU once the fake has no id.
        patcher = mock.patch(
            "metascan.core.t2i_runner.pick_vlm_model",
            side_effect=lambda vlm: vlm.model_id or "qwen3vl-8b",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.runner.aclose()

    def _record(self, channel: str, event: str, data: Dict[str, Any]) -> None:
        self.assertEqual(channel, "t2i")
        self.events.append((event, copy.deepcopy(data)))
        self.log.append(("event", event))

    def calls(self) -> List[Tuple[Any, ...]]:
        """The timeline without the frames: what the fakes were asked."""
        return [entry for entry in self.log if entry[0] != "event"]

    def _preset(self, name: str, workflow: Dict[str, Any], kind: str = "t2i") -> int:
        return self.db.create_workflow_preset(name, kind, json.dumps(workflow), "{}")

    def _write_csv(self, rows: List[List[str]]) -> None:
        with open(self.csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(rows)

    def manual(self, **over: Any) -> BatchRequest:
        fields: Dict[str, Any] = dict(
            mode="manual",
            model="krea2",
            preset_id=self.plain,
            megapixels=1.0,
            seed=100,
            seed_policy="increment",
            batch_size=1,
            count_per_batch=3,
            caption="a red kite",
            prompt="A red kite over a gray sea.",
            aspect_ratio="3:2",
        )
        fields.update(over)
        return BatchRequest(**fields)

    def random_mode(self, **over: Any) -> BatchRequest:
        fields: Dict[str, Any] = dict(
            mode="random",
            model="krea2",
            preset_id=self.plain,
            megapixels=1.0,
            seed=10,
            seed_policy="increment",
            batch_size=3,
            count_per_batch=2,
            filter=None,
        )
        fields.update(over)
        return BatchRequest(**fields)

    async def until(
        self, predicate: Callable[[], bool], what: str, timeout: float = 5.0
    ) -> None:
        """Wait for ``predicate``; fail the test if it never holds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                self.fail(f"timed out waiting for {what}")
            await asyncio.sleep(0.005)

    async def quiet(self, seconds: float = 0.05) -> None:
        """Give the runner time to do anything it is going to do."""
        await asyncio.sleep(seconds)

    def job_update(self, job_id: int, state: str, error: Optional[str] = None) -> None:
        """A job_update from ComfyUI, as the runner hears it."""
        self.comfy.emit(
            "job_update", {"job_id": job_id, "state": state, "error": error}
        )

    def roomy(self) -> None:
        """A window big enough that no test batch ever waits for it."""
        self.cfg = get_t2i_config({"t2i": {"window": 50}})

    def names(self) -> List[str]:
        return [name for name, _ in self.events]

    def frames(self, name: str) -> List[Dict[str, Any]]:
        return [data for event, data in self.events if event == name]

    def expected_text(self, caption: str, seed: int, style: str = "ref") -> str:
        library, _ = self.library.get()
        return resolve_caption(caption, seed, style, library).text


# ---- pure helpers ---------------------------------------------------------


class TestErrorClasses(unittest.TestCase):
    def test_four_distinct_runtime_errors(self) -> None:
        # The routes answer 400 / 404 / 409 / 503 for these and 502 for any
        # other RuntimeError, so none may be a subclass of another.
        classes = (
            T2iRequestError,
            T2iNotFoundError,
            T2iConflictError,
            T2iUnavailableError,
        )
        for cls in classes:
            self.assertTrue(issubclass(cls, RuntimeError))
            self.assertEqual(str(cls("why")), "why")
        for one in classes:
            for other in classes:
                if one is not other:
                    self.assertFalse(issubclass(one, other))


class TestDefaultOutputRoot(unittest.TestCase):
    def test_is_the_t2i_folder_under_the_comfy_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(default_output_root(Path(tmp)), Path(tmp) / "t2i")

    def test_a_relative_root_becomes_absolute(self) -> None:
        # resolve_output_target refuses a relative root, and comfy.output_root
        # defaults to the relative "data/storyboards".
        root = default_output_root("data/storyboards")
        self.assertTrue(root.is_absolute())
        self.assertEqual(root.parts[-3:], ("data", "storyboards", "t2i"))


class TestPlanSeeds(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = random.Random(7)

    def test_increment_and_decrement_step_by_one(self) -> None:
        self.assertEqual(
            plan_seeds("increment", 100, 4, self.rng), [100, 101, 102, 103]
        )
        self.assertEqual(plan_seeds("decrement", 100, 3, self.rng), [100, 99, 98])

    def test_fixed_repeats_the_seed(self) -> None:
        self.assertEqual(plan_seeds("fixed", 55, 4, self.rng), [55, 55, 55, 55])

    def test_random_starts_at_the_given_seed_then_draws_in_range(self) -> None:
        seeds = plan_seeds("random", 5, 200, self.rng)
        self.assertEqual(len(seeds), 200)
        self.assertEqual(seeds[0], 5)
        self.assertTrue(all(0 <= s <= SEED_MAX for s in seeds))
        self.assertGreater(len(set(seeds)), 150)

    def test_random_is_driven_by_the_rng_it_is_given(self) -> None:
        one = plan_seeds("random", 5, 20, random.Random(3))
        two = plan_seeds("random", 5, 20, random.Random(3))
        self.assertEqual(one, two)

    def test_increment_stops_at_the_top_of_the_range(self) -> None:
        self.assertEqual(
            plan_seeds("increment", SEED_MAX - 1, 5, self.rng),
            [SEED_MAX - 1, SEED_MAX],
        )

    def test_decrement_stops_at_zero(self) -> None:
        self.assertEqual(plan_seeds("decrement", 2, 5, self.rng), [2, 1, 0])

    def test_a_single_seed_never_steps(self) -> None:
        self.assertEqual(plan_seeds("increment", SEED_MAX, 1, self.rng), [SEED_MAX])
        self.assertEqual(plan_seeds("decrement", 0, 1, self.rng), [0])


# ---- generate_prompt ------------------------------------------------------


class TestGeneratePrompt(RunnerCase):
    async def test_the_result_carries_prompt_negative_caption_and_warnings(
        self,
    ) -> None:
        assert self.vlm is not None
        self.vlm.replies.append("A calm beach at dawn, soft light.")
        result = await self.runner.generate_prompt(
            caption=CAPTION, seed=101, model="krea2"
        )
        self.assertIsInstance(result, PromptResult)
        self.assertEqual(result.prompt, "A calm beach at dawn, soft light.")
        self.assertIsNone(result.negative)
        self.assertEqual(result.resolved_caption, self.expected_text(CAPTION, 101))
        self.assertNotIn("__ALICE__", result.resolved_caption)
        self.assertEqual(result.warnings, [])

    async def test_the_vlm_gets_the_composed_prompts_for_each_model(self) -> None:
        assert self.vlm is not None
        store = get_prompt_store()
        preamble = store.get("T2I_CAPTION_PREAMBLE").rstrip()
        for model_id, profile in MODEL_PROFILES.items():
            with self.subTest(model=model_id):
                self.vlm.calls.clear()
                await self.runner.generate_prompt(
                    caption=CAPTION, seed=101, model=model_id
                )
                self.assertEqual(len(self.vlm.calls), 1)
                call = self.vlm.calls[0]
                system = call["system_prompt"]
                self.assertTrue(system.startswith(preamble))
                guideline = store.get(profile.meta_key)
                self.assertIn(guideline, system)
                self.assertLess(system.index(preamble), system.index(guideline))
                resolved = self.expected_text(CAPTION, 101, profile.identity)
                self.assertEqual(
                    call["user_prompt"],
                    f"DESCRIPTION:\n{resolved}\n\nWrite the prompt now.",
                )
                self.assertEqual(call["temperature"], 0.6)
                self.assertEqual(call["max_tokens"], profile.max_tokens)
                self.assertEqual(call["timeout"], 240.0)
                # Text only: there is no image and no grammar.
                self.assertNotIn("image_path", call)
                self.assertNotIn("grammar", call)

    async def test_the_vlm_is_started_before_it_is_asked(self) -> None:
        assert self.vlm is not None
        await self.runner.generate_prompt(caption=CAPTION, seed=1, model="krea2")
        self.assertEqual(self.vlm.ensure_calls, ["qwen3vl-8b"])
        self.assertEqual(
            self.calls(), [("vlm", "ensure_started"), ("vlm", "generate_text")]
        )

    async def test_the_negative_block_is_split_off_for_qwen_and_sdxl_only(
        self,
    ) -> None:
        for model_id, profile in MODEL_PROFILES.items():
            with self.subTest(model=model_id):
                result = await self.runner.generate_prompt(
                    caption=CAPTION, seed=101, model=model_id
                )
                self.assertEqual(result.prompt, "A calm beach scene at dawn.")
                if profile.has_negative:
                    self.assertEqual(result.negative, "blurry, watermark")
                else:
                    self.assertIsNone(result.negative)
                self.assertNotIn("Negative", result.prompt)

    async def test_parentheses_never_reach_the_prompt_or_negative(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.append("A (very) calm beach.\n\nNegative: (blurry), text")
        result = await self.runner.generate_prompt(
            caption=CAPTION, seed=101, model="qwen"
        )
        self.assertEqual(result.prompt, "A very calm beach.")
        self.assertEqual(result.negative, "blurry, text")

    async def test_the_content_mode_picks_the_directive(self) -> None:
        assert self.vlm is not None
        store = get_prompt_store()
        uncensored = store.get("UNCENSORED_DIRECTIVE")
        safe = store.get("SAFETY_DIRECTIVE")
        expected = {
            "uncensored": (uncensored, safe),
            "sfw": (safe, uncensored),
            "default": (None, None),
        }
        self.assertEqual(set(expected), set(CONTENT_MODES))
        for mode, (present, absent) in expected.items():
            with self.subTest(mode=mode):
                self.cfg = get_t2i_config({"t2i": {"content_mode": mode}})
                self.vlm.calls.clear()
                await self.runner.generate_prompt(
                    caption=CAPTION, seed=1, model="krea2"
                )
                system = self.vlm.calls[0]["system_prompt"]
                for directive in (uncensored, safe):
                    if directive == present:
                        self.assertIn(directive, system)
                    else:
                        self.assertNotIn(directive, system)
                if present is not None:
                    self.assertTrue(system.endswith(present))

    async def test_the_identity_style_comes_from_the_config_each_call(self) -> None:
        # krea2 defaults to "ref" (a later mention is a descriptive handle);
        # the config can switch it to the plain noun without a restart.
        caption = "__ALICE__ smiles. __ALICE__ waves."
        ref = await self.runner.generate_prompt(caption=caption, seed=4, model="krea2")
        self.cfg = get_t2i_config({"t2i": {"identity": {"krea2": "noun"}}})
        noun = await self.runner.generate_prompt(caption=caption, seed=4, model="krea2")
        self.assertEqual(ref.resolved_caption, self.expected_text(caption, 4, "ref"))
        self.assertEqual(noun.resolved_caption, self.expected_text(caption, 4, "noun"))
        self.assertNotEqual(ref.resolved_caption, noun.resolved_caption)

    async def test_a_caption_without_tokens_is_passed_through(self) -> None:
        assert self.vlm is not None
        text = "A quiet harbour at night."
        result = await self.runner.generate_prompt(caption=text, seed=1, model="krea2")
        self.assertEqual(result.resolved_caption, text)
        self.assertIn(f"DESCRIPTION:\n{text}\n", self.vlm.calls[0]["user_prompt"])

    async def test_engine_warnings_are_reported(self) -> None:
        # No hair.txt-style list for BREASTS: the engine falls back to the
        # bare word and says so; the runner passes that on.
        result = await self.runner.generate_prompt(
            caption="__ALICE__ adjusts her __BREASTS__ top.", seed=1, model="krea2"
        )
        self.assertTrue(result.warnings)
        self.assertTrue(any("breasts" in w.lower() for w in result.warnings))

    async def test_no_vlm_gives_the_fallback_prompt_and_a_warning(self) -> None:
        self.vlm = None
        for model_id, profile in MODEL_PROFILES.items():
            with self.subTest(model=model_id):
                result = await self.runner.generate_prompt(
                    caption=CAPTION, seed=101, model=model_id
                )
                resolved = self.expected_text(CAPTION, 101, profile.identity)
                prompt, negative = fallback_prompt(profile, resolved)
                self.assertEqual(result.prompt, prompt)
                self.assertEqual(result.negative, negative)
                self.assertEqual(result.resolved_caption, resolved)
                self.assertEqual(
                    result.warnings, ["VLM unavailable - used the resolved caption"]
                )

    async def test_the_sdxl_fallback_has_its_stock_prefix_and_negative(self) -> None:
        self.vlm = None
        result = await self.runner.generate_prompt(
            caption="A quiet harbour at night.", seed=1, model="sd"
        )
        store = get_prompt_store()
        self.assertTrue(
            result.prompt.startswith(store.get("T2I_FALLBACK_PREFIX_SD").strip())
        )
        self.assertEqual(result.negative, store.get("T2I_FALLBACK_NEGATIVE_SD").strip())

    async def test_no_loadable_model_falls_back_with_the_same_warning(self) -> None:
        assert self.vlm is not None
        with mock.patch(
            "metascan.core.t2i_runner.pick_vlm_model",
            side_effect=VlmSelectError("no VLM model available on this hardware"),
        ):
            result = await self.runner.generate_prompt(
                caption=CAPTION, seed=101, model="krea2"
            )
        self.assertEqual(
            result.warnings, ["VLM unavailable - used the resolved caption"]
        )
        self.assertEqual(self.vlm.calls, [])
        self.assertEqual(self.vlm.ensure_calls, [])

    async def test_a_model_that_is_not_installed_falls_back_like_no_vlm(self) -> None:
        # A fresh install on a recommended GPU tier has no weights and no
        # llama-server yet: that is "no VLM", not a failure to retry.
        assert self.vlm is not None
        asked: List[str] = []

        def installed(model_id: str) -> bool:
            asked.append(model_id)
            return False

        self.runner.vlm_installed = installed
        result = await self.runner.generate_prompt(
            caption=CAPTION, seed=101, model="krea2"
        )
        self.assertEqual(
            result.warnings, ["VLM unavailable - used the resolved caption"]
        )
        self.assertEqual(asked, ["qwen3vl-8b"])
        self.assertEqual(self.vlm.ensure_calls, [])  # never started
        self.assertEqual(self.vlm.calls, [])

    async def test_an_installed_model_is_used(self) -> None:
        assert self.vlm is not None
        self.runner.vlm_installed = lambda model_id: True
        result = await self.runner.generate_prompt(
            caption=CAPTION, seed=101, model="krea2"
        )
        self.assertEqual(len(self.vlm.calls), 1)
        self.assertNotIn("VLM unavailable - used the resolved caption", result.warnings)

    async def test_vlm_failures_propagate_so_the_route_can_answer_502(self) -> None:
        assert self.vlm is not None
        for error in (VlmError("bad body"), TimeoutError("slow"), RuntimeError("boom")):
            with self.subTest(error=type(error).__name__):
                self.vlm.replies.append(error)
                with self.assertRaises(type(error)):
                    await self.runner.generate_prompt(
                        caption=CAPTION, seed=1, model="krea2"
                    )

    async def test_a_model_that_will_not_start_propagates(self) -> None:
        assert self.vlm is not None
        self.vlm.start_errors.append(VlmError("llama-server exited"))
        with self.assertRaises(VlmError):
            await self.runner.generate_prompt(caption=CAPTION, seed=1, model="krea2")
        self.assertEqual(self.vlm.calls, [])

    async def test_a_model_that_cannot_be_spawned_is_a_vlm_error(self) -> None:
        # VlmClient.start spawns llama-server with subprocess.Popen, which
        # raises OSError -- not VlmError -- when the binary is missing or not
        # executable. Left raw the route answered 500; as a VlmError it is
        # the usual 502 that names the reason.
        assert self.vlm is not None
        for error in (
            FileNotFoundError(2, "No such file or directory", "llama-server"),
            PermissionError(13, "Permission denied", "llama-server"),
        ):
            with self.subTest(error=type(error).__name__):
                self.vlm.start_errors.append(error)
                with self.assertRaises(VlmError) as raised:
                    await self.runner.generate_prompt(
                        caption=CAPTION, seed=1, model="krea2"
                    )
                self.assertIn("llama-server", str(raised.exception))
                self.assertIs(raised.exception.__cause__, error)

    async def test_an_oserror_while_asking_is_a_vlm_error_too(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.append(ConnectionResetError("reset by peer"))
        with self.assertRaises(VlmError):
            await self.runner.generate_prompt(caption=CAPTION, seed=1, model="krea2")

    async def test_an_empty_reply_is_a_failure_not_an_empty_prompt(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.append("   \n")
        with self.assertRaises(VlmError):
            await self.runner.generate_prompt(caption=CAPTION, seed=1, model="krea2")

    async def test_cancellation_is_never_swallowed(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.append(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.runner.generate_prompt(caption=CAPTION, seed=1, model="krea2")

    async def test_an_unknown_model_is_a_request_error(self) -> None:
        with self.assertRaisesRegex(T2iRequestError, "Unknown model 'flux9'"):
            await self.runner.generate_prompt(caption=CAPTION, seed=1, model="flux9")

    async def test_an_empty_caption_is_a_request_error(self) -> None:
        for caption in ("", "   \n"):
            with self.subTest(caption=caption):
                with self.assertRaisesRegex(T2iRequestError, "Caption is empty"):
                    await self.runner.generate_prompt(
                        caption=caption, seed=1, model="krea2"
                    )

    async def test_a_seed_outside_the_range_is_a_request_error(self) -> None:
        for seed in (-1, SEED_MAX + 1, True, 1.5):
            with self.subTest(seed=seed):
                with self.assertRaisesRegex(T2iRequestError, "Seed"):
                    await self.runner.generate_prompt(
                        caption=CAPTION, seed=seed, model="krea2"  # type: ignore[arg-type]
                    )

    async def test_the_boundary_seeds_are_accepted(self) -> None:
        for seed in (0, SEED_MAX):
            with self.subTest(seed=seed):
                result = await self.runner.generate_prompt(
                    caption=CAPTION, seed=seed, model="krea2"
                )
                self.assertEqual(
                    result.resolved_caption, self.expected_text(CAPTION, seed)
                )

    async def test_generate_prompt_writes_nothing(self) -> None:
        await self.runner.generate_prompt(caption=CAPTION, seed=1, model="qwen")
        self.assertEqual(self.events, [])
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.db.list_generation_jobs(limit=100), [])


class TestResolve(RunnerCase):
    async def test_resolve_uses_the_models_identity_style(self) -> None:
        caption = "__ALICE__ smiles. __ALICE__ waves."
        for model_id, profile in MODEL_PROFILES.items():
            with self.subTest(model=model_id):
                result = await self.runner.resolve(
                    caption=caption, seed=9, model=model_id
                )
                self.assertEqual(
                    result.text, self.expected_text(caption, 9, profile.identity)
                )
                self.assertIn("ALICE", result.characters)

    async def test_resolve_needs_no_vlm_and_allows_an_empty_caption(self) -> None:
        self.vlm = None
        result = await self.runner.resolve(caption="", seed=1, model="krea2")
        self.assertEqual(result.text, "")
        self.assertEqual(result.characters, {})

    async def test_resolve_rejects_an_unknown_model_and_a_bad_seed(self) -> None:
        with self.assertRaises(T2iRequestError):
            await self.runner.resolve(caption="x", seed=1, model="nope")
        with self.assertRaises(T2iRequestError):
            await self.runner.resolve(caption="x", seed=-5, model="krea2")

    async def test_resolve_follows_a_list_edit_without_a_restart(self) -> None:
        before = await self.runner.resolve(caption=CAPTION, seed=3, model="krea2")
        path = self.root / "lists" / "hair.txt"
        path.write_text("silver hair\n", encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        after = await self.runner.resolve(caption=CAPTION, seed=3, model="krea2")
        self.assertNotIn("silver hair", before.text)
        self.assertEqual(after.characters["ALICE"]["hair"], "silver hair")


# ---- start_batch: validation ---------------------------------------------


async def _parked(self: T2iRunner, batch: Any) -> None:
    """A run that never gets going, so a registered batch stays exactly as
    start_batch left it."""
    await asyncio.Event().wait()


class ParkedRunCase(RunnerCase):
    """For the tests about validation and planning: batches are registered
    and announced but nothing runs."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        patcher = mock.patch.object(T2iRunner, "_run", _parked)
        patcher.start()
        self.addCleanup(patcher.stop)


class ValidationCase(ParkedRunCase):
    async def rejects(self, exc_type: type, needle: str, req: BatchRequest) -> None:
        """start_batch raises ``exc_type`` naming ``needle`` and leaves no
        state behind: no batch, no event."""
        with self.assertRaises(exc_type) as ctx:
            await self.runner.start_batch(req)
        self.assertIn(needle, str(ctx.exception))
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.events, [])


class TestValidation(ValidationCase):
    async def test_an_unknown_mode(self) -> None:
        await self.rejects(T2iRequestError, "Unknown mode", self.manual(mode="auto"))

    async def test_an_unknown_model(self) -> None:
        await self.rejects(
            T2iRequestError, "Unknown model 'flux9'", self.manual(model="flux9")
        )

    async def test_an_unknown_seed_policy(self) -> None:
        await self.rejects(
            T2iRequestError, "seed policy", self.manual(seed_policy="shuffle")
        )

    async def test_a_seed_outside_the_range(self) -> None:
        for seed in (-1, SEED_MAX + 1, True, 2.5):
            with self.subTest(seed=seed):
                await self.rejects(T2iRequestError, "Seed", self.manual(seed=seed))

    async def test_the_boundary_seeds_are_accepted(self) -> None:
        for seed in (0, SEED_MAX):
            with self.subTest(seed=seed):
                started = await self.runner.start_batch(
                    self.manual(seed=seed, count_per_batch=1)
                )
                self.assertEqual(started.total_images, 1)

    async def test_megapixels_must_be_a_positive_number(self) -> None:
        for megapixels in (0, -1.0, float("nan"), 5000.0, "1"):
            with self.subTest(megapixels=megapixels):
                await self.rejects(
                    T2iRequestError, "megapixels", self.manual(megapixels=megapixels)
                )

    async def test_the_counts_must_be_whole_numbers_within_the_limits(self) -> None:
        self.cfg = get_t2i_config(
            {"t2i": {"max_batch_size": 5, "max_count_per_batch": 4}}
        )
        await self.rejects(
            T2iRequestError, "Batch size", self.random_mode(batch_size=6)
        )
        await self.rejects(
            T2iRequestError, "Batch size", self.random_mode(batch_size=0)
        )
        await self.rejects(
            T2iRequestError, "Count per batch", self.manual(count_per_batch=5)
        )
        await self.rejects(
            T2iRequestError, "Count per batch", self.manual(count_per_batch=0)
        )
        await self.rejects(
            T2iRequestError, "Count per batch", self.manual(count_per_batch=2.0)
        )
        started = await self.runner.start_batch(
            self.random_mode(batch_size=5, count_per_batch=4)
        )
        self.assertEqual(started.total_images, 20)

    async def test_a_manual_batch_has_a_batch_size_of_one(self) -> None:
        await self.rejects(T2iRequestError, "Manual", self.manual(batch_size=2))

    async def test_a_fixed_seed_cannot_render_several_images_per_step(self) -> None:
        # Review focus 5: the same seed renders the same image N times.
        await self.rejects(
            T2iRequestError,
            "Fixed",
            self.manual(seed_policy="fixed", count_per_batch=2),
        )
        await self.rejects(
            T2iRequestError,
            "Fixed",
            self.random_mode(seed_policy="fixed", batch_size=2, count_per_batch=2),
        )

    async def test_a_fixed_seed_with_one_image_per_step_is_fine(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(seed_policy="fixed", batch_size=4, count_per_batch=1)
        )
        self.assertEqual(started.total_images, 4)

    async def test_a_missing_preset(self) -> None:
        await self.rejects(
            T2iRequestError, "does not exist", self.manual(preset_id=9999)
        )

    async def test_a_preset_of_another_kind(self) -> None:
        video = self._preset("video", t2i_workflow(), kind="ref2v")
        await self.rejects(T2iRequestError, "t2i", self.manual(preset_id=video))

    async def test_bindings_that_do_not_resolve_propagate(self) -> None:
        broken = t2i_workflow()
        del broken["3"]  # MS_LATENT is required for kind t2i
        preset = self._preset("broken", broken)
        with self.assertRaisesRegex(BindingError, "MS_LATENT"):
            await self.runner.start_batch(self.manual(preset_id=preset))
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_lora_without_a_name_propagates_as_a_form_error(self) -> None:
        with self.assertRaises(T2iFormError):
            await self.runner.start_batch(
                self.manual(preset_id=self.with_stack, loras=[{"strength": 0.5}])
            )
        self.assertEqual(self.runner.active_batches(), [])

    async def test_loras_need_a_workflow_with_a_lora_stack(self) -> None:
        await self.rejects(
            T2iRequestError,
            "MS_LORA_STACK",
            self.manual(loras=[{"name": "kite.safetensors", "strength": 0.8}]),
        )

    async def test_loras_are_accepted_with_a_lora_stack(self) -> None:
        started = await self.runner.start_batch(
            self.manual(
                preset_id=self.with_stack,
                loras=[{"name": "kite.safetensors", "strength": 0.8}],
            )
        )
        self.assertEqual(started.warnings, [])

    async def test_a_manual_prompt_may_not_be_empty(self) -> None:
        for prompt in (None, "", "   \n"):
            with self.subTest(prompt=prompt):
                await self.rejects(
                    T2iRequestError, "Prompt is empty", self.manual(prompt=prompt)
                )

    async def test_a_manual_aspect_ratio_must_be_one_of_the_eleven(self) -> None:
        for aspect in (None, "5:7", "banana"):
            with self.subTest(aspect=aspect):
                await self.rejects(
                    T2iRequestError,
                    "Aspect ratio",
                    self.manual(aspect_ratio=aspect),
                )

    async def test_a_manual_caption_is_optional_provenance(self) -> None:
        started = await self.runner.start_batch(self.manual(caption=None))
        self.assertEqual(started.total_images, 3)

    async def test_a_random_first_step_needs_one_of_the_eleven_aspect_ratios(
        self,
    ) -> None:
        # A Random request that carries a prompt renders it as step 1 at the
        # ratio it names: there is no CSV row to take one from.
        for aspect in (None, "5:7", "banana"):
            with self.subTest(aspect=aspect):
                await self.rejects(
                    T2iRequestError,
                    "Aspect ratio",
                    self.random_mode(prompt="A red kite.", aspect_ratio=aspect),
                )

    async def test_a_random_request_with_neither_prompt_nor_caption_is_plain(
        self,
    ) -> None:
        # Guard: only a prompt supplies the first step and only a caption names
        # it. With both blank (or absent) the request is a plain Random one and
        # needs no ratio. (A caption alone names step 1: TestRandomFirstCaption.)
        for prompt, caption in ((None, None), ("", ""), ("  \n", "  "), (None, "\n")):
            with self.subTest(prompt=prompt, caption=caption):
                started = await self.runner.start_batch(
                    self.random_mode(prompt=prompt, caption=caption, aspect_ratio=None)
                )
                self.assertEqual(started.total_images, 6)
                await self.runner.cancel_batch(started.batch_id)  # frees the slot

    async def test_random_mode_needs_the_caption_csv(self) -> None:
        self.csv_path.unlink()
        with self.assertRaises(T2iRequestError) as ctx:
            await self.runner.start_batch(self.random_mode())
        self.assertTrue(str(ctx.exception).startswith("caption CSV unavailable: "))
        self.assertIn("not found", str(ctx.exception))
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.events, [])

    async def test_manual_mode_does_not_need_the_caption_csv(self) -> None:
        self.csv_path.unlink()
        started = await self.runner.start_batch(self.manual())
        self.assertEqual(started.total_images, 3)

    async def test_a_filter_with_an_unknown_key_propagates(self) -> None:
        with self.assertRaisesRegex(CaptionFilterError, "unknown filter key"):
            await self.runner.start_batch(self.random_mode(filter={"colour": ["red"]}))
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_filter_that_matches_nothing_propagates(self) -> None:
        with self.assertRaisesRegex(CaptionFilterError, "no captions match"):
            await self.runner.start_batch(self.random_mode(filter={"nudity": ["full"]}))
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.events, [])

    async def test_a_configured_output_root_must_be_an_existing_directory(
        self,
    ) -> None:
        missing = self.root / "nowhere"
        self.cfg = get_t2i_config({"t2i": {"output_root": str(missing)}})
        await self.rejects(T2iRequestError, "does not exist", self.manual())
        self.assertFalse(missing.exists())  # never auto-created

    async def test_an_output_prefix_that_climbs_out_is_refused(self) -> None:
        existing = self.root / "library"
        existing.mkdir()
        self.cfg = get_t2i_config(
            {"t2i": {"output_root": str(existing), "output_prefix": "/../evil_"}}
        )
        await self.rejects(T2iRequestError, "..", self.manual())

    async def test_the_default_output_root_is_created_on_demand(self) -> None:
        target = self.out_root / "t2i"
        self.assertFalse(target.exists())
        await self.runner.start_batch(self.manual())
        self.assertTrue(target.is_dir())

    async def test_a_rejected_request_does_not_create_the_default_root(self) -> None:
        await self.rejects(T2iRequestError, "Prompt is empty", self.manual(prompt=""))
        self.assertFalse((self.out_root / "t2i").exists())

    async def test_a_configured_root_is_used_as_is(self) -> None:
        library = self.root / "library"
        library.mkdir()
        self.cfg = get_t2i_config({"t2i": {"output_root": str(library)}})
        await self.runner.start_batch(self.manual())
        self.assertFalse((self.out_root / "t2i").exists())

    async def test_a_second_random_batch_is_a_conflict(self) -> None:
        first = await self.runner.start_batch(self.random_mode())
        self.events.clear()
        with self.assertRaises(T2iConflictError) as ctx:
            await self.runner.start_batch(self.random_mode())
        self.assertTrue(str(ctx.exception).startswith("random_batch_active"))
        self.assertEqual(
            [b["batch_id"] for b in self.runner.active_batches()], [first.batch_id]
        )
        self.assertEqual(self.events, [])

    async def test_manual_batches_are_never_limited(self) -> None:
        await self.runner.start_batch(self.random_mode())
        for _ in range(3):
            await self.runner.start_batch(self.manual())
        modes = [b["mode"] for b in self.runner.active_batches()]
        self.assertEqual(modes, ["random", "manual", "manual", "manual"])

    async def test_a_manual_batch_does_not_block_a_random_one(self) -> None:
        await self.runner.start_batch(self.manual())
        await self.runner.start_batch(self.random_mode())
        self.assertEqual(len(self.runner.active_batches()), 2)

    async def test_a_negative_the_workflow_cannot_take_is_dropped_with_a_warning(
        self,
    ) -> None:
        started = await self.runner.start_batch(self.manual(negative="blurry"))
        self.assertEqual(
            started.warnings,
            ["negative prompt ignored: the workflow has no MS_NEGATIVE node"],
        )

    async def test_no_negative_warning_when_the_workflow_binds_one(self) -> None:
        started = await self.runner.start_batch(
            self.manual(preset_id=self.with_negative, negative="blurry")
        )
        self.assertEqual(started.warnings, [])

    async def test_no_negative_warning_for_a_blank_negative(self) -> None:
        for negative in (None, "", "   "):
            with self.subTest(negative=negative):
                started = await self.runner.start_batch(self.manual(negative=negative))
                self.assertEqual(started.warnings, [])

    async def test_random_sdxl_warns_when_the_workflow_has_no_negative_node(
        self,
    ) -> None:
        # The model writes a negative; the workflow cannot take it.
        started = await self.runner.start_batch(self.random_mode(model="sd"))
        self.assertEqual(
            started.warnings,
            ["negative prompt ignored: the workflow has no MS_NEGATIVE node"],
        )

    async def test_random_krea2_has_no_negative_to_warn_about(self) -> None:
        started = await self.runner.start_batch(self.random_mode(model="krea2"))
        self.assertEqual(started.warnings, [])

    async def test_a_negative_supplied_with_a_random_first_step_is_warned_about(
        self,
    ) -> None:
        # Like a Manual one: the user typed it. krea2 writes none itself, so
        # the supplied one is the only thing the workflow could drop.
        started = await self.runner.start_batch(
            self.random_mode(
                model="krea2",
                batch_size=1,
                prompt="A red kite.",
                aspect_ratio="3:2",
                negative="blurry",
            )
        )
        self.assertEqual(
            started.warnings,
            ["negative prompt ignored: the workflow has no MS_NEGATIVE node"],
        )

    async def test_a_run_of_one_supplied_step_writes_no_negative_to_warn_about(
        self,
    ) -> None:
        # SDXL would write a negative, but this run draws no step to write one.
        started = await self.runner.start_batch(
            self.random_mode(
                model="sd", batch_size=1, prompt="A red kite.", aspect_ratio="3:2"
            )
        )
        self.assertEqual(started.warnings, [])

    async def test_a_supplied_step_does_not_hide_the_warning_for_the_drawn_ones(
        self,
    ) -> None:
        started = await self.runner.start_batch(
            self.random_mode(
                model="sd", batch_size=2, prompt="A red kite.", aspect_ratio="3:2"
            )
        )
        self.assertEqual(
            started.warnings,
            ["negative prompt ignored: the workflow has no MS_NEGATIVE node"],
        )

    async def test_validation_reports_the_first_problem_in_the_documented_order(
        self,
    ) -> None:
        # Each request has several problems; the earlier rule must win.
        ordered = [
            (self.manual(mode="x", model="nope"), "Unknown mode"),
            (self.manual(model="nope", seed_policy="x"), "Unknown model"),
            (self.manual(seed_policy="x", seed=-1), "seed policy"),
            (self.manual(seed=-1, megapixels=0), "Seed"),
            (self.manual(megapixels=0, batch_size=9), "megapixels"),
            (self.manual(batch_size=2, preset_id=9999), "Manual"),
            (self.manual(preset_id=9999, prompt=""), "does not exist"),
            (self.manual(prompt="", aspect_ratio="x"), "Prompt is empty"),
        ]
        for req, needle in ordered:
            with self.subTest(needle=needle):
                await self.rejects(T2iRequestError, needle, req)

    async def test_the_config_is_read_fresh_for_every_batch(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"max_count_per_batch": 2}})
        await self.rejects(
            T2iRequestError, "Count per batch", self.manual(count_per_batch=3)
        )
        self.cfg = get_t2i_config({"t2i": {"max_count_per_batch": 5}})
        started = await self.runner.start_batch(self.manual(count_per_batch=3))
        self.assertEqual(started.total_images, 3)


# ---- start_batch: planning ------------------------------------------------


class TestPlanning(ParkedRunCase):
    def planned_seeds(self, batch_id: str) -> List[int]:
        return list(self.runner._batches[batch_id].seeds)

    async def test_the_start_response(self) -> None:
        started = await self.runner.start_batch(self.manual())
        self.assertIsInstance(started, BatchStarted)
        self.assertEqual(len(started.batch_id), 32)
        self.assertEqual(started.total_images, 3)
        self.assertEqual(started.warnings, [])

    async def test_batch_ids_are_unique(self) -> None:
        ids = {
            (await self.runner.start_batch(self.manual())).batch_id for _ in range(5)
        }
        self.assertEqual(len(ids), 5)

    async def test_manual_totals_are_count_per_batch(self) -> None:
        started = await self.runner.start_batch(self.manual(count_per_batch=7))
        (row,) = self.runner.active_batches()
        self.assertEqual(started.total_images, 7)
        self.assertEqual(row["total_steps"], 1)
        self.assertEqual(row["images_total"], 7)

    async def test_random_totals_are_batch_size_times_count(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(batch_size=3, count_per_batch=2)
        )
        (row,) = self.runner.active_batches()
        self.assertEqual(started.total_images, 6)
        self.assertEqual(row["total_steps"], 3)
        self.assertEqual(row["images_total"], 6)

    async def test_seed_sequences_per_policy(self) -> None:
        cases = {
            "increment": [100, 101, 102],
            "decrement": [100, 99, 98],
        }
        for policy, expected in cases.items():
            with self.subTest(policy=policy):
                started = await self.runner.start_batch(
                    self.manual(seed_policy=policy, count_per_batch=3)
                )
                self.assertEqual(self.planned_seeds(started.batch_id), expected)
        started = await self.runner.start_batch(
            self.manual(seed_policy="fixed", count_per_batch=1)
        )
        self.assertEqual(self.planned_seeds(started.batch_id), [100])

    async def test_the_random_policy_starts_at_the_given_seed_then_draws(self) -> None:
        started = await self.runner.start_batch(
            self.manual(seed_policy="random", count_per_batch=12)
        )
        seeds = self.planned_seeds(started.batch_id)
        self.assertEqual(len(seeds), 12)
        self.assertEqual(seeds[0], 100)
        self.assertTrue(all(0 <= s <= SEED_MAX for s in seeds))
        self.assertGreater(len(set(seeds)), 6)

    async def test_random_mode_steps_take_consecutive_seeds(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.seeds, [10, 11, 12, 13, 14, 15])
        self.assertEqual(
            [batch.step_seeds(i) for i in range(batch.total_steps)],
            [[10, 11], [12, 13], [14, 15]],
        )

    # -- next_seed is planned, not observed --------------------------------

    async def next_seed_of(self, req: BatchRequest) -> Optional[int]:
        await self.runner.start_batch(req)
        return self.runner.active_batches()[-1]["next_seed"]

    async def test_increment_reports_the_seed_after_the_whole_run(self) -> None:
        # Manual, increment, 3 images from 100: 100, 101, 102 -> 103, at
        # once, before any job event has happened.
        self.assertEqual(
            await self.next_seed_of(self.manual(seed=100, count_per_batch=3)), 103
        )

    async def test_decrement_reports_the_seed_after_the_whole_run(self) -> None:
        self.assertEqual(
            await self.next_seed_of(
                self.manual(seed=100, seed_policy="decrement", count_per_batch=4)
            ),
            96,
        )

    async def test_fixed_reports_its_own_seed(self) -> None:
        self.assertEqual(
            await self.next_seed_of(
                self.manual(seed=777, seed_policy="fixed", count_per_batch=1)
            ),
            777,
        )

    async def test_the_random_policy_plans_a_fresh_seed_as_its_next(self) -> None:
        # Randomize has no successor to compute, but the seed box must still
        # move on after a run: one more random seed is planned for it.
        following = await self.next_seed_of(
            self.manual(seed_policy="random", count_per_batch=3)
        )
        assert following is not None
        self.assertTrue(0 <= following <= SEED_MAX)

    async def test_random_mode_plans_its_steps_arithmetically(self) -> None:
        # batch_size 3 x count 2, increment from 10: six images, 10..15 -> 16.
        self.assertEqual(
            await self.next_seed_of(
                self.random_mode(seed=10, batch_size=3, count_per_batch=2)
            ),
            16,
        )

    async def test_next_seed_runs_out_with_the_range(self) -> None:
        # Decrement from 2 asked for five images: 2, 1, 0 and then nothing --
        # there is no unused seed left, so no next seed is reported.
        started = await self.runner.start_batch(
            self.manual(seed=2, seed_policy="decrement", count_per_batch=5)
        )
        self.assertEqual(self.planned_seeds(started.batch_id), [2, 1, 0])
        self.assertIsNone(self.runner.active_batches()[0]["next_seed"])

    async def test_next_seed_after_a_shortened_run_still_in_range(self) -> None:
        # Decrement from 3 asked for three: 3, 2, 1 -> 0 is still usable.
        self.assertEqual(
            await self.next_seed_of(
                self.manual(seed=3, seed_policy="decrement", count_per_batch=3)
            ),
            0,
        )

    # -- seed-range exhaustion (review focus 5) -----------------------------

    async def test_decrement_past_zero_shortens_the_run_with_a_warning(self) -> None:
        started = await self.runner.start_batch(
            self.manual(seed=2, seed_policy="decrement", count_per_batch=5)
        )
        self.assertEqual(started.total_images, 3)
        (row,) = self.runner.active_batches()
        self.assertEqual(row["images_total"], 3)
        self.assertEqual(len(started.warnings), 1)
        self.assertIn("shortened", started.warnings[0])
        self.assertIn("5", started.warnings[0])
        self.assertIn("3", started.warnings[0])
        self.assertIn("decrement", started.warnings[0])
        (frame,) = self.frames("batch_started")
        self.assertEqual(frame["total_images"], 3)

    async def test_increment_past_the_top_shortens_the_run_with_a_warning(
        self,
    ) -> None:
        started = await self.runner.start_batch(
            self.manual(seed=SEED_MAX - 1, seed_policy="increment", count_per_batch=4)
        )
        self.assertEqual(started.total_images, 2)
        self.assertEqual(self.planned_seeds(started.batch_id), [SEED_MAX - 1, SEED_MAX])
        self.assertTrue(any("shortened" in w for w in started.warnings))

    async def test_an_exact_fit_is_not_a_shortened_run(self) -> None:
        started = await self.runner.start_batch(
            self.manual(seed=2, seed_policy="decrement", count_per_batch=3)
        )
        self.assertEqual(started.total_images, 3)
        self.assertEqual(started.warnings, [])

    async def test_random_mode_shortens_to_a_partial_last_step(self) -> None:
        # Six images wanted (3 steps x 2), only five seeds fit: the last
        # step renders one image and the run has three steps still.
        started = await self.runner.start_batch(
            self.random_mode(
                seed=4, seed_policy="decrement", batch_size=3, count_per_batch=2
            )
        )
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(started.total_images, 5)
        self.assertEqual(batch.seeds, [4, 3, 2, 1, 0])
        self.assertEqual(batch.total_steps, 3)
        self.assertEqual([len(batch.step_seeds(i)) for i in range(3)], [2, 2, 1])
        self.assertTrue(any("shortened" in w for w in started.warnings))

    async def test_random_mode_drops_whole_steps_that_get_no_seed(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(
                seed=3, seed_policy="decrement", batch_size=5, count_per_batch=2
            )
        )
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.seeds, [3, 2, 1, 0])
        self.assertEqual(batch.total_steps, 2)
        self.assertEqual(self.runner.active_batches()[0]["total_steps"], 2)
        (frame,) = self.frames("batch_started")
        self.assertEqual(frame["total_steps"], 2)

    # -- Fixed reuses the seed and the cast (review focus 5) ----------------

    async def test_fixed_gives_every_step_the_same_character_seed(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(
                seed=55, seed_policy="fixed", batch_size=4, count_per_batch=1
            )
        )
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.seeds, [55, 55, 55, 55])
        self.assertEqual(
            [batch.step_seeds(i)[0] for i in range(batch.total_steps)], [55] * 4
        )
        self.assertEqual(self.runner.active_batches()[0]["next_seed"], 55)

    async def test_a_caption_draws_the_same_cast_whatever_the_seed(self) -> None:
        # The cast follows the caption text; the seed is free to change.
        one = await self.runner.resolve(caption=CAPTION, seed=55, model="krea2")
        other = await self.runner.resolve(caption=CAPTION, seed=56, model="krea2")
        self.assertEqual(one.characters, other.characters)
        self.assertEqual(one.text, other.text)
        elsewhere = await self.runner.resolve(
            caption="__ALICE__ paints a mural on a tall wall.", seed=55, model="krea2"
        )
        self.assertNotEqual(one.characters["ALICE"], elsewhere.characters["ALICE"])

    # -- what start_batch does and does not touch ---------------------------

    async def test_the_first_frames_are_started_then_progress(self) -> None:
        started = await self.runner.start_batch(self.manual(count_per_batch=3))
        self.assertEqual(self.names(), ["batch_started", "batch_progress"])
        self.assertEqual(
            self.frames("batch_started"),
            [
                {
                    "batch_id": started.batch_id,
                    "mode": "manual",
                    "total_steps": 1,
                    "total_images": 3,
                }
            ],
        )
        self.assertEqual(
            self.frames("batch_progress"),
            [
                {
                    "batch_id": started.batch_id,
                    "phase": "rendering",
                    "images_done": 0,
                    "images_failed": 0,
                    "images_total": 3,
                    "seed": 100,
                    "next_seed": 103,
                }
            ],
        )

    async def test_a_random_batch_starts_in_the_prompting_phase(self) -> None:
        started = await self.runner.start_batch(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        (progress,) = self.frames("batch_progress")
        self.assertEqual(progress["phase"], "prompting")
        self.assertEqual(progress["images_total"], 6)
        self.assertEqual(progress["seed"], 10)
        self.assertEqual(progress["next_seed"], 16)
        (frame,) = self.frames("batch_started")
        self.assertEqual(frame["mode"], "random")
        self.assertEqual(frame["total_steps"], 3)
        self.assertEqual(frame["batch_id"], started.batch_id)

    async def test_the_random_policy_progress_frame_carries_the_planned_seed(
        self,
    ) -> None:
        await self.runner.start_batch(self.manual(seed_policy="random"))
        (progress,) = self.frames("batch_progress")
        (row,) = self.runner.active_batches()
        self.assertIsInstance(progress["next_seed"], int)
        self.assertTrue(0 <= progress["next_seed"] <= SEED_MAX)
        self.assertEqual(progress["next_seed"], row["next_seed"])

    async def test_a_progress_frame_carries_no_last_error_yet(self) -> None:
        await self.runner.start_batch(self.manual())
        (progress,) = self.frames("batch_progress")
        self.assertNotIn("last_error", progress)

    async def test_planning_submits_and_prompts_nothing(self) -> None:
        await self.runner.start_batch(self.manual())
        await self.runner.start_batch(self.random_mode())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.db.list_generation_jobs(limit=100), [])

    async def test_a_started_batch_is_listed_in_the_documented_shape(self) -> None:
        started = await self.runner.start_batch(
            self.manual(seed=100, count_per_batch=3)
        )
        (row,) = self.runner.active_batches()
        self.assertEqual(
            sorted(row),
            [
                "batch_id",
                "images_done",
                "images_failed",
                "images_total",
                "mode",
                "next_seed",
                "seed",
                "started_at",
                "state",
                "step",
                "total_steps",
            ],
        )
        self.assertEqual(row["batch_id"], started.batch_id)
        self.assertEqual(row["mode"], "manual")
        self.assertEqual(row["state"], "rendering")
        self.assertEqual(row["seed"], 100)  # the first image's, rendering first
        self.assertIsNone(row["step"])  # no step has been written yet
        self.assertEqual((row["images_done"], row["images_failed"]), (0, 0))
        stamp = datetime.fromisoformat(row["started_at"])
        self.assertIsNotNone(stamp.tzinfo)
        json.dumps(row)  # plain data: it goes straight into a JSON response

    async def test_listed_batches_are_oldest_first_and_hand_out_copies(self) -> None:
        first = await self.runner.start_batch(self.manual())
        second = await self.runner.start_batch(self.manual())
        rows = self.runner.active_batches()
        self.assertEqual(
            [r["batch_id"] for r in rows], [first.batch_id, second.batch_id]
        )
        rows[0]["images_done"] = 99
        self.assertEqual(self.runner.active_batches()[0]["images_done"], 0)

    async def test_the_request_is_not_mutated(self) -> None:
        req = self.manual(
            preset_id=self.with_stack,
            loras=[{"name": "kite.safetensors", "strength": "0.5", "junk": 1}],
        )
        before = copy.deepcopy(req)
        await self.runner.start_batch(req)
        self.assertEqual(req, before)


# ---- running batches ------------------------------------------------------


class BatchCase(RunnerCase):
    """Real batches: the run task is live; ComfyUI and the VLM are fakes."""

    async def start(self, req: BatchRequest) -> BatchStarted:
        return await self.runner.start_batch(req)

    async def wait(self, batch_id: str, timeout: float = 10.0) -> None:
        """wait_batch, but a run that is stuck fails the test instead of
        hanging it (a window too small for the batch is the usual cause)."""
        try:
            await asyncio.wait_for(self.runner.wait_batch(batch_id), timeout)
        except asyncio.TimeoutError:
            self.fail("the batch's run did not finish")

    async def run_to_end(self, req: BatchRequest) -> str:
        """Start a batch and wait until every job has been submitted."""
        started = await self.start(req)
        await self.wait(started.batch_id)
        return started.batch_id

    def params(self) -> List[Any]:
        return [params for _, params, _ in self.comfy.submits]

    def written(self, batch_id: str) -> List[Dict[str, Any]]:
        """Every step the batch has written so far, in order. ``batch_step``
        puts them on show one at a time, as their images render, so the
        frames are not where to look for a step that is only written."""
        batch = self.runner._batches.get(batch_id) or self.runner._finished[batch_id]
        return copy.deepcopy(batch.steps)

    def timeline(self) -> List[Tuple[Any, ...]]:
        """The shared log without the per-start bookkeeping calls."""
        return [e for e in self.log if e != ("vlm", "ensure_started")]

    def terminal_frames(self) -> List[str]:
        return [
            n
            for n in self.names()
            if n in ("batch_complete", "batch_cancelled", "batch_error")
        ]


class TestManualBatchRun(BatchCase):
    async def test_submits_count_jobs_with_the_documented_params(self) -> None:
        started = await self.start(self.manual())
        await self.wait(started.batch_id)
        self.assertEqual(len(self.comfy.submits), 3)
        for index, (preset_id, params, kwargs) in enumerate(self.comfy.submits):
            self.assertEqual(preset_id, self.plain)
            self.assertEqual(params.positive, "A red kite over a gray sea.")
            self.assertEqual(params.seed, 100 + index)
            self.assertEqual((params.width, params.height), (1232, 816))
            self.assertEqual(params.batch_size, 1)
            self.assertIsNone(params.negative)
            self.assertEqual(params.loras, [])
            self.assertEqual(kwargs["t2i_batch_id"], started.batch_id)
            self.assertEqual(
                sorted(kwargs), ["output_dir", "output_name", "t2i_batch_id"]
            )
        self.assertEqual(
            self.comfy.job_ids,
            [j["id"] for j in self.db.list_generation_jobs(limit=10)],
        )

    async def test_sdxl_renders_on_the_64_grid(self) -> None:
        await self.run_to_end(self.manual(model="sd", count_per_batch=1))
        (params,) = self.params()
        self.assertEqual((params.width, params.height), (1216, 832))

    async def test_the_dims_follow_the_aspect_ratio_and_size(self) -> None:
        await self.run_to_end(
            self.manual(aspect_ratio="2:3", megapixels=1.0, count_per_batch=1)
        )
        await self.run_to_end(
            self.manual(aspect_ratio="1:1", megapixels=0.5, count_per_batch=1)
        )
        dims = [(p.width, p.height) for p in self.params()]
        self.assertEqual(
            dims,
            [t2i_dims("2:3", 1.0, 16), t2i_dims("1:1", 0.5, 16)],
        )
        self.assertEqual(dims[0], (816, 1232))

    async def test_the_negative_is_sent_only_when_the_workflow_binds_one(self) -> None:
        await self.run_to_end(
            self.manual(
                preset_id=self.with_negative, negative="blurry", count_per_batch=1
            )
        )
        await self.run_to_end(self.manual(negative="blurry", count_per_batch=1))
        bound, unbound = self.params()
        self.assertEqual(bound.negative, "blurry")
        self.assertIsNone(unbound.negative)

    async def test_a_blank_negative_is_not_sent(self) -> None:
        await self.run_to_end(
            self.manual(preset_id=self.with_negative, negative="  ", count_per_batch=1)
        )
        (params,) = self.params()
        self.assertIsNone(params.negative)

    async def test_loras_are_sent_normalised(self) -> None:
        await self.run_to_end(
            self.manual(
                preset_id=self.with_stack,
                count_per_batch=2,
                loras=[{"name": "kite.safetensors", "strength": "0.5", "junk": 1}],
            )
        )
        for params in self.params():
            self.assertEqual(
                params.loras, [{"name": "kite.safetensors", "strength": 0.5}]
            )

    async def test_output_lands_in_the_default_root_by_date(self) -> None:
        before = datetime.now().strftime("%Y-%m-%d")
        await self.run_to_end(self.manual(count_per_batch=2))
        after = datetime.now().strftime("%Y-%m-%d")
        for _, _, kwargs in self.comfy.submits:
            directory = Path(kwargs["output_dir"])
            self.assertIn(
                directory,
                (self.out_root / "t2i" / before, self.out_root / "t2i" / after),
            )
            self.assertRegex(kwargs["output_name"], r"^t2i_\d+$")

    async def test_output_lands_under_a_configured_root_and_prefix(self) -> None:
        library = self.root / "library"
        library.mkdir()
        self.cfg = get_t2i_config(
            {"t2i": {"output_root": str(library), "output_prefix": "/shots/%Y/kite_"}}
        )
        await self.run_to_end(self.manual(count_per_batch=1))
        ((_, _, kwargs),) = self.comfy.submits
        self.assertEqual(
            Path(kwargs["output_dir"]),
            library / "shots" / datetime.now().strftime("%Y"),
        )
        self.assertRegex(kwargs["output_name"], r"^kite_\d+$")
        self.assertFalse((self.out_root / "t2i").exists())

    async def test_output_numbers_strictly_increase(self) -> None:
        self.roomy()
        await self.run_to_end(self.manual(count_per_batch=6))
        numbers = [
            int(k["output_name"].split("_")[-1]) for _, _, k in self.comfy.submits
        ]
        self.assertEqual(numbers, sorted(set(numbers)))
        self.assertEqual(len(numbers), 6)

    async def test_the_job_rows_carry_the_batch_and_the_params(self) -> None:
        started = await self.start(self.manual(count_per_batch=2))
        await self.wait(started.batch_id)
        rows = self.db.list_generation_jobs(limit=10)
        self.assertEqual([r["t2i_batch_id"] for r in rows], [started.batch_id] * 2)
        self.assertEqual([json.loads(r["params"])["seed"] for r in rows], [100, 101])
        self.assertEqual(
            [r["output_name"] for r in rows],
            [k["output_name"] for _, _, k in self.comfy.submits],
        )

    async def test_the_frames_are_started_progress_step(self) -> None:
        started = await self.start(self.manual())
        await self.wait(started.batch_id)
        self.assertEqual(
            self.names(), ["batch_started", "batch_progress", "batch_step"]
        )

    async def test_the_step_frame_carries_the_given_values(self) -> None:
        started = await self.start(
            self.manual(preset_id=self.with_negative, negative="blurry")
        )
        await self.wait(started.batch_id)
        self.assertEqual(
            self.frames("batch_step"),
            [
                {
                    "batch_id": started.batch_id,
                    "step": 1,
                    "total_steps": 1,
                    "caption": "a red kite",
                    "aspect_ratio": "3:2",
                    "seed": 100,
                    "prompt": "A red kite over a gray sea.",
                    "negative": "blurry",
                    "warnings": [],
                    "direction": None,
                    "direction_parts": [],
                }
            ],
        )

    async def test_the_step_is_announced_before_the_first_submit(self) -> None:
        await self.run_to_end(self.manual())
        timeline = self.timeline()
        self.assertLess(
            timeline.index(("event", "batch_step")), timeline.index(("comfy", "submit"))
        )
        self.assertEqual(self.names().count("batch_step"), 1)

    async def test_a_manual_batch_without_a_caption_has_an_empty_one_in_the_frame(
        self,
    ) -> None:
        await self.run_to_end(self.manual(caption=None, count_per_batch=1))
        (step,) = self.frames("batch_step")
        self.assertEqual(step["caption"], "")

    async def test_the_prompt_is_the_users_text_untouched(self) -> None:
        # Parentheses are the user's to write in a Manual prompt.
        await self.run_to_end(
            self.manual(prompt="A (red) kite,  over a sea.", count_per_batch=1)
        )
        (params,) = self.params()
        self.assertEqual(params.positive, "A (red) kite,  over a sea.")

    async def test_a_manual_batch_never_asks_the_vlm_to_write_anything(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(self.manual())
        self.assertEqual(self.vlm.calls, [])
        self.assertEqual(self.vlm.ensure_calls, [])

    async def test_every_submitted_job_is_remembered_until_it_is_accounted_for(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(self.manual(count_per_batch=4))
        self.assertEqual(set(self.runner._job_meta), set(self.comfy.job_ids))
        self.assertEqual(set(self.runner._job_batch), set(self.comfy.job_ids))

    async def test_the_job_snapshots_are_bounded(self) -> None:
        # A backstop for anything that never gets its terminal update: the
        # oldest snapshots go first.
        self.roomy()
        with mock.patch("metascan.core.t2i_runner._JOB_META_LIMIT", 5):
            await self.run_to_end(self.manual(count_per_batch=9))
        self.assertEqual(set(self.runner._job_meta), set(self.comfy.job_ids[-5:]))


class TestGpuOrder(BatchCase):
    """Spec 6.3: with unload on, every prompt is written first, then the VLM
    is unloaded, then the first job is submitted."""

    async def test_unload_on_writes_every_prompt_then_unloads_then_renders(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(self.random_mode())  # 3 steps x 2 images
        expected: List[Tuple[Any, ...]] = [
            ("event", "batch_started"),
            ("event", "batch_progress"),
        ]
        # Batch 1 goes on show when its prompt is written; the prompts written
        # after it wait for their images to render.
        expected += [("vlm", "generate_text"), ("event", "batch_step")]
        expected += [("vlm", "generate_text")] * 2
        expected += [("event", "batch_progress"), ("vlm", "shutdown")]
        expected += [("comfy", "submit")] * 6
        self.assertEqual(self.timeline(), expected)

    async def test_unload_on_writes_every_prompt_even_when_the_window_is_full(
        self,
    ) -> None:
        assert self.vlm is not None
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.random_mode())
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        await self.quiet()
        self.assertEqual(len(self.vlm.calls), 3)  # all three prompts are in
        self.assertEqual(self.vlm.shutdowns, 1)
        self.assertEqual(len(self.comfy.submits), 1)  # the window holds the rest
        self.job_update(self.comfy.job_ids[0], "done")
        await self.until(lambda: len(self.comfy.submits) == 2, "the second submit")
        self.assertEqual(self.vlm.shutdowns, 1)
        self.assertIn(
            started.batch_id, [b["batch_id"] for b in self.runner.active_batches()]
        )

    async def test_unload_off_overlaps_prompts_and_renders(self) -> None:
        assert self.vlm is not None
        self.runner.unload_vlm_during_generation = False
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=4, count_per_batch=1))
        timeline = self.timeline()
        first_submit = timeline.index(("comfy", "submit"))
        last_prompt = max(
            i for i, e in enumerate(timeline) if e == ("vlm", "generate_text")
        )
        self.assertLess(
            first_submit, last_prompt
        )  # a job went out before the last prompt
        self.assertLess(timeline.index(("event", "batch_step")), first_submit)
        self.assertEqual(self.vlm.shutdowns, 0)
        self.assertEqual(len(self.comfy.submits), 4)

    async def test_unload_off_keeps_the_prompt_writer_only_a_step_or_two_ahead(
        self,
    ) -> None:
        # Window of one and nothing ever finishing: the consumer holds one
        # step, the queue one more, and the writer one it cannot hand over.
        assert self.vlm is not None
        self.runner.unload_vlm_during_generation = False
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        await self.start(self.random_mode(batch_size=6, count_per_batch=1))
        await self.until(lambda: len(self.vlm.calls) >= 4, "the writer to run ahead")
        await self.quiet()
        self.assertEqual(len(self.vlm.calls), 4)
        self.assertEqual(len(self.comfy.submits), 1)

    async def test_a_manual_batch_unloads_before_its_first_submit(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(self.manual(count_per_batch=2))
        self.assertEqual(self.vlm.shutdowns, 1)
        timeline = self.timeline()
        self.assertLess(
            timeline.index(("vlm", "shutdown")), timeline.index(("comfy", "submit"))
        )
        self.assertLess(
            timeline.index(("event", "batch_step")), timeline.index(("vlm", "shutdown"))
        )

    async def test_a_manual_batch_does_not_unload_when_unload_is_off(self) -> None:
        assert self.vlm is not None
        self.runner.unload_vlm_during_generation = False
        await self.run_to_end(self.manual())
        self.assertEqual(self.vlm.shutdowns, 0)

    async def test_nothing_is_unloaded_when_no_model_is_loaded(self) -> None:
        assert self.vlm is not None
        self.vlm.model_id = None
        await self.run_to_end(self.manual())
        self.assertEqual(self.vlm.shutdowns, 0)
        self.assertEqual(len(self.comfy.submits), 3)

    async def test_no_vlm_at_all_is_fine(self) -> None:
        self.vlm = None
        await self.run_to_end(self.manual())
        self.assertEqual(len(self.comfy.submits), 3)

    async def test_a_failing_unload_does_not_stop_the_run(self) -> None:
        assert self.vlm is not None
        self.vlm.shutdown_error = RuntimeError("llama-server would not die")
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING") as logs:
            await self.run_to_end(self.manual(count_per_batch=2))
        self.assertTrue(
            any("llama-server would not die" in line for line in logs.output)
        )
        self.assertEqual(len(self.comfy.submits), 2)

    async def test_the_vlm_stays_up_while_another_random_batch_is_still_prompting(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        gate = asyncio.Event()
        self.vlm.gates.append(gate)  # batch A's first prompt waits
        a = await self.start(self.random_mode(batch_size=1, count_per_batch=1))
        await self.until(lambda: len(self.vlm.calls) == 1, "batch A to be writing")
        b = await self.start(self.manual(count_per_batch=2))
        await self.wait(b.batch_id)
        self.assertEqual(len(self.comfy.submits), 2)  # B rendered...
        self.assertEqual(self.vlm.shutdowns, 0)  # ...without unloading A's model
        gate.set()
        await self.wait(a.batch_id)
        self.assertEqual(self.vlm.shutdowns, 1)  # A unloads once ITS prompts are in
        self.assertEqual(len(self.comfy.submits), 3)

    async def test_a_finished_random_batch_no_longer_holds_the_unload_back(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        self.assertEqual(self.vlm.shutdowns, 1)
        await self.run_to_end(self.manual(count_per_batch=1))
        self.assertEqual(self.vlm.shutdowns, 2)


class TestWindow(BatchCase):
    async def test_the_run_blocks_at_the_window_and_resumes_on_job_updates(
        self,
    ) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(self.manual(count_per_batch=5))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 2)  # blocked: nothing finished yet

        # Progress inside ComfyUI is not a way out of the window.
        self.job_update(self.comfy.job_ids[0], "running")
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 2)

        self.job_update(self.comfy.job_ids[0], "done")
        await self.until(lambda: len(self.comfy.submits) == 3, "a third submit")
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 3)

        self.job_update(self.comfy.job_ids[1], "failed", "node exploded")
        await self.until(lambda: len(self.comfy.submits) == 4, "a fourth submit")
        self.job_update(self.comfy.job_ids[2], "cancelled")
        await self.until(lambda: len(self.comfy.submits) == 5, "a fifth submit")
        await self.wait(started.batch_id)  # all five are out
        self.assertEqual([p.seed for p in self.params()], [100, 101, 102, 103, 104])

    async def test_a_job_that_finishes_the_moment_it_is_submitted_is_still_seen(
        self,
    ) -> None:
        # ComfyClient can announce a job's end a loop iteration after submit
        # returns. The runner must already know the job by then, or its slot
        # is never freed and the run sticks at the window.
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        self.comfy.finish_at_once = "done"
        started = await self.start(self.manual(count_per_batch=4))
        await self.wait(started.batch_id)
        self.assertEqual(len(self.comfy.submits), 4)

    async def test_a_repeated_terminal_update_frees_only_one_slot(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        first = self.comfy.job_ids[0]
        self.job_update(first, "done")
        self.job_update(first, "done")
        self.job_update(first, "failed", "late")
        await self.until(lambda: len(self.comfy.submits) == 2, "the second submit")
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 2)  # one slot, freed once

    async def test_a_terminal_update_forgets_the_jobs_batch_link(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        first, second = self.comfy.job_ids
        self.assertEqual(set(self.runner._job_batch), {first, second})
        self.job_update(first, "done")
        await self.until(lambda: len(self.comfy.submits) == 3, "a third submit")
        self.assertEqual(set(self.runner._job_batch), {second, self.comfy.job_ids[2]})

    async def test_an_update_for_a_job_of_another_batch_frees_nothing(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        a = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 1, "batch A's first submit")
        b = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 2, "batch B's first submit")
        job_a, job_b = self.comfy.job_ids
        self.job_update(job_a, "done")  # frees A's window, not B's
        await self.until(
            lambda: len(self.comfy.submits) == 3, "batch A's second submit"
        )
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 3)
        owners = [k["t2i_batch_id"] for _, _, k in self.comfy.submits]
        self.assertEqual(sorted(owners), sorted([a.batch_id, a.batch_id, b.batch_id]))
        self.job_update(job_b, "done")
        await self.until(
            lambda: len(self.comfy.submits) == 4, "batch B's second submit"
        )

    async def test_an_update_for_a_job_nobody_submitted_is_ignored(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        await self.start(self.manual(count_per_batch=2))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        self.job_update(987654, "done")
        self.comfy.emit("job_update", {"job_id": None, "state": "done"})
        self.comfy.emit("job_update", {"state": "done"})
        self.comfy.emit("job_progress", {"job_id": self.comfy.job_ids[0], "value": 1})
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 1)

    async def test_the_window_size_is_read_from_the_config_at_start(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 3}})
        await self.start(self.manual(count_per_batch=6))
        await self.until(lambda: len(self.comfy.submits) == 3, "three submits")
        self.cfg = get_t2i_config({"t2i": {"window": 1}})  # too late for this batch
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 3)


class TestRandomBatchRun(BatchCase):
    async def test_a_caption_is_never_repeated_within_a_batch(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(batch_size=6, count_per_batch=1)
        )
        captions = [step["caption"] for step in self.written(batch_id)]
        self.assertEqual(len(captions), 6)
        self.assertEqual(sorted(captions), sorted(row[0] for row in CSV_ROWS))

    async def test_the_step_takes_its_aspect_ratio_from_the_row(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                batch_size=2, count_per_batch=2, filter={"aspect_ratios": ["2:3"]}
            )
        )
        self.assertEqual(
            [s["aspect_ratio"] for s in self.written(batch_id)], ["2:3"] * 2
        )
        self.assertEqual(
            [(p.width, p.height) for p in self.params()], [(816, 1232)] * 4
        )

    async def test_the_steps_use_the_size_in_the_request(self) -> None:
        # The CSV has no size column: only the ratio comes from the row.
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                batch_size=1,
                count_per_batch=1,
                megapixels=0.5,
                filter={"aspect_ratios": ["1:1"]},
            )
        )
        (params,) = self.params()
        self.assertEqual((params.width, params.height), t2i_dims("1:1", 0.5, 16))

    async def test_a_ratio_outside_the_eleven_is_used_as_the_row_gives_it(self) -> None:
        self.roomy()
        self._write_csv(
            [["__ALICE__ stands.", "5:7", "none", "0.9", "0.1", "0.0", "0", "1", "[]"]]
        )
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        (step,) = self.frames("batch_step")
        (params,) = self.params()
        self.assertEqual(step["aspect_ratio"], "5:7")
        self.assertEqual((params.width, params.height), t2i_dims("5:7", 1.0, 16))
        self.assertEqual(step["warnings"], [])

    async def test_an_unusable_ratio_falls_back_to_square_with_a_warning(self) -> None:
        self.roomy()
        self._write_csv(
            [["__ALICE__ stands.", "0:5", "none", "0.9", "0.1", "0.0", "0", "1", "[]"]]
        )
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        (step,) = self.frames("batch_step")
        (params,) = self.params()
        self.assertEqual(step["aspect_ratio"], "1:1")
        self.assertEqual((params.width, params.height), t2i_dims("1:1", 1.0, 16))
        self.assertEqual(len(step["warnings"]), 1)
        self.assertIn("0:5", step["warnings"][0])

    async def test_the_prompt_seed_is_the_first_seed_of_the_step(self) -> None:
        assert self.vlm is not None
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        steps = self.written(batch_id)
        self.assertEqual([s["seed"] for s in steps], [10, 12, 14])
        self.assertEqual([p.seed for p in self.params()], [10, 11, 12, 13, 14, 15])
        for step, call in zip(steps, self.vlm.calls):
            resolved = self.expected_text(step["caption"], step["seed"])
            self.assertEqual(
                call["user_prompt"],
                f"DESCRIPTION:\n{resolved}\n\nWrite the prompt now.",
            )

    async def test_the_images_of_a_step_share_one_prompt_and_size(self) -> None:
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=2, count_per_batch=3))
        params = self.params()
        self.assertEqual(len(params), 6)
        for start in (0, 3):
            group = params[start : start + 3]
            self.assertEqual(len({p.positive for p in group}), 1)
            self.assertEqual(len({(p.width, p.height) for p in group}), 1)
            self.assertEqual(len({p.seed for p in group}), 3)

    async def test_the_prompt_is_written_in_the_models_identity_style(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self._write_csv(
            [
                [
                    "__ALICE__ smiles. __ALICE__ waves.",
                    "3:2",
                    "none",
                    "0.9",
                    "0.1",
                    "0.0",
                    "0",
                    "1",
                    "[]",
                ]
            ]
        )
        await self.run_to_end(
            self.random_mode(model="sd", seed=4, batch_size=1, count_per_batch=1)
        )
        text = self.vlm.calls[0]["user_prompt"]
        self.assertIn(
            self.expected_text("__ALICE__ smiles. __ALICE__ waves.", 4, "noun"), text
        )
        self.assertIn("The woman waves.", text)

    async def test_a_new_seed_changes_the_image_not_the_cast(self) -> None:
        # Review focus 5, reversed: the cast follows the caption text, so the
        # same caption is written up with the same characters under every seed.
        assert self.vlm is not None
        self.roomy()
        caption = "__ALICE__ walks along a beach."
        self._write_csv(
            [[caption, "3:2", "none", "0.9", "0.1", "0.0", "0", "1", "[]"]] * 3
        )
        batch_id = await self.run_to_end(
            self.random_mode(
                seed=55, seed_policy="increment", batch_size=3, count_per_batch=1
            )
        )
        steps = self.written(batch_id)
        self.assertEqual([s["seed"] for s in steps], [55, 56, 57])
        self.assertEqual([p.seed for p in self.params()], [55, 56, 57])
        self.assertEqual(len(self.vlm.calls), 3)
        expected = self.expected_text(caption, 55)
        for call in self.vlm.calls:
            self.assertIn(expected, call["user_prompt"])
        self.assertEqual(len({call["user_prompt"] for call in self.vlm.calls}), 1)

    async def test_the_step_frame_carries_prompt_negative_and_warnings(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(
                model="sd",
                preset_id=self.with_negative,
                batch_size=1,
                count_per_batch=1,
                filter={"aspect_ratios": ["3:2"]},
            )
        )
        await self.wait(started.batch_id)
        (step,) = self.frames("batch_step")
        self.assertEqual(step["batch_id"], started.batch_id)
        self.assertEqual(step["step"], 1)
        self.assertEqual(step["total_steps"], 1)
        self.assertEqual(step["caption"], CSV_ROWS[0][0])
        self.assertEqual(step["aspect_ratio"], "3:2")
        self.assertEqual(step["seed"], 10)
        self.assertEqual(step["prompt"], "A calm beach scene at dawn.")
        self.assertEqual(step["negative"], "blurry, watermark")
        self.assertEqual(step["warnings"], [])
        (params,) = self.params()
        self.assertEqual(params.negative, "blurry, watermark")

    async def test_a_step_with_no_negative_model_sends_none(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                preset_id=self.with_negative, batch_size=1, count_per_batch=1
            )
        )
        (step,) = self.frames("batch_step")
        (params,) = self.params()
        self.assertIsNone(step["negative"])  # krea2 has no negative prompt
        self.assertIsNone(params.negative)

    async def test_engine_warnings_ride_on_the_step(self) -> None:
        self.roomy()
        self._write_csv(
            [
                [
                    "__ALICE__ adjusts her __BREASTS__ top.",
                    "3:2",
                    "none",
                    "0.9",
                    "0.1",
                    "0.0",
                    "0",
                    "1",
                    "[]",
                ]
            ]
        )
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        (step,) = self.frames("batch_step")
        self.assertEqual(len(step["warnings"]), 1)
        self.assertIn("BREASTS", step["warnings"][0])

    async def test_the_phase_flips_to_rendering_and_next_seed_never_changes(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        frames = self.frames("batch_progress")
        self.assertEqual([f["phase"] for f in frames], ["prompting", "rendering"])
        self.assertEqual({f["next_seed"] for f in frames}, {16})
        self.assertEqual({f["images_total"] for f in frames}, {6})

    async def test_a_step_is_listed_while_the_next_prompt_is_written(self) -> None:
        assert self.vlm is not None
        self.vlm.gates.extend([None, asyncio.Event()])
        started = await self.start(self.random_mode(batch_size=3, count_per_batch=1))
        (row,) = self.runner.active_batches()
        self.assertIsNone(row["step"])
        await self.until(lambda: len(self.vlm.calls) == 2, "the second prompt to start")
        (row,) = self.runner.active_batches()
        self.assertEqual(row["batch_id"], started.batch_id)
        self.assertEqual(row["state"], "prompting")
        self.assertEqual(row["total_steps"], 3)
        step = row["step"]
        self.assertEqual(
            sorted(step),
            [
                "aspect_ratio",
                "caption",
                "direction",
                "direction_parts",
                "negative",
                "prompt",
                "seed",
                "step",
                "total_steps",
                "warnings",
            ],
        )
        self.assertEqual((step["step"], step["total_steps"], step["seed"]), (1, 3, 10))
        self.assertEqual(step["prompt"], "A calm beach scene at dawn.")
        self.assertEqual(self.comfy.submits, [])  # unload on: nothing renders yet

    async def test_a_batch_that_stops_early_runs_the_steps_it_planned(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(
                seed=3, seed_policy="decrement", batch_size=5, count_per_batch=2
            )
        )
        await self.wait(started.batch_id)
        self.assertEqual(started.total_images, 4)
        self.assertEqual(len(self.written(started.batch_id)), 2)
        self.assertEqual([p.seed for p in self.params()], [3, 2, 1, 0])
        self.assertTrue(any("shortened" in w for w in started.warnings))

    async def test_the_random_policy_draws_a_fresh_seed_for_every_image(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                seed=7, seed_policy="random", batch_size=4, count_per_batch=1
            )
        )
        seeds = [p.seed for p in self.params()]
        self.assertEqual(seeds[0], 7)
        self.assertEqual(len(set(seeds)), 4)
        self.assertTrue(all(0 <= s <= SEED_MAX for s in seeds))
        self.assertEqual(
            [s["seed"] for s in self.frames("batch_step")], seeds[:1]
        )  # only the batch being rendered is on show
        following = {f["next_seed"] for f in self.frames("batch_progress")}
        self.assertEqual(len(following), 1)  # one fresh seed, planned once
        (planned,) = following
        self.assertTrue(0 <= planned <= SEED_MAX)


class TestRandomFirstStep(BatchCase):
    """The dialog's Generate Prompt, then Generate in Random Caption mode: a
    Random request that carries a prompt renders it as step 1, with no
    caption drawn and no model call for it. Only the later steps draw a
    caption and have a prompt written."""

    PROMPT = "A red kite over a gray sea."

    def supplied(self, **over: Any) -> BatchRequest:
        fields: Dict[str, Any] = dict(
            caption="a red kite", prompt=self.PROMPT, aspect_ratio="2:3"
        )
        fields.update(over)
        return self.random_mode(**fields)

    async def test_a_supplied_prompt_is_step_one_and_needs_no_model_call(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(self.supplied(seed=42, batch_size=1, count_per_batch=1))
        (step,) = self.frames("batch_step")
        self.assertEqual(step["prompt"], self.PROMPT)
        self.assertEqual(step["caption"], "a red kite")
        self.assertEqual(step["aspect_ratio"], "2:3")
        self.assertEqual(step["seed"], 42)
        self.assertEqual(self.vlm.calls, [])
        (params,) = self.params()
        self.assertEqual(params.positive, self.PROMPT)
        self.assertEqual((params.width, params.height), (816, 1232))

    async def test_only_step_one_is_supplied_the_rest_are_drawn_and_written(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        batch_id = await self.run_to_end(
            self.supplied(seed=10, batch_size=3, count_per_batch=2)
        )
        steps = self.written(batch_id)
        self.assertEqual([s["seed"] for s in steps], [10, 12, 14])
        self.assertEqual(steps[0]["caption"], "a red kite")
        drawn = [s["caption"] for s in steps[1:]]
        self.assertEqual(len(set(drawn)), 2)
        self.assertLessEqual(set(drawn), {row[0] for row in CSV_ROWS})
        # The model wrote exactly the two drawn steps and never saw the caption
        # the request supplied.
        self.assertEqual(len(self.vlm.calls), 2)
        for call in self.vlm.calls:
            self.assertNotIn("red kite", call["user_prompt"])
        positives = [p.positive for p in self.params()]
        self.assertEqual(positives[:2], [self.PROMPT] * 2)
        for positive in positives[2:]:
            self.assertTrue(positive.startswith("A calm beach scene at dawn."))

    async def test_a_supplied_step_draws_no_caption(self) -> None:
        # One draw per drawn step and none for the supplied one: a row drawn
        # and thrown away would be a reroll the user never sees. The real
        # picker still runs; the spy only counts. (Counting rows instead does
        # not work: the picker never repeats the last row when it reshuffles,
        # which hides a wasted draw.)
        self.roomy()
        with mock.patch.object(
            CaptionPicker, "next", autospec=True, side_effect=CaptionPicker.next
        ) as draw:
            await self.run_to_end(self.supplied(batch_size=3, count_per_batch=1))
        self.assertEqual(draw.call_count, 2)

    async def test_a_supplied_negative_reaches_the_workflow(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(
            self.supplied(
                model="sd",
                preset_id=self.with_negative,
                batch_size=1,
                count_per_batch=1,
                negative="blurry, extra fingers",
            )
        )
        (step,) = self.frames("batch_step")
        self.assertEqual(step["negative"], "blurry, extra fingers")
        (params,) = self.params()
        self.assertEqual(params.negative, "blurry, extra fingers")
        self.assertEqual(self.vlm.calls, [])

    async def test_a_supplied_step_without_a_negative_gets_none_written(self) -> None:
        # The model would have written "blurry, watermark" for a drawn step.
        await self.run_to_end(
            self.supplied(
                model="sd",
                preset_id=self.with_negative,
                batch_size=1,
                count_per_batch=1,
            )
        )
        (step,) = self.frames("batch_step")
        self.assertIsNone(step["negative"])
        (params,) = self.params()
        self.assertIsNone(params.negative)

    async def test_the_supplied_step_is_announced_before_the_model_is_asked(
        self,
    ) -> None:
        # What the dialog shows while step 2's prompt is being written: the
        # supplied prompt, not empty boxes waiting for a model call.
        assert self.vlm is not None
        self.vlm.gates.append(asyncio.Event())
        started = await self.start(self.supplied(batch_size=2, count_per_batch=1))
        await self.until(lambda: len(self.vlm.calls) == 1, "step 2's prompt to start")
        (step,) = self.frames("batch_step")
        self.assertEqual(step["prompt"], self.PROMPT)
        (row,) = self.runner.active_batches()
        self.assertEqual(row["step"]["prompt"], self.PROMPT)
        self.assertLess(
            self.log.index(("event", "batch_step")),
            self.log.index(("vlm", "generate_text")),
        )
        await self.runner.cancel_batch(started.batch_id)


class TestRandomFirstCaption(BatchCase):
    """A Random request that names a caption but carries no prompt (the dialog's
    Prompt box is empty, its Caption box is not): step 1 has a prompt written
    for THAT caption and no caption is drawn for it. The later steps are drawn
    and written as always."""

    # Not a row of the test CSV, so a later drawn step can never contain it.
    CAPTION = "__ALICE__ feeds ducks at a pond."

    def named(self, **over: Any) -> BatchRequest:
        fields: Dict[str, Any] = dict(caption=self.CAPTION, aspect_ratio="2:3")
        fields.update(over)
        return self.random_mode(**fields)

    async def test_step_one_is_written_for_the_caption_given(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(self.named(seed=42, batch_size=1, count_per_batch=1))
        (step,) = self.frames("batch_step")
        self.assertEqual(step["caption"], self.CAPTION)
        self.assertEqual(step["aspect_ratio"], "2:3")
        self.assertEqual(step["seed"], 42)
        self.assertEqual(len(self.vlm.calls), 1)
        self.assertIn(
            self.expected_text(self.CAPTION, 42), self.vlm.calls[0]["user_prompt"]
        )
        (params,) = self.params()
        self.assertEqual(params.positive, step["prompt"])
        self.assertEqual((params.width, params.height), (816, 1232))

    async def test_no_caption_is_drawn_for_step_one(self) -> None:
        # One draw per later step and none for the named one (see
        # test_a_supplied_step_draws_no_caption for why this is a spy).
        self.roomy()
        with mock.patch.object(
            CaptionPicker, "next", autospec=True, side_effect=CaptionPicker.next
        ) as draw:
            await self.run_to_end(self.named(batch_size=3, count_per_batch=1))
        self.assertEqual(draw.call_count, 2)

    async def test_the_steps_after_it_are_drawn_and_written(self) -> None:
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(self.named(seed=10, batch_size=3, count_per_batch=2))
        self.assertEqual(len(self.vlm.calls), 3)  # one prompt per step
        asked = [call["user_prompt"] for call in self.vlm.calls]
        self.assertIn(self.expected_text(self.CAPTION, 10), asked[0])
        for later in asked[1:]:
            self.assertNotIn("feeds ducks", later)
        self.assertEqual([p.seed for p in self.params()], [10, 11, 12, 13, 14, 15])

    async def test_a_prompt_wins_over_a_caption(self) -> None:
        assert self.vlm is not None
        await self.run_to_end(
            self.named(
                prompt="A red kite over a gray sea.", batch_size=1, count_per_batch=1
            )
        )
        self.assertEqual(self.vlm.calls, [])
        (params,) = self.params()
        self.assertEqual(params.positive, "A red kite over a gray sea.")

    async def test_a_blank_caption_names_nothing(self) -> None:
        self.roomy()
        with mock.patch.object(
            CaptionPicker, "next", autospec=True, side_effect=CaptionPicker.next
        ) as draw:
            await self.run_to_end(
                self.named(caption="   ", batch_size=2, count_per_batch=1)
            )
        self.assertEqual(draw.call_count, 2)

    async def test_its_aspect_ratio_is_checked_like_a_supplied_prompts(self) -> None:
        for bad in (None, "", "wide", "0:5"):
            with self.subTest(aspect=bad):
                with self.assertRaises(T2iRequestError):
                    await self.runner.start_batch(self.named(aspect_ratio=bad))
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_negative_the_model_will_write_is_warned_about_when_unusable(
        self,
    ) -> None:
        # The model writes step 1's negative here, so a workflow with no
        # MS_NEGATIVE node drops it, once, loudly (a supplied prompt has no
        # negative to drop unless the user typed one).
        started = await self.start(
            self.named(model="sd", batch_size=1, count_per_batch=1)
        )
        self.assertEqual(
            started.warnings,
            ["negative prompt ignored: the workflow has no MS_NEGATIVE node"],
        )
        await self.wait(started.batch_id)

    async def test_the_named_caption_is_remembered_with_its_images(self) -> None:
        await self.run_to_end(self.named(batch_size=1, count_per_batch=1))
        (job_id,) = self.comfy.job_ids
        self.assertEqual(self.runner._job_meta[job_id]["caption"], self.CAPTION)


class TestPromptRetryAndFallback(BatchCase):
    def one_step(self, **over: Any) -> BatchRequest:
        """One caption, always the first CSV row (the only 3:2 one)."""
        fields: Dict[str, Any] = dict(
            batch_size=1, count_per_batch=1, filter={"aspect_ratios": ["3:2"]}
        )
        fields.update(over)
        return self.random_mode(**fields)

    async def test_a_failed_call_is_retried_once(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend([VlmError("boom"), "A crisp prompt."])
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(step["prompt"], "A crisp prompt.")
        self.assertEqual(step["warnings"], [])
        self.assertEqual(len(self.vlm.calls), 2)
        self.assertEqual(
            len(self.vlm.ensure_calls), 2
        )  # the model is started again too
        self.assertEqual([p.positive for p in self.params()], ["A crisp prompt."])

    async def test_timeouts_and_runtime_errors_are_retried_too(self) -> None:
        assert self.vlm is not None
        self.roomy()
        for error in (TimeoutError("slow"), RuntimeError("odd")):
            with self.subTest(error=type(error).__name__):
                self.vlm.calls.clear()
                self.events.clear()
                self.vlm.replies.extend([error, "Second time lucky."])
                batch_id = await self.run_to_end(self.one_step())
                self.assertEqual(len(self.vlm.calls), 2)
                self.assertEqual(
                    self.frames("batch_step")[0]["prompt"], "Second time lucky."
                )
                await self.runner.cancel_batch(batch_id)  # frees the Random slot

    async def test_two_failures_fall_back_to_the_resolved_caption(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend([VlmError("first"), VlmError("boom")])
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        library, _ = self.library.get()
        resolved = resolve_caption(CSV_ROWS[0][0], 10, "ref", library).text
        self.assertEqual(step["prompt"], resolved)
        self.assertEqual(
            step["warnings"], ["VLM failed (boom) - used the resolved caption"]
        )
        self.assertEqual(len(self.vlm.calls), 2)  # one retry, no more
        # The run does not stop: the fallback prompt is rendered.
        self.assertEqual([p.positive for p in self.params()], [resolved])

    async def test_the_failure_reason_is_one_short_line(self) -> None:
        assert self.vlm is not None
        self.roomy()
        long_reason = "first line\nsecond line " + "x" * 400
        self.vlm.replies.extend([VlmError(long_reason), VlmError(long_reason)])
        await self.run_to_end(self.one_step())
        (warning,) = self.frames("batch_step")[0]["warnings"]
        self.assertTrue(warning.startswith("VLM failed (first line second line xxx"))
        self.assertTrue(warning.endswith(") - used the resolved caption"))
        self.assertNotIn("\n", warning)
        self.assertLess(len(warning), 250)

    async def test_a_blank_reason_names_the_exception(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend([VlmError(""), VlmError("")])
        await self.run_to_end(self.one_step())
        self.assertEqual(
            self.frames("batch_step")[0]["warnings"],
            ["VLM failed (VlmError) - used the resolved caption"],
        )

    async def test_a_model_that_will_not_start_counts_as_a_failed_attempt(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.start_errors.extend([VlmError("no gpu"), VlmError("no gpu")])
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(
            step["warnings"], ["VLM failed (no gpu) - used the resolved caption"]
        )
        self.assertEqual(self.vlm.calls, [])
        self.assertEqual(len(self.comfy.submits), 1)

    async def test_an_empty_reply_is_a_failed_attempt(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend(["", "  "])
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(len(self.vlm.calls), 2)
        self.assertIn("empty prompt", step["warnings"][0])

    async def test_no_vlm_falls_back_without_retrying(self) -> None:
        self.roomy()
        self.vlm = None
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(
            step["warnings"], ["VLM unavailable - used the resolved caption"]
        )
        self.assertEqual(len(self.comfy.submits), 1)

    async def test_no_loadable_model_falls_back_without_retrying(self) -> None:
        assert self.vlm is not None
        self.roomy()
        with mock.patch(
            "metascan.core.t2i_runner.pick_vlm_model",
            side_effect=VlmSelectError("no VLM model available on this hardware"),
        ):
            await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(
            step["warnings"], ["VLM unavailable - used the resolved caption"]
        )
        self.assertEqual(self.vlm.calls, [])

    async def test_a_model_that_is_not_installed_falls_back_without_retrying(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        self.runner.vlm_installed = lambda model_id: False
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(
            step["warnings"], ["VLM unavailable - used the resolved caption"]
        )
        self.assertEqual(self.vlm.ensure_calls, [])
        self.assertEqual(len(self.comfy.submits), 1)

    async def test_a_model_that_cannot_be_spawned_falls_back_like_any_failure(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        missing = FileNotFoundError(2, "No such file or directory", "llama-server")
        self.vlm.start_errors.extend([missing, missing])
        await self.run_to_end(self.one_step())
        (step,) = self.frames("batch_step")
        self.assertEqual(len(step["warnings"]), 1)
        self.assertTrue(step["warnings"][0].startswith("VLM failed ("))
        self.assertEqual(self.frames("batch_error"), [])
        self.assertEqual(len(self.comfy.submits), 1)  # the fallback prompt renders

    async def test_each_step_falls_back_on_its_own(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend([VlmError("x"), VlmError("x"), "Written by the model."])
        batch_id = await self.run_to_end(
            self.random_mode(batch_size=2, count_per_batch=1)
        )
        first, second = self.written(batch_id)
        self.assertEqual(
            first["warnings"], ["VLM failed (x) - used the resolved caption"]
        )
        self.assertEqual(second["warnings"], [])
        self.assertEqual(second["prompt"], "Written by the model.")

    async def test_the_sdxl_fallback_brings_its_stock_negative_and_prefix(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend([VlmError("x"), VlmError("x")])
        await self.run_to_end(self.one_step(model="sd", preset_id=self.with_negative))
        store = get_prompt_store()
        (step,) = self.frames("batch_step")
        self.assertTrue(
            step["prompt"].startswith(store.get("T2I_FALLBACK_PREFIX_SD").strip())
        )
        self.assertEqual(
            step["negative"], store.get("T2I_FALLBACK_NEGATIVE_SD").strip()
        )
        (params,) = self.params()
        self.assertEqual(params.negative, store.get("T2I_FALLBACK_NEGATIVE_SD").strip())

    async def test_the_fallback_has_no_parentheses(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self._write_csv(
            [
                [
                    "A (very) quiet harbour.",
                    "3:2",
                    "none",
                    "0.9",
                    "0.1",
                    "0.0",
                    "0",
                    "0",
                    "[]",
                ]
            ]
        )
        self.vlm.replies.extend([VlmError("x"), VlmError("x")])
        await self.run_to_end(self.one_step())
        (params,) = self.params()
        self.assertEqual(params.positive, "A very quiet harbour.")

    async def test_an_unexpected_error_fails_the_batch_instead_of_being_swallowed(
        self,
    ) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.append(ValueError("a bug, not a VLM failure"))
        started = await self.start(self.one_step())
        await self.wait(started.batch_id)
        self.assertEqual(self.terminal_frames(), ["batch_error"])
        (frame,) = self.frames("batch_error")
        self.assertIn("a bug, not a VLM failure", frame["error"])
        self.assertEqual(self.comfy.submits, [])
        self.assertEqual(len(self.vlm.calls), 1)  # not retried
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_caption_file_that_vanishes_mid_run_fails_the_batch(self) -> None:
        self.roomy()
        started = await self.runner.start_batch(self.one_step(batch_size=2))
        self.csv_path.unlink()
        await self.wait(started.batch_id)
        self.assertEqual(self.terminal_frames(), ["batch_error"])
        self.assertEqual(self.runner.active_batches(), [])


class TestCancel(BatchCase):
    async def test_cancel_during_the_prompt_phase(self) -> None:
        assert self.vlm is not None
        gate = asyncio.Event()
        self.vlm.gates.append(gate)  # the first prompt never comes back
        started = await self.start(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        await self.until(
            lambda: len(self.vlm.calls) == 1, "the VLM call to be in flight"
        )

        self.assertTrue(await self.runner.cancel_batch(started.batch_id))

        self.assertEqual(
            self.vlm.cancelled_calls, 1
        )  # the in-flight call was cancelled
        self.assertEqual(self.comfy.submits, [])  # nothing was ever submitted
        self.assertEqual(self.vlm.shutdowns, 0)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])  # exactly one
        (frame,) = self.frames("batch_cancelled")
        self.assertEqual(
            frame,
            {
                "batch_id": started.batch_id,
                "images_done": 0,
                "images_failed": 0,
                "images_total": 6,
                "images_cancelled": 6,
                "next_seed": 16,
            },
        )
        self.assertEqual(self.runner.active_batches(), [])
        await asyncio.wait_for(self.runner.wait_batch(started.batch_id), 1)
        leftover = [
            t.get_name()
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and t.get_name().startswith("t2i-")
        ]
        self.assertEqual(leftover, [])

    async def test_cancel_during_rendering_cancels_the_unfinished_jobs(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(self.manual(count_per_batch=5))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")

        self.assertTrue(await self.runner.cancel_batch(started.batch_id))

        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)
        self.assertEqual(len(self.comfy.submits), 2)  # the run stopped for good
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        timeline = self.timeline()
        self.assertLess(
            timeline.index(("comfy", "cancel", self.comfy.job_ids[-1])),
            timeline.index(("event", "batch_cancelled")),
        )
        await self.quiet()  # the jobs' own cancelled updates arrive: nothing more happens
        self.assertEqual(len(self.comfy.submits), 2)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])

    async def test_cancel_leaves_no_orphan_state(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(self.manual(count_per_batch=5))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        await self.runner.cancel_batch(started.batch_id)
        batch = self.runner._finished[started.batch_id]
        self.assertEqual(batch.pending, set())
        self.assertEqual((batch.unresolved, batch.ingesting), (set(), set()))
        self.assertEqual(batch.window._value, 2)  # every slot is back
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._job_meta, {})
        rows = self.db.list_generation_jobs(limit=10)
        self.assertEqual({r["state"] for r in rows}, {"cancelled"})

    async def test_cancel_only_touches_jobs_that_have_not_finished(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(self.manual(count_per_batch=5))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        self.job_update(self.comfy.job_ids[0], "done")
        await self.until(lambda: len(self.comfy.submits) == 3, "a third submit")
        await self.runner.cancel_batch(started.batch_id)
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids[1:])

    async def test_cancel_is_idempotent_and_unknown_ids_are_not_found(self) -> None:
        started = await self.start(self.manual(count_per_batch=1))
        await self.wait(started.batch_id)
        self.assertTrue(await self.runner.cancel_batch(started.batch_id))
        self.assertFalse(await self.runner.cancel_batch(started.batch_id))
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        with self.assertRaises(T2iNotFoundError):
            await self.runner.cancel_batch("no-such-batch")

    async def test_a_failing_comfy_cancel_does_not_stop_the_cancel(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 3}})
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 3, "three submits")
        self.comfy.cancel_errors[self.comfy.job_ids[1]] = RuntimeError(
            "ComfyUI is gone"
        )
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING") as logs:
            self.assertTrue(await self.runner.cancel_batch(started.batch_id))
        self.assertTrue(any("ComfyUI is gone" in line for line in logs.output))
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)  # all were attempted
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        batch = self.runner._finished[started.batch_id]
        self.assertEqual(batch.window._value, 3)
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._job_meta, {})

    async def test_cancel_while_a_submit_is_in_flight_still_cancels_that_job(
        self,
    ) -> None:
        # ComfyClient.submit writes the job row and only then queues it, so
        # a cancel landing in between must not lose the id: the job would sit
        # queued in the database and run after the next restart.
        self.comfy.hold = asyncio.Event()
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(
            lambda: len(self.comfy.submits) == 1, "the first submit to start"
        )
        cancel = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        self.assertFalse(cancel.done())  # waiting for the submit to land
        self.comfy.hold.set()
        self.assertTrue(await cancel)
        self.assertEqual(len(self.comfy.submits), 1)
        (job,) = self.db.list_generation_jobs(limit=10)
        self.assertEqual(self.comfy.cancelled, [job["id"]])
        self.assertEqual(job["state"], "cancelled")
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        # The landed job owned the slot: it comes back once, not twice.
        self.assertEqual(self.runner._finished[started.batch_id].window._value, 4)

    async def test_a_caller_that_goes_away_mid_cancel_does_not_strand_the_batch(
        self,
    ) -> None:
        # The cancel is shielded from the request that asked for it: if that
        # request is dropped while the cancel waits on an in-flight submit,
        # the batch must still end, once.
        self.comfy.hold = asyncio.Event()
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(
            lambda: len(self.comfy.submits) == 1, "the first submit to start"
        )
        caller = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.comfy.hold.set()
        await self.until(
            lambda: self.runner.active_batches() == [], "the cancel to finish anyway"
        )
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)

    async def test_a_cancelled_submit_that_then_fails_gives_its_slot_back(self) -> None:
        self.comfy.gate = asyncio.Event()  # the submit is in flight...
        self.comfy.submit_errors[0] = ComfyError("nope")  # ...and will fail
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(
            lambda: len(self.comfy.submits) == 1, "the first submit to start"
        )
        cancel = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        self.assertFalse(cancel.done())
        self.comfy.gate.set()
        self.assertTrue(await cancel)
        self.assertEqual(self.comfy.cancelled, [])  # no job ever existed
        batch = self.runner._finished[started.batch_id]
        self.assertEqual(batch.window._value, 4)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])

    async def test_two_cancels_at_once_end_the_batch_once(self) -> None:
        self.comfy.hold = asyncio.Event()  # keeps the first cancel waiting
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(
            lambda: len(self.comfy.submits) == 1, "the first submit to start"
        )
        first = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        second = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        self.assertTrue(second.done())
        self.assertFalse(second.result())  # already being cancelled
        self.comfy.hold.set()
        self.assertTrue(await first)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)

    async def test_a_finished_random_batch_lets_go_of_the_caption_index(self) -> None:
        started = await self.start(self.random_mode())
        batch = self.runner._batches[started.batch_id]
        self.assertIsNotNone(batch.picker)
        await self.runner.cancel_batch(started.batch_id)
        self.assertIsNone(batch.picker)

    async def test_only_the_last_fifty_finished_batches_are_remembered(self) -> None:
        ids = []
        for _ in range(55):
            started = await self.start(self.manual(count_per_batch=1))
            await self.runner.cancel_batch(started.batch_id)
            ids.append(started.batch_id)
        self.assertEqual(len(self.runner._finished), 50)
        for forgotten in ids[:5]:
            with self.assertRaises(T2iNotFoundError):
                await self.runner.cancel_batch(forgotten)
        for remembered in ids[5:]:
            self.assertFalse(await self.runner.cancel_batch(remembered))

    async def test_cancel_right_after_start_is_clean(self) -> None:
        started = await self.start(self.manual(count_per_batch=4))
        self.assertTrue(await self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertEqual(self.runner.active_batches(), [])
        submitted = len(self.comfy.submits)
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), submitted)  # nothing after the cancel

    async def test_cancelling_one_batch_leaves_the_others_alone(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        a = await self.start(self.manual(count_per_batch=4))
        b = await self.start(self.manual(count_per_batch=4))
        await self.until(
            lambda: len(self.comfy.submits) == 4, "both batches to fill their windows"
        )
        await self.runner.cancel_batch(a.batch_id)
        self.assertEqual(
            [r["batch_id"] for r in self.runner.active_batches()], [b.batch_id]
        )
        job_b = [
            j["id"]
            for j in self.db.list_generation_jobs(limit=10)
            if j["t2i_batch_id"] == b.batch_id
        ]
        self.assertEqual(set(job_b) & set(self.comfy.cancelled), set())
        self.job_update(job_b[0], "done")
        await self.until(lambda: len(self.comfy.submits) == 5, "batch B to carry on")

    async def test_a_cancelled_random_batch_frees_the_random_slot(self) -> None:
        started = await self.start(self.random_mode())
        await self.runner.cancel_batch(started.batch_id)
        again = await self.start(self.random_mode())
        self.assertNotEqual(again.batch_id, started.batch_id)

    async def test_a_cancelled_batch_stays_known_for_a_while(self) -> None:
        started = await self.start(self.manual(count_per_batch=1))
        await self.runner.cancel_batch(started.batch_id)
        self.assertNotIn(
            started.batch_id, [b["batch_id"] for b in self.runner.active_batches()]
        )
        self.assertIn(started.batch_id, self.runner._finished)


class TestSubmitError(BatchCase):
    async def test_a_submit_error_stops_the_run_and_reports_once(self) -> None:
        self.comfy.submit_errors[2] = ComfyError("ComfyUI rejected the job: 400")
        started = await self.start(self.manual(count_per_batch=5))
        await self.wait(started.batch_id)

        self.assertEqual(len(self.comfy.submits), 3)  # two out, the third refused
        self.assertEqual(
            self.comfy.cancelled, self.comfy.job_ids
        )  # the two are pulled back
        self.assertEqual(self.terminal_frames(), ["batch_error"])
        self.assertNotIn("batch_complete", self.names())
        (frame,) = self.frames("batch_error")
        self.assertEqual(
            frame,
            {
                "batch_id": started.batch_id,
                "error": "ComfyUI rejected the job: 400",
                "images_done": 0,
                "images_failed": 0,
                "images_total": 5,
                "images_cancelled": 5,
                "next_seed": 105,
            },
        )
        self.assertEqual(self.runner.active_batches(), [])
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 3)  # never tried again
        self.assertEqual(self.terminal_frames(), ["batch_error"])

    async def test_a_submit_error_leaves_no_orphan_state(self) -> None:
        self.comfy.submit_errors[2] = ComfyError("nope")
        started = await self.start(self.manual(count_per_batch=5))
        await self.wait(started.batch_id)
        batch = self.runner._finished[started.batch_id]
        self.assertEqual(batch.pending, set())
        self.assertEqual(
            batch.window._value, 4
        )  # the failing submit's slot is back too
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._job_meta, {})
        self.assertEqual(
            {r["state"] for r in self.db.list_generation_jobs(limit=10)}, {"cancelled"}
        )

    async def test_an_error_on_the_very_first_submit(self) -> None:
        self.comfy.submit_errors[0] = ComfyError("ComfyUI is down")
        started = await self.start(self.manual(count_per_batch=3))
        await self.wait(started.batch_id)
        self.assertEqual(self.terminal_frames(), ["batch_error"])
        self.assertEqual(self.comfy.cancelled, [])
        self.assertEqual(self.runner._finished[started.batch_id].window._value, 4)

    async def test_binding_errors_and_any_other_exception_are_handled_the_same_way(
        self,
    ) -> None:
        for error in (
            BindingError("no MS_NEGATIVE node"),
            ValueError("odd"),
            OSError("disk full"),
        ):
            with self.subTest(error=type(error).__name__):
                self.events.clear()
                self.comfy.submits.clear()
                self.comfy.submit_errors = {0: error}
                started = await self.start(self.manual(count_per_batch=2))
                await self.wait(started.batch_id)
                self.assertEqual(self.terminal_frames(), ["batch_error"])
                self.assertIn(str(error), self.frames("batch_error")[0]["error"])

    async def test_an_exception_without_a_message_is_reported_by_name(self) -> None:
        self.comfy.submit_errors[0] = KeyError()
        started = await self.start(self.manual(count_per_batch=1))
        await self.wait(started.batch_id)
        self.assertEqual(self.frames("batch_error")[0]["error"], "KeyError")

    async def test_jobs_that_already_finished_are_left_alone(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        self.comfy.submit_errors[2] = ComfyError("nope")
        started = await self.start(self.manual(count_per_batch=4))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        self.job_update(
            self.comfy.job_ids[0], "done"
        )  # frees a slot -> third submit fails
        await self.wait(started.batch_id)
        self.assertEqual(self.comfy.cancelled, [self.comfy.job_ids[1]])
        rows = {r["id"]: r["state"] for r in self.db.list_generation_jobs(limit=10)}
        self.assertEqual(
            rows[self.comfy.job_ids[0]], "queued"
        )  # untouched by the abort
        self.assertEqual(rows[self.comfy.job_ids[1]], "cancelled")

    async def test_a_submit_error_stops_a_writer_that_is_still_prompting(self) -> None:
        assert self.vlm is not None
        self.runner.unload_vlm_during_generation = False
        self.roomy()
        self.vlm.gates.extend(
            [None, asyncio.Event()]
        )  # step 2's prompt never comes back
        self.comfy.submit_errors[0] = ComfyError("nope")
        self.comfy.gate = asyncio.Event()  # the first submit waits for the writer
        started = await self.start(self.random_mode(batch_size=3, count_per_batch=1))
        await self.until(lambda: len(self.vlm.calls) == 2, "the writer to be mid-call")
        self.comfy.gate.set()  # now the submit fails
        await self.wait(started.batch_id)
        self.assertEqual(self.terminal_frames(), ["batch_error"])
        self.assertEqual(self.vlm.cancelled_calls, 1)  # the writer was stopped mid-call
        leftover = [
            t.get_name()
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and t.get_name().startswith("t2i-")
        ]
        self.assertEqual(leftover, [])

    async def test_an_error_frees_the_random_slot(self) -> None:
        self.comfy.submit_errors[0] = ComfyError("nope")
        started = await self.start(self.random_mode(batch_size=1, count_per_batch=1))
        await self.wait(started.batch_id)
        self.comfy.submit_errors.clear()
        again = await self.start(self.random_mode(batch_size=1, count_per_batch=1))
        self.assertNotEqual(again.batch_id, started.batch_id)

    async def test_cancel_after_an_error_reports_it_is_already_over(self) -> None:
        self.comfy.submit_errors[0] = ComfyError("nope")
        started = await self.start(self.manual(count_per_batch=1))
        await self.wait(started.batch_id)
        self.assertFalse(await self.runner.cancel_batch(started.batch_id))
        self.assertEqual(self.terminal_frames(), ["batch_error"])


class TestWaitBatchAndClose(BatchCase):
    async def test_an_unknown_batch_is_not_found(self) -> None:
        with self.assertRaises(T2iNotFoundError):
            await self.runner.wait_batch("no-such-batch")

    async def test_wait_returns_when_the_last_job_is_submitted_not_finished(
        self,
    ) -> None:
        started = await self.start(self.manual(count_per_batch=3))
        await asyncio.wait_for(self.runner.wait_batch(started.batch_id), 5)
        self.assertEqual(len(self.comfy.submits), 3)
        # The jobs are still queued; the batch is still active.
        self.assertEqual(
            [b["batch_id"] for b in self.runner.active_batches()], [started.batch_id]
        )

    async def test_wait_blocks_while_the_window_holds_the_run(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.manual(count_per_batch=2))
        waiter = asyncio.ensure_future(self.runner.wait_batch(started.batch_id))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        await self.quiet()
        self.assertFalse(waiter.done())
        self.job_update(self.comfy.job_ids[0], "done")
        await asyncio.wait_for(waiter, 5)
        self.assertEqual(len(self.comfy.submits), 2)

    async def test_a_cancelled_waiter_does_not_cancel_the_run(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.manual(count_per_batch=2))
        waiter = asyncio.ensure_future(self.runner.wait_batch(started.batch_id))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.job_update(self.comfy.job_ids[0], "done")
        await self.until(lambda: len(self.comfy.submits) == 2, "the run to carry on")

    async def test_wait_on_a_finished_batch_returns_at_once(self) -> None:
        started = await self.start(self.manual(count_per_batch=1))
        await self.runner.cancel_batch(started.batch_id)
        await asyncio.wait_for(self.runner.wait_batch(started.batch_id), 1)

    async def test_wait_does_not_raise_when_the_run_failed(self) -> None:
        self.comfy.submit_errors[0] = ComfyError("nope")
        started = await self.start(self.manual(count_per_batch=1))
        await self.wait(started.batch_id)  # returns; the error is a frame

    async def test_aclose_stops_the_runs_without_cancelling_their_jobs(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        await self.runner.aclose()
        self.assertTrue(self.runner._batches[started.batch_id].task.done())
        self.assertEqual(self.comfy.cancelled, [])  # the jobs are ComfyClient's to keep
        self.assertEqual(self.terminal_frames(), [])
        await self.runner.aclose()  # idempotent


# ---- job events: ingest and accounting -------------------------------------


class AccountingCase(BatchCase):
    """Jobs that live out their whole life. The tests play ComfyClient's part:
    they announce outputs, then the end of the job, as it does."""

    def image(self, job_id: int, suffix: str = ".png") -> str:
        return str(self.root / "images" / f"job{job_id}{suffix}")

    def stamp(self, job_id: int, started: str, finished: Optional[str]) -> None:
        self.db.update_generation_job(job_id, started_at=started, finished_at=finished)

    async def ingested(self) -> None:
        """Wait for every ingest the runner has started."""
        tasks = list(self.runner._ingest_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    def outputs(self, job_id: int, files: Optional[List[str]] = None) -> None:
        paths = [self.image(job_id)] if files is None else files
        self.comfy.emit("job_outputs", {"job_id": job_id, "files": paths})

    def end(self, job_id: int, state: str, error: Optional[str] = None) -> None:
        """The row goes terminal first, then the update is announced."""
        fields: Dict[str, Any] = {
            "state": state,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if error is not None:
            fields["error"] = error
        self.db.update_generation_job(job_id, **fields)
        self.job_update(job_id, state, error)

    async def job_done(self, job_id: int, files: Optional[List[str]] = None) -> None:
        """A job ending as ComfyClient ends one: outputs first, then done."""
        self.outputs(job_id, files)
        self.end(job_id, "done")
        await self.ingested()

    def job_failed(self, job_id: int, error: str = "node exploded") -> None:
        self.end(job_id, "failed", error)

    def job_cancelled(self, job_id: int) -> None:
        self.end(job_id, "cancelled")

    async def manual_batch(self, count: int = 3, **over: Any) -> str:
        """A Manual batch, fully submitted, its window never in the way."""
        self.roomy()
        return await self.run_to_end(self.manual(count_per_batch=count, **over))

    def after_step(self) -> List[str]:
        """The frame names after the batch's own opening frames."""
        return self.names()[self.names().index("batch_step") + 1 :]


class TestIngest(AccountingCase):
    async def test_a_finished_job_becomes_a_row_with_its_facts(self) -> None:
        batch_id = await self.manual_batch(
            preset_id=self.with_negative, negative="blurry"
        )
        first, second, _ = self.comfy.job_ids
        self.stamp(
            first, "2026-09-29T10:00:00+00:00", "2026-09-29T10:00:41.500000+00:00"
        )
        self.db.update_generation_job(first, comfy_prompt_id="prompt-abc")
        # The image is recorded before the row is marked done (which would
        # restamp finished_at): job_outputs comes first, as in ComfyClient.
        self.outputs(first)
        await self.ingested()
        self.end(first, "done")
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(row["file_path"], self.image(first))
        self.assertEqual(row["batch_id"], batch_id)
        self.assertEqual(row["model"], "krea2")
        self.assertEqual(row["preset_id"], self.with_negative)
        self.assertEqual(row["caption"], "a red kite")
        self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
        self.assertEqual(row["negative_used"], "blurry")
        self.assertEqual((row["seed"], row["prompt_seed"]), (100, 100))
        self.assertEqual((row["width"], row["height"]), (1232, 816))
        self.assertEqual(row["megapixels"], 1.0)
        self.assertEqual(row["aspect_ratio"], "3:2")
        self.assertEqual(row["loras"], [])
        self.assertEqual(row["render_s"], 41.5)
        self.assertEqual(row["comfy_prompt_id"], "prompt-abc")
        # The next image has its own seed; the step's prompt seed is shared.
        await self.job_done(second)
        again = self.db.get_t2i_image(2)
        assert again is not None
        self.assertEqual((again["seed"], again["prompt_seed"]), (101, 100))

    async def test_a_manual_batch_without_a_caption_stores_none(self) -> None:
        await self.manual_batch(count=1, caption=None)
        await self.job_done(self.comfy.job_ids[0])
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertIsNone(row["caption"])
        self.assertIsNone(row["form_state"]["caption"])

    async def test_the_form_state_is_the_dialog_as_submitted(self) -> None:
        await self.manual_batch(
            preset_id=self.with_stack,
            negative="blurry",  # the workflow has no MS_NEGATIVE: not sent, still typed
            loras=[{"name": "kite.safetensors", "strength": "0.5"}],
        )
        first, second, _ = self.comfy.job_ids
        await self.job_done(first)
        await self.job_done(second)
        one, two = self.db.get_t2i_image(1), self.db.get_t2i_image(2)
        assert one is not None and two is not None
        self.assertEqual(
            one["form_state"],
            {
                "mode": "manual",
                "filter": None,
                "caption": "a red kite",
                "model": "krea2",
                "preset_id": self.with_stack,
                "megapixels": 1.0,
                "aspect_ratio": "3:2",
                "seed": 100,
                "prompt": "A red kite over a gray sea.",
                "negative": "blurry",
                "loras": [{"name": "kite.safetensors", "strength": 0.5}],
            },
        )
        self.assertEqual(two["form_state"]["seed"], 101)  # that image's own seed
        self.assertIsNone(one["negative_used"])  # what was rendered: no negative
        self.assertEqual(one["loras"], [{"name": "kite.safetensors", "strength": 0.5}])

    async def test_random_rows_carry_the_raw_caption_and_the_step_values(self) -> None:
        self.roomy()
        flt = {"aspect_ratios": ["3:2"]}
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2, filter=flt)
        )
        for job_id in self.comfy.job_ids:
            await self.job_done(job_id)
        rows = [self.db.get_t2i_image(n) for n in (1, 2, 3, 4)]
        for row in rows:
            assert row is not None
            self.assertEqual(row["caption"], CSV_ROWS[0][0])  # tokens and all
            self.assertEqual(row["prompt_used"], "A calm beach scene at dawn.")
            self.assertEqual(row["aspect_ratio"], "3:2")
            self.assertEqual(row["form_state"]["mode"], "random")
            self.assertEqual(row["form_state"]["filter"], flt)
            self.assertEqual(row["form_state"]["caption"], CSV_ROWS[0][0])
            self.assertEqual(row["form_state"]["prompt"], "A calm beach scene at dawn.")
            self.assertEqual(row["form_state"]["seed"], row["seed"])
        assert all(r is not None for r in rows)
        self.assertEqual([r["seed"] for r in rows if r], [10, 11, 12, 13])
        self.assertEqual([r["prompt_seed"] for r in rows if r], [10, 10, 12, 12])

    async def test_a_supplied_first_step_is_remembered_as_the_dialog_had_it(
        self,
    ) -> None:
        # Step 1 of a Random run, supplied by the request with no caption: the
        # image remembers a Random dialog with its filter, its own prompt and
        # negative, and no caption -- the way a Manual one does.
        self.roomy()
        flt = {"aspect_ratios": ["3:2"]}
        await self.run_to_end(
            self.random_mode(
                model="sd",
                preset_id=self.with_negative,
                seed=10,
                batch_size=1,
                count_per_batch=1,
                filter=flt,
                prompt="A red kite over a gray sea.",
                negative="blurry",
                aspect_ratio="2:3",
            )
        )
        await self.job_done(self.comfy.job_ids[0])
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(
            row["form_state"],
            {
                "mode": "random",
                "filter": flt,
                "caption": None,
                "model": "sd",
                "preset_id": self.with_negative,
                "megapixels": 1.0,
                "aspect_ratio": "2:3",
                "seed": 10,
                "prompt": "A red kite over a gray sea.",
                "negative": "blurry",
                "loras": [],
            },
        )
        self.assertIsNone(row["caption"])
        self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
        self.assertEqual(row["negative_used"], "blurry")
        self.assertEqual((row["seed"], row["prompt_seed"]), (10, 10))

    async def test_random_sdxl_keeps_the_negative_it_rendered_with(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                model="sd",
                preset_id=self.with_negative,
                batch_size=1,
                count_per_batch=1,
                filter={"aspect_ratios": ["3:2"]},
            )
        )
        await self.job_done(self.comfy.job_ids[0])
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(row["negative_used"], "blurry, watermark")
        self.assertEqual(row["form_state"]["negative"], "blurry, watermark")

    async def test_render_time_when_the_finish_is_not_stamped_yet(self) -> None:
        # job_outputs fires before the job is marked done.
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        started = datetime.now(timezone.utc).timestamp() - 12
        self.stamp(job, datetime.fromtimestamp(started, timezone.utc).isoformat(), None)
        self.outputs(job)
        await self.ingested()
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertGreaterEqual(row["render_s"], 12.0)
        self.assertLess(row["render_s"], 20.0)

    async def test_no_start_stamp_means_no_render_time(self) -> None:
        await self.manual_batch(count=1)
        await self.job_done(self.comfy.job_ids[0])
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertIsNone(row["render_s"])

    async def test_each_ingest_announces_its_files(self) -> None:
        batch_id = await self.manual_batch()
        first, second, _ = self.comfy.job_ids
        await self.job_done(first)
        await self.job_done(second, [self.image(second), self.image(second, "_b.png")])
        self.assertEqual(
            self.frames("t2i_images_changed"),
            [
                {"batch_id": batch_id, "files": [self.image(first)]},
                {
                    "batch_id": batch_id,
                    "files": [self.image(second), self.image(second, "_b.png")],
                },
            ],
        )

    async def test_two_files_of_one_job_are_two_rows_and_one_job(self) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        await self.job_done(job, [self.image(job), self.image(job, "_b.png")])
        self.assertIsNotNone(self.db.get_t2i_image(2))
        (progress,) = self.frames("batch_progress")[-1:]
        self.assertEqual(progress["images_done"], 2)
        self.assertEqual(self.terminal_frames(), ["batch_complete"])  # still one job

    async def test_a_done_job_with_no_files_counts_as_failed(self) -> None:
        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        await self.job_done(first, [])
        (progress,) = self.frames("batch_progress")[-1:]
        self.assertEqual(progress["images_failed"], 1)
        self.assertEqual(progress["last_error"], "the job produced no image")
        self.assertEqual(self.frames("t2i_images_changed"), [])
        await self.job_done(second)
        self.assertEqual(self.frames("batch_complete")[0]["images_failed"], 1)

    async def test_a_path_that_already_has_a_row_is_skipped_not_counted(self) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        self.db.create_t2i_image(file_path=self.image(job), prompt_used="the older row")
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING") as logs:
            await self.job_done(job)
        self.assertTrue(any("already has a row" in line for line in logs.output))
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(row["prompt_used"], "the older row")  # left alone
        self.assertIsNone(self.db.get_t2i_image(2))
        self.assertEqual(self.frames("t2i_images_changed"), [])
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_failed"]), (0, 1))
        self.assertEqual(frame["last_error"], "the job's image could not be recorded")

    async def test_an_unusable_form_state_is_stored_as_null_and_the_image_is_kept(
        self,
    ) -> None:
        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        self.runner._job_meta[first]["aspect_ratio"] = "banana"  # PATCH would refuse it
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING") as logs:
            await self.job_done(first)
        self.assertTrue(any("form state" in line for line in logs.output))
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertIsNone(row["form_state"])
        self.assertEqual(
            row["prompt_used"], "A red kite over a gray sea."
        )  # facts intact
        self.assertEqual(
            self.frames("t2i_images_changed")[0]["files"], [self.image(first)]
        )
        self.runner._job_meta[second]["model"] = ""  # another refusal
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING"):
            await self.job_done(second)
        again = self.db.get_t2i_image(2)
        assert again is not None
        self.assertIsNone(again["form_state"])
        self.assertEqual(self.frames("batch_complete")[0]["images_done"], 2)

    async def test_a_snapshot_that_is_missing_fields_costs_only_the_form(self) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        self.runner._job_meta[job] = {}
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING"):
            await self.job_done(job)
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertIsNone(row["form_state"])
        self.assertEqual(row["seed"], 100)

    async def test_a_database_error_reading_the_job_fails_that_job_not_the_batch(
        self,
    ) -> None:
        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        with mock.patch.object(
            self.db, "get_generation_job", side_effect=RuntimeError("db is locked")
        ):
            with self.assertLogs("metascan.core.t2i_runner", level="WARNING") as logs:
                await self.job_done(first)
        self.assertTrue(any("db is locked" in line for line in logs.output))
        (progress,) = self.frames("batch_progress")[-1:]
        self.assertEqual(progress["images_failed"], 1)
        self.assertNotIn(first, self.runner._job_meta)
        await self.job_done(second)
        self.assertEqual(self.terminal_frames(), ["batch_complete"])


class TestAccounting(AccountingCase):
    async def test_the_frames_of_a_batch_that_ends_three_ways(self) -> None:
        batch_id = await self.manual_batch()
        first, second, third = self.comfy.job_ids
        await self.job_done(first)
        self.job_failed(second, "node exploded")
        self.job_cancelled(third)
        self.assertEqual(
            self.after_step(),
            [
                "t2i_images_changed",
                "batch_progress",  # one image done
                "batch_progress",  # one failed
                "batch_complete",  # the cancelled one changes no counter
            ],
        )
        progress = self.frames("batch_progress")
        self.assertEqual(
            progress[1:],
            [
                {
                    "batch_id": batch_id,
                    "phase": "rendering",
                    "images_done": 1,
                    "images_failed": 0,
                    "images_total": 3,
                    "seed": 101,  # the image rendering now is the second
                    "next_seed": 103,
                },
                {
                    "batch_id": batch_id,
                    "phase": "rendering",
                    "images_done": 1,
                    "images_failed": 1,
                    "images_total": 3,
                    "seed": 102,
                    "next_seed": 103,
                    "last_error": "node exploded",
                },
            ],
        )
        self.assertEqual(
            self.frames("batch_complete"),
            [
                {
                    "batch_id": batch_id,
                    "images_done": 1,
                    "images_failed": 1,
                    "images_total": 3,
                    "images_cancelled": 1,
                    "next_seed": 103,
                    "last_error": "node exploded",
                }
            ],
        )
        self.assertEqual(self.runner.active_batches(), [])
        # Review focus 4: the counters sum to the total.
        (final,) = self.frames("batch_complete")
        self.assertEqual(
            final["images_done"] + final["images_failed"] + final["images_cancelled"],
            final["images_total"],
        )

    async def test_the_counters_are_listed_while_the_batch_runs(self) -> None:
        batch_id = await self.manual_batch(count=4)
        first, second, _, _ = self.comfy.job_ids
        await self.job_done(first)
        self.job_failed(second)
        (row,) = self.runner.active_batches()
        self.assertEqual(row["batch_id"], batch_id)
        self.assertEqual((row["images_done"], row["images_failed"]), (1, 1))
        self.assertEqual(row["images_total"], 4)
        self.assertEqual(row["state"], "rendering")
        self.assertEqual(row["next_seed"], 104)

    async def test_a_failed_job_frees_its_slot_and_the_run_carries_on(self) -> None:
        # Review focus 4: a failed job leaves nothing behind.
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        self.job_failed(self.comfy.job_ids[0], "out of memory")
        await self.until(lambda: len(self.comfy.submits) == 2, "the second submit")
        await self.job_done(self.comfy.job_ids[1])
        await self.until(lambda: len(self.comfy.submits) == 3, "the third submit")
        await self.wait(started.batch_id)
        await self.job_done(self.comfy.job_ids[2])
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_failed"]), (2, 1))
        batch = self.runner._finished[started.batch_id]
        self.assertEqual(batch.window._value, 1)
        self.assertEqual(batch.pending, set())
        self.assertEqual(batch.unresolved, set())
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._job_meta, {})

    async def test_a_finished_batch_leaves_no_bookkeeping(self) -> None:
        batch_id = await self.manual_batch(count=2)
        for job_id in self.comfy.job_ids:
            await self.job_done(job_id)
        batch = self.runner._finished[batch_id]
        self.assertEqual(batch.finished, "complete")
        self.assertEqual(
            (batch.pending, batch.unresolved, batch.ingesting), (set(), set(), set())
        )
        self.assertEqual(batch.window._value, 50)
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._job_meta, {})
        self.assertEqual(self.runner._ingest_tasks, set())
        self.assertIsNone(batch.picker)

    async def test_the_terminal_frame_fires_exactly_once(self) -> None:
        batch_id = await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        self.outputs(first)
        self.end(first, "done")
        self.job_failed(second)  # back to back, nothing awaited between
        await self.ingested()
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        # Whatever ComfyClient says afterwards changes nothing.
        seen = len(self.events)
        self.job_update(first, "done")
        self.job_update(second, "failed", "again")
        self.job_update(second, "cancelled")
        self.outputs(first)
        await self.ingested()
        self.assertEqual(len(self.events), seen)
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        self.assertNotIn(
            batch_id, [b["batch_id"] for b in self.runner.active_batches()]
        )

    async def test_a_repeated_outputs_event_records_nothing_twice(self) -> None:
        await self.manual_batch(count=2)
        first = self.comfy.job_ids[0]
        await self.job_done(first)
        with self.assertLogs("metascan.core.t2i_runner", level="WARNING"):
            self.outputs(first)
            await self.ingested()
        self.assertIsNone(self.db.get_t2i_image(2))
        (progress,) = self.frames("batch_progress")[-1:]
        self.assertEqual(progress["images_done"], 1)  # not counted again
        self.assertEqual(progress["images_failed"], 0)  # nor as a failure
        self.assertEqual(len(self.frames("t2i_images_changed")), 1)
        self.assertEqual(self.terminal_frames(), [])  # one job is still out
        (row,) = self.runner.active_batches()
        self.assertEqual((row["images_done"], row["images_failed"]), (1, 0))

    async def test_the_ingest_may_finish_before_or_after_the_done_update(self) -> None:
        release = threading.Event()
        original = self.db.create_t2i_image

        def slow(**facts: Any) -> int:
            release.wait(5)
            return original(**facts)

        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        # Update first: the ingest is still writing when "done" arrives.
        with mock.patch.object(self.db, "create_t2i_image", side_effect=slow):
            self.outputs(first)
            self.end(first, "done")
            await self.quiet()
            self.assertEqual(self.frames("batch_progress")[-1]["images_done"], 0)
            self.assertEqual(len(self.runner.active_batches()), 1)
            release.set()
            await self.ingested()
        self.assertEqual(self.frames("batch_progress")[-1]["images_done"], 1)
        # Ingest first: the image is recorded before the update is announced,
        # and that is what ends the batch; the update then changes nothing.
        self.outputs(second)
        await self.ingested()
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        finished = next(iter(self.runner._finished.values()))
        self.assertEqual(finished.window._value, 50)  # the slot came back at once
        self.assertEqual(self.runner._job_batch, {})  # ...and the link went
        self.end(second, "done")
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        batch = next(iter(self.runner._finished.values()))
        self.assertEqual(batch.window._value, 50)  # the last slot came back too

    async def test_a_job_that_is_done_without_outputs_is_failed_not_awaited(
        self,
    ) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        self.end(job, "done")  # no job_outputs ever came
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_failed"]), (0, 1))
        self.assertEqual(frame["last_error"], "the job finished without an image")

    async def test_completion_frees_the_random_slot(self) -> None:
        self.roomy()
        started = await self.start(self.random_mode(batch_size=1, count_per_batch=1))
        await self.wait(started.batch_id)
        await self.job_done(self.comfy.job_ids[0])
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        again = await self.start(self.random_mode(batch_size=1, count_per_batch=1))
        self.assertNotEqual(again.batch_id, started.batch_id)

    async def test_the_window_is_freed_by_the_update_not_by_the_ingest(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        await self.start(self.manual(count_per_batch=2))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        first = self.comfy.job_ids[0]
        self.outputs(first)
        await self.ingested()  # the image is recorded...
        await self.quiet()
        self.assertEqual(len(self.comfy.submits), 1)  # ...but the job is not over yet
        self.end(first, "done")
        await self.until(lambda: len(self.comfy.submits) == 2, "the second submit")

    async def test_a_cancelled_batch_ignores_what_comes_after(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(self.manual(count_per_batch=4))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")
        first = self.comfy.job_ids[0]
        await self.runner.cancel_batch(started.batch_id)
        seen = len(self.events)
        self.job_update(first, "done")
        self.job_failed(self.comfy.job_ids[1])
        await self.quiet()
        self.assertEqual(len(self.events), seen)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])

    async def test_images_that_land_after_the_cancel_are_kept_but_not_counted(
        self,
    ) -> None:
        self.roomy()
        started = await self.start(self.manual(count_per_batch=2))
        await self.wait(started.batch_id)
        first, second = self.comfy.job_ids
        await self.runner.cancel_batch(started.batch_id)
        for job_id in (first, second):  # both finished just as the cancel went out
            self.outputs(job_id)
        await self.ingested()
        for image_id, job_id in ((1, first), (2, second)):
            row = self.db.get_t2i_image(image_id)
            assert row is not None
            self.assertEqual(row["file_path"], self.image(job_id))
            self.assertEqual(row["batch_id"], started.batch_id)
            self.assertIsNone(row["form_state"])  # the snapshot went with the cancel
        self.assertEqual(len(self.frames("t2i_images_changed")), 2)
        # Every job of the batch is now accounted for -- and still the batch
        # ended once, as cancelled, with the counters it had at the time.
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertEqual(self.frames("batch_cancelled")[0]["images_done"], 0)

    async def test_an_image_that_lands_mid_cancel_cannot_end_the_batch_twice(
        self,
    ) -> None:
        self.roomy()
        started = await self.start(self.manual(count_per_batch=2))
        await self.wait(started.batch_id)
        first, second = self.comfy.job_ids
        await self.job_done(first)  # job 0 is over and accounted for
        self.comfy.cancel_gate = asyncio.Event()
        cancel = asyncio.ensure_future(self.runner.cancel_batch(started.batch_id))
        await self.quiet()
        self.assertFalse(cancel.done())  # waiting on ComfyUI for job 1
        self.outputs(second)  # the last image lands meanwhile
        await self.ingested()
        self.comfy.cancel_gate.set()
        self.assertTrue(await cancel)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertIsNotNone(self.db.get_t2i_image(2))  # kept...
        self.assertEqual(
            self.frames("batch_cancelled")[0]["images_done"], 1
        )  # ...uncounted

    async def test_a_batch_that_stopped_early_completes_at_its_shorter_total(
        self,
    ) -> None:
        self.roomy()
        started = await self.start(
            self.manual(seed=1, seed_policy="decrement", count_per_batch=5)
        )
        await self.wait(started.batch_id)
        self.assertEqual(len(self.comfy.job_ids), 2)
        for job_id in self.comfy.job_ids:
            await self.job_done(job_id)
        (frame,) = self.frames("batch_complete")
        self.assertEqual(frame["images_total"], 2)
        self.assertEqual(frame["images_done"], 2)
        self.assertIsNone(frame["next_seed"])  # the range ran out with the run

    async def test_a_random_batch_completes_across_its_steps(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2)
        )
        await self.wait(started.batch_id)
        for job_id in self.comfy.job_ids[:-1]:
            await self.job_done(job_id)
        self.assertEqual(self.terminal_frames(), [])
        await self.job_done(self.comfy.job_ids[-1])
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_total"]), (4, 4))
        self.assertEqual(frame["next_seed"], 14)

    async def test_progress_frames_name_the_last_failure(self) -> None:
        await self.manual_batch(count=3)
        first, second, _ = self.comfy.job_ids
        self.job_failed(first, "first failure")
        self.job_failed(second, "second failure")
        self.assertEqual(
            [f.get("last_error") for f in self.frames("batch_progress")],
            [None, "first failure", "second failure"],
        )

    async def test_a_failure_without_a_message_still_says_something(self) -> None:
        await self.manual_batch(count=1)
        self.job_update(self.comfy.job_ids[0], "failed", None)
        self.assertEqual(
            self.frames("batch_complete")[0]["last_error"], "the job failed"
        )


class TestWhatTheDialogShows(AccountingCase):
    """While a batch renders the dialog shows ONE image's worth of state: the
    seed of the image being rendered, and the batch (caption, prompt, aspect
    ratio) that image belongs to. Both move on as images are accounted for.
    Writing a later batch's prompt never takes the boxes away from the batch
    that is rendering, whatever order the GPU work runs in."""

    def seeds_after_images(self) -> List[Optional[int]]:
        """The seed in every progress frame sent after an image was accounted for."""
        return [
            f["seed"] for f in self.frames("batch_progress") if f["images_done"] > 0
        ]

    def shown_steps(self) -> List[int]:
        return [s["step"] for s in self.frames("batch_step")]

    async def test_the_progress_frame_carries_the_seed_of_the_image_rendering_now(
        self,
    ) -> None:
        await self.manual_batch(count=3)  # 100, 101, 102, then 103 is next
        first, second, third = self.comfy.job_ids
        self.assertEqual([f["seed"] for f in self.frames("batch_progress")], [100])
        await self.job_done(first)
        await self.job_done(second)
        await self.job_done(third)
        self.assertEqual(
            [f["seed"] for f in self.frames("batch_progress")], [100, 101, 102, 103]
        )

    async def test_a_failed_image_moves_the_seed_on_too(self) -> None:
        await self.manual_batch(count=3)
        first, second, _ = self.comfy.job_ids
        self.job_failed(first)
        await self.job_done(second)
        self.assertEqual(
            [f["seed"] for f in self.frames("batch_progress")], [100, 101, 102]
        )

    async def test_decrement_counts_down_and_ends_on_the_next_unused_seed(
        self,
    ) -> None:
        await self.manual_batch(count=3, seed=50, seed_policy="decrement")
        for job_id in self.comfy.job_ids:
            await self.job_done(job_id)
        self.assertEqual(self.seeds_after_images(), [49, 48, 47])

    async def test_the_random_policy_follows_its_drawn_seeds_to_a_fresh_one(
        self,
    ) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                seed=7, seed_policy="random", batch_size=2, count_per_batch=2
            )
        )
        batch = self.runner._batches[batch_id]
        assert batch.next_seed is not None
        for job_id in list(self.comfy.job_ids):
            await self.job_done(job_id)
        self.assertEqual(self.seeds_after_images(), batch.seeds[1:] + [batch.next_seed])
        self.assertEqual(batch.seeds[0], 7)

    async def test_fixed_keeps_showing_its_seed(self) -> None:
        await self.manual_batch(count=1, seed=321, seed_policy="fixed")
        await self.job_done(self.comfy.job_ids[0])
        self.assertEqual({f["seed"] for f in self.frames("batch_progress")}, {321})

    async def test_the_listed_batch_shows_the_seed_rendering_now(self) -> None:
        await self.manual_batch(count=3)
        (row,) = self.runner.active_batches()
        self.assertEqual(row["seed"], 100)
        await self.job_done(self.comfy.job_ids[0])
        (row,) = self.runner.active_batches()
        self.assertEqual(row["seed"], 101)

    # -- the batch on show -----------------------------------------------------

    async def drive(self, total: int = 6) -> List[List[int]]:
        """Finish the jobs one by one; the batches on show after each."""
        seen: List[List[int]] = []
        for job_id in list(self.comfy.job_ids)[:total]:
            await self.job_done(job_id)
            seen.append(self.shown_steps())
        return seen

    async def test_with_the_prompts_written_first_batch_one_stays_on_show(
        self,
    ) -> None:
        # Unload on (the default): all three prompts are written before the
        # first image renders, and still only batch 1 is on show.
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        self.assertEqual(len(self.vlm.calls), 3)
        self.assertEqual(self.shown_steps(), [1])
        (row,) = self.runner.active_batches()
        self.assertEqual(row["step"]["step"], 1)
        self.assertEqual(row["step"]["seed"], 10)

    async def test_the_next_batch_comes_on_show_when_the_last_image_of_one_is_done(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        self.assertEqual(
            await self.drive(),
            [[1], [1, 2], [1, 2], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
        )

    async def test_each_batch_on_show_is_its_own_caption_and_seed(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        await self.drive()
        steps = self.frames("batch_step")
        self.assertEqual([s["seed"] for s in steps], [10, 12, 14])
        self.assertEqual(len({s["caption"] for s in steps}), 3)
        self.assertEqual([s["total_steps"] for s in steps], [3, 3, 3])

    async def test_the_listed_batch_follows_the_render_too(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        first, second, third, *_ = self.comfy.job_ids
        await self.job_done(first)
        await self.job_done(second)
        await self.job_done(third)
        (row,) = self.runner.active_batches()
        self.assertEqual((row["step"]["step"], row["seed"]), (2, 13))

    async def test_a_batch_on_show_arrives_before_the_progress_that_moved_it(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=1)
        )
        await self.job_done(self.comfy.job_ids[0])
        self.assertEqual(
            self.names()[-3:], ["t2i_images_changed", "batch_step", "batch_progress"]
        )

    async def test_with_the_prompts_overlapping_a_later_prompt_does_not_take_the_boxes(
        self,
    ) -> None:
        # Unload off: batch 1 is rendering while batches 2 and 3 are written.
        self.runner.unload_vlm_during_generation = False
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=3, count_per_batch=2)
        )
        self.assertEqual(self.shown_steps(), [1])
        self.assertEqual(
            await self.drive(),
            [[1], [1, 2], [1, 2], [1, 2, 3], [1, 2, 3], [1, 2, 3]],
        )

    async def test_the_last_batch_is_the_one_a_finished_run_leaves_on_show(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2)
        )
        await self.drive(4)
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        self.assertEqual(self.shown_steps(), [1, 2])
        self.assertEqual(self.frames("batch_step")[-1]["total_steps"], 2)

    async def test_a_failed_image_moves_the_batch_on_too(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=1)
        )
        first, _ = self.comfy.job_ids
        self.assertEqual(self.shown_steps(), [1])  # batch 2 is written, not on show
        self.job_failed(first)
        self.assertEqual(self.shown_steps(), [1, 2])

    async def test_a_manual_batch_shows_its_one_step_throughout(self) -> None:
        await self.manual_batch(count=3)
        for job_id in self.comfy.job_ids:
            await self.job_done(job_id)
        self.assertEqual(self.shown_steps(), [1])


class TestSnapshotLifecycle(AccountingCase):
    async def test_the_ingest_pops_a_done_jobs_snapshot(self) -> None:
        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        self.outputs(first)  # the update has not arrived yet
        await self.ingested()
        self.assertNotIn(first, self.runner._job_meta)  # the ingest owns it
        self.assertIn(first, self.runner._job_batch)  # the update still finds its batch
        self.assertIn(second, self.runner._job_meta)
        self.end(first, "done")
        self.assertNotIn(first, self.runner._job_batch)

    async def test_a_failed_job_leaves_no_entry(self) -> None:
        await self.manual_batch(count=2)
        first, second = self.comfy.job_ids
        self.job_failed(first)
        self.assertNotIn(first, self.runner._job_meta)
        self.assertNotIn(first, self.runner._job_batch)
        self.assertIn(second, self.runner._job_meta)  # the others are untouched

    async def test_a_cancelled_job_leaves_no_entry(self) -> None:
        await self.manual_batch(count=2)
        first, _ = self.comfy.job_ids
        self.job_cancelled(first)
        self.assertNotIn(first, self.runner._job_meta)
        self.assertNotIn(first, self.runner._job_batch)

    async def test_the_snapshot_goes_even_when_the_ingest_fails(self) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        with mock.patch.object(
            self.db, "get_generation_job", side_effect=RuntimeError("db is locked")
        ):
            with self.assertLogs("metascan.core.t2i_runner", level="WARNING"):
                self.outputs(job)
                await self.ingested()
        self.assertNotIn(job, self.runner._job_meta)

    async def test_every_job_of_a_finished_batch_is_forgotten(self) -> None:
        await self.manual_batch(count=3)
        first, second, third = self.comfy.job_ids
        await self.job_done(first)
        self.job_failed(second)
        self.job_cancelled(third)
        self.assertEqual(self.runner._job_meta, {})
        self.assertEqual(self.runner._job_batch, {})


class TestForeignAndRestartedJobs(AccountingCase):
    def orphan_job(self, batch_id: Optional[str], **over: Any) -> int:
        """A generation_jobs row the runner has no memory of."""
        params = GenerationParams(
            positive="A kite.", seed=5, width=640, height=480, batch_size=1, **over
        )
        return self.db.create_generation_job(
            self.plain,
            params.to_json(),
            None,
            None,
            None,
            None,
            None,
            "t2i_x",
            batch_id,
        )

    async def test_an_image_of_a_batch_from_before_a_restart_is_still_ingested(
        self,
    ) -> None:
        job = self.orphan_job("old-batch")
        self.db.update_generation_job(job, comfy_prompt_id="prompt-9")
        self.runner.handle_job_event(
            "job_outputs", {"job_id": job, "files": [self.image(job)]}
        )
        await self.ingested()
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(row["batch_id"], "old-batch")
        self.assertEqual(row["file_path"], self.image(job))
        self.assertEqual(row["prompt_used"], "A kite.")
        self.assertEqual((row["seed"], row["width"], row["height"]), (5, 640, 480))
        self.assertEqual(row["comfy_prompt_id"], "prompt-9")
        self.assertIsNone(row["negative_used"])
        self.assertEqual(row["loras"], [])
        for unknown in (
            "model",
            "caption",
            "prompt_seed",
            "megapixels",
            "aspect_ratio",
        ):
            self.assertIsNone(row[unknown], unknown)
        self.assertIsNone(row["form_state"])  # nothing to seed it from
        self.assertEqual(
            self.events,
            [
                (
                    "t2i_images_changed",
                    {"batch_id": "old-batch", "files": [self.image(job)]},
                )
            ],
        )

    async def test_an_unknown_batch_has_no_counters_and_no_terminal_frame(self) -> None:
        job = self.orphan_job("old-batch")
        self.runner.handle_job_event(
            "job_outputs", {"job_id": job, "files": [self.image(job)]}
        )
        self.runner.handle_job_event("job_update", {"job_id": job, "state": "done"})
        await self.ingested()
        self.assertEqual(self.terminal_frames(), [])
        self.assertEqual(self.frames("batch_progress"), [])
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_live_batch_whose_snapshot_is_gone_is_still_accounted_for(
        self,
    ) -> None:
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        del self.runner._job_meta[job]  # evicted by the backstop, say
        await self.job_done(job)
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertIsNone(row["form_state"])
        self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
        self.assertEqual(self.frames("batch_complete")[0]["images_done"], 1)

    async def test_an_unreadable_params_blob_costs_only_the_facts(self) -> None:
        job = self.db.create_generation_job(
            self.plain, "not json", None, None, None, None, None, None, "old-batch"
        )
        self.runner.handle_job_event(
            "job_outputs", {"job_id": job, "files": [self.image(job)]}
        )
        await self.ingested()
        row = self.db.get_t2i_image(1)
        assert row is not None
        self.assertEqual(row["file_path"], self.image(job))
        self.assertIsNone(row["prompt_used"])
        self.assertIsNone(row["seed"])

    async def test_jobs_of_other_features_are_ignored(self) -> None:
        plain = self.db.create_generation_job(self.plain, "{}", None, None)
        i2v = self.db.create_generation_job(
            self.plain, "{}", None, None, None, None, "/lib/a.png", None
        )
        panel = self.db.create_generation_job(self.plain, "{}", 7)
        for job in (plain, i2v, panel, 987654):
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job, "files": [self.image(job)]}
            )
            self.runner.handle_job_event("job_update", {"job_id": job, "state": "done"})
        await self.ingested()
        self.assertIsNone(self.db.get_t2i_image(1))
        self.assertEqual(self.events, [])

    async def test_malformed_events_are_ignored_and_never_raise(self) -> None:
        for event, payload in (
            ("job_outputs", {}),
            ("job_outputs", {"job_id": "7", "files": []}),
            ("job_outputs", {"job_id": None}),
            ("job_update", {"job_id": [1], "state": "done"}),
            ("job_update", None),
            ("job_outputs", None),
            ("job_progress", {"job_id": 1}),
            ("something_else", {}),
        ):
            with self.subTest(event=event, payload=payload):
                self.runner.handle_job_event(event, payload)  # type: ignore[arg-type]
        await self.ingested()
        self.assertEqual(self.events, [])

    async def test_an_event_with_no_running_loop_is_dropped(self) -> None:
        # ComfyClient calls listeners from its loop, but a stray call from a
        # plain thread must not raise or leave a half-registered ingest.
        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        errors: List[BaseException] = []

        def from_a_thread() -> None:
            try:
                self.runner.handle_job_event(
                    "job_outputs", {"job_id": job, "files": [self.image(job)]}
                )
            except BaseException as exc:  # pragma: no cover - the failure case
                errors.append(exc)

        thread = threading.Thread(target=from_a_thread)
        thread.start()
        thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.runner._ingest_tasks, set())
        batch = next(iter(self.runner._batches.values()))
        self.assertEqual(batch.ingesting, set())


class TestClose(AccountingCase):
    async def test_aclose_drains_the_ingests_in_flight(self) -> None:
        release = threading.Event()
        original = self.db.create_t2i_image

        def slow(**facts: Any) -> int:
            release.wait(5)
            return original(**facts)

        await self.manual_batch(count=1)
        (job,) = self.comfy.job_ids
        with mock.patch.object(self.db, "create_t2i_image", side_effect=slow):
            self.outputs(job)
            await self.until(
                lambda: bool(self.runner._ingest_tasks), "an ingest to start"
            )
            closing = asyncio.ensure_future(self.runner.aclose())
            await self.quiet()
            self.assertFalse(closing.done())  # waiting for the ingest
            release.set()
            await asyncio.wait_for(closing, 5)
        self.assertIsNotNone(self.db.get_t2i_image(1))  # the image was not lost
        self.assertEqual(self.runner._ingest_tasks, set())

    async def test_aclose_stops_runs_and_drains_ingests_together(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 1}})
        started = await self.start(self.manual(count_per_batch=3))
        await self.until(lambda: len(self.comfy.submits) == 1, "the first submit")
        self.outputs(self.comfy.job_ids[0])
        await self.runner.aclose()
        self.assertTrue(self.runner._batches[started.batch_id].task.done())
        self.assertIsNotNone(self.db.get_t2i_image(1))
        self.assertEqual(self.comfy.cancelled, [])


class RealClientCase(BatchCase):
    """The runner over the REAL ComfyClient and the repo's in-process fake
    ComfyUI. Everything above plays ComfyClient's part from what the runner
    assumes about it (outputs are announced before the job is marked done,
    a cancel emits its own update, ...); this case shows the real one does it."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.server = FakeComfyServer()
        await self.server.start()
        self.server.images_per_job = 1  # a t2i job renders one image
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        # What ComfyClient announced, and how many jobs were unfinished at once.
        self.announced: List[Tuple[str, Any, Any]] = []
        self.unfinished = 0
        self.peak = 0
        await self.boot(in_flight=2)

    async def boot(self, in_flight: int) -> None:
        """A ComfyClient and a T2iRunner over this database: what a server
        start builds (the second time, what a restart builds)."""
        self.client = ComfyClient(
            base_url=self.server.base_url,
            output_root=self.out_root,
            db=self.db,
            scanner=Scanner(self.db),  # as in the lifespan: files reach the library
            in_flight=in_flight,
        )
        await self.client.start()
        self.runner = T2iRunner(
            db=self.db,
            comfy=self.client,
            get_vlm=lambda: self.vlm,
            captions=self.captions,
            library=self.library,
            output_root=self.out_root,
            get_config=lambda: self.cfg,
        )
        self.runner.on_event(self._record)
        self.client.on_job_event(self.runner.handle_job_event)
        self.client.on_job_event(self._watch)

    def _watch(self, event: str, payload: Dict[str, Any]) -> None:
        self.announced.append((event, payload.get("job_id"), payload.get("state")))
        if event == "job_update" and payload.get("state") == "queued":
            self.unfinished += 1
            self.peak = max(self.peak, self.unfinished)
        elif event == "job_update" and payload.get("state") in (
            "done",
            "failed",
            "cancelled",
        ):
            self.unfinished -= 1

    async def asyncTearDown(self) -> None:
        await self.client.shutdown()
        await super().asyncTearDown()
        await self.server.stop()

    def comfy_job_ids(self) -> List[int]:
        """The ids of the jobs in the database, in submit order."""
        return [j["id"] for j in self.db.list_generation_jobs(limit=50)]

    async def until_over(self) -> None:
        await self.until(lambda: bool(self.terminal_frames()), "the batch to end")


class TestWithTheRealComfyClient(RealClientCase):
    async def test_a_batch_runs_to_completion(self) -> None:
        started = await self.start(self.manual(count_per_batch=5))
        await self.until_over()
        (frame,) = self.frames("batch_complete")
        self.assertEqual(
            frame,
            {
                "batch_id": started.batch_id,
                "images_done": 5,
                "images_failed": 0,
                "images_total": 5,
                "images_cancelled": 0,
                "next_seed": 105,
            },
        )
        rows = self.db.list_t2i_images(limit=50)
        # (Two jobs that finish together are recorded in either order.)
        self.assertEqual(sorted(r["seed"] for r in rows), [100, 101, 102, 103, 104])
        for row in rows:
            path = Path(row["file_path"])
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent.parent, self.out_root / "t2i")
            self.assertRegex(path.name, r"^t2i_\d+\.png$")
            self.assertEqual(row["batch_id"], started.batch_id)
            self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
            self.assertEqual(row["form_state"]["seed"], row["seed"])
        self.assertEqual(len(self.frames("t2i_images_changed")), 5)
        self.assertLessEqual(self.peak, 2)  # the window held
        # Nothing is left behind.
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.runner._job_meta, {})
        self.assertEqual(self.runner._job_batch, {})
        self.assertEqual(self.runner._finished[started.batch_id].window._value, 2)

    async def test_comfyclient_announces_outputs_before_it_marks_a_job_done(
        self,
    ) -> None:
        # The order the ingest and the accounting are built on.
        await self.start(self.manual(count_per_batch=3))
        await self.until_over()
        jobs = {job for _, job, _ in self.announced if job is not None}
        self.assertEqual(len(jobs), 3)
        for job in jobs:
            events = [(e, s) for e, j, s in self.announced if j == job]
            self.assertLess(
                events.index(("job_outputs", None)),
                events.index(("job_update", "done")),
            )
            self.assertLess(
                events.index(("job_update", "queued")),
                events.index(("job_outputs", None)),
            )

    async def test_failed_jobs_are_counted_with_the_nodes_error(self) -> None:
        self.server.fail_with = "boom: node exploded"
        started = await self.start(self.manual(count_per_batch=3))
        await self.until_over()
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_failed"]), (0, 3))
        self.assertIn("boom: node exploded", frame["last_error"])
        self.assertEqual(self.db.list_t2i_images(limit=10), [])
        self.assertEqual(self.frames("t2i_images_changed"), [])
        self.assertEqual(self.runner._finished[started.batch_id].window._value, 2)

    async def test_cancelling_a_running_batch_interrupts_its_jobs(self) -> None:
        self.server.hold = asyncio.Event()  # the first prompt stays running
        started = await self.start(self.manual(count_per_batch=6))
        await self.until(
            lambda: len(self.server.submitted) >= 1, "a prompt to reach ComfyUI"
        )
        self.assertTrue(await self.runner.cancel_batch(started.batch_id))

        def states() -> set:
            return {j["state"] for j in self.db.list_generation_jobs(limit=50)}

        # ComfyClient.cancel does not wait for a job that is mid-dispatch: it
        # ends the row a moment later, when the dispatch notices.
        await self.until(
            lambda: states() == {"cancelled"}, "every job to end cancelled"
        )
        self.assertLessEqual(len(self.db.list_generation_jobs(limit=50)), 2)  # window
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        submitted = len(self.server.submitted)
        self.server.hold.set()
        await self.quiet(0.2)
        self.assertEqual(len(self.server.submitted), submitted)  # nothing more goes out
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        self.assertEqual(self.frames("t2i_images_changed"), [])

    async def test_a_random_batch_runs_end_to_end(self) -> None:
        started = await self.start(
            self.random_mode(seed=10, batch_size=3, count_per_batch=1)
        )
        await self.until_over()
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_total"]), (3, 3))
        self.assertEqual(frame["next_seed"], 13)
        rows = self.db.list_t2i_images(limit=10)
        self.assertEqual(sorted(r["seed"] for r in rows), [10, 11, 12])
        captions = {row[0] for row in CSV_ROWS}
        for row in rows:
            self.assertIn(row["caption"], captions)  # the raw row, tokens and all
            self.assertEqual(row["prompt_used"], "A calm beach scene at dawn.")
            self.assertEqual(row["prompt_seed"], row["seed"])  # one image per step
            self.assertEqual(row["form_state"]["mode"], "random")
            self.assertEqual(row["batch_id"], started.batch_id)
        # GPU order, for real: the prompts, then the unload, then the jobs.
        assert self.vlm is not None
        self.assertEqual(self.vlm.shutdowns, 1)
        timeline = self.timeline()
        self.assertLess(
            timeline.index(("vlm", "shutdown")),
            timeline.index(("event", "t2i_images_changed")),
        )

    async def test_jobs_queued_across_a_restart_still_ingest_without_a_form_state(
        self,
    ) -> None:
        self.server.hold = asyncio.Event()  # the running prompt does not finish yet
        await self.client.shutdown()
        await self.runner.aclose()
        await self.boot(in_flight=1)  # one job runs, the next waits in ComfyClient
        started = await self.start(self.manual(count_per_batch=2))
        await self.wait(started.batch_id)
        first, second = self.comfy_job_ids()
        await self.until(
            lambda: self.db.get_generation_job(first)["state"] == "running",
            "the first job to be running",
        )
        self.assertEqual(self.db.get_generation_job(second)["state"], "queued")

        # The server restarts: both the runner's memory and the client's go.
        await self.client.shutdown()
        await self.runner.aclose()
        self.events.clear()
        await self.boot(in_flight=1)
        self.server.hold.set()  # ComfyUI gets on with the work

        await self.until(lambda: self.frames("t2i_images_changed"), "the image")
        (frame,) = self.frames("t2i_images_changed")
        self.assertEqual(frame["batch_id"], started.batch_id)
        (row,) = self.db.list_t2i_images(limit=10)
        self.assertEqual(row["seed"], 101)  # the job that was still queued
        self.assertEqual(row["prompt_used"], "A red kite over a gray sea.")
        self.assertIsNone(row["form_state"])  # nothing remembers the dialog
        self.assertIsNone(row["model"])
        states = [j["state"] for j in self.db.list_generation_jobs(limit=10)]
        self.assertEqual(states, ["failed", "done"])  # the running one was lost
        # The batch left with the old process: no counters, no terminal frame.
        self.assertEqual(self.terminal_frames(), [])
        self.assertEqual(self.runner.active_batches(), [])

    async def test_a_job_with_two_outputs_is_one_job_and_two_images(self) -> None:
        self.server.images_per_job = 2
        await self.start(self.manual(count_per_batch=3))
        await self.until_over()
        (frame,) = self.frames("batch_complete")
        self.assertEqual((frame["images_done"], frame["images_total"]), (6, 3))
        self.assertEqual(len(self.db.list_t2i_images(limit=50)), 6)
        self.assertEqual(self.runner.active_batches(), [])


if __name__ == "__main__":
    unittest.main()
