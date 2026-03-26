"""Tests for content search functionality."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager


class TestContentSearchFlow(unittest.TestCase):
    """Test the end-to-end content search flow using FAISS."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name)
        self.faiss_mgr = FaissIndexManager(self.index_dir)
        self.faiss_mgr.create(embedding_dim=4, model_key="test")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_returns_ranked_results(self):
        """Search should return results sorted by similarity."""
        # Add vectors: file1 is aligned with query, file2 is orthogonal
        self.faiss_mgr.add("file1.png", np.array([1, 0, 0, 0], dtype=np.float32))
        self.faiss_mgr.add("file2.png", np.array([0, 1, 0, 0], dtype=np.float32))
        self.faiss_mgr.add("file3.png", np.array([0.8, 0.2, 0, 0], dtype=np.float32))

        query = np.array([1, 0, 0, 0], dtype=np.float32)
        results = self.faiss_mgr.search(query, top_k=3)

        self.assertEqual(len(results), 3)
        # file1 should be most similar (exact match)
        self.assertEqual(results[0][0], "file1.png")
        # file3 should be second (partial match)
        self.assertEqual(results[1][0], "file3.png")
        # file2 should be last (orthogonal)
        self.assertEqual(results[2][0], "file2.png")

    def test_search_respects_top_k(self):
        """Search should return at most top_k results."""
        for i in range(10):
            self.faiss_mgr.add(
                f"file{i}.png",
                np.random.randn(4).astype(np.float32),
            )

        query = np.random.randn(4).astype(np.float32)
        results = self.faiss_mgr.search(query, top_k=3)
        self.assertEqual(len(results), 3)

    def test_search_scores_are_float(self):
        """Scores should be float values."""
        self.faiss_mgr.add("file1.png", np.array([1, 0, 0, 0], dtype=np.float32))
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        results = self.faiss_mgr.search(query, top_k=1)
        self.assertIsInstance(results[0][1], float)

    def test_save_load_search_roundtrip(self):
        """Index should work correctly after save and load."""
        self.faiss_mgr.add("file1.png", np.array([1, 0, 0, 0], dtype=np.float32))
        self.faiss_mgr.save()

        new_mgr = FaissIndexManager(self.index_dir)
        new_mgr.load()

        results = new_mgr.search(np.array([1, 0, 0, 0], dtype=np.float32), top_k=1)
        self.assertEqual(results[0][0], "file1.png")


class TestFiltersPanelContentSearch(unittest.TestCase):
    """Test the content search signal from FiltersPanel."""

    def test_signal_defined(self):
        """FiltersPanel should have content_search_requested signal."""
        from metascan.ui.filters_panel import FiltersPanel

        # Just check the signal exists as a class attribute
        self.assertTrue(hasattr(FiltersPanel, "content_search_requested"))
        self.assertTrue(hasattr(FiltersPanel, "content_search_cleared"))


if __name__ == "__main__":
    unittest.main()
