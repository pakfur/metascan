"""Export from one database, import into a fresh one under another root."""

import base64
import json
import sqlite3
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import library_transfer as transfer_api
from backend.api import scan as scan_api
from backend.services import library_transfer_service as service
from backend.services import taste_snapshot
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.library_transfer import (
    BUNDLE_FORMAT,
    DATA_NAME,
    MANIFEST_NAME,
    NOT_TRANSFERRED_TABLES,
    TRANSFERRED_TABLES,
    BundleError,
    MediaResolver,
    PathMap,
    bundle_members,
    export_library_data,
    extract_thumbnails,
    import_library_data,
    read_bundle,
    suggest_targets,
    validate_data,
    write_bundle,
)
from metascan.core.media import Media

OLD = "/mnt/d/library"
NEW = "/data/pictures"


def seed_media(db: DatabaseManager, paths: Iterable[str]) -> None:
    for path in paths:
        db.save_media(
            Media(
                file_path=Path(path),
                file_size=1,
                width=1,
                height=1,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )


def stored_paths(db: DatabaseManager) -> List[str]:
    return db.all_media_paths()


def execute(db: DatabaseManager, sql: str, args: Tuple[Any, ...] = ()) -> None:
    with db.lock, db._get_connection() as conn:
        conn.execute(sql, args)
        conn.commit()


def rows(db: DatabaseManager, sql: str, args: Tuple[Any, ...] = ()) -> List[Any]:
    with db.lock, db._get_connection() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def seed_source(db: DatabaseManager, data_dir: Path) -> Dict[str, Any]:
    """One of each kind of data, under ``OLD``."""
    a, b, c = f"{OLD}/a.png", f"{OLD}/m/b.png", f"{OLD}/z/c.png"
    seed_media(db, [a, b, c])
    a, b, c = sorted(stored_paths(db))[0:3]
    execute(db, "UPDATE media SET is_favorite = 1 WHERE file_path = ?", (a,))
    execute(db, "UPDATE media SET playback_speed = 1.5 WHERE file_path = ?", (b,))
    execute(
        db,
        "INSERT INTO media_judgments (file_path, grade, reasons, seen_at, "
        "judged_at, confirmed_at) VALUES (?, 'fail', '[\"blur\"]', '2020-01-01', "
        "'2020-01-02', '2020-01-03')",
        (b,),
    )
    execute(
        db,
        "INSERT INTO judgment_events (file_path, event, score_shown, created_at) "
        "VALUES (?, 'grade_fail', 0.25, '2020-01-02')",
        (b,),
    )
    execute(db, "INSERT INTO categories (name, created_at) VALUES ('cats', '2020')")
    execute(
        db,
        "INSERT INTO media_categories (file_path, category_id, source, assigned_at) "
        "SELECT ?, id, 'manual', '2020' FROM categories",
        (a,),
    )
    execute(
        db,
        "INSERT INTO hindsight_briefs VALUES (?, 'a brief', 'model', '2020')",
        (a,),
    )
    execute(
        db,
        "INSERT INTO saved_prompts (file_path, name, prompt, target_model, "
        "architecture, mode, created_at) VALUES (?, 'p', 'text', 'm', 'x', "
        "'generate', '2020')",
        (a,),
    )
    execute(
        db,
        "INSERT INTO folders (id, kind, name, created_at, updated_at) "
        "VALUES ('f1', 'manual', 'Mine', 1.0, 2.0)",
    )
    execute(
        db,
        "INSERT INTO folder_items (folder_id, file_path, added_at) "
        "VALUES ('f1', ?, 3.0)",
        (a,),
    )
    preset_id = db.create_workflow_preset("Flux", "t2i", "{}", "{}")
    execute(
        db,
        "INSERT INTO writer_adapters (id, name, scope, kind, base_model, status, "
        "created_at, hyperparams, counts, file_path) VALUES (7, 'adapter-7', 'all', "
        "'t2i', 'base', 'ready', '2020', '{}', '{}', 'weights.gguf')",
    )
    execute(
        db,
        "INSERT INTO t2i_images (file_path, preset_id, prompt_used, writer_id, "
        "form_state) VALUES (?, ?, 'a prompt', 'adapter:7', ?)",
        (c, preset_id, json.dumps({"preset_id": preset_id, "seed": 5})),
    )
    execute(
        db,
        "INSERT INTO i2v_videos (source_path, file_path, form_state) "
        'VALUES (?, ?, \'{"idea": "walk"}\')',
        (a, b),
    )
    stem = uuid.uuid4().hex
    thumb = data_dir / "archive" / "thumbs" / f"{stem}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"jpeg")
    gone = f"{OLD}/gone.png"
    lineage = {"t2i": {"file_path": gone, "writer_id": "adapter:7", "preset_id": 1}}
    execute(
        db,
        "INSERT INTO media_tombstones (file_path, deleted_at, is_favorite, grade, "
        "reasons, lineage, thumb_path, embedded_model, embedded_at) VALUES "
        "(?, '2020-02-02', 0, 'pass', '[]', ?, ?, 'clip', '2020')",
        (gone, json.dumps(lineage), f"archive/thumbs/{stem}.jpg"),
    )
    vector = np.arange(32, dtype=np.float32).tobytes()
    execute(
        db,
        "INSERT INTO tombstone_vectors SELECT id, 'clip', ?, '2020' "
        "FROM media_tombstones",
        (vector,),
    )
    return {"a": a, "b": b, "c": c, "preset": preset_id, "vector": vector}


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Tuple[DatabaseManager, Path, Dict[str, Any]]]:
    data_dir = tmp_path / "old"
    data_dir.mkdir()
    db = DatabaseManager(data_dir)
    yield db, data_dir, seed_source(db, data_dir)


@pytest.fixture
def target(tmp_path: Path) -> Tuple[DatabaseManager, Path]:
    data_dir = tmp_path / "new"
    data_dir.mkdir()
    return DatabaseManager(data_dir), data_dir


def relative_files(source_paths: Dict[str, Any]) -> List[str]:
    return [
        f"{NEW}/{Path(source_paths[k]).as_posix().split('/library/', 1)[1]}"
        for k in ("a", "b", "c")
    ]


def old_root(db: DatabaseManager) -> str:
    return sorted(stored_paths(db))[0].rsplit("/", 1)[0]


def transfer(
    db_from: DatabaseManager,
    dir_from: Path,
    db_to: DatabaseManager,
    dir_to: Path,
    tmp_path: Path,
) -> Dict[str, Dict[str, int]]:
    root = old_root(db_from)
    bundle = tmp_path / f"{uuid.uuid4().hex}.zip"
    write_bundle(bundle, export_library_data(db_from), [root], dir_from)
    _, data = read_bundle(bundle)
    extract_thumbnails(bundle, data, dir_to)
    return import_library_data(
        db_to, data, PathMap({root: NEW}), bundle_members(bundle)
    )


def test_every_table_is_classified(target: Tuple[DatabaseManager, Path]) -> None:
    db, _ = target
    tables = {
        r["name"] for r in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not TRANSFERRED_TABLES & NOT_TRANSFERRED_TABLES
    assert tables == TRANSFERRED_TABLES | NOT_TRANSFERRED_TABLES


def test_everything_arrives_under_the_new_root(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    files = relative_files(seeded)
    seed_media(db_to, files)
    a, b, c = sorted(stored_paths(db_to))[0:3]

    counts = transfer(db, dir_from, db_to, dir_to, tmp_path)

    assert not any(c_["not_found"] for c_ in counts.values())
    media = {
        r["file_path"]: r
        for r in rows(db_to, "SELECT file_path, is_favorite, playback_speed FROM media")
    }
    assert media[a]["is_favorite"] == 1
    assert media[b]["playback_speed"] == 1.5
    judgment = rows(db_to, "SELECT * FROM media_judgments")[0]
    assert judgment["file_path"] == b
    assert judgment["grade"] == "fail"
    assert judgment["confirmed_at"] == "2020-01-03"
    assert rows(db_to, "SELECT score_shown FROM judgment_events") == [
        {"score_shown": 0.25}
    ]
    assert rows(db_to, "SELECT file_path FROM media_categories") == [{"file_path": a}]
    assert rows(db_to, "SELECT brief FROM hindsight_briefs") == [{"brief": "a brief"}]
    assert rows(db_to, "SELECT file_path FROM saved_prompts") == [{"file_path": a}]
    assert rows(db_to, "SELECT file_path FROM folder_items") == [{"file_path": a}]
    preset = rows(db_to, "SELECT id FROM workflow_presets WHERE name = 'Flux'")[0]["id"]
    adapter = rows(db_to, "SELECT * FROM writer_adapters")[0]
    assert adapter["status"] == "deleted"
    assert adapter["deleted_at"]
    assert adapter["file_path"] is None
    t2i = rows(db_to, "SELECT * FROM t2i_images")[0]
    assert t2i["file_path"] == c
    assert t2i["preset_id"] == preset
    assert t2i["writer_id"] == f"adapter:{adapter['id']}"
    assert json.loads(t2i["form_state"]) == {"preset_id": preset, "seed": 5}
    i2v = rows(db_to, "SELECT * FROM i2v_videos")[0]
    assert (i2v["source_path"], i2v["file_path"]) == (a, b)
    assert json.loads(i2v["form_state"]) == {"idea": "walk"}


def test_what_a_media_import_makes_is_left_alone(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))
    queries = (
        "SELECT file_path, data, created_at FROM media ORDER BY file_path",
        "SELECT * FROM indices ORDER BY file_path, index_type, index_key",
        "SELECT * FROM media_hashes ORDER BY file_path",
    )
    before = [rows(db_to, sql) for sql in queries]

    transfer(db, dir_from, db_to, dir_to, tmp_path)

    assert [rows(db_to, sql) for sql in queries] == before
    assert not rows(db_to, "SELECT * FROM taste_models")


def test_a_tombstone_keeps_its_signal_but_no_faiss_evidence(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))

    transfer(db, dir_from, db_to, dir_to, tmp_path)

    tombstone = rows(db_to, "SELECT * FROM media_tombstones")[0]
    assert tombstone["file_path"].endswith("/gone.png")
    assert tombstone["file_path"].startswith(NEW)
    assert tombstone["grade"] == "pass"
    assert tombstone["embedded_model"] == ""
    assert tombstone["embedded_at"] is None
    assert tombstone["path_rewritten"] == 1
    lineage = json.loads(tombstone["lineage"])["t2i"]
    assert lineage["file_path"].startswith(NEW)
    adapter = rows(db_to, "SELECT id FROM writer_adapters")[0]["id"]
    assert lineage["writer_id"] == f"adapter:{adapter}"
    assert (dir_to / tombstone["thumb_path"]).read_bytes() == b"jpeg"
    vector = rows(db_to, "SELECT model_key, embedding FROM tombstone_vectors")[0]
    assert vector["model_key"] == "clip"
    assert bytes(vector["embedding"]) == seeded["vector"]
    assert db_to.tombstones_for_vector_backfill("clip", time.time()) == []


def test_importing_twice_adds_nothing(source: Any, target: Any, tmp_path: Path) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))

    transfer(db, dir_from, db_to, dir_to, tmp_path)
    second = transfer(db, dir_from, db_to, dir_to, tmp_path)

    # A judgment row reads as newer once it is in: "already".
    assert not any(c["added"] for c in second.values())
    for table in (
        "judgment_events",
        "media_tombstones",
        "tombstone_vectors",
        "saved_prompts",
        "writer_adapters",
        "workflow_presets",
    ):
        assert len(rows(db_to, f"SELECT * FROM {table}")) == 1


def test_a_view_alone_never_replaces_a_row_and_is_added_only_once(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))
    a, _, c = sorted(stored_paths(db_to))[0:3]
    for path in (seeded["a"], seeded["c"]):
        execute(
            db,
            "INSERT INTO media_judgments (file_path, seen_at) VALUES (?, '2020')",
            (path,),
        )
    execute(
        db_to,
        "INSERT INTO media_judgments (file_path, seen_at) VALUES (?, '2030')",
        (a,),
    )

    first = transfer(db, dir_from, db_to, dir_to, tmp_path)
    second = transfer(db, dir_from, db_to, dir_to, tmp_path)

    # b's grade and c's view are new; a already has a newer view.
    assert first["judgments"] == {"added": 2, "already": 1, "not_found": 0}
    assert second["judgments"] == {"added": 0, "already": 3, "not_found": 0}
    seen = rows(db_to, "SELECT seen_at FROM media_judgments WHERE file_path = ?", (a,))
    assert seen == [{"seen_at": "2030"}]
    seen = rows(db_to, "SELECT seen_at FROM media_judgments WHERE file_path = ?", (c,))
    assert seen == [{"seen_at": "2020"}]


def test_files_that_arrive_later_are_picked_up_by_a_second_import(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    files = relative_files(seeded)
    seed_media(db_to, files[:1])

    first = transfer(db, dir_from, db_to, dir_to, tmp_path)
    assert first["judgments"]["not_found"] == 1
    assert first["t2i_images"]["not_found"] == 1

    seed_media(db_to, files[1:])
    second = transfer(db, dir_from, db_to, dir_to, tmp_path)
    assert second["judgments"]["added"] == 1
    assert second["t2i_images"]["added"] == 1


def test_newer_data_here_is_never_overwritten(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))
    b = sorted(stored_paths(db_to))[1]
    execute(
        db_to,
        "INSERT INTO media_judgments (file_path, grade, reasons, judged_at) "
        "VALUES (?, 'pass', '[]', '2030-01-01')",
        (b,),
    )
    execute(db_to, "UPDATE media SET playback_speed = 2.0 WHERE file_path = ?", (b,))

    transfer(db, dir_from, db_to, dir_to, tmp_path)

    assert rows(db_to, "SELECT grade FROM media_judgments") == [{"grade": "pass"}]
    assert (
        rows(db_to, "SELECT playback_speed FROM media WHERE file_path = ?", (b,))[0][
            "playback_speed"
        ]
        == 2.0
    )


def test_a_failed_import_changes_nothing(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, _, seeded = source
    db_to, _ = target
    seed_media(db_to, relative_files(seeded))
    data = export_library_data(db)
    data["folders"][0]["kind"] = "not-a-kind"  # breaks a CHECK after ★ went in

    with pytest.raises(sqlite3.IntegrityError):
        import_library_data(db_to, data, PathMap({old_root(db): NEW}))

    assert not rows(db_to, "SELECT 1 FROM media WHERE is_favorite = 1")
    assert not rows(db_to, "SELECT * FROM workflow_presets WHERE name = 'Flux'")


def test_a_taken_adapter_name_gets_a_new_one(
    source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))
    execute(
        db_to,
        "INSERT INTO writer_adapters (name, scope, kind, base_model, status, "
        "created_at, hyperparams, counts) VALUES ('adapter-7', 'all', 't2i', 'base', "
        "'ready', '2031', '{}', '{}')",
    )

    transfer(db, dir_from, db_to, dir_to, tmp_path)
    transfer(db, dir_from, db_to, dir_to, tmp_path)

    names = [r["name"] for r in rows(db_to, "SELECT name FROM writer_adapters")]
    assert sorted(names) == ["adapter-7", "adapter-7 (imported)"]


def test_the_deepest_root_wins_and_a_path_outside_stays() -> None:
    paths = PathMap({"/lib": "/new", "/lib/sub": "/other"})
    assert paths.translate("/lib/a.png") == "/new/a.png"
    assert paths.translate("/lib/sub/a.png") == "/other/a.png"
    assert paths.translate("/library/a.png") == "/library/a.png"
    assert paths.translate("/elsewhere/a.png") == "/elsewhere/a.png"


def test_a_root_with_no_target_keeps_its_paths() -> None:
    assert PathMap({"/lib": None}).translate("/lib/a.png") == "/lib/a.png"


def test_a_root_spelled_in_another_case_still_matches() -> None:
    assert PathMap({"/mnt/d/Lib": "/new"}).translate("/mnt/d/lib/A.png") == "/new/A.png"


def test_the_resolver_folds_spellings_unless_that_is_ambiguous() -> None:
    nfd = "/lib/café.png"
    library = MediaResolver([nfd, "/lib/Same.png", "/lib/same.PNG"])
    assert library.resolve("/lib/café.png") == nfd
    assert library.resolve("/lib/SAME.png") is None
    assert library.resolve("/lib/Same.png") == "/lib/Same.png"


def test_suggest_targets_prefers_where_the_files_are() -> None:
    library = MediaResolver(["/new/a.png", "/new/sub/b.png"])
    out = suggest_targets(
        ["/old/lib"],
        ["/old/lib/a.png", "/old/lib/sub/b.png"],
        ["/empty", "/new"],
        library,
    )
    assert out == {"/old/lib": "/new"}


def test_suggest_targets_falls_back_to_the_same_folder_name() -> None:
    out = suggest_targets(["/old/lib"], [], ["/x", "/y/lib"], MediaResolver([]))
    assert out == {"/old/lib": "/y/lib"}


def test_suggest_targets_with_nothing_to_go_on() -> None:
    out = suggest_targets(["/old/lib"], [], ["/x"], MediaResolver([]))
    assert out == {"/old/lib": None}


def write_zip(path: Path, manifest: Any, data: Any) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(MANIFEST_NAME, json.dumps(manifest))
        bundle.writestr(DATA_NAME, json.dumps(data))


def test_files_that_are_no_bundle_are_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(BundleError):
        read_bundle(junk)
    good = {"format": BUNDLE_FORMAT, "version": 1, "roots": []}
    wrong = tmp_path / "wrong.zip"
    write_zip(wrong, {**good, "format": "other"}, {})
    with pytest.raises(BundleError):
        read_bundle(wrong)
    newer = tmp_path / "newer.zip"
    write_zip(newer, {**good, "version": 99}, {})
    with pytest.raises(BundleError):
        read_bundle(newer)
    empty = tmp_path / "empty.zip"
    write_zip(empty, good, {})
    with pytest.raises(BundleError):
        read_bundle(empty)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["judgments"][0].update(grade="maybe"),
        lambda d: d["judgments"][0].pop("reasons"),
        lambda d: d["favorites"].append(5),
        lambda d: d["t2i_images"][0].update(extra=["a list"]),
        lambda d: d["tombstones"][0]["vectors"][0].update(embedding="@@@"),
        lambda d: d["media_categories"][0].update(category="\udc80"),
    ],
)
def test_a_bad_row_is_refused(source: Any, mutate: Any) -> None:
    db, _, _ = source
    data = export_library_data(db)
    validate_data(data)
    mutate(data)
    with pytest.raises(BundleError):
        validate_data(data)


def test_a_column_name_from_the_file_never_reaches_the_sql(
    source: Any, target: Any
) -> None:
    db, _, seeded = source
    db_to, _ = target
    seed_media(db_to, relative_files(seeded))
    data = export_library_data(db)
    data["t2i_images"][0]["seed) VALUES (1); DROP TABLE media; --"] = 1

    import_library_data(db_to, data, PathMap({old_root(db): NEW}))

    assert len(rows(db_to, "SELECT * FROM t2i_images")) == 1


def test_a_thumbnail_is_only_ever_written_under_the_archive(
    source: Any, tmp_path: Path, target: Any
) -> None:
    db, dir_from, _ = source
    _, dir_to = target
    bundle = tmp_path / "evil.zip"
    data = export_library_data(db)
    write_bundle(bundle, data, ["/x"], dir_from)
    _, read = read_bundle(bundle)
    read["tombstones"][0]["thumb_path"] = "../../evil.jpg"
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr("../../evil.jpg", b"x")

    assert extract_thumbnails(bundle, read, dir_to) == 0
    assert not (dir_to.parent.parent / "evil.jpg").exists()


# ---- the API ---------------------------------------------------------------


def wait_for_import(client: TestClient) -> Dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = client.get("/api/library-transfer/import/status").json()
        if not status["running"]:
            return status
        time.sleep(0.02)
    raise AssertionError("import did not finish")


@pytest.fixture
def api(target: Tuple[DatabaseManager, Path]) -> Iterator[TestClient]:
    db, _ = target
    app = FastAPI()
    app.include_router(transfer_api.router)
    service._staged = None
    service._run = {"running": False}
    config = {
        "directories": [{"filepath": NEW, "search_subfolders": True}],
        "t2i": {},
        "i2v": {},
        "comfy": {},
    }
    with (
        patch.object(transfer_api, "get_db", return_value=db),
        patch.object(service, "load_app_config", return_value=config),
        patch.object(service, "_announce"),
        TestClient(app) as client,
    ):
        yield client


def bundle_bytes(db: DatabaseManager, data_dir: Path, tmp_path: Path) -> bytes:
    path = tmp_path / f"{uuid.uuid4().hex}.zip"
    write_bundle(path, export_library_data(db), [old_root(db)], data_dir)
    return path.read_bytes()


def test_upload_suggests_the_folder_then_apply_imports(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, dir_to = target
    seed_media(db_to, relative_files(seeded))

    res = api.post(
        "/api/library-transfer/import/upload",
        content=bundle_bytes(db, dir_from, tmp_path),
    )
    assert res.status_code == 200, res.text
    preview = res.json()
    assert len(preview["roots"]) == 1
    root = preview["roots"][0]
    assert root["target"] == NEW
    assert root["found"] == root["paths"] == 3

    res = api.post(
        "/api/library-transfer/import/apply",
        json={"token": preview["token"], "mapping": {root["source"]: NEW}},
    )
    assert res.status_code == 202, res.text
    status = wait_for_import(api)

    assert status["error"] is None
    assert status["report"]["counts"]["judgments"]["added"] == 1
    assert rows(db_to, "SELECT grade FROM media_judgments") == [{"grade": "fail"}]
    assert not list((dir_to / service.TRANSFER_DIRNAME).glob("upload-*"))


def test_a_root_with_no_files_is_not_offered(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, _ = target
    seed_media(db_to, relative_files(seeded))
    path = tmp_path / "two-roots.zip"
    write_bundle(path, export_library_data(db), [old_root(db), "/unused"], dir_from)

    preview = api.post(
        "/api/library-transfer/import/upload", content=path.read_bytes()
    ).json()

    assert [root["source"] for root in preview["roots"]] == [old_root(db)]


def test_changing_the_mapping_recounts(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, _ = target
    seed_media(db_to, relative_files(seeded))
    preview = api.post(
        "/api/library-transfer/import/upload",
        content=bundle_bytes(db, dir_from, tmp_path),
    ).json()
    source_root = preview["roots"][0]["source"]

    res = api.post(
        "/api/library-transfer/import/preview",
        json={"token": preview["token"], "mapping": {source_root: "/somewhere/else"}},
    )

    assert res.status_code == 200, res.text
    assert res.json()["roots"][0]["found"] == 0


def test_generated_files_on_disk_come_into_the_library(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, _ = source
    db_to, _ = target
    outputs = tmp_path / "outputs"
    (outputs / "z").mkdir(parents=True)
    on_disk = outputs / "z" / "c.png"
    on_disk.write_bytes(b"png")
    upload = api.post(
        "/api/library-transfer/import/upload",
        content=bundle_bytes(db, dir_from, tmp_path),
    ).json()
    source_root = upload["roots"][0]["source"]
    target_root = outputs.as_posix()

    class FakeScanner:
        def __init__(self, *_: Any) -> None:
            pass

        def ingest_file(self, path: Path) -> Optional[Media]:
            seed_media(db_to, [str(path)])
            return Media(
                file_path=path,
                file_size=1,
                width=1,
                height=1,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )

    with (
        patch.object(service, "Scanner", FakeScanner),
        patch.object(service, "get_thumbnail_cache"),
    ):
        preview = api.post(
            "/api/library-transfer/import/preview",
            json={"token": upload["token"], "mapping": {source_root: target_root}},
        ).json()
        assert preview["generated_to_ingest"] == 1
        api.post(
            "/api/library-transfer/import/apply",
            json={"token": upload["token"], "mapping": {source_root: target_root}},
        )
        status = wait_for_import(api)

    assert status["report"]["ingested"] == 1
    assert status["report"]["counts"]["t2i_images"]["added"] == 1
    assert len(rows(db_to, "SELECT * FROM t2i_images")) == 1


def test_junk_is_a_422(api: TestClient) -> None:
    res = api.post("/api/library-transfer/import/upload", content=b"junk")
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "not_a_bundle"


def test_an_unknown_token_is_refused(api: TestClient) -> None:
    res = api.post(
        "/api/library-transfer/import/apply", json={"token": "nope", "mapping": {}}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_staged"


def test_export_returns_a_bundle_and_removes_its_file(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, _ = source
    with patch.object(transfer_api, "get_db", return_value=db):
        res = api.get("/api/library-transfer/export")
    assert res.status_code == 200, res.text
    assert "metascan-library-data-" in res.headers["content-disposition"]
    path = tmp_path / "got.zip"
    path.write_bytes(res.content)
    manifest, data = read_bundle(path)
    assert manifest["format"] == BUNDLE_FORMAT
    assert data["judgments"][0]["grade"] == "fail"
    assert not list((dir_from / service.TRANSFER_DIRNAME).glob("export-*"))


def test_export_is_refused_during_a_scan(api: TestClient) -> None:
    with patch.object(scan_api, "scan_running", return_value=True):
        res = api.get("/api/library-transfer/export")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "scan_running"


def test_export_is_refused_while_a_full_clean_is_unfinished(
    api: TestClient, target: Any
) -> None:
    _, data_dir = target
    taste_snapshot.write_full_clean_marker(data_dir)
    res = api.get("/api/library-transfer/export")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "full_clean_unfinished"


def test_an_import_refused_at_the_gate_changes_nothing(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, seeded = source
    db_to, data_dir = target
    seed_media(db_to, relative_files(seeded))
    upload = api.post(
        "/api/library-transfer/import/upload",
        content=bundle_bytes(db, dir_from, tmp_path),
    ).json()
    source_root = upload["roots"][0]["source"]

    assert scan_api.move_gate.acquire(blocking=False)
    try:
        with patch.object(service, "GATE_WAIT_S", 0.05):
            res = api.post(
                "/api/library-transfer/import/apply",
                json={"token": upload["token"], "mapping": {source_root: NEW}},
            )
            assert res.status_code == 202
            status = wait_for_import(api)
    finally:
        scan_api.move_gate.release()

    assert "busy" in status["error"]
    assert not rows(db_to, "SELECT * FROM media_judgments")


def test_discard_drops_the_staged_file(
    api: TestClient, source: Any, target: Any, tmp_path: Path
) -> None:
    db, dir_from, _ = source
    _, data_dir = target
    upload = api.post(
        "/api/library-transfer/import/upload",
        content=bundle_bytes(db, dir_from, tmp_path),
    ).json()

    res = api.delete(f"/api/library-transfer/import/{upload['token']}")

    assert res.json() == {"status": "deleted"}
    assert not list((data_dir / service.TRANSFER_DIRNAME).glob("upload-*"))


def test_vectors_are_base64_in_the_file(source: Any) -> None:
    db, _, seeded = source
    vector = export_library_data(db)["tombstones"][0]["vectors"][0]
    assert base64.b64decode(vector["embedding"]) == seeded["vector"]
