"""A tombstone gets a CLIP vector read from the FAISS index by its path only
with evidence that the row there is its own file's. A row holds the vector
of the last file embedded at the path, which after a delete may be another
file's (Review Focus 5).

* At deletion (the installed in-memory lookup): only when the file's
  ``media_hashes`` row says it was embedded with the index's CLIP model,
  and only from an index loaded after that mark. The evidence goes on the
  tombstone: ``embedded_model`` (``''`` when the file was never embedded,
  NULL when nothing is known -- a tombstone written before this evidence,
  or a mark older than the fix, which carries no time) and ``embedded_at``
  (the mark's time, ``media_hashes.embedding_updated_at``).
* By the backfill once an index loads (``tombstones_for_vector_backfill``),
  into ``tombstone_vectors`` under the index's model: only while nothing
  has been embedded at the path since the tombstone was written
  (``path_rewritten = 0``; the embedding worker stamps it), and either the
  file was embedded with the index's model before the index was loaded,
  or the tombstone has no evidence (NULL) and passes the old guards -- no
  vector for any model, no archived thumbnail, a path no live media row or
  other tombstone holds.

A mark is stored to the second (SQLite ``CURRENT_TIMESTAMP``), so an index
counts as loaded after it only from the next whole second on.

The worker-side stamping and the review's sequences are in
``tests/test_faiss_vector_replacement.py``."""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from backend.services import judgments_service
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.utils.path_utils import to_native_path


# An in-memory index loaded after every mark these tests make.
LATER = time.time() + 3600


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


class FakeFaiss:
    def __init__(self, vectors, model_key="clip-medium", loaded=True, loaded_at=LATER):
        self.vectors = vectors
        self.meta = {"model_key": model_key}
        self.is_loaded = loaded
        self.loaded_at = loaded_at
        self.calls = []

    def get_embedding(self, key):
        self.calls.append(key)
        return self.vectors.get(key)


def _graded_delete(
    db, path, grade="pass", *, embedded_with: Optional[str] = None, thumb=None
) -> int:
    """Ingest, optionally mark embedded with ``embedded_with`` (what the
    worker does once it wrote the row), grade and delete ``path``; returns
    the new tombstone's id."""
    db.save_media(_media(path))
    if embedded_with is not None:
        db.mark_embedded([path], embedded_with)
    db.set_grade(path, grade)
    assert db.delete_media(Path(path), thumb)
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


def _make_legacy(db, *tomb_ids) -> None:
    """A tombstone written before deletions recorded the embedding evidence:
    ``embedded_model`` is NULL there."""
    with db.lock, db._get_connection() as conn:
        conn.executemany(
            "UPDATE media_tombstones SET embedded_model = NULL WHERE id = ?",
            [(t,) for t in tomb_ids],
        )
        conn.commit()


def _tombstone(db, tomb_id):
    (row,) = [t for t in db.list_tombstones() if t["id"] == tomb_id]
    return row


def _vector(db, tomb_id, model):
    blob = db.get_tombstone_vectors(tomb_id).get(model)
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def _listed(db, model="clip-medium", loaded_at=LATER):
    return [t for t, _ in db.tombstones_for_vector_backfill(model, loaded_at)]


def _mark_epoch(db, path) -> float:
    """When ``path`` was marked embedded, as stored (to the second)."""
    with db._get_connection() as conn:
        (stamp,) = conn.execute(
            "SELECT embedding_updated_at FROM media_hashes WHERE file_path = ?",
            (path,),
        ).fetchone()
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# The evidence a deletion records
# ---------------------------------------------------------------------------


def test_deletion_records_the_model_the_file_was_embedded_with(db):
    embedded = _graded_delete(db, "/p/embedded.png", embedded_with="clip-medium")
    never = _graded_delete(db, "/p/never.png")
    db.save_media(_media("/p/hashed.png"))
    db.save_media_hash(Path("/p/hashed.png"), "ffff0000ffff0000")  # pHash only
    hashed = _graded_delete(db, "/p/hashed.png")
    db.save_media(_media("/p/skipped.png"))
    db.mark_embedding_skipped(["/p/skipped.png"])  # unreadable for CLIP
    skipped = _graded_delete(db, "/p/skipped.png")
    db.save_media(_media("/p/cleared.png"))
    db.mark_embedded(["/p/cleared.png"], "clip-medium")
    db.clear_embeddings()  # "Rebuild all" (or a CLIP switch) before it got there
    cleared = _graded_delete(db, "/p/cleared.png")
    # A mark without a time (one v7 found from before the fix): no evidence.
    db.save_media(_media("/p/untimed.png"))
    db.mark_embedded(["/p/untimed.png"], "clip-medium")
    with db.lock, db._get_connection() as conn:
        conn.execute("UPDATE media_hashes SET embedding_updated_at = NULL")
        conn.commit()
    untimed = _graded_delete(db, "/p/untimed.png")

    evidence = {
        t: (_tombstone(db, t)["embedded_model"], _tombstone(db, t)["embedded_at"])
        for t in (embedded, never, hashed, skipped, cleared, untimed)
    }
    assert evidence[embedded][0] == "clip-medium"
    assert datetime.fromisoformat(evidence[embedded][1])  # the mark's time
    assert {t: e for t, e in evidence.items() if t != embedded} == {
        never: ("", None),
        hashed: ("", None),
        skipped: ("", None),
        cleared: ("", None),
        untimed: (None, None),
    }
    assert {t["path_rewritten"] for t in db.list_tombstones()} == {0}


def test_a_restored_snapshot_tombstone_has_no_evidence(db):
    """A full clean truncates the media rows (and their embedding records)
    before it tombstones what did not come back: nothing says whose row the
    index holds at such a path, so it is read by path only under the old
    guards, and not at all at restore time."""
    db.save_media(_media("/p/gone.png"))
    db.mark_embedded(["/p/gone.png"], "clip-medium")
    db.set_grade("/p/gone.png", "pass")
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    calls = []
    db.set_embedding_lookup(
        lambda p: calls.append(p)
        or (np.ones(32, dtype=np.float32), "clip-medium", LATER)
    )

    db.restore_taste_state(snapshot, tombstone_missing=True)

    [t] = db.list_tombstones()
    assert t["embedded_model"] is None and t["embedded_at"] is None
    assert t["path_rewritten"] == 0
    assert db.get_tombstone_vectors(t["id"]) == {} and calls == []
    assert _listed(db) == [t["id"]]


# ---------------------------------------------------------------------------
# At deletion: the in-memory index, with evidence
# ---------------------------------------------------------------------------


def test_delete_time_capture_needs_the_file_embedded_with_the_index_model(db):
    vec = np.arange(32, dtype=np.float32)
    calls = []
    db.set_embedding_lookup(lambda p: calls.append(p) or (vec, "clip-medium", LATER))

    never = _graded_delete(db, "/p/never.png")
    other = _graded_delete(db, "/p/other.png", embedded_with="clip-small")
    same = _graded_delete(db, "/p/same.png", embedded_with="clip-medium")

    # Never embedded: the index is not even read at its path.
    assert db.get_tombstone_vectors(never) == {}
    assert "/p/never.png" not in calls and to_native_path("/p/never.png") not in calls
    # Embedded with another model than the in-memory index's: whatever the
    # index holds at the path was written for another file.
    assert db.get_tombstone_vectors(other) == {}
    assert np.array_equal(_vector(db, same, "clip-medium"), vec)


def test_delete_time_capture_needs_an_index_loaded_after_the_mark(db):
    """The server's in-memory index is replaced only after an embedding run
    completes, so it can predate a file's mark (and with it the row the
    worker wrote for the file). Only an index loaded from the second after
    the mark on is read; a skipped capture keeps its evidence for the
    backfill on the next load."""
    vec = np.arange(32, dtype=np.float32)
    for name, offset, kept in (
        ("same_second", 0.9, False),
        ("next_second", 1.0, True),
        ("unknown", None, False),
    ):
        path = f"/p/{name}.png"
        db.save_media(_media(path))
        db.mark_embedded([path], "clip-medium")
        loaded = None if offset is None else _mark_epoch(db, path) + offset
        db.set_embedding_lookup(lambda p, at=loaded: (vec, "clip-medium", at))
        t = _graded_delete(db, path)
        assert (db.get_tombstone_vectors(t) != {}) is kept, name
        assert _tombstone(db, t)["embedded_model"] == "clip-medium"


def test_delete_time_vector_is_kept_per_model_and_never_overwritten(db):
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-small", LATER))
    t = _graded_delete(db, "/p/a.png", embedded_with="clip-small")
    db.set_embedding_lookup(None)
    (row,) = db.list_tombstones()
    # The legacy single-vector columns are no longer written.
    assert row["embedding"] is None and row["embedding_model"] is None
    other = np.zeros(32, dtype=np.float32).tobytes()
    assert db.add_tombstone_vectors([(t, "clip-small", other)]) == 0
    assert np.array_equal(_vector(db, t, "clip-small"), vec)
    assert db.add_tombstone_vectors([(t, "clip-large", other)]) == 1
    assert set(db.get_tombstone_vectors(t)) == {"clip-small", "clip-large"}


# ---------------------------------------------------------------------------
# The backfill: evidence
# ---------------------------------------------------------------------------


def test_backfill_with_evidence_fills_even_a_reused_or_shared_path(db):
    """Embedded with the index's model and nothing embedded at the path
    since: the row is still the deleted file's, even when a new file lives
    at the path now (not embedded yet) or the path had another life."""
    va = np.full(32, 1.0, dtype=np.float32)
    vb = np.full(32, 2.0, dtype=np.float32)
    reused = _graded_delete(db, "/p/reused.png", embedded_with="clip-medium")
    db.save_media(_media("/p/reused.png"))  # a new file, not embedded yet
    shared = _graded_delete(db, "/p/twice.png", embedded_with="clip-medium")
    later = _graded_delete(db, "/p/twice.png", "fail")  # never embedded
    fm = FakeFaiss({"/p/reused.png": va, "/p/twice.png": vb})

    assert _listed(db) == [reused, shared]
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 2

    assert np.array_equal(_vector(db, reused, "clip-medium"), va)
    assert np.array_equal(_vector(db, shared, "clip-medium"), vb)
    assert db.get_tombstone_vectors(later) == {}
    assert _listed(db) == []  # filled: not listed again


def test_backfill_with_evidence_fills_an_archived_tombstone(db):
    """With evidence the index's row is the deleted image's own vector, from
    the full image; the re-embedder then has nothing left to do for it."""
    thumb = "archive/thumbs/0123456789abcdef0123456789abcdef.jpg"
    t = _graded_delete(db, "/p/kept.png", embedded_with="clip-medium", thumb=thumb)
    assert db.tombstones_needing_vectors("clip-medium") == [(t, thumb)]
    fm = FakeFaiss({"/p/kept.png": np.ones(32, dtype=np.float32)})

    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 1

    assert db.tombstones_needing_vectors("clip-medium") == []
    assert db.tombstones_needing_vectors("clip-large") == [(t, thumb)]


def test_backfill_with_evidence_needs_an_index_loaded_after_the_mark(db):
    """The evidence is about the index on disk once the file was marked;
    an index loaded before that (in the mark's second or earlier) may hold
    another file's row at the path. Tombstones without evidence do not
    depend on the load time."""
    evidence = _graded_delete(db, "/p/a.png", embedded_with="clip-medium")
    mark = _tombstone(db, evidence)["embedded_at"]
    marked = datetime.fromisoformat(mark).replace(tzinfo=timezone.utc).timestamp()
    legacy = _graded_delete(db, "/p/b.png")
    _make_legacy(db, legacy)

    assert _listed(db, loaded_at=marked + 0.5) == [legacy]
    assert _listed(db, loaded_at=None) == [legacy]
    assert _listed(db, loaded_at=marked + 1) == [evidence, legacy]
    early = FakeFaiss(
        {"/p/a.png": np.ones(32, dtype=np.float32)}, loaded_at=marked + 0.5
    )
    assert judgments_service.backfill_tombstone_embeddings(db, early) == 0
    assert db.get_tombstone_vectors(evidence) == {}
    assert "/p/a.png" not in early.calls


def test_backfill_never_reads_the_path_of_a_file_never_embedded(db):
    """``embedded_model = ''``: whatever the index holds at the path, it
    was not written for this file -- even when nothing else ever lived
    there (the old guards would have let it through)."""
    t = _graded_delete(db, "/p/never.png")
    fm = FakeFaiss({"/p/never.png": np.ones(32, dtype=np.float32)})

    assert _listed(db) == []
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0
    assert db.get_tombstone_vectors(t) == {} and fm.calls == []


def test_backfill_never_borrows_across_models(db):
    """An image embedded and deleted under the "small" index (its vector
    captured then): a "medium" index built afterwards holds at its path
    only another file's vector, if any."""
    small = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (small, "clip-small", LATER))
    t = _graded_delete(db, "/p/switched.png", embedded_with="clip-small")
    db.set_embedding_lookup(None)
    fm = FakeFaiss({"/p/switched.png": np.full(32, 9.0, dtype=np.float32)})

    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0

    assert db.get_tombstone_vectors(t) == {"clip-small": small.tobytes()}
    assert fm.calls == []


def test_backfill_never_reads_a_path_embedded_since_the_delete(db):
    """Once the worker appended or overwrote the path's row, nothing tells
    whose vector it holds: stamped tombstones -- with evidence or legacy --
    are never read by path again."""
    evidence = _graded_delete(db, "/p/a.png", embedded_with="clip-medium")
    legacy = _graded_delete(db, "/p/b.png")
    _make_legacy(db, legacy)
    assert _listed(db) == [evidence, legacy]

    assert db.mark_tombstone_paths_rewritten(["/p/a.png", "/p/b.png"]) == 2

    assert _listed(db) == []
    fm = FakeFaiss({k: np.ones(32, dtype=np.float32) for k in ("/p/a.png", "/p/b.png")})
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0
    assert fm.calls == []


def test_the_stamp_reaches_every_tombstone_at_each_posix_path(db):
    native = r"C:\lib\a.png"  # how the worker names a Windows file
    first = _graded_delete(db, "/mnt/c/lib/a.png", embedded_with="clip-medium")
    second = _graded_delete(db, "/mnt/c/lib/a.png", "fail")
    other = _graded_delete(db, "/mnt/c/lib/b.png", embedded_with="clip-medium")

    assert db.mark_tombstone_paths_rewritten([native, native]) == 2
    assert db.mark_tombstone_paths_rewritten([native]) == 0  # already stamped
    assert db.mark_tombstone_paths_rewritten([]) == 0

    assert {t: _tombstone(db, t)["path_rewritten"] for t in (first, second)} == {
        first: 1,
        second: 1,
    }
    assert _tombstone(db, other)["path_rewritten"] == 0


# ---------------------------------------------------------------------------
# The backfill: tombstones written before the evidence (the old guards)
# ---------------------------------------------------------------------------


def test_legacy_backfill_lists_only_tombstones_without_any_vector(db):
    a = _graded_delete(db, "/p/a.png")
    b = _graded_delete(db, "/p/b.png", "fail")
    _make_legacy(db, a, b)
    assert db.tombstones_for_vector_backfill("clip-medium", LATER) == [
        (a, "/p/a.png"),
        (b, "/p/b.png"),
    ]
    vec = np.ones(32, dtype=np.float32)
    assert db.add_tombstone_vectors([(a, "clip-x", vec.tobytes())]) == 1
    # A vector for any CLIP model takes it off the list, whatever the index's
    # model is: it was deleted before an index of another model was built.
    assert db.tombstones_for_vector_backfill("clip-medium", LATER) == [(b, "/p/b.png")]


def test_legacy_backfill_fills_missing_vectors_from_native_or_posix_key(db):
    have = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (have, "clip-medium", LATER))
    already = _graded_delete(db, "/p/already.png", embedded_with="clip-medium")
    db.set_embedding_lookup(lambda p: (have, "clip-small", LATER))
    small_only = _graded_delete(db, "/p/small_only.png", embedded_with="clip-small")
    db.set_embedding_lookup(None)
    a = _graded_delete(db, "/p/a.png")
    b = _graded_delete(db, "/p/b.png", "fail")
    nothere = _graded_delete(db, "/p/nothere.png")
    _make_legacy(db, already, small_only, a, b, nothere)

    va = np.full(32, 1.0, dtype=np.float32)
    vb = np.full(32, 2.0, dtype=np.float32)
    vs = np.full(32, 3.0, dtype=np.float32)
    fm = FakeFaiss(
        {
            to_native_path("/p/a.png"): va,  # native key
            "/p/b.png": vb,  # posix key
            "/p/small_only.png": vs,
            "/p/already.png": np.zeros(32, dtype=np.float32),
        }
    )

    filled = judgments_service.backfill_tombstone_embeddings(db, fm)

    assert filled == 2
    assert np.array_equal(_vector(db, a, "clip-medium"), va)
    assert np.array_equal(_vector(db, b, "clip-medium"), vb)
    assert db.get_tombstone_vectors(nothere) == {}
    # A tombstone with a vector -- for this model or another -- is not even
    # looked up: the "medium" index's vector at its path is another file's.
    assert np.array_equal(_vector(db, already, "clip-medium"), have)
    assert set(db.get_tombstone_vectors(small_only)) == {"clip-small"}
    assert np.array_equal(_vector(db, small_only, "clip-small"), have)
    for key in ("/p/already.png", "/p/small_only.png"):
        assert key not in fm.calls and to_native_path(key) not in fm.calls


def test_legacy_backfill_leaves_archived_tombstones_to_the_reembedder(db):
    """A deleted graded image with an archived thumbnail and no evidence
    gets its vector from its own image (the re-embedder), never by its old
    path."""
    from metascan.core.training_archive import archive_dir

    root = archive_dir(Path(db.db_path))
    root.mkdir(parents=True, exist_ok=True)
    (root / "0123456789abcdef0123456789abcdef.jpg").write_bytes(b"jpeg")
    thumb = "archive/thumbs/0123456789abcdef0123456789abcdef.jpg"
    t = _graded_delete(db, "/p/kept.png", thumb=thumb)
    _make_legacy(db, t)
    fm = FakeFaiss({"/p/kept.png": np.ones(32, dtype=np.float32)})

    assert db.tombstones_for_vector_backfill("clip-medium", LATER) == []
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0
    assert db.get_tombstone_vectors(t) == {} and fm.calls == []
    assert db.tombstones_needing_vectors("clip-medium") == [(t, thumb)]


def test_legacy_backfill_skips_a_reused_path(db):
    """Without evidence, a path that held another file after the deletion
    may give the tombstone that file's vector: such tombstones get none by
    path (the re-embedder computes theirs from the archived thumbnail)."""
    # A ✓ image deleted while the index was not in memory; a new file lives
    # at its path now.
    reused = _graded_delete(db, "/p/reused.png")
    db.save_media(_media("/p/reused.png"))
    # A ✗ image whose path was reused by a file that was deleted as well:
    # two lives of one path, and nothing says whose vector the index holds.
    twice_old = _graded_delete(db, "/p/twice.png", "fail")
    twice_new = _graded_delete(db, "/p/twice.png", "pass")
    # An ordinary deletion: one life, nothing at the path now.
    plain = _graded_delete(db, "/p/plain.png")
    _make_legacy(db, reused, twice_old, twice_new, plain)
    fm = FakeFaiss(
        {
            "/p/reused.png": np.full(32, 1.0, dtype=np.float32),
            "/p/twice.png": np.full(32, 2.0, dtype=np.float32),
            "/p/plain.png": np.full(32, 3.0, dtype=np.float32),
        }
    )

    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 1

    assert np.array_equal(
        _vector(db, plain, "clip-medium"), np.full(32, 3.0, dtype=np.float32)
    )
    for t in (reused, twice_old, twice_new):
        assert db.get_tombstone_vectors(t) == {}
    assert _listed(db) == []
    # The skipped paths are not even read from the index.
    assert all(k not in fm.calls for k in ("/p/reused.png", "/p/twice.png"))
    assert all(
        to_native_path(k) not in fm.calls for k in ("/p/reused.png", "/p/twice.png")
    )


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def test_backfill_noop_when_index_not_loaded_or_no_model_key(db):
    t = _graded_delete(db, "/p/a.png", embedded_with="clip-medium")
    v = {"/p/a.png": np.ones(32, dtype=np.float32)}
    assert (
        judgments_service.backfill_tombstone_embeddings(db, FakeFaiss(v, loaded=False))
        == 0
    )
    assert (
        judgments_service.backfill_tombstone_embeddings(
            db, FakeFaiss(v, model_key=None)
        )
        == 0
    )
    assert db.get_tombstone_vectors(t) == {}


def test_backfill_reads_faiss_outside_the_db_lock(db):
    _graded_delete(db, "/p/a.png", embedded_with="clip-medium")

    class LockCheckingFaiss(FakeFaiss):
        def get_embedding(self, key):
            assert not db.lock.locked(), "FAISS read under the DB lock"
            return super().get_embedding(key)

    fm = LockCheckingFaiss({"/p/a.png": np.ones(32, dtype=np.float32)})
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 1


def test_loading_the_faiss_manager_triggers_backfill(monkeypatch, tmp_path):
    from backend.api import similarity

    started = []

    class FakeManager:
        def __init__(self, index_dir):
            self.is_loaded = False

        def load(self):
            self.is_loaded = True
            return True

    class FakeQueue:
        index_dir = tmp_path

    monkeypatch.setattr(similarity, "_faiss_manager", None)
    monkeypatch.setattr(similarity, "FaissIndexManager", FakeManager)
    monkeypatch.setattr(similarity, "_get_embedding_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        similarity, "_start_tombstone_backfill", lambda fm: started.append(fm)
    )

    fm = similarity._get_faiss_manager()
    assert started == [fm] and fm.is_loaded
    similarity._get_faiss_manager()  # cached: no second backfill
    assert started == [fm]
