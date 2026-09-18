"""Unit tests for the pure I2VA prompt compiler."""

import json
import unittest

from metascan.core import i2v_compiler as c


def _beats_json(n, camera="static"):
    return json.dumps(
        {
            "beats": [
                {"action": f"Beat {i} action happens here.", "camera": camera}
                for i in range(n)
            ],
            "overall_soundscape": "Rain ticks against the window.",
            "non_diegetic_music": "Sparse piano.",
        }
    )


class TestBeatCount(unittest.TestCase):
    def test_table(self):
        self.assertEqual(c.beat_count(6), 3)
        self.assertEqual(c.beat_count(10), 4)
        self.assertEqual(c.beat_count(15), 5)
        self.assertEqual(c.beat_count(20), 6)

    def test_nearest_for_off_table_values(self):
        self.assertEqual(c.beat_count(7), 3)
        self.assertEqual(c.beat_count(12), 4)
        self.assertEqual(c.beat_count(30), 6)


class TestGrammar(unittest.TestCase):
    def test_beat_count_is_baked_in(self):
        import re

        g = c.i2v_grammar(10)
        # 4 beats -> the root rule references the "beat" rule 4 times
        # (\b keeps the "beats" key from matching).
        root = [ln for ln in g.splitlines() if ln.startswith("root ::=")][0]
        self.assertEqual(len(re.findall(r"\bbeat\b", root)), 4)

    def test_camera_alternation_matches_vocab(self):
        g = c.i2v_grammar(6)
        for v in c.I2V_CAMERA_VALUES:
            self.assertIn(f'"\\"{v}\\""', g)
        self.assertNotIn("pov", c.I2V_CAMERA_VALUES)

    def test_no_escaped_hyphens(self):
        self.assertNotIn(r"\-", c.i2v_grammar(20))

    def test_max_tokens_scales(self):
        self.assertGreater(c.i2v_max_tokens(20), c.i2v_max_tokens(6))


class TestValidate(unittest.TestCase):
    def test_happy_path(self):
        r = c.validate_i2v_beats(_beats_json(3, camera="push_in"))
        self.assertEqual(len(r.beats), 3)
        self.assertEqual(r.beats[0].camera, "push_in")

    def test_bad_json_raises(self):
        with self.assertRaises(c.I2vError):
            c.validate_i2v_beats("not json")

    def test_unknown_camera_falls_back_to_static(self):
        raw = json.dumps(
            {
                "beats": [{"action": "x happens.", "camera": "warp_drive"}],
                "overall_soundscape": "s",
                "non_diegetic_music": "m",
            }
        )
        self.assertEqual(c.validate_i2v_beats(raw).beats[0].camera, "static")

    def test_empty_actions_dropped_and_all_empty_raises(self):
        raw = json.dumps(
            {
                "beats": [{"action": "  ", "camera": "static"}],
                "overall_soundscape": "s",
                "non_diegetic_music": "m",
            }
        )
        with self.assertRaises(c.I2vError):
            c.validate_i2v_beats(raw)

    def test_empty_sound_fields_get_defaults(self):
        raw = json.dumps(
            {
                "beats": [{"action": "x happens.", "camera": "static"}],
                "overall_soundscape": "",
                "non_diegetic_music": "",
            }
        )
        r = c.validate_i2v_beats(raw)
        self.assertTrue(r.overall_soundscape)
        self.assertTrue(r.non_diegetic_music)


class TestAssemble(unittest.TestCase):
    def test_document_structure(self):
        r = c.validate_i2v_beats(_beats_json(3, camera="push_in"))
        text = c.assemble_i2v_prompt(r)
        lines = text.splitlines()
        # Base guide 2.1: instruction first, then one blank line.
        self.assertEqual(lines[0], c.ALIGNMENT_LINE)
        self.assertEqual(lines[1], "")
        self.assertIn("integrated_multimodal_description: [Shot 1]", text)
        self.assertIn("\n\noverall_soundscape: ", text)
        self.assertIn("\n\nnon_diegetic_music: ", text)
        self.assertIn("<Picture 1>", text)
        self.assertIn("The camera pushes in", text)

    def test_alignment_line_exact(self):
        self.assertEqual(
            c.ALIGNMENT_LINE,
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.",
        )

    def test_all_static_gets_one_static_sentence(self):
        r = c.validate_i2v_beats(_beats_json(3, camera="static"))
        text = c.assemble_i2v_prompt(r)
        self.assertEqual(text.count("The camera holds a static shot."), 1)


class TestLint(unittest.TestCase):
    def _clean_text(self, n=4):
        return c.assemble_i2v_prompt(c.validate_i2v_beats(_beats_json(n)))

    def test_assembled_output_passes_structural_checks(self):
        issues = c.lint_i2v_prompt(self._clean_text(6), 20)
        structural = [i for i in issues if "short" not in i]  # word-count band may warn
        self.assertEqual(structural, [])

    def test_missing_alignment_line(self):
        text = self._clean_text().split("\n", 2)[2]
        self.assertTrue(any("alignment" in i for i in c.lint_i2v_prompt(text, 10)))

    def test_unknown_label(self):
        text = self._clean_text().replace("<Picture 1>", "<Picture 2>", 1)
        self.assertTrue(any("Picture 2" in i for i in c.lint_i2v_prompt(text, 10)))

    def test_dialog_markers_allowed(self):
        text = self._clean_text() + " <d>[English] Hi.</d>"
        self.assertFalse(
            any("unknown reference label" in i for i in c.lint_i2v_prompt(text, 10))
        )

    def test_ref_guide_sections_flagged(self):
        text = self._clean_text() + "\n\nretention_analysis: nope"
        self.assertTrue(any("ref-guide" in i for i in c.lint_i2v_prompt(text, 10)))

    def test_word_count_shortfall_warns(self):
        text = (
            c.ALIGNMENT_LINE
            + "\n\nintegrated_multimodal_description: [Shot 1] Tiny.\n\n"
            + "overall_soundscape: s\n\nnon_diegetic_music: m"
        )
        self.assertTrue(any("short" in i for i in c.lint_i2v_prompt(text, 20)))

    def test_off_vocab_camera_sentence_warns(self):
        text = self._clean_text().replace(
            "The camera holds a static shot.",
            "The camera does a barrel roll.",
        )
        self.assertTrue(any("camera" in i for i in c.lint_i2v_prompt(text, 10)))
