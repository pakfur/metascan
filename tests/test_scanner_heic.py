"""End-to-end scanner tests for HEIC + EXIF orientation handling."""

import base64
import tempfile
from pathlib import Path

import pytest
from PIL import Image

# Module under test will register pillow-heif on import.
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.utils.heic import _heif_decode_probe, _HEIF_1X1_B64


def _write_jpeg_with_orientation(
    path: Path, w: int = 100, h: int = 50, orientation: int = 6
) -> None:
    """Write a tiny JPEG whose EXIF claims a given orientation tag (1..8)."""
    img = Image.new("RGB", (w, h), color=(200, 100, 50))
    exif = img.getexif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone Test"
    exif[0x0112] = orientation
    img.save(path, "JPEG", exif=exif.tobytes())


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_dir = tmp_path / "db"
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        manager = DatabaseManager(db_dir)
        yield manager, media_dir
        manager.close()


def test_supported_extensions_includes_heic_heif():
    assert ".heic" in Scanner.SUPPORTED_EXTENSIONS
    assert ".heif" in Scanner.SUPPORTED_EXTENSIONS


def test_scanner_persists_camera_make_and_model_from_jpeg(db):
    manager, tmp = db
    img_path = tmp / "shot.jpg"
    _write_jpeg_with_orientation(img_path, w=100, h=50, orientation=1)

    scanner = Scanner(manager)
    scanner.scan_directory(str(tmp))

    loaded = manager.get_media(img_path)
    assert loaded is not None
    assert loaded.camera_make == "Apple"
    assert loaded.camera_model == "iPhone Test"
    assert loaded.orientation == 1
    assert loaded.width == 100
    assert loaded.height == 50


def test_scanner_applies_exif_orientation_to_dimensions(db):
    """Orientation=6 (90° CW) means the JPEG bytes are landscape but the
    image should display as portrait. width/height stored on Media must be
    the displayed dimensions, not the raw bytes dims."""
    manager, tmp = db
    img_path = tmp / "portrait.jpg"
    _write_jpeg_with_orientation(img_path, w=100, h=50, orientation=6)

    scanner = Scanner(manager)
    scanner.scan_directory(str(tmp))

    loaded = manager.get_media(img_path)
    assert loaded is not None
    # Display dims after exif_transpose: portrait (50 wide x 100 tall)
    assert loaded.width == 50
    assert loaded.height == 100
    assert loaded.orientation == 6


@pytest.mark.skipif(
    not _heif_decode_probe(), reason="pillow-heif unavailable or decode crashes"
)
def test_scanner_handles_heic_when_pillow_heif_available(db):
    """Write a pre-built minimal HEIF to disk and scan it.

    We deliberately avoid ``Image.save(..., 'HEIF')`` here because the
    pillow-heif *encoder* segfaults on some ARM macOS builds.  The scanner
    only *decodes* HEIC files, so the test should exercise only that path.
    """
    manager, tmp = db
    heic_path = tmp / "shot.heic"
    heic_path.write_bytes(base64.b64decode(_HEIF_1X1_B64))

    scanner = Scanner(manager)
    scanner.scan_directory(str(tmp))

    loaded = manager.get_media(heic_path)
    assert loaded is not None
    assert loaded.format in ("HEIF", "HEIC")


def test_scanner_handles_png_with_no_exif(db):
    """AI-generated PNG with no EXIF must not crash the scan and must
    leave the new photo fields as None."""
    manager, tmp = db
    img_path = tmp / "ai.png"
    Image.new("RGB", (512, 512), color=(50, 50, 50)).save(img_path, "PNG")

    scanner = Scanner(manager)
    scanner.scan_directory(str(tmp))

    loaded = manager.get_media(img_path)
    assert loaded is not None
    assert loaded.width == 512
    assert loaded.height == 512
    assert loaded.camera_make is None
    assert loaded.camera_model is None
    assert loaded.gps_latitude is None
    assert loaded.orientation is None
    assert loaded.photo_exposure is None
