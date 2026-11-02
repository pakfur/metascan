"""The swap of a library file for its upscaled version, and back.

The promises under test: a crash at any step never loses the original and
never leaves two rows or a stray file in a library folder; ratings, metadata
and lineage survive; Import and hygiene leave a swap alone."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core import upscale_store as store_mod
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.core.upscale_store import UpscaleError, UpscaleStore
from metascan.utils.path_utils import to_native_path, to_posix_path


class Crash(BaseException):
    """A process dying: nothing catches it."""


def png(path: Path, color: str, size: int, prompt: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    if prompt:
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "MS_POSITIVE"},
                "inputs": {"text": prompt},
            },
            "2": {
                "class_type": "KSampler",
                "inputs": {"seed": 5, "steps": 20, "cfg": 7.0, "positive": ["1", 0]},
            },
        }
        info.add_text("prompt", json.dumps(graph))
    Image.new("RGB", (size, size), color).save(path, pnginfo=info)
    return path


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.db = DatabaseManager(tmp_path / "db")
        self.library = tmp_path / "library"
        self.originals = tmp_path / "originals"
        self.staging = tmp_path / "staging"
        self.scanner = Scanner(self.db, ThumbnailCache(tmp_path / "thumbs"))
        self.trashed: List[Path] = []
        self.store = self.make_store()

    def make_store(self) -> UpscaleStore:
        def trash(path: Path) -> None:
            if path.exists():
                self.trashed.append(path)
                path.unlink()

        return UpscaleStore(
            self.db,
            lambda: self.originals,
            self.scanner.read_technical,
            supported=Scanner.SUPPORTED_EXTENSIONS,
            trash=trash,
        )

    def media(self, name: str = "a.png", prompt: str = "a red kite") -> Path:
        path = png(self.library / name, "red", 64, prompt)
        self.scanner.ingest_file(path)
        return path

    def candidate(self, source: Path, suffix: str = ".png", size: int = 256) -> int:
        made = len(list(self.staging.glob("*"))) if self.staging.exists() else 0
        path = self.staging / f"c{made}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if suffix == ".webp":
            Image.new("RGB", (size, size), "blue").save(path, "WEBP")
        else:
            png(path, "blue", size)
        return self.db.add_upscale_candidate(
            source_path=to_posix_path(source),
            file_path=to_posix_path(path),
            kind="upscale_image",
            preset_id=None,
            job_id=None,
        )

    def accept(self, candidate_id: int) -> Path:
        journal = self.store.prepare_accept(candidate_id)
        return Path(to_native_path(self.store.commit(journal)))

    def revert(self, path: Path) -> Path:
        journal = self.store.prepare_revert(to_posix_path(path))
        return Path(to_native_path(self.store.commit(journal)))

    def library_files(self) -> List[str]:
        return sorted(p.name for p in self.library.rglob("*") if p.is_file())

    def rows(self) -> List[str]:
        return sorted(self.db.all_media_paths())

    def record(self, path: Path) -> Any:
        return self.db.upscale_record(to_posix_path(path))


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


def size_of(path: Path) -> int:
    with Image.open(path) as image:
        return image.size[0]


# ---- the happy paths ----------------------------------------------------------


def test_accept_puts_the_upscaled_file_in_place_and_keeps_the_original(env):
    path = env.media()
    original_bytes = path.read_bytes()
    final = env.accept(env.candidate(path))

    assert final == path and size_of(path) == 256
    record = env.record(path)
    stored = Path(to_native_path(record["original_path"]))
    assert stored.parent == env.originals
    assert stored.read_bytes() == original_bytes
    assert record["original_name"] == "a.png"
    assert record["upscaled_size"] == path.stat().st_size
    assert env.library_files() == ["a.png"]
    assert env.db.list_upscale_candidates(to_posix_path(path)) == []
    assert env.db.list_upscale_journal() == []


def test_a_changed_extension_moves_the_row_and_keeps_the_name(env):
    path = env.media()
    final = env.accept(env.candidate(path, ".webp"))
    assert final.name == "a.webp"
    assert env.library_files() == ["a.webp"]
    assert env.rows() == [to_posix_path(final)]
    assert env.record(final) is not None and env.record(path) is None


def test_extension_indices_follow_a_move(env):
    path = env.media()
    final = env.accept(env.candidate(path, ".webp"))
    with env.db._get_connection() as conn:
        keys = {
            r["index_type"]: r["index_key"]
            for r in conn.execute(
                "SELECT index_type, index_key FROM indices WHERE file_path = ? "
                "AND index_type IN ('ext', 'path')",
                (to_posix_path(final),),
            )
        }
    assert keys == {"ext": ".webp", "path": to_posix_path(final).lower()}


def test_a_case_only_extension_change_is_the_same_file(env, monkeypatch):
    monkeypatch.setattr(store_mod, "path_key", str.casefold)
    path = env.media("a.PNG")
    env.db.save_media(env.scanner._process_media_file(path))
    final = env.accept(env.candidate(path))
    assert final == path and env.library_files() == ["a.PNG"]
    assert size_of(path) == 256


def test_a_name_clash_takes_a_number(env):
    path = env.media("a.jpg")
    png(env.library / "a.png", "green", 16)  # an unrelated file on disk
    final = env.accept(env.candidate(path))
    assert final.name == "a_2.png"
    assert size_of(env.library / "a.png") == 16


def test_upscaling_again_replaces_only_the_upscaled_file(env):
    path = env.media()
    env.accept(env.candidate(path, size=128))
    first = env.record(path)
    original_bytes = Path(to_native_path(first["original_path"])).read_bytes()

    env.accept(env.candidate(path, size=256))

    second = env.record(path)
    assert second["original_path"] == first["original_path"]
    assert Path(to_native_path(second["original_path"])).read_bytes() == original_bytes
    assert size_of(path) == 256
    assert len(list(env.originals.iterdir())) == 1
    assert len(env.trashed) == 1  # the previous upscaled file is recoverable


def test_revert_restores_the_original_and_forgets_the_record(env):
    path = env.media()
    before = path.read_bytes()
    env.accept(env.candidate(path))
    final = env.revert(path)

    assert final == path and path.read_bytes() == before
    assert env.record(path) is None
    assert list(env.originals.iterdir()) == []
    assert len(env.trashed) == 1


def test_revert_undoes_an_extension_change(env):
    path = env.media()
    upscaled = env.accept(env.candidate(path, ".webp"))
    final = env.revert(upscaled)
    assert final == path and env.library_files() == ["a.png"]
    assert env.rows() == [to_posix_path(path)]


def test_revert_of_a_file_never_upscaled_is_refused(env):
    with pytest.raises(UpscaleError) as e:
        env.store.prepare_revert(to_posix_path(env.media()))
    assert e.value.code == store_mod.CODE_NO_RECORD


def test_revert_with_the_original_missing_is_refused_and_changes_nothing(env):
    path = env.media()
    env.accept(env.candidate(path))
    Path(to_native_path(env.record(path)["original_path"])).unlink()
    with pytest.raises(UpscaleError):
        env.store.prepare_revert(to_posix_path(path))
    assert size_of(path) == 256 and env.record(path) is not None


def test_a_candidate_metascan_cannot_read_is_refused(env):
    path = env.media()
    bad = env.staging / "c.gifv"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"x")
    candidate = env.db.add_upscale_candidate(
        source_path=to_posix_path(path),
        file_path=to_posix_path(bad),
        kind="upscale_image",
    )
    with pytest.raises(UpscaleError) as e:
        env.store.prepare_accept(candidate)
    assert e.value.code == store_mod.CODE_WRONG_TYPE
    assert env.db.list_upscale_journal() == []


# ---- what must survive --------------------------------------------------------


def test_ratings_categories_and_favourite_survive_accept_and_revert(env):
    path = env.media()
    posix = to_posix_path(path)
    env.db.set_grade(posix, "pass")
    env.db.set_favorite(path, True)

    upscaled = env.accept(env.candidate(path, ".webp"))
    assert env.db.get_grade(str(upscaled)) == "pass"
    assert env.db.get_media_with_details(upscaled).is_favorite is True

    env.revert(upscaled)
    assert env.db.get_grade(str(path)) == "pass"
    assert env.db.get_media_with_details(path).is_favorite is True


def test_the_row_takes_the_new_facts_and_keeps_the_prompt(env):
    path = env.media()
    env.accept(env.candidate(path))
    media = env.db.get_media_with_details(path)
    assert (media.width, media.height) == (256, 256)
    assert media.file_size == path.stat().st_size
    assert media.prompt == "a red kite"


def test_a_swap_forgets_the_phash_and_the_embedding(env):
    path = env.media()
    posix = to_posix_path(path)
    with env.db._get_connection() as conn:
        conn.execute(
            "UPDATE media_hashes SET phash = 'ab', has_embedding = 1, "
            "clip_model = 'small' WHERE file_path = ?",
            (posix,),
        )
        conn.commit()
    env.accept(env.candidate(path))
    with env.db._get_connection() as conn:
        row = conn.execute(
            "SELECT phash, has_embedding, clip_model FROM media_hashes "
            "WHERE file_path = ?",
            (posix,),
        ).fetchone()
    assert tuple(row) == (None, 0, None)


def test_a_reingest_reads_metadata_from_the_original(env):
    """The case the whole hook exists for: a Full clean, ``ChangedFiles``."""
    path = env.media()
    env.accept(env.candidate(path))

    media = env.scanner.ingest_file(path)

    assert media.prompt == "a red kite"
    assert (media.width, media.height) == (256, 256)


def test_the_record_outlives_a_truncate(env):
    path = env.media()
    env.accept(env.candidate(path))
    assert env.db.truncate_all_data()
    assert env.record(path) is not None
    assert env.scanner.ingest_file(path).prompt == "a red kite"


def test_a_new_file_on_a_dead_records_path_keeps_its_own_metadata(env):
    path = env.media()
    env.accept(env.candidate(path))
    png(path, "green", 32, "a green field")  # a different file, same path
    assert env.scanner.ingest_file(path).prompt == "a green field"


def test_a_lost_original_falls_back_to_the_file(env):
    path = env.media()
    env.accept(env.candidate(path))
    Path(to_native_path(env.record(path)["original_path"])).unlink()
    assert env.scanner.ingest_file(path) is not None


def test_a_failed_record_lookup_never_costs_the_file(env, monkeypatch):
    path = env.media()

    def boom(*args: Any) -> None:
        raise RuntimeError("database locked")

    monkeypatch.setattr(env.db, "upscale_record", boom)
    assert env.scanner.ingest_file(path) is not None


def test_a_move_carries_the_record(env):
    path = env.media()
    env.accept(env.candidate(path))
    moved = to_posix_path(env.library / "sub" / "a.png")
    assert env.db.move_media_batch_applied([(to_posix_path(path), moved)])
    assert env.db.upscale_record(moved) is not None


def test_a_dangling_record_refuses_one_move_and_not_the_batch(env):
    a, b = env.media("a.png"), env.media("b.png")
    dead = to_posix_path(env.library / "dead.png")
    with env.db._get_connection() as conn:
        conn.execute(
            "INSERT INTO upscale_records (file_path, original_path, original_name, "
            "upscaled_size) VALUES (?, '/o/1.png', 'dead.png', 1)",
            (dead,),
        )
        conn.commit()
    moved = to_posix_path(env.library / "c.png")
    applied = env.db.move_media_batch_applied(
        [(to_posix_path(a), dead), (to_posix_path(b), moved)]
    )
    assert applied == [(to_posix_path(b), moved)]


def test_the_grid_flag(env):
    path = env.media()
    other = env.media("b.png")
    env.accept(env.candidate(path))
    flags = {
        Path(s["file_path"]).name: s["upscaled"]
        for s in env.db.get_all_media_summaries()
    }
    assert flags == {path.name: True, other.name: False}


# ---- crashes ------------------------------------------------------------------


def assert_consistent(env: Env) -> None:
    """Whatever happened: no temp files, one row per file in the library, every
    row has its file, every original a record still needs exists."""
    leftovers = [n for n in env.library_files() if n.endswith(".tmp")]
    assert leftovers == []
    paths = {to_posix_path(p) for p in env.library.rglob("*") if p.is_file()}
    assert paths == set(env.rows())
    for record in env.db.list_upscale_records():
        assert Path(to_native_path(record["original_path"])).is_file()
    assert env.db.list_upscale_journal() == []


@pytest.mark.parametrize("suffix", [".png", ".webp"])
@pytest.mark.parametrize(
    "where",
    [
        "copy_original",
        "copy_candidate",
        "technical",
        "journal_placing",
        "after_aside",
        "after_replace",
        "before_commit",
        "after_commit",
    ],
)
def test_a_crash_at_every_step_of_a_first_upscale(env, monkeypatch, where, suffix):
    path = env.media()
    original = path.read_bytes()
    candidate = env.candidate(path, suffix)
    crash_at(env, monkeypatch, where)

    with pytest.raises(Crash):
        env.accept(candidate)

    monkeypatch.undo()
    env.store = env.make_store()
    env.store.recover()
    assert_consistent(env)

    records = env.db.list_upscale_records()
    if records:
        # Rolled forward: the upscaled file is in, the original is safe.
        stored = Path(to_native_path(records[0]["original_path"]))
        assert stored.read_bytes() == original
        final = Path(to_native_path(records[0]["file_path"]))
        assert final.read_bytes() != original
    else:
        # Rolled back: the library is as it was.
        assert path.read_bytes() == original
        assert list(env.originals.glob("*")) == []


@pytest.mark.parametrize(
    "where",
    [
        "copy_candidate",
        "technical",
        "journal_placing",
        "after_aside",
        "after_replace",
        "before_commit",
        "after_commit",
    ],
)
def test_a_crash_at_every_step_of_a_re_upscale(env, monkeypatch, where):
    path = env.media()
    env.accept(env.candidate(path, size=128))
    first = env.record(path)
    original = Path(to_native_path(first["original_path"])).read_bytes()
    candidate = env.candidate(path, size=256)
    crash_at(env, monkeypatch, where)

    with pytest.raises(Crash):
        env.accept(candidate)

    monkeypatch.undo()
    env.store = env.make_store()
    env.store.recover()
    assert_consistent(env)
    record = env.record(path)
    assert record["original_path"] == first["original_path"]
    assert Path(to_native_path(record["original_path"])).read_bytes() == original
    assert size_of(path) in (128, 256)


@pytest.mark.parametrize(
    "where",
    [
        "copy_candidate",
        "technical",
        "journal_placing",
        "after_aside",
        "after_replace",
        "before_commit",
        "after_commit",
    ],
)
def test_a_crash_at_every_step_of_a_revert(env, monkeypatch, where):
    path = env.media()
    before = path.read_bytes()
    env.accept(env.candidate(path, ".webp"))
    upscaled = path.with_suffix(".webp")
    crash_at(env, monkeypatch, where)

    with pytest.raises(Crash):
        env.revert(upscaled)

    monkeypatch.undo()
    env.store = env.make_store()
    env.store.recover()
    assert_consistent(env)
    if env.db.list_upscale_records():
        assert upscaled.exists()  # rolled back
        stored = env.db.list_upscale_records()[0]["original_path"]
        assert Path(to_native_path(stored)).read_bytes() == before
    else:
        assert path.read_bytes() == before  # rolled forward


def crash_at(env: Env, monkeypatch: Any, where: str) -> None:
    real_copy = store_mod._copy_verified
    real_replace = os.replace
    store = env.store
    copies = {"n": 0}
    replaces = {"n": 0}

    def copy(source: Path, target: Path) -> None:
        copies["n"] += 1
        is_original = target.parent == env.originals
        real_copy(source, target)
        if (where == "copy_original" and is_original) or (
            where == "copy_candidate" and not is_original
        ):
            raise Crash()

    def replace(source: Any, target: Any) -> None:
        replaces["n"] += 1
        real_replace(source, target)
        if where == "after_aside" and replaces["n"] == 1:
            raise Crash()
        if where == "after_replace" and replaces["n"] == 2:
            raise Crash()

    monkeypatch.setattr(store_mod, "_copy_verified", copy)
    monkeypatch.setattr(store_mod.os, "replace", replace)

    if where == "technical":

        def technical(path: Path) -> Dict[str, Any]:
            raise Crash()

        monkeypatch.setattr(store, "technical", technical)
    if where == "journal_placing":
        real_set = env.db.set_upscale_journal

        def set_journal(journal_id: int, **fields: Any) -> None:
            real_set(journal_id, **fields)
            if fields.get("phase") == "placing":
                raise Crash()

        monkeypatch.setattr(env.db, "set_upscale_journal", set_journal)
    if where == "before_commit":

        def apply(*args: Any) -> None:
            raise Crash()

        monkeypatch.setattr(env.db, "apply_upscale_swap", apply)
    if where == "after_commit":
        real_tidy = store._tidy

        def tidy(row: Dict[str, Any]) -> None:
            raise Crash()

        monkeypatch.setattr(store, "_tidy", tidy)
        assert real_tidy is not None


def test_a_locked_file_refuses_the_swap_and_changes_nothing(env, monkeypatch):
    path = env.media()
    before = path.read_bytes()
    candidate = env.candidate(path)
    real = os.replace
    monkeypatch.setattr(store_mod, "LOCK_RETRY_DELAYS_S", ())

    def locked(source: Any, target: Any) -> None:
        if Path(source) == path:
            raise PermissionError(13, "in use")
        real(source, target)

    monkeypatch.setattr(store_mod.os, "replace", locked)
    with pytest.raises(UpscaleError) as e:
        env.accept(candidate)
    assert e.value.code == store_mod.CODE_FILE_IN_USE
    assert path.read_bytes() == before
    assert env.record(path) is None
    assert_consistent(env)
    assert env.db.get_upscale_candidate(candidate) is not None  # still offered


def test_a_lock_that_clears_is_retried(env, monkeypatch):
    path = env.media()
    candidate = env.candidate(path)
    real = os.replace
    calls = {"n": 0}
    monkeypatch.setattr(store_mod, "LOCK_RETRY_DELAYS_S", (0.0, 0.0))

    def once(source: Any, target: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "in use")
        real(source, target)

    monkeypatch.setattr(store_mod.os, "replace", once)
    env.accept(candidate)
    assert size_of(path) == 256


def test_a_failed_commit_is_finished_later(env, monkeypatch):
    path = env.media()
    candidate = env.candidate(path)

    def boom(*args: Any) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(env.db, "apply_upscale_swap", boom)
    with pytest.raises(UpscaleError) as e:
        env.accept(candidate)
    assert e.value.code == store_mod.CODE_UNFINISHED
    assert to_posix_path(path) in env.db.upscale_busy_paths()

    monkeypatch.undo()
    env.store.recover()
    assert env.record(path) is not None and size_of(path) == 256
    assert_consistent(env)


def test_a_file_that_will_not_go_keeps_the_journal_until_it_does(env, monkeypatch):
    path = env.media()
    env.accept(env.candidate(path, size=128))
    candidate = env.candidate(path, size=256)
    refused = {"on": True}
    trash = env.store.trash

    def stubborn(target: Path) -> None:
        if refused["on"]:
            raise PermissionError(13, "in use")
        trash(target)

    env.store.trash = stubborn
    env.accept(candidate)
    assert len(env.db.list_upscale_journal()) == 1
    assert env.db.upscale_busy_paths() == set()  # the row is right already

    refused["on"] = False
    assert env.store.recover() == 1
    assert_consistent(env)


# ---- delete --------------------------------------------------------------------


def test_deleting_the_item_trashes_its_original_and_candidates(env):
    from metascan.core.training_archive import delete_keeping_signal

    path = env.media()
    env.accept(env.candidate(path))
    spare = env.candidate(path)
    stored = Path(to_native_path(env.record(path)["original_path"]))
    assert stored.exists()

    delete_keeping_signal(
        env.db,
        [path],
        lambda p: p.unlink(),
        lambda thumbs: env.db.delete_media(path, None),
    )

    assert not stored.exists()
    assert env.record(path) is None
    assert env.db.get_upscale_candidate(spare) is None


def test_a_stale_candidate_goes_when_a_new_run_starts(env):
    path = env.media()
    chosen = env.candidate(path)
    old = env.candidate(path)
    env.accept(chosen)
    assert env.store.purge_stale_candidates(to_posix_path(path)) == 1
    assert env.db.get_upscale_candidate(old) is None


def test_candidates_stay_before_any_accept(env):
    path = env.media()
    candidate = env.candidate(path)
    assert env.store.purge_stale_candidates(to_posix_path(path)) == 0
    assert env.db.get_upscale_candidate(candidate) is not None


# ---- Import, hygiene and FAISS ---------------------------------------------------


def _ingest_one(env: Env, path: Path) -> str:
    from backend.services.background import import_process as ip

    class Proc:
        _scanner = env.scanner

        @staticmethod
        def _db() -> DatabaseManager:
            return env.db

    return ip.ImportProcess._ingest_one(Proc(), str(path))


def _mid_swap(env: Env, path: Path, suffix: str = ".png") -> Path:
    """Stop a swap after the file moved and before the row did."""
    journal = env.store.prepare_accept(env.candidate(path, suffix))
    row = env.db.get_upscale_journal(journal)
    env.db.set_upscale_journal(journal, phase="placing")
    os.replace(to_native_path(row["media_path"]), to_native_path(row["aside_path"]))
    os.replace(to_native_path(row["tmp_path"]), to_native_path(row["target_path"]))
    return Path(to_native_path(row["target_path"]))


def test_import_leaves_a_swap_alone_until_the_row_is_written(env):
    from backend.services.background import import_process as ip

    path = env.media()
    target = _mid_swap(env, path, ".webp")

    assert _ingest_one(env, target) == ip.SKIPPED
    assert env.rows() == [to_posix_path(path)]

    env.store.recover()
    assert _ingest_one(env, target) == ip.SKIPPED  # it has a row now
    assert env.rows() == [to_posix_path(target)]


def test_hygiene_does_not_reread_a_file_mid_swap(env):
    from types import SimpleNamespace

    from backend.services.background.hygiene_checks import ChangedFiles

    path = env.media()
    _mid_swap(env, path)
    ctx = SimpleNamespace(db=env.db, scanner=env.scanner)
    assert ChangedFiles._reingest(ctx, to_posix_path(path)) is False
    assert env.db.get_media_with_details(path).width == 64


def test_a_finished_swap_reads_as_unchanged(env):
    from backend.services.background.hygiene_checks import ChangedFiles

    path = env.media()
    final = env.accept(env.candidate(path, ".webp"))
    page = env.db.media_stat_page("", 10)
    assert [row[0] for row in page] == [to_posix_path(final)]
    assert ChangedFiles._differing(page) == []


def test_a_changed_extension_never_renames_the_old_vector(env):
    """The vector described the old pixels: the file is embedded again."""
    from metascan.core.faiss_renames import apply_pending_renames

    path = env.media()
    posix = to_posix_path(path)
    with env.db._get_connection() as conn:
        conn.execute(
            "UPDATE media_hashes SET has_embedding = 1 WHERE file_path = ?", (posix,)
        )
        conn.commit()
    final = env.accept(env.candidate(path, ".webp"))
    assert env.db.list_faiss_pending_renames() == [(posix, to_posix_path(final))]

    class Index:
        renamed: list = []

        def has_file(self, path: str) -> bool:
            return True

        def rename_applied(self, pairs: Any) -> list:
            self.renamed.extend(pairs)
            return list(pairs)

    index = Index()
    apply_pending_renames(env.db, index)
    assert index.renamed == []
    assert env.db.list_faiss_pending_renames() == []


def test_facts_are_never_read_from_the_tmp_name(env, monkeypatch):
    """A reader goes by the extension: a video called .tmp reads as an image."""
    seen: List[str] = []
    read = env.store.technical

    def technical(path: Path) -> Dict[str, Any]:
        seen.append(path.suffix)
        return read(path)

    monkeypatch.setattr(env.store, "technical", technical)
    path = env.media()
    env.accept(env.candidate(path, ".webp"))
    assert seen == [".webp"]
    env.revert(path.with_suffix(".webp"))
    assert seen == [".webp", ".png"]


def test_a_recorded_mtime_matches_the_placed_file(env):
    """ChangedFiles compares them: the copy must keep the time it was read at."""
    from metascan.core.move_detector import mtimes_equal

    path = env.media()
    env.accept(env.candidate(path))
    (_, _, mtime) = env.db.media_stat_page("", 1)[0]
    assert mtimes_equal(path.stat().st_mtime, mtime)


def test_an_unreadable_file_is_refused_with_a_code(env):
    path = env.media()
    candidate = env.candidate(path)
    row = env.db.get_upscale_candidate(candidate)
    Path(to_native_path(row["file_path"])).write_bytes(b"not an image")
    with pytest.raises(UpscaleError) as e:
        env.store.prepare_accept(candidate)
    assert e.value.code == store_mod.CODE_WRONG_TYPE
    assert env.db.list_upscale_journal() == []
    assert list(env.originals.glob("*")) == []
    assert env.library_files() == ["a.png"]
