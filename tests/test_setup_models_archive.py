"""Archive extraction for the llama-server release assets.

b10456 ships Linux/macOS assets as .tar.gz and Windows as .zip; both carry
the binary plus sister shared libraries (with SONAME symlink chains) under
a bin/ folder that must be flattened next to the binary (RUNPATH=$ORIGIN).
Uses file:// URLs so no network is involved.
"""

import io
import tarfile
from pathlib import Path

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


def test_targz_asset_extracts_flat_with_symlinks(tmp_path):
    archive = tmp_path / "llama-b10456-bin-ubuntu-x64.tar.gz"
    _make_targz(archive)
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
