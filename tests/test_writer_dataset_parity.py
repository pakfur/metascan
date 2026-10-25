"""Train/infer parity for the prompt-writer dataset: the system and user turns
``writer_dataset`` builds for a generated image are byte-identical to the
ones the T2I runner sent when an adapter wrote that image -- per path
(Random batch; Generate Prompt then a Manual batch of its drafts). Also:
adapters write without angles, an ``all``-scope adapter names the batch's
categories, and each image stores its resolved caption and direction.

All captions and prompts are synthetic.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_directions import SnippetCache
from metascan.core.writer_adapters import adapter_writer_id
from metascan.core.writer_dataset import build_examples
from tests.test_t2i_runner_directions import ACT_CAPTION, COLUMNS, PLAIN_CAPTION, _row
from tests.test_writer_adapter_serving import BASE, _T2iBase

DIRECTION = "He takes her from behind. A soft unguarded smile."


def _lineage(db: Any) -> List[Dict[str, Any]]:
    with db.lock, db._get_connection() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT file_path, caption, resolved_caption, direction, "
                "prompt_seed, prompt_used FROM t2i_images ORDER BY id"
            )
        ]


class ParityCase(_T2iBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COLUMNS)
            w.writerow(_row(ACT_CAPTION, "doggy", "0.9500", "0.0500"))
            w.writerow(_row(PLAIN_CAPTION, "none-artistic", "0.9900", "0.9900"))
        snippets = Path(self.root) / "directions"
        snippets.mkdir()
        (snippets / "act.doggy.txt").write_text(
            "he takes her from behind\n", encoding="utf-8"
        )
        (snippets / "emotion.txt").write_text(
            "a soft unguarded smile\n", encoding="utf-8"
        )
        self.runner.directions = SnippetCache(snippets)
        self.cat = self.db.create_category("Bedroom")
        self.other = self.db.create_category("Beach")
        self.adapter = self.db.create_writer_adapter(
            name="taste-all-v1",
            scope="all",
            kind="t2i",
            base_model=BASE,
            status="untested",
        )
        self.scoped = self.db.create_writer_adapter(
            name="taste-bedroom-v1",
            scope=str(self.cat.id),
            kind="t2i",
            base_model=BASE,
            status="untested",
        )
        self.wid = adapter_writer_id(self.adapter)
        self.scoped_wid = adapter_writer_id(self.scoped)
        self.served = {
            self.adapter: self.root / "a.gguf",
            self.scoped: self.root / "b.gguf",
        }
        self.runner.writer_adapter = self.served.get

    async def render_all(self) -> List[str]:
        """Finish every job with a library row per image (so the ingest tags
        its generation categories), then return the image paths."""
        paths = []
        for job_id in list(self.comfy.job_ids):
            path = self.image(job_id)
            self.db.save_media(_media(path))
            await self.job_done(job_id)
            paths.append(path)
        return paths

    def judge(self, winner: str, loser: str) -> None:
        """✓ the winner's image, ✗ the loser's (grades are the only label)."""
        self.assertIsNotNone(self.db.set_grade(winner, "pass"))
        self.assertIsNotNone(self.db.set_grade(loser, "fail"))

    def build(self, scope: str = "all") -> Any:
        return build_examples(
            self.db,
            scope,
            get_prompt_store(),
            datetime.now(timezone.utc) + timedelta(days=1),
            content_mode=self.cfg["content_mode"],
        )

    def turns(self) -> List[Dict[str, Any]]:
        assert self.vlm is not None
        return list(self.vlm.calls)


def _media(path: str) -> Any:
    from metascan.core.media import Media

    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


class TestRandomBatchParity(ParityCase):
    async def test_training_turns_equal_what_the_adapter_was_sent(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend(["SYNTH first", "SYNTH second"])
        await self.run_to_end(
            self.random_mode(
                caption=ACT_CAPTION,
                aspect_ratio="3:2",
                batch_size=1,
                count_per_batch=1,
                variants=2,
                writer=self.wid,
                category_ids=[self.cat.id, self.other.id],
            )
        )
        first, second = await self.render_all()
        calls = self.turns()
        self.assertEqual(len(calls), 2)
        # No angle for an adapter: both variants were asked the same thing.
        self.assertEqual(calls[0]["user_prompt"], calls[1]["user_prompt"])
        user = calls[0]["user_prompt"]
        self.assertNotIn("ANGLE", user)
        self.assertIn("CATEGORIES: Beach, Bedroom\n", user)
        self.assertIn(f"DIRECTION:\n{DIRECTION}", user)

        rows = _lineage(self.db)
        resolved = self.expected_text(ACT_CAPTION, rows[0]["prompt_seed"])
        self.assertEqual({r["resolved_caption"] for r in rows}, {resolved})
        self.assertEqual({r["direction"] for r in rows}, {DIRECTION})

        self.judge(first, second)
        _, prefs, _ = self.build()
        (pair,) = prefs
        self.assertEqual((pair.chosen, pair.rejected), ("SYNTH first", "SYNTH second"))
        self.assertEqual(pair.user, user)
        self.assertEqual(pair.system, calls[0]["system_prompt"])

    async def test_base_random_variants_keep_their_angles_and_no_categories(
        self,
    ) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(
                caption=ACT_CAPTION,
                aspect_ratio="3:2",
                batch_size=1,
                count_per_batch=1,
                variants=2,
                category_ids=[self.cat.id],
            )
        )
        calls = self.turns()
        self.assertNotIn("ANGLE", calls[0]["user_prompt"])
        self.assertIn("ANGLE: lead with", calls[1]["user_prompt"])
        for call in calls:
            self.assertNotIn("CATEGORIES", call["user_prompt"])


class TestGeneratePromptThenManualParity(ParityCase):
    async def test_training_turns_equal_what_generate_prompt_sent(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.vlm.replies.extend(["SYNTH one", "SYNTH two"])
        result = await self.runner.generate_prompt(
            caption=ACT_CAPTION,
            seed=100,
            model="krea2",
            variants=2,
            writer=self.wid,
            category_ids=[self.cat.id],
        )
        self.assertEqual([d.angle for d in result.drafts], [None, None])
        calls = self.turns()
        self.assertEqual(calls[0]["user_prompt"], calls[1]["user_prompt"])
        user = calls[0]["user_prompt"]
        self.assertIn("CATEGORIES: Bedroom\n", user)
        await self.run_to_end(
            self.manual(
                caption=ACT_CAPTION,
                seed=100,
                count_per_batch=1,
                variants=2,
                category_ids=[self.cat.id],
                prompts=[
                    {
                        "prompt": d.prompt,
                        "negative": None,
                        "draft": d.prompt,
                        "writer_id": d.writer_id,
                        "writer_model": d.writer_model,
                        "variant_index": d.variant_index,
                    }
                    for d in result.drafts
                ],
            )
        )
        first, second = await self.render_all()
        rows = _lineage(self.db)
        self.assertEqual(
            {r["resolved_caption"] for r in rows}, {result.resolved_caption}
        )
        self.assertEqual({r["direction"] for r in rows}, {result.direction})
        self.judge(second, first)
        _, prefs, _ = self.build()
        (pair,) = prefs
        self.assertEqual((pair.chosen, pair.rejected), ("SYNTH two", "SYNTH one"))
        self.assertEqual(pair.user, user)
        self.assertEqual(pair.system, calls[0]["system_prompt"])

    async def test_a_one_category_adapter_writes_and_trains_without_the_line(
        self,
    ) -> None:
        assert self.vlm is not None
        self.vlm.replies.extend(["SYNTH one", "SYNTH two"])
        result = await self.runner.generate_prompt(
            caption=ACT_CAPTION,
            seed=100,
            model="krea2",
            variants=2,
            writer=self.scoped_wid,
            category_ids=[self.cat.id],
        )
        user = self.turns()[0]["user_prompt"]
        self.assertNotIn("CATEGORIES", user)
        self.roomy()
        await self.run_to_end(
            self.manual(
                caption=ACT_CAPTION,
                seed=100,
                count_per_batch=1,
                variants=2,
                category_ids=[self.cat.id],
                prompts=[
                    {
                        "prompt": d.prompt,
                        "negative": None,
                        "draft": d.prompt,
                        "writer_id": d.writer_id,
                        "writer_model": d.writer_model,
                        "variant_index": d.variant_index,
                    }
                    for d in result.drafts
                ],
            )
        )
        first, second = await self.render_all()
        self.judge(first, second)
        _, prefs, _ = self.build(str(self.cat.id))
        (pair,) = prefs
        self.assertEqual(pair.user, user)

    async def test_a_typed_manual_prompt_stores_no_lineage_text(self) -> None:
        self.roomy()
        await self.run_to_end(self.manual(count_per_batch=1))
        await self.render_all()
        (row,) = _lineage(self.db)
        self.assertIsNone(row["resolved_caption"])
        self.assertIsNone(row["direction"])


# ---- round 2: the lineage rides with the drafts ---------------------------

WX = "__ALICE__ under a __WEATHER__ sky."
WEATHER = "\n".join(f"weather kind {n}" for n in range(40)) + "\n"


def _entry(
    prompt: str,
    draft: str,
    writer_id: str,
    writer_model: Any,
    index: int,
    lineage: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "prompt": prompt,
        "negative": None,
        "draft": draft,
        "writer_id": writer_id,
        "writer_model": writer_model,
        "variant_index": index,
        **lineage,
    }


class LineageCase(ParityCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        (self.root / "lists" / "weather.txt").write_text(WEATHER, encoding="utf-8")

    def assert_seed_matters(self, seed_a: int, seed_b: int) -> None:
        # Precondition: rebuilding for the new seed WOULD give other text.
        self.assertNotEqual(
            self.expected_text(WX, seed_a), self.expected_text(WX, seed_b)
        )


class TestLineageTravelsWithDrafts(LineageCase):
    async def test_a_second_generate_after_the_seed_moved(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.assert_seed_matters(100, 101)
        self.vlm.replies.extend(["SYNTH one", "SYNTH two"])
        result = await self.runner.generate_prompt(
            caption=WX,
            seed=100,
            model="krea2",
            variants=2,
            writer=self.wid,
            category_ids=[self.cat.id],
        )
        self.assertEqual(result.categories, ["Bedroom"])
        user = self.turns()[0]["user_prompt"]
        system = self.turns()[0]["system_prompt"]
        lineage = {
            "resolved_caption": result.resolved_caption,
            "direction": result.direction,
            "categories": result.categories,
        }
        entries = [
            _entry(d.prompt, d.prompt, d.writer_id, d.writer_model, i, lineage)
            for i, d in enumerate(result.drafts)
        ]
        # The first Generate moved the seed box on; the category was renamed.
        self.db.rename_category(self.cat.id, "Renamed")
        await self.run_to_end(
            self.manual(
                caption=WX,
                seed=101,
                count_per_batch=1,
                variants=2,
                category_ids=[self.cat.id],
                prompts=entries,
            )
        )
        first, second = await self.render_all()
        rows = _lineage(self.db)
        self.assertEqual({r["prompt_seed"] for r in rows}, {101})
        self.assertEqual(
            {r["resolved_caption"] for r in rows}, {result.resolved_caption}
        )
        self.judge(first, second)
        _, prefs, _ = self.build()
        (pair,) = prefs
        self.assertEqual(pair.user, user)
        self.assertEqual(pair.system, system)
        self.assertIn("CATEGORIES: Bedroom\n", pair.user)

    async def test_a_random_hand_back_rendered_later(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.assert_seed_matters(10, 50)
        self.vlm.replies.extend(["SYNTH first", "SYNTH second"])
        batch_id = await self.run_to_end(
            self.random_mode(
                caption=WX,
                aspect_ratio="3:2",
                seed=10,
                batch_size=1,
                count_per_batch=1,
                variants=2,
                writer=self.wid,
                category_ids=[self.cat.id],
            )
        )
        user = self.turns()[0]["user_prompt"]
        steps = self.written(batch_id)
        self.assertTrue(all(s["lineage"] is not None for s in steps))
        # What draftSetFromSteps holds and usablePrompts sends back.
        entries = [
            _entry(
                s["prompt"],
                s["draft"],
                s["writer_id"],
                s["writer_model"],
                s["variant_index"],
                s["lineage"],
            )
            for s in steps
        ]
        jobs_before = set(self.comfy.job_ids)
        await self.run_to_end(
            self.manual(
                caption=WX,
                seed=50,
                count_per_batch=1,
                variants=2,
                category_ids=[self.cat.id],
                prompts=entries,
            )
        )
        paths = await self.render_all()
        manual = [self.image(j) for j in self.comfy.job_ids if j not in jobs_before]
        self.assertEqual(len(manual), 2)
        self.assertTrue(set(manual) <= set(paths))
        self.judge(manual[0], manual[1])
        _, prefs, _ = self.build()
        (pair,) = prefs
        self.assertEqual(pair.user, user)

    async def test_a_tile_reload_rendered_with_a_newer_seed(self) -> None:
        assert self.vlm is not None
        self.roomy()
        self.assert_seed_matters(10, 77)
        self.vlm.replies.extend(["SYNTH tile"])
        await self.run_to_end(
            self.random_mode(
                caption=WX,
                aspect_ratio="3:2",
                seed=10,
                batch_size=1,
                count_per_batch=1,
                writer=self.wid,
                category_ids=[self.cat.id],
            )
        )
        user = self.turns()[0]["user_prompt"]
        await self.render_all()
        (tile,) = self.db.list_t2i_images()
        self.assertEqual(tile["prompt_categories"], ["Bedroom"])
        # draftSetFromImage: the tile's lineage columns ride with its draft.
        entry = _entry(
            "SYNTH tile, edited by hand",
            tile["draft_prompt"],
            tile["writer_id"],
            tile["writer_model"],
            0,
            {
                "resolved_caption": tile["resolved_caption"],
                "direction": tile["direction"],
                "categories": tile["prompt_categories"],
            },
        )
        await self.run_to_end(
            self.manual(
                caption=WX,
                seed=77,
                count_per_batch=1,
                category_ids=[self.cat.id],
                prompts=[entry],
            )
        )
        await self.render_all()
        # An edit trains only once one of its renders is ✓.
        _, _, counts = self.build()
        self.assertEqual(counts.edit_pairs, 0)
        (edited,) = [r for r in self.db.list_t2i_images() if r["edited"]]
        self.assertIsNotNone(self.db.set_grade(edited["file_path"], "pass"))
        sft, prefs, counts = self.build()
        self.assertEqual(counts.edit_pairs, 1)
        (edit,) = [p for p in prefs if p.source == "edit"]
        self.assertEqual(
            (edit.chosen, edit.rejected), ("SYNTH tile, edited by hand", "SYNTH tile")
        )
        self.assertEqual(edit.user, user)

    async def test_a_typed_entry_stores_no_lineage_whatever_it_says(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.manual(
                caption=WX,
                count_per_batch=1,
                prompts=[
                    {
                        "prompt": "typed",
                        "negative": None,
                        "draft": None,
                        "writer_id": None,
                        "writer_model": None,
                        "variant_index": 0,
                        "resolved_caption": "claimed",
                        "direction": "claimed",
                        "categories": ["claimed"],
                    }
                ],
            )
        )
        await self.render_all()
        (row,) = _lineage(self.db)
        self.assertIsNone(row["resolved_caption"])
        self.assertIsNone(row["direction"])

    async def test_bad_lineage_is_a_request_error_without_its_text(self) -> None:
        from metascan.core.t2i_runner import T2iRequestError

        base = _entry("p", "p", "base", None, 0, {})
        for bad in (
            {"resolved_caption": 5},
            {"resolved_caption": "x" * 9000},
            {"resolved_caption": "ok", "direction": ["SECRETDIR"]},
            {"resolved_caption": "ok", "categories": "SECRETCAT"},
            {"resolved_caption": "ok", "categories": [""]},
            {"resolved_caption": "ok", "categories": ["c"] * 65},
        ):
            with self.subTest(bad=list(bad)):
                with self.assertRaises(T2iRequestError) as caught:
                    await self.runner.start_batch(
                        self.manual(caption=WX, prompts=[{**base, **bad}])
                    )
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertNotIn("xxxx", str(caught.exception))

    async def test_a_rebuild_failure_logs_the_exception_type_only(self) -> None:
        from unittest import mock

        self.roomy()
        entry = _entry("p", "p", "base", None, 0, {})  # an older client
        with mock.patch.object(
            self.library, "get", side_effect=RuntimeError("SECRET caption text")
        ), self.assertLogs("metascan.core.t2i_runner", "WARNING") as logs:
            await self.run_to_end(
                self.manual(caption=WX, count_per_batch=1, prompts=[entry])
            )
        text = "\n".join(logs.output)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SECRET", text)
