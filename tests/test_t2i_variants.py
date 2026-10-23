"""T2I prompt variants, taste-guided writing and draft/edit lineage.

Each caption of a batch expands to ``variants`` steps that share the
caption's seed list (images differ by prompt, not by seed); variant 0 is
written without an angle, variant k with angle k-1 of
``T2I_VARIANT_ANGLES``. Every image records its writer, variant group and
index, the writer's draft and whether the owner edited it.

All prompts and captions here are synthetic.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

from backend.config import get_t2i_config
from metascan.core.judgments import normalize_prompt
from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_captions import CaptionPicker
from metascan.core.t2i_runner import (
    BatchRequest,
    PromptDraft,
    PromptResult,
    T2iConflictError,
    T2iRequestError,
)
from metascan.core.taste_examples import variant_angles
from tests.test_t2i_api import T2iApiCase
from tests.test_t2i_runner import AccountingCase, RunnerCase


def _rows(db: Any) -> List[Dict[str, Any]]:
    with db.lock, db._get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT file_path, prompt_used, seed, writer_id, variant_group, "
                "variant_index, draft_prompt, edited FROM t2i_images ORDER BY id"
            )
        ]


class FakeTaste:
    """Stands in for TasteExamplesService.examples_for."""

    def __init__(
        self, examples: Optional[List[str]] = None, warning: Optional[str] = None
    ) -> None:
        self.examples = list(examples or [])
        self.warning = warning
        self.calls: List[Tuple[str, str]] = []

    async def __call__(
        self, caption: str, target_model: str
    ) -> Tuple[List[str], Optional[str]]:
        self.calls.append((caption, target_model))
        return list(self.examples), self.warning


class VariantCase(AccountingCase):
    def angles(self) -> List[str]:
        return variant_angles(get_prompt_store())

    async def finish_all(self) -> None:
        for job_id in list(self.comfy.job_ids):
            await self.job_done(job_id)

    def user_turns(self) -> List[str]:
        assert self.vlm is not None
        return [str(call["user_prompt"]) for call in self.vlm.calls]

    def system_turns(self) -> List[str]:
        assert self.vlm is not None
        return [str(call["system_prompt"]) for call in self.vlm.calls]


class TestNormalizePrompt(VariantCase):
    async def test_whitespace_only_differences_normalize_equal(self) -> None:
        self.assertEqual(normalize_prompt("  a  b\n\tc "), "a b c")
        self.assertEqual(normalize_prompt(""), "")
        self.assertNotEqual(normalize_prompt("a b"), normalize_prompt("A b"))


class TestVariantSteps(VariantCase):
    async def test_random_variants_share_seeds(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2, variants=3)
        )
        await self.wait(started.batch_id)
        self.assertEqual(started.total_images, 12)
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.total_steps, 6)
        self.assertEqual(batch.next_seed, 14)
        self.assertEqual(
            [p.seed for p in self.params()],
            [10, 11, 10, 11, 10, 11, 12, 13, 12, 13, 12, 13],
        )
        self.assertEqual(
            [batch.step_seeds(i) for i in range(6)],
            [[10, 11]] * 3 + [[12, 13]] * 3,
        )
        written = self.written(started.batch_id)
        self.assertEqual([s["step"] for s in written], [1, 2, 3, 4, 5, 6])
        self.assertEqual([s["variant_index"] for s in written], [0, 1, 2] * 2)
        self.assertEqual([s["variants"] for s in written], [3] * 6)
        # One caption per group, two different captions across groups.
        self.assertEqual(len({s["caption"] for s in written[:3]}), 1)
        self.assertEqual(len({s["caption"] for s in written[3:]}), 1)
        self.assertNotEqual(written[0]["caption"], written[3]["caption"])
        # All seeds of the steps are each step's prompt seed.
        self.assertEqual([s["seed"] for s in written], [10, 10, 10, 12, 12, 12])

    async def test_one_caption_is_drawn_per_group(self) -> None:
        # Variants share their caption: one draw per caption, never per step.
        self.roomy()
        with mock.patch.object(
            CaptionPicker, "next", autospec=True, side_effect=CaptionPicker.next
        ) as draw:
            await self.run_to_end(
                self.random_mode(seed=10, batch_size=2, count_per_batch=1, variants=3)
            )
        self.assertEqual(draw.call_count, 2)

    async def test_a_partial_last_caption_keeps_the_seeds_shared(self) -> None:
        from metascan.core.t2i_form import SEED_MAX

        self.roomy()
        started = await self.start(
            self.random_mode(
                seed=SEED_MAX - 2, batch_size=2, count_per_batch=2, variants=2
            )
        )
        await self.wait(started.batch_id)
        top = SEED_MAX
        self.assertEqual(started.total_images, 6)
        self.assertEqual(
            [p.seed for p in self.params()],
            [top - 2, top - 1, top - 2, top - 1, top, top],
        )
        batch = self.runner._batches[started.batch_id]
        self.assertEqual(batch.total_steps, 4)
        self.assertTrue(any("seed range exhausted" in w for w in started.warnings))
        # rendering_step follows the uneven step sizes.
        shown: List[int] = []
        for job_id in list(self.comfy.job_ids):
            shown.append(batch.rendering_step)
            await self.job_done(job_id)
        self.assertEqual(shown, [0, 0, 1, 1, 2, 3])

    async def test_variant_angles_in_prompts(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(seed=10, batch_size=1, count_per_batch=1, variants=3)
        )
        await self.wait(started.batch_id)
        angles = self.angles()
        self.assertGreaterEqual(len(angles), 2)
        turns = self.user_turns()
        self.assertEqual(len(turns), 3)
        self.assertNotIn("ANGLE:", turns[0])
        self.assertIn(f"ANGLE: lead with {angles[0]}", turns[1])
        self.assertIn(f"ANGLE: lead with {angles[1]}", turns[2])
        # Same caption for every variant: the DESCRIPTION blocks match.
        descriptions = {t.split("\n\n")[0] for t in turns}
        self.assertEqual(len(descriptions), 1)

    async def test_variant_lineage_recorded(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend(f"SYNTH prompt {n}" for n in range(6))
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=1, variants=3)
        )
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["variant_index"] for r in rows], [0, 1, 2, 0, 1, 2])
        self.assertEqual({r["writer_id"] for r in rows}, {"base"})
        first, second = {r["variant_group"] for r in rows[:3]}, {
            r["variant_group"] for r in rows[3:]
        }
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first, second)
        self.assertNotIn(None, first | second)
        for row in rows:
            self.assertEqual(row["draft_prompt"], row["prompt_used"])
            self.assertEqual(row["edited"], 0)
        self.assertEqual(
            [r["prompt_used"] for r in rows], [f"SYNTH prompt {n}" for n in range(6)]
        )
        self.assertEqual([r["seed"] for r in rows], [10, 10, 10, 11, 11, 11])

    async def test_one_variant_is_still_a_group_of_one(self) -> None:
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=2, count_per_batch=1))
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual([r["variant_index"] for r in rows], [0, 0])
        self.assertEqual(len({r["variant_group"] for r in rows}), 2)

    async def test_a_vlm_failure_falls_back_for_that_variant_only(self) -> None:
        from metascan.core.vlm_client import VlmError

        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend(["SYNTH ok 0", VlmError("x"), VlmError("x"), "SYNTH 2"])
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=1, count_per_batch=1, variants=3)
        )
        await self.finish_all()
        self.assertEqual(
            [r["writer_id"] for r in _rows(self.db)], ["base", "fallback", "base"]
        )

    async def test_unload_queue_holds_every_variant_step(self) -> None:
        # Unloading on (default): every prompt first, then the jobs.
        assert self.vlm is not None
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=1, variants=3)
        )
        timeline = [e for e in self.timeline() if e[0] in ("vlm", "comfy")]
        kinds = [e[1] for e in timeline]
        self.assertEqual(kinds[:6], ["generate_text"] * 6)
        self.assertEqual(kinds[6], "shutdown")
        self.assertEqual(kinds[7:], ["submit"] * 6)


class TestTasteWriter(VariantCase):
    async def test_taste_writer_recorded_only_when_used(self) -> None:
        # No favorites: the service returns no examples and says why.
        taste = FakeTaste([], "taste guidance unavailable: no favorites")
        self.runner.taste_examples = taste
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                batch_size=1, count_per_batch=1, variants=2, writer="base+taste"
            )
        )
        await self.finish_all()
        self.assertEqual([r["writer_id"] for r in _rows(self.db)], ["base", "base"])
        for step in self.written(batch_id):
            self.assertIn("taste guidance unavailable: no favorites", step["warnings"])
        self.assertEqual(len(taste.calls), 1)  # once per caption
        for system in self.system_turns():
            self.assertNotIn("EXAMPLES:", system)

    async def test_examples_guide_every_variant_and_are_fetched_once(self) -> None:
        taste = FakeTaste(["SYNTH-EX favorite one", "SYNTH-EX favorite two"])
        self.runner.taste_examples = taste
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                batch_size=2, count_per_batch=1, variants=2, writer="base+taste"
            )
        )
        await self.finish_all()
        self.assertEqual(len(taste.calls), 2)  # one per caption, not per variant
        self.assertEqual({model for _, model in taste.calls}, {"krea2"})
        systems = self.system_turns()
        self.assertEqual(len(systems), 4)
        for system in systems:
            self.assertIn("EXAMPLES:", system)
            self.assertIn("SYNTH-EX favorite one", system)
        self.assertEqual({r["writer_id"] for r in _rows(self.db)}, {"base+taste"})

    async def test_the_resolved_caption_is_what_is_searched(self) -> None:
        taste = FakeTaste(["SYNTH-EX one"])
        self.runner.taste_examples = taste
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                batch_size=1,
                count_per_batch=1,
                writer="base+taste",
                caption="__ALICE__ walks along a beach at dawn.",
                aspect_ratio="3:2",
            )
        )
        (step,) = self.written(batch_id)
        expected = self.expected_text(step["caption"], step["seed"])
        self.assertEqual(taste.calls, [(expected, "krea2")])

    async def test_a_missing_examples_preamble_records_base(self) -> None:
        from metascan.core import t2i_prompt

        real = get_prompt_store()

        class NoPreamble:
            def get(self, key: str) -> str:
                if key == "T2I_EXAMPLES_PREAMBLE":
                    raise KeyError(key)
                return real.get(key)

            def __contains__(self, key: object) -> bool:
                return key != "T2I_EXAMPLES_PREAMBLE" and key in real

        self.runner.taste_examples = FakeTaste(["SYNTH-EX one"])
        self.roomy()
        with mock.patch.object(
            t2i_prompt, "get_prompt_store", return_value=NoPreamble()
        ):
            batch_id = await self.run_to_end(
                self.random_mode(batch_size=1, count_per_batch=1, writer="base+taste")
            )
        await self.finish_all()
        self.assertEqual([r["writer_id"] for r in _rows(self.db)], ["base"])
        (step,) = self.written(batch_id)
        self.assertTrue(
            any(w.startswith("taste guidance unavailable") for w in step["warnings"])
        )

    async def test_no_service_degrades_to_base_with_a_warning(self) -> None:
        self.runner.taste_examples = None
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(batch_size=1, count_per_batch=1, writer="base+taste")
        )
        await self.finish_all()
        self.assertEqual([r["writer_id"] for r in _rows(self.db)], ["base"])
        (step,) = self.written(batch_id)
        self.assertTrue(
            any(w.startswith("taste guidance unavailable") for w in step["warnings"])
        )

    async def test_the_base_writer_never_asks_for_examples(self) -> None:
        taste = FakeTaste(["SYNTH-EX one"])
        self.runner.taste_examples = taste
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=2, count_per_batch=1))
        self.assertEqual(taste.calls, [])

    async def test_no_vlm_is_the_fallback_writer_even_when_guided(self) -> None:
        taste = FakeTaste(["SYNTH-EX one"])
        self.runner.taste_examples = taste
        self.runner.vlm_installed = lambda model_id: False
        self.roomy()
        await self.run_to_end(
            self.random_mode(batch_size=1, count_per_batch=1, writer="base+taste")
        )
        await self.finish_all()
        self.assertEqual([r["writer_id"] for r in _rows(self.db)], ["fallback"])

    async def test_examples_and_prompts_are_not_logged_at_info(self) -> None:
        taste = FakeTaste(["SYNTH-EX secret favorite"])
        self.runner.taste_examples = taste
        assert self.vlm is not None
        self.vlm.default_reply = "SYNTH-PROMPT secret"
        self.roomy()
        with self.assertLogs("metascan", "INFO") as logs:
            import logging

            logging.getLogger("metascan.core.t2i_runner").info("marker")
            await self.run_to_end(
                self.random_mode(
                    batch_size=1, count_per_batch=1, variants=2, writer="base+taste"
                )
            )
            await self.finish_all()
        text = "\n".join(logs.output)
        self.assertNotIn("SYNTH-EX", text)
        self.assertNotIn("SYNTH-PROMPT", text)


class TestSuppliedPrompts(VariantCase):
    def drafts(self, *pairs: Tuple[Optional[str], str]) -> List[Dict[str, Any]]:
        return [
            {
                "prompt": prompt,
                "negative": None,
                "draft": draft,
                "writer_id": "base" if draft is not None else None,
                "variant_index": index,
            }
            for index, (draft, prompt) in enumerate(pairs)
        ]

    async def test_manual_prompts_with_drafts(self) -> None:
        batch_id = await self.manual_batch(
            count=1,
            prompt=None,
            variants=2,
            prompts=self.drafts(("SYNTH a", "SYNTH a"), ("SYNTH b", "SYNTH b edited")),
        )
        self.assertEqual(
            [p.positive for p in self.params()], ["SYNTH a", "SYNTH b edited"]
        )
        self.assertEqual([p.seed for p in self.params()], [100, 100])
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual([r["edited"] for r in rows], [0, 1])
        self.assertEqual([r["draft_prompt"] for r in rows], ["SYNTH a", "SYNTH b"])
        self.assertEqual([r["writer_id"] for r in rows], ["base", "base"])
        self.assertEqual([r["variant_index"] for r in rows], [0, 1])
        self.assertEqual(len({r["variant_group"] for r in rows}), 1)
        self.assertEqual(len(self.written(batch_id)), 2)

    async def test_a_whitespace_only_edit_is_not_an_edit(self) -> None:
        await self.manual_batch(
            count=1,
            prompt=None,
            variants=1,
            prompts=self.drafts(("SYNTH a  b", " SYNTH a\nb ")),
        )
        await self.finish_all()
        (row,) = _rows(self.db)
        self.assertEqual(row["edited"], 0)
        self.assertEqual(row["draft_prompt"], "SYNTH a  b")

    async def test_a_prompt_without_a_draft_is_manual(self) -> None:
        await self.manual_batch(
            count=1,
            prompt=None,
            variants=2,
            prompts=self.drafts(("SYNTH a", "SYNTH a"), (None, "SYNTH typed")),
        )
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual([r["writer_id"] for r in rows], ["base", "manual"])
        self.assertEqual([r["draft_prompt"] for r in rows], ["SYNTH a", None])
        self.assertEqual([r["edited"] for r in rows], [0, 0])

    async def test_variant_negatives_ride_their_own_variant(self) -> None:
        prompts = self.drafts(("SYNTH a", "SYNTH a"), ("SYNTH b", "SYNTH b"))
        prompts[1]["negative"] = "SYNTH blur"
        await self.manual_batch(
            count=1,
            prompt=None,
            preset_id=self.with_negative,
            variants=2,
            prompts=prompts,
        )
        self.assertEqual([p.negative for p in self.params()], [None, "SYNTH blur"])

    async def test_typed_prompt_is_manual(self) -> None:
        await self.manual_batch(count=2)
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual([r["writer_id"] for r in rows], ["manual", "manual"])
        self.assertEqual([r["draft_prompt"] for r in rows], [None, None])
        self.assertEqual([r["edited"] for r in rows], [0, 0])
        self.assertEqual([r["variant_index"] for r in rows], [0, 0])

    async def test_a_typed_prompt_renders_once_whatever_the_variants(self) -> None:
        started = await self.start(self.manual(count_per_batch=2, variants=3))
        await self.wait(started.batch_id)
        self.assertEqual(started.total_images, 2)
        self.assertTrue(any("variants" in w for w in started.warnings))

    async def test_a_random_first_batch_supplies_its_variants(self) -> None:
        assert self.vlm is not None
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                seed=10,
                batch_size=2,
                count_per_batch=1,
                variants=2,
                caption="SYNTH caption",
                aspect_ratio="2:3",
                prompts=self.drafts(("SYNTH a", "SYNTH a x"), ("SYNTH b", "SYNTH b")),
            )
        )
        written = self.written(batch_id)
        self.assertEqual(len(written), 4)
        self.assertEqual([s["prompt"] for s in written[:2]], ["SYNTH a x", "SYNTH b"])
        self.assertEqual(len(self.vlm.calls), 2)  # only the drawn caption's
        self.assertEqual([p.seed for p in self.params()], [10, 10, 11, 11])
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual([r["edited"] for r in rows], [1, 0, 0, 0])
        self.assertEqual([r["draft_prompt"] for r in rows[:2]], ["SYNTH a", "SYNTH b"])

    async def test_a_random_first_batch_may_supply_fewer_variants(self) -> None:
        # The dialog's drafts no longer match the picker: it sends the one
        # on show. Batch 1 renders it once, as the writer's draft; the drawn
        # captions after it still get every variant.
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(
                seed=10,
                batch_size=2,
                count_per_batch=1,
                variants=2,
                caption="SYNTH caption",
                aspect_ratio="2:3",
                prompts=self.drafts(("SYNTH b", "SYNTH b")),
            )
        )
        written = self.written(batch_id)
        self.assertEqual([s["variants"] for s in written], [1, 2, 2])
        self.assertEqual([p.seed for p in self.params()], [10, 11, 11])
        await self.finish_all()
        rows = _rows(self.db)
        self.assertEqual(rows[0]["writer_id"], "base")
        self.assertEqual(rows[0]["draft_prompt"], "SYNTH b")

    async def test_a_supplied_step_frame_carries_its_draft(self) -> None:
        # The frame names the writer's draft, not the edited text, so the
        # hand-back keeps the original draft for the next Generate.
        batch_id = await self.manual_batch(
            count=1,
            prompt=None,
            variants=2,
            prompts=self.drafts(("SYNTH a", "SYNTH a"), ("SYNTH b", "SYNTH b edited")),
        )
        await self.finish_all()
        # Each step goes on show (batch_step) as its image renders.
        steps = self.frames("batch_step")
        self.assertEqual(
            steps, [{"batch_id": batch_id, **w} for w in self.written(batch_id)]
        )
        self.assertEqual(
            [(s["prompt"], s["draft"], s["writer_id"]) for s in steps],
            [("SYNTH a", "SYNTH a", "base"), ("SYNTH b edited", "SYNTH b", "base")],
        )

    async def test_a_legacy_random_first_prompt_is_manual_and_single(self) -> None:
        self.roomy()
        started = await self.start(
            self.random_mode(
                seed=10,
                batch_size=2,
                count_per_batch=1,
                variants=2,
                prompt="SYNTH typed",
                aspect_ratio="2:3",
            )
        )
        await self.wait(started.batch_id)
        self.assertEqual(started.total_images, 3)  # 1 typed + 2 variants
        self.assertEqual([p.seed for p in self.params()], [10, 11, 11])
        await self.finish_all()
        self.assertEqual(
            [r["writer_id"] for r in _rows(self.db)], ["manual", "base", "base"]
        )


class TestSuppliedPromptValidation(RunnerCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()

        async def parked(runner: Any, batch: Any) -> None:
            await asyncio.Event().wait()

        patcher = mock.patch(
            "metascan.core.t2i_runner.T2iRunner._run", autospec=True, side_effect=parked
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def rejects(self, needle: str, req: BatchRequest) -> None:
        with self.assertRaises(T2iRequestError) as caught:
            await self.runner.start_batch(req)
        self.assertIn(needle, str(caught.exception))

    def entry(self, index: int, **over: Any) -> Dict[str, Any]:
        e: Dict[str, Any] = {
            "prompt": f"SYNTH {index}",
            "negative": None,
            "draft": f"SYNTH {index}",
            "writer_id": "base",
            "variant_index": index,
        }
        e.update(over)
        return e

    async def test_variants_out_of_range(self) -> None:
        for bad in (0, 5, True, "2"):
            with self.subTest(variants=bad):
                await self.rejects("Variants", self.random_mode(variants=bad))

    async def test_unknown_writer(self) -> None:
        await self.rejects("writer", self.random_mode(writer="adapter:x"))

    async def test_prompts_cannot_outnumber_the_variants(self) -> None:
        await self.rejects(
            "prompts",
            self.manual(
                prompt=None, variants=1, prompts=[self.entry(0), self.entry(1)]
            ),
        )
        await self.rejects("prompts", self.manual(prompt=None, prompts=[]))

    async def test_fewer_prompts_than_variants_is_valid(self) -> None:
        started = await self.runner.start_batch(
            self.manual(prompt=None, variants=3, prompts=[self.entry(0)])
        )
        self.assertEqual(started.total_images, 3)

    async def test_variant_indexes_must_be_zero_to_n(self) -> None:
        await self.rejects(
            "variant_index",
            self.manual(
                prompt=None, variants=2, prompts=[self.entry(0), self.entry(0)]
            ),
        )

    async def test_a_blank_variant_prompt(self) -> None:
        await self.rejects(
            "empty",
            self.manual(
                prompt=None,
                variants=2,
                prompts=[self.entry(0), self.entry(1, prompt="  ")],
            ),
        )

    async def test_a_draft_needs_a_known_writer(self) -> None:
        await self.rejects(
            "writer_id",
            self.manual(prompt=None, prompts=[self.entry(0, writer_id="someone")]),
        )
        await self.rejects(
            "writer_id",
            self.manual(prompt=None, prompts=[self.entry(0, writer_id=None)]),
        )

    async def test_an_unknown_or_non_writer_model_is_rejected(self) -> None:
        for bad in ("nope", "qwen38-27b", 3):
            with self.subTest(writer_model=bad):
                await self.rejects(
                    "writer_model",
                    self.manual(
                        prompt=None,
                        prompts=[self.entry(0, writer_model=bad)],
                    ),
                )

    async def test_a_random_first_batch_with_prompts_needs_a_ratio(self) -> None:
        await self.rejects(
            "Aspect ratio",
            self.random_mode(prompts=[self.entry(0)], aspect_ratio=None),
        )

    async def test_prompts_in_the_request_are_valid(self) -> None:
        started = await self.runner.start_batch(
            self.manual(prompt=None, variants=2, prompts=[self.entry(0), self.entry(1)])
        )
        self.assertEqual(started.total_images, 6)


class TestBatchShowWithVariants(VariantCase):
    def shown_steps(self) -> List[int]:
        return [s["step"] for s in self.frames("batch_step")]

    async def test_batch_step_on_show_with_variants(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2, variants=2)
        )
        # Every prompt is written (unload on), only step 1 is on show.
        self.assertEqual(len(self.written(batch_id)), 4)
        self.assertEqual(self.shown_steps(), [1])
        seen: List[Tuple[List[int], Any, Any]] = []
        for job_id in list(self.comfy.job_ids):
            await self.job_done(job_id)
            rows = self.runner.active_batches()
            listed = (rows[0]["step"]["step"], rows[0]["seed"]) if rows else None
            seen.append((self.shown_steps(), listed, None))
        self.assertEqual(
            [s[0] for s in seen],
            [
                [1],
                [1, 2],
                [1, 2],
                [1, 2, 3],
                [1, 2, 3],
                [1, 2, 3, 4],
                [1, 2, 3, 4],
                [1, 2, 3, 4],
            ],
        )
        # GET /batches lists the step on show and the seed rendering now.
        self.assertEqual(
            [s[1] for s in seen[:-1]],
            [(1, 11), (2, 10), (2, 11), (3, 12), (3, 13), (4, 12), (4, 13)],
        )
        steps = self.frames("batch_step")
        self.assertEqual([s["variant_index"] for s in steps], [0, 1, 0, 1])
        self.assertEqual([s["seed"] for s in steps], [10, 10, 12, 12])
        # Which caption of how many, and each step's lineage (a written
        # step's draft is its prompt), so the dialog can label it and keep
        # the handed-back prompt as the writer's draft.
        self.assertEqual(
            [(s["caption_index"], s["captions"]) for s in steps],
            [(0, 2), (0, 2), (1, 2), (1, 2)],
        )
        self.assertEqual([s["draft"] for s in steps], [s["prompt"] for s in steps])
        self.assertEqual({s["writer_id"] for s in steps}, {"base"})
        self.assertEqual(self.terminal_frames(), ["batch_complete"])
        (done,) = self.frames("batch_complete")
        self.assertEqual(done["images_done"], 8)
        self.assertEqual(done["next_seed"], 14)

    async def test_progress_seed_follows_the_shared_seed_list(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(seed=10, batch_size=1, count_per_batch=2, variants=2)
        )
        await self.finish_all()
        seeds = [
            f["seed"] for f in self.frames("batch_progress") if f["images_done"] > 0
        ]
        self.assertEqual(seeds, [11, 10, 11, 12])

    async def test_with_overlap_the_written_variant_waits_its_turn(self) -> None:
        self.runner.unload_vlm_during_generation = False
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(seed=10, batch_size=1, count_per_batch=1, variants=3)
        )
        self.assertEqual(len(self.written(batch_id)), 3)
        self.assertEqual(self.shown_steps(), [1])
        await self.finish_all()
        self.assertEqual(self.shown_steps(), [1, 2, 3])


class TestRandomLockAndCancelWithVariants(VariantCase):
    async def test_random_lock_and_cancel_with_variants(self) -> None:
        self.cfg = get_t2i_config({"t2i": {"window": 2}})
        started = await self.start(
            self.random_mode(seed=10, batch_size=2, count_per_batch=2, variants=3)
        )
        with self.assertRaises(T2iConflictError) as caught:
            await self.start(self.random_mode(variants=2))
        self.assertTrue(str(caught.exception).startswith("random_batch_active"))
        await self.until(lambda: len(self.comfy.submits) == 2, "two submits")

        self.assertTrue(await self.runner.cancel_batch(started.batch_id))
        self.assertEqual(self.comfy.cancelled, self.comfy.job_ids)
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])
        (frame,) = self.frames("batch_cancelled")
        self.assertEqual(frame["images_total"], 12)
        self.assertEqual(frame["images_cancelled"], 12)
        self.assertEqual(frame["next_seed"], 14)
        # The lock is free again.
        again = await self.start(self.random_mode(variants=2, batch_size=1))
        await self.runner.cancel_batch(again.batch_id)

    async def test_cancel_while_writing_a_later_variant(self) -> None:
        assert self.vlm is not None
        gate = asyncio.Event()
        self.vlm.gates.extend([None, gate])  # variant 1 never comes back
        started = await self.start(
            self.random_mode(seed=10, batch_size=1, count_per_batch=1, variants=3)
        )
        await self.until(lambda: len(self.vlm.calls) == 2, "the second VLM call")
        self.assertTrue(await self.runner.cancel_batch(started.batch_id))
        self.assertEqual(self.vlm.cancelled_calls, 1)
        self.assertEqual(self.comfy.submits, [])
        self.assertEqual(self.terminal_frames(), ["batch_cancelled"])


class TestGeneratePromptDrafts(VariantCase):
    async def test_variants_give_one_draft_each(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.extend(["SYNTH d0", "SYNTH d1", "SYNTH d2"])
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2", variants=3
        )
        self.assertEqual(
            [d.prompt for d in result.drafts], ["SYNTH d0", "SYNTH d1", "SYNTH d2"]
        )
        self.assertEqual([d.variant_index for d in result.drafts], [0, 1, 2])
        self.assertEqual({d.writer_id for d in result.drafts}, {"base"})
        angles = self.angles()
        self.assertEqual([d.angle for d in result.drafts], [None, angles[0], angles[1]])
        self.assertEqual(result.prompt, "SYNTH d0")
        self.assertEqual(result.negative, result.drafts[0].negative)
        turns = self.user_turns()
        self.assertNotIn("ANGLE:", turns[0])
        self.assertIn(f"ANGLE: lead with {angles[1]}", turns[2])

    async def test_one_variant_is_the_old_answer(self) -> None:
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2"
        )
        self.assertEqual(len(result.drafts), 1)
        self.assertEqual(result.drafts[0].prompt, result.prompt)
        self.assertIsNone(result.drafts[0].angle)

    async def test_taste_guidance_and_its_warning(self) -> None:
        taste = FakeTaste(["SYNTH-EX one"])
        self.runner.taste_examples = taste
        result = await self.runner.generate_prompt(
            caption="SYNTH caption",
            seed=3,
            model="krea2",
            variants=2,
            writer="base+taste",
        )
        self.assertEqual({d.writer_id for d in result.drafts}, {"base+taste"})
        self.assertEqual(len(taste.calls), 1)
        self.runner.taste_examples = FakeTaste([], "taste guidance unavailable: why")
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2", writer="base+taste"
        )
        self.assertEqual(result.drafts[0].writer_id, "base")
        self.assertIn("taste guidance unavailable: why", result.warnings)

    async def test_no_vlm_gives_fallback_drafts(self) -> None:
        self.vlm = None
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2", variants=2
        )
        self.assertEqual([d.writer_id for d in result.drafts], ["fallback", "fallback"])
        self.assertEqual(len(result.drafts), 2)

    async def test_bad_variants_or_writer_are_request_errors(self) -> None:
        for kwargs in ({"variants": 0}, {"variants": 5}, {"writer": "nope"}):
            with self.subTest(**kwargs):
                with self.assertRaises(T2iRequestError):
                    await self.runner.generate_prompt(
                        caption="SYNTH caption", seed=3, model="krea2", **kwargs
                    )


class TestVariantsApi(T2iApiCase):
    def test_variants_out_of_range_422(self) -> None:
        for variants in (0, 5, -1):
            with self.subTest(variants=variants):
                resp = self.client.post(
                    "/api/t2i/batches", json=self.batch_body(variants=variants)
                )
                self.assertEqual(resp.status_code, 422)
                resp = self.client.post(
                    "/api/t2i/prompt",
                    json={
                        "caption": "x",
                        "seed": 1,
                        "model": "sd",
                        "variants": variants,
                    },
                )
                self.assertEqual(resp.status_code, 422)
        resp = self.client.post("/api/t2i/batches", json=self.batch_body(writer="nope"))
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.runner.calls, [])

    def test_batch_fields_reach_the_runner(self) -> None:
        prompts = [
            {
                "prompt": "SYNTH a",
                "negative": None,
                "draft": "SYNTH a",
                "writer_id": "base",
                "variant_index": 0,
            },
            {"prompt": "SYNTH b", "draft": None, "variant_index": 1},
        ]
        resp = self.client.post(
            "/api/t2i/batches",
            json=self.batch_body(
                prompt=None, variants=2, writer="base+taste", prompts=prompts
            ),
        )
        self.assertEqual(resp.status_code, 200)
        ((_, req),) = self.runner.calls
        self.assertEqual(req.variants, 2)
        self.assertEqual(req.writer, "base+taste")
        self.assertEqual(
            req.prompts,
            [
                {
                    "prompt": "SYNTH a",
                    "negative": None,
                    "draft": "SYNTH a",
                    "writer_id": "base",
                    "writer_model": None,
                    "variant_index": 0,
                },
                {
                    "prompt": "SYNTH b",
                    "negative": None,
                    "draft": None,
                    "writer_id": None,
                    "writer_model": None,
                    "variant_index": 1,
                },
            ],
        )

    def test_defaults_are_one_base_variant(self) -> None:
        resp = self.client.post("/api/t2i/batches", json=self.batch_body())
        self.assertEqual(resp.status_code, 200)
        ((_, req),) = self.runner.calls
        self.assertEqual((req.variants, req.writer, req.prompts), (1, "base", None))

    def test_prompt_endpoint_returns_drafts(self) -> None:
        drafts = [
            PromptDraft(f"SYNTH d{i}", None, "base", i, None if i == 0 else f"angle{i}")
            for i in range(3)
        ]
        self.runner.prompt_result = PromptResult(
            "SYNTH d0",
            None,
            "Resolved.",
            ["taste guidance unavailable: x"],
            drafts=drafts,
        )
        resp = self.client.post(
            "/api/t2i/prompt",
            json={
                "caption": "x",
                "seed": 1,
                "model": "sd",
                "variants": 3,
                "writer": "base+taste",
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["drafts"]), 3)
        self.assertEqual(body["prompt"], body["drafts"][0]["prompt"])
        self.assertEqual(
            body["drafts"][1],
            {
                "prompt": "SYNTH d1",
                "negative": None,
                "writer_id": "base",
                "writer_model": None,
                "variant_index": 1,
                "angle": "angle1",
            },
        )
        self.assertIn("taste guidance unavailable: x", body["warnings"])
        ((_, kwargs),) = self.runner.calls
        self.assertEqual((kwargs["variants"], kwargs["writer"]), (3, "base+taste"))


# --------------------------------------------------------------------------
# the writer's model is lineage too (final review item 2)
# --------------------------------------------------------------------------


def _models(db: Any) -> List[Tuple[Optional[str], Optional[str]]]:
    with db.lock, db._get_connection() as conn:
        return [
            (r["writer_id"], r["writer_model"])
            for r in conn.execute(
                "SELECT writer_id, writer_model FROM t2i_images ORDER BY id"
            )
        ]


class TestWriterModel(VariantCase):
    def entry(self, index: int, **over: Any) -> Dict[str, Any]:
        e: Dict[str, Any] = {
            "prompt": f"SYNTH {index}",
            "negative": None,
            "draft": f"SYNTH {index}",
            "writer_id": "base",
            "writer_model": "qwen3vl-8b",
            "variant_index": index,
        }
        e.update(over)
        return e

    async def test_random_steps_record_the_writer_model(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(seed=10, batch_size=1, count_per_batch=1, variants=2)
        )
        await self.finish_all()
        self.assertEqual(_models(self.db), [("base", "qwen3vl-8b")] * 2)
        self.assertEqual(
            {s["writer_model"] for s in self.written(batch_id)}, {"qwen3vl-8b"}
        )

    async def test_the_model_actually_asked_is_recorded(self) -> None:
        assert self.vlm is not None
        self.vlm.model_id = "qwen3vl-4b"
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        await self.finish_all()
        self.assertEqual(_models(self.db), [("base", "qwen3vl-4b")])

    async def test_a_fallback_or_typed_prompt_has_no_writer_model(self) -> None:
        self.vlm = None
        self.roomy()
        await self.run_to_end(self.random_mode(batch_size=1, count_per_batch=1))
        await self.manual_batch(count=1)
        await self.finish_all()
        self.assertEqual(_models(self.db), [("fallback", None), ("manual", None)])

    async def test_supplied_drafts_keep_their_writer_model(self) -> None:
        await self.manual_batch(
            count=1,
            prompt=None,
            variants=3,
            prompts=[
                self.entry(0),
                self.entry(1, writer_model="qwen3vl-4b"),
                # Typed: manual, and no model whatever the entry says.
                self.entry(2, draft=None, writer_id=None),
            ],
        )
        await self.finish_all()
        self.assertEqual(
            _models(self.db),
            [("base", "qwen3vl-8b"), ("base", "qwen3vl-4b"), ("manual", None)],
        )

    async def test_a_draft_without_a_model_is_recorded_as_unknown(self) -> None:
        entry = self.entry(0)
        del entry["writer_model"]
        await self.manual_batch(count=1, prompt=None, prompts=[entry])
        await self.finish_all()
        self.assertEqual(_models(self.db), [("base", None)])

    async def test_a_fallback_draft_has_no_model(self) -> None:
        await self.manual_batch(
            count=1, prompt=None, prompts=[self.entry(0, writer_id="fallback")]
        )
        await self.finish_all()
        self.assertEqual(_models(self.db), [("fallback", None)])

    async def test_generate_prompt_drafts_name_their_model(self) -> None:
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2", variants=2
        )
        self.assertEqual({d.writer_model for d in result.drafts}, {"qwen3vl-8b"})
        self.vlm = None
        result = await self.runner.generate_prompt(
            caption="SYNTH caption", seed=3, model="krea2"
        )
        self.assertEqual([d.writer_model for d in result.drafts], [None])


class TestWriterModelApi(T2iApiCase):
    def test_prompt_endpoint_names_the_model(self) -> None:
        self.runner.prompt_result = PromptResult(
            "SYNTH d0",
            None,
            "Resolved.",
            [],
            drafts=[PromptDraft("SYNTH d0", None, "base", 0, None, "qwen3vl-8b")],
        )
        resp = self.client.post(
            "/api/t2i/prompt", json={"caption": "x", "seed": 1, "model": "sd"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["drafts"][0]["writer_model"], "qwen3vl-8b")

    def test_batch_entries_carry_the_model(self) -> None:
        prompts = [
            {
                "prompt": "SYNTH a",
                "draft": "SYNTH a",
                "writer_id": "base",
                "writer_model": "qwen3vl-8b",
                "variant_index": 0,
            }
        ]
        resp = self.client.post(
            "/api/t2i/batches", json=self.batch_body(prompt=None, prompts=prompts)
        )
        self.assertEqual(resp.status_code, 200)
        ((_, req),) = self.runner.calls
        self.assertEqual(req.prompts[0]["writer_model"], "qwen3vl-8b")
