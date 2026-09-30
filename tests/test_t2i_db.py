"""DB tests for the t2i_images table and generation_jobs.t2i_batch_id."""

import inspect
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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


OUT = [f"/lib/t2i/out{n}.png" for n in range(1, 6)]

FACTS = {
    "batch_id": "b" * 32,
    "model": "krea2",
    "preset_id": 7,
    "caption": "a red kite over a gray sea",
    "prompt_used": "A red kite flies over a gray sea.",
    "negative_used": "blurry",
    "seed": 1234,
    "prompt_seed": 1230,
    "width": 1232,
    "height": 816,
    "megapixels": 1.0,
    "aspect_ratio": "3:2",
    "loras": [{"name": "kite.safetensors", "strength": 0.7}],
    "render_s": 41.5,
    "comfy_prompt_id": "p-1",
    "form_state": {"mode": "manual", "prompt": "A red kite flies over a gray sea."},
}


class TestT2iImagesDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        _seed_media(self.db, OUT)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _create(self, n, **kw):
        return self.db.create_t2i_image(file_path=OUT[n - 1], **kw)

    def test_the_table_has_the_spec_columns_and_no_foreign_keys(self):
        with self.db._get_connection() as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(t2i_images)")]
            foreign = conn.execute("PRAGMA foreign_key_list(t2i_images)").fetchall()
        self.assertEqual(
            columns,
            [
                "id",
                "file_path",
                "batch_id",
                "model",
                "preset_id",
                "caption",
                "prompt_used",
                "negative_used",
                "seed",
                "prompt_seed",
                "width",
                "height",
                "megapixels",
                "aspect_ratio",
                "loras",
                "render_s",
                "comfy_prompt_id",
                "created_at",
                "form_state",
            ],
        )
        self.assertEqual(foreign, [])

    def test_a_file_path_is_unique(self):
        self._create(1)
        with self.assertRaises(sqlite3.IntegrityError):
            self._create(1)

    def test_create_and_list(self):
        image_id = self._create(1, batch_id="abc", model="krea2", seed=42)
        self.assertIsInstance(image_id, int)
        rows = self.db.list_t2i_images()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], image_id)
        self.assertEqual(row["seed"], 42)
        self.assertEqual(row["model"], "krea2")
        self.assertEqual(row["batch_id"], "abc")
        self.assertIs(row["is_favorite"], False)
        self.assertEqual(row["file_name"], "out1.png")
        self.assertIn("out1.png", row["file_path"])  # native path
        self.assertNotIn("media_path", row)

    def test_the_path_is_stored_posix_and_returned_native(self):
        _seed_media(self.db, ["/mnt/c/lib/t2i/win.png"])
        self.db.create_t2i_image(file_path="C:\\lib\\t2i\\win.png")
        with self.db._get_connection() as conn:
            stored = conn.execute(
                "SELECT file_path FROM t2i_images WHERE file_path LIKE '%win.png'"
            ).fetchone()[0]
        self.assertEqual(stored, "/mnt/c/lib/t2i/win.png")
        row = self.db.list_t2i_images()[0]
        self.assertEqual(row["file_name"], "win.png")
        self.assertTrue(row["file_path"].endswith("win.png"))

    def test_every_fact_round_trips_as_parsed_json(self):
        image_id = self._create(1, **FACTS)
        row = self.db.get_t2i_image(image_id)
        for key, value in FACTS.items():
            self.assertEqual(row[key], value, key)
        self.assertTrue(row["created_at"])
        listed = self.db.list_t2i_images()[0]
        self.assertEqual(listed["loras"], FACTS["loras"])
        self.assertEqual(listed["form_state"], FACTS["form_state"])

    def test_absent_values_are_none_not_strings(self):
        row = self.db.get_t2i_image(self._create(1))
        for key in FACTS:
            self.assertIsNone(row[key], key)
        self.assertTrue(row["created_at"])

    def test_get_has_the_same_shape_as_a_list_row(self):
        image_id = self._create(1, **FACTS)
        self.db.set_favorite(OUT[0], True)
        got = self.db.get_t2i_image(image_id)
        listed = self.db.list_t2i_images()[0]
        self.assertEqual(got, listed)
        self.assertIs(got["is_favorite"], True)
        self.assertEqual(got["file_name"], "out1.png")

    def test_get_a_missing_image(self):
        self.assertIsNone(self.db.get_t2i_image(9999))

    def test_get_does_not_prune_a_row_whose_media_is_gone(self):
        image_id = self._create(1)
        self.db.delete_media(Path(OUT[0]))
        row = self.db.get_t2i_image(image_id)
        self.assertIsNotNone(row)
        self.assertIs(row["is_favorite"], False)

    def test_list_is_newest_first(self):
        ids = [self._create(n) for n in (1, 2, 3)]
        rows = self.db.list_t2i_images()
        self.assertEqual([r["id"] for r in rows], ids[::-1])

    def test_list_limit_and_before_id_page_through_everything(self):
        ids = [self._create(n) for n in range(1, 6)]
        first = self.db.list_t2i_images(limit=2)
        self.assertEqual([r["id"] for r in first], [ids[4], ids[3]])
        second = self.db.list_t2i_images(limit=2, before_id=first[-1]["id"])
        self.assertEqual([r["id"] for r in second], [ids[2], ids[1]])
        third = self.db.list_t2i_images(limit=2, before_id=second[-1]["id"])
        self.assertEqual([r["id"] for r in third], [ids[0]])
        self.assertEqual(self.db.list_t2i_images(limit=2, before_id=ids[0]), [])

    def test_the_default_page_is_sixty_rows(self):
        self.assertEqual(
            inspect.signature(self.db.list_t2i_images).parameters["limit"].default, 60
        )

    def test_a_non_positive_limit_lists_nothing(self):
        self._create(1)
        self.assertEqual(self.db.list_t2i_images(limit=0), [])
        self.assertEqual(self.db.list_t2i_images(limit=-3), [])

    def test_list_prunes_rows_whose_media_is_gone(self):
        self._create(1)
        self._create(2)
        self.db.delete_media(Path(OUT[0]))  # library delete
        rows = self.db.list_t2i_images()
        self.assertEqual([r["file_name"] for r in rows], ["out2.png"])
        # pruned for real, not just filtered
        with self.db._get_connection() as conn:
            left = conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0]
        self.assertEqual(left, 1)

    def test_a_page_is_still_full_when_the_newest_rows_are_dangling(self):
        # The strip decides whether an "older" page exists by whether this
        # one came back full, so pruning must not shorten it.
        ids = [self._create(n) for n in range(1, 6)]
        self.db.delete_media(Path(OUT[4]))
        self.db.delete_media(Path(OUT[3]))
        page = self.db.list_t2i_images(limit=2)
        self.assertEqual([r["id"] for r in page], [ids[2], ids[1]])
        older = self.db.list_t2i_images(limit=2, before_id=page[-1]["id"])
        self.assertEqual([r["id"] for r in older], [ids[0]])
        with self.db._get_connection() as conn:
            left = conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0]
        self.assertEqual(left, 3)

    def test_a_page_of_only_dangling_rows_is_empty_and_prunes_them(self):
        self._create(1)
        self._create(2)
        self.db.delete_media(Path(OUT[0]))
        self.db.delete_media(Path(OUT[1]))
        self.assertEqual(self.db.list_t2i_images(), [])
        with self.db._get_connection() as conn:
            left = conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0]
        self.assertEqual(left, 0)

    def test_favorite_flows_through(self):
        self._create(1)
        self.assertIs(self.db.list_t2i_images()[0]["is_favorite"], False)
        self.db.set_favorite(OUT[0], True)
        self.assertIs(self.db.list_t2i_images()[0]["is_favorite"], True)

    def test_list_paths_is_native_ordered_and_read_only(self):
        self.assertEqual(self.db.list_t2i_paths(), [])
        self._create(1)
        self._create(2)
        self.db.create_t2i_image(file_path="/lib/t2i/gone.png")  # no media row
        paths = self.db.list_t2i_paths()
        self.assertEqual(len(paths), 2)
        self.assertTrue(paths[0].endswith("out1.png"))
        self.assertTrue(paths[1].endswith("out2.png"))
        # the read never prunes; list_t2i_images owns that
        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0]
        self.assertEqual(rows, 3)

    def test_junk_json_reads_as_none(self):
        image_id = self._create(1)
        with self.db.lock, self.db._get_connection() as conn:
            conn.execute(
                "UPDATE t2i_images SET loras = ?, form_state = ? WHERE id = ?",
                ("{not json", "[1, 2]", image_id),
            )
            conn.commit()
        row = self.db.get_t2i_image(image_id)
        self.assertIsNone(row["loras"])  # unparseable
        self.assertIsNone(row["form_state"])  # parseable but not an object
        with self.db.lock, self.db._get_connection() as conn:
            conn.execute(
                "UPDATE t2i_images SET loras = ? WHERE id = ?", ('{"a": 1}', image_id)
            )
            conn.commit()
        self.assertIsNone(self.db.get_t2i_image(image_id)["loras"])  # not a list

    def test_set_form_state_leaves_the_rendered_facts_alone(self):
        image_id = self._create(1, **FACTS)
        edited = {**FACTS["form_state"], "prompt": "edited", "seed": 7}

        self.assertTrue(self.db.set_t2i_image_form_state(image_id, edited))

        row = self.db.get_t2i_image(image_id)
        self.assertEqual(row["form_state"], edited)
        for key, value in FACTS.items():
            if key != "form_state":
                self.assertEqual(row[key], value, key)

    def test_set_form_state_on_a_missing_image(self):
        self.assertFalse(self.db.set_t2i_image_form_state(12345, {"mode": "manual"}))

    def test_form_state_is_the_only_thing_that_can_be_updated(self):
        source = inspect.getsource(DatabaseManager)
        self.assertEqual(
            re.findall(r"UPDATE t2i_images SET (\w+)", source), ["form_state"]
        )
        writers = [
            name
            for name in dir(DatabaseManager)
            if "t2i_image" in name
            and name.split("_")[0] in ("set", "update", "patch", "edit")
        ]
        self.assertEqual(writers, ["set_t2i_image_form_state"])

    def test_delete_purges_the_row_and_the_media_and_returns_native_paths(self):
        image_id = self._create(1)
        self._create(2)
        deleted, purged = self.db.delete_t2i_image(image_id)
        self.assertTrue(deleted)
        self.assertEqual(len(purged), 1)
        self.assertTrue(purged[0].endswith("out1.png"))
        self.assertIsNone(self.db.get_media(Path(OUT[0])))
        self.assertIsNone(self.db.get_t2i_image(image_id))
        self.assertEqual(
            [r["file_name"] for r in self.db.list_t2i_images()], ["out2.png"]
        )

    def test_delete_cascades_the_media_rows_folder_membership(self):
        image_id = self._create(1)
        self.db.create_folder("f_1", "manual", "Renders", items=[OUT[0], OUT[1]])
        self.db.delete_t2i_image(image_id)
        self.assertEqual(self.db.get_folder("f_1")["items"], [OUT[1]])

    def test_delete_missing_image(self):
        self.assertEqual(self.db.delete_t2i_image(9999), (False, []))

    def test_delete_keeps_a_file_a_storyboard_still_references(self):
        # A scene's reference picture may be a generated image; the media FK
        # would null that reference, so the file is released, not purged.
        board = self.db.create_storyboard(
            name="B", target_model="sd", architecture="t2i", base_seed=1
        )
        scene = self.db.create_scene(board, name="Yard", reference_path=OUT[0])
        image_id = self._create(1)

        deleted, purged = self.db.delete_t2i_image(image_id)

        self.assertTrue(deleted)
        self.assertEqual(purged, [])
        self.assertIsNotNone(self.db.get_media(Path(OUT[0])))
        self.assertIsNone(self.db.get_t2i_image(image_id))
        self.assertIn("out1.png", self.db.get_scene(scene)["reference_path"])

    def test_opening_the_same_database_twice_is_fine(self):
        image_id = self._create(1, **FACTS)
        again = DatabaseManager(Path(self.tmp.name))
        self.assertEqual(again.get_t2i_image(image_id)["seed"], 1234)
        self.assertEqual(len(again.list_t2i_images()), 1)


class TestT2iUpgrade(unittest.TestCase):
    def test_table_and_column_are_added_to_a_database_that_predates_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = DatabaseManager(Path(tmp))
            first.close()

            # Rebuild the schema the way it looked before this feature.
            conn = sqlite3.connect(str(Path(tmp) / "metascan.db"))
            conn.execute("DROP TABLE t2i_images")
            conn.execute("ALTER TABLE generation_jobs DROP COLUMN t2i_batch_id")
            conn.commit()
            conn.close()

            reopened = DatabaseManager(Path(tmp))
            try:
                _seed_media(reopened, ["/lib/t2i/x.png"])
                image_id = reopened.create_t2i_image(file_path="/lib/t2i/x.png", seed=1)
                self.assertEqual(reopened.get_t2i_image(image_id)["seed"], 1)
                preset = reopened.create_workflow_preset("p", "t2i", "{}", "{}")
                job = reopened.create_generation_job(preset, "{}", t2i_batch_id="b1")
                self.assertEqual(reopened.get_generation_job(job)["t2i_batch_id"], "b1")
            finally:
                reopened.close()


class TestGenerationJobsT2iColumn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.tmp.name))
        self.preset_id = self.db.create_workflow_preset("p", "t2i", "{}", "{}")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_t2i_batch_id_round_trip(self):
        job_id = self.db.create_generation_job(
            self.preset_id, "{}", t2i_batch_id="0123456789abcdef0123456789abcdef"
        )
        job = self.db.get_generation_job(job_id)
        self.assertEqual(job["t2i_batch_id"], "0123456789abcdef0123456789abcdef")

    def test_default_is_null(self):
        job_id = self.db.create_generation_job(self.preset_id, "{}")
        self.assertIsNone(self.db.get_generation_job(job_id)["t2i_batch_id"])

    def test_it_appears_in_list_generation_jobs_rows(self):
        tagged = self.db.create_generation_job(self.preset_id, "{}", t2i_batch_id="b1")
        plain = self.db.create_generation_job(self.preset_id, "{}")
        rows = {r["id"]: r for r in self.db.list_generation_jobs()}
        self.assertEqual(rows[tagged]["t2i_batch_id"], "b1")
        self.assertIsNone(rows[plain]["t2i_batch_id"])

    def test_the_new_parameter_is_last_so_existing_positional_calls_still_work(self):
        names = list(inspect.signature(self.db.create_generation_job).parameters)
        self.assertEqual(names[-2:], ["output_name", "t2i_batch_id"])
        job_id = self.db.create_generation_job(
            self.preset_id, "{}", 3, "/out", 4, "pre_", "/lib/src.png", "stem"
        )
        job = self.db.get_generation_job(job_id)
        self.assertEqual(
            (
                job["panel_id"],
                job["output_dir"],
                job["beat_id"],
                job["output_prefix"],
                job["i2v_source_path"],
                job["output_name"],
                job["t2i_batch_id"],
            ),
            (3, "/out", 4, "pre_", "/lib/src.png", "stem", None),
        )

    def test_the_column_is_a_plain_nullable_text_without_a_foreign_key(self):
        with self.db._get_connection() as conn:
            info = {
                r["name"]: r for r in conn.execute("PRAGMA table_info(generation_jobs)")
            }
            keys = [
                r["from"]
                for r in conn.execute("PRAGMA foreign_key_list(generation_jobs)")
            ]
        self.assertEqual(info["t2i_batch_id"]["type"], "TEXT")
        self.assertEqual(info["t2i_batch_id"]["notnull"], 0)
        self.assertNotIn("t2i_batch_id", keys)


class TestJobsRouteCarriesTheBatchId(unittest.TestCase):
    def test_get_comfy_jobs_returns_t2i_batch_id(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.api import comfy as comfy_api

        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(Path(tmp))
            preset = db.create_workflow_preset("p", "t2i", "{}", "{}")
            tagged = db.create_generation_job(preset, "{}", t2i_batch_id="batch-1")
            plain = db.create_generation_job(preset, "{}")
            app = FastAPI()
            app.include_router(comfy_api.router)
            with patch("backend.api.comfy.get_db", lambda: db):
                client = TestClient(app)
                listed = {j["id"]: j for j in client.get("/api/comfy/jobs").json()}
                single = client.get(f"/api/comfy/jobs/{tagged}").json()
            self.assertEqual(listed[tagged]["t2i_batch_id"], "batch-1")
            self.assertIsNone(listed[plain]["t2i_batch_id"])
            self.assertEqual(single["t2i_batch_id"], "batch-1")


if __name__ == "__main__":
    unittest.main()
