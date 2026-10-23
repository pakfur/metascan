"""Writer training assets in the Models tab (Task 7): the HF base weights
row and the llama.cpp LoRA converter row, their downloads (stubbed: no
network), deletes, and the ``writer_training`` gate following the disk."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import models as models_api
from backend.config import save_app_config
from backend.services import training_manager as tm
from backend.services import writer_training_assets as wta
from metascan.core import hardware
from metascan.core.vlm_models import REGISTRY
from metascan.core.writer_adapters import DEFAULT_WRITER_TRAIN_REPO, train_base_dir
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import LLAMA_CPP_RELEASE

# ---- helpers ---------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(models_api.router)
    return TestClient(app)


@pytest.fixture
def events(monkeypatch) -> List[Tuple[str, str, Dict[str, Any]]]:
    log: List[Tuple[str, str, Dict[str, Any]]] = []
    monkeypatch.setattr(
        models_api.ws_manager,
        "broadcast_sync",
        lambda ch, ev, data: log.append((ch, ev, dict(data))),
    )
    return log


@pytest.fixture(autouse=True)
def _no_manager():
    tm.set_training_manager(None)
    models_api._writer_downloads.clear()
    models_api._writer_deletes.clear()
    yield
    tm.set_training_manager(None)
    models_api._writer_downloads.clear()
    models_api._writer_deletes.clear()


def _base_dir() -> Path:
    return train_base_dir(get_data_dir(), DEFAULT_WRITER_TRAIN_REPO)


def _write_base(d: Path, shards: int = 2, present: Optional[int] = None) -> None:
    """A sharded HF checkpoint: config.json, the index naming ``shards``
    shards, and the first ``present`` of them on disk."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    names = [f"model-{i + 1:05d}-of-{shards:05d}.safetensors" for i in range(shards)]
    (d / "model.safetensors.index.json").write_text(
        json.dumps(
            {"metadata": {}, "weight_map": {f"w{i}": n for i, n in enumerate(names)}}
        )
    )
    for n in names[: shards if present is None else present]:
        (d / n).write_bytes(b"x" * 10)


def _rows(client: TestClient) -> Dict[str, Dict[str, Any]]:
    r = client.get("/api/models/status")
    assert r.status_code == 200
    return {m["id"]: m for m in r.json()["models"]}


def _gate_reasons(client: TestClient) -> List[str]:
    return client.get("/api/models/status").json()["gates"]["writer_training"][
        "reasons"
    ]


PREFIX = f"llama.cpp-{LLAMA_CPP_RELEASE}"


def _tarball(entries: Dict[str, Any]) -> bytes:
    """name -> bytes (file) | ("symlink", target) | ("dir",)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            if isinstance(value, tuple) and value[0] == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = value[1]
                tf.addfile(info)
            elif isinstance(value, tuple) and value[0] == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = value[1]
                tf.addfile(info)
            elif isinstance(value, tuple) and value[0] == "chr":
                info.type = tarfile.CHRTYPE
                tf.addfile(info)
            elif isinstance(value, tuple) and value[0] == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                info.size = len(value)
                tf.addfile(info, io.BytesIO(value))
    return buf.getvalue()


def _good_tar_entries() -> Dict[str, Any]:
    return {
        f"{PREFIX}/": ("dir",),
        f"{PREFIX}/convert_lora_to_gguf.py": b"# lora\n",
        f"{PREFIX}/convert_hf_to_gguf.py": b"# hf\n",
        f"{PREFIX}/LICENSE": b"MIT\n",
        f"{PREFIX}/requirements.txt": b"-r requirements/a.txt\n",
        f"{PREFIX}/requirements/a.txt": b"numpy\n",
        f"{PREFIX}/gguf-py/gguf/__init__.py": b"",
        f"{PREFIX}/gguf-py/gguf/constants.py": b"X = 1\n",
        f"{PREFIX}/conversion/__init__.py": b"",
        f"{PREFIX}/models/templates/a.jinja": b"{{ x }}",
        # not needed by the converter: never extracted
        f"{PREFIX}/README.md": b"readme",
        f"{PREFIX}/src/llama.cpp": b"int main(){}",
        f"{PREFIX}/models/ggml-vocab-llama.gguf": b"big",
        f"{PREFIX}/convert_llama_ggml_to_gguf.py": b"# other",
    }


# ---- registry: one source of truth for the training repo -------------------


def test_hf_train_repo_is_on_the_8b_spec_only():
    assert REGISTRY["qwen3vl-8b"].hf_train_repo == (
        "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated"
    )
    assert DEFAULT_WRITER_TRAIN_REPO == REGISTRY["qwen3vl-8b"].hf_train_repo
    for mid, spec in REGISTRY.items():
        if mid != "qwen3vl-8b":
            assert spec.hf_train_repo is None, mid


# ---- the gate's base-weights check ----------------------------------------


def test_base_weights_need_every_shard_the_index_lists(tmp_path):
    d = tmp_path / "base"
    assert not hardware.base_weights_present(d)
    _write_base(d, shards=3, present=2)
    assert not hardware.base_weights_present(d)
    _write_base(d, shards=3)
    assert hardware.base_weights_present(d)


def test_base_weights_with_an_unreadable_index_are_not_present(tmp_path):
    d = tmp_path / "base"
    _write_base(d, shards=1)
    (d / "model.safetensors.index.json").write_text("{not json")
    assert not hardware.base_weights_present(d)


def test_single_file_checkpoint_without_index_is_present(tmp_path):
    d = tmp_path / "base"
    d.mkdir()
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_bytes(b"x")
    assert hardware.base_weights_present(d)


def test_partial_index_shard_missing_keeps_the_gate_reason(tmp_path):
    paths = hardware.WriterTrainingPaths(
        python=sys.executable,
        base_dir=tmp_path / "base",
        converter_script=tmp_path / "convert_lora_to_gguf.py",
    )
    _write_base(paths.base_dir, shards=2, present=1)
    reasons = hardware.writer_training_reasons(
        hardware.HardwareReport(), paths, probe=False
    )
    assert any("base weights not downloaded" in r for r in reasons)


# ---- status rows ----------------------------------------------------------------


def test_rows_present_after_the_vlm_rows_with_the_group_flag(client):
    r = client.get("/api/models/status").json()["models"]
    ids = [m["id"] for m in r]
    assert wta.BASE_ID in ids and wta.CONVERTER_ID in ids
    last_vlm = max(i for i, m in enumerate(r) if m.get("is_vlm"))
    assert ids.index(wta.BASE_ID) == last_vlm + 1
    assert ids.index(wta.CONVERTER_ID) == last_vlm + 2
    for mid in (wta.BASE_ID, wta.CONVERTER_ID):
        row = next(m for m in r if m["id"] == mid)
        assert row["writer_training"] is True
        assert row["group"] == "Writer training"
        for key in ("name", "description", "status", "size_bytes", "cache_path"):
            assert key in row


def test_base_row_empty_partial_complete(client):
    row = _rows(client)[wta.BASE_ID]
    assert row["status"] == "missing"
    assert row["size_bytes"] is None
    assert row["repo"] == DEFAULT_WRITER_TRAIN_REPO
    assert row["download_allowed"] is True

    _write_base(_base_dir(), shards=2, present=1)
    row = _rows(client)[wta.BASE_ID]
    assert row["status"] == "missing"
    assert row["partial"] is True
    assert row["size_bytes"] and row["size_bytes"] > 0

    _write_base(_base_dir(), shards=2)
    row = _rows(client)[wta.BASE_ID]
    assert row["status"] == "available"
    assert row["partial"] is False
    assert row["cache_path"] == str(_base_dir())


def test_an_interrupted_download_shows_as_partial(client):
    partial = wta.partial_dir(_base_dir())
    partial.mkdir(parents=True)
    (partial / "config.json").write_text("{}")
    row = _rows(client)[wta.BASE_ID]
    assert row["status"] == "missing"
    assert row["partial"] is True


def test_base_row_follows_the_configured_repo(client):
    save_app_config({"models": {"writer_train_repo": "someone/Other-8B"}})
    row = _rows(client)[wta.BASE_ID]
    assert row["repo"] == "someone/Other-8B"
    reasons = _gate_reasons(client)
    other = train_base_dir(get_data_dir(), "someone/Other-8B")
    assert any(str(other) in r for r in reasons)


def test_paths_are_shared_with_the_training_manager(tmp_path):
    cfg = {
        "models": {
            "writer_train_repo": "a/b",
            "llama_cpp_dir": str(tmp_path / "lc"),
            "writer_train_python": "/x/python",
        }
    }
    assets = wta.asset_paths(cfg, tmp_path)
    gate_paths = tm.training_paths_from_config(cfg, tmp_path)
    assert gate_paths == assets.gate_paths()
    assert assets.llama_cpp_configured is True
    assert gate_paths.converter_script == tmp_path / "lc" / "convert_lora_to_gguf.py"


# ---- the gate follows the disk without a restart -----------------------------


def test_gate_reasons_follow_downloads_and_deletes(client, events):
    reasons = _gate_reasons(client)
    assert any("base weights not downloaded" in r for r in reasons)
    assert any("LoRA converter not found" in r for r in reasons)

    _write_base(_base_dir())
    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("#")
    (lc / wta.RELEASE_MARKER).write_text(LLAMA_CPP_RELEASE)
    reasons = _gate_reasons(client)
    assert not any("base weights not downloaded" in r for r in reasons)
    assert not any("LoRA converter not found" in r for r in reasons)

    assert client.delete(f"/api/models/{wta.BASE_ID}").status_code == 200
    assert client.delete(f"/api/models/{wta.CONVERTER_ID}").status_code == 200
    reasons = _gate_reasons(client)
    assert any("base weights not downloaded" in r for r in reasons)
    assert any("LoRA converter not found" in r for r in reasons)
    assert ("training", "gate_changed", {}) in events


# ---- base download -------------------------------------------------------------


def test_base_download_writes_via_partial_then_renames(monkeypatch, events):
    base = _base_dir()
    calls: List[Dict[str, Any]] = []

    def fake_snapshot(**kw: Any) -> str:
        calls.append(kw)
        local = Path(kw["local_dir"])
        assert local == wta.partial_dir(base)
        assert not base.exists()  # never written in place
        _write_base(local, shards=2)
        (local / ".cache" / "huggingface").mkdir(parents=True)
        return str(local)

    monkeypatch.setattr(wta, "_hf_snapshot_download", fake_snapshot)
    monkeypatch.setattr(wta, "_hf_expected_bytes", lambda repo, token: 20)
    asyncio.run(models_api._download_writer_asset(wta.BASE_ID))

    assert hardware.base_weights_present(base)
    assert not wta.partial_dir(base).exists()
    assert not (base / ".cache").exists()
    kw = calls[0]
    assert kw["repo_id"] == DEFAULT_WRITER_TRAIN_REPO
    pats = kw["allow_patterns"]
    assert "*.safetensors" in pats and "*.json" in pats and "*.jinja" in pats
    assert not any(p in ("*", "*.md", "*.bin", "*.pt") for p in pats)
    names = [(c, e, d.get("id")) for c, e, d in events]
    assert ("models", "download_complete", wta.BASE_ID) in names
    assert ("training", "gate_changed", None) in names


def test_base_download_uses_the_configured_hf_token(monkeypatch, events):
    save_app_config({"models": {"huggingface_token": "hf_test_token"}})
    seen: Dict[str, Any] = {}

    def fake_snapshot(**kw: Any) -> str:
        seen.update(kw)
        _write_base(Path(kw["local_dir"]))
        return kw["local_dir"]

    monkeypatch.setattr(wta, "_hf_snapshot_download", fake_snapshot)
    monkeypatch.setattr(wta, "_hf_expected_bytes", lambda repo, token: None)
    asyncio.run(models_api._download_writer_asset(wta.BASE_ID))
    assert seen["token"] == "hf_test_token"


def test_base_download_failure_leaves_no_base_dir(monkeypatch, events):
    def boom(**kw: Any) -> str:
        Path(kw["local_dir"]).mkdir(parents=True)
        (Path(kw["local_dir"]) / "config.json").write_text("{}")
        raise RuntimeError("network down")

    monkeypatch.setattr(wta, "_hf_snapshot_download", boom)
    monkeypatch.setattr(wta, "_hf_expected_bytes", lambda repo, token: None)
    asyncio.run(models_api._download_writer_asset(wta.BASE_ID))
    assert not _base_dir().exists()
    errs = [d for c, e, d in events if e == "download_error"]
    assert errs and errs[0]["id"] == wta.BASE_ID
    assert "network down" in errs[0]["error"]


def test_base_download_that_misses_a_shard_is_not_installed(monkeypatch, events):
    def short(**kw: Any) -> str:
        _write_base(Path(kw["local_dir"]), shards=3, present=2)
        return kw["local_dir"]

    monkeypatch.setattr(wta, "_hf_snapshot_download", short)
    monkeypatch.setattr(wta, "_hf_expected_bytes", lambda repo, token: None)
    asyncio.run(models_api._download_writer_asset(wta.BASE_ID))
    assert not _base_dir().exists()
    assert any(e == "download_error" for _, e, _ in events)


def test_download_is_single_flight(client, monkeypatch):
    started: List[str] = []

    async def fake(mid: str) -> None:
        started.append(mid)

    monkeypatch.setattr(models_api, "_download_writer_asset", fake)
    models_api._writer_downloads.add(wta.BASE_ID)
    r = client.post("/api/models/download", json={"id": wta.BASE_ID})
    assert r.status_code == 200
    assert r.json()["status"] == "already_running"
    assert started == []


# ---- converter fetch --------------------------------------------------------------


def _stub_fetch(monkeypatch, data: bytes, seen: Optional[List[str]] = None) -> None:
    def fetch(url: str, dest: Path) -> None:
        if seen is not None:
            seen.append(url)
        Path(dest).write_bytes(data)

    monkeypatch.setattr(wta, "_http_fetch", fetch)


def test_converter_fetch_extracts_only_the_allowed_paths(monkeypatch, events):
    urls: List[str] = []
    _stub_fetch(monkeypatch, _tarball(_good_tar_entries()), urls)
    asyncio.run(models_api._download_writer_asset(wta.CONVERTER_ID))

    assert urls == [
        "https://github.com/ggml-org/llama.cpp/archive/refs/tags/"
        f"{LLAMA_CPP_RELEASE}.tar.gz"
    ]
    lc = wta.asset_paths().llama_cpp_dir
    got = sorted(p.relative_to(lc).as_posix() for p in lc.rglob("*") if p.is_file())
    assert got == sorted(
        [
            ".metascan-release",
            "LICENSE",
            "conversion/__init__.py",
            "convert_hf_to_gguf.py",
            "convert_lora_to_gguf.py",
            "gguf-py/gguf/__init__.py",
            "gguf-py/gguf/constants.py",
            "models/templates/a.jinja",
            "requirements.txt",
            "requirements/a.txt",
        ]
    )
    assert (lc / wta.RELEASE_MARKER).read_text().strip() == LLAMA_CPP_RELEASE
    assert not wta.partial_dir(lc).exists()
    assert ("models", "download_complete", {"id": wta.CONVERTER_ID}) in events
    assert ("training", "gate_changed", {}) in events


@pytest.mark.parametrize(
    "bad",
    [
        {f"{PREFIX}/../evil.py": b"x"},
        {"/abs/evil.py": b"x"},
        {f"{PREFIX}/gguf-py/../../evil.py": b"x"},
        {f"{PREFIX}/gguf-py/link": ("symlink", "/etc/passwd")},
        {f"{PREFIX}/gguf-py/hard": ("hardlink", f"{PREFIX}/LICENSE")},
        {f"{PREFIX}/conversion/dev": ("chr",)},
    ],
)
def test_converter_extraction_rejects_unsafe_members(tmp_path, bad):
    entries = _good_tar_entries()
    entries.update(bad)
    tar = tmp_path / "src.tar.gz"
    tar.write_bytes(_tarball(entries))
    dest = tmp_path / "out"
    with pytest.raises(wta.AssetError):
        wta.extract_converter(tar, dest, LLAMA_CPP_RELEASE)
    assert not (tmp_path / "evil.py").exists()
    assert not dest.exists()


def test_converter_fetch_with_a_bad_archive_installs_nothing(monkeypatch, events):
    entries = _good_tar_entries()
    entries[f"{PREFIX}/../evil.py"] = b"x"
    _stub_fetch(monkeypatch, _tarball(entries))
    asyncio.run(models_api._download_writer_asset(wta.CONVERTER_ID))
    lc = wta.asset_paths().llama_cpp_dir
    assert not lc.exists()
    assert not wta.partial_dir(lc).exists()
    assert any(e == "download_error" for _, e, _ in events)


def test_converter_archive_without_the_script_is_refused(tmp_path):
    entries = _good_tar_entries()
    del entries[f"{PREFIX}/convert_lora_to_gguf.py"]
    tar = tmp_path / "src.tar.gz"
    tar.write_bytes(_tarball(entries))
    with pytest.raises(wta.AssetError):
        wta.extract_converter(tar, tmp_path / "out", LLAMA_CPP_RELEASE)


def test_converter_row_shows_the_tag_and_flags_a_mismatch(client):
    row = _rows(client)[wta.CONVERTER_ID]
    assert row["status"] == "missing"
    assert row["release"] is None
    assert row["server_release"] == LLAMA_CPP_RELEASE
    assert row["download_allowed"] is True

    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("#")
    (lc / wta.RELEASE_MARKER).write_text("b1\n")
    row = _rows(client)[wta.CONVERTER_ID]
    assert row["status"] == "available"
    assert row["release"] == "b1"
    assert row["release_mismatch"] is True

    (lc / wta.RELEASE_MARKER).write_text(LLAMA_CPP_RELEASE)
    row = _rows(client)[wta.CONVERTER_ID]
    assert row["release"] == LLAMA_CPP_RELEASE
    assert row["release_mismatch"] is False


def test_configured_llama_cpp_dir_hides_the_download(client, tmp_path):
    own = tmp_path / "my-llama.cpp"
    save_app_config({"models": {"llama_cpp_dir": str(own)}})
    row = _rows(client)[wta.CONVERTER_ID]
    assert row["status"] == "missing"
    assert row["download_allowed"] is False
    assert row["managed"] is False
    assert "models.llama_cpp_dir" in row["hint"]
    assert "build_llama_server.sh" in row["hint"]

    r = client.post("/api/models/download", json={"id": wta.CONVERTER_ID})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "llama_cpp_dir_configured"

    own.mkdir()
    (own / "convert_lora_to_gguf.py").write_text("#")
    row = _rows(client)[wta.CONVERTER_ID]
    assert row["status"] == "available"
    assert row["cache_path"] == str(own / "convert_lora_to_gguf.py")
    # the user's checkout is never deleted from here
    r = client.delete(f"/api/models/{wta.CONVERTER_ID}")
    assert r.status_code == 409
    assert (own / "convert_lora_to_gguf.py").exists()


def test_an_unmanaged_default_dir_is_never_replaced_or_deleted(client, monkeypatch):
    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("# mine")
    r = client.delete(f"/api/models/{wta.CONVERTER_ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "llama_cpp_dir_unmanaged"
    _stub_fetch(monkeypatch, _tarball(_good_tar_entries()))
    with pytest.raises(wta.AssetError):
        wta.fetch_converter(wta.asset_paths())
    assert (lc / "convert_lora_to_gguf.py").read_text() == "# mine"


def test_converter_refetch_replaces_a_managed_dir(monkeypatch, events):
    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("# old")
    (lc / "stale.py").write_text("# stale")
    (lc / wta.RELEASE_MARKER).write_text("b1")
    _stub_fetch(monkeypatch, _tarball(_good_tar_entries()))
    asyncio.run(models_api._download_writer_asset(wta.CONVERTER_ID))
    assert (lc / "convert_lora_to_gguf.py").read_text() == "# lora\n"
    assert not (lc / "stale.py").exists()
    assert wta.read_release_marker(lc) == LLAMA_CPP_RELEASE


# ---- delete while training uses the base ---------------------------------------


class _StubManager:
    def __init__(self, using: List[int], active: bool = False) -> None:
        self.using = using
        self.active = active
        self._lock = asyncio.Lock()

    @property
    def op_lock(self) -> asyncio.Lock:  # a property, as on TrainingManager
        return self._lock

    def runs_using_base(self, base_dir: Path) -> List[int]:
        return list(self.using)

    def status(self) -> Dict[str, Any]:
        return {"state": "running" if self.active else "idle"}


def test_base_delete_refused_while_a_run_uses_it(client):
    _write_base(_base_dir())
    tm.set_training_manager(_StubManager([7]))  # type: ignore[arg-type]
    r = client.delete(f"/api/models/{wta.BASE_ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "training_uses_base"
    assert r.json()["detail"]["run_ids"] == [7]
    assert hardware.base_weights_present(_base_dir())

    tm.set_training_manager(_StubManager([]))  # type: ignore[arg-type]
    assert client.delete(f"/api/models/{wta.BASE_ID}").status_code == 200
    assert not _base_dir().exists()


def test_converter_delete_refused_while_training(client):
    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("#")
    (lc / wta.RELEASE_MARKER).write_text(LLAMA_CPP_RELEASE)
    tm.set_training_manager(_StubManager([], active=True))  # type: ignore[arg-type]
    r = client.delete(f"/api/models/{wta.CONVERTER_ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "gpu_busy_training"
    assert lc.exists()


def test_manager_reports_the_runs_whose_task_reads_the_base(tmp_path):
    from metascan.core.database_sqlite import DatabaseManager

    db = DatabaseManager(tmp_path / "db")
    try:
        base = tmp_path / "base"
        other = tmp_path / "other"

        def run(status: str, base_dir: Path) -> int:
            aid = db.create_writer_adapter(
                name=f"a{status}{base_dir.name}",
                scope="all",
                kind="t2i",
                base_model="qwen3vl-8b",
                status="paused",
                hyperparams={},
                counts={},
            )
            run_dir = tmp_path / "runs" / f"{status}-{base_dir.name}"
            run_dir.mkdir(parents=True)
            (run_dir / "task.json").write_text(json.dumps({"base_dir": str(base_dir)}))
            return db.create_training_run(aid, status=status, run_dir=str(run_dir))

        paused = run("paused", base)
        failed = run("failed", base)
        run("completed", base)
        run("cancelled", base)
        run("paused", other)

        mgr = tm.TrainingManager(
            db,
            tmp_path / "data",
            dataset_inputs=lambda: (object(), "uncensored", None),
            gpu_busy=lambda: [],
            release_gpu=_anoop,
            refresh_adapters=_anoop,
            broadcast=lambda e, d: None,
            paths=lambda: hardware.WriterTrainingPaths(
                python=sys.executable,
                base_dir=base,
                converter_script=tmp_path / "c.py",
            ),
        )
        assert sorted(mgr.runs_using_base(base)) == sorted([paused, failed])
    finally:
        db.close()


async def _anoop() -> None:
    return None


def test_base_delete_with_the_real_manager(client, tmp_path):
    """Through the real TrainingManager (its op_lock property and the
    task.json scan): a paused run on the base refuses the delete."""
    from metascan.core.database_sqlite import DatabaseManager

    base = _base_dir()
    _write_base(base)
    db = DatabaseManager(tmp_path / "db")
    try:
        aid = db.create_writer_adapter(
            name="taste-all-v1",
            scope="all",
            kind="t2i",
            base_model="qwen3vl-8b",
            status="paused",
        )
        run_dir = tmp_path / "runs" / "1"
        run_dir.mkdir(parents=True)
        (run_dir / "task.json").write_text(json.dumps({"base_dir": str(base)}))
        rid = db.create_training_run(aid, status="paused", run_dir=str(run_dir))
        mgr = tm.TrainingManager(
            db,
            get_data_dir(),
            dataset_inputs=lambda: (object(), "uncensored", None),
            gpu_busy=lambda: [],
            release_gpu=_anoop,
            refresh_adapters=_anoop,
            broadcast=lambda e, d: None,
            paths=lambda: wta.asset_paths().gate_paths(),
        )
        tm.set_training_manager(mgr)
        r = client.delete(f"/api/models/{wta.BASE_ID}")
        assert r.status_code == 409
        assert r.json()["detail"]["run_ids"] == [rid]
        assert hardware.base_weights_present(base)

        db.update_training_run(rid, status="cancelled")
        assert client.delete(f"/api/models/{wta.BASE_ID}").status_code == 200
        assert not base.exists()
    finally:
        db.close()


# ---- fix round 1 -------------------------------------------------------------


def _managed_converter() -> Path:
    lc = wta.asset_paths().llama_cpp_dir
    lc.mkdir(parents=True)
    (lc / "convert_lora_to_gguf.py").write_text("#")
    (lc / wta.RELEASE_MARKER).write_text(LLAMA_CPP_RELEASE)
    return lc


def test_converter_refetch_refused_while_training(client, monkeypatch):
    _managed_converter()
    started: List[str] = []

    async def fake(mid: str) -> None:
        started.append(mid)

    monkeypatch.setattr(models_api, "_download_writer_asset", fake)
    tm.set_training_manager(_StubManager([], active=True))  # type: ignore[arg-type]
    r = client.post("/api/models/download", json={"id": wta.CONVERTER_ID})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "gpu_busy_training"
    assert started == []
    assert wta.CONVERTER_ID not in models_api._writer_downloads


def test_download_refused_while_its_delete_runs(client, monkeypatch):
    started: List[str] = []

    async def fake(mid: str) -> None:
        started.append(mid)

    monkeypatch.setattr(models_api, "_download_writer_asset", fake)
    models_api._writer_deletes.add(wta.BASE_ID)
    r = client.post("/api/models/download", json={"id": wta.BASE_ID})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "delete_running"
    assert started == []


def test_delete_registers_itself_while_running(client, monkeypatch):
    _write_base(_base_dir())
    seen: List[bool] = []

    def spy(paths):
        seen.append(wta.BASE_ID in models_api._writer_deletes)

    monkeypatch.setattr(wta, "delete_base", spy)
    assert client.delete(f"/api/models/{wta.BASE_ID}").status_code == 200
    assert seen == [True]
    assert wta.BASE_ID not in models_api._writer_deletes


def test_delete_refused_while_a_download_runs(client):
    _write_base(_base_dir())
    models_api._writer_downloads.add(wta.BASE_ID)
    r = client.delete(f"/api/models/{wta.BASE_ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "download_running"
    assert hardware.base_weights_present(_base_dir())


@pytest.mark.parametrize("mid", [wta.BASE_ID, wta.CONVERTER_ID])
def test_delete_oserror_is_a_500_and_still_broadcasts(client, monkeypatch, events, mid):
    _write_base(_base_dir())
    _managed_converter()

    def boom(paths):
        raise OSError("device busy")

    monkeypatch.setattr(
        wta, "delete_base" if mid == wta.BASE_ID else "delete_converter", boom
    )
    r = client.delete(f"/api/models/{mid}")
    assert r.status_code == 500
    assert "device busy" in str(r.json()["detail"])
    assert ("training", "gate_changed", {}) in events
    assert mid not in models_api._writer_deletes


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        ".",
        "a/..",
        "../x",
        "/abs/x",
        "a/b/c",
        "org",
        "org/",
        "/name",
        "org/na me",
        "org/.",
        "./name",
        "org/na\\me",
        "o rg/name",
    ],
)
def test_invalid_train_repo_falls_back_to_the_default(bad, caplog):
    with caplog.at_level("WARNING"):
        paths = wta.asset_paths({"models": {"writer_train_repo": bad}}, get_data_dir())
    assert paths.repo == DEFAULT_WRITER_TRAIN_REPO
    assert paths.base_dir == _base_dir()
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_valid_train_repo_is_kept():
    paths = wta.asset_paths(
        {"models": {"writer_train_repo": "Org-1/Model_x.8B"}}, get_data_dir()
    )
    assert paths.repo == "Org-1/Model_x.8B"
