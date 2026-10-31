"""The always-on import: polling, ingest, moves, state (spec 9, 11).

Real files in tmp_path and a real temp database; the Scanner is the real one.
Passes are driven by hand (``run_pass``): the first sees a new file, the second
settles it, the third (after its one pass beside the vanished list) ingests."""

import asyncio
import os
import random
from pathlib import Path

import pytest
from PIL import Image

from backend import dependencies
from backend.api import scan as scan_api
from backend.services import taste_snapshot as ts
from backend.services.background.import_process import ImportProcess
from backend.services.background.protocol import State
from backend.services.background.registry import ProcessRegistry
from metascan.core import folder_poller as fp
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.utils.fs_compat import path_key
from tests.test_background_services import Stub


class Rig:
    def __init__(self, tmp_path, monkeypatch):
        self.lib = tmp_path / "lib"
        self.lib.mkdir()
        self.db = DatabaseManager(tmp_path / "db")
        self.config = {
            "watch_directories": True,
            "directories": [{"filepath": str(self.lib), "search_subfolders": True}],
        }
        self.events = []
        self.kicks = 0
        self.moves = 0
        self.scan = {"running": False}
        self.registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
        for pid in ("embedding", "tagging"):
            self.registry.register(Stub(pid))
        self.proc = self.make()
        self.registry.register(self.proc)
        dependencies.set_registry(self.registry)

    def status(self):
        base = dict(scan_api.scan_status())
        base.update(self.scan)
        return base

    def make(self, **kw):
        async def kick():
            self.kicks += 1

        async def after_move():
            self.moves += 1

        scanner = kw.pop("scanner", None) or Scanner(self.db, None)
        return ImportProcess(
            self.registry,
            self.status,
            lambda: False,
            get_db=lambda: self.db,
            get_config=lambda: self.config,
            make_scanner=lambda: scanner,
            probe=scan_api._probe_folder,
            output_roots=lambda c: [],
            broadcast=lambda ch, ev, data: self.events.append((ch, ev, data)),
            kick_embedding=kick,
            after_move=after_move,
            announce_interval_s=0.0,
            embed_debounce_s=0.05,
            **kw,
        )

    def image(self, name, seed, folder=None):
        rnd = random.Random(seed)
        size = 8 + seed  # distinct sizes on disk
        img = Image.new("RGB", (size, size))
        img.putdata([(rnd.randrange(256),) * 3 for _ in range(size * size)])
        path = (folder or self.lib) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        return path

    def rows(self):
        return set(self.db.all_media_paths())

    async def passes(self, n=3, proc=None):
        for _ in range(n):
            await (proc or self.proc).run_pass()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    yield r
    dependencies.set_registry(None)


def run(coro):
    return asyncio.run(coro)


def test_dropped_file_ingests_and_broadcasts(rig):
    async def go():
        await rig.passes(1)
        pic = rig.image("a.png", 1)
        await rig.passes(3)
        return pic

    pic = run(go())
    assert pic.as_posix() in rig.rows()
    assert ("library", "media_added", {"count": 1}) in rig.events
    assert rig.db.candidate_for(pic.as_posix()).phash


def test_embedding_is_kicked_once_after_the_last_ingest(rig):
    async def go():
        rig.image("a.png", 1)
        rig.image("b.png", 2)
        await rig.passes(3)
        assert rig.kicks == 0  # debounced
        await asyncio.sleep(0.3)

    run(go())
    assert rig.kicks == 1


def test_library_events_are_throttled(rig):
    rig.proc._announce_interval_s = 3600.0

    async def go():
        rig.image("a.png", 1)
        rig.image("b.png", 2)
        await rig.passes(3)

    run(go())
    added = [e for e in rig.events if e[1] == "media_added"]
    assert added == [("library", "media_added", {"count": 1})]  # the rest waits


def test_a_moved_file_keeps_its_row_and_rating(rig):
    async def go():
        pic = rig.image("a.png", 1)
        await rig.passes(3)
        old = pic.as_posix()
        rig.db.set_grade(old, "pass")
        dest = rig.lib / "sub" / "b.png"
        dest.parent.mkdir()
        os.rename(pic, dest)
        await rig.passes(3)
        return old, dest.as_posix()

    old, new = run(go())
    assert rig.rows() == {new}
    assert rig.db.get_judgment(new).grade == "pass"
    assert rig.moves == 1 and rig.db.candidate_for(old) is None


def test_folder_rename_moves_every_file_in_one_batch(rig, monkeypatch):
    batches = []
    real = rig.db.move_media_batch_applied

    def spy(pairs):
        batches.append(list(pairs))
        return real(pairs)

    monkeypatch.setattr(rig.db, "move_media_batch_applied", spy)

    async def go():
        for i in range(5):
            rig.image(f"p{i}.png", i + 1, rig.lib / "old")
        await rig.passes(3)
        os.rename(rig.lib / "old", rig.lib / "new")
        await rig.passes(3)

    run(go())
    assert len(batches) == 1 and len(batches[0]) == 5
    assert rig.rows() == {(rig.lib / "new" / f"p{i}.png").as_posix() for i in range(5)}


def test_a_move_re_keys_the_pending_snapshot_with_the_applied_pairs_only(
    rig, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        ts,
        "rekey_pending",
        lambda data_dir, pairs: seen.append((data_dir, list(pairs))),
    )

    async def go():
        pic = rig.image("a.png", 1)
        await rig.passes(3)
        os.rename(pic, rig.lib / "b.png")
        await rig.passes(3)
        return pic

    pic = run(go())
    assert seen == [
        (Path(rig.db.db_path), [(pic.as_posix(), (rig.lib / "b.png").as_posix())])
    ]


def test_an_ambiguous_vanish_is_a_new_file_not_a_move(rig):
    async def go():
        a = rig.image("a.png", 1)
        b = rig.lib / "b.png"
        b.write_bytes(a.read_bytes())  # same size, same content
        os.utime(b, (a.stat().st_mtime, a.stat().st_mtime))
        rig.image("keep.png", 2)
        await rig.passes(3)
        os.remove(a)
        os.remove(b)
        # Another file of the same size and mtime: two candidates.
        c = rig.lib / "c.png"
        c.write_bytes(a_bytes)
        os.utime(c, (a_mtime, a_mtime))
        await rig.passes(4)
        return a, b, c

    # remember what a looked like before it is removed
    a0 = rig.image("tmp_probe.png", 1)
    a_bytes, a_mtime = a0.read_bytes(), a0.stat().st_mtime
    os.remove(a0)
    a, b, c = run(go())
    assert c.as_posix() in rig.rows()
    assert rig.moves == 0
    assert {a.as_posix(), b.as_posix()} <= rig.rows()  # left for hygiene


def test_vanished_row_is_not_cleaned_by_import(rig):
    async def go():
        pic = rig.image("a.png", 1)
        rig.image("keep.png", 2)  # an emptied folder reads as an unmounted drive
        await rig.passes(3)
        os.remove(pic)
        await rig.passes(3)
        return pic

    pic = run(go())
    assert pic.as_posix() in rig.rows()  # hygiene's job, after the grace
    assert rig.proc.vanished_since(pic.as_posix()) is not None
    assert rig.proc.vanished_since("/never/seen.png") is None


def test_case_only_rename_updates_the_stored_spelling(rig, monkeypatch):
    monkeypatch.setattr(fp, "path_key", lambda p: path_key(p, "win32"))

    async def go():
        pic = rig.image("Pic.png", 1)
        await rig.passes(3)
        os.rename(pic, rig.lib / "pic.png")
        await rig.passes(2)
        return pic

    pic = run(go())
    assert rig.rows() == {(rig.lib / "pic.png").as_posix()}
    assert pic.as_posix() not in rig.rows()


def test_paused_import_catches_up_on_resume(rig):
    async def go():
        await rig.passes(1)
        await rig.registry.command("import", "pause")
        assert rig.registry.status("import").state == State.PAUSED
        pic = rig.image("a.png", 1)
        await rig.passes(4)
        assert rig.rows() == set()
        await rig.registry.command("import", "resume")
        await rig.passes(3)
        return pic

    pic = run(go())
    assert rig.rows() == {pic.as_posix()}


def test_stop_halts_and_start_catches_up(rig):
    async def go():
        await rig.passes(1)
        await rig.registry.command("import", "stop")
        rig.image("a.png", 1)
        await rig.passes(4)
        assert rig.rows() == set()
        assert rig.registry.status("import").capabilities.can_start
        await rig.registry.command("import", "start")
        await rig.passes(3)

    run(go())
    assert len(rig.rows()) == 1


def test_restart_catches_up(rig):
    async def go():
        await rig.passes(3)
        rig.image("a.png", 1)  # dropped while the server was off
        fresh = rig.make()
        await rig.passes(3, fresh)

    run(go())
    assert len(rig.rows()) == 1


def test_a_known_file_is_not_ingested_again_after_a_restart(rig):
    class Counting:
        n = 0

        def __init__(self, inner):
            self.inner = inner

        def ingest_file(self, p):
            Counting.n += 1
            return self.inner.ingest_file(p)

    async def go():
        rig.image("a.png", 1)
        await rig.passes(3)
        fresh = rig.make(scanner=Counting(Scanner(rig.db, None)))
        await rig.passes(4, fresh)

    run(go())
    assert Counting.n == 0


def test_one_bad_file_counts_a_warning_and_import_stays_running(rig):
    class Picky:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def ingest_file(self, p):
            self.calls.append(p.name)
            if p.name == "bad.png":
                raise ValueError("/secret/path is broken")
            return self.inner.ingest_file(p)

    picky = Picky(Scanner(rig.db, None))
    proc = rig.make(scanner=picky)
    rig.registry._procs.pop("import")
    rig.registry.register(proc)

    async def go():
        rig.image("good.png", 1)
        (rig.lib / "bad.png").write_bytes(b"not an image")
        await rig.passes(3, proc)
        await rig.passes(2, proc)

    run(go())
    st = rig.registry.status("import")
    assert picky.calls.count("bad.png") == 2  # retried once, then left
    assert st.warnings_count == 1 and st.state != State.FAILED
    assert (rig.lib / "good.png").as_posix() in rig.rows()
    assert "secret" not in (st.activity + (st.error or ""))
    # left alone until it changes
    assert picky.calls.count("bad.png") == 2


def test_unavailable_folder_is_waiting_and_touches_nothing(rig, tmp_path):
    gone = tmp_path / "unplugged_drive"
    rig.config["directories"].append({"filepath": str(gone), "search_subfolders": True})
    ghost = (gone / "x.png").as_posix()
    rig.db.save_media(
        __import__("tests.test_taste_snapshot_scan", fromlist=["_media"])._media(ghost)
    )

    async def go():
        await rig.passes(4)

    run(go())
    st = rig.registry.status("import")
    assert st.state == State.WAITING
    assert "unplugged_drive" in st.activity and str(tmp_path) not in st.activity
    assert ghost in rig.rows()
    assert rig.proc.vanished_since(ghost) is None


def test_watch_directories_false_stays_idle(rig):
    rig.config["watch_directories"] = False

    async def go():
        rig.image("a.png", 1)
        await rig.passes(4)

    run(go())
    st = rig.registry.status("import")
    assert st.state == State.IDLE and st.activity == "Watching is off"
    assert rig.rows() == set()
    assert (st.capabilities.can_pause, st.capabilities.can_stop) == (False, False)


def test_polling_alone_is_idle_and_ingest_is_running(rig):
    async def go():
        await rig.passes(2)
        idle = rig.registry.status("import")
        rig.image("a.png", 1)
        await rig.passes(1)
        working = rig.registry.status("import")  # settling
        await rig.passes(2)
        return idle, working

    idle, working = run(go())
    assert idle.state == State.IDLE and idle.activity == "Watching 1 folder"
    assert (idle.capabilities.can_pause, idle.capabilities.can_stop) == (True, True)
    assert working.state == State.RUNNING


def test_ingest_progress_is_done_of_total_with_a_bare_file_name(rig):
    seen = []

    class Slow:
        def __init__(self, inner):
            self.inner = inner

        def ingest_file(self, p):
            seen.append(rig.registry.status("import"))
            return self.inner.ingest_file(p)

    proc = rig.make(scanner=Slow(Scanner(rig.db, None)))
    rig.registry._procs.pop("import")
    rig.registry.register(proc)

    async def go():
        for i in range(3):
            rig.image(f"p{i}.png", i + 1)
        await rig.passes(3, proc)

    run(go())
    first = seen[0]
    assert first.state == State.RUNNING
    assert (first.progress.done, first.progress.total) == (0, 3)
    assert (
        first.activity.startswith("Importing p") and str(rig.lib) not in first.activity
    )
    assert [s.progress.done for s in seen] == [0, 1, 2]


def test_a_running_scan_suspends_the_watcher_and_it_reseeds_after(rig):
    async def go():
        await rig.passes(1)
        rig.scan["running"] = True
        pic = rig.image("a.png", 1)
        await rig.passes(4)
        assert rig.rows() == set()
        rig.scan["running"] = False
        rig.proc.poke()  # the scan route's poke on its end
        assert rig.proc._resync
        await rig.passes(3)
        return pic

    pic = run(go())
    assert rig.rows() == {pic.as_posix()}


def test_pause_is_not_offered_during_a_full_clean(rig):
    async def go():
        await rig.passes(1)

    run(go())
    rig.scan.update(running=True, full_clean=True)
    caps = rig.proc.capabilities()
    assert (caps.can_pause, caps.can_stop, caps.confirm_stop) == (False, True, True)
    rig.scan.update(full_clean=False)
    assert rig.proc.capabilities().can_pause is True


def test_a_new_folder_in_config_is_seen_without_a_restart(rig, tmp_path):
    other = tmp_path / "other"
    other.mkdir()

    async def go():
        await rig.passes(1)
        rig.image("a.png", 1, other)
        rig.config["directories"].append(
            {"filepath": str(other), "search_subfolders": True}
        )
        await rig.passes(4)

    run(go())
    assert (other / "a.png").as_posix() in rig.rows()


def test_the_loop_runs_and_stops_with_aclose(rig):
    async def go():
        rig.proc.start_loop()
        rig.image("a.png", 1)
        for _ in range(100):
            if rig.rows():
                break
            await asyncio.sleep(0.1)
        await rig.proc.aclose()

    # The interval is 5 s: poke the loop by hand instead of waiting.
    async def driver():
        rig.proc.start_loop()
        rig.image("a.png", 1)
        for _ in range(3):
            await asyncio.sleep(0.2)
            rig.proc._wake.set()
        await asyncio.sleep(0.5)
        await rig.proc.aclose()

    run(driver())
    assert len(rig.rows()) == 1
    assert rig.proc._task is None
