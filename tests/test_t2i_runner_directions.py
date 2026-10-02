"""Caption directions through T2iRunner: Manual prompts and Random steps.

Reuses the runner fixtures; the captions CSV here carries hand-written
classification columns, and the snippet lists are written by the test.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from unittest import mock

from metascan.core.t2i_directions import SnippetCache
from tests.test_t2i_runner import BatchCase

COLUMNS = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
    "Caption SHA1",
    "Emotion",
    "Emotion Explicit",
    "Kiss",
    "Partner",
    "Act",
    "Act P",
    "Act Conflict",
    "Issues",
]
ACT_CAPTION = "__ALICE__ is on all fours on the bed."
PLAIN_CAPTION = "__ALICE__ laughs at the window."


def _row(caption: str, act: str, act_p: str, explicit: str) -> list:
    digest = hashlib.sha1(caption.encode("utf-8")).hexdigest()
    return [
        caption,
        "1:1",
        "full",
        "0.5",
        "0.2",
        "0.9",
        "1",
        "1",
        "[]",
        digest,
        "none",
        explicit,
        "0.0",
        "none",
        act,
        act_p,
        "false",
        "",
    ]


class DirectionCase(BatchCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COLUMNS)
            w.writerow(_row(ACT_CAPTION, "doggy", "0.9500", "0.0500"))
            w.writerow(_row(PLAIN_CAPTION, "none-artistic", "0.9900", "0.9900"))
        snippet_dir = Path(self.root) / "directions"
        snippet_dir.mkdir()
        (snippet_dir / "act.doggy.txt").write_text(
            "he takes her from behind\n", encoding="utf-8"
        )
        (snippet_dir / "emotion.txt").write_text(
            "a soft unguarded smile\n", encoding="utf-8"
        )
        self.runner.directions = SnippetCache(snippet_dir)

    def user_turns(self) -> list:
        assert self.vlm is not None
        return [call["user_prompt"] for call in self.vlm.calls]


class TestManualPromptDirections(DirectionCase):
    async def test_a_classified_caption_gets_a_direction_block(self) -> None:
        result = await self.runner.generate_prompt(
            caption=ACT_CAPTION, seed=5, model="krea2"
        )
        (user,) = self.user_turns()
        self.assertIn(
            "\n\nDIRECTION:\nHe takes her from behind. A soft unguarded smile.\n\n",
            user,
        )
        self.assertEqual(
            result.direction, "He takes her from behind. A soft unguarded smile."
        )
        self.assertEqual(result.direction_parts, ["act:doggy", "emotion"])

    async def test_below_threshold_or_turned_off_the_prompt_is_unchanged(self) -> None:
        for caption, flag in ((PLAIN_CAPTION, None), (ACT_CAPTION, False)):
            with self.subTest(caption=caption, flag=flag):
                assert self.vlm is not None
                self.vlm.calls.clear()
                result = await self.runner.generate_prompt(
                    caption=caption, seed=5, model="krea2", directions=flag
                )
                (user,) = self.user_turns()
                self.assertNotIn("DIRECTION", user)
                self.assertIsNone(result.direction)

    async def test_the_config_switch_is_the_default(self) -> None:
        self.cfg["directions"] = {**self.cfg["directions"], "enabled": False}
        await self.runner.generate_prompt(caption=ACT_CAPTION, seed=5, model="krea2")
        self.assertNotIn("DIRECTION", self.user_turns()[0])

    async def test_a_failing_lookup_gives_no_direction_and_a_warning(self) -> None:
        with mock.patch.object(self.captions, "find", side_effect=OSError("gone")):
            result = await self.runner.generate_prompt(
                caption=ACT_CAPTION, seed=5, model="krea2"
            )
        self.assertNotIn("DIRECTION", self.user_turns()[0])
        self.assertTrue(any("direction" in w for w in result.warnings))

    async def test_without_a_vlm_the_fallback_carries_the_direction(self) -> None:
        self.vlm = None
        result = await self.runner.generate_prompt(
            caption=ACT_CAPTION, seed=5, model="krea2"
        )
        self.assertTrue(
            result.prompt.endswith("He takes her from behind. A soft unguarded smile.")
        )


class TestRandomStepDirections(DirectionCase):
    async def test_a_drawn_step_carries_and_reports_its_direction(self) -> None:
        self.roomy()
        batch_id = await self.run_to_end(
            self.random_mode(batch_size=2, count_per_batch=1)
        )
        steps = {s["caption"]: s for s in self.written(batch_id)}
        self.assertEqual(
            steps[ACT_CAPTION]["direction_parts"], ["act:doggy", "emotion"]
        )
        self.assertIsNone(steps[PLAIN_CAPTION]["direction"])
        directed = [u for u in self.user_turns() if "DIRECTION" in u]
        self.assertEqual(len(directed), 1)
        frames = self.frames("batch_step")
        self.assertTrue(all("direction" in f for f in frames))

    async def test_directions_false_turns_them_off_for_the_batch(self) -> None:
        self.roomy()
        await self.run_to_end(
            self.random_mode(batch_size=2, count_per_batch=1, directions=False)
        )
        self.assertFalse(any("DIRECTION" in u for u in self.user_turns()))


class TestSnippetWarnings(DirectionCase):
    async def test_a_rejected_snippet_line_is_reported_on_the_prompt(self) -> None:
        emotion = Path(self.root) / "directions" / "emotion.txt"
        emotion.write_text("a soft unguarded smile\na grin (wide)\n", encoding="utf-8")
        result = await self.runner.generate_prompt(
            caption=ACT_CAPTION, seed=5, model="krea2"
        )
        self.assertTrue(any("emotion.txt:2" in w for w in result.warnings))
        self.assertEqual(result.direction_parts, ["act:doggy", "emotion"])
