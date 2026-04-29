# Real-World Photo Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HEIC ingestion, EXIF metadata persistence (camera/exposure/lens/GPS/orientation), an interactive MapLibre location panel, and filter-sidebar buckets for camera make/model/has-GPS, so real-world photos are first-class citizens in Metascan.

**Architecture:** EXIF parsing lives in a new pure module `metascan/core/photo_exif.py` (no I/O). The scanner extracts photo EXIF + orientation in one Pillow read, applies `ImageOps.exif_transpose` for display dimensions, and persists 9 new columns on the `media` table (8 individual fields + a `photo_exposure` JSON blob). `pillow-heif` is registered idempotently in every process that calls `Image.open`. Frontend gains two new collapsible sections inside `MetadataPanel.vue` (Camera + Location), three new filter buckets (`camera_make`, `camera_model`, `has_gps`), and a dynamically-imported MapLibre GL map pointed at OpenFreeMap tiles.

**Tech Stack:** Python 3.11 / Pillow / pillow-heif / SQLite / FastAPI / Vue 3 / Pinia / MapLibre GL JS / OpenFreeMap.

**Spec:** `docs/superpowers/specs/2026-04-29-real-world-photos-design.md` — read this before starting.

---

## Pre-flight

- [ ] **Step 0.1: Verify clean working tree on the right branch**

```bash
git status
git rev-parse --abbrev-ref HEAD
```

Expected: working tree has only the spec from the previous commit; branch is `feature/vue-ui` (or a worktree branched off it).

- [ ] **Step 0.2: Verify the test suite is green before changes**

```bash
source venv/bin/activate
make quality test
```

Expected: 175 tests pass, flake8/black/mypy clean. If anything is red, stop and fix or report before continuing.

---

## Task 1: Add `pillow-heif` dependency and HEIC registration helper

**Files:**
- Modify: `requirements.txt`
- Create: `metascan/utils/heic.py`
- Test: (no test — module-level side-effect; covered indirectly by Task 7)

- [ ] **Step 1.1: Add the dependency to `requirements.txt`**

Edit `requirements.txt`. Find the `# Image Processing` section (currently has `Pillow==10.2.0` and `piexif==1.1.3`) and add a line:

```
pillow-heif>=0.16.0
```

Final block:

```
# Image Processing
Pillow==10.2.0
piexif==1.1.3
pillow-heif>=0.16.0
```

- [ ] **Step 1.2: Install it into the venv**

```bash
source venv/bin/activate
pip install -r requirements.txt
```

Expected: `pillow-heif` installs successfully (manylinux wheel on WSL/Linux, native on macOS, prebuilt on Windows).

- [ ] **Step 1.3: Create the registration helper**

Create `metascan/utils/heic.py`:

```python
"""HEIC/HEIF support via pillow-heif.

Idempotent registration helper. Importers must call ``register_heif_opener()``
at module load time before any ``Image.open`` calls. Calling it more than once
is a no-op (pillow-heif keeps a single global registration).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REGISTERED = False


def register_heif_opener() -> None:
    """Register pillow-heif with Pillow so HEIC/HEIF files are decodable.

    Safe to call multiple times. If pillow-heif isn't installed, logs a
    warning once and returns silently — HEIC files will then be skipped by
    the scanner instead of crashing the import.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from pillow_heif import register_heif_opener as _register

        _register()
        _REGISTERED = True
        logger.debug("pillow-heif registered.")
    except ImportError:
        _REGISTERED = True  # don't try again
        logger.warning(
            "pillow-heif not installed; HEIC/HEIF files will be skipped during scan."
        )
```

- [ ] **Step 1.4: Smoke test the helper**

```bash
python -c "from metascan.utils.heic import register_heif_opener; register_heif_opener(); from PIL import Image; print('HEIF' in Image.registered_extensions().values() or any('heif' in str(v).lower() for v in Image.registered_extensions().values()))"
```

Expected: `True` (Pillow now knows the HEIF extension).

- [ ] **Step 1.5: Commit**

```bash
git add requirements.txt metascan/utils/heic.py
git commit -m "$(cat <<'EOF'
feat: add pillow-heif dep and HEIC registration helper

Adds metascan/utils/heic.py::register_heif_opener for idempotent registration.
Subsequent commits wire it into scanner, thumbnail cache, embedding manager,
and upscaler.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `PhotoExif` parser module (pure)

**Files:**
- Create: `metascan/core/photo_exif.py`
- Create: `tests/test_photo_exif.py`

This is the largest unit. Pure (no I/O, no DB). All decoding wrapped in narrow per-field try/except so one bad tag never kills the parse.

- [ ] **Step 2.1: Write the failing test file**

Create `tests/test_photo_exif.py`:

```python
"""Unit tests for metascan/core/photo_exif.py — pure EXIF parser."""

from datetime import datetime
from fractions import Fraction
from typing import Any, Dict

import pytest
from PIL import Image
from PIL.ExifTags import IFD

from metascan.core.photo_exif import (
    PhotoExif,
    PhotoExposure,
    extract_photo_exif,
    _dms_to_decimal,
    decode_orientation_label,
    decode_flash,
)


# ----------------------------------------------------------------------
# Helpers to build a synthetic PIL Exif object without needing an image.
# ----------------------------------------------------------------------

def _make_exif(main: Dict[int, Any] | None = None,
               exif_ifd: Dict[int, Any] | None = None,
               gps_ifd: Dict[int, Any] | None = None) -> Image.Exif:
    """Build an Image.Exif populated via the documented public API.

    Pillow's Exif object writes back through `_ifds` for sub-IFDs; we use
    the `get_ifd` setter pattern which is the supported public path.
    """
    exif = Image.Exif()
    if main:
        for k, v in main.items():
            exif[k] = v
    if exif_ifd:
        sub = exif.get_ifd(IFD.Exif)
        for k, v in exif_ifd.items():
            sub[k] = v
    if gps_ifd:
        sub = exif.get_ifd(IFD.GPSInfo)
        for k, v in gps_ifd.items():
            sub[k] = v
    return exif


# ----------------------------------------------------------------------
# DMS -> decimal helper
# ----------------------------------------------------------------------

class TestDmsToDecimal:
    def test_zero_origin(self):
        assert _dms_to_decimal((Fraction(0), Fraction(0), Fraction(0))) == 0.0

    def test_pure_degrees(self):
        assert _dms_to_decimal((Fraction(45), Fraction(0), Fraction(0))) == 45.0

    def test_minutes_only(self):
        # 30 minutes = 0.5 degrees
        assert _dms_to_decimal((Fraction(0), Fraction(30), Fraction(0))) == 0.5

    def test_seconds_only(self):
        # 60 seconds = 1 minute = 1/60 degrees
        assert _dms_to_decimal((Fraction(0), Fraction(0), Fraction(60))) == pytest.approx(1.0 / 60.0)

    def test_combined(self):
        # 37° 46' 30" = 37.775
        result = _dms_to_decimal((Fraction(37), Fraction(46), Fraction(30)))
        assert result == pytest.approx(37.775, abs=1e-6)

    def test_accepts_float_tuple(self):
        # Real EXIF returns IFDRational, which behaves like float — accept floats too.
        assert _dms_to_decimal((37.0, 46.0, 30.0)) == pytest.approx(37.775, abs=1e-6)


# ----------------------------------------------------------------------
# Orientation labels
# ----------------------------------------------------------------------

class TestOrientationLabel:
    @pytest.mark.parametrize("value,expected", [
        (1, "Landscape"),
        (2, "Landscape (mirrored)"),
        (3, "Landscape (rotated 180°)"),
        (4, "Landscape (mirrored, rotated 180°)"),
        (5, "Portrait (mirrored, rotated 270°)"),
        (6, "Portrait"),
        (7, "Portrait (mirrored, rotated 90°)"),
        (8, "Portrait (rotated 270°)"),
    ])
    def test_known_values(self, value: int, expected: str):
        assert decode_orientation_label(value) == expected

    def test_unknown_value(self):
        assert decode_orientation_label(99) == "Unknown orientation (99)"


# ----------------------------------------------------------------------
# Flash decoding
# ----------------------------------------------------------------------

class TestFlashDecode:
    @pytest.mark.parametrize("value,expected", [
        (0x00, "Off, Did not fire"),
        (0x01, "Fired"),
        (0x09, "On, Fired"),
        (0x10, "Off, Did not fire (compulsory)"),
        (0x18, "Auto, Did not fire"),
        (0x19, "Auto, Fired"),
    ])
    def test_known_values(self, value: int, expected: str):
        assert decode_flash(value) == expected

    def test_unknown_value(self):
        assert decode_flash(0x42) == "Flash 0x42"


# ----------------------------------------------------------------------
# Full extract_photo_exif behaviour
# ----------------------------------------------------------------------

class TestExtractPhotoExif:
    def test_none_input_returns_none_pair(self):
        assert extract_photo_exif(None) == (None, None)

    def test_empty_exif_returns_none_pair(self):
        assert extract_photo_exif(_make_exif()) == (None, None)

    def test_iphone_full_exif(self):
        exif = _make_exif(
            main={
                0x010F: "Apple",                # Make
                0x0110: "iPhone 15 Pro",         # Model
                0x0112: 6,                       # Orientation (portrait)
            },
            exif_ifd={
                0x9003: "2026:04:12 15:24:31",   # DateTimeOriginal
                0x829A: (Fraction(1, 250),),     # ExposureTime — some cameras nest in tuple
                0x829D: Fraction(18, 10),        # FNumber = 1.8
                0x8827: 400,                     # ISOSpeedRatings
                0x9209: 0x19,                    # Flash = "Auto, Fired"
                0x920A: Fraction(686, 100),      # FocalLength = 6.86mm
                0xA405: 27,                      # FocalLengthIn35mmFilm
                0xA434: "iPhone 15 Pro back triple camera 6.86mm f/1.78",
            },
            gps_ifd={
                1: "N",                          # GPSLatitudeRef
                2: (Fraction(37), Fraction(46), Fraction(30)),  # GPSLatitude
                3: "W",                          # GPSLongitudeRef
                4: (Fraction(122), Fraction(25), Fraction(10)),  # GPSLongitude
                5: 0,                             # GPSAltitudeRef (above)
                6: Fraction(12),                  # GPSAltitude
            },
        )
        photo, orientation = extract_photo_exif(exif)
        assert orientation == 6
        assert photo is not None
        assert photo.camera_make == "Apple"
        assert photo.camera_model == "iPhone 15 Pro"
        assert photo.lens_model == "iPhone 15 Pro back triple camera 6.86mm f/1.78"
        assert photo.datetime_original == datetime(2026, 4, 12, 15, 24, 31)
        assert photo.gps_latitude == pytest.approx(37.775, abs=1e-3)
        assert photo.gps_longitude == pytest.approx(-122.4194, abs=1e-3)
        assert photo.gps_altitude == 12.0
        assert photo.exposure is not None
        assert photo.exposure.shutter_speed == "1/250"
        assert photo.exposure.aperture == 1.8
        assert photo.exposure.iso == 400
        assert photo.exposure.flash == "Auto, Fired"
        assert photo.exposure.focal_length == 6.9
        assert photo.exposure.focal_length_35mm == 27

    def test_canon_no_gps(self):
        exif = _make_exif(
            main={0x010F: "Canon", 0x0110: "EOS R5"},
            exif_ifd={0x9003: "2025:08:01 10:00:00", 0x829D: Fraction(28, 10)},
        )
        photo, orientation = extract_photo_exif(exif)
        assert orientation is None
        assert photo is not None
        assert photo.camera_make == "Canon"
        assert photo.camera_model == "EOS R5"
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None

    def test_bogus_zero_gps_rejected(self):
        exif = _make_exif(
            main={0x010F: "Generic"},
            gps_ifd={
                1: "N", 2: (Fraction(0), Fraction(0), Fraction(0)),
                3: "E", 4: (Fraction(0), Fraction(0), Fraction(0)),
            },
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None

    def test_out_of_range_gps_rejected(self):
        # lat=100 is invalid (max 90)
        exif = _make_exif(
            main={0x010F: "Generic"},
            gps_ifd={
                1: "N", 2: (Fraction(100), Fraction(0), Fraction(0)),
                3: "W", 4: (Fraction(50), Fraction(0), Fraction(0)),
            },
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None

    def test_southern_western_hemisphere_negative(self):
        exif = _make_exif(
            main={0x010F: "Generic"},
            gps_ifd={
                1: "S", 2: (Fraction(34), Fraction(0), Fraction(0)),
                3: "W", 4: (Fraction(58), Fraction(0), Fraction(0)),
            },
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.gps_latitude == pytest.approx(-34.0)
        assert photo.gps_longitude == pytest.approx(-58.0)

    def test_below_sea_level_altitude(self):
        exif = _make_exif(
            main={0x010F: "Generic"},
            gps_ifd={
                1: "N", 2: (Fraction(36), Fraction(0), Fraction(0)),
                3: "W", 4: (Fraction(116), Fraction(0), Fraction(0)),
                5: 1,                       # ref=1 -> below sea level
                6: Fraction(86),
            },
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.gps_altitude == -86.0

    def test_corrupt_datetime_does_not_break_other_fields(self):
        exif = _make_exif(
            main={0x010F: "Apple", 0x0110: "iPhone"},
            exif_ifd={0x9003: "not-a-date", 0x8827: 200},
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.datetime_original is None
        assert photo.camera_make == "Apple"
        assert photo.exposure is not None
        assert photo.exposure.iso == 200

    def test_year_out_of_range_rejected(self):
        exif = _make_exif(
            main={0x010F: "Apple"},
            exif_ifd={0x9003: "1850:01:01 00:00:00"},
        )
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.datetime_original is None

    def test_orientation_only_returns_orientation_but_no_photoexif(self):
        # Orientation alone does NOT trigger a PhotoExif return — the section
        # gates on photographic fields (make/model/lens/datetime/exposure).
        exif = _make_exif(main={0x0112: 1})
        photo, orientation = extract_photo_exif(exif)
        assert orientation == 1
        assert photo is None

    def test_iso_tuple_form_taken_first(self):
        exif = _make_exif(main={0x010F: "X"}, exif_ifd={0x8827: (800, 800)})
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.exposure is not None
        assert photo.exposure.iso == 800

    def test_long_exposure_decimal_format(self):
        # ExposureTime = 5 -> format as "5" (or "5.0" or "5 s") — choose one stable form.
        # Spec says: numerator==1 -> "1/N", else decimal seconds rounded to 2 places.
        exif = _make_exif(main={"X": 1}, exif_ifd={0x829A: Fraction(5)})
        photo, _ = extract_photo_exif(exif)
        # No camera info, but exposure has shutter -> photo non-None
        assert photo is not None
        assert photo.exposure is not None
        assert photo.exposure.shutter_speed == "5"

    def test_short_exposure_fraction_format(self):
        exif = _make_exif(main={"X": 1}, exif_ifd={0x829A: Fraction(1, 4000)})
        photo, _ = extract_photo_exif(exif)
        assert photo is not None
        assert photo.exposure is not None
        assert photo.exposure.shutter_speed == "1/4000"
```

- [ ] **Step 2.2: Run the test to verify it fails**

```bash
pytest tests/test_photo_exif.py -v
```

Expected: collection error or test failures because `metascan/core/photo_exif.py` doesn't exist yet.

- [ ] **Step 2.3: Implement the parser**

Create `metascan/core/photo_exif.py`:

```python
"""Parse photo-relevant EXIF tags into a structured dataclass.

Pure module — no I/O, no DB, no Pillow Image.open. Takes an already-parsed
``PIL.Image.Exif`` and returns ``(PhotoExif | None, orientation_tag | None)``.

Each field decode is wrapped in a narrow try/except returning ``None`` for
that field — one bad tag never kills the whole parse. Whole-function failure
returns ``(None, None)`` so the caller can still save a Media row without
photo metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Tuple

from PIL import Image
from PIL.ExifTags import IFD

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------


@dataclass
class PhotoExposure:
    """Exposure / lens settings — serialized as the ``photo_exposure`` JSON
    blob column on the media table."""

    shutter_speed: Optional[str] = None
    aperture: Optional[float] = None
    iso: Optional[int] = None
    flash: Optional[str] = None
    focal_length: Optional[float] = None
    focal_length_35mm: Optional[int] = None

    def is_empty(self) -> bool:
        return all(
            getattr(self, f) is None
            for f in (
                "shutter_speed",
                "aperture",
                "iso",
                "flash",
                "focal_length",
                "focal_length_35mm",
            )
        )


@dataclass
class PhotoExif:
    """Bundle of photo-EXIF fields. Returned by ``extract_photo_exif``;
    callers unpack into Media kwargs (each field maps to its own Media field
    / DB column, except ``exposure`` which serializes to photo_exposure)."""

    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    datetime_original: Optional[datetime] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_altitude: Optional[float] = None
    exposure: Optional[PhotoExposure] = None


# ----------------------------------------------------------------------
# EXIF tag IDs (raw integers; PIL.ExifTags.TAGS maps these to names)
# ----------------------------------------------------------------------

_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_ORIENTATION = 0x0112
_TAG_DATETIME = 0x0132

# Inside the Exif IFD:
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_EXPOSURE_TIME = 0x829A
_TAG_FNUMBER = 0x829D
_TAG_ISO = 0x8827
_TAG_FLASH = 0x9209
_TAG_FOCAL_LENGTH = 0x920A
_TAG_FOCAL_LENGTH_35MM = 0xA405
_TAG_LENS_MODEL = 0xA434

# Inside the GPSInfo IFD:
_GPS_LAT_REF = 1
_GPS_LAT = 2
_GPS_LON_REF = 3
_GPS_LON = 4
_GPS_ALT_REF = 5
_GPS_ALT = 6


# Flash bitfield -> human label. EXIF Flash is a 16-bit flag field; we only
# decode the most common low-byte values. Anything else falls through to a
# hex-formatted "Flash 0xNN" so we never silently swallow values.
_FLASH_LABELS = {
    0x00: "Off, Did not fire",
    0x01: "Fired",
    0x05: "Fired, Return not detected",
    0x07: "Fired, Return detected",
    0x08: "On, Did not fire",
    0x09: "On, Fired",
    0x0D: "On, Return not detected",
    0x0F: "On, Return detected",
    0x10: "Off, Did not fire (compulsory)",
    0x18: "Auto, Did not fire",
    0x19: "Auto, Fired",
    0x1D: "Auto, Fired, Return not detected",
    0x1F: "Auto, Fired, Return detected",
    0x20: "No flash function",
    0x41: "Fired, Red-eye reduction",
    0x45: "Fired, Red-eye reduction, Return not detected",
    0x47: "Fired, Red-eye reduction, Return detected",
    0x49: "On, Fired, Red-eye reduction",
    0x4D: "On, Red-eye reduction, Return not detected",
    0x4F: "On, Red-eye reduction, Return detected",
    0x59: "Auto, Fired, Red-eye reduction",
    0x5D: "Auto, Fired, Red-eye reduction, Return not detected",
    0x5F: "Auto, Fired, Red-eye reduction, Return detected",
}


_ORIENTATION_LABELS = {
    1: "Landscape",
    2: "Landscape (mirrored)",
    3: "Landscape (rotated 180°)",
    4: "Landscape (mirrored, rotated 180°)",
    5: "Portrait (mirrored, rotated 270°)",
    6: "Portrait",
    7: "Portrait (mirrored, rotated 90°)",
    8: "Portrait (rotated 270°)",
}


# ----------------------------------------------------------------------
# Helpers — public so tests can hit them directly.
# ----------------------------------------------------------------------


def decode_orientation_label(value: int) -> str:
    """EXIF orientation tag value (1-8) -> human-readable label."""
    return _ORIENTATION_LABELS.get(value, f"Unknown orientation ({value})")


def decode_flash(value: int) -> str:
    """EXIF flash bitfield -> human-readable label."""
    label = _FLASH_LABELS.get(int(value))
    if label is not None:
        return label
    return f"Flash 0x{int(value):02X}"


def _dms_to_decimal(dms: Tuple[Any, Any, Any]) -> float:
    """Convert (degrees, minutes, seconds) to signed-friendly decimal degrees.

    Hemisphere sign is applied by the caller. Accepts Fractions, IFDRational,
    floats, or ints — anything that supports ``float(x)``.
    """
    d, m, s = dms[0], dms[1], dms[2]
    return float(d) + float(m) / 60.0 + float(s) / 3600.0


# ----------------------------------------------------------------------
# Per-field extractors. Each returns Optional[T]; logs at DEBUG on failure.
# ----------------------------------------------------------------------


def _str_field(exif: Any, tag: int) -> Optional[str]:
    try:
        v = exif.get(tag)
        if v is None:
            return None
        s = str(v).strip().rstrip("\x00").strip()
        return s if s else None
    except Exception as exc:
        logger.debug("Failed to read tag 0x%04X as string: %s", tag, exc)
        return None


def _int_field(exif: Any, tag: int) -> Optional[int]:
    try:
        v = exif.get(tag)
        if v is None:
            return None
        # ISOSpeedRatings sometimes comes as (iso,) tuple
        if isinstance(v, (tuple, list)):
            v = v[0]
        return int(v)
    except Exception as exc:
        logger.debug("Failed to read tag 0x%04X as int: %s", tag, exc)
        return None


def _float_field(exif: Any, tag: int, ndigits: int = 1) -> Optional[float]:
    try:
        v = exif.get(tag)
        if v is None:
            return None
        if isinstance(v, (tuple, list)):
            v = v[0]
        return round(float(v), ndigits)
    except Exception as exc:
        logger.debug("Failed to read tag 0x%04X as float: %s", tag, exc)
        return None


def _datetime_field(exif: Any, tag: int) -> Optional[datetime]:
    raw = _str_field(exif, tag)
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        logger.debug("Unparseable datetime in tag 0x%04X: %r", tag, raw)
        return None
    if dt.year < 1900 or dt.year > 2100:
        return None
    return dt


def _shutter_speed(exif: Any) -> Optional[str]:
    try:
        v = exif.get(_TAG_EXPOSURE_TIME)
        if v is None:
            return None
        if isinstance(v, (tuple, list)):
            v = v[0]
        f = float(v)
        if f <= 0:
            return None
        # Sub-second exposures: prefer "1/N" form. Pillow returns IFDRational
        # which exposes .numerator/.denominator; fall back to deriving N from
        # 1/f when those attrs are absent.
        num = getattr(v, "numerator", None)
        den = getattr(v, "denominator", None)
        if num == 1 and isinstance(den, int) and den > 1:
            return f"1/{den}"
        if f < 1:
            return f"1/{round(1.0 / f)}"
        # Long exposures: integer if exact, else 2-decimal float.
        if f == int(f):
            return str(int(f))
        return f"{round(f, 2)}"
    except Exception as exc:
        logger.debug("Failed to format shutter speed: %s", exc)
        return None


def _flash_field(exif: Any) -> Optional[str]:
    try:
        v = exif.get(_TAG_FLASH)
        if v is None:
            return None
        return decode_flash(int(v))
    except Exception as exc:
        logger.debug("Failed to read flash: %s", exc)
        return None


def _gps_signed(value_dms: Any, ref: Optional[str], neg_ref: str) -> Optional[float]:
    if value_dms is None or ref is None:
        return None
    try:
        decimal = _dms_to_decimal(tuple(value_dms))
    except Exception as exc:
        logger.debug("Bad GPS DMS tuple: %s", exc)
        return None
    if str(ref).strip().upper() == neg_ref:
        decimal = -decimal
    return decimal


def _gps_altitude(value: Any, ref: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        alt = float(value)
    except Exception:
        return None
    try:
        if ref is not None and int(ref) == 1:
            alt = -alt
    except Exception:
        pass
    return alt


# ----------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------


def extract_photo_exif(
    exif: Optional[Image.Exif],
) -> Tuple[Optional[PhotoExif], Optional[int]]:
    """Parse a PIL Exif into a ``(PhotoExif | None, orientation_tag | None)``.

    PhotoExif is None when no photographic fields are present (so the caller
    can hide the Camera section cleanly). Orientation is returned separately
    because the scanner needs it before EXIF parsing completes — it drives
    ``ImageOps.exif_transpose`` which determines the displayed dimensions.
    Orientation alone does NOT cause a non-None PhotoExif (many AI tools
    write Orientation=1 to all output, which would otherwise render a
    one-row Camera section on every AI image).
    """
    if exif is None:
        return None, None

    try:
        # Top-level IFD (Make, Model, Orientation, DateTime fallback)
        camera_make = _str_field(exif, _TAG_MAKE)
        camera_model = _str_field(exif, _TAG_MODEL)
        orientation = _int_field(exif, _TAG_ORIENTATION)

        # Sub-IFDs
        try:
            exif_ifd = exif.get_ifd(IFD.Exif)
        except Exception:
            exif_ifd = {}
        try:
            gps_ifd = exif.get_ifd(IFD.GPSInfo)
        except Exception:
            gps_ifd = {}

        lens_model = _str_field(exif_ifd, _TAG_LENS_MODEL)
        datetime_original = _datetime_field(exif_ifd, _TAG_DATETIME_ORIGINAL)
        if datetime_original is None:
            datetime_original = _datetime_field(exif, _TAG_DATETIME)

        # Exposure block
        exposure = PhotoExposure(
            shutter_speed=_shutter_speed(exif_ifd),
            aperture=_float_field(exif_ifd, _TAG_FNUMBER, ndigits=1),
            iso=_int_field(exif_ifd, _TAG_ISO),
            flash=_flash_field(exif_ifd),
            focal_length=_float_field(exif_ifd, _TAG_FOCAL_LENGTH, ndigits=1),
            focal_length_35mm=_int_field(exif_ifd, _TAG_FOCAL_LENGTH_35MM),
        )

        # GPS — sanity guards reject failed-lock zeros and out-of-range values.
        lat = _gps_signed(gps_ifd.get(_GPS_LAT), gps_ifd.get(_GPS_LAT_REF), "S")
        lon = _gps_signed(gps_ifd.get(_GPS_LON), gps_ifd.get(_GPS_LON_REF), "W")
        if lat is not None and lon is not None:
            if (
                (lat == 0.0 and lon == 0.0)
                or abs(lat) > 90.0
                or abs(lon) > 180.0
            ):
                lat, lon = None, None
        # If only one of the two parsed cleanly, drop both — half a coordinate
        # isn't useful and the marker would be wrong.
        if lat is None or lon is None:
            lat, lon = None, None

        alt = (
            _gps_altitude(gps_ifd.get(_GPS_ALT), gps_ifd.get(_GPS_ALT_REF))
            if (lat is not None and lon is not None)
            else None
        )

        # Section gating: orientation alone doesn't trigger a non-None return.
        any_photo_field = any(
            (
                camera_make,
                camera_model,
                lens_model,
                datetime_original,
                lat is not None and lon is not None,
                not exposure.is_empty(),
            )
        )
        if not any_photo_field:
            return None, orientation

        photo = PhotoExif(
            camera_make=camera_make,
            camera_model=camera_model,
            lens_model=lens_model,
            datetime_original=datetime_original,
            gps_latitude=lat,
            gps_longitude=lon,
            gps_altitude=alt,
            exposure=exposure if not exposure.is_empty() else None,
        )
        return photo, orientation
    except Exception as exc:
        logger.warning("extract_photo_exif crashed: %s", exc, exc_info=True)
        return None, None
```

- [ ] **Step 2.4: Run the test to verify it passes**

```bash
pytest tests/test_photo_exif.py -v
```

Expected: all tests pass.

- [ ] **Step 2.5: Lint + type-check the new module**

```bash
flake8 metascan/core/photo_exif.py tests/test_photo_exif.py
black --check metascan/core/photo_exif.py tests/test_photo_exif.py
mypy metascan/core/photo_exif.py
```

Expected: clean. If black complains, run `black metascan/core/photo_exif.py tests/test_photo_exif.py`.

- [ ] **Step 2.6: Commit**

```bash
git add metascan/core/photo_exif.py tests/test_photo_exif.py
git commit -m "$(cat <<'EOF'
feat: add photo_exif parser module

Pure module that converts PIL Exif into a structured PhotoExif dataclass
plus the raw orientation tag. Per-field try/except keeps a single bogus tag
from killing the whole parse. GPS sanity guards reject (0,0) and
out-of-range coords. Orientation alone does not trigger a non-None return
(AI tools often write Orientation=1, which would otherwise pop a one-row
Camera section on every AI image).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add new fields to `Media` dataclass

**Files:**
- Modify: `metascan/core/media.py`
- Modify: `tests/test_embedding_pipeline.py` (only if it constructs Media — verify, don't preemptively change)

- [ ] **Step 3.1: Write a failing test for `Media.from_dict_fast` round-trip**

Append to `tests/test_photo_exif.py`:

```python
# ----------------------------------------------------------------------
# Media.from_dict_fast round-trip with new photo fields
# ----------------------------------------------------------------------


def test_media_from_dict_fast_with_photo_exif():
    from datetime import datetime
    from pathlib import Path
    from metascan.core.media import Media, PhotoExposure

    data = {
        "file_path": "/tmp/IMG_0001.HEIC",
        "file_size": 1234567,
        "width": 3024,
        "height": 4032,
        "format": "HEIF",
        "created_at": "2026-04-12T15:24:31",
        "modified_at": "2026-04-12T15:24:31",
        "camera_make": "Apple",
        "camera_model": "iPhone 15 Pro",
        "lens_model": "iPhone 15 Pro back triple camera",
        "datetime_original": "2026-04-12T15:24:31",
        "gps_latitude": 37.775,
        "gps_longitude": -122.4194,
        "gps_altitude": 12.0,
        "orientation": 6,
        "photo_exposure": {
            "shutter_speed": "1/250",
            "aperture": 1.8,
            "iso": 400,
            "flash": "Auto, Fired",
            "focal_length": 6.9,
            "focal_length_35mm": 27,
        },
    }
    m = Media.from_dict_fast(data)
    assert m.camera_make == "Apple"
    assert m.camera_model == "iPhone 15 Pro"
    assert m.gps_latitude == 37.775
    assert m.orientation == 6
    assert isinstance(m.datetime_original, datetime)
    assert m.datetime_original == datetime(2026, 4, 12, 15, 24, 31)
    assert isinstance(m.photo_exposure, PhotoExposure)
    assert m.photo_exposure.iso == 400
    assert m.photo_exposure.shutter_speed == "1/250"


def test_media_from_dict_fast_without_photo_exif():
    """Existing AI-generated media without photo fields still loads."""
    from metascan.core.media import Media

    data = {
        "file_path": "/tmp/ai.png",
        "file_size": 100,
        "width": 512,
        "height": 512,
        "format": "PNG",
        "created_at": "2026-01-01T00:00:00",
        "modified_at": "2026-01-01T00:00:00",
    }
    m = Media.from_dict_fast(data)
    assert m.camera_make is None
    assert m.gps_latitude is None
    assert m.orientation is None
    assert m.photo_exposure is None
```

- [ ] **Step 3.2: Run to verify failure**

```bash
pytest tests/test_photo_exif.py -v -k from_dict_fast
```

Expected: FAIL — `Media` lacks the new fields.

- [ ] **Step 3.3: Add fields to `Media`**

Edit `metascan/core/media.py`:

After the `LoRA` dataclass and before `Media`, add `PhotoExposure`:

```python
@dataclass_json
@dataclass
class PhotoExposure:
    """Exposure / lens settings — serialized to media.photo_exposure JSON column."""

    shutter_speed: Optional[str] = None
    aperture: Optional[float] = None
    iso: Optional[int] = None
    flash: Optional[str] = None
    focal_length: Optional[float] = None
    focal_length_35mm: Optional[int] = None
```

In the `Media` dataclass field list, after `tags: List[str] = field(default_factory=list)` and before `loras:`, insert these new fields (keep them grouped logically):

```python
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    datetime_original: Optional[datetime] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_altitude: Optional[float] = None
    orientation: Optional[int] = None
    photo_exposure: Optional[PhotoExposure] = None
```

In `from_dict_fast` (around line 128), extend the `Media(...)` constructor call. First add datetime parsing for `datetime_original` near the other datetime parses (around line 121):

```python
        datetime_original = data.get("datetime_original")
        if isinstance(datetime_original, str):
            datetime_original = datetime.fromisoformat(datetime_original)
        elif isinstance(datetime_original, (int, float)):
            datetime_original = datetime.fromtimestamp(datetime_original)
```

Then add `PhotoExposure` parsing right above the `Media(...)` call:

```python
        photo_exposure = None
        pe = data.get("photo_exposure")
        if isinstance(pe, dict):
            photo_exposure = PhotoExposure(
                shutter_speed=pe.get("shutter_speed"),
                aperture=pe.get("aperture"),
                iso=pe.get("iso"),
                flash=pe.get("flash"),
                focal_length=pe.get("focal_length"),
                focal_length_35mm=pe.get("focal_length_35mm"),
            )
```

Then in the `Media(...)` kwargs (alongside the existing args, before `thumbnail_path=...`), append:

```python
            camera_make=data.get("camera_make"),
            camera_model=data.get("camera_model"),
            lens_model=data.get("lens_model"),
            datetime_original=datetime_original,
            gps_latitude=data.get("gps_latitude"),
            gps_longitude=data.get("gps_longitude"),
            gps_altitude=data.get("gps_altitude"),
            orientation=data.get("orientation"),
            photo_exposure=photo_exposure,
```

- [ ] **Step 3.4: Run the test to verify it passes**

```bash
pytest tests/test_photo_exif.py -v -k from_dict_fast
```

Expected: PASS.

- [ ] **Step 3.5: Run the full suite to verify no regressions**

```bash
make quality test
```

Expected: 175 + new tests pass (177-ish), flake8/black/mypy clean.

- [ ] **Step 3.6: Commit**

```bash
git add metascan/core/media.py tests/test_photo_exif.py
git commit -m "$(cat <<'EOF'
feat: add photo-EXIF fields to Media dataclass

Adds 9 new optional fields (camera_make, camera_model, lens_model,
datetime_original, gps_latitude, gps_longitude, gps_altitude, orientation,
photo_exposure) plus the PhotoExposure dataclass. from_dict_fast extended
to round-trip the new fields including datetime parsing and PhotoExposure
nested deserialization.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Database schema migration + indices

**Files:**
- Modify: `metascan/core/database_sqlite.py`
- Test: `tests/test_database_photo_columns.py` (new)

- [ ] **Step 4.1: Write the failing test file**

Create `tests/test_database_photo_columns.py`:

```python
"""Tests for the photo-EXIF columns added to the media table + filter indices."""

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media, PhotoExposure


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "test.db"
        manager = DatabaseManager(str(db_file))
        yield manager
        manager.close()


def _make_photo_media(path: str = "/tmp/IMG_001.HEIC") -> Media:
    return Media(
        file_path=Path(path),
        file_size=1234,
        width=3024,
        height=4032,
        format="HEIF",
        created_at=datetime(2026, 4, 12, 15, 24, 31),
        modified_at=datetime(2026, 4, 12, 15, 24, 31),
        camera_make="Apple",
        camera_model="iPhone 15 Pro",
        lens_model="iPhone 15 Pro back triple camera",
        datetime_original=datetime(2026, 4, 12, 15, 24, 31),
        gps_latitude=37.775,
        gps_longitude=-122.4194,
        gps_altitude=12.0,
        orientation=6,
        photo_exposure=PhotoExposure(
            shutter_speed="1/250", aperture=1.8, iso=400,
            flash="Auto, Fired", focal_length=6.9, focal_length_35mm=27,
        ),
    )


class TestSchema:
    def test_new_columns_exist(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
        for c in (
            "camera_make", "camera_model", "lens_model", "datetime_original",
            "gps_latitude", "gps_longitude", "gps_altitude", "orientation",
            "photo_exposure",
        ):
            assert c in cols, f"missing column {c}"

    def test_user_version_advances_to_2(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v >= 2

    def test_summary_indexes_include_new_columns(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            for idx in ("idx_media_summary_added", "idx_media_summary_modified"):
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (idx,),
                ).fetchone()
                assert row is not None, f"index {idx} missing"
                ddl = row[0]
                for col in ("camera_make", "camera_model", "datetime_original",
                            "gps_latitude", "gps_longitude", "orientation"):
                    assert col in ddl, f"{idx} missing column {col}"


class TestSaveAndLoadPhotoMedia:
    def test_round_trip_columns(self, db):
        m = _make_photo_media()
        assert db.save_media(m) is True

        with sqlite3.connect(str(db.db_file)) as conn:
            row = conn.execute(
                "SELECT camera_make, camera_model, lens_model, datetime_original, "
                "gps_latitude, gps_longitude, gps_altitude, orientation, photo_exposure "
                "FROM media WHERE file_path = ?",
                (str(m.file_path).replace("\\", "/"),),
            ).fetchone()
        assert row is not None
        (make, model, lens, dt, lat, lon, alt, ori, expo_json) = row
        assert make == "Apple"
        assert model == "iPhone 15 Pro"
        assert lens == "iPhone 15 Pro back triple camera"
        assert dt == "2026-04-12T15:24:31"
        assert lat == pytest.approx(37.775)
        assert lon == pytest.approx(-122.4194)
        assert alt == 12.0
        assert ori == 6
        # photo_exposure JSON
        import json
        expo = json.loads(expo_json)
        assert expo["iso"] == 400
        assert expo["shutter_speed"] == "1/250"

    def test_get_media_round_trip(self, db):
        m = _make_photo_media()
        db.save_media(m)
        loaded = db.get_media(m.file_path)
        assert loaded is not None
        assert loaded.camera_make == "Apple"
        assert loaded.gps_latitude == pytest.approx(37.775)
        assert loaded.orientation == 6
        assert loaded.photo_exposure is not None
        assert loaded.photo_exposure.iso == 400


class TestFilterIndices:
    def test_camera_make_indexed(self, db):
        db.save_media(_make_photo_media("/tmp/a.HEIC"))
        with sqlite3.connect(str(db.db_file)) as conn:
            rows = conn.execute(
                "SELECT index_key FROM indices "
                "WHERE index_type='camera_make' AND file_path=?",
                ("/tmp/a.HEIC",),
            ).fetchall()
        assert any(r[0] == "apple" for r in rows)

    def test_camera_model_indexed(self, db):
        db.save_media(_make_photo_media("/tmp/b.HEIC"))
        with sqlite3.connect(str(db.db_file)) as conn:
            rows = conn.execute(
                "SELECT index_key FROM indices "
                "WHERE index_type='camera_model' AND file_path=?",
                ("/tmp/b.HEIC",),
            ).fetchall()
        assert any(r[0] == "iphone 15 pro" for r in rows)

    def test_has_gps_indexed_when_present(self, db):
        db.save_media(_make_photo_media("/tmp/c.HEIC"))
        with sqlite3.connect(str(db.db_file)) as conn:
            rows = conn.execute(
                "SELECT index_key FROM indices "
                "WHERE index_type='has_gps' AND file_path=?",
                ("/tmp/c.HEIC",),
            ).fetchall()
        assert rows and rows[0][0] == "yes"

    def test_has_gps_not_emitted_when_absent(self, db):
        m = _make_photo_media("/tmp/d.JPG")
        m.gps_latitude = None
        m.gps_longitude = None
        db.save_media(m)
        with sqlite3.connect(str(db.db_file)) as conn:
            rows = conn.execute(
                "SELECT index_key FROM indices "
                "WHERE index_type='has_gps' AND file_path=?",
                ("/tmp/d.JPG",),
            ).fetchall()
        assert rows == []


class TestSummaryEndpoint:
    def test_summary_includes_new_fields(self, db):
        db.save_media(_make_photo_media("/tmp/e.HEIC"))
        rows = db.get_all_media_summaries()
        assert len(rows) == 1
        r = rows[0]
        assert r["camera_make"] == "Apple"
        assert r["camera_model"] == "iPhone 15 Pro"
        assert r["gps_latitude"] == pytest.approx(37.775)
        assert r["gps_longitude"] == pytest.approx(-122.4194)
        assert r["orientation"] == 6
        # datetime_original surfaced as ISO string
        assert "2026-04-12" in (r["datetime_original"] or "")
```

- [ ] **Step 4.2: Run to verify failures**

```bash
pytest tests/test_database_photo_columns.py -v
```

Expected: many failures (columns don't exist yet, indices not emitted, summaries don't carry new fields).

- [ ] **Step 4.3: Add columns + bump `user_version` to 2 + thumbnail-cache wipe**

Edit `metascan/core/database_sqlite.py`. Locate the `_init_database` block where existing column adds happen (after the `if "modified_at" not in columns:` migration; around line 167-189). After that block but **before** the `required_cols = ("modified_at", "created_at")` line, add the new column adds:

```python
            # Photo-EXIF columns (real-world photo support).
            self._ensure_column(
                conn, "media", "camera_make",
                "ALTER TABLE media ADD COLUMN camera_make TEXT",
            )
            self._ensure_column(
                conn, "media", "camera_model",
                "ALTER TABLE media ADD COLUMN camera_model TEXT",
            )
            self._ensure_column(
                conn, "media", "lens_model",
                "ALTER TABLE media ADD COLUMN lens_model TEXT",
            )
            self._ensure_column(
                conn, "media", "datetime_original",
                "ALTER TABLE media ADD COLUMN datetime_original TEXT",
            )
            self._ensure_column(
                conn, "media", "gps_latitude",
                "ALTER TABLE media ADD COLUMN gps_latitude REAL",
            )
            self._ensure_column(
                conn, "media", "gps_longitude",
                "ALTER TABLE media ADD COLUMN gps_longitude REAL",
            )
            self._ensure_column(
                conn, "media", "gps_altitude",
                "ALTER TABLE media ADD COLUMN gps_altitude REAL",
            )
            self._ensure_column(
                conn, "media", "orientation",
                "ALTER TABLE media ADD COLUMN orientation INTEGER",
            )
            self._ensure_column(
                conn, "media", "photo_exposure",
                "ALTER TABLE media ADD COLUMN photo_exposure TEXT",
            )
```

Update the `required_cols` tuple to include the new summary columns so the index-rebuild logic recreates `idx_media_summary_added` / `idx_media_summary_modified` if they're missing the new columns:

```python
            required_cols = (
                "modified_at", "created_at",
                "camera_make", "camera_model", "datetime_original",
                "gps_latitude", "gps_longitude", "orientation",
            )
```

Update the index DDL to include the new summary columns. Replace the `idx_media_summary_added` block:

```python
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_added
                ON media(
                    created_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    modified_at,
                    camera_make, camera_model, datetime_original,
                    gps_latitude, gps_longitude, orientation
                )
                """
            )
```

Replace the `idx_media_summary_modified` block:

```python
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_summary_modified
                ON media(
                    modified_at, file_path, is_favorite, playback_speed,
                    width, height, file_size, frame_rate, duration,
                    created_at,
                    camera_make, camera_model, datetime_original,
                    gps_latitude, gps_longitude, orientation
                )
                """
            )
```

Then immediately after the existing `if user_version < 1:` backfill block (which sets `PRAGMA user_version = 1`), add the v2 migration:

```python
            if user_version < 2:
                # Photo-EXIF support landed: orientation is now applied at
                # thumbnail-generation time. Existing thumbnails for sideways
                # iPhone photos would stay cached forever (key is
                # (path, mtime, size), and mtime hasn't changed). Wipe the
                # cache directory contents once so they regenerate correctly
                # on next view. Idempotent guard: only fire on first launch
                # with the v2 schema.
                from metascan.utils.app_paths import get_thumbnail_cache_dir

                try:
                    cache_dir = get_thumbnail_cache_dir()
                    if cache_dir.exists():
                        wiped = 0
                        for entry in cache_dir.iterdir():
                            if entry.is_file():
                                try:
                                    entry.unlink()
                                    wiped += 1
                                except OSError as exc:
                                    logger.warning(
                                        "Could not delete cached thumbnail "
                                        "%s: %s", entry, exc,
                                    )
                        logger.info(
                            "Wiped %d cached thumbnail(s) for v2 migration "
                            "(EXIF orientation handling).",
                            wiped,
                        )
                except Exception as exc:
                    logger.warning(
                        "Thumbnail cache wipe (v2 migration) failed: %s", exc,
                    )
                conn.execute("PRAGMA user_version = 2")
```

If `metascan/utils/app_paths.py` doesn't already export `get_thumbnail_cache_dir`, locate it and confirm it's exported. Run:

```bash
grep -n "thumbnail_cache_dir\|cache_dir\|thumbnails" /mnt/c/Users/jtkli/gws/metascan/metascan/utils/app_paths.py
```

If it doesn't exist, **stop** — read `metascan/cache/thumbnail.py` to see how the cache directory is resolved (likely via a constructor argument or app-paths helper) and adjust this migration to use the same source. The whole point is to wipe whatever directory `ThumbnailCache` actually writes into.

- [ ] **Step 4.4: Extend `_generate_indices`**

In `metascan/core/database_sqlite.py`, locate `_generate_indices` (around line 809) and add the new indices. After the existing `indices.append(("ext", media.file_extension, None))` line and before `# Add reverse index for the fully qualified file path`, add:

```python
        if media.camera_make:
            indices.append(("camera_make", media.camera_make.strip().lower(), None))
        if media.camera_model:
            indices.append(("camera_model", media.camera_model.strip().lower(), None))
        if media.gps_latitude is not None and media.gps_longitude is not None:
            indices.append(("has_gps", "yes", None))
```

- [ ] **Step 4.5: Extend the upsert SQL + params**

Update `_MEDIA_UPSERT_SQL`. Replace the constant with:

```python
    _MEDIA_UPSERT_SQL = """
        INSERT INTO media (
            file_path, data, is_favorite, playback_speed,
            width, height, file_size, frame_rate, duration, modified_at,
            camera_make, camera_model, lens_model, datetime_original,
            gps_latitude, gps_longitude, gps_altitude, orientation,
            photo_exposure,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(file_path) DO UPDATE SET
            data = excluded.data,
            is_favorite = excluded.is_favorite,
            playback_speed = excluded.playback_speed,
            width = excluded.width,
            height = excluded.height,
            file_size = excluded.file_size,
            frame_rate = excluded.frame_rate,
            duration = excluded.duration,
            modified_at = excluded.modified_at,
            camera_make = excluded.camera_make,
            camera_model = excluded.camera_model,
            lens_model = excluded.lens_model,
            datetime_original = excluded.datetime_original,
            gps_latitude = excluded.gps_latitude,
            gps_longitude = excluded.gps_longitude,
            gps_altitude = excluded.gps_altitude,
            orientation = excluded.orientation,
            photo_exposure = excluded.photo_exposure,
            updated_at = CURRENT_TIMESTAMP
    """
```

Update `_media_upsert_params`. Replace with:

```python
    @staticmethod
    def _media_upsert_params(media: Media, posix_path: str) -> tuple:
        import json as _json
        expo_json = None
        if media.photo_exposure is not None:
            expo_json = _json.dumps(
                {
                    "shutter_speed": media.photo_exposure.shutter_speed,
                    "aperture": media.photo_exposure.aperture,
                    "iso": media.photo_exposure.iso,
                    "flash": media.photo_exposure.flash,
                    "focal_length": media.photo_exposure.focal_length,
                    "focal_length_35mm": media.photo_exposure.focal_length_35mm,
                }
            )
        return (
            posix_path,
            media.to_json(),  # type: ignore[attr-defined]
            1 if media.is_favorite else 0,
            media.playback_speed,
            media.width,
            media.height,
            media.file_size,
            media.frame_rate,
            media.duration,
            media.modified_at.isoformat() if media.modified_at else None,
            media.camera_make,
            media.camera_model,
            media.lens_model,
            media.datetime_original.isoformat() if media.datetime_original else None,
            media.gps_latitude,
            media.gps_longitude,
            media.gps_altitude,
            media.orientation,
            expo_json,
        )
```

- [ ] **Step 4.6: Extend `get_all_media_summaries` SELECT**

Locate the SQL string in `get_all_media_summaries` (around line 533). Replace:

```python
        sql = (
            "SELECT file_path, is_favorite, playback_speed, "
            "width, height, file_size, frame_rate, duration, "
            "modified_at, created_at, "
            "camera_make, camera_model, datetime_original, "
            "gps_latitude, gps_longitude, orientation "
            f"FROM media {where} ORDER BY {order_clause}"
        )
```

In the per-row dict construction (around line 549), add the new keys:

```python
                    out.append(
                        {
                            "file_path": file_path,
                            "is_favorite": bool(row["is_favorite"]),
                            "is_video": ext in video_exts,
                            "playback_speed": (
                                float(playback) if playback is not None else None
                            ),
                            "width": row["width"],
                            "height": row["height"],
                            "file_size": row["file_size"],
                            "frame_rate": row["frame_rate"],
                            "duration": row["duration"],
                            "modified_at": row["modified_at"],
                            "created_at": row["created_at"],
                            "camera_make": row["camera_make"],
                            "camera_model": row["camera_model"],
                            "datetime_original": row["datetime_original"],
                            "gps_latitude": row["gps_latitude"],
                            "gps_longitude": row["gps_longitude"],
                            "orientation": row["orientation"],
                        }
                    )
```

- [ ] **Step 4.7: Run the new tests**

```bash
pytest tests/test_database_photo_columns.py -v
```

Expected: all PASS.

- [ ] **Step 4.8: Run the full suite**

```bash
make quality test
```

Expected: ~190 tests pass, lint clean.

- [ ] **Step 4.9: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_database_photo_columns.py
git commit -m "$(cat <<'EOF'
feat(db): persist photo-EXIF columns and filter indices

- Add 9 columns to media (camera/lens/datetime/GPS/orientation/exposure JSON)
- Extend covering indexes to include the 6 new summary columns
- _generate_indices emits camera_make / camera_model / has_gps
- get_all_media_summaries projects the new fields
- Upsert preserves photo fields on rescan, fires excluded-set update path
- user_version advances 1 -> 2; one-shot thumbnail cache wipe handles
  pre-existing sideways thumbnails

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire scanner to extract photo EXIF + apply orientation

**Files:**
- Modify: `metascan/core/scanner.py`
- Test: `tests/test_scanner_heic.py` (new)

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_scanner_heic.py`:

```python
"""End-to-end scanner tests for HEIC + EXIF orientation handling."""

import io
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

# Module under test will register pillow-heif on import.
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner


def _write_jpeg_with_orientation(path: Path, w: int = 100, h: int = 50,
                                 orientation: int = 6) -> None:
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
        db_file = Path(tmp) / "test.db"
        manager = DatabaseManager(str(db_file))
        yield manager, Path(tmp)
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
```

- [ ] **Step 5.2: Run to verify failures**

```bash
pytest tests/test_scanner_heic.py -v
```

Expected: failures — HEIC extensions not in `SUPPORTED_EXTENSIONS`, photo fields not populated, dims not transposed.

- [ ] **Step 5.3: Update Scanner**

Edit `metascan/core/scanner.py`. At the top, add the imports:

```python
from PIL import Image, ImageOps
```

(Replace the existing `from PIL import Image` line.)

Add the heic + photo_exif imports:

```python
from metascan.utils.heic import register_heif_opener
from metascan.core.photo_exif import extract_photo_exif

register_heif_opener()
```

Place those after the existing imports, before the `try: import ffmpeg` block.

Update `SUPPORTED_EXTENSIONS`:

```python
    SUPPORTED_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif",
        ".heic", ".heif",
        ".mp4", ".webm",
    }
```

Update `_get_media_info` (around line 213) to apply orientation and return display dims:

```python
    def _get_media_info(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        try:
            if file_path.suffix.lower() in {".mp4", ".webm"}:
                return self._get_video_info(file_path)
            else:
                with Image.open(file_path) as img:
                    transposed = ImageOps.exif_transpose(img)
                    return transposed.width, transposed.height, img.format
        except Exception as e:
            logger.error(f"Failed to get media info for {file_path}: {e}")
            return None, None, None
```

Now the scanner must also harvest photo EXIF and orientation. Locate the function that constructs the `Media` object during scan (in `_process_file` or equivalent — search for where `Media(` is instantiated). Run:

```bash
grep -n "Media(" /mnt/c/Users/jtkli/gws/metascan/metascan/core/scanner.py
```

Find the construction site. Just **before** the `Media(` call in `_process_file` (or wherever the per-image construction happens — there is likely one for images and a separate path for videos), open the file once with Pillow to harvest EXIF. The cleanest pattern is a new helper:

Add this private method to `Scanner`:

```python
    def _read_image_photo_exif(
        self, file_path: Path
    ) -> Tuple[Optional["PhotoExif"], Optional[int]]:
        """Open a still image once, return (photo_exif, orientation_tag)."""
        if file_path.suffix.lower() in {".mp4", ".webm"}:
            return None, None
        try:
            with Image.open(file_path) as img:
                return extract_photo_exif(img.getexif())
        except Exception as exc:
            logger.debug("Could not read EXIF from %s: %s", file_path, exc)
            return None, None
```

Add the type-only import at the top of the file alongside the other typing imports:

```python
from metascan.core.photo_exif import PhotoExif  # noqa: F401  (type hint only)
```

Now find where Media is constructed for images (NOT videos). Around line 207 there's `return media` — work back to the `Media(...)` call. Update that construction so it adds the photo-EXIF kwargs. Pattern:

```python
        photo_exif, orientation_tag = self._read_image_photo_exif(file_path)

        media = Media(
            file_path=file_path,
            file_size=file_size,
            width=width,
            height=height,
            format=fmt or "",
            created_at=created_at,
            modified_at=modified_at,
            # ...existing kwargs...
            camera_make=photo_exif.camera_make if photo_exif else None,
            camera_model=photo_exif.camera_model if photo_exif else None,
            lens_model=photo_exif.lens_model if photo_exif else None,
            datetime_original=photo_exif.datetime_original if photo_exif else None,
            gps_latitude=photo_exif.gps_latitude if photo_exif else None,
            gps_longitude=photo_exif.gps_longitude if photo_exif else None,
            gps_altitude=photo_exif.gps_altitude if photo_exif else None,
            orientation=orientation_tag,
            photo_exposure=(
                _photo_exposure_to_media(photo_exif.exposure)
                if photo_exif and photo_exif.exposure is not None
                else None
            ),
        )
```

Where `_photo_exposure_to_media` converts the `photo_exif.PhotoExposure` to the `media.PhotoExposure` (they have identical fields but live in different modules). Add the helper at module scope inside `scanner.py`:

```python
def _photo_exposure_to_media(src):  # type: ignore[no-untyped-def]
    """Convert metascan.core.photo_exif.PhotoExposure -> metascan.core.media.PhotoExposure.

    They have identical shape; lives in two modules so photo_exif stays pure
    (no Media/dataclass-json dependency)."""
    from metascan.core.media import PhotoExposure as MediaPhotoExposure

    return MediaPhotoExposure(
        shutter_speed=src.shutter_speed,
        aperture=src.aperture,
        iso=src.iso,
        flash=src.flash,
        focal_length=src.focal_length,
        focal_length_35mm=src.focal_length_35mm,
    )
```

If the existing `Media(...)` call lives in both `Scanner._process_file` and `ThreadedScanner` (or a worker), update both call sites — search for every `Media(` construction and apply the same pattern. Run:

```bash
grep -n "Media(" /mnt/c/Users/jtkli/gws/metascan/metascan/core/scanner.py
```

For each construction site that handles images (skip video-only paths), add the photo-EXIF kwargs.

- [ ] **Step 5.4: Run the new tests**

```bash
pytest tests/test_scanner_heic.py -v
```

Expected: all PASS.

- [ ] **Step 5.5: Run full suite**

```bash
make quality test
```

Expected: green.

- [ ] **Step 5.6: Commit**

```bash
git add metascan/core/scanner.py tests/test_scanner_heic.py
git commit -m "$(cat <<'EOF'
feat(scanner): extract photo EXIF, apply orientation, accept HEIC/HEIF

- Register pillow-heif at scanner module import
- .heic/.heif added to SUPPORTED_EXTENSIONS
- ImageOps.exif_transpose applied to determine display dims (so iPhone
  shots with Orientation=6 are stored as 3024x4032 portrait, not raw)
- Per-image extract_photo_exif call harvests camera/lens/exposure/GPS
  fields and the raw orientation tag
- Media construction populates the new fields

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Apply orientation in thumbnail cache + register HEIC

**Files:**
- Modify: `metascan/cache/thumbnail.py`
- Test: covered manually + via existing scan path

- [ ] **Step 6.1: Update thumbnail.py**

Edit `metascan/cache/thumbnail.py`. Update the imports near the top:

```python
from PIL import Image, ImageOps
```

(Replace the existing `from PIL import Image` line.)

Add the registration call right after the imports (before `_find_ffmpeg`):

```python
from metascan.utils.heic import register_heif_opener

register_heif_opener()
```

In `_create_image_thumbnail` (around line 141), apply `exif_transpose` immediately after `Image.open`. Replace:

```python
            with Image.open(image_path) as img:
                # Convert RGBA to RGB if necessary
```

with:

```python
            with Image.open(image_path) as img:
                img = ImageOps.exif_transpose(img)
                # Convert RGBA to RGB if necessary
```

Add `.heic`/`.heif` to `SUPPORTED_FORMATS` (locate the `SUPPORTED_FORMATS = {` block near the top of `ThumbnailCache`). Insert `".heic", ".heif",` alongside the other still-image extensions.

- [ ] **Step 6.2: Smoke test thumbnail generation**

Make a tiny throwaway script in your shell:

```bash
python <<'EOF'
import tempfile
from pathlib import Path
from PIL import Image
from metascan.cache.thumbnail import ThumbnailCache

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / "p.jpg"
    img = Image.new("RGB", (100, 50), color=(50, 100, 200))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90° CW
    img.save(src, "JPEG", exif=exif.tobytes())

    cache = ThumbnailCache(thumbnail_dir=Path(tmp) / "thumbs")
    out = cache.get_thumbnail_for(src) if hasattr(cache, "get_thumbnail_for") else cache._create_thumbnail(src, Path(tmp) / "thumbs" / "p.jpg")
    thumb = Image.open(out)
    print("thumb size", thumb.size)
    # After exif_transpose with orientation=6, width<height (portrait)
    assert thumb.size[1] >= thumb.size[0], "thumbnail not transposed"
    print("OK")
EOF
```

Expected: prints "OK". (If the cache API differs from what's in the script, adjust to whatever public method the codebase exposes — the assertion is the load-bearing part.)

- [ ] **Step 6.3: Run full suite**

```bash
make quality test
```

Expected: green.

- [ ] **Step 6.4: Commit**

```bash
git add metascan/cache/thumbnail.py
git commit -m "$(cat <<'EOF'
feat(thumbnails): apply EXIF orientation and accept HEIC/HEIF

ImageOps.exif_transpose runs before resize so iPhone shots aren't sideways
in the grid. SUPPORTED_FORMATS gains .heic/.heif. pillow-heif registered at
module import.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Apply orientation in CLIP embedding paths + register HEIC

**Files:**
- Modify: `metascan/core/embedding_manager.py`

- [ ] **Step 7.1: Update embedding_manager.py**

Edit `metascan/core/embedding_manager.py`. Update imports near the top:

```python
from PIL import Image, ImageOps
```

(Replace existing `from PIL import Image`.)

Add registration right after imports, at module scope:

```python
from metascan.utils.heic import register_heif_opener

register_heif_opener()
```

Find the two image-open sites:
1. `_load_and_downsize` around line 296: `image = Image.open(image_path).convert("RGB")` → wrap with `ImageOps.exif_transpose`.
2. The other site around line 481: `image = Image.open(image_path).convert("RGB")` → same wrap.

Replace both with:

```python
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
```

`ImageOps.exif_transpose` returns a *new* Image with the EXIF orientation applied; we `.convert("RGB")` on the result.

- [ ] **Step 7.2: Verify embedding tests still pass**

```bash
pytest tests/test_embedding_manager.py tests/test_embedding_pipeline.py -v
```

Expected: green.

- [ ] **Step 7.3: Smoke test live encode_image with a rotated JPEG**

Optional but valuable: write a one-shot script that loads a rotated JPEG, encodes it via `EmbeddingManager.compute_image_embedding`, and confirms it returns a non-None vector. Skip if you trust the existing tests.

- [ ] **Step 7.4: Commit**

```bash
git add metascan/core/embedding_manager.py
git commit -m "$(cat <<'EOF'
feat(embeddings): apply EXIF orientation and accept HEIC

CLIP encoders see the user-visible image (post-exif_transpose) so embeddings
align with what the grid shows. pillow-heif registered at module import so
HEIC files round-trip through both batch (embedding_worker) and live
(inference_worker) paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Apply orientation + HEIC support in upscaler

**Files:**
- Modify: `metascan/core/media_upscaler.py`

The upscaler reads via `cv2.imread`, which is BGR-numpy and ignores EXIF orientation. For HEIC support and for orientation correctness, replace each `cv2.imread` with a Pillow → numpy conversion.

- [ ] **Step 8.1: Add a Pillow-backed loader helper**

Edit `metascan/core/media_upscaler.py`. Near the top of the file (after existing imports), add:

```python
import numpy as np
from PIL import Image, ImageOps

from metascan.utils.heic import register_heif_opener

register_heif_opener()


def _imread_oriented_bgr(path):  # type: ignore[no-untyped-def]
    """cv2.imread replacement that respects EXIF orientation and reads HEIC.

    Returns a BGR numpy array (matching cv2.imread's convention) or None on
    failure, so existing call sites that check ``if img is None`` keep
    working without modification.
    """
    try:
        with Image.open(path) as pil_img:
            oriented = ImageOps.exif_transpose(pil_img).convert("RGB")
            rgb = np.asarray(oriented)
            return rgb[:, :, ::-1].copy()  # RGB -> BGR
    except Exception:
        return None
```

If `numpy as np` is already imported above, skip that line.

- [ ] **Step 8.2: Replace `cv2.imread` call sites**

Locate every `cv2.imread(str(...), cv2.IMREAD_COLOR)` and `cv2.imread(str(...))` site in `media_upscaler.py`. Replace each with `_imread_oriented_bgr(...)`.

For example, line 450:

```python
            input_img = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
```

becomes:

```python
            input_img = _imread_oriented_bgr(str(input_path))
```

Apply this rewrite to all sites. Confirm by:

```bash
grep -n "cv2.imread" /mnt/c/Users/jtkli/gws/metascan/metascan/core/media_upscaler.py
```

Expected after edit: no matches (or matches only on inter-frame video paths if those handle frames not source files — in those cases keep cv2.imread because frames don't have EXIF).

Decision rule: replace **only** the call sites that read **user source files**. Replace inter-frame reads (RIFE intermediate frames written by the upscaler itself, around lines 1152, 1153, 1176) with the original `cv2.imread` — those frames have no EXIF and don't benefit from the new path.

For the path around line 1361 (already uses `Image.open`), apply `ImageOps.exif_transpose` if the result is consumed by anything that needs upright orientation:

```python
                    with Image.open(file_path) as img:
                        img = ImageOps.exif_transpose(img)
                        # ...rest of the existing logic...
```

- [ ] **Step 8.3: Run the upscale tests**

```bash
pytest tests/test_upscale_api.py -v
```

Expected: green.

- [ ] **Step 8.4: Smoke-test upscaling a rotated JPEG (manual, optional)**

Through the running app, drag a portrait iPhone-orientation JPEG onto the upscaler and confirm the output is upright (not sideways). Skip this if you don't have a real upscale model installed locally — the unit tests cover the I/O path.

- [ ] **Step 8.5: Run full suite**

```bash
make quality test
```

Expected: green.

- [ ] **Step 8.6: Commit**

```bash
git add metascan/core/media_upscaler.py
git commit -m "$(cat <<'EOF'
feat(upscaler): apply EXIF orientation and accept HEIC sources

Replaces cv2.imread on user-source paths with a Pillow-backed loader that
honours EXIF orientation and decodes HEIC via pillow-heif. Inter-frame reads
(RIFE intermediate frames) keep cv2.imread since those have no EXIF.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Backend `/api/filters` integration test

**Files:**
- Test: `tests/test_filters_camera.py` (new)

The `/api/filters` endpoint already enumerates all `index_type` values present in the `indices` table — no code change needed once `_generate_indices` emits the new types. This task verifies that with a test.

- [ ] **Step 9.1: Write the test**

Create `tests/test_filters_camera.py`:

```python
"""Integration tests for /api/filters with camera buckets."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media, PhotoExposure


def _photo(path: str, make: str, model: str, gps: bool = True) -> Media:
    return Media(
        file_path=Path(path),
        file_size=100, width=10, height=10, format="JPEG",
        created_at=datetime.now(), modified_at=datetime.now(),
        camera_make=make, camera_model=model,
        gps_latitude=37.0 if gps else None,
        gps_longitude=-122.0 if gps else None,
    )


@pytest.fixture
def client_and_db(monkeypatch, tmp_path):
    db_file = tmp_path / "test.db"
    db = DatabaseManager(str(db_file))

    # Patch dependency injection so the running app uses our temp DB.
    from backend import dependencies
    monkeypatch.setattr(dependencies, "_db_singleton", db)
    monkeypatch.setattr(dependencies, "get_db", lambda: db)

    from backend.main import create_app
    app = create_app()
    yield TestClient(app), db
    db.close()


def test_filters_endpoint_returns_camera_buckets(client_and_db):
    client, db = client_and_db
    db.save_media(_photo("/tmp/a.jpg", "Apple", "iPhone 15 Pro", gps=True))
    db.save_media(_photo("/tmp/b.jpg", "Apple", "iPhone 14", gps=True))
    db.save_media(_photo("/tmp/c.jpg", "Canon", "EOS R5", gps=False))

    r = client.get("/api/filters")
    assert r.status_code == 200
    data = r.json()
    assert "camera_make" in data
    makes = {b["key"]: b["count"] for b in data["camera_make"]}
    assert makes.get("apple") == 2
    assert makes.get("canon") == 1
    models = {b["key"]: b["count"] for b in data["camera_model"]}
    assert models.get("iphone 15 pro") == 1
    assert models.get("iphone 14") == 1
    assert models.get("eos r5") == 1
    has_gps = {b["key"]: b["count"] for b in data["has_gps"]}
    assert has_gps.get("yes") == 2  # only the two with GPS coords


def test_filters_apply_narrows_by_camera_make(client_and_db):
    client, db = client_and_db
    db.save_media(_photo("/tmp/a.jpg", "Apple", "iPhone 15 Pro"))
    db.save_media(_photo("/tmp/b.jpg", "Canon", "EOS R5"))

    r = client.post("/api/filters/apply", json={"filters": {"camera_make": ["apple"]}})
    assert r.status_code == 200
    paths = set(r.json()["paths"])
    assert any("a.jpg" in p for p in paths)
    assert not any("b.jpg" in p for p in paths)
```

- [ ] **Step 9.2: Run the test**

```bash
pytest tests/test_filters_camera.py -v
```

Expected: PASS — the backend already pages filter buckets generically.

If it fails because of how the app is constructed (no `create_app` factory, etc.), look at how `tests/test_folders_api.py` builds its `TestClient` and follow that pattern instead.

- [ ] **Step 9.3: Commit**

```bash
git add tests/test_filters_camera.py
git commit -m "$(cat <<'EOF'
test: cover /api/filters camera_make / camera_model / has_gps buckets

Asserts the new index_type buckets surface with correct counts and that
filter application narrows by camera_make.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Backend config: `ui.map_tile_url`

**Files:**
- Modify: `backend/config.py`

The frontend will read `config.ui.map_tile_url` via the existing `/api/config` endpoint. Backend just needs to accept and pass-through the new key (the config is a free-form JSON dict, so this is mostly documentation + a sane default fallback in the loader if needed).

- [ ] **Step 10.1: Add a default getter**

Edit `backend/config.py`. After the existing `get_models_config` function, add:

```python
def get_ui_config(config: dict) -> dict:
    """UI section of config.json. Currently exposes:
        - map_tile_url: MapLibre GL style URL for the location panel.
                        Defaults to OpenFreeMap liberty.
    """
    ui = config.get("ui") or {}
    return {
        "map_tile_url": ui.get(
            "map_tile_url",
            "https://tiles.openfreemap.org/styles/liberty",
        ),
    }
```

The frontend can either read the raw config and apply the default itself, or call this helper through `/api/config`. The simpler path: have the frontend default in JS if `config.ui?.map_tile_url` is absent.

- [ ] **Step 10.2: Document the new key in CLAUDE.md**

Edit `CLAUDE.md`. Find the `### config.json keys managed by the Models tab` section. Above that section header, add a new section:

```markdown
### `config.json` keys for the location panel

```jsonc
{
  "ui": {
    "map_tile_url": "https://tiles.openfreemap.org/styles/liberty"
  }
}
```

Defaults to OpenFreeMap liberty if absent. Override to point MapLibre GL at any compatible style URL, including a self-hosted one.
```

- [ ] **Step 10.3: Commit**

```bash
git add backend/config.py CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(config): add ui.map_tile_url for the location panel

Defaults to OpenFreeMap liberty. Documented in CLAUDE.md alongside the
existing models-config section.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Frontend types — extend `Media` interface

**Files:**
- Modify: `frontend/src/types/media.ts`

- [ ] **Step 11.1: Add new fields**

Edit `frontend/src/types/media.ts`. Add the `PhotoExposure` interface above `Media`:

```typescript
export interface PhotoExposure {
  shutter_speed?: string | null
  aperture?: number | null
  iso?: number | null
  flash?: string | null
  focal_length?: number | null
  focal_length_35mm?: number | null
}
```

Inside the `Media` interface, in the summary-fields block (the part with `width`, `height`, etc.), add the summary photo fields:

```typescript
  // --- Photo summary fields (also returned by GET /api/media) ---
  camera_make?: string | null
  camera_model?: string | null
  datetime_original?: string | null
  gps_latitude?: number | null
  gps_longitude?: number | null
  orientation?: number | null
```

Inside the detail-only block, add the detail-only photo fields:

```typescript
  lens_model?: string | null
  gps_altitude?: number | null
  photo_exposure?: PhotoExposure | null
```

- [ ] **Step 11.2: Type-check**

```bash
cd frontend && npm run build
```

Expected: clean build, no TS errors. Cancel with Ctrl-C if it hangs after type-check; we only need the type-check to pass.

- [ ] **Step 11.3: Commit**

```bash
git add frontend/src/types/media.ts
git commit -m "$(cat <<'EOF'
feat(types): add photo-EXIF fields to Media + PhotoExposure interface

Mirrors the backend additions: camera/lens/datetime/GPS/orientation/exposure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Settings store — `mapTileUrl`

**Files:**
- Modify: `frontend/src/stores/settings.ts`

- [ ] **Step 12.1: Add the field + loader**

Edit `frontend/src/stores/settings.ts`. Add the ref and load it in `loadConfig`:

```typescript
  const mapTileUrl = ref('https://tiles.openfreemap.org/styles/liberty')

  async function loadConfig() {
    config.value = await fetchConfig()
    const t = config.value.theme as string | undefined
    if (t) theme.value = t.replace('.xml', '')
    const ts = config.value.thumbnail_size as [number, number] | undefined
    if (ts) {
      thumbnailSize.value = ts
      if (ts[0] <= 150) thumbnailSizeLabel.value = 'small'
      else if (ts[0] <= 250) thumbnailSizeLabel.value = 'medium'
      else thumbnailSizeLabel.value = 'large'
    }
    const ui = config.value.ui as { map_tile_url?: string } | undefined
    if (ui?.map_tile_url) mapTileUrl.value = ui.map_tile_url
  }
```

Add `mapTileUrl` to the returned object:

```typescript
  return {
    theme,
    thumbnailSizeLabel,
    thumbnailSize,
    mapTileUrl,
    config,
    loadConfig,
    setThumbnailSize,
    setTheme,
  }
```

- [ ] **Step 12.2: Type-check**

```bash
cd frontend && npm run build
```

Expected: clean.

- [ ] **Step 12.3: Commit**

```bash
git add frontend/src/stores/settings.ts
git commit -m "$(cat <<'EOF'
feat(settings): expose mapTileUrl for the location panel

Loaded from config.ui.map_tile_url; defaults to OpenFreeMap liberty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Install MapLibre GL JS

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`

- [ ] **Step 13.1: Install**

```bash
cd frontend && npm install maplibre-gl @types/maplibre-gl
```

Expected: dependencies added to `package.json`, `package-lock.json` updated. No native compile step.

- [ ] **Step 13.2: Verify build still passes**

```bash
cd frontend && npm run build
```

Expected: clean.

- [ ] **Step 13.3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "$(cat <<'EOF'
chore(frontend): add maplibre-gl + @types/maplibre-gl

Used by the new LocationSection in the metadata panel; loaded via dynamic
import so the ~200 KB doesn't enter the main bundle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `CameraSection.vue`

**Files:**
- Create: `frontend/src/components/metadata/CameraSection.vue`
- Create: `frontend/src/utils/orientation.ts` (small label helper)

- [ ] **Step 14.1: Create the orientation label helper**

Create `frontend/src/utils/orientation.ts`:

```typescript
const LABELS: Record<number, string> = {
  1: 'Landscape',
  2: 'Landscape (mirrored)',
  3: 'Landscape (rotated 180°)',
  4: 'Landscape (mirrored, rotated 180°)',
  5: 'Portrait (mirrored, rotated 270°)',
  6: 'Portrait',
  7: 'Portrait (mirrored, rotated 90°)',
  8: 'Portrait (rotated 270°)',
}

export function orientationLabel(value: number | null | undefined): string | null {
  if (value == null) return null
  return LABELS[value] ?? `Unknown orientation (${value})`
}
```

- [ ] **Step 14.2: Create CameraSection**

Create `frontend/src/components/metadata/CameraSection.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { Media } from '../../types/media'
import MetadataField from './MetadataField.vue'
import { orientationLabel } from '../../utils/orientation'

const props = defineProps<{ media: Media }>()

const cameraLabel = computed(() => {
  const make = props.media.camera_make ?? ''
  const model = props.media.camera_model ?? ''
  const joined = [make, model].filter(Boolean).join(' ')
  return joined || null
})

const focalLengthLabel = computed(() => {
  const fl = props.media.photo_exposure?.focal_length
  const fl35 = props.media.photo_exposure?.focal_length_35mm
  if (fl == null) return null
  if (fl35 != null) return `${fl} mm (35mm equiv. ${fl35} mm)`
  return `${fl} mm`
})

const dateTaken = computed(() => {
  const v = props.media.datetime_original
  if (!v) return null
  try { return new Date(v).toLocaleString() } catch { return v }
})

const visible = computed(() =>
  Boolean(
    props.media.camera_make ||
    props.media.camera_model ||
    props.media.lens_model ||
    props.media.datetime_original ||
    props.media.photo_exposure
  )
)
</script>

<template>
  <details v-if="visible" class="meta-section" open>
    <summary class="section-title">Camera</summary>
    <div class="section-body">
      <MetadataField v-if="cameraLabel" label="Camera" :value="cameraLabel" />
      <MetadataField v-if="media.lens_model" label="Lens" :value="media.lens_model" />
      <MetadataField v-if="dateTaken" label="Date taken" :value="dateTaken" />
      <MetadataField
        v-if="media.photo_exposure?.shutter_speed"
        label="Shutter"
        :value="`${media.photo_exposure.shutter_speed} s`"
      />
      <MetadataField
        v-if="media.photo_exposure?.aperture != null"
        label="Aperture"
        :value="`f/${media.photo_exposure.aperture}`"
      />
      <MetadataField
        v-if="media.photo_exposure?.iso != null"
        label="ISO"
        :value="`ISO ${media.photo_exposure.iso}`"
      />
      <MetadataField
        v-if="focalLengthLabel"
        label="Focal length"
        :value="focalLengthLabel"
      />
      <MetadataField
        v-if="media.photo_exposure?.flash"
        label="Flash"
        :value="media.photo_exposure.flash"
      />
      <MetadataField
        v-if="orientationLabel(media.orientation)"
        label="Orientation"
        :value="orientationLabel(media.orientation) ?? ''"
      />
    </div>
  </details>
</template>
```

- [ ] **Step 14.3: Type-check**

```bash
cd frontend && npm run build
```

Expected: clean.

- [ ] **Step 14.4: Commit**

```bash
git add frontend/src/components/metadata/CameraSection.vue frontend/src/utils/orientation.ts
git commit -m "$(cat <<'EOF'
feat(ui): add CameraSection metadata panel section

Shown when any photographic field is set (camera/lens/datetime/exposure).
Orientation alone does not trigger the section but appears as a row inside
when the section is shown for another reason.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `LocationSection.vue` (MapLibre)

**Files:**
- Create: `frontend/src/components/metadata/LocationSection.vue`

- [ ] **Step 15.1: Create the component**

Create `frontend/src/components/metadata/LocationSection.vue`:

```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Media } from '../../types/media'
import MetadataField from './MetadataField.vue'
import { useSettingsStore } from '../../stores/settings'
import { copyToClipboard } from '../../utils/clipboard'

// Lazy MapLibre import — keeps ~200 KB out of the main bundle.
type MaplibreModule = typeof import('maplibre-gl')

const props = defineProps<{ media: Media }>()
const settings = useSettingsStore()

const mapEl = ref<HTMLDivElement | null>(null)
const mapLoadFailed = ref(false)
let map: import('maplibre-gl').Map | null = null
let marker: import('maplibre-gl').Marker | null = null
let maplibre: MaplibreModule | null = null

const hasGps = computed(() =>
  props.media.gps_latitude != null && props.media.gps_longitude != null
)

const lat = computed(() => props.media.gps_latitude as number)
const lng = computed(() => props.media.gps_longitude as number)

const coordsLabel = computed(() => {
  const ns = lat.value >= 0 ? 'N' : 'S'
  const ew = lng.value >= 0 ? 'E' : 'W'
  return `${Math.abs(lat.value).toFixed(4)}° ${ns}, ${Math.abs(lng.value).toFixed(4)}° ${ew}`
})

const altitudeLabel = computed(() => {
  const a = props.media.gps_altitude
  if (a == null) return null
  if (a < 0) return `${Math.abs(a).toFixed(0)} m below sea level`
  return `${a.toFixed(0)} m above sea level`
})

const osmUrl = computed(() => {
  const lat4 = lat.value.toFixed(5)
  const lng4 = lng.value.toFixed(5)
  return `https://www.openstreetmap.org/?mlat=${lat4}&mlon=${lng4}#map=15/${lat4}/${lng4}`
})

async function ensureMap() {
  if (!mapEl.value || !hasGps.value) return
  if (map) return  // already initialised
  try {
    if (!maplibre) {
      maplibre = await import('maplibre-gl')
      // Side-effect import for default styles
      await import('maplibre-gl/dist/maplibre-gl.css')
    }
    map = new maplibre.Map({
      container: mapEl.value,
      style: settings.mapTileUrl,
      center: [lng.value, lat.value],
      zoom: 13,
      attributionControl: { compact: true },
    })
    map.scrollZoom.disable()
    mapEl.value.addEventListener('mouseenter', () => map?.scrollZoom.enable())
    mapEl.value.addEventListener('mouseleave', () => map?.scrollZoom.disable())
    marker = new maplibre.Marker().setLngLat([lng.value, lat.value]).addTo(map)
  } catch (e) {
    console.warn('MapLibre failed to load:', e)
    mapLoadFailed.value = true
  }
}

watch(
  () => [hasGps.value, lat.value, lng.value, mapEl.value] as const,
  async ([gpsOk, la, lo, el]) => {
    if (!gpsOk || !el) return
    if (!map) {
      await ensureMap()
      return
    }
    map.flyTo({ center: [lo as number, la as number], zoom: 13, duration: 600 })
    if (marker) marker.setLngLat([lo as number, la as number])
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  marker?.remove()
  map?.remove()
  marker = null
  map = null
})

async function copyCoords() {
  await copyToClipboard(`${lat.value},${lng.value}`)
}
</script>

<template>
  <details v-if="hasGps" class="meta-section" open>
    <summary class="section-title">Location</summary>
    <div class="section-body location-body">
      <div v-if="!mapLoadFailed" ref="mapEl" class="map-canvas" />
      <div v-else class="map-fallback">
        Map unavailable — showing coordinates only.
      </div>
      <MetadataField label="Coordinates" :value="coordsLabel" />
      <MetadataField v-if="altitudeLabel" label="Altitude" :value="altitudeLabel" />
      <div class="map-actions">
        <a class="map-link" :href="osmUrl" target="_blank" rel="noopener">
          Open in OpenStreetMap ↗
        </a>
        <button class="copy-coords-btn" @click="copyCoords">Copy coords</button>
      </div>
    </div>
  </details>
</template>

<style scoped>
.location-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.map-canvas {
  width: 100%;
  height: 220px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-200, #1a1a1a);
}
.map-fallback {
  padding: 24px 12px;
  text-align: center;
  font-size: 0.9em;
  color: var(--text-color-secondary, #888);
  background: var(--surface-200, #1a1a1a);
  border-radius: 6px;
}
.map-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.map-link {
  font-size: 0.9em;
}
.copy-coords-btn {
  font-size: 0.85em;
  padding: 2px 8px;
  cursor: pointer;
}
</style>
```

- [ ] **Step 15.2: Type-check**

```bash
cd frontend && npm run build
```

Expected: clean. If TS complains about `import('maplibre-gl/dist/maplibre-gl.css')` (CSS import in TS), add to `frontend/src/env.d.ts` (or wherever module declarations live):

```typescript
declare module '*.css'
```

If `frontend/src/env.d.ts` already has `/// <reference types="vite/client" />` it likely already accepts CSS imports — only add the declaration if the build complains.

- [ ] **Step 15.3: Commit**

```bash
git add frontend/src/components/metadata/LocationSection.vue
# Possibly: git add frontend/src/env.d.ts
git commit -m "$(cat <<'EOF'
feat(ui): add LocationSection metadata panel section with MapLibre map

Renders only when GPS lat+lng are present. MapLibre + its CSS imported
dynamically so the bundle stays slim. Scroll-zoom only on hover (so
trackpad scroll through the metadata panel doesn't accidentally zoom).
Marker and camera fly across selection changes instead of recreating the
map. Falls back to plain coordinates + OSM link if the dynamic import fails
(network/CDN outage).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Wire Camera + Location into `MetadataPanel.vue` and reorder Tags

**Files:**
- Modify: `frontend/src/components/metadata/MetadataPanel.vue`

- [ ] **Step 16.1: Import + register sections**

At the top of `MetadataPanel.vue`'s `<script setup>` block, add the imports:

```typescript
import CameraSection from './CameraSection.vue'
import LocationSection from './LocationSection.vue'
```

- [ ] **Step 16.2: Reorder sections in template**

Edit the `<template>` block. Move the existing `<!-- Tags -->` `<details>` block (currently the last section) to **immediately after** the existing `<!-- In Folders -->` block (just below `</details>` of the In Folders section, before the File Information section).

Insert `<CameraSection :media="media" />` after the Properties section's closing `</details>` and before AI Generation:

```vue
      <CameraSection :media="media" />
      <LocationSection :media="media" />
```

Final section order inside the `<template v-if="media">`:
1. `<!-- In Folders -->` (existing)
2. `<!-- Tags -->` (moved up)
3. `<!-- File Information -->` (existing)
4. `<!-- Image/Video Properties -->` (existing)
5. `<CameraSection />` (new)
6. `<LocationSection />` (new)
7. `<!-- AI Generation -->` (existing)
8. `<!-- LoRAs -->` (existing)

- [ ] **Step 16.3: Verify in dev server**

```bash
cd frontend && npm run dev
```

In the running app, select a real-world JPEG with EXIF and confirm the Camera + Location sections render in the right place. Select an AI-generated PNG and confirm those sections are absent. Stop the dev server (Ctrl-C) when done.

- [ ] **Step 16.4: Commit**

```bash
git add frontend/src/components/metadata/MetadataPanel.vue
git commit -m "$(cat <<'EOF'
feat(ui): integrate Camera + Location sections; move Tags to top

Section order now:
  In folders -> Tags -> File -> Properties -> Camera -> Location ->
  AI Generation -> LoRAs

Camera and Location only render when their data is present, so AI images
remain visually unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Add filter sidebar buckets

**Files:**
- Modify: `frontend/src/components/filters/FilterPanel.vue`

- [ ] **Step 17.1: Extend filterSections**

Edit `frontend/src/components/filters/FilterPanel.vue`. Update the `filterSections` array:

```typescript
const filterSections = [
  { type: 'camera_make', label: 'Camera Make' },
  { type: 'camera_model', label: 'Camera Model' },
  { type: 'has_gps', label: 'Has Location' },
  { type: 'model', label: 'Model' },
  { type: 'lora', label: 'LoRA' },
  { type: 'tag', label: 'Tags' },
]
```

(Camera-related sections appear above existing AI sections so a real-world photo library surfaces them first.)

- [ ] **Step 17.2: Verify in dev server**

```bash
cd frontend && npm run dev
```

In the running app, scan a directory with iPhone JPEGs/HEICs. Confirm Camera Make / Camera Model / Has Location buckets appear and clicking one filters the grid correctly.

- [ ] **Step 17.3: Commit**

```bash
git add frontend/src/components/filters/FilterPanel.vue
git commit -m "$(cat <<'EOF'
feat(ui): add Camera Make / Camera Model / Has Location filter sections

Buckets are populated by the existing index_type-driven filters API; the
backend already groups indices.camera_make/camera_model/has_gps so this is
just a config addition.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Final validation

**Files:** none (verification only)

- [ ] **Step 18.1: Backend quality + tests**

```bash
make quality test
```

Expected: ~190 tests pass, flake8/black/mypy clean.

- [ ] **Step 18.2: Frontend build**

```bash
cd frontend && npm run build
```

Expected: clean type-check, build succeeds.

- [ ] **Step 18.3: Manual smoke checklist**

Start the app:

```bash
# terminal 1
source venv/bin/activate && python run_server.py

# terminal 2
cd frontend && npm run dev
```

Walk the checklist:
- [ ] Scan a directory containing one HEIC file. It appears in the grid right-side-up.
- [ ] Open it; Camera section shows make/model/lens/exposure/datetime/orientation.
- [ ] Open a photo with GPS; Location section renders MapLibre map with marker.
- [ ] Pan and zoom map. Scroll-wheel zoom works only when hovering the map.
- [ ] Click "Open in OpenStreetMap" — opens the right pin in a new tab.
- [ ] Select a different photo; map flies to new coords without flicker.
- [ ] Select an AI-generated PNG (no EXIF) — Camera + Location sections are absent.
- [ ] Filter sidebar shows Camera Make / Camera Model / Has Location buckets with correct counts.
- [ ] Click "iPhone 15 Pro" under Camera Model — grid filters down to those photos.
- [ ] Click an iPhone shot pre-existing in the library before this work — confirm thumbnail is correctly oriented (cache wipe migration removed the sideways one).

If any item fails, file it as a follow-up rather than blocking merge unless it's the orientation correctness or HEIC ingestion (those are spec-load-bearing).

- [ ] **Step 18.4: Commit a completion marker (optional)**

If you'd like to leave a marker that the manual checklist passed, an empty commit is fine — but only if useful for review. Otherwise skip.

---

## Self-Review (run by the plan author after writing)

- ✅ Spec section 1 (Goals) — all five goals covered (Tasks 1-17 collectively).
- ✅ Spec §2 (Architecture) — boundaries match Tasks 1, 2, 4, 5-8 (backend) and 11-17 (frontend).
- ✅ Spec §3 (Data model) — Media fields added in Task 3, schema migration in Task 4.
- ✅ Spec §4 (Scan pipeline) — Task 5 (scanner), Task 6 (thumbnails), Task 7 (embeddings), Task 8 (upscaler).
- ✅ Spec §5 (Thumbnail cache invalidation) — included in Task 4 v2 migration.
- ✅ Spec §6 (UI: MetadataPanel) — Task 14 (Camera), Task 15 (Location), Task 16 (wire-up + reorder).
- ✅ Spec §7 (Filter sidebar) — Task 9 (backend test) + Task 17 (frontend config).
- ✅ Spec §8-9 (Error handling, edge cases) — covered by per-field try/except in Task 2 implementation, GPS sanity guards in Task 2, MapLibre fallback in Task 15.
- ✅ Spec §10 (Testing) — Tasks 2, 4, 5, 9 hold the new test files.
- ✅ Spec §11 (Cross-platform) — `pillow-heif` and `maplibre-gl` choices in Tasks 1 + 13 are the cross-platform-friendly options.
- ✅ Spec §12 (Configuration) — Task 1 (`pillow-heif`), Task 10 (`ui.map_tile_url`), Task 13 (`maplibre-gl`).
- ✅ Spec §13 (Out of scope) — none of these appear in the plan.

Type / signature consistency:
- `extract_photo_exif` returns `tuple[PhotoExif | None, int | None]` in spec, Task 2 implementation, and Task 5 caller — consistent.
- `PhotoExif.exposure` is `Optional[PhotoExposure]` (from `metascan.core.photo_exif`); `Media.photo_exposure` is the same shape but lives on `metascan.core.media`. Task 5 has an explicit `_photo_exposure_to_media` helper to bridge — consistent.
- New media columns (9): `camera_make`, `camera_model`, `lens_model`, `datetime_original`, `gps_latitude`, `gps_longitude`, `gps_altitude`, `orientation`, `photo_exposure` — same set in spec §3, Task 3 dataclass, Task 4 schema. Consistent.
- Inverted index types (3): `camera_make`, `camera_model`, `has_gps` — same set in spec §7, Task 4 `_generate_indices`, Task 9 test. Consistent.

No `TBD`, `TODO`, "appropriate error handling", or similar placeholders in any task.
