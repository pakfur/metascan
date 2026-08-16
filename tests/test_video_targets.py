"""Per-target shot-duration caps (spec V3 §10.3)."""

from metascan.core.video_targets import DEFAULT_SHOT_CAP, TARGET_CAPS, shot_cap


def test_minimax_cap_is_15():
    assert TARGET_CAPS["minimax"] == 15.0
    assert shot_cap("minimax") == 15.0


def test_unknown_and_none_fall_back_to_default():
    assert shot_cap(None) == DEFAULT_SHOT_CAP == 15.0
    assert shot_cap("ltx") == DEFAULT_SHOT_CAP  # reserved, not yet capped
