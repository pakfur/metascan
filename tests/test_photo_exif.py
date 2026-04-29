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


def _make_exif(
    main: Dict[int, Any] | None = None,
    exif_ifd: Dict[int, Any] | None = None,
    gps_ifd: Dict[int, Any] | None = None,
) -> Image.Exif:
    """Build an Image.Exif populated for tests.

    Pillow 10.2.0's ``Image.Exif.get_ifd(tag)`` returns a fresh empty dict
    when the IFD hasn't been loaded from a file — and doesn't write it back
    to ``_ifds`` — so mutations on the returned dict are silently discarded.
    Pre-populating ``_ifds[tag] = {}`` makes ``get_ifd`` return the stored
    dict, which is the same approach Pillow's own test suite uses.
    """
    exif = Image.Exif()
    if main:
        for k, v in main.items():
            exif[k] = v
    if exif_ifd:
        exif._ifds[IFD.Exif] = {}  # noqa: SLF001  Pillow 10.2.0 workaround
        sub = exif.get_ifd(IFD.Exif)
        for k, v in exif_ifd.items():
            sub[k] = v
    if gps_ifd:
        exif._ifds[IFD.GPSInfo] = {}  # noqa: SLF001  Pillow 10.2.0 workaround
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
        assert _dms_to_decimal(
            (Fraction(0), Fraction(0), Fraction(60))
        ) == pytest.approx(1.0 / 60.0)

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
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1, "Landscape"),
            (2, "Landscape (mirrored)"),
            (3, "Landscape (rotated 180°)"),
            (4, "Landscape (mirrored, rotated 180°)"),
            (5, "Portrait (mirrored, rotated 270°)"),
            (6, "Portrait"),
            (7, "Portrait (mirrored, rotated 90°)"),
            (8, "Portrait (rotated 270°)"),
        ],
    )
    def test_known_values(self, value: int, expected: str):
        assert decode_orientation_label(value) == expected

    def test_unknown_value(self):
        assert decode_orientation_label(99) == "Unknown orientation (99)"


# ----------------------------------------------------------------------
# Flash decoding
# ----------------------------------------------------------------------


class TestFlashDecode:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0x00, "Off, Did not fire"),
            (0x01, "Fired"),
            (0x09, "On, Fired"),
            (0x10, "Off, Did not fire (compulsory)"),
            (0x18, "Auto, Did not fire"),
            (0x19, "Auto, Fired"),
        ],
    )
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
                0x010F: "Apple",  # Make
                0x0110: "iPhone 15 Pro",  # Model
                0x0112: 6,  # Orientation (portrait)
            },
            exif_ifd={
                0x9003: "2026:04:12 15:24:31",  # DateTimeOriginal
                0x829A: (
                    Fraction(1, 250),
                ),  # ExposureTime — some cameras nest in tuple
                0x829D: Fraction(18, 10),  # FNumber = 1.8
                0x8827: 400,  # ISOSpeedRatings
                0x9209: 0x19,  # Flash = "Auto, Fired"
                0x920A: Fraction(686, 100),  # FocalLength = 6.86mm
                0xA405: 27,  # FocalLengthIn35mmFilm
                0xA434: "iPhone 15 Pro back triple camera 6.86mm f/1.78",
            },
            gps_ifd={
                1: "N",  # GPSLatitudeRef
                2: (Fraction(37), Fraction(46), Fraction(30)),  # GPSLatitude
                3: "W",  # GPSLongitudeRef
                4: (Fraction(122), Fraction(25), Fraction(10)),  # GPSLongitude
                5: 0,  # GPSAltitudeRef (above)
                6: Fraction(12),  # GPSAltitude
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
                1: "N",
                2: (Fraction(0), Fraction(0), Fraction(0)),
                3: "E",
                4: (Fraction(0), Fraction(0), Fraction(0)),
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
                1: "N",
                2: (Fraction(100), Fraction(0), Fraction(0)),
                3: "W",
                4: (Fraction(50), Fraction(0), Fraction(0)),
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
                1: "S",
                2: (Fraction(34), Fraction(0), Fraction(0)),
                3: "W",
                4: (Fraction(58), Fraction(0), Fraction(0)),
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
                1: "N",
                2: (Fraction(36), Fraction(0), Fraction(0)),
                3: "W",
                4: (Fraction(116), Fraction(0), Fraction(0)),
                5: 1,  # ref=1 -> below sea level
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
