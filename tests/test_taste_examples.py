"""Variant angles and taste-guided examples (taste-writer-variants task 3).

Every prompt and caption below is synthetic. The FAISS index and the CLIP
client are fakes; the database is a real isolated one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import WRITER_BASE_TASTE
from metascan.core.media import Media
from metascan.core.prompt_store import PromptStore, get_prompt_store
from metascan.core.taste_examples import (
    TASTE_EXAMPLE_MAX_CHARS,
    TASTE_EXAMPLES_K,
    matches_model_family,
    variant_angles,
)
from backend.services.taste_examples_service import (
    CLIP_TIMEOUT_S,
    TasteExamplesService,
)

DIM = 32
CAPTION = "SYNTH-CAPTION a lighthouse keeper at dusk"


def _media(path: str, prompt: Optional[str] = None, model: Any = ()) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        prompt=prompt,
        model=list(model),
    )


def _add(
    db: DatabaseManager,
    path: str,
    *,
    prompt: Optional[str] = None,
    model: Any = (),
    grade: Optional[str] = "pass",
    favorite: bool = False,
    seen: bool = False,
    t2i_model: Optional[str] = None,
    prompt_used: Optional[str] = None,
) -> None:
    """A library item, ✓ by default (``grade=None`` leaves it ungraded);
    ``favorite`` / ``seen`` star and view it, which never makes an
    example."""
    db.save_media(_media(path, prompt, model))
    if t2i_model is not None or prompt_used is not None:
        db.create_t2i_image(file_path=path, model=t2i_model, prompt_used=prompt_used)
    if seen:
        assert db.mark_seen(path) is not None
    if favorite:
        assert db.set_favorite(Path(path), True)
    if grade is not None:
        assert db.set_grade(path, grade) is not None


class FakeIndex:
    def __init__(self, ranked: List[str], dim: int = DIM) -> None:
        self.ranked = ranked
        self.is_loaded = True
        self.meta: Dict[str, Any] = {"embedding_dim": dim, "model_key": "small"}
        self._index = None
        self.calls: List[int] = []

    def search(self, vec: np.ndarray, top_k: int = 50) -> List[Tuple[str, float]]:
        self.calls.append(top_k)
        n = len(self.ranked)
        return [(p, 1.0 - i / (n + 1)) for i, p in enumerate(self.ranked)][:top_k]


class FakeClient:
    def __init__(
        self,
        delay: float = 0.0,
        dim: int = DIM,
        fail: bool = False,
        state: str = "ready",
    ):
        self.delay = delay
        self.dim = dim
        self.fail = fail
        self.state = state
        self.texts: List[str] = []
        self.started: List[Any] = []

    async def ensure_started(self, *args: Any, **kwargs: Any) -> None:
        self.started.append((args, kwargs))

    async def start(self, *args: Any, **kwargs: Any) -> None:
        self.started.append((args, kwargs))

    async def reload(self, *args: Any, **kwargs: Any) -> None:
        self.started.append((args, kwargs))

    async def encode_text(self, text: str, timeout: float = 60.0) -> np.ndarray:
        self.texts.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("worker crashed")
        vec = np.ones(self.dim, dtype=np.float32)
        return vec / np.linalg.norm(vec)


def _svc(
    db: DatabaseManager,
    index: Optional[FakeIndex],
    client: Optional[FakeClient],
    timeout_s: float = CLIP_TIMEOUT_S,
) -> TasteExamplesService:
    return TasteExamplesService(
        db,
        get_client=lambda: client,
        get_index=lambda: index,
        timeout_s=timeout_s,
    )


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(tmp_path / "db")


LONG = "SYNTH-LONG " + "word " * 300

RANKED = [
    "/lib/notpass.png",  # best match, but ★ only (no grade)
    "/lib/clip.mp4",  # ✓ video
    "/lib/noprompt.png",  # ✓, no known prompt
    "/lib/t2i_a.png",  # t2i prompt wins over the metadata prompt
    "/lib/lib_b.png",  # library metadata prompt
    "/lib/long.png",  # truncated
    "/lib/lib_c.png",  # fourth valid one: beyond k=3
]


@pytest.fixture
def svc_fake_index(db: DatabaseManager) -> Tuple[TasteExamplesService, FakeIndex]:
    _add(
        db,
        "/lib/notpass.png",
        prompt="SYNTH-NOTPASS",
        model=["krea2_dev"],
        grade=None,
        favorite=True,
    )
    _add(db, "/lib/clip.mp4", prompt="SYNTH-VIDEO", model=["krea2_dev"])
    _add(db, "/lib/noprompt.png", prompt=None, model=["krea2_dev"])
    _add(
        db,
        "/lib/t2i_a.png",
        prompt="SYNTH-META-A",
        model=["something"],
        t2i_model="krea2",
        prompt_used="SYNTH-T2I-A a keeper climbs the stairs",
    )
    _add(db, "/lib/lib_b.png", prompt="SYNTH-LIB-B a lamp glows", model=["Krea2_FP8"])
    _add(db, "/lib/long.png", prompt=LONG, model=["krea2_dev"])
    _add(db, "/lib/lib_c.png", prompt="SYNTH-LIB-C gulls", model=["krea2_dev"])
    index = FakeIndex(RANKED)
    return _svc(db, index, FakeClient()), index


# --------------------------------------------------------------------------
# core helpers
# --------------------------------------------------------------------------


def test_constants() -> None:
    assert TASTE_EXAMPLES_K == 3
    assert TASTE_EXAMPLE_MAX_CHARS == 600
    assert CLIP_TIMEOUT_S == 10.0
    assert WRITER_BASE_TASTE == "base+taste"


def test_default_angles_are_shipped() -> None:
    assert variant_angles(get_prompt_store()) == [
        "composition",
        "light",
        "emotion",
        "realism",
    ]


def test_variant_angles_hot_reload(tmp_path: Path) -> None:
    path = tmp_path / "meta_prompt.yml"
    path.write_text("T2I_VARIANT_ANGLES: |-\n  composition\n  - light\n\n  emotion\n")
    store = PromptStore(path)
    assert variant_angles(store) == ["composition", "light", "emotion"]
    path.write_text("T2I_VARIANT_ANGLES: |-\n  realism\n  colour\n")
    store.reload()
    assert variant_angles(store) == ["realism", "colour"]
    path.write_text("OTHER: x\n")
    store.reload()
    assert variant_angles(store) == []


def test_model_family_matching() -> None:
    assert matches_model_family(["krea2_dev.safetensors"], "krea2")
    assert matches_model_family(["qwen_image_fp8.safetensors"], "qwen")
    assert matches_model_family(["juggernautXL_v9.safetensors"], "sd")
    assert matches_model_family(["sd_xl_base_1.0.safetensors"], "sd")
    for tag_dialect in (
        "ponyDiffusionV6XL.safetensors",
        "illustriousXL_v01.safetensors",
        "noobaiXLNAIXL_vPred10.safetensors",
        "animagine-xl-3.1.safetensors",
        "waiNSFWIllustrious_v14.safetensors",
    ):
        assert not matches_model_family([tag_dialect], "sd"), tag_dialect
    assert matches_model_family(["z_image_turbo_bf16.safetensors"], "zimage")
    assert not matches_model_family(["flux1-dev.safetensors"], "krea2")
    assert not matches_model_family([], "krea2")
    assert not matches_model_family(["krea2_dev"], "unknown-model")


# --------------------------------------------------------------------------
# DB candidates
# --------------------------------------------------------------------------


def test_pass_prompt_candidates(db: DatabaseManager) -> None:
    _add(db, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    _add(db, "/lib/b.png", prompt="SYNTH-B", model=["flux1"], grade=None)
    _add(db, "/lib/c.png", prompt="  ", model=["krea2_dev"])
    _add(
        db, "/lib/d.png", prompt="SYNTH-D-META", t2i_model="qwen", prompt_used="SYNTH-D"
    )
    _add(db, "/lib/e.webm", prompt="SYNTH-E", model=["krea2_dev"])
    paths = ["/lib/a.png", "/lib/b.png", "/lib/c.png", "/lib/d.png", "/lib/e.webm"]
    paths.append("/lib/missing.png")
    assert db.pass_prompt_candidates(paths) == {
        "/lib/a.png": "SYNTH-A",
        "/lib/d.png": "SYNTH-D",
    }
    assert db.pass_prompt_candidates(paths, model_id="krea2") == {
        "/lib/a.png": "SYNTH-A"
    }
    assert db.pass_prompt_candidates(paths, model_id="qwen") == {
        "/lib/d.png": "SYNTH-D"
    }
    assert db.pass_prompt_candidates([]) == {}


async def test_examples_are_passed_prompts(db: DatabaseManager) -> None:
    # Only a ✓ image's prompt guides the writer: ★ alone, ✗ (★ or not), a
    # view alone and no grade at all never do.
    krea = ["krea2_dev"]
    _add(
        db, "/lib/star.png", prompt="SYNTH-STAR", model=krea, grade=None, favorite=True
    )
    _add(
        db,
        "/lib/starfail.png",
        prompt="SYNTH-STARFAIL",
        model=krea,
        grade="fail",
        favorite=True,
    )
    _add(db, "/lib/fail.png", prompt="SYNTH-FAIL", model=krea, grade="fail")
    _add(db, "/lib/seen.png", prompt="SYNTH-SEEN", model=krea, grade=None, seen=True)
    _add(db, "/lib/none.png", prompt="SYNTH-NONE", model=krea, grade=None)
    _add(db, "/lib/cleared.png", prompt="SYNTH-CLEARED", model=krea)
    assert db.set_grade("/lib/cleared.png", None) is not None
    _add(db, "/lib/pass.png", prompt="SYNTH-PASS", model=krea)
    _add(db, "/lib/starpass.png", prompt="SYNTH-STARPASS", model=krea, favorite=True)
    _add(db, "/lib/t2i.png", t2i_model="krea2", prompt_used="SYNTH-T2I-PASS")
    _add(
        db,
        "/lib/t2ifail.png",
        t2i_model="krea2",
        prompt_used="SYNTH-T2I-FAIL",
        grade="fail",
    )
    ranked = [
        "/lib/star.png",
        "/lib/starfail.png",
        "/lib/fail.png",
        "/lib/seen.png",
        "/lib/none.png",
        "/lib/cleared.png",
        "/lib/pass.png",
        "/lib/t2ifail.png",
        "/lib/starpass.png",
        "/lib/t2i.png",
    ]
    assert db.pass_prompt_candidates(ranked) == {
        "/lib/pass.png": "SYNTH-PASS",
        "/lib/starpass.png": "SYNTH-STARPASS",
        "/lib/t2i.png": "SYNTH-T2I-PASS",
    }
    examples, warning = await _svc(db, FakeIndex(ranked), FakeClient()).examples_for(
        CAPTION, "krea2"
    )
    assert warning is None
    assert examples == ["SYNTH-PASS", "SYNTH-STARPASS", "SYNTH-T2I-PASS"]


def _grade_index_sql(db: DatabaseManager) -> str:
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_media_judgments_grade'"
        ).fetchone()
    assert row is not None
    return str(row["sql"])


def test_pass_candidates_come_from_a_covering_index(db: DatabaseManager) -> None:
    # The media table is never read to find the ✓ paths (its data blob sits
    # before every column): the (grade, file_path) index answers it alone.
    assert "file_path" in _grade_index_sql(db)
    sql = DatabaseManager._PASS_PATHS_SQL.format(marks="?, ?")
    with db._get_connection() as conn:
        plan = " | ".join(
            r["detail"]
            for r in conn.execute("EXPLAIN QUERY PLAN " + sql, ["/a.png", "/b.png"])
        )
    assert "COVERING INDEX idx_media_judgments_grade" in plan, plan
    assert "media_judgments" in plan and " media " not in f" {plan} ", plan


def test_an_older_grade_index_is_rebuilt_with_the_path(tmp_path: Path) -> None:
    first = DatabaseManager(tmp_path / "db")
    _add(first, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    with first._get_connection() as conn:
        conn.execute("DROP INDEX idx_media_judgments_grade")
        conn.execute("CREATE INDEX idx_media_judgments_grade ON media_judgments(grade)")
        conn.commit()
    assert "file_path" not in _grade_index_sql(first)
    again = DatabaseManager(tmp_path / "db")
    assert "file_path" in _grade_index_sql(again)
    assert again.pass_prompt_candidates(["/lib/a.png"]) == {"/lib/a.png": "SYNTH-A"}


# --------------------------------------------------------------------------
# service
# --------------------------------------------------------------------------


async def test_examples_from_passed_images_only(svc_fake_index: Any) -> None:
    svc, index = svc_fake_index
    examples, warning = await svc.examples_for(CAPTION, "krea2")
    assert warning is None
    assert index.calls == [200]
    assert len(examples) == 3
    assert examples[0] == "SYNTH-T2I-A a keeper climbs the stairs"
    assert examples[1] == "SYNTH-LIB-B a lamp glows"
    assert examples[2].startswith("SYNTH-LONG word")
    assert len(examples[2]) <= TASTE_EXAMPLE_MAX_CHARS
    joined = "\n".join(examples)
    for excluded in ("SYNTH-NOTPASS", "SYNTH-VIDEO", "SYNTH-META-A", "SYNTH-LIB-C"):
        assert excluded not in joined

    two, _ = await svc.examples_for(CAPTION, "krea2", k=2)
    assert two == examples[:2]


async def test_duplicate_prompts_count_once(db: DatabaseManager) -> None:
    for name in ("x1", "x2", "x3"):
        _add(db, f"/lib/{name}.png", t2i_model="krea2", prompt_used="SYNTH-SAME")
    _add(db, "/lib/y.png", t2i_model="krea2", prompt_used="SYNTH-OTHER")
    index = FakeIndex(["/lib/x1.png", "/lib/x2.png", "/lib/x3.png", "/lib/y.png"])
    examples, warning = await _svc(db, index, FakeClient()).examples_for(
        CAPTION, "krea2"
    )
    assert warning is None
    assert examples == ["SYNTH-SAME", "SYNTH-OTHER"]


async def test_examples_model_family_filter(svc_fake_index: Any) -> None:
    svc, _ = svc_fake_index
    # Nothing in the fixture is a ✓ Qwen-Image image.
    examples, warning = await svc.examples_for(CAPTION, "qwen")
    assert examples == []
    assert warning == (
        "taste guidance unavailable: "
        "no passed images with a known prompt match this caption"
    )


async def test_no_index_or_clip_degrades(db: DatabaseManager) -> None:
    _add(db, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    cases = {
        "no index": _svc(db, None, FakeClient()),
        "index not loaded": _svc(db, _unloaded(), FakeClient()),
        "no clip": _svc(db, FakeIndex(["/lib/a.png"]), None),
        "clip error": _svc(db, FakeIndex(["/lib/a.png"]), FakeClient(fail=True)),
        "dim mismatch": _svc(db, FakeIndex(["/lib/a.png"], dim=64), FakeClient()),
        "unknown model": None,
    }
    for name, svc in cases.items():
        if svc is None:
            svc = _svc(db, FakeIndex(["/lib/a.png"]), FakeClient())
            examples, warning = await svc.examples_for(CAPTION, "no-such-model")
        else:
            examples, warning = await svc.examples_for(CAPTION, "krea2")
        assert examples == [], name
        assert warning is not None and warning.startswith(
            "taste guidance unavailable: "
        ), name


def _unloaded() -> FakeIndex:
    index = FakeIndex([])
    index.is_loaded = False
    return index


async def test_default_index_never_loads_from_disk(
    db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api import similarity

    def _boom() -> Any:
        raise AssertionError("_get_faiss_manager must not be called")

    monkeypatch.setattr(similarity, "_faiss_manager", None)
    monkeypatch.setattr(similarity, "_get_faiss_manager", _boom)
    svc = TasteExamplesService(db, get_client=lambda: FakeClient())
    examples, warning = await svc.examples_for(CAPTION, "krea2")
    assert examples == []
    assert warning is not None and "index" in warning


async def test_clip_timeout_degrades(db: DatabaseManager) -> None:
    _add(db, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    svc = _svc(db, FakeIndex(["/lib/a.png"]), FakeClient(delay=30), timeout_s=0.2)
    loop = asyncio.get_running_loop()
    start = loop.time()
    examples, warning = await svc.examples_for(CAPTION, "krea2")
    assert loop.time() - start < 5
    assert examples == []
    assert warning is not None and "timed out" in warning


async def test_clip_not_ready_is_never_started(db: DatabaseManager) -> None:
    _add(db, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    for state in ("idle", "spawning", "loading", "error", "stopped"):
        client = FakeClient(state=state)
        svc = _svc(db, FakeIndex(["/lib/a.png"]), client)
        examples, warning = await svc.examples_for(CAPTION, "krea2")
        assert examples == [], state
        assert warning == "taste guidance unavailable: CLIP is not loaded", state
        assert client.started == [], state
        assert client.texts == [], state


async def test_ready_clip_is_used_without_starting(db: DatabaseManager) -> None:
    _add(db, "/lib/a.png", prompt="SYNTH-A", model=["krea2_dev"])
    client = FakeClient(state="ready")
    examples, warning = await _svc(db, FakeIndex(["/lib/a.png"]), client).examples_for(
        CAPTION, "krea2"
    )
    assert (examples, warning) == (["SYNTH-A"], None)
    assert client.started == []
    assert client.texts == [CAPTION]


async def test_unexpected_error_degrades_without_prompt_text(
    db: DatabaseManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "pass_prompt_candidates", _boom)
    caplog.set_level(logging.DEBUG)
    examples, warning = await _svc(
        db, FakeIndex(["/lib/a.png"]), FakeClient()
    ).examples_for(CAPTION, "krea2")
    assert examples == [] and warning is not None
    assert warning.startswith("taste guidance unavailable: ")
    warned = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.endswith("taste_examples_service")
    ]
    assert warned and warned[0].exc_info
    assert warned[0].exc_info[0] is RuntimeError
    for record in caplog.records:
        assert "SYNTH-" not in record.getMessage()
        assert "SYNTH-" not in caplog.text


async def test_degrade_reasons_are_debug_only(
    db: DatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await _svc(db, None, FakeClient()).examples_for(CAPTION, "krea2")
    await _svc(db, FakeIndex([]), FakeClient(state="loading")).examples_for(
        CAPTION, "krea2"
    )
    mine = [r for r in caplog.records if r.name.endswith("taste_examples_service")]
    assert mine and all(r.levelno == logging.DEBUG for r in mine)


async def test_no_prompt_text_logged(
    svc_fake_index: Any, db: DatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    svc, _ = svc_fake_index
    caplog.set_level(logging.INFO)
    await svc.examples_for(CAPTION, "krea2")
    await svc.examples_for(CAPTION, "qwen")
    await _svc(db, FakeIndex(RANKED), FakeClient(fail=True)).examples_for(
        CAPTION, "krea2"
    )
    secrets = ("SYNTH-CAPTION", "SYNTH-T2I-A", "SYNTH-LIB-B", "SYNTH-LONG", "SYNTH-")
    for record in caplog.records:
        if record.levelno < logging.INFO:
            continue
        text = record.getMessage()
        for secret in secrets:
            assert secret not in text, (record.name, text)


# --------------------------------------------------------------------------
# the in-memory index survives an embedding run (final review item 1)
# --------------------------------------------------------------------------


class _ReloadedIndex(FakeIndex):
    """Stands in for FaissIndexManager: ``load`` blocks until released."""

    instances: List["_ReloadedIndex"] = []
    release: Optional[Any] = None

    def __init__(self, index_dir: Any) -> None:
        super().__init__(RANKED)
        self.is_loaded = False
        self.index_dir = index_dir
        _ReloadedIndex.instances.append(self)

    def load(self) -> None:
        if _ReloadedIndex.release is not None:
            assert _ReloadedIndex.release.wait(10)
        self.is_loaded = True


async def test_examples_survive_an_embedding_complete(
    svc_fake_index: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every embedding ``on_complete`` (the auto-index build after each T2I
    ingest included) used to drop the in-memory index, so every later
    caption recorded ``base``. The index is rebuilt off the loop and
    swapped in atomically: the old one stays readable until the new one is
    loaded."""
    import threading
    from types import SimpleNamespace

    from backend.api import similarity

    svc, old = svc_fake_index
    _ReloadedIndex.instances = []
    _ReloadedIndex.release = threading.Event()
    monkeypatch.setattr(similarity, "FaissIndexManager", _ReloadedIndex)
    monkeypatch.setattr(similarity, "_start_tombstone_backfill", lambda fm: None)
    monkeypatch.setattr(similarity, "_faiss_manager", old)
    monkeypatch.setattr(similarity, "_server_loop", asyncio.get_running_loop())
    monkeypatch.setattr(similarity, "_on_embedding_complete", None)
    monkeypatch.setattr(similarity, "_current_scan_tag_with_vlm", False)
    eq = SimpleNamespace(index_dir=tmp_path, get_last_progress=lambda: {})
    monkeypatch.setattr(similarity, "_get_embedding_queue", lambda: eq)
    similarity._attach_embedding_callbacks(eq)  # type: ignore[arg-type]
    guided = TasteExamplesService(svc.db, get_client=lambda: FakeClient())

    # on_complete fires on the embedding poller's worker thread.
    await asyncio.to_thread(eq.on_complete, 3)  # type: ignore[attr-defined]
    assert similarity._faiss_manager is old  # still readable while loading
    examples, warning = await guided.examples_for(CAPTION, "krea2")
    assert warning is None and len(examples) == 3

    _ReloadedIndex.release.set()
    for _ in range(200):
        if similarity._faiss_manager is not old:
            break
        await asyncio.sleep(0.01)
    new = similarity._faiss_manager
    assert isinstance(new, _ReloadedIndex) and new.is_loaded
    examples, warning = await guided.examples_for(CAPTION, "krea2")
    assert warning is None and len(examples) == 3


async def test_back_to_back_reloads_install_the_newest_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from backend.api import similarity

    _ReloadedIndex.instances = []
    _ReloadedIndex.release = None
    old = FakeIndex(RANKED)
    monkeypatch.setattr(similarity, "FaissIndexManager", _ReloadedIndex)
    monkeypatch.setattr(similarity, "_start_tombstone_backfill", lambda fm: None)
    monkeypatch.setattr(similarity, "_faiss_manager", old)
    monkeypatch.setattr(similarity, "_server_loop", asyncio.get_running_loop())
    monkeypatch.setattr(
        similarity, "_get_embedding_queue", lambda: SimpleNamespace(index_dir=tmp_path)
    )
    await asyncio.to_thread(similarity._reload_faiss_after_index_build)
    await asyncio.to_thread(similarity._reload_faiss_after_index_build)
    for _ in range(200):
        if len(_ReloadedIndex.instances) == 2 and all(
            i.is_loaded for i in _ReloadedIndex.instances
        ):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert similarity._faiss_manager is _ReloadedIndex.instances[-1]


async def test_no_manager_loaded_stays_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api import similarity

    def _boom(*a: Any) -> Any:
        raise AssertionError("must not construct a manager nobody loaded")

    monkeypatch.setattr(similarity, "FaissIndexManager", _boom)
    monkeypatch.setattr(similarity, "_faiss_manager", None)
    monkeypatch.setattr(similarity, "_server_loop", asyncio.get_running_loop())
    await asyncio.to_thread(similarity._reload_faiss_after_index_build)
    await asyncio.sleep(0.05)
    assert similarity._faiss_manager is None
