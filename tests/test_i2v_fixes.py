"""Pure tests for the i2v prompt rewrites (no model call): natural camera
phrasing -> the MiniMax guide's motion vocabulary, and quoted speech ->
the guide's `(S1) says: <d>[Language] ...</d>` form."""

import unittest

from metascan.core import i2v_compiler as c
from metascan.core.i2v_fixes import (
    apply_i2v_fixes,
    camera_findings,
    lint_i2v_report,
    rewrite_camera_fragment,
    speech_findings,
)


def _fixed(text: str) -> str:
    return apply_i2v_fixes(text, camera_findings(text) + speech_findings(text))


class TestCameraFragment(unittest.TestCase):
    """rewrite_camera_fragment takes what follows 'The camera ' (no final
    period) and returns the guide phrasing, or None when it cannot tell."""

    CASES = {
        # leading adverbs move behind the verb as guide modifiers
        "slowly zooms in on her face": "zooms in at slow speed on her face",
        "quickly pans left": "pans left at fast speed",
        "slightly pushes in": "pushes in with small amplitude",
        "slowly and slightly tilts up": "tilts up with small amplitude at slow speed",
        "gently zooms out": "zooms out with small amplitude",
        # push in / pull out synonyms
        "dollies in": "pushes in",
        "dollies in on the letter": "pushes in toward the letter",
        "moves closer to her face": "pushes in toward her face",
        "slowly moves in": "pushes in at slow speed",
        "moves toward the window": "pushes in toward the window",
        "dollies out": "pulls out",
        "pulls back": "pulls out",
        "slowly pulls away from the table": "pulls out at slow speed from the table",
        "moves backward": "pulls out",
        # zoom
        "zooms closer": "zooms in",
        "punches in on the ring": "zooms in on the ring",
        # pan / tilt / truck / pedestal
        "pans to the right": "pans right",
        "sweeps left across the room": "pans left across the room",
        "angles upward": "tilts up",
        "looks down at the floor": "tilts down at the floor",
        "slides left": "trucks left",
        "glides to the right": "trucks right",
        "rises": "pedestals up",
        "cranes up over the crowd": "pedestals up over the crowd",
        "slowly lowers": "pedestals down at slow speed",
        "descends": "pedestals down",
        # arc / tracking drop the object: the guide phrase names "the subject"
        "orbits the woman": "arcs around the subject",
        "circles around her slowly": "arcs around the subject at slow speed",
        "follows her as she walks": "tracks the subject as she walks",
        "tracks the dog, keeping it centered": "tracks the subject, keeping it centered",
        "stays with him": "tracks the subject",
        # static
        "stays still": "holds a static shot",
        "remains fixed on the doorway": "holds a static shot on the doorway",
        "is locked off": "holds a static shot",
        "does not move": "holds a static shot",
        "holds steady": "holds a static shot",
        # shake / roll
        "shakes violently": "shakes strongly",
        "trembles": "shakes slightly",
        "is handheld": "shakes slightly",
        "rolls to the right": "rolls clockwise",
        "rotates counterclockwise": "rolls counterclockwise",
    }

    def test_rewrites(self):
        for frag, want in self.CASES.items():
            self.assertEqual(rewrite_camera_fragment(frag), want, frag)

    def test_every_rewrite_passes_the_lint(self):
        """The point of a fix: its output must be what the lint accepts."""
        for frag in self.CASES:
            sentence = f"The camera {rewrite_camera_fragment(frag)}."
            self.assertEqual(camera_findings(sentence), [], sentence)

    def test_static_drops_motion_modifiers(self):
        self.assertEqual(
            rewrite_camera_fragment("slowly stays still"), "holds a static shot"
        )

    def test_existing_modifiers_are_not_duplicated(self):
        self.assertEqual(
            rewrite_camera_fragment("slowly dollies in at slow speed"),
            "pushes in at slow speed",
        )

    def test_ambiguous_or_unknown_is_left_alone(self):
        for frag in (
            "moves left",  # pan or truck? a guess would be wrong half the time
            "moves right across the room",
            "does a barrel roll",
            "pans across the room",  # no direction
            "whips around",
            "",
        ):
            self.assertIsNone(rewrite_camera_fragment(frag), frag)

    def test_guide_phrasing_needs_no_rewrite(self):
        for frag in (
            "pushes in with small amplitude at slow speed toward the letter",
            "holds a static shot as the runner exits the frame",
            "cuts to a close-up of the bread",
        ):
            self.assertIsNone(rewrite_camera_fragment(frag), frag)


class TestCameraFindings(unittest.TestCase):
    def test_fixable_finding_carries_span_and_replacement(self):
        text = "She turns. The camera slowly zooms in on her face. She smiles."
        (f,) = camera_findings(text)
        self.assertEqual(f.code, "camera_phrase")
        self.assertTrue(f.fixable)
        self.assertEqual(
            text[f.start : f.end], "The camera slowly zooms in on her face."
        )
        self.assertEqual(
            f.replacement, "The camera zooms in at slow speed on her face."
        )
        self.assertIn("non-guide phrasing", f.message)

    def test_unfixable_finding_has_no_replacement(self):
        (f,) = camera_findings("The camera does a barrel roll.")
        self.assertFalse(f.fixable)
        self.assertIsNone(f.replacement)

    def test_clean_sentences_produce_nothing(self):
        self.assertEqual(camera_findings("The camera pans left at fast speed."), [])

    def test_apply_rewrites_only_the_flagged_sentences(self):
        text = (
            "The camera slowly dollies in. She waits. The camera pans left. "
            "The camera does a barrel roll. The camera follows her."
        )
        self.assertEqual(
            _fixed(text),
            "The camera pushes in at slow speed. She waits. The camera pans left. "
            "The camera does a barrel roll. The camera tracks the subject.",
        )


class TestSpeech(unittest.TestCase):
    def test_pronoun_speaker(self):
        self.assertEqual(
            _fixed('She says, "I get off at the next station." She folds the letter.'),
            "She (S1) says: <d>[English] I get off at the next station.</d> "
            "She folds the letter.",
        )

    def test_words_and_punctuation_inside_the_quote_are_verbatim(self):
        text = 'He whispers: "Wait -- don\'t go, not yet?!"'
        self.assertEqual(
            _fixed(text),
            "He (S1) whispers: <d>[English] Wait -- don't go, not yet?!</d>",
        )

    def test_noun_phrase_speaker_keeps_its_verb(self):
        self.assertEqual(
            _fixed('The young woman shouts "Wait for us!"'),
            "The young woman (S1) shouts: <d>[English] Wait for us!</d>",
        )

    def test_adverb_between_speaker_and_verb(self):
        self.assertEqual(
            _fixed('The man softly says, "Hello."'),
            "The man (S1) softly says: <d>[English] Hello.</d>",
        )

    def test_pronoun_with_an_action_before_the_verb(self):
        """The id belongs to the speaker, not to the speech verb."""
        self.assertEqual(
            _fixed('She smiles and says, "Hi."'),
            "She (S1) smiles and says: <d>[English] Hi.</d>",
        )

    def test_words_between_verb_and_quote(self):
        self.assertEqual(
            _fixed('She says to the camera, "Look."'),
            "She (S1) says to the camera: <d>[English] Look.</d>",
        )

    def test_curly_quotes(self):
        self.assertEqual(
            _fixed("She says, “Hello.”"), "She (S1) says: <d>[English] Hello.</d>"
        )

    def test_mid_paragraph_sentence(self):
        text = 'The light shifts. She looks up and says, "It\'s late." The clock ticks.'
        self.assertEqual(
            _fixed(text),
            "The light shifts. She (S1) looks up and says: <d>[English] It's late.</d> "
            "The clock ticks.",
        )

    def test_attribution_after_the_quote(self):
        self.assertEqual(
            _fixed('"I still remember that road," he says.'),
            "He (S1) says: <d>[English] I still remember that road.</d>",
        )
        self.assertEqual(
            _fixed('"Wait!" the girl shouts.'),
            "The girl (S1) shouts: <d>[English] Wait!</d>",
        )

    def test_same_speaker_reuses_the_id_and_a_new_one_gets_the_next(self):
        text = 'She says, "One." He says, "Two." She says, "Three."'
        self.assertEqual(
            _fixed(text),
            "She (S1) says: <d>[English] One.</d> He (S2) says: <d>[English] Two.</d> "
            "She (S1) says: <d>[English] Three.</d>",
        )

    def test_numbering_continues_after_ids_already_in_the_text(self):
        text = 'The man (S1) says: <d>[English] Hi.</d> She says, "Hello."'
        self.assertEqual(
            _fixed(text),
            "The man (S1) says: <d>[English] Hi.</d> She (S2) says: <d>[English] Hello.</d>",
        )

    def test_a_speaker_id_already_present_is_not_doubled(self):
        self.assertEqual(
            _fixed('She (S1) says, "Hello."'), "She (S1) says: <d>[English] Hello.</d>"
        )

    def test_speech_already_in_d_tags_is_untouched(self):
        text = "She (S1) says: <d>[English] Hello.</d>"
        self.assertEqual(speech_findings(text), [])

    def test_language_follows_the_script(self):
        self.assertEqual(
            _fixed('She says, "こんにちは"'),
            "She (S1) says: <d>[Japanese] こんにちは</d>",
        )
        self.assertIn("[Korean]", _fixed('She says, "안녕"'))
        self.assertIn("[Chinese]", _fixed('She says, "你好"'))
        self.assertIn("[Russian]", _fixed('She says, "Привет"'))

    def test_visible_text_is_not_speech(self):
        for text in (
            'A neon sign reads "OPEN" above the door.',
            'The label says "Fragile" in red letters.',
            'The screen shows the words "Game Over".',
        ):
            self.assertEqual(speech_findings(text), [], text)

    def test_quote_with_no_attribution_warns_but_is_not_fixable(self):
        """Could be a title, a thought, a lyric -- don't guess a speaker."""
        (f,) = speech_findings('A voice echoes through the hall: "Hello?"')
        self.assertEqual(f.code, "speech_format")
        self.assertFalse(f.fixable)
        self.assertIn("<d>", f.message)

    def test_fixable_finding_shape(self):
        text = 'She says, "Hello."'
        (f,) = speech_findings(text)
        self.assertEqual(f.code, "speech_format")
        self.assertTrue(f.fixable)
        self.assertEqual(text[f.start : f.end], text)
        self.assertIn("<d>", f.message)


class TestReport(unittest.TestCase):
    def _doc(self, description: str) -> str:
        return (
            f"{c.ALIGNMENT_LINE}\n\n"
            f"integrated_multimodal_description: {c._OPENING} {description}\n\n"
            "overall_soundscape: Wind.\n\nnon_diegetic_music: None."
        )

    def test_report_lists_fixes_and_the_fixed_prompt(self):
        text = self._doc(
            'The camera slowly dollies in. She says, "Hello there, friend."'
        )
        report = lint_i2v_report(text, 6)
        self.assertEqual(
            [f["code"] for f in report["fixes"]], ["camera_phrase", "speech_format"]
        )
        self.assertEqual(
            report["fixes"][0]["original"], "The camera slowly dollies in."
        )
        self.assertEqual(
            report["fixes"][0]["replacement"], "The camera pushes in at slow speed."
        )
        fixed = report["fixed_prompt"]
        self.assertIn("The camera pushes in at slow speed.", fixed)
        self.assertIn("She (S1) says: <d>[English] Hello there, friend.</d>", fixed)
        # Every fixable problem is also an ordinary warning.
        self.assertTrue(any("non-guide phrasing" in w for w in report["warnings"]))
        self.assertTrue(any("<d>" in w for w in report["warnings"]))

    def test_applying_the_fixes_clears_those_warnings(self):
        text = self._doc(
            'The camera slowly dollies in. She says, "Hello there, friend."'
        )
        fixed = lint_i2v_report(text, 6)["fixed_prompt"]
        again = lint_i2v_report(fixed, 6)
        self.assertEqual(again["fixes"], [])
        self.assertIsNone(again["fixed_prompt"])
        self.assertFalse(any("non-guide" in w or "<d>" in w for w in again["warnings"]))

    def test_nothing_fixable(self):
        report = lint_i2v_report(self._doc("The camera does a barrel roll."), 6)
        self.assertEqual(report["fixes"], [])
        self.assertIsNone(report["fixed_prompt"])
        self.assertTrue(any("non-guide phrasing" in w for w in report["warnings"]))

    def test_lint_i2v_prompt_reports_unwrapped_speech(self):
        issues = c.lint_i2v_prompt(self._doc('She says, "Hello."'), 6)
        self.assertTrue(any("<d>" in i for i in issues))

    def test_structure_is_never_touched_by_a_fix(self):
        text = self._doc('She says, "Hello."')
        fixed = lint_i2v_report(text, 6)["fixed_prompt"]
        self.assertTrue(fixed.startswith(c.ALIGNMENT_LINE + "\n\n"))
        for fld in ("overall_soundscape: Wind.", "non_diegetic_music: None."):
            self.assertIn(fld, fixed)


if __name__ == "__main__":
    unittest.main()
