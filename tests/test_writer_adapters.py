"""Writer-adapter registry: DB CRUD, lineage ids, validation of adapter
writer ids, history names in writer stats and /api/writers."""

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import (
    WriterModelError,
    check_writer_model,
    writer_id_allowed,
)
from metascan.core.media import Media
from metascan.core.t2i_runner import _check_prompts
from metascan.core.writer_adapters import (
    WRITER_ADAPTER_PREFIX,
    WriterAdapterUnavailable,
    adapter_writer_id,
    parse_adapter_writer_id,
)

BASE = "qwen3vl-8b"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _make(db, name="taste-all-v1", status="untested", scope="all", kind="t2i"):
    return db.create_writer_adapter(
        name=name, scope=scope, kind=kind, base_model=BASE, status=status
    )


def test_writer_id_round_trip_and_parsing():
    assert WRITER_ADAPTER_PREFIX == "adapter:"
    assert adapter_writer_id(7) == "adapter:7"
    assert parse_adapter_writer_id("adapter:7") == 7
    for bad in (
        "adapter:",
        "adapter:x",
        "adapter:-1",
        "adapter:0",
        "adapter:07",
        "adapter:1.5",
        "base",
        "",
        "adapter: 3",
        None,
        5,
    ):
        assert parse_adapter_writer_id(bad) is None  # type: ignore[arg-type]


def test_crud_and_name_uniqueness(db):
    a = _make(db)
    row = db.get_writer_adapter(a)
    assert row["name"] == "taste-all-v1" and row["status"] == "untested"
    assert row["kind"] == "t2i" and row["deleted_at"] is None
    assert row["hyperparams"] == {} and row["counts"] == {}
    with pytest.raises(ValueError, match="duplicate_name"):
        _make(db, name="TASTE-ALL-V1")
    b = _make(db, name="other")
    with pytest.raises(ValueError, match="duplicate_name"):
        db.rename_writer_adapter(b, "Taste-All-V1")
    db.rename_writer_adapter(b, "renamed")
    assert db.get_writer_adapter(b)["name"] == "renamed"
    assert db.get_writer_adapter(999) is None
    assert [r["id"] for r in db.list_writer_adapters()] == [a, b]


def test_status_fields(db):
    a = _make(db, status="training")
    db.set_writer_adapter_status(
        a,
        "ready",
        holdout_agreement=0.7,
        base_agreement=0.5,
        file_path="x/adapter.gguf",
        counts={"pairs": 3},
        hyperparams={"rank": 8},
    )
    row = db.get_writer_adapter(a)
    assert row["status"] == "ready" and row["holdout_agreement"] == 0.7
    assert row["counts"] == {"pairs": 3} and row["hyperparams"] == {"rank": 8}
    with pytest.raises(ValueError):
        db.set_writer_adapter_status(a, "ready", bogus=1)


def test_soft_delete_keeps_row_for_history(db):
    a = _make(db)
    db.soft_delete_writer_adapter(a)
    row = db.get_writer_adapter(a)
    assert row["status"] == "deleted" and row["deleted_at"]
    assert db.list_writer_adapters() == []
    assert [r["id"] for r in db.list_writer_adapters(include_deleted=True)] == [a]


def test_next_adapter_name_sequences(db):
    assert db.next_adapter_name("all") == "taste-all-v1"
    _make(db, name="taste-all-v1")
    _make(db, name="taste-all-v2")
    _make(db, name="taste-landscape-v5", scope="3")
    assert db.next_adapter_name("all") == "taste-all-v3"
    assert db.next_adapter_name("landscape") == "taste-landscape-v6"
    # A deleted adapter's number is never reused (its name is remembered).
    last = _make(db, name="taste-all-v3")
    db.soft_delete_writer_adapter(last)
    assert db.next_adapter_name("all") == "taste-all-v4"
    # A renamed adapter does not disturb the sequence.
    db.rename_writer_adapter(1, "mine")
    assert db.next_adapter_name("all") == "taste-all-v4"


def test_writer_id_validation(db):
    ok = _make(db, name="a", status="ready")
    untested = _make(db, name="b", status="untested")
    training = _make(db, name="c", status="training")
    failed = _make(db, name="d", status="failed")
    gone = _make(db, name="e", status="ready")
    db.soft_delete_writer_adapter(gone)
    for i in (ok, untested):
        wid = adapter_writer_id(i)
        assert writer_id_allowed(wid, db)
        assert check_writer_model(wid, BASE, db=db) == BASE
        with pytest.raises(WriterModelError):
            check_writer_model(wid, "other-model", db=db)
        with pytest.raises(WriterModelError):
            check_writer_model(wid, None, db=db)
    for i in (training, failed, gone, 999):
        wid = adapter_writer_id(i)
        assert not writer_id_allowed(wid, db)
        with pytest.raises(WriterModelError):
            check_writer_model(wid, BASE, db=db)
    assert writer_id_allowed("base", db) and writer_id_allowed("fallback", db)
    assert not writer_id_allowed("manual", db)
    assert not writer_id_allowed("adapter:abc", db)
    # Without a db an adapter id cannot be verified.
    assert not writer_id_allowed(adapter_writer_id(ok))


def test_t2i_prompts_accept_ready_adapter_only(db):
    ok = _make(db, name="a", status="ready")
    gone = _make(db, name="b", status="ready")
    db.soft_delete_writer_adapter(gone)

    def req(adapter):
        return SimpleNamespace(
            prompts=[
                {
                    "prompt": "p",
                    "draft": "d",
                    "writer_id": adapter_writer_id(adapter),
                    "writer_model": BASE,
                    "variant_index": 0,
                }
            ]
        )

    out = _check_prompts(req(ok), 1, db)  # type: ignore[arg-type]
    assert out and out[0]["writer_id"] == f"adapter:{ok}"
    assert out[0]["writer_model"] == BASE
    # A gone adapter's draft is a 409 adapter_unavailable (stale clients
    # fall back to Base), not a 400.
    with pytest.raises(WriterAdapterUnavailable):
        _check_prompts(req(gone), 1, db)  # type: ignore[arg-type]


def _media(path):
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


def test_history_rows_keep_the_deleted_adapters_name(db):
    a = _make(db, name="taste-all-v1", status="ready")
    wid = adapter_writer_id(a)
    db.save_media(_media("/g/a.png"))
    db.create_t2i_image(file_path="/g/a.png", writer_id=wid, writer_model=BASE)
    db.save_media(_media("/g/b.png"))
    db.create_t2i_image(file_path="/g/b.png", writer_id="base", writer_model=BASE)
    db.rename_writer_adapter(a, "my-writer")
    db.soft_delete_writer_adapter(a)
    rows = db.writer_window_stats(14, 6)
    adapter_rows = [r for r in rows if r["writer_id"] == wid]
    assert adapter_rows and all(r["name"] == "my-writer" for r in adapter_rows)
    assert all("name" not in r for r in rows if r["writer_id"] == "base")


@pytest.fixture
def client(tmp_path):
    os.environ.setdefault("METASCAN_API_KEY", "")
    db = DatabaseManager(tmp_path)
    import backend.dependencies as deps

    old = deps._db_singleton
    deps._db_singleton = db  # type: ignore[attr-defined]
    with patch("backend.dependencies.get_data_dir", return_value=tmp_path):
        from backend.api import writers

        app = FastAPI()
        app.include_router(writers.router)
        yield TestClient(app), db
    deps._db_singleton = old  # type: ignore[attr-defined]


def test_api_list_and_rename(client, monkeypatch):
    c, db = client
    sent = []
    monkeypatch.setattr(
        "backend.api.writers.ws_manager.broadcast_sync", lambda *a: sent.append(a)
    )
    a = _make(db, name="one")
    b = _make(db, name="two")
    db.soft_delete_writer_adapter(b)
    rows = c.get("/api/writers/adapters").json()
    assert [r["id"] for r in rows] == [a]
    assert rows[0]["writer_id"] == f"adapter:{a}"
    r = c.patch(f"/api/writers/adapters/{a}", json={"name": "  Renamed "})
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    assert sent == [("training", "adapters_changed", {})]
    _make(db, name="Third")
    r = c.patch(f"/api/writers/adapters/{a}", json={"name": "third"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_name"
    r = c.patch(f"/api/writers/adapters/{a}", json={"name": "   "})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_name"
    assert c.patch("/api/writers/adapters/999", json={"name": "x"}).status_code == 404
    assert c.patch(f"/api/writers/adapters/{b}", json={"name": "x"}).status_code == 404


def test_api_delete_removes_files(client, tmp_path, monkeypatch):
    c, db = client
    monkeypatch.setattr(
        "backend.api.writers.ws_manager.broadcast_sync", lambda *a: None
    )
    a = _make(db, name="one", status="ready")
    from metascan.utils.app_paths import get_data_dir

    folder = get_data_dir() / "models" / "vlm" / "adapters" / str(a)
    folder.mkdir(parents=True)
    (folder / "adapter.gguf").write_bytes(b"x")
    assert c.delete(f"/api/writers/adapters/{a}").json() == {"status": "deleted"}
    assert not folder.exists()
    assert db.get_writer_adapter(a)["status"] == "deleted"
    assert c.delete(f"/api/writers/adapters/{a}").status_code == 404
    assert c.delete("/api/writers/adapters/999").status_code == 404


def test_api_delete_refused_while_training(client):
    c, db = client
    a = _make(db, name="one", status="training")
    r = c.delete(f"/api/writers/adapters/{a}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "adapter_training"
    assert db.get_writer_adapter(a)["status"] == "training"


def test_api_delete_failed_removal_is_retryable(client, monkeypatch):
    c, db = client
    monkeypatch.setattr(
        "backend.api.writers.ws_manager.broadcast_sync", lambda *a: None
    )
    a = _make(db, name="one", status="ready")
    db.set_writer_adapter_status(a, "ready", file_path="x/adapter.gguf")
    from metascan.utils.app_paths import get_data_dir

    folder = get_data_dir() / "models" / "vlm" / "adapters" / str(a)
    folder.mkdir(parents=True)
    (folder / "adapter.gguf").write_bytes(b"x")

    def boom(*args, **kwargs):
        raise OSError("busy")

    with patch("backend.api.writers.shutil.rmtree", boom):
        r = c.delete(f"/api/writers/adapters/{a}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "adapter_files_busy"
    row = db.get_writer_adapter(a)
    assert row["status"] == "ready" and row["file_path"] == "x/adapter.gguf"
    assert folder.exists()
    # Retry succeeds once the files can go.
    assert c.delete(f"/api/writers/adapters/{a}").json() == {"status": "deleted"}
    assert not folder.exists()


def test_adapter_kind_must_match_surface(db):
    t2i = _make(db, name="a", status="ready", kind="t2i")
    i2v = _make(db, name="b", status="ready", kind="i2v")
    w_t2i, w_i2v = adapter_writer_id(t2i), adapter_writer_id(i2v)
    assert writer_id_allowed(w_t2i, db, "t2i")
    assert not writer_id_allowed(w_t2i, db, "i2v")
    assert writer_id_allowed(w_i2v, db, "i2v")
    assert not writer_id_allowed(w_i2v, db, "t2i")
    assert check_writer_model(w_t2i, BASE, db=db, kind="t2i") == BASE
    with pytest.raises(WriterModelError):
        check_writer_model(w_t2i, BASE, db=db, kind="i2v")
    with pytest.raises(WriterModelError):
        check_writer_model(w_i2v, BASE, db=db, kind="t2i")
    req = SimpleNamespace(
        prompts=[
            {
                "prompt": "p",
                "draft": "d",
                "writer_id": w_i2v,
                "writer_model": BASE,
                "variant_index": 0,
            }
        ]
    )
    with pytest.raises(WriterAdapterUnavailable):
        _check_prompts(req, 1, db)  # type: ignore[arg-type]
