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
import unittest
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from unittest import mock

from backend.config import get_t2i_config
from metascan.core.comfy_bindings import BindingError
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.t2i_captions import CaptionFilterError, CaptionStore
from metascan.core.t2i_characters import resolve_caption
from metascan.core.t2i_form import SEED_MAX, T2iFormError
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
    it runs dry ``default_reply`` answers. ``log`` is shared with the other
    fakes so a test can assert the order of calls across them."""

    def __init__(self, log: List[Tuple[Any, ...]]) -> None:
        self.model_id: Optional[str] = "qwen3vl-8b"
        self.log = log
        self.calls: List[Dict[str, Any]] = []
        self.ensure_calls: List[str] = []
        self.replies: Deque[Any] = deque()
        self.start_errors: Deque[BaseException] = deque()
        self.default_reply = REPLY_WITH_NEGATIVE

    async def ensure_started(self, model_id: str) -> None:
        self.log.append(("vlm", "ensure_started"))
        self.ensure_calls.append(model_id)
        if self.start_errors:
            raise self.start_errors.popleft()

    async def generate_text(self, **kwargs: Any) -> str:
        self.log.append(("vlm", "generate_text"))
        self.calls.append(kwargs)
        reply = self.replies.popleft() if self.replies else self.default_reply
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)


class FakeComfy:
    """Stands in for ComfyClient. Nothing is submitted in these tests: a
    batch that has been planned and registered never reaches ComfyUI."""

    def __init__(self, db: DatabaseManager, log: List[Tuple[Any, ...]]) -> None:
        self.db = db
        self.log = log


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


class ValidationCase(RunnerCase):
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


class TestPlanning(RunnerCase):
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

    async def test_the_random_policy_reports_no_next_seed(self) -> None:
        self.assertIsNone(
            await self.next_seed_of(
                self.manual(seed_policy="random", count_per_batch=3)
            )
        )

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

    async def test_the_same_character_seed_draws_the_same_cast(self) -> None:
        # What "same seed, same cast" means for two different captions.
        one = await self.runner.resolve(
            caption="__ALICE__ walks along a beach at dawn.", seed=55, model="krea2"
        )
        two = await self.runner.resolve(
            caption="__ALICE__ paints a mural on a tall wall.", seed=55, model="krea2"
        )
        self.assertEqual(one.characters["ALICE"], two.characters["ALICE"])
        other = await self.runner.resolve(
            caption="__ALICE__ paints a mural on a tall wall.", seed=56, model="krea2"
        )
        self.assertNotEqual(one.characters["ALICE"], other.characters["ALICE"])

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
        self.assertEqual(progress["next_seed"], 16)
        (frame,) = self.frames("batch_started")
        self.assertEqual(frame["mode"], "random")
        self.assertEqual(frame["total_steps"], 3)
        self.assertEqual(frame["batch_id"], started.batch_id)

    async def test_the_random_policy_progress_frame_has_a_null_next_seed(self) -> None:
        await self.runner.start_batch(self.manual(seed_policy="random"))
        (progress,) = self.frames("batch_progress")
        self.assertIn("next_seed", progress)
        self.assertIsNone(progress["next_seed"])

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
                "started_at",
                "state",
                "step",
                "total_steps",
            ],
        )
        self.assertEqual(row["batch_id"], started.batch_id)
        self.assertEqual(row["mode"], "manual")
        self.assertEqual(row["state"], "rendering")
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


if __name__ == "__main__":
    unittest.main()
