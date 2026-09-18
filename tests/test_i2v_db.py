"""DB tests for the i2v_videos table and generation_jobs.i2v_source_path."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


def _seed_media(db, paths):
    for p in paths:
        db.save_media(
            Media(
                file_path=Path(p),
                file_size=1,
                width=8,
                height=8,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )


class TestI2vVideosDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        _seed_media(self.db, ["/lib/src.png", "/lib/out1.mp4", "/lib/out2.mp4"])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_create_and_list(self):
        vid = self.db.create_i2v_video(
            source_path="/lib/src.png",
            file_path="/lib/out1.mp4",
            prompt_used="p",
            idea="a cat",
            seed=42,
            duration_s=6.0,
            quality="fast",
        )
        self.assertIsInstance(vid, int)
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seed"], 42)
        self.assertEqual(rows[0]["quality"], "fast")
        self.assertFalse(rows[0]["is_favorite"])
        # POSIX in, native out
        self.assertIn("out1.mp4", rows[0]["file_path"])

    def test_list_newest_first(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out2.mp4")
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertIn("out2.mp4", rows[0]["file_path"])

    def test_list_prunes_rows_whose_media_is_gone(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out2.mp4")
        self.db.delete_media(Path("/lib/out1.mp4"))  # library delete
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(len(rows), 1)
        # pruned for real, not just filtered
        rows2 = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(len(rows2), 1)

    def test_favorite_flows_through(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        self.db.set_favorite("/lib/out1.mp4", True)
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertTrue(rows[0]["is_favorite"])

    def test_delete_video_purges_media(self):
        vid = self.db.create_i2v_video(
            source_path="/lib/src.png", file_path="/lib/out1.mp4"
        )
        deleted, purged = self.db.delete_i2v_video(vid)
        self.assertTrue(deleted)
        self.assertEqual(len(purged), 1)
        self.assertIsNone(self.db.get_media(Path("/lib/out1.mp4")))
        self.assertEqual(self.db.list_i2v_videos("/lib/src.png"), [])

    def test_delete_missing_video(self):
        self.assertEqual(self.db.delete_i2v_video(9999), (False, []))

    def test_source_delete_keeps_videos(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        self.db.delete_media(Path("/lib/src.png"))
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(len(rows), 1)


class TestGenerationJobsI2vColumn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        self.preset_id = self.db.create_workflow_preset(
            "p", "ref2v", "{}", "{}", video_target=None, video_mode=None
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_i2v_source_path_round_trip(self):
        job_id = self.db.create_generation_job(
            self.preset_id, "{}", i2v_source_path="/lib/src.png"
        )
        job = self.db.get_generation_job(job_id)
        self.assertEqual(job["i2v_source_path"], "/lib/src.png")

    def test_default_is_null(self):
        job_id = self.db.create_generation_job(self.preset_id, "{}")
        self.assertIsNone(self.db.get_generation_job(job_id)["i2v_source_path"])
