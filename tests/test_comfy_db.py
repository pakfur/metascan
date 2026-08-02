"""CRUD tests for workflow_presets and generation_jobs.

Uses an isolated temp DB, following tests/test_folders_db.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield DatabaseManager(Path(tmp))


def test_create_and_get_preset(db):
    pid = db.create_workflow_preset("sdxl-sb", "t2i", '{"3": {}}', '{"positive": "6"}')
    assert isinstance(pid, int)

    row = db.get_workflow_preset(pid)
    assert row["name"] == "sdxl-sb"
    assert row["kind"] == "t2i"
    assert row["workflow_json"] == '{"3": {}}'
    assert row["bindings"] == '{"positive": "6"}'
    assert row["created_at"]


def test_get_missing_preset_returns_none(db):
    assert db.get_workflow_preset(9999) is None


def test_list_presets_omits_the_workflow_blob(db):
    db.create_workflow_preset("a", "t2i", '{"big": "blob"}', "{}")
    db.create_workflow_preset("b", "ref", '{"big": "blob"}', "{}")
    rows = db.list_workflow_presets()
    assert [r["name"] for r in rows] == ["a", "b"]
    assert "workflow_json" not in rows[0]


def test_preset_name_is_unique(db):
    db.create_workflow_preset("dup", "t2i", "{}", "{}")
    with pytest.raises(Exception):
        db.create_workflow_preset("dup", "t2i", "{}", "{}")


def test_delete_preset(db):
    pid = db.create_workflow_preset("gone", "t2i", "{}", "{}")
    assert db.delete_workflow_preset(pid) is True
    assert db.get_workflow_preset(pid) is None
    assert db.delete_workflow_preset(pid) is False


def test_invalid_kind_is_rejected(db):
    with pytest.raises(Exception):
        db.create_workflow_preset("bad", "video", "{}", "{}")


def test_create_job_defaults_to_queued(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, '{"seed": 1}')
    job = db.get_generation_job(jid)
    assert job["state"] == "queued"
    assert job["preset_id"] == pid
    assert job["params"] == '{"seed": 1}'
    assert job["panel_id"] is None
    assert job["comfy_prompt_id"] is None
    assert job["error"] is None


def test_job_accepts_a_panel_id_without_a_panels_table(db):
    """panel_id is a plain column — Phase B's panels table does not exist yet."""
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}", panel_id=1234)
    assert db.get_generation_job(jid)["panel_id"] == 1234


def test_update_job_fields(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")

    db.update_generation_job(jid, state="running", comfy_prompt_id="abc-123")
    job = db.get_generation_job(jid)
    assert job["state"] == "running"
    assert job["comfy_prompt_id"] == "abc-123"

    db.update_generation_job(jid, state="failed", error="CheckpointLoaderSimple: nope")
    assert db.get_generation_job(jid)["error"] == "CheckpointLoaderSimple: nope"


def test_update_job_rejects_unknown_fields(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    with pytest.raises(ValueError):
        db.update_generation_job(jid, sneaky="value")


def test_update_job_rejects_invalid_state(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    with pytest.raises(Exception):
        db.update_generation_job(jid, state="exploded")


def test_lookup_job_by_comfy_prompt_id(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    jid = db.create_generation_job(pid, "{}")
    db.update_generation_job(jid, comfy_prompt_id="pid-9")
    assert db.get_job_by_comfy_prompt_id("pid-9")["id"] == jid
    assert db.get_job_by_comfy_prompt_id("nope") is None


def test_list_jobs_filters_by_state(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    a = db.create_generation_job(pid, "{}")
    b = db.create_generation_job(pid, "{}")
    db.update_generation_job(b, state="done")

    assert [j["id"] for j in db.list_generation_jobs(states=["queued"])] == [a]
    assert {j["id"] for j in db.list_generation_jobs()} == {a, b}


def test_deleting_a_preset_is_blocked_while_jobs_reference_it(db):
    pid = db.create_workflow_preset("p", "t2i", "{}", "{}")
    db.create_generation_job(pid, "{}")
    with pytest.raises(Exception):
        db.delete_workflow_preset(pid)
