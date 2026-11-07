"""The structured writer for multi-character T2I captions.

Pure pieces (``t2i_characters.mark_caption``, ``t2i_structured``) and the
runner's use of them: which captions go structured, what the VLM is asked,
the retry, the prose fallback and the lineage.
"""

from __future__ import annotations

import json
import re
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from metascan.core import t2i_structured
from metascan.core.judgments import (
    DRAFT_WRITERS,
    MODEL_WRITERS,
    WRITER_STRUCTURED,
    writer_id_allowed,
)
from metascan.core.t2i_characters import (
    CastMember,
    MarkedCaption,
    mark_caption,
    resolve_caption,
)
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.utils.log_files import close_file_loggers
from metascan.core.t2i_runner import (
    STRUCTURED_LOG_NAME,
    WARN_STRUCTURED_NO_TASTE,
    BatchRequest,
)
from metascan.core.t2i_structured import StructuredError
from metascan.core.vlm_client import VlmError
from tests.test_t2i_runner import BatchCase, RunnerCase

MOSS = MODEL_PROFILES["moss"]
TWO = (
    "__ALICE__ and __BELLA__ sit on a park bench. __ALICE__ has long, wavy "
    "__HAIR__ and wears a red coat. __ALICE__ hands __BELLA__ a paper cup."
)


def member(
    name: str,
    hair: str,
    handle: Optional[str] = None,
    has_hair: bool = True,
    noun: str = "woman",
) -> CastMember:
    stem = hair.replace(" hair", "").replace(" ", "-")
    return CastMember(
        name=name,
        noun=noun,
        identity=f"a 30-year-old {noun}",
        traits=("fair skin", "grey eyes"),
        hair=hair,
        handle=handle or f"the {stem}-haired {noun}",
        handle_has_hair=has_hair,
    )


CAST = (member("ALICE", "auburn hair"), member("BELLA", "jet-black hair"))


def subject(sid: str, **over: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": sid,
        "position": "left" if sid == "ALICE" else "right",
        "hair": "",
        "features": [],
        "clothing": [f"a {'red' if sid == 'ALICE' else 'blue'} coat"],
        "garment_label": f"{'red' if sid == 'ALICE' else 'blue'} coat",
        "pose": "sits on the bench",
        "expression": "a soft smile",
    }
    out.update(over)
    return out


def reply(
    names: List[str], subjects: Optional[List[Dict[str, Any]]] = None, **over: Any
) -> str:
    data: Dict[str, Any] = {
        "scene": "A photograph of two women on a park bench, framed as a medium shot",
        "subjects": subjects if subjects is not None else [subject(n) for n in names],
        "interactions": ["{ALICE} hands {BELLA} a paper cup."],
        "background": ["Autumn trees stand behind the bench."],
        "camera": "Shot at eye level on a 50mm lens at f/2.8.",
        "lighting": "Soft afternoon sun from the left.",
        "colour": "Warm oranges against muted greens.",
        "textures": "Wool coats, weathered wood, paper.",
        "mood": "Calm and friendly.",
        "negative_extra": ["deformed hands", "merged bodies"],
        "unresolved": [],
    }
    data.update(over)
    return json.dumps(data)


class TestMarkCaption(RunnerCase):
    async def lib(self) -> Any:
        library, _ = self.library.get()
        return library

    async def test_mentions_become_markers_and_other_tokens_resolve(self) -> None:
        marked = mark_caption(TWO, 5, await self.lib())
        self.assertEqual([m.name for m in marked.cast], ["ALICE", "BELLA"])
        self.assertTrue(marked.text.startswith("{ALICE} and {BELLA} sit"))
        self.assertIn("{ALICE} hands {BELLA} a paper cup.", marked.text)
        self.assertNotIn("__", marked.text)
        # The spelled-out hair is the owner's drawn value, as resolve_caption
        # writes it.
        hair = marked.cast[0].hair
        assert hair is not None
        self.assertIn(f"long, wavy {hair}", marked.text)

    async def test_the_cast_is_the_resolved_captions_cast(self) -> None:
        library = await self.lib()
        marked = mark_caption(TWO, 5, library)
        resolved = resolve_caption(TWO, 5, "ref", library)
        for m in marked.cast:
            drawn = resolved.characters[m.name]
            self.assertEqual(m.hair, drawn["hair"])
            self.assertIn(drawn["age"], m.identity)
            self.assertIn(drawn["ethnicity"], m.identity)
            # The identity is the resolved caption's own first mention.
            self.assertIn(m.identity.split(" ", 1)[1], resolved.text)

    async def test_spelled_hair_is_not_a_trait_but_unspelled_hair_is(self) -> None:
        marked = mark_caption(TWO, 5, await self.lib())
        alice, bella = marked.cast
        self.assertNotIn(alice.hair, alice.traits)
        self.assertIn(bella.hair, bella.traits)

    async def test_a_caption_without_names_has_no_cast(self) -> None:
        library = await self.lib()
        self.assertEqual(mark_caption("a red kite", 1, library).cast, ())
        self.assertEqual(len(mark_caption("__ALICE__ waves.", 1, library).cast), 1)


class TestApplies(unittest.TestCase):
    def test_only_a_structured_profile_with_two_to_four_people(self) -> None:
        def marked(n: int) -> MarkedCaption:
            return MarkedCaption("x", tuple(CAST[:1] * n), [])

        self.assertTrue(MOSS.structured)
        self.assertFalse(t2i_structured.applies(MOSS, marked(1)))
        self.assertTrue(t2i_structured.applies(MOSS, marked(2)))
        self.assertTrue(t2i_structured.applies(MOSS, marked(4)))
        self.assertFalse(t2i_structured.applies(MOSS, marked(5)))
        self.assertFalse(t2i_structured.applies(MOSS, None))
        for other in ("krea2", "qwen", "sd", "zimage"):
            self.assertFalse(MODEL_PROFILES[other].structured)
            self.assertFalse(t2i_structured.applies(MODEL_PROFILES[other], marked(2)))


class TestComposeAndGrammar(unittest.TestCase):
    def test_the_user_turn_names_the_people_and_carries_direction_and_angle(
        self,
    ) -> None:
        marked = MarkedCaption("{ALICE} waves at {BELLA}.", CAST, [])
        system, user = t2i_structured.compose(
            marked, "default", "Make it rain.", "a low angle"
        )
        self.assertIn("{ALICE}", system)
        self.assertTrue(user.startswith("PEOPLE: {ALICE} a woman, {BELLA} a woman\n\n"))
        self.assertIn("DESCRIPTION:\n{ALICE} waves at {BELLA}.", user)
        self.assertIn("DIRECTION:\nMake it rain.", user)
        self.assertIn("ANGLE: lead with a low angle", user)
        self.assertTrue(user.endswith("Write the JSON now."))
        plain_system, plain_user = t2i_structured.compose(marked, "default")
        self.assertNotIn("DIRECTION", plain_user)
        self.assertNotIn("ANGLE", plain_user)
        sfw_system, _ = t2i_structured.compose(marked, "sfw")
        self.assertGreater(len(sfw_system), len(plain_system))

    def test_the_grammar_fixes_one_subject_per_person_in_order(self) -> None:
        g = t2i_structured.grammar(CAST)
        self.assertIn('"\\"ALICE\\""', g)
        self.assertIn('"\\"BELLA\\""', g)
        root = g.splitlines()[0]
        self.assertTrue(root.startswith("root ::= "))
        self.assertLess(root.index("subject0"), root.index("subject1"))
        for rule in (
            "subject0 ::=",
            "subject1 ::=",
            "position ::=",
            "strlist ::=",
            "string ::=",
        ):
            self.assertIn(rule, g)
        self.assertNotIn("subject2", g)
        # Every rule a line refers to outside its quoted literals is defined.
        defined = {line.split(" ::= ")[0] for line in g.splitlines() if " ::= " in line}
        for line in g.splitlines():
            if " ::= " not in line:
                continue
            body = re.sub(
                r'"(?:\\.|[^"\\])*"|\[(?:\\.|[^\]\\])*\]',
                " ",
                line.split(" ::= ", 1)[1],
            )
            for name in re.findall(r"\b[a-z][a-z0-9]*\b", body):
                self.assertIn(name, defined, line)

    def test_no_parentheses_in_the_system_prompt(self) -> None:
        system, _ = t2i_structured.compose(MarkedCaption("x", CAST, []), "default")
        self.assertNotIn("(", system)
        self.assertNotIn(")", system)


class TestParse(unittest.TestCase):
    def test_a_good_reply_parses_clean(self) -> None:
        data = t2i_structured.parse(reply(["ALICE", "BELLA"]), CAST)
        self.assertEqual([s["id"] for s in data["subjects"]], ["ALICE", "BELLA"])
        self.assertEqual(data["interactions"], ["{ALICE} hands {BELLA} a paper cup."])

    def test_unusable_replies_raise(self) -> None:
        bad = {
            "not json": "A prose prompt.",
            "not an object": "[1, 2]",
            "too few people": reply(["ALICE"]),
            "wrong person": reply([], subjects=[subject("ALICE"), subject("CLARA")]),
            "no scene": reply(["ALICE", "BELLA"], scene=" "),
            "unknown name": reply(["ALICE", "BELLA"], interactions=["{CLARA} waves."]),
        }
        for label, raw in bad.items():
            with self.subTest(label), self.assertRaises(StructuredError):
                t2i_structured.parse(raw, CAST)

    def test_an_unknown_position_becomes_center(self) -> None:
        raw = reply([], subjects=[subject("ALICE", position="up"), subject("BELLA")])
        self.assertEqual(
            t2i_structured.parse(raw, CAST)["subjects"][0]["position"], "center"
        )


class TestCheck(unittest.TestCase):
    def problems(self, raw: str, cast: Any = CAST) -> List[str]:
        return t2i_structured.check(t2i_structured.parse(raw, cast), cast)

    def test_a_clean_reply_has_no_problems(self) -> None:
        self.assertEqual(self.problems(reply(["ALICE", "BELLA"])), [])

    def test_each_problem_is_named(self) -> None:
        cases = {
            "hedges": reply(
                [],
                subjects=[
                    subject("ALICE", clothing=["fabric resembling a sash"]),
                    subject("BELLA"),
                ],
            ),
            "involves another person": reply(
                [],
                subjects=[
                    subject("ALICE", pose="holds {BELLA}'s hand"),
                    subject("BELLA"),
                ],
            ),
            "uses she, he": reply(
                ["ALICE", "BELLA"], interactions=["She hands {BELLA} a cup."]
            ),
            "lighting mentions a person": reply(
                ["ALICE", "BELLA"], lighting="Sun on {ALICE}."
            ),
            "same position": reply(
                [],
                subjects=[
                    subject("ALICE", position="left"),
                    subject("BELLA", position="left"),
                ],
            ),
        }
        for needle, raw in cases.items():
            with self.subTest(needle):
                self.assertTrue(
                    any(needle in p for p in self.problems(raw)), self.problems(raw)
                )

    def test_an_actors_own_possessive_in_an_interaction_is_fine(self) -> None:
        ok = reply(
            ["ALICE", "BELLA"],
            interactions=["{ALICE} wraps her right arm around {BELLA}'s shoulder."],
        )
        self.assertEqual(self.problems(ok), [])
        for bad in ("{ALICE} hands her a cup.", "{ALICE} hugs him.", "He waves."):
            with self.subTest(bad):
                raw = reply(["ALICE", "BELLA"], interactions=[bad])
                self.assertTrue(any("uses she, he" in p for p in self.problems(raw)))
        # A possessive in the background is fine; a subject pronoun is not.
        coat = reply(["ALICE", "BELLA"], background=["Her coat hangs on a hook."])
        self.assertEqual(self.problems(coat), [])
        scene = reply(["ALICE", "BELLA"], background=["She leans on a tree."])
        self.assertTrue(any("background uses" in p for p in self.problems(scene)))

    def test_words_the_reply_dropped_are_listed(self) -> None:
        description = (
            "{ALICE} sits on a park bench, watching {BELLA}. {ALICE} wears a red coat."
        )
        lost = reply(["ALICE", "BELLA"])
        problems = t2i_structured.check(
            t2i_structured.parse(lost, CAST), CAST, description
        )
        (missing,) = [p for p in problems if "missing" in p]
        self.assertIn("'watching'", missing)
        for kept in ("'park'", "'bench'", "'wears'", "'coat'"):
            self.assertNotIn(kept, missing)
        # A word kept in another form ("watches") counts as kept.
        kept = reply(["ALICE", "BELLA"], interactions=["{ALICE} watches {BELLA}."])
        data = t2i_structured.parse(kept, CAST)
        self.assertEqual(t2i_structured.missing_words(data, CAST, description), [])
        # A typo in the caption is kept by its right spelling.
        spelled = t2i_structured.parse(
            reply(["ALICE", "BELLA"], interactions=["{ALICE} is watching {BELLA}."]),
            CAST,
        )
        self.assertEqual(
            t2i_structured.missing_words(
                spelled, CAST, "{ALICE} is vwatching {BELLA}."
            ),
            [],
        )
        # The cast's draws are the renderer's: a trait in the text is kept.
        self.assertEqual(
            t2i_structured.missing_words(data, CAST, "{ALICE} has grey eyes."), []
        )

    def test_garment_labels_are_checked_only_when_handles_need_them(self) -> None:
        same = reply(
            [],
            subjects=[
                subject("ALICE", garment_label="coat"),
                subject("BELLA", garment_label="coat"),
            ],
        )
        self.assertEqual(self.problems(same), [])
        blondes = (
            member("ALICE", "honey blonde hair"),
            member("BELLA", "platinum blonde hair"),
        )
        self.assertTrue(any("garment_label" in p for p in self.problems(same, blondes)))


class TestRender(unittest.TestCase):
    def render(self, raw: str, cast: Any = CAST) -> str:
        prompt, _ = t2i_structured.render(t2i_structured.parse(raw, cast), cast, MOSS)
        return prompt

    def test_the_prompt_reads_scene_people_interactions_rest(self) -> None:
        prompt = self.render(reply(["ALICE", "BELLA"]))
        self.assertTrue(prompt.startswith("A photograph of two women on a park bench"))
        alice = prompt.index(
            "On the left, the auburn-haired woman is a 30-year-old woman"
        )
        bella = prompt.index(
            "On the right, the jet-black-haired woman is a 30-year-old woman"
        )
        hands = prompt.index(
            "The auburn-haired woman hands the jet-black-haired woman a paper cup."
        )
        self.assertLess(alice, bella)
        self.assertLess(bella, hands)
        self.assertLess(hands, prompt.index("Autumn trees"))
        self.assertLess(prompt.index("Autumn trees"), prompt.index("Shot at eye level"))
        self.assertIn("with fair skin and grey eyes.", prompt)
        self.assertIn("She wears a red coat.", prompt)
        self.assertIn("She has a soft smile.", prompt)
        self.assertNotIn("{", prompt)

    def test_a_pose_that_repeats_an_interaction_is_left_out(self) -> None:
        raw = reply(
            [],
            subjects=[subject("ALICE", pose="hands {BELLA} a cup"), subject("BELLA")],
        )
        prompt = self.render(raw)
        self.assertNotIn("She hands", prompt)
        alone = reply(
            [],
            subjects=[subject("ALICE", pose="hands {BELLA} a cup"), subject("BELLA")],
            interactions=[],
        )
        self.assertIn("She hands the jet-black-haired woman a cup.", self.render(alone))

    def test_the_spelled_hair_replaces_the_drawn_value(self) -> None:
        cast = (
            CastMember(
                "ALICE",
                "woman",
                "a 30-year-old woman",
                ("fair skin", "auburn hair"),
                "auburn hair",
                "the auburn-haired woman",
                True,
            ),
            CAST[1],
        )
        raw = reply(
            [],
            subjects=[
                subject("ALICE", hair="long, wavy auburn hair"),
                subject("BELLA"),
            ],
        )
        prompt = self.render(raw, cast)
        self.assertIn("with fair skin and long, wavy auburn hair.", prompt)
        self.assertEqual(prompt.count("auburn hair"), 1)

    def test_similar_hair_names_people_by_garment_then_position(self) -> None:
        blondes = (
            member("ALICE", "honey blonde hair"),
            member("BELLA", "platinum blonde hair"),
        )
        prompt = self.render(reply(["ALICE", "BELLA"]), blondes)
        self.assertIn(
            "the woman in the red coat hands the woman in the blue coat", prompt.lower()
        )
        same = reply(
            [],
            subjects=[
                subject("ALICE", garment_label="coat"),
                subject("BELLA", garment_label="coat"),
            ],
        )
        self.assertIn(". The woman on the left is", self.render(same, blondes))
        # A shared hair value already gives the bare noun as the handle.
        shared = (
            member("ALICE", "auburn hair", "the woman", False),
            member("BELLA", "auburn hair", "the woman", False),
        )
        self.assertIn(
            "the woman in the red coat", self.render(reply(["ALICE", "BELLA"]), shared)
        )

    def test_a_man_and_a_woman_are_named_by_their_nouns(self) -> None:
        # Two brown-haired people with no clothing: the nouns tell them apart.
        cast = (
            member("ADAM", "chestnut brown hair", noun="man"),
            member("ALICE", "espresso brown hair"),
        )
        raw = reply(
            [],
            subjects=[
                subject(
                    "ADAM",
                    position="center",
                    clothing=[],
                    garment_label="",
                    pose="sitting on his bed",
                ),
                subject(
                    "ALICE",
                    position="back",
                    clothing=[],
                    garment_label="",
                    pose="stands in the doorway",
                ),
            ],
            interactions=["{ALICE} watches {ADAM}."],
        )
        data = t2i_structured.parse(raw, cast)
        self.assertEqual(t2i_structured.check(data, cast), [])
        prompt, _ = t2i_structured.render(data, cast, MOSS)
        self.assertIn("In the center, the man is a 30-year-old man", prompt)
        self.assertIn("At the back, the woman is a 30-year-old woman", prompt)
        self.assertIn("He is sitting on his bed.", prompt)
        self.assertIn("She stands in the doorway.", prompt)
        self.assertIn("The woman watches the man.", prompt)

    def test_position_handles_read_as_english(self) -> None:
        blondes = (
            member("ALICE", "honey blonde hair"),
            member("BELLA", "platinum blonde hair"),
        )
        raw = reply(
            [],
            subjects=[
                subject("ALICE", position="center", clothing=[], garment_label=""),
                subject("BELLA", position="back", clothing=[], garment_label=""),
            ],
        )
        prompt = self.render(raw, blondes)
        self.assertIn(". The woman in the center is a 30-year-old woman", prompt)
        self.assertIn(". The woman at the back is a 30-year-old woman", prompt)
        self.assertNotIn("In the center, the woman", prompt)

    def test_the_negative_is_the_stock_one_plus_new_extras(self) -> None:
        data = t2i_structured.parse(
            reply(
                ["ALICE", "BELLA"],
                negative_extra=["watermark", "deformed hands", "a", "b", "c"],
            ),
            CAST,
        )
        _, negative = t2i_structured.render(data, CAST, MOSS)
        assert negative is not None
        self.assertTrue(
            negative.startswith("worst quality, bad anatomy, watermark, jpeg artifact")
        )
        self.assertEqual(negative.count("watermark"), 1)
        self.assertIn("deformed hands", negative)
        _, none = t2i_structured.render(data, CAST, MODEL_PROFILES["krea2"])
        self.assertIsNone(none)

    def test_parentheses_never_reach_the_prompt(self) -> None:
        prompt = self.render(reply(["ALICE", "BELLA"], mood="Calm (and friendly)."))
        self.assertNotIn("(", prompt)


class TestWriterId(unittest.TestCase):
    def test_structured_is_a_draft_and_model_writer(self) -> None:
        self.assertIn(WRITER_STRUCTURED, DRAFT_WRITERS)
        self.assertIn(WRITER_STRUCTURED, MODEL_WRITERS)
        self.assertTrue(writer_id_allowed(WRITER_STRUCTURED))


class StructuredRunnerCase(RunnerCase):
    def names(self) -> List[str]:
        library, _ = self.library.get()
        return [m.name for m in mark_caption(TWO, 7, library).cast]

    def good(self, seed: int = 7, **over: Any) -> Dict[str, Any]:
        """A subject for ALICE that keeps everything TWO says about her."""
        library, _ = self.library.get()
        hair = mark_caption(TWO, seed, library).cast[0].hair
        fields: Dict[str, Any] = dict(hair=f"long, wavy {hair}")
        fields.update(over)
        return subject("ALICE", **fields)

    def good_reply(self, seed: int = 7, **over: Any) -> str:
        return reply([], subjects=[self.good(seed, **over), subject("BELLA")])


class TestGeneratePromptStructured(StructuredRunnerCase):
    async def test_a_two_character_moss_caption_is_written_structured(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.append(self.good_reply())
        result = await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        (call,) = self.vlm.calls
        self.assertIn("grammar", call)
        self.assertIn('"\\"ALICE\\""', call["grammar"])
        self.assertTrue(
            call["user_prompt"].startswith("PEOPLE: {ALICE} a woman, {BELLA} a woman")
        )
        self.assertEqual(call["max_tokens"], t2i_structured.MAX_TOKENS)
        (draft,) = result.drafts
        self.assertEqual(draft.writer_id, WRITER_STRUCTURED)
        self.assertEqual(draft.writer_model, "qwen3vl-8b")
        library, _ = self.library.get()
        for m in mark_caption(TWO, 7, library).cast:
            self.assertIn(m.identity, result.prompt)
        self.assertNotIn("{", result.prompt)
        assert result.negative is not None
        self.assertIn("deformed hands", result.negative)
        self.assertEqual(result.warnings, [])
        # The lineage keeps the resolved caption, as for any writer.
        self.assertEqual(result.resolved_caption, self.expected_text(TWO, 7, "ref"))

    async def test_problems_are_sent_back_once_and_the_better_reply_wins(self) -> None:
        assert self.vlm is not None
        names = self.names()
        hedged = reply(
            [],
            subjects=[
                self.good(clothing=["fabric resembling a sash"]),
                subject(names[1]),
            ],
        )
        self.vlm.replies.extend([hedged, self.good_reply()])
        result = await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        self.assertEqual(len(self.vlm.calls), 2)
        retry = self.vlm.calls[1]["user_prompt"]
        self.assertIn("Your previous JSON:", retry)
        self.assertIn("'resembling'", retry)
        self.assertNotIn("resembling", result.prompt)
        self.assertEqual(result.warnings, [])

    async def test_problems_left_after_the_retry_are_a_warning(self) -> None:
        assert self.vlm is not None
        names = self.names()
        hedged = reply(
            [],
            subjects=[
                self.good(clothing=["fabric resembling a sash"]),
                subject(names[1]),
            ],
        )
        self.vlm.replies.extend([hedged, hedged])
        result = await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        self.assertEqual(result.drafts[0].writer_id, WRITER_STRUCTURED)
        self.assertEqual(
            result.warnings, ["structured prompt: 1 check(s) still failing"]
        )

    async def test_an_unusable_reply_falls_back_to_prose(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.extend(
            ["not json at all", "A prose prompt.\n\nNegative: blurry"]
        )
        result = await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        self.assertEqual(len(self.vlm.calls), 2)
        self.assertNotIn("grammar", self.vlm.calls[1])
        self.assertEqual(result.prompt, "A prose prompt.")
        self.assertEqual(result.drafts[0].writer_id, "base")
        self.assertEqual(
            result.warnings,
            [
                "structured prompt unavailable (the reply is not JSON) - the prompt was written as prose"
            ],
        )

    async def test_other_captions_and_models_stay_prose(self) -> None:
        assert self.vlm is not None
        cases = [("__ALICE__ waves.", "moss"), (TWO, "krea2"), ("a red kite", "moss")]
        for caption, model in cases:
            with self.subTest(caption=caption, model=model):
                self.vlm.calls.clear()
                result = await self.runner.generate_prompt(
                    caption=caption, seed=7, model=model
                )
                self.assertNotIn("grammar", self.vlm.calls[0])
                self.assertEqual(result.drafts[0].writer_id, "base")

    async def test_taste_examples_are_not_fetched_for_a_structured_caption(
        self,
    ) -> None:
        assert self.vlm is not None
        fetch = mock.AsyncMock(return_value=(["a passed prompt"], None))
        self.runner.taste_examples = fetch
        self.vlm.replies.append(self.good_reply())
        result = await self.runner.generate_prompt(
            caption=TWO, seed=7, model="moss", writer="base+taste"
        )
        fetch.assert_not_called()
        self.assertEqual(result.drafts[0].writer_id, WRITER_STRUCTURED)
        self.assertIn(WARN_STRUCTURED_NO_TASTE, result.warnings)

    async def test_each_variant_gets_its_angle(self) -> None:
        assert self.vlm is not None
        self.vlm.default_reply = self.good_reply()
        result = await self.runner.generate_prompt(
            caption=TWO, seed=7, model="moss", variants=2
        )
        self.assertEqual([d.writer_id for d in result.drafts], [WRITER_STRUCTURED] * 2)
        turns = sorted(str(c["user_prompt"]) for c in self.vlm.calls)
        self.assertEqual(sum("ANGLE: lead with" in t for t in turns), 1)


class TestStructuredJsonLog(StructuredRunnerCase):
    def log_path(self) -> Any:
        directory = self.root / "logs"
        self.runner.structured_log_dir = directory
        path = directory / STRUCTURED_LOG_NAME
        self.addCleanup(close_file_loggers, path)
        return path

    def entries(self, path: Any) -> List[Dict[str, Any]]:
        close_file_loggers(path)  # flush and release the file (Windows)
        text = path.read_text(encoding="utf-8")
        decoder = json.JSONDecoder()
        out, at = [], 0
        while at < len(text):
            if text[at].isspace():
                at += 1
                continue
            obj, at = decoder.raw_decode(text, at)
            out.append(obj)
        return out

    async def test_off_by_default_nothing_is_written(self) -> None:
        assert self.vlm is not None
        path = self.log_path()
        self.assertFalse(self.cfg["structured_json_log"])
        self.vlm.replies.append(self.good_reply())
        await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        self.assertFalse(path.exists())

    async def test_each_structured_prompt_is_one_pretty_entry(self) -> None:
        assert self.vlm is not None
        path = self.log_path()
        self.cfg["structured_json_log"] = True
        self.vlm.replies.append(self.good_reply())
        await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        (entry,) = self.entries(path)
        self.assertEqual(entry["caption"], TWO)
        self.assertEqual(entry["writer_model"], "qwen3vl-8b")
        self.assertEqual(
            [x["id"] for x in entry["json"]["subjects"]], ["ALICE", "BELLA"]
        )
        self.assertEqual(entry["checks_failing"], [])
        self.assertNotIn("retry", entry)
        self.assertRegex(entry["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]")
        # Pretty-printed: one key per line, indented.
        self.assertIn('\n  "caption": ', path.read_text(encoding="utf-8"))

    async def test_a_retry_logs_the_first_reply_and_its_problems(self) -> None:
        assert self.vlm is not None
        path = self.log_path()
        self.cfg["structured_json_log"] = True
        hedged = reply(
            [],
            subjects=[
                self.good(clothing=["fabric resembling a sash"]),
                subject("BELLA"),
            ],
        )
        self.vlm.replies.extend([hedged, self.good_reply()])
        await self.runner.generate_prompt(caption=TWO, seed=7, model="moss")
        (entry,) = self.entries(path)
        self.assertEqual(
            entry["retry"]["first_json"]["subjects"][0]["clothing"],
            ["fabric resembling a sash"],
        )
        self.assertTrue(
            any("resembling" in p for p in entry["retry"]["first_problems"])
        )
        self.assertEqual(entry["checks_failing"], [])

    async def test_a_failed_write_never_fails_the_prompt(self) -> None:
        assert self.vlm is not None
        self.log_path()
        self.cfg["structured_json_log"] = True
        self.vlm.replies.append(self.good_reply())
        with mock.patch(
            "metascan.core.t2i_runner.get_file_logger", side_effect=OSError("disk full")
        ):
            result = await self.runner.generate_prompt(
                caption=TWO, seed=7, model="moss"
            )
        self.assertEqual(result.drafts[0].writer_id, WRITER_STRUCTURED)

    async def test_prose_prompts_are_never_logged(self) -> None:
        assert self.vlm is not None
        path = self.log_path()
        self.cfg["structured_json_log"] = True
        await self.runner.generate_prompt(
            caption="__ALICE__ waves.", seed=7, model="moss"
        )
        self.assertFalse(path.exists())


class TestBatchStructured(BatchCase):
    def named(self, **over: Any) -> BatchRequest:
        fields: Dict[str, Any] = dict(
            caption=TWO,
            aspect_ratio="2:3",
            model="moss",
            batch_size=1,
            count_per_batch=1,
        )
        fields.update(over)
        return self.random_mode(**fields)

    def cast_names(self) -> List[str]:
        library, _ = self.library.get()
        return [m.name for m in mark_caption(TWO, 10, library).cast]

    async def test_a_batch_step_is_written_structured(self) -> None:
        assert self.vlm is not None
        library, _ = self.library.get()
        hair = mark_caption(TWO, 10, library).cast[0].hair
        good = subject("ALICE", hair=f"long, wavy {hair}")
        self.vlm.replies.append(reply([], subjects=[good, subject("BELLA")]))
        batch_id = await self.run_to_end(self.named())
        (step,) = self.written(batch_id)
        self.assertEqual(step["writer_id"], WRITER_STRUCTURED)
        self.assertEqual(step["writer_model"], "qwen3vl-8b")
        (params,) = self.params()
        self.assertEqual(params.positive, step["prompt"])
        self.assertIn("grammar", self.vlm.calls[0])

    async def test_a_vlm_failure_writes_the_step_as_prose(self) -> None:
        assert self.vlm is not None
        self.vlm.replies.extend(
            [VlmError("boom"), "A prose prompt.\n\nNegative: blurry"]
        )
        batch_id = await self.run_to_end(self.named())
        (step,) = self.written(batch_id)
        self.assertEqual(step["writer_id"], "base")
        self.assertEqual(step["prompt"], "A prose prompt.")
        self.assertTrue(any("written as prose" in w for w in step["warnings"]))


class TestCaptionTokens(RunnerCase):
    async def test_the_tokens_list_characters_slots_and_wildcards(self) -> None:
        tokens = (await self.runner.caption_tokens())["tokens"]
        by_token = {t["token"]: t for t in tokens}
        self.assertEqual(by_token["__ALICE__"]["kind"], "character")
        self.assertEqual(by_token["__ALICE__"]["note"], "woman")
        self.assertEqual(by_token["__ADAM__"]["note"], "man")
        self.assertEqual(by_token["__HAIR__"]["kind"], "slot")
        for slot in ("AGE", "SKIN", "EYES"):
            self.assertNotIn(f"__{slot}__", by_token)
        for t in tokens:
            self.assertRegex(t["token"], r"^__[A-Z][A-Z0-9]*__$")


if __name__ == "__main__":
    unittest.main()
