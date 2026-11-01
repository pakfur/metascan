"""replace_file retries only the Windows reader race."""

import os
import threading
import time

import pytest

from metascan.utils import replace_file as rf


def _flaky(monkeypatch, failures):
    real = os.replace
    calls = []

    def fake(src, dst):
        calls.append((src, dst))
        if len(calls) <= failures:
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(rf.os, "replace", fake)
    return calls


def _pair(tmp_path):
    src, dst = tmp_path / "new", tmp_path / "old"
    src.write_text("new")
    dst.write_text("old")
    return src, dst


def test_windows_retries_until_the_reader_lets_go(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)
    calls = _flaky(monkeypatch, failures=3)
    slept = []
    rf.replace_file(src, dst, platform="win32", sleep=slept.append)
    assert dst.read_text() == "new"
    assert len(calls) == 4
    assert slept == list(rf.REPLACE_RETRY_DELAYS_S[:3])


def test_windows_gives_up_with_the_error(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)
    calls = _flaky(monkeypatch, failures=1000)
    with pytest.raises(PermissionError):
        rf.replace_file(src, dst, platform="win32", sleep=lambda _: None)
    assert len(calls) == len(rf.REPLACE_RETRY_DELAYS_S) + 1
    assert dst.read_text() == "old"


def test_posix_raises_at_once(tmp_path, monkeypatch):
    src, dst = _pair(tmp_path)
    calls = _flaky(monkeypatch, failures=1)
    with pytest.raises(PermissionError):
        rf.replace_file(src, dst, platform="linux", sleep=lambda _: None)
    assert len(calls) == 1


def test_other_errors_are_not_retried(tmp_path):
    with pytest.raises(FileNotFoundError):
        rf.replace_file(
            tmp_path / "gone", tmp_path / "dst", platform="win32", sleep=lambda _: None
        )


@pytest.mark.skipif(os.name != "nt", reason="the race exists only on Windows")
def test_a_real_open_reader_does_not_fail_the_replace(tmp_path):
    """The failure this exists for: the target held open by a reader."""
    src, dst = _pair(tmp_path)

    def hold():
        with open(dst, "r", encoding="utf-8"):
            time.sleep(0.15)

    reader = threading.Thread(target=hold)
    reader.start()
    time.sleep(0.05)
    rf.replace_file(src, dst)
    reader.join()
    assert dst.read_text() == "new"
