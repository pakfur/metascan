"""End-to-end scanner tests for HEIC + EXIF orientation handling."""

import tempfile
from pathlib import Path

import pytest
from PIL import Image

# Module under test will register pillow-heif on import.
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner


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


def test_scanner_handles_heic_when_pillow_heif_available(db):
    pytest.importorskip("pillow_heif")
    manager, tmp = db
    heic_path = tmp / "shot.heic"
    img = Image.new("RGB", (40, 40), color=(10, 20, 30))
    img.save(heic_path, "HEIF")

    scanner = Scanner(manager)
    scanner.scan_directory(str(tmp))

    loaded = manager.get_media(heic_path)
    assert loaded is not None
    assert loaded.format in ("HEIF", "HEIC")
