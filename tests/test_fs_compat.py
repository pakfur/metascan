import unicodedata

import pytest


from metascan.utils.fs_compat import (
    can_open_for_read,
    is_ignored_name,
    is_partial_download,
    path_key,
)

NFC = unicodedata.normalize("NFC", "café.png")
NFD = unicodedata.normalize("NFD", "café.png")


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_nfd_equals_nfc(platform):
    assert NFC != NFD
    assert path_key(NFD, platform) == path_key(NFC, platform)


@pytest.mark.parametrize(
    "platform,equal", [("win32", True), ("darwin", True), ("linux", False)]
)
def test_case_folding(platform, equal):
    assert (path_key("A/B.PNG", platform) == path_key("a/b.png", platform)) is equal


@pytest.mark.parametrize(
    "name",
    [
        "Thumbs.db",
        "thumbs.db",
        "desktop.ini",
        "~$a.png",
        ".DS_Store",
        "._x.png",
        ".Spotlight-V100",
    ],
)
def test_ignored(name):
    assert is_ignored_name(name)


@pytest.mark.parametrize(
    "name",
    [
        ".hidden.png",
        ".sketch.png",
        ".hidden",
        "a.png",
        "thumbs.png",
        "my~$a.png",
        "x.mp4",
    ],
)
def test_not_ignored(name):
    assert not is_ignored_name(name)


@pytest.mark.parametrize(
    "name", ["a.crdownload", "a.part", "a.tmp", "a.download", "A.PART"]
)
def test_partial(name):
    assert is_partial_download(name)


@pytest.mark.parametrize("name", ["a.png", "part.png", "a.tmp.png"])
def test_not_partial(name):
    assert not is_partial_download(name)


def test_can_open(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    assert can_open_for_read(str(f))
    assert not can_open_for_read(str(tmp_path / "missing.png"))


def test_permission_error_and_other_oserror(monkeypatch, tmp_path):
    for exc in (PermissionError("sharing violation"), OSError("boom")):

        def fake_open(*a, _exc=exc, **k):
            raise _exc

        monkeypatch.setattr("builtins.open", fake_open)
        assert not can_open_for_read(str(tmp_path / "a"))


def test_handle_closed_and_contents_unread(monkeypatch, tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"data")
    real_open = open
    opened = []

    def spy(*a, **k):
        h = real_open(*a, **k)
        opened.append(h)
        return h

    monkeypatch.setattr("builtins.open", spy)
    assert can_open_for_read(str(f))
    assert opened and all(h.closed for h in opened)


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_casefold_result_is_nfc(platform):
    # U+0390 casefolds to a decomposed sequence; the key must be NFC again.
    key = path_key("\u0390", platform)
    assert unicodedata.is_normalized("NFC", key)
    assert key == path_key("\u03b9\u0308\u0301", platform)
