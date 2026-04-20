"""REST tests for /api/folders."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _seed_media(db: DatabaseManager, paths) -> None:
    for p in paths:
        db.save_media(
            Media(
                file_path=Path(p),
                file_size=1,
                width=1,
                height=1,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )


class TestFoldersApi(unittest.TestCase):
    tmp: Optional[tempfile.TemporaryDirectory] = None

    @classmethod
    def setUpClass(cls):
        # The test app mustn't serve the built frontend — that branch
        # hangs when frontend/dist doesn't match an API route.
        os.environ.setdefault("METASCAN_API_KEY", "")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db = DatabaseManager(self.data_dir)
        _seed_media(self.db, ["/lib/a.png", "/lib/b.png", "/lib/c.png"])
        # Patch the singleton accessor so the app uses our temp DB.
        self._patches = [
            patch(
                "backend.dependencies.get_data_dir",
                return_value=self.data_dir,
            ),
            patch("backend.dependencies._db_singleton", None, create=False),
        ]
        for p in self._patches:
            p.start()
        # Also force-reset the module-level singleton because the patch
        # above only replaces the attribute at assertion time.
        import backend.dependencies as deps

        deps._db_singleton = self.db  # type: ignore[attr-defined]

        from backend.api import folders as folders_api
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(folders_api.router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for p in self._patches:
            p.stop()
        import backend.dependencies as deps

        deps._db_singleton = None  # type: ignore[attr-defined]
        assert self.tmp is not None
        self.tmp.cleanup()

    def test_create_and_list_manual(self):
        resp = self.client.post(
            "/api/folders",
            json={
                "id": "f_1",
                "kind": "manual",
                "name": "Refs",
                "items": ["/lib/a.png", "/lib/b.png"],
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        rec = resp.json()
        self.assertEqual(rec["kind"], "manual")
        self.assertEqual(rec["count"], 2)

        listed = self.client.get("/api/folders").json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], "f_1")

    def test_create_smart(self):
        rules = {
            "match": "all",
            "conditions": [{"field": "favorite", "op": "is", "value": True}],
        }
        resp = self.client.post(
            "/api/folders",
            json={
                "id": "s_1",
                "kind": "smart",
                "name": "Favs",
                "rules": rules,
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        rec = resp.json()
        self.assertEqual(rec["rules"], rules)
        self.assertNotIn("items", rec)

    def test_create_smart_with_items_rejected(self):
        resp = self.client.post(
            "/api/folders",
            json={
                "id": "s_1",
                "kind": "smart",
                "name": "x",
                "items": ["/lib/a.png"],
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_duplicate_id_returns_409(self):
        self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "A"},
        )
        resp = self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "B"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_patch_name(self):
        self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "Old"},
        )
        resp = self.client.patch("/api/folders/f_1", json={"name": "New"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "New")

    def test_patch_rules_on_manual_rejected(self):
        self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "Old"},
        )
        resp = self.client.patch(
            "/api/folders/f_1",
            json={"rules": {"match": "all", "conditions": []}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_delete(self):
        self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "A"},
        )
        resp = self.client.delete("/api/folders/f_1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "deleted"})
        self.assertEqual(self.client.get("/api/folders").json(), [])
        self.assertEqual(self.client.delete("/api/folders/f_1").status_code, 404)

    def test_add_and_remove_items(self):
        self.client.post(
            "/api/folders",
            json={"id": "f_1", "kind": "manual", "name": "A"},
        )
        r1 = self.client.post(
            "/api/folders/f_1/items",
            json={"paths": ["/lib/a.png", "/lib/b.png"]},
        )
        self.assertEqual(r1.json(), {"added": 2})
        # Idempotent add.
        r2 = self.client.post("/api/folders/f_1/items", json={"paths": ["/lib/a.png"]})
        self.assertEqual(r2.json(), {"added": 0})
        r3 = self.client.request(
            "DELETE",
            "/api/folders/f_1/items",
            json={"paths": ["/lib/a.png"]},
        )
        self.assertEqual(r3.json(), {"removed": 1})

    def test_items_on_smart_rejected(self):
        self.client.post(
            "/api/folders",
            json={
                "id": "s_1",
                "kind": "smart",
                "name": "x",
                "rules": {"match": "all", "conditions": []},
            },
        )
        resp = self.client.post(
            "/api/folders/s_1/items",
            json={"paths": ["/lib/a.png"]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_items_missing_folder_404(self):
        resp = self.client.post(
            "/api/folders/nope/items",
            json={"paths": ["/lib/a.png"]},
        )
        self.assertEqual(resp.status_code, 404)

    def test_cascade_on_media_delete(self):
        self.client.post(
            "/api/folders",
            json={
                "id": "f_1",
                "kind": "manual",
                "name": "A",
                "items": ["/lib/a.png", "/lib/b.png"],
            },
        )
        # Simulate the media row going away.
        self.db.delete_media(Path("/lib/a.png"))
        rec = self.client.get("/api/folders").json()[0]
        self.assertEqual(rec["items"], ["/lib/b.png"])


if __name__ == "__main__":
    unittest.main()
