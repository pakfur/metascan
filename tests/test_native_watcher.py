"""The native file watcher: hints only, and never trusted to be alive."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from metascan.core.folder_poller import FolderSpec
from metascan.core.native_watcher import NativeWatcher, _Handler


class FakeEmitter:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class FakeObserver:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.scheduled = {}
        self.emitters = []
        self.started = self.stopped = False

    def schedule(self, handler, path, recursive=False):
        if path in self.fail_on:
            raise OSError(28, "inotify watch limit reached")
        self.scheduled[path] = recursive
        self.handler = handler
        self.emitters.append(FakeEmitter())
        return path

    def unschedule(self, watch):
        del self.scheduled[watch]

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.started and not self.stopped


def always(_path):
    return True


def rig(reliable=always, fail_on=()):
    observers = []
    hints = []

    def make():
        observers.append(FakeObserver(fail_on))
        return observers[-1]

    return NativeWatcher(hints.append, reliable, make), observers, hints


def test_a_folder_is_armed_recursively_as_configured():
    w, obs, _ = rig()
    deep, shallow = FolderSpec("/a", True), FolderSpec("/b", False)
    assert w.sync([deep, shallow]) == [deep, shallow]
    assert obs[0].started
    assert obs[0].scheduled == {"/a": True, "/b": False}


def test_a_mount_that_stays_silent_is_never_armed():
    """inotify accepts a watch on a WSL 9p mount and then delivers nothing."""
    w, obs, _ = rig(reliable=lambda p: p != "/mnt/d")
    good = FolderSpec("/home", True)
    assert w.sync([FolderSpec("/mnt/d", True), good]) == [good]
    assert list(obs[0].scheduled) == ["/home"]


def test_no_reliable_folder_starts_no_observer():
    w, obs, _ = rig(reliable=lambda p: False)
    assert w.sync([FolderSpec("/mnt/d", True)]) == []
    assert obs == []


def test_a_folder_that_refuses_a_watch_is_left_to_the_poller():
    w, _, _ = rig(fail_on=["/bad"])
    good = FolderSpec("/good", True)
    assert w.sync([FolderSpec("/bad", True), good]) == [good]


def test_an_observer_that_will_not_start_leaves_everything_to_the_poller():
    def make():
        raise RuntimeError("no backend")

    w = NativeWatcher(lambda p: None, always, make)
    assert w.sync([FolderSpec("/a", True)]) == []


def test_a_missing_watchdog_leaves_everything_to_the_poller(monkeypatch):
    import builtins

    real = builtins.__import__

    def block(name, *a, **k):
        if name.startswith("watchdog"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block)
    w = NativeWatcher(lambda p: None, always)
    assert w.sync([FolderSpec("/a", True)]) == []


def test_sync_is_idempotent():
    w, obs, _ = rig()
    spec = FolderSpec("/a", True)
    w.sync([spec])
    w.sync([spec])
    assert len(obs) == 1


def test_a_folder_gone_from_the_list_is_disarmed():
    w, obs, _ = rig()
    a, b = FolderSpec("/a", True), FolderSpec("/b", True)
    w.sync([a, b])
    assert w.sync([b]) == [b]
    assert list(obs[0].scheduled) == ["/b"]


def test_a_dead_emitter_is_rebuilt():
    """A drive pulled out ends the emitter's thread and tells nobody."""
    w, obs, _ = rig()
    spec = FolderSpec("/a", True)
    w.sync([spec])
    obs[0].emitters[0].alive = False
    assert w.sync([spec]) == [spec]
    assert len(obs) == 2
    assert obs[0].stopped and obs[1].started


def test_a_dead_observer_is_rebuilt():
    w, obs, _ = rig()
    spec = FolderSpec("/a", True)
    w.sync([spec])
    obs[0].stopped = True
    w.sync([spec])
    assert len(obs) == 2


def test_close_stops_it_and_may_be_called_twice():
    w, obs, _ = rig()
    w.sync([FolderSpec("/a", True)])
    w.close()
    w.close()
    assert obs[0].stopped
    assert w.armed == ()


def _event(kind, src, dest="", is_dir=False):
    return SimpleNamespace(
        event_type=kind, src_path=src, dest_path=dest, is_directory=is_dir
    )


def test_every_change_is_a_hint():
    hints = []
    h = _Handler(hints.append)
    for kind in ("created", "modified", "deleted"):
        h.dispatch(_event(kind, "/a/x.png"))
    assert hints == ["/a/x.png"] * 3


def test_a_move_hints_both_sides():
    hints = []
    _Handler(hints.append).dispatch(_event("moved", "/a/x.png", "/a/y.png"))
    assert hints == ["/a/x.png", "/a/y.png"]


def test_a_folder_modified_by_its_children_is_no_hint():
    """It would walk the whole folder once per file dropped into it."""
    hints = []
    _Handler(hints.append).dispatch(_event("modified", "/a", is_dir=True))
    assert hints == []


def test_opening_and_reading_a_file_is_no_hint():
    hints = []
    h = _Handler(hints.append)
    h.dispatch(_event("opened", "/a/x.png"))
    h.dispatch(_event("closed_no_write", "/a/x.png"))
    assert hints == []


def test_a_consumer_that_raises_does_not_kill_the_observer_thread():
    def boom(_path):
        raise ValueError("no")

    _Handler(boom).dispatch(_event("created", "/a/x.png"))


def test_a_real_observer_sees_a_real_file(tmp_path):
    seen = threading.Event()
    target = tmp_path / "sub" / "a.png"
    target.parent.mkdir()

    def hint(path):
        if os.path.basename(path) == "a.png":
            seen.set()

    w = NativeWatcher(hint, always)
    try:
        assert w.sync([FolderSpec(str(tmp_path), True)])
        time.sleep(0.2)
        target.write_bytes(b"x")
        assert seen.wait(5)
    finally:
        w.close()
