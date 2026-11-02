"""Upscale workflow presets: the bindings, the validator, the kind CHECK
rebuild and what ComfyClient does differently for an upscale job."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import (
    BindingError,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)
from metascan.core.comfy_client import ComfyClient, job_source
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.workflow_validation import validate_workflow
from tests._fake_comfy_server import fake_comfy  # noqa: F401


def node(class_type: str, title: str, **inputs) -> dict:
    return {"class_type": class_type, "_meta": {"title": title}, "inputs": inputs}


def image_workflow(**extra) -> dict:
    graph = {
        "1": node("LoadImage", "MS_SOURCE_IMAGE", image="baked.png"),
        "2": node("SaveImage", "MS_SAVE", images=["1", 0], filename_prefix="up"),
    }
    graph.update(extra)
    return graph


def video_workflow(widget: str = "video", audio: bool = False) -> dict:
    save = {"images": ["1", 0]}
    if audio:
        save["audio"] = ["1", 2]
    return {
        "1": node("VHS_LoadVideo", "MS_SOURCE_VIDEO", **{widget: "baked.mp4"}),
        "2": node("VHS_VideoCombine", "MS_SAVE", **save),
    }


def upscale_params(**kw) -> GenerationParams:
    values = dict(positive=None, seed=None, width=0, height=0, batch_size=1)
    values.update(kw)
    return GenerationParams(**values)


def test_an_upscale_needs_no_prompt_and_no_seed():
    b = resolve_bindings(image_workflow(), "upscale_image")
    assert b.source_image == "1" and b.save == "2"
    assert b.positive is None and b.seed is None and b.seed_widget is None


def test_every_other_kind_still_requires_prompt_and_seed():
    with pytest.raises(BindingError, match="MS_POSITIVE"):
        resolve_bindings(image_workflow(), "t2i")


def test_the_source_title_is_required_per_kind():
    with pytest.raises(BindingError, match="MS_SOURCE_VIDEO"):
        resolve_bindings(image_workflow(), "upscale_video")
    with pytest.raises(BindingError, match="MS_SOURCE_IMAGE"):
        resolve_bindings(video_workflow(), "upscale_image")


@pytest.mark.parametrize("widget", ["video", "file"])
def test_a_source_video_takes_either_loader_widget(widget):
    workflow = video_workflow(widget)
    b = resolve_bindings(workflow, "upscale_video")
    assert b.source_video_widget == widget
    graph = apply_overrides(workflow, b, upscale_params(source_video="in/clip.mp4"))
    assert graph["1"]["inputs"][widget] == "in/clip.mp4"


def test_a_source_video_with_neither_widget_is_refused():
    workflow = video_workflow()
    workflow["1"]["inputs"] = {"path": "x.mp4"}
    with pytest.raises(BindingError, match="MS_SOURCE_VIDEO"):
        resolve_bindings(workflow, "upscale_video")
    report = validate_workflow(workflow, "upscale_video")
    assert not report.ok
    assert any(f.code == "missing_widget" for f in report.findings)


def test_only_what_the_user_chose_is_written():
    workflow = image_workflow(
        **{
            "3": node("CLIPTextEncode", "MS_POSITIVE", text="baked prompt"),
            "4": node("KSampler", "MS_SEED", seed=7),
            "5": node("PrimitiveFloat", "MS_SCALE", value=4.0),
            "6": node("ImageResize", "MS_RESOLUTION", width=512, height=512),
        }
    )
    b = resolve_bindings(workflow, "upscale_image")
    graph = apply_overrides(workflow, b, upscale_params(source_image="a.png"))
    assert graph["1"]["inputs"]["image"] == "a.png"
    assert graph["3"]["inputs"]["text"] == "baked prompt"
    assert graph["4"]["inputs"]["seed"] == 7
    assert graph["5"]["inputs"]["value"] == 4.0
    assert graph["6"]["inputs"] == {"width": 512, "height": 512}

    graph = apply_overrides(
        workflow,
        b,
        upscale_params(source_image="a.png", positive="new", seed=9, scale=2),
    )
    assert graph["3"]["inputs"]["text"] == "new"
    assert graph["4"]["inputs"]["seed"] == 9
    assert graph["5"]["inputs"]["value"] == 2.0


@pytest.mark.parametrize(
    "supplied, title",
    [
        ({"positive": "x"}, "MS_POSITIVE"),
        ({"seed": 1}, "MS_SEED"),
        ({"scale": 2.0}, "MS_SCALE"),
        ({"source_video": "x.mp4"}, "MS_SOURCE_VIDEO"),
    ],
)
def test_a_value_the_workflow_cannot_take_raises(supplied, title):
    workflow = image_workflow()
    b = resolve_bindings(workflow, "upscale_image")
    with pytest.raises(BindingError, match=title):
        apply_overrides(workflow, b, upscale_params(**supplied))


def test_bindings_round_trip_through_json():
    b = resolve_bindings(video_workflow("file"), "upscale_video")
    assert type(b).from_json(b.to_json()) == b


def test_a_video_workflow_without_audio_warns():
    report = validate_workflow(video_workflow(), "upscale_video")
    assert report.ok
    assert [f.code for f in report.findings] == ["no_audio_output"]


def test_audio_wired_at_the_save_node_or_upstream_does_not_warn():
    assert validate_workflow(video_workflow(audio=True), "upscale_video").findings == []
    graph = {
        "1": node("VHS_LoadVideo", "MS_SOURCE_VIDEO", video="a.mp4"),
        "2": node("CreateVideo", "", images=["1", 0], audio=["1", 2]),
        "3": node("SaveVideo", "MS_SAVE", video=["2", 0]),
    }
    assert validate_workflow(graph, "upscale_video").findings == []


def test_an_image_workflow_has_no_audio_finding():
    assert validate_workflow(image_workflow(), "upscale_image").findings == []


def test_a_source_title_typo_gets_a_fix():
    workflow = image_workflow()
    workflow["1"]["_meta"]["title"] = "MS_SOURCE_IMGAE"
    report = validate_workflow(workflow, "upscale_image")
    assert not report.ok
    assert [f.title for f in report.fixes] == ["MS_SOURCE_IMAGE"]


# ---- the kind CHECK rebuild ---------------------------------------------------


OLD_DDL = """
CREATE TABLE workflow_presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK(kind IN ('t2i','ref','ref2v')),
    workflow_json TEXT NOT NULL,
    bindings      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    {extra}
)
"""


def old_database(db_dir: Path, tagged: bool) -> Path:
    """A database as a build before the upscale kinds left it, with a job
    pointing at its preset."""
    fresh = DatabaseManager(db_dir)
    conn = sqlite3.connect(str(fresh.db_file))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE workflow_presets")
    extra = ", video_target TEXT, video_mode TEXT" if tagged else ""
    conn.execute(OLD_DDL.format(extra=extra))
    if tagged:
        conn.execute(
            "INSERT INTO workflow_presets (id, name, kind, workflow_json, bindings, "
            "created_at, updated_at, video_target, video_mode) VALUES "
            "(5, 'h3', 'ref2v', '{}', '{}', 'c', 'u', 'minimax', 'i2va')"
        )
    else:
        conn.execute(
            "INSERT INTO workflow_presets (id, name, kind, workflow_json, bindings, "
            "created_at, updated_at) VALUES (5, 'h3', 'ref2v', '{}', '{}', 'c', 'u')"
        )
    conn.execute("INSERT INTO generation_jobs (preset_id, params) VALUES (5, '{}')")
    conn.commit()
    conn.close()
    return Path(fresh.db_file)


def read_preset(db: DatabaseManager) -> dict:
    conn = sqlite3.connect(str(db.db_file))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM workflow_presets WHERE id = 5").fetchone()
        return dict(row)
    finally:
        conn.close()


def test_the_rebuild_keeps_every_preset_and_its_tag(tmp_path):
    old_database(tmp_path, tagged=True)
    db = DatabaseManager(tmp_path)
    row = read_preset(db)
    assert row["video_target"] == "minimax" and row["video_mode"] == "i2va"
    assert (row["name"], row["kind"]) == ("h3", "ref2v")
    assert (row["created_at"], row["updated_at"]) == ("c", "u")
    assert db.count_jobs_for_preset(5) == 1


def test_the_rebuild_takes_a_database_from_before_the_tag(tmp_path):
    old_database(tmp_path, tagged=False)
    db = DatabaseManager(tmp_path)
    row = read_preset(db)
    assert row["kind"] == "ref2v" and row["video_target"] is None


def test_the_rebuild_recovers_from_a_leftover_migration_table(tmp_path):
    db_file = old_database(tmp_path, tagged=True)
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE workflow_presets_kind_migration (id INTEGER)")
    conn.commit()
    conn.close()
    assert read_preset(DatabaseManager(tmp_path))["video_mode"] == "i2va"


def test_the_rebuild_runs_once(tmp_path):
    old_database(tmp_path, tagged=True)
    DatabaseManager(tmp_path)
    db = DatabaseManager(tmp_path)
    assert (
        "upscale_video"
        in sqlite3.connect(str(db.db_file))
        .execute("SELECT sql FROM sqlite_master WHERE name = 'workflow_presets'")
        .fetchone()[0]
    )


def test_a_new_database_takes_the_new_kinds(tmp_path):
    db = DatabaseManager(tmp_path)
    conn = sqlite3.connect(str(db.db_file))
    for kind in ("upscale_image", "upscale_video"):
        conn.execute(
            "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
            "VALUES (?, ?, '{}', '{}')",
            (kind, kind),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO workflow_presets (name, kind, workflow_json, bindings) "
            "VALUES ('x', 'nope', '{}', '{}')"
        )
    conn.close()


# ---- ComfyClient ---------------------------------------------------------------


class CountingScanner:
    def __init__(self) -> None:
        self.ingested = []

    def ingest_file(self, path):
        self.ingested.append(path)
        return None


@pytest.fixture
async def comfy(fake_comfy):  # noqa: F811
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        c = ComfyClient(
            base_url=fake_comfy.base_url,
            output_root=Path(tmp) / "out",
            db=DatabaseManager(Path(tmp) / "db"),
            scanner=CountingScanner(),
        )
        c.tmp = Path(tmp)
        await c.start()
        try:
            yield c
        finally:
            await c.shutdown()


def test_job_source_knows_upscale():
    assert job_source(upscale_source_path="/a.png") == "upscale"


async def test_an_upscale_output_is_written_but_never_ingested(comfy):
    pid = await comfy.register_preset("up", "upscale_image", image_workflow())
    target = comfy.tmp / "staging"
    job = await comfy.submit(
        pid,
        upscale_params(source_image="a.png"),
        output_dir=target,
        output_name="candidate",
        upscale_source_path="/lib/a.png",
    )
    row = await comfy.wait_for_job(job, timeout=10)
    assert row["state"] == "done" and row["upscale_source_path"] == "/lib/a.png"
    assert sorted(p.name for p in target.iterdir())[0] == "candidate.png"
    assert comfy.scanner.ingested == []


async def test_a_download_leaves_no_partial_file(comfy):
    pid = await comfy.register_preset("up", "upscale_image", image_workflow())
    target = comfy.tmp / "staging"
    job = await comfy.submit(
        pid,
        upscale_params(source_image="a.png"),
        output_dir=target,
        upscale_source_path="/lib/a.png",
    )
    await comfy.wait_for_job(job, timeout=10)
    assert not list(target.glob("*.part"))


async def test_a_failed_download_removes_its_partial_file(comfy, monkeypatch):
    target = comfy.tmp / "broken.png"

    class Boom:
        async def __aenter__(self):
            raise RuntimeError("connection lost")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(comfy._http, "stream", lambda *a, **k: Boom())
    with pytest.raises(RuntimeError):
        await comfy._download_image({"filename": "x.png"}, target)
    assert not target.exists()
    assert not target.with_name("broken.png.part").exists()


async def test_the_upscale_source_can_be_paused(comfy, fake_comfy):  # noqa: F811
    pid = await comfy.register_preset("up", "upscale_image", image_workflow())
    comfy.set_source_paused("upscale", True)
    job = await comfy.submit(
        pid,
        upscale_params(source_image="a.png"),
        output_dir=comfy.tmp / "staging",
        upscale_source_path="/lib/a.png",
    )
    assert comfy.db.get_generation_job(job)["state"] == "queued"
    assert comfy.has_gated_jobs("upscale")
    assert comfy.source_job_count("upscale") == 1
    assert await comfy.cancel_source("upscale") == 1
    assert fake_comfy.submitted == []
