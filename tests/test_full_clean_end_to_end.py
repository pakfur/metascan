"""Full clean & rescan with every full-clean rule at once: the taste
snapshot's briefs and library prompts, the lineage prune pause and marker
with the re-ingest of generated output, the unavailable-folder rules and the
FAISS evidence of restore tombstones.

One library: a ★ ✓ image (briefed) in a scan folder that stays, a ✓ image
there whose file was deleted behind the library's back (briefed, with a
metadata prompt), a ✓ image (briefed, a reason, a category) on a drive that
is unplugged during the rescan, and a ✓ T2I render (briefed) in ComfyUI's
output folder, outside every scan folder. Real folders and PNGs, the real
Scanner, the real route, and the guard and lookup the lifespan installs."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from backend.api import scan as scan_api
from backend.services import taste_snapshot as ts
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
BRIEF_MODEL = "qwen3vl-8b"
MODELS = ("krea2_dev.safetensors", "vae_ft.safetensors")
T2I_LINEAGE = {
    "batch_id": "b" * 32,
    "model": "krea2",
    "caption": "a red kite over a gray sea",
    "prompt_used": "A red kite flies over a gray sea.",
    "writer_id": "base+taste",
    "writer_model": "qwen3-vl-8b",
    "variant_group": "vg-1",
}


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _png(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)
    return path


def _start_full_clean():
    """POST /api/scan/start {full_clean} as the route runs it, then wait for
    the scan it started. The HTTPException of a refusal, else None."""

    async def go():
        try:
            await scan_api.start_scan(scan_api.ScanRequest(full_clean=True))
        except HTTPException as e:
            return e
        await scan_api._scan_task
        return None

    return asyncio.run(go())


def _pending(db):
    target = ts.snapshot_file_path(Path(db.db_path))
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def _marker(db) -> Path:
    return ts.full_clean_marker_path(Path(db.db_path))


def _brief(path: Path) -> dict:
    return {
        "brief": f"a brief of {path.name}",
        "model": BRIEF_MODEL,
        "created_at": T0.isoformat(),
    }


def _ratings(db, path: Path):
    j = db.get_judgment(str(path))
    return (j.favorite, j.grade, j.reasons, [c.name for c in j.categories])


def test_a_full_clean_with_a_drive_lost_mid_run_keeps_everything_until_it_completes(
    monkeypatch, tmp_path, db
):
    lib, drive = tmp_path / "lib", tmp_path / "drive"
    unplugged = tmp_path / "unplugged"
    here, gone = _png(lib / "here.png"), _png(lib / "gone.png")
    away = _png(drive / "away.png")
    gen = _png(tmp_path / "comfy_output" / "t2i" / "gen.png")
    scanner = Scanner(db)
    for folder in (lib, drive):
        scanner.scan_directory(str(folder))
    assert scanner.ingest_file(gen) is not None  # what ComfyClient does
    t2i_id = db.create_t2i_image(file_path=str(gen), **T2I_LINEAGE)
    with db.lock, db._get_connection() as conn:  # gone.png's metadata prompt
        conn.execute(
            "UPDATE media SET data = json_set(data, '$.prompt', ?) "
            "WHERE file_path = ?",
            ("a fox in the snow", str(gone)),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO indices (index_type, index_key, file_path) "
            "VALUES ('model', ?, ?)",
            [(m, str(gone)) for m in MODELS],
        )
        conn.commit()
    night = db.create_category("Night")
    db.set_favorite_state(str(here), True, now=T0)
    for path in (here, gone, away, gen):
        db.set_grade(str(path), "pass", now=T0)
        db.set_hindsight_brief(str(path), _brief(path)["brief"], BRIEF_MODEL, now=T0)
    db.set_reason(str(away), "light", True, now=T0)
    db.add_media_categories([str(away)], night.id, "viewer")
    gone.unlink()  # deleted on disk behind the library's back
    events_before = db.list_judgment_events()

    # What the lifespan installs: the lineage prune guard and the FAISS
    # lookup (here an index that would answer for any path).
    db.set_lineage_prune_guard(ts.lineage_prune_guard(Path(db.db_path)))
    db.set_embedding_lookup(
        lambda p: (np.ones(32, dtype=np.float32), "clip-small", float("inf"))
    )

    events = []
    listed_mid_run = []
    unplug = {"now": True}

    class UnplugsTheDrive(Scanner):
        def scan_directory(self, directory, *args, **kwargs):
            n = super().scan_directory(directory, *args, **kwargs)
            if Path(directory) == lib and unplug["now"]:
                listed_mid_run.append(db.list_t2i_images())  # the T2I dialog
                drive.rename(unplugged)  # the drive is unplugged
            return n

    async def broadcast(channel, event, data=None):
        events.append((event, data))

    config = {
        "directories": [
            {"filepath": str(f), "search_subfolders": True} for f in (lib, drive)
        ]
    }
    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: None)
    monkeypatch.setattr(scan_api, "Scanner", UnplugsTheDrive)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: config)
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(scan_api, "_scan_task", None)
    monkeypatch.setattr(scan_api, "_cancel_requested", False)

    # 1. Both folders are there at the start; the drive goes during the run.
    assert _start_full_clean() is None

    assert not [e for e in events if e[0] == "error"]
    [complete] = [data for event, data in events if event == "complete"]
    assert complete["missing_folders"] == [str(drive)]
    # Incomplete: nothing tombstoned (not even the really deleted gone.png),
    # the marker kept, the lineage row never pruned.
    assert db.list_tombstones() == []
    assert _marker(db).exists()
    assert listed_mid_run == [[]]  # left out mid-rescan, not deleted
    [row] = db.list_t2i_images()
    assert row["id"] == t2i_id and row["grade"] == "pass"
    assert {k: row[k] for k in T2I_LINEAGE} == T2I_LINEAGE
    # What came back is restored, the generated render by its re-ingest.
    assert set(db.get_existing_file_paths()) == {str(here), str(gen)}
    assert _ratings(db, here) == (True, "pass", [], [])
    assert _ratings(db, gen) == (False, "pass", [], [])
    briefs = db.get_hindsight_briefs()
    assert {p: briefs[p] for p in briefs} == {
        str(here): _brief(here),
        str(gen): _brief(gen),
    }
    # The rest waits in the pending file, briefs and prompt included.
    pending = _pending(db)
    assert sorted(j["file_path"] for j in pending["judgments"]) == [
        str(away),
        str(gone),
    ]
    assert {b["file_path"]: b["brief"] for b in pending["briefs"]} == {
        str(away): _brief(away)["brief"],
        str(gone): _brief(gone)["brief"],
    }
    assert pending["library_prompts"] == [
        {
            "file_path": str(gone),
            "prompt": "a fox in the snow",
            "model_name": json.dumps(sorted(MODELS)),
        }
    ]
    assert [lk["file_path"] for lk in pending["categories"]] == [str(away)]

    # 2. While the drive is away a full clean is refused, with nothing
    # written: the marker and the pending file stay as they are.
    unplug["now"] = False
    written = (_marker(db), ts.snapshot_file_path(Path(db.db_path)))

    def as_written():
        return [(f.read_bytes(), f.stat().st_mtime_ns) for f in written]

    before = as_written()
    events.clear()
    refused = _start_full_clean()
    assert refused is not None and refused.status_code == 409
    assert refused.detail["code"] == "scan_folders_missing"
    assert refused.detail["folders"] == [str(drive)]
    assert events == []
    assert as_written() == before
    # The scan task checks again before it writes anything, the marker
    # included (a direct caller, or a drive lost after the route's check).
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    [error] = [data for event, data in events if event == "error"]
    assert (error["code"], error["folders"]) == ("scan_folders_missing", [str(drive)])
    assert as_written() == before
    events.clear()

    # 3. The drive is back: the next full clean completes.
    unplugged.rename(drive)
    assert _start_full_clean() is None

    assert not [e for e in events if e[0] == "error"]
    [complete] = [data for event, data in events if event == "complete"]
    assert complete["missing_folders"] == []
    assert set(db.get_existing_file_paths()) == {str(here), str(away), str(gen)}
    assert _ratings(db, here) == (True, "pass", [], [])
    assert _ratings(db, away) == (False, "pass", ["light"], ["Night"])
    assert db.get_judgment(str(away)).confirmed_at == T0.isoformat()
    assert _ratings(db, gen) == (False, "pass", [], [])
    briefs = db.get_hindsight_briefs()
    assert {p: briefs[p] for p in briefs} == {
        str(here): _brief(here),
        str(away): _brief(away),
        str(gen): _brief(gen),
    }
    [row] = db.list_t2i_images()
    assert row["id"] == t2i_id and {k: row[k] for k in T2I_LINEAGE} == T2I_LINEAGE
    # Only the file really gone is tombstoned: with its grade, brief and
    # metadata prompt from the snapshot, and no FAISS evidence (the truncate
    # took its embedding record), so no vector despite the lookup.
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (str(gone), "pass")
    assert (t["brief"], t["brief_model"]) == (_brief(gone)["brief"], BRIEF_MODEL)
    assert (t["prompt"], t["model_name"]) == (
        "a fox in the snow",
        json.dumps(sorted(MODELS)),
    )
    assert (t["embedded_model"], t["embedded_at"]) == (None, None)
    assert db.get_tombstone_vectors(t["id"]) == {}
    # Finished: no pending file, no marker; the restore logged no events.
    assert _pending(db) is None and not _marker(db).exists()
    assert db.list_judgment_events() == events_before
