"""Tests for metascan/core/t2i_form.py -- the pure helpers behind the
Text-to-Image dialog: seed stepping, output size, and the editable
per-image form state.

Every caption, prompt and LoRA name below is hand-written for the test.
"""

from __future__ import annotations

import json
import math
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Tuple
from unittest.mock import patch

import numpy as np
import pytest

from metascan.core import t2i_form
from metascan.core.t2i_form import (
    ASPECT_RATIOS,
    MAX_MEGAPIXELS,
    SEED_MAX,
    SEED_POLICIES,
    T2I_FORM_FIELDS,
    T2I_MODES,
    T2iFormError,
    build_form_state,
    form_state_for_row,
    next_seed,
    normalize_loras,
    render_seconds,
    t2i_dims,
    validate_form_patch,
)

# ---- constants ----------------------------------------------------------


def test_the_shared_constants_are_exactly_the_contract_values():
    assert SEED_MAX == 2**31 - 1
    assert SEED_POLICIES == ("fixed", "increment", "decrement", "random")
    assert ASPECT_RATIOS == (
        "1:1",
        "4:3",
        "3:4",
        "3:2",
        "2:3",
        "16:9",
        "9:16",
        "4:5",
        "5:4",
        "21:9",
        "9:21",
    )
    assert T2I_FORM_FIELDS == (
        "mode",
        "filter",
        "caption",
        "model",
        "preset_id",
        "megapixels",
        "aspect_ratio",
        "seed",
        "prompt",
        "negative",
        "loras",
    )


def test_the_added_public_constants():
    assert T2I_MODES == ("manual", "random")
    assert MAX_MEGAPIXELS == 1000.0


def test_form_error_is_a_value_error():
    assert issubclass(T2iFormError, ValueError)


def test_the_module_imports_no_i2v_code():
    source = Path(t2i_form.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+\S*i2v", source, re.MULTILINE)


# ---- seed stepping ------------------------------------------------------


class _StubRng:
    """Stands in for random.Random and records the bounds it was given."""

    def __init__(self, value: int) -> None:
        self.value = value
        self.calls: List[Tuple[int, int]] = []

    def randint(self, low: int, high: int) -> int:
        self.calls.append((low, high))
        return self.value


@pytest.mark.parametrize("seed", [0, 1, 321424, SEED_MAX])
def test_fixed_keeps_the_seed(seed):
    assert next_seed("fixed", seed, random.Random(1)) == seed


def test_increment_steps_up_by_one():
    rng = random.Random(1)
    assert next_seed("increment", 0, rng) == 1
    assert next_seed("increment", 41, rng) == 42
    assert next_seed("increment", SEED_MAX - 1, rng) == SEED_MAX


def test_increment_stops_at_the_top_of_the_range():
    assert next_seed("increment", SEED_MAX, random.Random(1)) is None


def test_decrement_steps_down_by_one():
    rng = random.Random(1)
    assert next_seed("decrement", 1, rng) == 0
    assert next_seed("decrement", 43, rng) == 42
    assert next_seed("decrement", SEED_MAX, rng) == SEED_MAX - 1


def test_decrement_stops_at_the_bottom_of_the_range():
    assert next_seed("decrement", 0, random.Random(1)) is None


def test_walking_off_either_end_yields_exactly_the_seeds_that_remain():
    rng = random.Random(1)

    def walk(policy: str, start: int) -> List[int]:
        seeds = [start]
        while True:
            following = next_seed(policy, seeds[-1], rng)
            if following is None:
                return seeds
            seeds.append(following)

    assert walk("increment", SEED_MAX - 2) == [SEED_MAX - 2, SEED_MAX - 1, SEED_MAX]
    assert walk("decrement", 2) == [2, 1, 0]


def test_random_draws_the_whole_range_through_the_rng_it_is_given():
    rng = _StubRng(123456)
    assert next_seed("random", 5, rng) == 123456  # type: ignore[arg-type]
    assert rng.calls == [(0, SEED_MAX)]


def test_random_is_reproducible_for_a_seeded_rng_and_stays_in_range():
    first, second = random.Random(2026), random.Random(2026)
    drawn = [next_seed("random", 0, first) for _ in range(200)]
    assert drawn == [next_seed("random", 0, second) for _ in range(200)]
    assert all(value is not None and 0 <= value <= SEED_MAX for value in drawn)
    assert len(set(drawn)) > 190  # not a constant


def test_random_does_not_depend_on_the_current_seed():
    assert next_seed("random", 0, random.Random(3)) == next_seed(
        "random", SEED_MAX, random.Random(3)
    )


@pytest.mark.parametrize("policy", SEED_POLICIES)
@pytest.mark.parametrize("bad", [-1, SEED_MAX + 1, True, 1.5, "7", None])
def test_a_seed_outside_the_range_is_rejected_under_every_policy(policy, bad):
    with pytest.raises(T2iFormError, match="seed"):
        next_seed(policy, bad, random.Random(1))


@pytest.mark.parametrize("bad", ["", "Fixed", "shuffle", None, 3])
def test_an_unknown_policy_is_rejected(bad):
    with pytest.raises(T2iFormError, match="policy"):
        next_seed(bad, 5, random.Random(1))


# ---- t2i_dims -----------------------------------------------------------

HUGE_INT = 10**400  # an int that float() cannot represent
MEGAPIXELS = (0.5, 1.0, 1.5, 2.0)
MULTIPLES = (16, 64)  # Krea2 / Qwen / Z-Image, and SDXL
GRID = [(r, mp, m) for r in ASPECT_RATIOS for mp in MEGAPIXELS for m in MULTIPLES]
GRID_IDS = [f"{r}-{mp}MP-x{m}" for r, mp, m in GRID]


def _ratio(name: str) -> float:
    wide, high = name.split(":")
    return int(wide) / int(high)


def _ideal_short_edge(name: str, megapixels: float) -> float:
    """The short edge of the exact-ratio rectangle with exactly the budget."""
    ratio = _ratio(name)
    return math.sqrt(megapixels * 1_000_000 / max(ratio, 1 / ratio))


@pytest.mark.parametrize("name,megapixels,multiple", GRID, ids=GRID_IDS)
class TestDimsOverTheWholeGrid:
    def test_both_edges_sit_on_the_grid_and_are_at_least_one_multiple(
        self, name, megapixels, multiple
    ):
        width, height = t2i_dims(name, megapixels, multiple)
        assert width % multiple == 0 and height % multiple == 0
        assert width >= multiple and height >= multiple

    def test_the_orientation_follows_the_ratio(self, name, megapixels, multiple):
        width, height = t2i_dims(name, megapixels, multiple)
        ratio = _ratio(name)
        assert (width >= height) == (ratio >= 1)

    def test_the_pixel_budget_is_honoured_within_fifteen_percent(
        self, name, megapixels, multiple
    ):
        # The brief asks for this at 1 MP and up. It holds at 0.5 MP too
        # (worst case 11.5%: 4:3 at 0.5 MP on the 64-grid), so it is asserted
        # everywhere.
        width, height = t2i_dims(name, megapixels, multiple)
        assert abs(width * height / (megapixels * 1_000_000) - 1) <= 0.15

    def test_the_aspect_error_is_within_half_a_grid_step_of_the_short_edge(
        self, name, megapixels, multiple
    ):
        # Why this bound: moving the short edge by one multiple changes the
        # ratio by about multiple / short_edge, so a size whose ratio is off
        # by more than half of that would have a closer neighbour. The
        # budget term may pull the pick away from the closest ratio, so this
        # is checked, not assumed: on this grid the worst case is 0.82 of
        # the bound (4:5 at 0.5 MP on the 64-grid: 4.2% against 5.1%).
        width, height = t2i_dims(name, megapixels, multiple)
        error = abs((width / height) / _ratio(name) - 1)
        assert error <= (multiple / 2) / _ideal_short_edge(name, megapixels)

    def test_the_aspect_error_regression_caps(self, name, megapixels, multiple):
        # Pins the achieved precision: worst observed 0.67% on the 16-grid
        # and 4.17% on the 64-grid.
        width, height = t2i_dims(name, megapixels, multiple)
        error = abs((width / height) / _ratio(name) - 1)
        assert error <= (0.01 if multiple == 16 else 0.045)

    def test_a_portrait_ratio_is_the_landscape_answer_turned_on_its_side(
        self, name, megapixels, multiple
    ):
        wide, high = name.split(":")
        assert t2i_dims(f"{high}:{wide}", megapixels, multiple) == tuple(
            reversed(t2i_dims(name, megapixels, multiple))
        )


@pytest.mark.parametrize(
    "name,megapixels,multiple,expected",
    [
        # The SDXL (x64) buckets everyone knows, at 1 MP.
        ("1:1", 1.0, 64, (1024, 1024)),
        ("3:2", 1.0, 64, (1216, 832)),
        ("2:3", 1.0, 64, (832, 1216)),
        ("16:9", 1.0, 64, (1344, 768)),
        ("9:16", 1.0, 64, (768, 1344)),
        # x16 models can follow the budget and the ratio to within a percent.
        ("3:2", 1.0, 16, (1232, 816)),
        ("2:3", 1.0, 16, (816, 1232)),
        ("16:9", 1.0, 16, (1328, 752)),
        ("16:9", 2.0, 16, (1872, 1056)),
    ],
)
def test_known_sizes_are_pinned(name, megapixels, multiple, expected):
    assert t2i_dims(name, megapixels, multiple) == expected


def test_dims_are_deterministic():
    assert [t2i_dims("3:2", 1.0, 16) for _ in range(5)] == [(1232, 816)] * 5


# -- review focus 3: extreme ratios and coarse grids ----------------------


@pytest.mark.parametrize(
    "name,expected",
    [("21:9", (1024, 448)), ("9:21", (448, 1024))],
)
def test_ultrawide_at_half_a_megapixel_on_the_64_grid(name, expected):
    # The hardest cell of the grid: one grid step is 14% of the short edge.
    width, height = t2i_dims(name, 0.5, 64)
    assert (width, height) == expected
    assert width % 64 == 0 and height % 64 == 0
    assert min(width, height) >= 64
    assert abs((width / height) / _ratio(name) - 1) < 0.025  # 2.0% and 2.1%
    assert abs(width * height / 500_000 - 1) < 0.10  # -8.2%


@pytest.mark.parametrize("multiple", [16, 64])
@pytest.mark.parametrize("name", ["21:9", "9:21"])
@pytest.mark.parametrize("megapixels", [0.5, 1.0, 2.0])
def test_ultrawide_and_ultratall_keep_a_usable_short_edge(name, megapixels, multiple):
    width, height = t2i_dims(name, megapixels, multiple)
    assert min(width, height) >= multiple
    assert width % multiple == 0 and height % multiple == 0


@pytest.mark.parametrize("multiple", [16, 64])
def test_a_budget_smaller_than_one_grid_cell_floors_at_one_multiple(multiple):
    # 100 pixels: less than a single 16x16 cell, never mind 64x64.
    assert t2i_dims("1:1", 0.0001, multiple) == (multiple, multiple)


@pytest.mark.parametrize(
    "name,megapixels",
    [("32:1", 0.05), ("1:32", 0.05), ("32:1", 1.0), ("1:32", 1.0), ("16:1", 0.5)],
)
def test_the_most_extreme_supported_ratios_never_produce_a_zero_edge(name, megapixels):
    width, height = t2i_dims(name, megapixels, 64)
    assert width % 64 == 0 and height % 64 == 0
    assert min(width, height) >= 64
    # The floor binds here (the budget's short edge is under one grid cell),
    # so the ratio is honoured and the long edge grows instead.
    assert abs((width / height) / _ratio(name) - 1) < 0.05


@pytest.mark.parametrize("bad", ["33:1", "1:33", "999999:1", "1:999999", "100:3"])
def test_a_ratio_more_extreme_than_32_to_1_is_rejected(bad):
    # Past this the short edge is pinned at one grid cell and the long edge
    # runs to tens of thousands of pixels: not a size any model can render.
    with pytest.raises(T2iFormError, match="aspect ratio"):
        t2i_dims(bad, 1.0, 16)


def _rule_score(width: float, height: float, ratio: float, budget: float) -> float:
    """The documented rule: aspect error counts four times the budget error,
    both as absolute log ratios."""
    return 4.0 * abs(math.log((width / height) / ratio)) + abs(
        math.log(width * height / budget)
    )


@pytest.mark.parametrize("megapixels", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("multiple", [8, 16, 32, 64, 128])
@pytest.mark.parametrize("name", list(ASPECT_RATIOS) + ["32:1", "1:32", "7:3"])
def test_the_windowed_search_finds_the_best_pair_on_the_grid(
    name, multiple, megapixels
):
    # An independent exhaustive scan of every grid pair up to three times the
    # ideal edges (numpy, one broadcast). Nothing may score better than what
    # t2i_dims returns: proof its search window is wide enough. Scores are
    # compared, not pairs, so an exact tie cannot make this flaky.
    ratio, budget = _ratio(name), megapixels * 1_000_000
    top_wide = int(3 * math.sqrt(budget * ratio) / multiple) + 2
    top_high = int(3 * math.sqrt(budget / ratio) / multiple) + 2
    wide = np.arange(1, top_wide + 1)[:, None] * multiple
    high = np.arange(1, top_high + 1)[None, :] * multiple
    scores = 4.0 * np.abs(np.log((wide / high) / ratio)) + np.abs(
        np.log(wide * high / budget)
    )
    chosen = _rule_score(*t2i_dims(name, megapixels, multiple), ratio, budget)
    assert chosen <= float(scores.min()) + 1e-9


@pytest.mark.parametrize("bad", ["", "3x2", "3:", ":2", "0:1", "1:0", "0:0"])
def test_a_malformed_ratio_is_rejected(bad):
    with pytest.raises(T2iFormError, match="aspect ratio"):
        t2i_dims(bad, 1.0, 16)


@pytest.mark.parametrize(
    "bad",
    ["-1:2", "3:2 ", " 3:2", "3:2:1", "1.5:1", "3/2", "٣:٢", None, 1.5, ["3:2"]],
)
def test_a_ratio_that_is_not_plain_digits_is_rejected(bad):
    with pytest.raises(T2iFormError, match="aspect ratio"):
        t2i_dims(bad, 1.0, 16)


def test_a_ratio_too_long_to_be_real_is_rejected_not_an_overflow():
    with pytest.raises(T2iFormError, match="aspect ratio"):
        t2i_dims("1" + "0" * 400 + ":1", 1.0, 16)


@pytest.mark.parametrize(
    "bad",
    [
        0,
        0.0,
        -1,
        -0.5,
        float("nan"),
        float("inf"),
        True,
        "1",
        None,
        1000.5,
        1e305,
        pytest.param(HUGE_INT, id="int-too-large-for-a-float"),
    ],
)
def test_a_bad_megapixel_budget_is_rejected(bad):
    with pytest.raises(T2iFormError, match="megapixels"):
        t2i_dims("3:2", bad, 16)


@pytest.mark.parametrize("bad", [0, -16, 16.0, True, "16", None])
def test_a_bad_grid_multiple_is_rejected(bad):
    with pytest.raises(T2iFormError, match="multiple"):
        t2i_dims("3:2", 1.0, bad)


def test_an_integer_megapixel_budget_is_fine():
    assert t2i_dims("1:1", 1, 64) == (1024, 1024)


# ---- normalize_loras ----------------------------------------------------


def test_loras_are_reduced_to_name_and_float_strength():
    cleaned = normalize_loras(
        [
            {"name": "watercolor.safetensors", "strength": 1},
            {"name": "sub/dir/ink.safetensors", "strength": "0.65", "note": "x"},
        ]
    )
    assert cleaned == [
        {"name": "watercolor.safetensors", "strength": 1.0},
        {"name": "sub/dir/ink.safetensors", "strength": 0.65},
    ]
    assert all(isinstance(entry["strength"], float) for entry in cleaned)


def test_a_lora_without_a_strength_defaults_to_full_strength():
    assert normalize_loras([{"name": "a.safetensors"}]) == [
        {"name": "a.safetensors", "strength": 1.0}
    ]


def test_negative_and_zero_strengths_are_legal():
    assert normalize_loras(
        [{"name": "a", "strength": -0.5}, {"name": "b", "strength": 0}]
    ) == [{"name": "a", "strength": -0.5}, {"name": "b", "strength": 0.0}]


def test_an_empty_list_and_a_tuple_are_accepted_and_order_is_kept():
    assert normalize_loras([]) == []
    assert [e["name"] for e in normalize_loras(({"name": "z"}, {"name": "a"}))] == [
        "z",
        "a",
    ]


def test_normalizing_loras_leaves_the_input_untouched():
    original = [{"name": "a.safetensors", "strength": "0.5", "extra": 1}]
    snapshot = json.loads(json.dumps(original))
    normalize_loras(original)
    assert original == snapshot


@pytest.mark.parametrize("bad", [None, {}, "a.safetensors", 5, {"name": "a"}])
def test_loras_must_be_a_list(bad):
    with pytest.raises(T2iFormError, match="loras must be a list"):
        normalize_loras(bad)


@pytest.mark.parametrize(
    "entry",
    ["a.safetensors", None, 3, ["a"], {}, {"strength": 0.5}]
    + [{"name": bad} for bad in ("", "   ", None, 5, ["a"])],
)
def test_every_lora_needs_a_text_name(entry):
    with pytest.raises(T2iFormError, match="name"):
        normalize_loras([entry])


@pytest.mark.parametrize(
    "strength",
    [
        None,
        True,
        "abc",
        "",
        float("nan"),
        float("inf"),
        pytest.param(HUGE_INT, id="int-too-large-for-a-float"),
        [1],
        {},
    ],
)
def test_a_lora_strength_must_be_a_finite_number(strength):
    with pytest.raises(T2iFormError, match="strength"):
        normalize_loras([{"name": "a", "strength": strength}])


# ---- validate_form_patch ------------------------------------------------


def test_a_valid_patch_returns_exactly_the_supplied_fields_cleaned():
    cleaned = validate_form_patch(
        {
            "mode": "random",
            "filter": {"nudity": ["none"], "females": {"min": 1, "max": 2}},
            "caption": "a lighthouse at dusk",
            "model": "krea2",
            "preset_id": 4,
            "megapixels": 2,
            "aspect_ratio": "21:9",
            "seed": 0,
            "prompt": "A lighthouse at dusk.",
            "negative": None,
            "loras": [{"name": "a.safetensors", "strength": "0.5"}],
        }
    )
    assert set(cleaned) == set(T2I_FORM_FIELDS)
    assert cleaned["megapixels"] == 2.0 and isinstance(cleaned["megapixels"], float)
    assert cleaned["loras"] == [{"name": "a.safetensors", "strength": 0.5}]
    assert cleaned["filter"] == {"nudity": ["none"], "females": {"min": 1, "max": 2}}
    assert cleaned["negative"] is None


def test_a_patch_is_partial():
    assert validate_form_patch({"seed": 7}) == {"seed": 7}


def test_the_returned_filter_and_loras_are_copies():
    flt = {"nudity": ["none"]}
    loras = [{"name": "a", "strength": 1.0}]
    cleaned = validate_form_patch({"filter": flt, "loras": loras})
    cleaned["filter"]["nudity"].append("full")
    cleaned["loras"][0]["strength"] = 9.0
    assert flt == {"nudity": ["none"]}
    assert loras == [{"name": "a", "strength": 1.0}]


@pytest.mark.parametrize("mode", ["manual", "random"])
def test_both_modes_are_accepted(mode):
    assert validate_form_patch({"mode": mode}) == {"mode": mode}


@pytest.mark.parametrize("bad", ["Manual", "batch", "", None, 1])
def test_an_unknown_mode_is_rejected(bad):
    with pytest.raises(T2iFormError, match="mode"):
        validate_form_patch({"mode": bad})


@pytest.mark.parametrize("field", ["caption", "prompt", "negative"])
def test_text_fields_take_text_or_null(field):
    assert validate_form_patch({field: "hand written"}) == {field: "hand written"}
    assert validate_form_patch({field: ""}) == {field: ""}
    assert validate_form_patch({field: None}) == {field: None}
    for bad in (5, ["x"], {"a": 1}, True):
        with pytest.raises(T2iFormError, match=field):
            validate_form_patch({field: bad})


def test_the_filter_takes_a_mapping_or_null():
    assert validate_form_patch({"filter": {}}) == {"filter": {}}
    assert validate_form_patch({"filter": None}) == {"filter": None}
    for bad in ("nudity", ["none"], 3, True):
        with pytest.raises(T2iFormError, match="filter"):
            validate_form_patch({"filter": bad})


@pytest.mark.parametrize("bad", ["", "   ", None, 5, ["krea2"]])
def test_the_model_must_be_a_non_empty_string(bad):
    with pytest.raises(T2iFormError, match="model"):
        validate_form_patch({"model": bad})


def test_the_model_is_kept_verbatim():
    assert validate_form_patch({"model": "zimage"}) == {"model": "zimage"}


def test_the_preset_id_is_an_integer_or_null():
    assert validate_form_patch({"preset_id": 12}) == {"preset_id": 12}
    assert validate_form_patch({"preset_id": None}) == {"preset_id": None}
    for bad in (1.5, 3.0, "3", True, [3]):
        with pytest.raises(T2iFormError, match="preset_id"):
            validate_form_patch({"preset_id": bad})


def test_megapixels_are_a_positive_number_or_null():
    assert validate_form_patch({"megapixels": 0.5}) == {"megapixels": 0.5}
    assert validate_form_patch({"megapixels": None}) == {"megapixels": None}
    for bad in (0, -1, float("nan"), float("inf"), True, "1", 1000.5, HUGE_INT):
        with pytest.raises(T2iFormError, match="megapixels"):
            validate_form_patch({"megapixels": bad})


def test_the_aspect_ratio_is_w_colon_h_or_null():
    for ok in ("3:2", "16:9", "5:7"):
        assert validate_form_patch({"aspect_ratio": ok}) == {"aspect_ratio": ok}
    assert validate_form_patch({"aspect_ratio": None}) == {"aspect_ratio": None}
    for bad in ("", "3x2", "3:", "0:1", "3:2 ", "-3:2", 1.5, ["3:2"]):
        with pytest.raises(T2iFormError, match="aspect_ratio"):
            validate_form_patch({"aspect_ratio": bad})


def test_the_seed_is_an_integer_in_range_or_null():
    for ok in (0, 1, 321424, SEED_MAX):
        assert validate_form_patch({"seed": ok}) == {"seed": ok}
    assert validate_form_patch({"seed": None}) == {"seed": None}
    for bad in (-1, SEED_MAX + 1, 1.5, 5.0, "7", True, [7]):
        with pytest.raises(T2iFormError, match="seed"):
            validate_form_patch({"seed": bad})


def test_loras_are_validated_through_normalize_loras():
    assert validate_form_patch({"loras": []}) == {"loras": []}
    for bad in (
        None,
        "a.safetensors",
        [{"name": ""}],
        [{"name": "a", "strength": "x"}],
    ):
        with pytest.raises(T2iFormError, match="lora"):
            validate_form_patch({"loras": bad})


def test_an_unknown_field_is_an_error_naming_it():
    with pytest.raises(T2iFormError, match="bogus"):
        validate_form_patch({"seed": 1, "bogus": 2})


def test_fields_that_belong_to_the_session_are_not_form_state():
    # Seed policy, batch size and count per batch are actions, not properties
    # of an image; a PATCH carrying them is a caller bug, not a no-op.
    for name in ("seed_policy", "batch_size", "count_per_batch"):
        with pytest.raises(T2iFormError, match=name):
            validate_form_patch({name: 1})


def test_an_empty_patch_is_an_error():
    with pytest.raises(T2iFormError, match="No form fields"):
        validate_form_patch({})


@pytest.mark.parametrize("bad", [None, [("seed", 1)], "seed", 3])
def test_a_patch_must_be_a_mapping(bad):
    with pytest.raises(T2iFormError, match="mapping|object"):
        validate_form_patch(bad)


# ---- build_form_state ---------------------------------------------------


def _full_fields() -> dict:
    return {
        "mode": "manual",
        "filter": None,
        "caption": "a red kite over a gray sea",
        "model": "qwen",
        "preset_id": 3,
        "megapixels": 1,
        "aspect_ratio": "3:2",
        "seed": 1234,
        "prompt": "A red kite flies over a gray sea.",
        "negative": "blurry",
        "loras": [{"name": "kite.safetensors", "strength": "0.7"}],
    }


def test_build_form_state_holds_exactly_the_form_fields_normalised():
    state = build_form_state(**_full_fields())
    assert tuple(state) == T2I_FORM_FIELDS
    assert state["megapixels"] == 1.0 and isinstance(state["megapixels"], float)
    assert state["loras"] == [{"name": "kite.safetensors", "strength": 0.7}]


def test_build_form_state_fills_what_it_was_not_given():
    state = build_form_state(model="krea2", prompt="p", seed=5)
    assert tuple(state) == T2I_FORM_FIELDS
    assert state["mode"] == "manual"
    assert state["filter"] is None
    assert state["loras"] == []
    assert state["caption"] is None and state["negative"] is None
    assert state["preset_id"] is None and state["aspect_ratio"] is None
    assert state["megapixels"] is None
    assert (state["model"], state["prompt"], state["seed"]) == ("krea2", "p", 5)


def test_build_form_state_with_nothing_is_a_complete_blank_form():
    state = build_form_state()
    assert tuple(state) == T2I_FORM_FIELDS
    assert state["mode"] == "manual" and state["loras"] == []
    assert state["model"] is None and state["seed"] is None


def test_build_form_state_treats_no_loras_as_an_empty_list():
    assert build_form_state(loras=None)["loras"] == []


def test_build_form_state_is_json_serialisable():
    assert json.loads(json.dumps(build_form_state(**_full_fields())))["seed"] == 1234


def test_what_build_form_state_stores_is_accepted_back_as_a_patch():
    state = build_form_state(**_full_fields())
    assert validate_form_patch(state) == state


def test_build_form_state_rejects_an_unknown_field():
    with pytest.raises(T2iFormError, match="batch_size"):
        build_form_state(model="krea2", batch_size=4)


def test_build_form_state_rejects_a_bad_value_naming_the_field():
    with pytest.raises(T2iFormError, match="seed"):
        build_form_state(seed=-1)
    with pytest.raises(T2iFormError, match="mode"):
        build_form_state(mode="batch")


def test_build_form_state_does_not_alias_its_inputs():
    flt = {"nudity": ["none"]}
    state = build_form_state(mode="random", filter=flt)
    state["filter"]["nudity"].append("full")
    assert flt == {"nudity": ["none"]}


# ---- form_state_for_row -------------------------------------------------


def _row(**over: Any) -> dict:
    row = {
        "id": 9,
        "file_path": "/lib/out.png",
        "caption": "a red kite over a gray sea",
        "model": "qwen",
        "preset_id": 3,
        "megapixels": 1.0,
        "aspect_ratio": "3:2",
        "seed": 1234,
        "prompt_used": "A red kite flies over a gray sea.",
        "negative_used": "blurry",
        "loras": [{"name": "kite.safetensors", "strength": 0.7}],
        "width": 1248,
        "height": 832,
        "form_state": None,
    }
    row.update(over)
    return row


def test_a_row_without_form_state_is_completed_from_the_rendered_facts():
    state = form_state_for_row(_row())
    assert state == {
        "mode": "manual",
        "filter": None,
        "caption": "a red kite over a gray sea",
        "model": "qwen",
        "preset_id": 3,
        "megapixels": 1.0,
        "aspect_ratio": "3:2",
        "seed": 1234,
        "prompt": "A red kite flies over a gray sea.",
        "negative": "blurry",
        "loras": [{"name": "kite.safetensors", "strength": 0.7}],
    }
    assert tuple(state) == T2I_FORM_FIELDS


def test_a_stored_form_state_wins_field_by_field():
    stored = {"prompt": "edited prompt", "seed": 7, "mode": "random"}
    state = form_state_for_row(_row(form_state=stored))
    assert state["prompt"] == "edited prompt"
    assert state["seed"] == 7
    assert state["mode"] == "random"
    # everything the stored state lacks still comes from the facts
    assert state["model"] == "qwen"
    assert state["negative"] == "blurry"
    assert state["aspect_ratio"] == "3:2"


def test_a_stored_null_is_a_deliberate_clear_and_beats_the_fact():
    # The user emptied the negative box; reloading must not bring it back.
    state = form_state_for_row(_row(form_state={"negative": None, "caption": ""}))
    assert state["negative"] is None
    assert state["caption"] == ""


def test_a_complete_stored_form_state_is_returned_as_is():
    stored = build_form_state(**_full_fields())
    other_facts = _row(prompt_used="as rendered", seed=1, model="sd")
    assert form_state_for_row({**other_facts, "form_state": stored}) == stored


def test_keys_that_are_not_form_fields_are_never_leaked():
    state = form_state_for_row(_row(form_state={"seed": 2, "width": 1, "junk": 1}))
    assert tuple(state) == T2I_FORM_FIELDS
    assert state["seed"] == 2


def test_a_stored_loras_value_that_is_not_a_list_falls_back_to_the_facts():
    state = form_state_for_row(_row(form_state={"loras": None}))
    assert state["loras"] == [{"name": "kite.safetensors", "strength": 0.7}]
    state = form_state_for_row(_row(form_state={"loras": "x"}, loras=None))
    assert state["loras"] == []


def test_a_stored_mode_that_is_not_a_mode_falls_back_to_manual():
    assert form_state_for_row(_row(form_state={"mode": "banana"}))["mode"] == "manual"


@pytest.mark.parametrize("stored", [None, "x", 5, ["seed"]])
def test_a_form_state_that_is_not_a_mapping_is_ignored(stored):
    assert form_state_for_row(_row(form_state=stored))["seed"] == 1234


def test_a_row_with_no_facts_at_all_still_yields_the_complete_shape():
    state = form_state_for_row({})
    assert tuple(state) == T2I_FORM_FIELDS
    assert state["mode"] == "manual" and state["filter"] is None
    assert state["loras"] == []
    assert state["seed"] is None and state["prompt"] is None


def test_a_restart_row_with_only_the_job_facts_keeps_what_it_has():
    # After a server restart the ingest has no in-memory job meta, so the
    # model, caption and megapixels facts are NULL; the rest must survive.
    state = form_state_for_row(
        _row(model=None, caption=None, megapixels=None, aspect_ratio=None)
    )
    assert state["model"] is None and state["megapixels"] is None
    assert state["seed"] == 1234
    assert state["prompt"] == "A red kite flies over a gray sea."


def test_completing_a_row_never_mutates_it():
    row = _row(form_state={"loras": [{"name": "x", "strength": 1.0}]})
    snapshot = json.loads(json.dumps(row))
    state = form_state_for_row(row)
    state["loras"].append({"name": "y", "strength": 2.0})
    state["loras"][0]["strength"] = 5.0
    assert row == snapshot


# ---- render_seconds -----------------------------------------------------


def test_the_difference_between_two_iso_stamps():
    assert (
        render_seconds("2026-09-20T18:00:00+00:00", "2026-09-20T18:04:07.500000+00:00")
        == 247.5
    )


def test_the_result_is_rounded_to_a_tenth_of_a_second():
    assert (
        render_seconds("2026-09-20T18:00:00+00:00", "2026-09-20T18:00:10.260000+00:00")
        == 10.3
    )


def test_naive_timestamps_are_read_as_utc():
    assert render_seconds("2026-09-20T18:00:00", "2026-09-20T18:00:10") == 10.0
    assert render_seconds("2026-09-20T18:00:00", "2026-09-20T18:00:10+00:00") == 10.0
    assert render_seconds("2026-09-20T18:00:00+00:00", "2026-09-20T18:00:10") == 10.0


def test_explicit_offsets_are_honoured():
    assert (
        render_seconds("2026-09-20T20:00:00+02:00", "2026-09-20T18:00:05+00:00") == 5.0
    )


def test_a_zero_span_is_zero_not_none():
    assert render_seconds("2026-09-20T18:00:00", "2026-09-20T18:00:00") == 0.0


def test_a_negative_span_is_none():
    assert (
        render_seconds("2026-09-20T18:05:00+00:00", "2026-09-20T18:00:00+00:00") is None
    )


@pytest.mark.parametrize("start", [None, "", "not a date", 123, ["2026-09-20"]])
def test_a_missing_or_unreadable_start_is_none(start):
    assert render_seconds(start, None) is None
    assert render_seconds(start, "2026-09-20T18:00:00+00:00") is None


def test_an_unreadable_finish_is_none():
    assert render_seconds("2026-09-20T18:00:00+00:00", "yesterday") is None


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls(2026, 9, 20, 18, 1, 30, tzinfo=timezone.utc)


def test_a_missing_finish_means_now():
    # job_outputs fires before the job row is marked done, so the ingest
    # sees no finished_at yet.
    with patch("metascan.core.t2i_form.datetime", _FrozenDatetime):
        assert render_seconds("2026-09-20T18:00:00+00:00", None) == 90.0
        assert render_seconds("2026-09-20T18:00:00", "") == 90.0
