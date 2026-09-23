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

    def test_stores_and_returns_dimensions(self):
        self.db.create_i2v_video(
            source_path="/lib/src.png",
            file_path="/lib/out1.mp4",
            width=1328,
            height=752,
        )
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(rows[0]["width"], 1328)
        self.assertEqual(rows[0]["height"], 752)

    def test_stores_and_returns_steps_and_render_time(self):
        self.db.create_i2v_video(
            source_path="/lib/src.png",
            file_path="/lib/out1.mp4",
            steps=30,
            render_s=247.5,
        )
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertEqual(rows[0]["steps"], 30)
        self.assertEqual(rows[0]["render_s"], 247.5)

    def test_steps_and_render_time_default_to_null(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertIsNone(rows[0]["steps"])
        self.assertIsNone(rows[0]["render_s"])

    def test_dimensions_default_to_null(self):
        self.db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/out1.mp4")
        rows = self.db.list_i2v_videos("/lib/src.png")
        self.assertIsNone(rows[0]["width"])
        self.assertIsNone(rows[0]["height"])

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


class TestI2vFormState(unittest.TestCase):
    """form_state is the editable copy; the rendered facts never move."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        _seed_media(self.db, ["/lib/src.png", "/lib/out1.mp4"])
        self.form = {
            "idea": "a cat",
            "prompt": "p",
            "duration_s": 6.0,
            "quality": "quality",
            "megapixels": 0.75,
            "steps": 30,
            "seed": 42,
            "loras": [{"name": "a.safetensors", "strength": 0.8}],
        }

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _create(self, **kw):
        return self.db.create_i2v_video(
            source_path="/lib/src.png",
            file_path="/lib/out1.mp4",
            prompt_used="p",
            seed=42,
            **kw,
        )

    def test_ingest_values_round_trip_as_parsed_json(self):
        self._create(megapixels=0.75, loras=self.form["loras"], form_state=self.form)
        row = self.db.list_i2v_videos("/lib/src.png")[0]
        self.assertEqual(row["megapixels"], 0.75)
        self.assertEqual(row["loras"], self.form["loras"])
        self.assertEqual(row["form_state"], self.form)

    def test_absent_values_are_none_not_strings(self):
        self._create()
        row = self.db.list_i2v_videos("/lib/src.png")[0]
        self.assertIsNone(row["form_state"])
        self.assertIsNone(row["loras"])
        self.assertIsNone(row["megapixels"])

    def test_get_i2v_video(self):
        vid = self._create(form_state=self.form)
        row = self.db.get_i2v_video(vid)
        self.assertEqual(row["form_state"], self.form)
        self.assertEqual(row["source_path"], "/lib/src.png")
        self.assertIsNone(self.db.get_i2v_video(vid + 99))

    def test_set_form_state_leaves_the_rendered_facts_alone(self):
        vid = self._create(form_state=self.form)
        edited = {**self.form, "prompt": "edited", "seed": 7}

        self.assertTrue(self.db.set_i2v_video_form_state(vid, edited))

        row = self.db.get_i2v_video(vid)
        self.assertEqual(row["form_state"], edited)
        self.assertEqual(row["prompt_used"], "p")
        self.assertEqual(row["seed"], 42)

    def test_set_form_state_on_a_missing_video(self):
        self.assertFalse(self.db.set_i2v_video_form_state(12345, self.form))

    def test_a_corrupt_stored_form_state_reads_as_none(self):
        vid = self._create()
        with self.db.lock, self.db._get_connection() as conn:
            conn.execute(
                "UPDATE i2v_videos SET form_state = ? WHERE id = ?", ("{not json", vid)
            )
            conn.commit()
        self.assertIsNone(self.db.get_i2v_video(vid)["form_state"])


class TestI2vFormStateColumnUpgrade(unittest.TestCase):
    def test_columns_are_added_to_a_database_that_predates_them(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            first = DatabaseManager(Path(tmp))
            _seed_media(first, ["/lib/src.png", "/lib/old.mp4"])
            first.create_i2v_video(
                source_path="/lib/src.png", file_path="/lib/old.mp4", seed=1
            )
            first.close()

            # Rebuild the table the way it looked before this feature.
            conn = sqlite3.connect(str(Path(tmp) / "metascan.db"))
            for column in ("form_state", "loras", "megapixels"):
                conn.execute(f"ALTER TABLE i2v_videos DROP COLUMN {column}")
            conn.commit()
            conn.close()

            reopened = DatabaseManager(Path(tmp))
            try:
                row = reopened.list_i2v_videos("/lib/src.png")[0]
                self.assertEqual(row["seed"], 1)
                self.assertIsNone(row["form_state"])
                self.assertTrue(
                    reopened.set_i2v_video_form_state(row["id"], {"idea": "x"})
                )
            finally:
                reopened.close()
