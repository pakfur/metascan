"""Tests for Scanner.ingest_file — the public single-file ingest path.

Used by the ComfyUI output pipeline, which has one file at a time and no
directory to scan.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "media").mkdir()
        yield root


def _png(path: Path, size=(64, 64), color=(200, 30, 30)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def test_ingest_file_returns_media_and_persists_it(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "a.png")

    media = scanner.ingest_file(img)

    assert media is not None
    assert media.width == 64
    assert media.height == 64
    assert db.get_media(img) is not None


def test_ingest_file_stores_a_perceptual_hash(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "b.png")

    scanner.ingest_file(img)

    assert str(img) in db.get_all_phashes()


def test_ingest_file_returns_none_for_a_non_image(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    junk = workspace / "media" / "c.png"
    junk.write_bytes(b"not an image")

    assert scanner.ingest_file(junk) is None


def test_ingest_file_is_idempotent(workspace):
    """Re-ingesting the same file upserts rather than duplicating.

    save_media uses INSERT ... ON CONFLICT(file_path) DO UPDATE, so a
    second ingest must not add a row.
    """
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    img = _png(workspace / "media" / "d.png")

    scanner.ingest_file(img)
    scanner.ingest_file(img)

    assert len(db.get_existing_file_paths()) == 1


def test_scan_directory_still_works_through_the_extracted_method(workspace):
    db = DatabaseManager(workspace / "db")
    scanner = Scanner(db)
    _png(workspace / "media" / "e.png")
    _png(workspace / "media" / "f.png")

    assert scanner.scan_directory(str(workspace / "media")) == 2
