"""Tests for the photo-EXIF columns added to the media table + filter indices."""

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
        manager = DatabaseManager(db_file)
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
            shutter_speed="1/250",
            aperture=1.8,
            iso=400,
            flash="Auto, Fired",
            focal_length=6.9,
            focal_length_35mm=27,
        ),
    )


class TestSchema:
    def test_new_columns_exist(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
        for c in (
            "camera_make",
            "camera_model",
            "lens_model",
            "datetime_original",
            "gps_latitude",
            "gps_longitude",
            "gps_altitude",
            "orientation",
            "photo_exposure",
        ):
            assert c in cols, f"missing column {c}"

    def test_user_version_advances_to_2(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v >= 2

    def test_v1_to_v2_migration_adds_columns_and_advances_version(self, tmp_path):
        """A pre-existing v1 DB (without EXIF columns) gets columns added
        and user_version bumped to 2 on the next _init_database call."""
        # DatabaseManager treats its argument as a directory and opens
        # <arg>/metascan.db — build the v1 DB at that exact path.
        db_dir = tmp_path / "v1migration"
        db_dir.mkdir()
        db_file = db_dir / "metascan.db"
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "CREATE TABLE media (file_path TEXT PRIMARY KEY, data TEXT, "
                "is_favorite INTEGER, playback_speed REAL, "
                "width INTEGER, height INTEGER, file_size INTEGER, "
                "frame_rate REAL, duration REAL, modified_at TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO media (file_path, data, is_favorite, playback_speed, "
                "width, height, file_size) VALUES "
                "('/tmp/legacy.jpg', '{}', 0, NULL, 100, 100, 1000)"
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()

        # Now construct a DatabaseManager — _init_database runs and should
        # add the EXIF columns + bump user_version to 2.
        manager = DatabaseManager(db_dir)

        with sqlite3.connect(str(manager.db_file)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
            for c in (
                "camera_make",
                "camera_model",
                "lens_model",
                "datetime_original",
                "gps_latitude",
                "gps_longitude",
                "gps_altitude",
                "orientation",
                "photo_exposure",
            ):
                assert c in cols, f"missing column {c}"
            v = conn.execute("PRAGMA user_version").fetchone()[0]
            assert v >= 2, f"expected user_version >= 2, got {v}"
            # Pre-existing row preserved
            row = conn.execute(
                "SELECT file_path, width, height FROM media WHERE file_path = ?",
                ("/tmp/legacy.jpg",),
            ).fetchone()
            assert row is not None
            assert row[0] == "/tmp/legacy.jpg"
            assert row[1] == 100
            # New EXIF columns are NULL on the pre-existing row
            row = conn.execute(
                "SELECT camera_make, gps_latitude FROM media WHERE file_path = ?",
                ("/tmp/legacy.jpg",),
            ).fetchone()
            assert row[0] is None
            assert row[1] is None

        manager.close()

    def test_summary_indexes_include_new_columns(self, db):
        with sqlite3.connect(str(db.db_file)) as conn:
            for idx in ("idx_media_summary_added", "idx_media_summary_modified"):
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (idx,),
                ).fetchone()
                assert row is not None, f"index {idx} missing"
                ddl = row[0]
                for col in (
                    "camera_make",
                    "camera_model",
                    "datetime_original",
                    "gps_latitude",
                    "gps_longitude",
                    "orientation",
                ):
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


class TestMediaToDictPhotoFields:
    def test_media_to_dict_includes_photo_exif_fields(self):
        """media_to_dict must mirror media_to_summary_dict's photo-EXIF
        coverage, plus the 3 detail-only fields. Without this, the detail
        endpoint silently drops camera/lens/exposure/GPS data, and the
        frontend's Camera and Location sections vanish when
        fetchMediaDetails resolves."""
        from backend.services.media_service import MediaService

        # media_to_dict only touches the Media argument — no DB/cache calls.
        service = MediaService(db=None, thumbnail_cache=None)  # type: ignore[arg-type]

        m = _make_photo_media("/tmp/regtest.HEIC")
        out = service.media_to_dict(m)

        assert out["camera_make"] == "Apple"
        assert out["camera_model"] == "iPhone 15 Pro"
        assert out["lens_model"] == "iPhone 15 Pro back triple camera"
        assert out["datetime_original"] == "2026-04-12T15:24:31"
        assert out["gps_latitude"] == pytest.approx(37.775)
        assert out["gps_longitude"] == pytest.approx(-122.4194)
        assert out["gps_altitude"] == 12.0
        assert out["orientation"] == 6
        assert isinstance(out["photo_exposure"], dict)
        assert out["photo_exposure"]["iso"] == 400
        assert out["photo_exposure"]["shutter_speed"] == "1/250"
        assert out["photo_exposure"]["aperture"] == pytest.approx(1.8)
        assert out["photo_exposure"]["flash"] == "Auto, Fired"
        assert out["photo_exposure"]["focal_length"] == pytest.approx(6.9)
        assert out["photo_exposure"]["focal_length_35mm"] == 27
