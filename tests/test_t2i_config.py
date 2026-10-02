"""backend.config.get_t2i_config: the ``t2i`` section of config.json with
defaults filled in and junk sanitised. Same defensive style as
get_i2v_config (tests/test_i2v_api.py), but typed: a value of the wrong
JSON type is ignored, never coerced (True is not 1, "3" is not 3)."""

from __future__ import annotations

import copy
import json
import unittest

import pytest

from backend.config import T2I_DEFAULT_OUTPUT_PREFIX, get_t2i_config
from metascan.core.t2i_characters import IDENTITY_STYLES
from metascan.core.t2i_models import MODEL_PROFILES
from metascan.core.t2i_prompt import CONTENT_MODES

DEFAULTS = {
    "output_root": "",
    "output_prefix": "/%Y-%m-%d/t2i_",
    "megapixels": [0.5, 1.0, 1.5, 2.0],
    "default_megapixels": 1.0,
    "default_model": "krea2",
    "model_workflows": {"krea2": None, "qwen": None, "sd": None, "zimage": None},
    "content_mode": "uncensored",
    "identity": {},
    "window": 4,
    "max_batch_size": 500,
    "max_count_per_batch": 32,
    "directions": {
        "enabled": True,
        "emotion_missing_min": 0.70,
        "emotion_sensual_from": 0.60,
        "kiss_min": 0.80,
        "act_min": 0.80,
        "skip_act_on_conflict": True,
    },
}

JUNK = [None, True, False, 5, 1.5, "x", "", [], ["x"], {}, {"a": 1}]
# Junk for a field where a plain number is legitimate.
NOT_A_NUMBER = [None, True, False, "x", "1.0", "", [], ["x"], {}, {"a": 1}]


def cfg(**section):
    return get_t2i_config({"t2i": section})


class TestDefaults(unittest.TestCase):
    def test_an_empty_config_gives_exactly_the_spec_defaults(self):
        self.assertEqual(get_t2i_config({}), DEFAULTS)

    def test_the_default_prefix_is_a_module_constant(self):
        self.assertEqual(T2I_DEFAULT_OUTPUT_PREFIX, "/%Y-%m-%d/t2i_")

    def test_an_empty_or_junk_section_gives_the_defaults(self):
        for section in (None, {}, "x", [], 5, True):
            with self.subTest(section=section):
                self.assertEqual(get_t2i_config({"t2i": section}), DEFAULTS)

    def test_other_sections_are_ignored(self):
        self.assertEqual(
            get_t2i_config({"i2v": {"output_prefix": "/x_"}, "comfy": {}}), DEFAULTS
        )

    def test_the_result_is_a_fresh_object_each_call(self):
        first = get_t2i_config({})
        first["megapixels"].append(9.0)
        first["model_workflows"]["krea2"] = 5
        first["identity"]["sd"] = "name"
        self.assertEqual(get_t2i_config({}), DEFAULTS)

    def test_the_input_is_never_mutated(self):
        raw = {
            "t2i": {
                "megapixels": [2, "x"],
                "model_workflows": {"krea2": 3, "flux": 9},
                "identity": {"sd": "name", "flux": "ref"},
            }
        }
        snapshot = copy.deepcopy(raw)
        get_t2i_config(raw)
        self.assertEqual(raw, snapshot)

    def test_the_result_is_json_serialisable(self):
        json.dumps(get_t2i_config({"t2i": {"identity": {"sd": "name"}}}))

    def test_a_result_fed_back_in_is_a_fixed_point(self):
        once = get_t2i_config(
            {
                "t2i": {
                    "output_root": " /mnt/d/T2I ",
                    "megapixels": [1, 2],
                    "default_model": "sd",
                    "model_workflows": {"sd": 4, "junk": 1},
                    "content_mode": "sfw",
                    "identity": {"sd": "name", "krea2": "bogus"},
                    "window": 0,
                }
            }
        )
        self.assertEqual(get_t2i_config({"t2i": once}), once)


class TestValuesPassThrough(unittest.TestCase):
    def test_a_complete_valid_section_comes_back_as_written(self):
        section = {
            "output_root": "/mnt/d/Media/t2i",
            "output_prefix": "/%Y/%m/img_",
            "megapixels": [0.75, 1.25],
            "default_megapixels": 1.25,
            "default_model": "zimage",
            "model_workflows": {"krea2": 3, "qwen": 4, "sd": 5, "zimage": 6},
            "content_mode": "default",
            "identity": {"krea2": "name", "qwen": "noun", "sd": "ref", "zimage": "ref"},
            "window": 8,
            "max_batch_size": 100,
            "max_count_per_batch": 10,
            "directions": {
                "enabled": False,
                "emotion_missing_min": 0.5,
                "emotion_sensual_from": 0.4,
                "kiss_min": 0.9,
                "act_min": 0.95,
                "skip_act_on_conflict": False,
            },
        }
        self.assertEqual(get_t2i_config({"t2i": section}), section)


class TestOutputPlacement(unittest.TestCase):
    def test_root_and_prefix_are_trimmed(self):
        c = cfg(output_root="  /mnt/d/Media  ", output_prefix="  /a/b_ ")
        self.assertEqual(c["output_root"], "/mnt/d/Media")
        self.assertEqual(c["output_prefix"], "/a/b_")

    def test_an_explicit_empty_prefix_is_honoured(self):
        # "" is a real choice: files straight into the root, bare number as
        # the name. Only an absent or junk value gets the default.
        self.assertEqual(cfg(output_prefix="")["output_prefix"], "")
        self.assertEqual(cfg(output_prefix="   ")["output_prefix"], "")

    def test_junk_root_and_prefix_fall_back(self):
        for junk in (None, 5, ["x"], {"a": 1}, True):
            with self.subTest(junk=junk):
                c = cfg(output_root=junk, output_prefix=junk)
                self.assertEqual(c["output_root"], "")
                self.assertEqual(c["output_prefix"], T2I_DEFAULT_OUTPUT_PREFIX)


class TestMegapixels(unittest.TestCase):
    def test_integers_become_floats(self):
        c = cfg(megapixels=[1, 2], default_megapixels=2)
        self.assertEqual(c["megapixels"], [1.0, 2.0])
        self.assertEqual(c["default_megapixels"], 2.0)
        self.assertIsInstance(c["megapixels"][0], float)

    def test_a_junk_list_falls_back_to_the_default_ladder(self):
        for junk in ("x", 5, {"a": 1}, None, True, [], ["x", None]):
            with self.subTest(junk=junk):
                self.assertEqual(
                    cfg(megapixels=junk)["megapixels"], [0.5, 1.0, 1.5, 2.0]
                )

    def test_junk_entries_are_dropped_one_by_one(self):
        c = cfg(megapixels=["x", 2, None, True, 1.5, [1], {"a": 1}])
        self.assertEqual(c["megapixels"], [2.0, 1.5])

    def test_out_of_range_entries_are_dropped(self):
        c = cfg(megapixels=[0, -1, 0.5, float("nan"), float("inf"), 5000, 1000])
        self.assertEqual(c["megapixels"], [0.5, 1000.0])

    def test_an_integer_too_large_for_a_float_is_dropped_not_raised(self):
        c = cfg(megapixels=[10**400, 2], default_megapixels=10**400)
        self.assertEqual(c["megapixels"], [2.0])
        self.assertEqual(c["default_megapixels"], 2.0)

    def test_a_junk_default_falls_back_to_one_megapixel(self):
        for junk in NOT_A_NUMBER:
            with self.subTest(junk=junk):
                self.assertEqual(
                    cfg(default_megapixels=junk)["default_megapixels"], 1.0
                )

    def test_a_default_that_is_not_in_the_ladder_falls_back_to_the_first_entry(self):
        self.assertEqual(cfg(megapixels=[0.5, 2.0])["default_megapixels"], 0.5)
        self.assertEqual(
            cfg(megapixels=[0.5, 2.0], default_megapixels=1.0)["default_megapixels"],
            0.5,
        )

    def test_a_valid_number_outside_the_ladder_also_falls_back_to_the_first_entry(self):
        # Not to 1.0: the choice named something the dialog cannot offer.
        self.assertEqual(cfg(default_megapixels=5)["default_megapixels"], 0.5)
        self.assertEqual(cfg(default_megapixels=0.75)["default_megapixels"], 0.5)

    def test_the_default_can_be_any_entry_of_the_ladder(self):
        c = cfg(megapixels=[0.5, 2.0], default_megapixels=2)
        self.assertEqual(c["default_megapixels"], 2.0)

    def test_the_default_falls_back_after_junk_entries_are_dropped(self):
        c = cfg(megapixels=["x", 3], default_megapixels=1.0)
        self.assertEqual(c["megapixels"], [3.0])
        self.assertEqual(c["default_megapixels"], 3.0)


class TestDefaultModel(unittest.TestCase):
    def test_every_profile_id_is_accepted(self):
        for model_id in MODEL_PROFILES:
            with self.subTest(model=model_id):
                self.assertEqual(cfg(default_model=model_id)["default_model"], model_id)

    def test_an_unknown_or_junk_model_falls_back_to_krea2(self):
        for junk in ["flux", "KREA2", "", " qwen", *JUNK]:
            with self.subTest(junk=junk):
                self.assertEqual(cfg(default_model=junk)["default_model"], "krea2")


class TestModelWorkflows(unittest.TestCase):
    def test_every_profile_gets_a_key_in_profile_order(self):
        c = cfg(model_workflows={"zimage": 7})
        self.assertEqual(list(c["model_workflows"]), list(MODEL_PROFILES))
        self.assertEqual(
            c["model_workflows"], {"krea2": None, "qwen": None, "sd": None, "zimage": 7}
        )

    def test_unknown_model_ids_are_dropped(self):
        c = cfg(model_workflows={"krea2": 3, "flux": 9, "": 1})
        self.assertEqual(c["model_workflows"]["krea2"], 3)
        self.assertNotIn("flux", c["model_workflows"])
        self.assertNotIn("", c["model_workflows"])
        self.assertEqual(len(c["model_workflows"]), len(MODEL_PROFILES))

    def test_a_preset_id_that_is_not_an_int_becomes_none(self):
        for junk in [
            None,
            True,
            False,
            1.5,
            3.0,
            "3",
            "abc",
            "",
            [3],
            {"id": 3},
            0,
            -2,
        ]:
            with self.subTest(junk=junk):
                self.assertIsNone(
                    cfg(model_workflows={"qwen": junk})["model_workflows"]["qwen"]
                )

    def test_positive_int_ids_pass(self):
        self.assertEqual(cfg(model_workflows={"sd": 1})["model_workflows"]["sd"], 1)
        self.assertEqual(
            cfg(model_workflows={"sd": 12345})["model_workflows"]["sd"], 12345
        )

    def test_a_junk_container_gives_every_model_none(self):
        for junk in ["x", 5, ["krea2"], None, True]:
            with self.subTest(junk=junk):
                self.assertEqual(
                    cfg(model_workflows=junk)["model_workflows"],
                    DEFAULTS["model_workflows"],
                )


class TestContentMode(unittest.TestCase):
    def test_every_mode_is_accepted(self):
        for mode in CONTENT_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(cfg(content_mode=mode)["content_mode"], mode)

    def test_a_bad_mode_falls_back_to_uncensored(self):
        for junk in ["nsfw", "SFW", "Sfw ", "", *JUNK]:
            with self.subTest(junk=junk):
                self.assertEqual(cfg(content_mode=junk)["content_mode"], "uncensored")


class TestIdentity(unittest.TestCase):
    def test_valid_overrides_pass_in_profile_order(self):
        c = cfg(identity={"zimage": "name", "sd": "ref", "qwen": "noun"})
        self.assertEqual(c["identity"], {"qwen": "noun", "sd": "ref", "zimage": "name"})
        self.assertEqual(list(c["identity"]), ["qwen", "sd", "zimage"])

    def test_every_known_style_is_accepted(self):
        for style in IDENTITY_STYLES:
            with self.subTest(style=style):
                self.assertEqual(cfg(identity={"sd": style})["identity"], {"sd": style})

    def test_unknown_models_are_dropped(self):
        self.assertEqual(
            cfg(identity={"flux": "ref", "sd": "name"})["identity"], {"sd": "name"}
        )

    def test_unknown_or_junk_styles_are_dropped(self):
        c = cfg(
            identity={"krea2": "nickname", "qwen": 5, "sd": None, "zimage": ["ref"]}
        )
        self.assertEqual(c["identity"], {})

    def test_a_junk_container_is_an_empty_override_map(self):
        for junk in ["x", 5, ["sd"], None, True]:
            with self.subTest(junk=junk):
                self.assertEqual(cfg(identity=junk)["identity"], {})


class TestWindowAndLimits(unittest.TestCase):
    FIELDS = (("window", 4), ("max_batch_size", 500), ("max_count_per_batch", 32))

    def test_defaults(self):
        c = get_t2i_config({})
        for key, default in self.FIELDS:
            self.assertEqual(c[key], default, key)

    def test_valid_values_pass_through_with_no_upper_cap(self):
        for key, _ in self.FIELDS:
            for value in (1, 2, 64, 100000):
                with self.subTest(key=key, value=value):
                    self.assertEqual(get_t2i_config({"t2i": {key: value}})[key], value)

    def test_values_below_one_are_clamped_to_one(self):
        for key, _ in self.FIELDS:
            for value in (0, -1, -500):
                with self.subTest(key=key, value=value):
                    self.assertEqual(get_t2i_config({"t2i": {key: value}})[key], 1)

    def test_junk_falls_back_to_the_default(self):
        for key, default in self.FIELDS:
            for junk in [None, True, False, 4.5, 4.0, "8", "x", "", [4], {"a": 1}]:
                with self.subTest(key=key, junk=junk):
                    self.assertEqual(get_t2i_config({"t2i": {key: junk}})[key], default)


@pytest.mark.parametrize("junk", JUNK, ids=[repr(j) for j in JUNK])
def test_no_junk_value_in_any_field_can_raise(junk):
    section = {key: junk for key in DEFAULTS}
    result = get_t2i_config({"t2i": section})
    assert set(result) == set(DEFAULTS)
    assert result["default_model"] in MODEL_PROFILES
    assert result["content_mode"] in CONTENT_MODES
    assert all(m > 0 for m in result["megapixels"])
    assert result["window"] >= 1


if __name__ == "__main__":
    unittest.main()


class TestDirections(unittest.TestCase):
    def test_valid_values_pass_through(self):
        section = {
            "enabled": False,
            "emotion_missing_min": 0.5,
            "emotion_sensual_from": 0.4,
            "kiss_min": 0.9,
            "act_min": 0.95,
            "skip_act_on_conflict": False,
        }
        self.assertEqual(cfg(directions=section)["directions"], section)

    def test_out_of_range_numbers_are_clamped(self):
        got = cfg(directions={"act_min": 1.7, "kiss_min": -0.2})["directions"]
        self.assertEqual((got["act_min"], got["kiss_min"]), (1.0, 0.0))

    def test_junk_falls_back_to_the_defaults(self):
        for junk in JUNK:
            with self.subTest(junk=junk):
                got = cfg(directions=junk)["directions"]
                self.assertEqual(got, DEFAULTS["directions"])
        got = cfg(
            directions={"enabled": "yes", "act_min": "0.9", "kiss_min": float("nan")}
        )["directions"]
        self.assertEqual(got, DEFAULTS["directions"])
