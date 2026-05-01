"""Unit tests for DatabaseManager folder CRUD."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=1,
        height=1,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


class TestFolderCrud(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        # Seed two media rows so folder_items FKs resolve.
        self.db.save_media(_media("/lib/a.png"))
        self.db.save_media(_media("/lib/b.png"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_manual_with_items(self):
        rec = self.db.create_folder("f_1", "manual", "Refs", items=["/lib/a.png"])
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec["kind"], "manual")
        self.assertEqual(rec["items"], ["/lib/a.png"])
        self.assertEqual(rec["count"], 1)
        self.assertIsNone(rec.get("rules"))

    def test_create_smart_with_rules(self):
        rules = {
            "match": "all",
            "conditions": [{"field": "favorite", "op": "is", "value": True}],
        }
        rec = self.db.create_folder("s_1", "smart", "Favs", rules=rules)
        assert rec is not None
        self.assertEqual(rec["kind"], "smart")
        self.assertEqual(rec["rules"], rules)
        self.assertNotIn("items", rec)

    def test_create_invalid_kind(self):
        with self.assertRaises(ValueError):
            self.db.create_folder("x", "weird", "x")

    def test_duplicate_id_returns_none(self):
        self.assertIsNotNone(self.db.create_folder("f_1", "manual", "A"))
        self.assertIsNone(self.db.create_folder("f_1", "manual", "B"))

    def test_list_folders_ordering(self):
        self.db.create_folder("f_2", "manual", "Later")
        self.db.create_folder("f_1", "manual", "First")
        self.db.create_folder("s_1", "smart", "Smart")
        out = self.db.list_folders()
        # kind asc, then sort_order, then created_at
        kinds = [r["kind"] for r in out]
        self.assertEqual(kinds, ["manual", "manual", "smart"])

    def test_update_name_and_icon(self):
        self.db.create_folder("f_1", "manual", "Old")
        before = self.db.get_folder("f_1")
        assert before is not None
        self.db.update_folder("f_1", name="New", icon="pi-star")
        after = self.db.get_folder("f_1")
        assert after is not None
        self.assertEqual(after["name"], "New")
        self.assertEqual(after["icon"], "pi-star")
        self.assertGreaterEqual(after["updated_at"], before["updated_at"])

    def test_update_rules_on_smart(self):
        self.db.create_folder(
            "s_1",
            "smart",
            "Favs",
            rules={"match": "all", "conditions": []},
        )
        new_rules = {
            "match": "any",
            "conditions": [{"field": "type", "op": "is", "value": "video"}],
        }
        rec = self.db.update_folder("s_1", rules=new_rules)
        assert rec is not None
        self.assertEqual(rec["rules"], new_rules)

    def test_update_missing_returns_none(self):
        self.assertIsNone(self.db.update_folder("nope", name="x"))

    def test_delete_folder(self):
        self.db.create_folder("f_1", "manual", "A", items=["/lib/a.png"])
        self.assertTrue(self.db.delete_folder("f_1"))
        self.assertIsNone(self.db.get_folder("f_1"))
        # folder_items should cascade away.
        with self.db._get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM folder_items WHERE folder_id=?",
                ("f_1",),
            ).fetchone()["n"]
        self.assertEqual(n, 0)

    def test_delete_missing_returns_false(self):
        self.assertFalse(self.db.delete_folder("nope"))

    def test_add_items_dedupes(self):
        self.db.create_folder("f_1", "manual", "A")
        first = self.db.add_folder_items("f_1", ["/lib/a.png", "/lib/b.png"])
        self.assertEqual(first, 2)
        # Re-adding /lib/a.png should not double-count.
        second = self.db.add_folder_items("f_1", ["/lib/a.png", "/lib/b.png"])
        self.assertEqual(second, 0)
        rec = self.db.get_folder("f_1")
        assert rec is not None
        self.assertEqual(rec["count"], 2)

    def test_add_items_on_smart_returns_none(self):
        self.db.create_folder(
            "s_1", "smart", "Smart", rules={"match": "all", "conditions": []}
        )
        self.assertIsNone(self.db.add_folder_items("s_1", ["/lib/a.png"]))

    def test_add_items_missing_folder_returns_none(self):
        self.assertIsNone(self.db.add_folder_items("nope", ["/lib/a.png"]))

    def test_remove_items(self):
        self.db.create_folder("f_1", "manual", "A", items=["/lib/a.png", "/lib/b.png"])
        removed = self.db.remove_folder_items("f_1", ["/lib/a.png"])
        self.assertEqual(removed, 1)
        rec = self.db.get_folder("f_1")
        assert rec is not None
        self.assertEqual(rec["items"], ["/lib/b.png"])

    def test_remove_items_missing_paths_noop(self):
        self.db.create_folder("f_1", "manual", "A", items=["/lib/a.png"])
        self.assertEqual(self.db.remove_folder_items("f_1", ["/never/touched.png"]), 0)

    def test_cascade_on_media_delete(self):
        self.db.create_folder("f_1", "manual", "A", items=["/lib/a.png", "/lib/b.png"])
        self.db.delete_media(Path("/lib/a.png"))
        rec = self.db.get_folder("f_1")
        assert rec is not None
        self.assertEqual(rec["items"], ["/lib/b.png"])


if __name__ == "__main__":
    unittest.main()
