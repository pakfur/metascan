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

    def judge(self, favorite: str, seen: str) -> None:
        self.db.set_verdict(favorite, "favorite")
        self.db.mark_seen(seen)

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
