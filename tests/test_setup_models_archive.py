"""Archive extraction for the llama-server release assets.

b10456 ships Linux/macOS assets as .tar.gz and Windows as .zip; both carry
the binary plus sister shared libraries (with SONAME symlink chains) under
a bin/ folder that must be flattened next to the binary (RUNPATH=$ORIGIN).
Uses file:// URLs so no network is involved.
"""

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from setup_models import DownloadTarget, _ensure_target


def _make_targz(path: Path, prefix: str = "llama-b10456/bin/") -> None:
    with tarfile.open(path, "w:gz") as tf:

        def add_file(name: str, data: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))

        add_file("llama-server", b"#!/bin/sh\necho ok\n", mode=0o755)
        add_file("libllama.so.0.0.1", b"ELFDATA")
        link = tarfile.TarInfo(prefix + "libllama.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libllama.so.0.0.1"
        tf.addfile(link)
        # Nested payloads (e.g. a docs/ subfolder) must be skipped.
        add_file("extras/README.txt", b"skip me")


@pytest.mark.parametrize(
    "prefix",
    [
        "llama-b10456/bin/",  # synthetic nested-bin layout
        "llama-b10456/",  # real shipped flat layout
    ],
)
def test_targz_asset_extracts_flat_with_symlinks(tmp_path, prefix):
    archive = tmp_path / "llama-b10456-bin-ubuntu-x64.tar.gz"
    _make_targz(archive, prefix=prefix)
    dest = tmp_path / "out" / "llama-server"

    ok = _ensure_target(DownloadTarget(url=archive.as_uri(), dest=dest))

    assert ok is True
    assert dest.exists()
    assert dest.stat().st_mode & 0o111  # executable
    assert (tmp_path / "out" / "libllama.so.0.0.1").exists()
    link = tmp_path / "out" / "libllama.so"
    assert link.is_symlink()
    assert link.readlink() == Path("libllama.so.0.0.1")
    assert not (tmp_path / "out" / "README.txt").exists()
    assert not (tmp_path / "out" / "extras").exists()


def _make_zip(path: Path, prefix: str = "llama-b10456/") -> None:
    with zipfile.ZipFile(path, "w") as zf:

        def add_file(name: str, data: bytes, mode: int = 0o644) -> None:
            info = zipfile.ZipInfo(prefix + name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)

        add_file("llama-server", b"#!/bin/sh\necho ok\n", mode=0o755)
        add_file("libllama.so.0.0.1", b"ELFDATA")
        # zipfile has no native symlink support: store the link target as
        # the entry's content and mark it via S_IFLNK in external_attr, the
        # same convention _extract_flat_bin_zip decodes.
        link_info = zipfile.ZipInfo(prefix + "libllama.so")
        link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link_info, "libllama.so.0.0.1")
        # Nested payload (e.g. a docs/ subfolder) must be skipped.
        add_file("extras/README.txt", b"skip me")


def test_zip_asset_extracts_flat_with_symlinks(tmp_path):
    archive = tmp_path / "llama-b10456-bin-win-cpu-x64.zip"
    _make_zip(archive)
    dest = tmp_path / "out" / "llama-server"

    ok = _ensure_target(DownloadTarget(url=archive.as_uri(), dest=dest))

    assert ok is True
    assert dest.exists()
    assert dest.stat().st_mode & 0o111  # executable
    assert (tmp_path / "out" / "libllama.so.0.0.1").exists()
    link = tmp_path / "out" / "libllama.so"
    assert link.is_symlink()
    assert link.readlink() == Path("libllama.so.0.0.1")
    assert not (tmp_path / "out" / "README.txt").exists()
    assert not (tmp_path / "out" / "extras").exists()
