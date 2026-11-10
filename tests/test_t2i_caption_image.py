"""T2iRunner.caption_image: the Upscale dialog's "Write a prompt from the
image" captions the image, then generate_prompt rewrites the caption."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from PIL import Image

from metascan.core.t2i_runner import (
    CAPTION_MAX_TOKENS,
    CAPTION_USER_PROMPT,
    T2iRequestError,
    T2iUnavailableError,
    plain_caption,
)
from metascan.core.vlm_client import VlmError
from tests.test_t2i_runner import RunnerCase


def test_plain_caption_strips_the_caption_engines_markup():
    raw = '"A woman (smiling) reads {ALICE} by a __HAIR__ lamp.\n\n  Rain falls. "'
    # A marker keeps its word: only the markup goes.
    assert (
        plain_caption(raw) == "A woman smiling reads ALICE by a HAIR lamp. Rain falls."
    )


def test_plain_caption_of_nothing_is_empty():
    assert plain_caption("") == ""
    assert plain_caption("  \n ") == ""
    assert plain_caption("__ {} ()") == ""


class CaptionImageCase(RunnerCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        assert self.vlm is not None
        # The real client tells images apart by extension.
        self.vlm.is_image_path = lambda p: Path(p).suffix.lower() in (  # type: ignore[attr-defined]
            ".png",
            ".jpg",
        )
        self.image = self.root / "lake.png"
        Image.new("RGB", (8, 8), "blue").save(self.image)

    async def test_the_vlm_sees_the_image_and_its_reply_is_made_plain(self):
        assert self.vlm is not None
        self.vlm.replies.append("A lake (at dusk) under {ALICE}'s sky.")
        caption, model = await self.runner.caption_image(self.image)
        self.assertEqual(caption, "A lake at dusk under ALICE's sky.")
        self.assertEqual(model, "qwen3vl-8b")
        call = self.vlm.calls[-1]
        self.assertEqual(call["image_path"], self.image)
        self.assertEqual(call["user_prompt"], CAPTION_USER_PROMPT)
        self.assertEqual(call["max_tokens"], CAPTION_MAX_TOKENS)
        self.assertIn("describe it as a caption", call["system_prompt"])

    async def test_a_caption_then_feeds_the_moss_prompt_flow(self):
        assert self.vlm is not None
        self.vlm.replies.extend(
            [
                "A woman in her thirties reads on a porch at dusk.",
                "A photograph of a woman reading.\n\nNegative: blurry",
            ]
        )
        caption, _ = await self.runner.caption_image(self.image)
        result = await self.runner.generate_prompt(
            caption=caption, seed=0, model="moss", directions=False
        )
        self.assertEqual(result.prompt, "A photograph of a woman reading.")
        self.assertEqual(result.negative, "blurry")
        rewrite = self.vlm.calls[-1]
        self.assertNotIn("image_path", rewrite)  # the rewrite reads the caption
        self.assertIn(caption, rewrite["user_prompt"])

    async def test_no_writer_is_unavailable(self):
        self.vlm = None
        with self.assertRaises(T2iUnavailableError):
            await self.runner.caption_image(self.image)

    async def test_a_writer_that_is_not_installed_is_unavailable(self):
        self.runner.vlm_installed = lambda model_id: False
        with self.assertRaises(T2iUnavailableError) as ctx:
            await self.runner.caption_image(self.image)
        self.assertNotIn("used the resolved caption", str(ctx.exception))

    async def test_a_video_or_a_missing_file_is_refused(self):
        with self.assertRaises(T2iRequestError):
            await self.runner.caption_image(self.root / "clip.mp4")
        with self.assertRaises(T2iRequestError):
            await self.runner.caption_image(self.root / "gone.png")

    async def test_an_empty_reply_or_a_dead_vlm_is_a_vlm_error(self):
        assert self.vlm is not None
        self.vlm.replies.append("  ( ) ")
        with self.assertRaises(VlmError):
            await self.runner.caption_image(self.image)
        self.vlm.replies.append(ConnectionResetError("gone"))
        with self.assertRaises(VlmError):
            await self.runner.caption_image(self.image)

    async def test_the_caption_is_never_logged(self):
        assert self.vlm is not None
        secret = "A unique lighthouse keeper named nobody."
        self.vlm.replies.append(secret)
        with mock.patch("metascan.core.t2i_runner.logger") as log:
            await self.runner.caption_image(self.image)
        for call in log.method_calls:
            self.assertNotIn(secret, repr(call))
