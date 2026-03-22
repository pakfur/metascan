"""Tests for the embedding manager and FAISS index."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

from metascan.core.embedding_manager import (
    CLIP_MODELS,
    EmbeddingManager,
    FaissIndexManager,
)
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from datetime import datetime


class TestClipModelRegistry(unittest.TestCase):
    """Test the CLIP model registry configuration."""

    def test_required_models_exist(self):
        self.assertIn("small", CLIP_MODELS)
        self.assertIn("medium", CLIP_MODELS)
        self.assertIn("large", CLIP_MODELS)

    def test_model_fields(self):
        for key, model in CLIP_MODELS.items():
            with self.subTest(model=key):
                self.assertIn("name", model)
                self.assertIn("pretrained", model)
                self.assertIn("embedding_dim", model)
                self.assertIn("vram_mb", model)
                self.assertIn("description", model)
                self.assertIsInstance(model["embedding_dim"], int)
                self.assertGreater(model["embedding_dim"], 0)

    def test_embedding_dimensions(self):
        self.assertEqual(CLIP_MODELS["small"]["embedding_dim"], 512)
        self.assertEqual(CLIP_MODELS["medium"]["embedding_dim"], 768)
        self.assertEqual(CLIP_MODELS["large"]["embedding_dim"], 1024)


class TestEmbeddingManagerInit(unittest.TestCase):
    """Test EmbeddingManager initialization (no model loading)."""

    def test_default_init(self):
        mgr = EmbeddingManager()
        self.assertEqual(mgr.model_key, "small")
        self.assertEqual(mgr.device_preference, "auto")
        self.assertEqual(mgr.embedding_dim, 512)

    def test_custom_init(self):
        mgr = EmbeddingManager(model_key="large", device="cpu")
        self.assertEqual(mgr.model_key, "large")
        self.assertEqual(mgr.device_preference, "cpu")
        self.assertEqual(mgr.embedding_dim, 1024)

    def test_model_config(self):
        mgr = EmbeddingManager(model_key="medium")
        config = mgr.model_config
        self.assertEqual(config["name"], "ViT-L-14")
        self.assertEqual(config["pretrained"], "openai")


class TestFaissIndexManager(unittest.TestCase):
    """Test FAISS index operations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name)
        self.manager = FaissIndexManager(self.index_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_index(self):
        self.manager.create(embedding_dim=512, model_key="small")
        self.assertTrue(self.manager.is_loaded)
        self.assertEqual(self.manager.file_count, 0)
        self.assertEqual(self.manager.meta["model_key"], "small")
        self.assertEqual(self.manager.meta["embedding_dim"], 512)

    def test_add_and_search(self):
        self.manager.create(embedding_dim=4, model_key="test")

        # Add some vectors
        vec1 = np.array([1, 0, 0, 0], dtype=np.float32)
        vec2 = np.array([0, 1, 0, 0], dtype=np.float32)
        vec3 = np.array([0.9, 0.1, 0, 0], dtype=np.float32)

        self.manager.add("file1.png", vec1)
        self.manager.add("file2.png", vec2)
        self.manager.add("file3.png", vec3)

        self.assertEqual(self.manager.file_count, 3)

        # Search for vec1 - should find file1 and file3 as most similar
        results = self.manager.search(vec1, top_k=3)
        self.assertEqual(len(results), 3)
        # First result should be file1 (exact match)
        self.assertEqual(results[0][0], "file1.png")
        # Second should be file3 (similar)
        self.assertEqual(results[1][0], "file3.png")

    def test_add_duplicate_path_skipped(self):
        self.manager.create(embedding_dim=4, model_key="test")
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        self.manager.add("file1.png", vec)
        self.manager.add("file1.png", vec)  # duplicate
        self.assertEqual(self.manager.file_count, 1)

    def test_add_batch(self):
        self.manager.create(embedding_dim=4, model_key="test")
        paths = ["a.png", "b.png", "c.png"]
        vecs = np.eye(3, 4, dtype=np.float32)
        self.manager.add_batch(paths, vecs)
        self.assertEqual(self.manager.file_count, 3)

    def test_has_file(self):
        self.manager.create(embedding_dim=4, model_key="test")
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        self.manager.add("file1.png", vec)
        self.assertTrue(self.manager.has_file("file1.png"))
        self.assertFalse(self.manager.has_file("file2.png"))

    def test_save_and_load(self):
        self.manager.create(embedding_dim=4, model_key="test")
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        self.manager.add("file1.png", vec)
        self.manager.add("file2.png", np.array([0, 1, 0, 0], dtype=np.float32))

        self.assertTrue(self.manager.save())

        # Load into new manager
        new_manager = FaissIndexManager(self.index_dir)
        self.assertTrue(new_manager.load())
        self.assertEqual(new_manager.file_count, 2)
        self.assertTrue(new_manager.has_file("file1.png"))
        self.assertTrue(new_manager.has_file("file2.png"))

        # Verify search still works
        results = new_manager.search(vec, top_k=2)
        self.assertEqual(results[0][0], "file1.png")

    def test_search_empty_index(self):
        self.manager.create(embedding_dim=4, model_key="test")
        vec = np.array([1, 0, 0, 0], dtype=np.float32)
        results = self.manager.search(vec, top_k=5)
        self.assertEqual(results, [])

    def test_search_unloaded_index(self):
        results = self.manager.search(np.array([1, 0, 0, 0], dtype=np.float32))
        self.assertEqual(results, [])

    def test_clear(self):
        self.manager.create(embedding_dim=4, model_key="test")
        self.manager.add("file1.png", np.array([1, 0, 0, 0], dtype=np.float32))
        self.manager.save()

        self.manager.clear()
        self.assertFalse(self.manager.is_loaded)
        self.assertEqual(self.manager.file_count, 0)
        self.assertFalse(self.manager._index_path.exists())

    def test_check_model_match(self):
        self.manager.create(embedding_dim=4, model_key="small")
        self.assertTrue(self.manager.check_model_match("small"))
        self.assertFalse(self.manager.check_model_match("large"))

    def test_remove_stale_entries(self):
        self.manager.create(embedding_dim=4, model_key="test")
        self.manager.add("file1.png", np.array([1, 0, 0, 0], dtype=np.float32))
        self.manager.add("file2.png", np.array([0, 1, 0, 0], dtype=np.float32))
        self.manager.add("file3.png", np.array([0, 0, 1, 0], dtype=np.float32))

        removed = self.manager.remove_stale_entries({"file1.png", "file3.png"})
        self.assertEqual(removed, 1)
        self.assertEqual(self.manager.file_count, 2)
        self.assertTrue(self.manager.has_file("file1.png"))
        self.assertFalse(self.manager.has_file("file2.png"))
        self.assertTrue(self.manager.has_file("file3.png"))


class TestDatabaseHashMethods(unittest.TestCase):
    """Test the media_hashes database methods."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(Path(self.temp_dir.name))

        # Insert a media record so foreign key works
        self.test_media = Media(
            file_path=Path("/test/image.png"),
            file_size=1000,
            width=512,
            height=512,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
        self.db.save_media(self.test_media)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_get_phash(self):
        self.db.save_media_hash(Path("/test/image.png"), "abcdef1234567890")
        phashes = self.db.get_all_phashes()
        # Path may be native format
        self.assertEqual(len(phashes), 1)
        found_hash = list(phashes.values())[0]
        self.assertEqual(found_hash, "abcdef1234567890")

    def test_save_hash_batch(self):
        # Add another media record
        media2 = Media(
            file_path=Path("/test/image2.png"),
            file_size=2000,
            width=256,
            height=256,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
        self.db.save_media(media2)

        items = [
            (Path("/test/image.png"), "hash1"),
            (Path("/test/image2.png"), "hash2"),
        ]
        count = self.db.save_media_hash_batch(items)
        self.assertEqual(count, 2)

        phashes = self.db.get_all_phashes()
        self.assertEqual(len(phashes), 2)

    def test_get_unembedded_file_paths(self):
        unembedded = self.db.get_unembedded_file_paths()
        self.assertEqual(len(unembedded), 1)

    def test_mark_embedded(self):
        # First file is unembedded
        unembedded = self.db.get_unembedded_file_paths()
        self.assertEqual(len(unembedded), 1)

        # Mark it as embedded
        self.db.mark_embedded(unembedded, "small")

        # Now should be empty
        unembedded2 = self.db.get_unembedded_file_paths()
        self.assertEqual(len(unembedded2), 0)

    def test_clear_embeddings(self):
        path = self.db.get_unembedded_file_paths()
        self.db.mark_embedded(path, "small")
        self.assertEqual(len(self.db.get_unembedded_file_paths()), 0)

        self.db.clear_embeddings()
        self.assertEqual(len(self.db.get_unembedded_file_paths()), 1)

    def test_get_embedding_stats(self):
        stats = self.db.get_embedding_stats()
        self.assertEqual(stats["total_media"], 1)
        self.assertEqual(stats["hashed"], 0)
        self.assertEqual(stats["embedded"], 0)

        self.db.save_media_hash(Path("/test/image.png"), "somehash")
        stats2 = self.db.get_embedding_stats()
        self.assertEqual(stats2["hashed"], 1)
        self.assertEqual(stats2["embedded"], 0)

    def test_cascade_delete(self):
        """Verify media_hashes entries are deleted when media is deleted."""
        self.db.save_media_hash(Path("/test/image.png"), "somehash")
        self.assertEqual(len(self.db.get_all_phashes()), 1)

        self.db.delete_media(Path("/test/image.png"))
        self.assertEqual(len(self.db.get_all_phashes()), 0)


if __name__ == "__main__":
    unittest.main()
