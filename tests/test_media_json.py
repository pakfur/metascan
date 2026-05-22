"""Tests for Media.from_json_fast tolerance of non-strict JSON values.

Real-world rows in the SQLite ``media.data`` column can contain bare ``NaN``
or ``Infinity`` literals — Python's stdlib ``json.dumps`` (used transitively
via ``dataclasses_json``) emits them, and ComfyUI workflow payloads stored
in ``generation_data`` (e.g. node ``is_changed`` validation hashes) are a
common source. ``orjson.loads`` rejects those literals, so the read path
must fall back to a tolerant parser instead of dropping the whole row.
"""

import json

from metascan.core.media import Media


_BASE = {
    "file_path": "/tmp/ComfyUI_00073_.png",
    "file_size": 100,
    "width": 512,
    "height": 512,
    "format": "PNG",
    "created_at": "2026-01-01T00:00:00",
    "modified_at": "2026-01-01T00:00:00",
}


def test_from_json_fast_tolerates_nan_in_generation_data():
    payload = json.dumps(
        {**_BASE, "generation_data": {"node_42": {"is_changed": float("nan")}}},
        allow_nan=True,
    )
    assert "NaN" in payload  # guard against test going stale silently

    m = Media.from_json_fast(payload)

    assert m.file_size == 100
    assert "node_42" in m.generation_data


def test_from_json_fast_tolerates_infinity_in_generation_data():
    payload = json.dumps(
        {**_BASE, "generation_data": {"x": float("inf"), "y": float("-inf")}},
        allow_nan=True,
    )
    assert "Infinity" in payload

    m = Media.from_json_fast(payload)

    assert m.generation_data["x"] == float("inf")
    assert m.generation_data["y"] == float("-inf")


def test_from_json_fast_strict_payload_unchanged():
    """Strict JSON (the fast path) still works."""
    payload = json.dumps({**_BASE, "generation_data": {"k": 1}})

    m = Media.from_json_fast(payload)

    assert m.generation_data == {"k": 1}
