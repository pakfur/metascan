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
        # Sub-second exposures: prefer "1/N" form when the rational's
        # numerator is exactly 1 (the universal camera-firmware convention).
        # Anything else: decimal seconds rounded to 2 places per spec §4.
        num = getattr(v, "numerator", None)
        den = getattr(v, "denominator", None)
        if num == 1 and isinstance(den, int) and den > 1:
            return f"1/{den}"
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
    except Exception as exc:
        logger.debug("Failed to read GPS altitude: %s", exc)
        return None
    try:
        if ref is not None and int(ref) == 1:
            alt = -alt
    except Exception as exc:
        logger.debug("Failed to apply GPS altitude ref: %s", exc)
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
            if (lat == 0.0 and lon == 0.0) or abs(lat) > 90.0 or abs(lon) > 180.0:
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
