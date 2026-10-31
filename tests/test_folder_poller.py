import os
from pathlib import Path

import pytest

from metascan.core import folder_poller
from metascan.core.folder_poller import FolderPoller, FolderSpec


def _probe(folder):
    if not os.path.isdir(folder):
        return "missing"
    return "present" if os.listdir(folder) else "empty"


def _is_media(path):
    return path.lower().endswith((".png", ".jpg", ".mp4"))


def _poller(tmp_path, skip=(), sub=True, folders=None):
    specs = folders or [FolderSpec(str(tmp_path), sub)]
    return FolderPoller(specs, _probe, list(skip), _is_media)


def _write(path: Path, data=b"x" * 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def test_new_file_settles_after_two_equal_polls(tmp_path):
    f = _write(tmp_path / "a.png")
    p = _poller(tmp_path)
    assert p.poll().settled_new == []
    assert p.poll().settled_new == [f]
    assert p.poll().settled_new == []


def test_growing_file_does_not_settle(tmp_path):
    f = _write(tmp_path / "a.png")
    p = _poller(tmp_path)
    p.poll()
    _write(tmp_path / "a.png", b"x" * 20)
    assert p.poll().settled_new == []
    assert p.poll().settled_new == [f]


def test_zero_byte_file_does_not_settle(tmp_path):
    f = _write(tmp_path / "a.png", b"")
    p = _poller(tmp_path)
    for _ in range(3):
        assert p.poll().settled_new == []
    _write(tmp_path / "a.png")
    p.poll()
    assert p.poll().settled_new == [f]


def test_unopenable_file_does_not_settle(tmp_path, monkeypatch):
    f = _write(tmp_path / "a.png")
    monkeypatch.setattr(folder_poller, "can_open_for_read", lambda path: False)
    p = _poller(tmp_path)
    p.poll()
    assert p.poll().settled_new == []
    monkeypatch.setattr(folder_poller, "can_open_for_read", lambda path: True)
    assert p.poll().settled_new == [f]


def test_ignored_and_partial_names_skipped(tmp_path):
    _write(tmp_path / ".DS_Store")
    _write(tmp_path / "._a.png")
    _write(tmp_path / "b.png.part")
    _write(tmp_path / "c.png.crdownload")
    _write(tmp_path / "notes.txt")
    good = _write(tmp_path / "d.png")
    p = _poller(tmp_path)
    p.poll()
    assert p.poll().settled_new == [good]


def test_paths_under_skip_roots_skipped(tmp_path):
    _write(tmp_path / "lib" / "a.png")
    other = _write(tmp_path / "library" / "b.png")
    p = _poller(tmp_path, skip=[str(tmp_path / "lib")])
    p.poll()
    assert p.poll().settled_new == [other]


def test_seed_reports_unknown_disk_files_as_new(tmp_path):
    known = _write(tmp_path / "known.png")
    new = _write(tmp_path / "new.png")
    gone = str(tmp_path / "gone.png")
    p = _poller(tmp_path)
    p.seed([known, gone])
    first = p.poll()
    assert first.settled_new == [] and first.changed == []
    assert first.vanished == [gone]
    second = p.poll()
    assert second.settled_new == [new]
    assert second.changed == []  # known file got its stamp on pass one
    _write(tmp_path / "known.png", b"y" * 30)
    assert p.poll().changed == [known]


def test_missing_folder_reports_unavailable_and_nothing_vanishes(tmp_path):
    folder = tmp_path / "drive"
    f = _write(folder / "a.png")
    p = _poller(tmp_path, folders=[FolderSpec(str(folder), True)])
    p.seed([f])
    p.poll()
    os.remove(f)
    os.rmdir(folder)
    r = p.poll()
    assert r.unavailable == [str(folder)] and r.vanished == []
    _write(folder / "a.png")
    r = p.poll()
    assert r.unavailable == [] and r.vanished == [] and r.settled_new == []


def test_folder_empty_with_known_files_reports_unavailable(tmp_path):
    folder = tmp_path / "mount"
    f = _write(folder / "a.png")
    p = _poller(tmp_path, folders=[FolderSpec(str(folder), True)])
    p.seed([f])
    p.poll()
    os.remove(f)
    r = p.poll()
    assert r.unavailable == [str(folder)] and r.vanished == []


def test_empty_folder_with_nothing_known_is_available(tmp_path):
    r = _poller(tmp_path).poll()
    assert r.unavailable == []


def test_listing_error_mid_walk_reports_unavailable(tmp_path, monkeypatch):
    f = _write(tmp_path / "a.png")
    _write(tmp_path / "sub" / "b.png")
    p = _poller(tmp_path)
    p.seed([f])
    real = os.scandir

    def bad(path):
        if os.path.basename(str(path)) == "sub":
            raise PermissionError(path)
        return real(path)

    monkeypatch.setattr(folder_poller.os, "scandir", bad)
    r = p.poll()
    assert r.unavailable == [str(tmp_path)]
    assert r.vanished == [] and r.settled_new == []


def test_vanished_while_settling_is_dropped_silently(tmp_path):
    f = _write(tmp_path / "a.png")
    p = _poller(tmp_path)
    p.poll()
    os.remove(f)
    _write(tmp_path / "other.png")
    r = p.poll()
    assert r.vanished == [] and r.settled_new == []


def test_interval_scales_with_a_slow_walk():
    p = FolderPoller([], _probe, [], _is_media)
    assert p.next_interval() == folder_poller.WATCH_INTERVAL_S
    p._last_walk_s = 2.0
    assert p.next_interval() == 40.0


def test_search_subfolders_false_stays_shallow(tmp_path):
    top = _write(tmp_path / "a.png")
    _write(tmp_path / "sub" / "b.png")
    p = _poller(tmp_path, sub=False)
    p.poll()
    assert p.poll().settled_new == [top]


def test_directory_symlink_not_followed(tmp_path):
    _write(tmp_path / "real" / "a.png")
    os.symlink(tmp_path, tmp_path / "real" / "loop")
    p = _poller(tmp_path)
    p.poll()
    assert len(p.poll().settled_new) == 1
