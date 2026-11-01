import unicodedata

import pytest


from metascan.utils.fs_compat import (
    native_watch_reliable,
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


# ------------------------------------------- can a native watcher be trusted

MOUNTINFO = (
    "60 59 8:32 / / rw,relatime shared:1 - ext4 /dev/sdc rw\n"
    "70 60 0:47 / /mnt/d rw,noatime shared:2 - 9p D:\\134 rw,aname=drvfs\n"
    "71 60 0:48 / /mnt/my\\040nas rw shared:3 - cifs //nas/x rw\n"
    "72 60 0:49 / /home/jk/nfs rw shared:4 - nfs4 s:/x rw\n"
)


@pytest.mark.parametrize(
    "path,want",
    [
        ("/home/jk/media", True),
        ("/mnt/d/Media/Staging", False),
        ("/mnt/d", False),
        ("/mnt/data/x", True),
        ("/mnt/my nas/x", False),
        ("/home/jk/nfs/a", False),
    ],
)
def test_linux_trusts_only_local_filesystems(path, want):
    assert native_watch_reliable(path, "linux", MOUNTINFO) is want


def test_unreadable_mountinfo_means_no_native_watch():
    assert native_watch_reliable("/x", "linux", "") is False


@pytest.mark.parametrize(
    "path,drive_type,want",
    [
        ("D:\\Media", 3, True),  # fixed: exFAT included
        ("E:\\Media", 2, True),  # removable
        ("Z:\\Media", 4, False),  # mapped network drive
        ("\\\\nas\\share\\x", 3, False),  # UNC
    ],
)
def test_windows_trusts_only_local_drives(path, drive_type, want):
    got = native_watch_reliable(path, "win32", drive_type=lambda root: drive_type)
    assert got is want


def test_macos_is_trusted():
    assert native_watch_reliable("/Volumes/x", "darwin") is True
