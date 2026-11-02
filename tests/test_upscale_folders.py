"""The Original folder stays out of the library, and upscale leftovers are
cleaned without touching a file that cannot be seen."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import upscale_folders as folders


def config(root: str, *scan_folders: str, **extra: Any) -> Dict[str, Any]:
    return {
        "directories": [{"filepath": f} for f in scan_folders],
        "comfy": {"output_root": "/gen"},
        "upscale": {"original_root": root},
        **extra,
    }


def test_the_default_is_the_data_folder():
    root = folders.original_root({})
    assert root.name == "_original" and root.parent.name == "data"


def test_a_folder_outside_the_library_is_fine(tmp_path):
    cfg = config(str(tmp_path / "keep"), str(tmp_path / "lib"))
    assert folders.original_folder_conflict(cfg) is None


def test_a_folder_inside_a_scan_folder_is_refused(tmp_path):
    lib = tmp_path / "lib"
    conflict = folders.original_folder_conflict(config(str(lib / "keep"), str(lib)))
    assert conflict["code"] == folders.CODE_ORIGINAL_IN_LIBRARY
    assert str(lib) in conflict["message"]


def test_a_scan_folder_inside_the_original_folder_is_refused(tmp_path):
    cfg = config(str(tmp_path), str(tmp_path / "lib"))
    assert folders.original_folder_conflict(cfg) is not None


def test_a_similar_name_is_not_inside(tmp_path):
    cfg = config(str(tmp_path / "library-originals"), str(tmp_path / "library"))
    assert folders.original_folder_conflict(cfg) is None


def test_a_generated_output_root_counts(tmp_path):
    cfg = config(
        str(tmp_path / "out" / "t2i" / "keep"),
        comfy={"output_root": str(tmp_path / "out")},
    )
    assert folders.original_folder_conflict(cfg) is not None


def test_staging_inside_the_library_is_reported(tmp_path):
    with patch.object(folders, "get_data_dir", return_value=tmp_path / "lib" / "data"):
        cfg = config(str(tmp_path / "keep"), str(tmp_path / "lib"))
        assert folders.staging_conflict(cfg)["code"] == folders.CODE_STAGING_IN_LIBRARY


@pytest.fixture
def api(tmp_path, monkeypatch):
    from backend.api import config as config_api

    stored: Dict[str, Any] = config(str(tmp_path / "keep"), str(tmp_path / "lib"))

    class Db:
        records: list = []

        def list_upscale_records(self):
            return self.records

    db = Db()
    monkeypatch.setattr(config_api, "load_app_config", lambda: dict(stored))
    monkeypatch.setattr(config_api, "save_app_config", stored.update)
    monkeypatch.setattr(config_api, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(config_api.router)
    client = TestClient(app)
    client.tmp = tmp_path
    client.db = db
    client.stored = stored
    return client


def test_a_config_save_refuses_an_original_folder_in_the_library(api):
    bad = {"original_root": str(api.tmp / "lib" / "keep")}
    r = api.put("/api/config", json={"upscale": bad})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == folders.CODE_ORIGINAL_IN_LIBRARY
    assert api.stored["upscale"]["original_root"] == str(api.tmp / "keep")


def test_adding_a_scan_folder_around_the_original_folder_is_refused(api):
    r = api.put("/api/config", json={"directories": [{"filepath": str(api.tmp)}]})
    assert r.status_code == 400
    assert api.stored["directories"] == [{"filepath": str(api.tmp / "lib")}]


def test_a_conflict_already_there_does_not_block_other_saves(api):
    api.stored["upscale"] = {"original_root": str(api.tmp / "lib" / "keep")}
    r = api.put("/api/config", json={"i2v": {"window": 2}})
    assert r.status_code == 200


def test_an_unrelated_save_is_not_checked(api):
    api.stored["upscale"] = {"original_root": str(api.tmp / "lib" / "keep")}
    assert api.put("/api/config", json={"theme": "x"}).status_code == 200


def test_moving_the_folder_past_its_originals_is_refused(api):
    api.db.records = [{"file_path": "/lib/a.png"}]
    r = api.put(
        "/api/config", json={"upscale": {"original_root": str(api.tmp / "other")}}
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "original_folder_has_files"


def test_moving_an_empty_folder_is_a_plain_save(api):
    r = api.put(
        "/api/config", json={"upscale": {"original_root": str(api.tmp / "other")}}
    )
    assert r.status_code == 200


# ---- hygiene ---------------------------------------------------------------


class FakeStore:
    def __init__(self) -> None:
        self.released: list = []
        self.recovered = 0

    def recover(self) -> int:
        self.recovered += 1
        return 0

    def release(self, paths) -> None:
        self.released.extend(paths)


class FakeDb:
    def __init__(self, records) -> None:
        self.records = records

    def upscale_records_without_media(self):
        return self.records


def run_check(tmp_path: Path, records, scan_folder: Path, data_dir: Path) -> FakeStore:
    from backend.services.background import hygiene_checks as hc
    from backend.services.background.hygiene import HygieneContext

    store = FakeStore()
    ctx = HygieneContext(
        db=FakeDb(records),
        config={
            "directories": [{"filepath": str(scan_folder)}],
            "comfy": {"output_root": str(tmp_path / "gen")},
        },
        data_dir=data_dir,
        import_process=None,
        should_stop=lambda: False,
        report=lambda *a: None,
    )
    hc.UpscaleOrphans()._sweep(ctx, store)
    return store


def test_a_gone_file_under_a_visible_folder_is_released(tmp_path):
    lib = tmp_path / "lib"
    (lib / "other").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    path = (lib / "a.png").as_posix()
    store = run_check(tmp_path, [{"file_path": path}], lib, data)
    assert store.recovered == 1 and store.released == [path]


def test_a_file_under_a_missing_folder_is_left_alone(tmp_path):
    lib = tmp_path / "unmounted"
    data = tmp_path / "data"
    data.mkdir()
    path = (lib / "a.png").as_posix()
    store = run_check(tmp_path, [{"file_path": path}], lib, data)
    assert store.released == []


def test_a_file_that_is_there_is_left_alone(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.png").write_bytes(b"x")
    data = tmp_path / "data"
    data.mkdir()
    store = run_check(tmp_path, [{"file_path": (lib / "a.png").as_posix()}], lib, data)
    assert store.released == []


def test_nothing_happens_during_an_unfinished_full_clean(tmp_path):
    from backend.services import taste_snapshot

    lib = tmp_path / "lib"
    lib.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    taste_snapshot.write_full_clean_marker(data)
    store = run_check(tmp_path, [{"file_path": (lib / "a.png").as_posix()}], lib, data)
    assert store.recovered == 0 and store.released == []
