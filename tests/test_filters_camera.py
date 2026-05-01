"""Integration tests for /api/filters with camera buckets."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _photo(path: str, make: str, model: str, gps: bool = True) -> Media:
    return Media(
        file_path=Path(path),
        file_size=100,
        width=10,
        height=10,
        format="JPEG",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        camera_make=make,
        camera_model=model,
        gps_latitude=37.0 if gps else None,
        gps_longitude=-122.0 if gps else None,
    )


class TestFiltersCameraApi(unittest.TestCase):
    tmp: Optional[tempfile.TemporaryDirectory] = None

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("METASCAN_API_KEY", "")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db = DatabaseManager(self.data_dir)
        self.thumbnail_cache = ThumbnailCache(self.data_dir / "thumbs")

        self._patches = [
            patch("backend.dependencies.get_data_dir", return_value=self.data_dir),
            patch("backend.dependencies._db_singleton", None, create=False),
            patch(
                "backend.dependencies._thumbnail_cache_singleton", None, create=False
            ),
        ]
        for p in self._patches:
            p.start()

        import backend.dependencies as deps

        deps._db_singleton = self.db  # type: ignore[attr-defined]
        deps._thumbnail_cache_singleton = self.thumbnail_cache  # type: ignore[attr-defined]

        from backend.api import filters as filters_api
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(filters_api.router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for p in self._patches:
            p.stop()
        import backend.dependencies as deps

        deps._db_singleton = None  # type: ignore[attr-defined]
        deps._thumbnail_cache_singleton = None  # type: ignore[attr-defined]
        assert self.tmp is not None
        self.tmp.cleanup()

    def test_filters_endpoint_returns_camera_buckets(self):
        self.db.save_media(_photo("/tmp/a.jpg", "Apple", "iPhone 15 Pro", gps=True))
        self.db.save_media(_photo("/tmp/b.jpg", "Apple", "iPhone 14", gps=True))
        self.db.save_media(_photo("/tmp/c.jpg", "Canon", "EOS R5", gps=False))

        r = self.client.get("/api/filters")
        self.assertEqual(r.status_code, 200)
        data = r.json()

        self.assertIn("camera_make", data)
        makes = {b["key"]: b["count"] for b in data["camera_make"]}
        self.assertEqual(makes.get("apple"), 2)
        self.assertEqual(makes.get("canon"), 1)

        self.assertIn("camera_model", data)
        models = {b["key"]: b["count"] for b in data["camera_model"]}
        self.assertEqual(models.get("iphone 15 pro"), 1)
        self.assertEqual(models.get("iphone 14"), 1)
        self.assertEqual(models.get("eos r5"), 1)

        self.assertIn("has_gps", data)
        has_gps = {b["key"]: b["count"] for b in data["has_gps"]}
        self.assertEqual(has_gps.get("yes"), 2)

    def test_filters_apply_narrows_by_camera_make(self):
        self.db.save_media(_photo("/tmp/a.jpg", "Apple", "iPhone 15 Pro"))
        self.db.save_media(_photo("/tmp/b.jpg", "Canon", "EOS R5"))

        r = self.client.post(
            "/api/filters/apply",
            json={"filters": {"camera_make": ["apple"]}},
        )
        self.assertEqual(r.status_code, 200)
        paths = set(r.json()["paths"])
        self.assertTrue(any("a.jpg" in p for p in paths))
        self.assertFalse(any("b.jpg" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
