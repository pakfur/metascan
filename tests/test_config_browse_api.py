"""GET /api/config/browse -- the server-side directory picker's listing."""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import config as config_api


class TestBrowse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "beta").mkdir()
        (self.root / "Alpha").mkdir()
        (self.root / ".hidden").mkdir()
        (self.root / "file.txt").write_text("x")
        app = FastAPI()
        app.include_router(config_api.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_lists_only_visible_directories_sorted(self):
        body = self.client.get(
            "/api/config/browse", params={"path": str(self.root)}
        ).json()
        self.assertEqual(body["path"], str(self.root))
        self.assertEqual(body["parent"], str(self.root.parent))
        self.assertEqual([d["name"] for d in body["dirs"]], ["Alpha", "beta"])
        self.assertEqual(body["dirs"][0]["path"], str(self.root / "Alpha"))

    def test_no_path_starts_at_home(self):
        body = self.client.get("/api/config/browse").json()
        self.assertEqual(body["path"], str(Path.home().resolve()))

    def test_filesystem_root_has_no_parent(self):
        anchor = Path(self.root.anchor)
        body = self.client.get(
            "/api/config/browse", params={"path": str(anchor)}
        ).json()
        self.assertIsNone(body["parent"])

    def test_missing_directory_is_404(self):
        resp = self.client.get(
            "/api/config/browse", params={"path": str(self.root / "nope")}
        )
        self.assertEqual(resp.status_code, 404)

    def test_a_file_is_400(self):
        resp = self.client.get(
            "/api/config/browse", params={"path": str(self.root / "file.txt")}
        )
        self.assertEqual(resp.status_code, 400)

    def test_relative_path_is_400(self):
        resp = self.client.get("/api/config/browse", params={"path": "some/dir"})
        self.assertEqual(resp.status_code, 400)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX perms")
    def test_unreadable_directory_is_403(self):
        locked = self.root / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            resp = self.client.get("/api/config/browse", params={"path": str(locked)})
            self.assertEqual(resp.status_code, 403)
        finally:
            locked.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
