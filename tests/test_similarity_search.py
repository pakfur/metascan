"""Tests for similarity search functionality."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from metascan.core.embedding_manager import FaissIndexManager


class TestSimilaritySearch(unittest.TestCase):
    """Test similarity search via FAISS index."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name)
        self.faiss_mgr = FaissIndexManager(self.index_dir)
        self.faiss_mgr.create(embedding_dim=8, model_key="test")

        # Add a set of vectors representing different content
        np.random.seed(42)
        self.files = {}
        for i in range(20):
            vec = np.random.randn(8).astype(np.float32)
            vec = vec / np.linalg.norm(vec)  # normalize
            name = f"file_{i:02d}.png"
            self.faiss_mgr.add(name, vec)
            self.files[name] = vec

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_self_similarity(self):
        """A file should be its own best match."""
        query_file = "file_00.png"
        query_vec = self.files[query_file]
        results = self.faiss_mgr.search(query_vec, top_k=5)
        self.assertEqual(results[0][0], query_file)
        self.assertAlmostEqual(results[0][1], 1.0, places=3)

    def test_results_sorted_by_score(self):
        """Results should be sorted by descending similarity."""
        query_vec = self.files["file_05.png"]
        results = self.faiss_mgr.search(query_vec, top_k=10)
        scores = [r[1] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_find_similar_excludes_dissimilar(self):
        """Similar vectors should rank higher than dissimilar ones."""
        # Create a query similar to file_00
        base = self.files["file_00.png"]
        similar = base + np.random.randn(8).astype(np.float32) * 0.1
        similar = similar / np.linalg.norm(similar)

        self.faiss_mgr.add("similar_to_00.png", similar)

        results = self.faiss_mgr.search(base, top_k=3)
        result_files = [r[0] for r in results]
        # file_00 should be first, similar_to_00 should be in top 3
        self.assertEqual(result_files[0], "file_00.png")
        self.assertIn("similar_to_00.png", result_files)

    def test_get_embedding_and_search(self):
        """Should be able to retrieve a stored embedding and search with it."""
        embedding = self.faiss_mgr.get_embedding("file_10.png")
        self.assertIsNotNone(embedding)

        results = self.faiss_mgr.search(embedding, top_k=1)
        self.assertEqual(results[0][0], "file_10.png")


class TestSignalPropagation(unittest.TestCase):
    """Test that the find_similar_requested signal exists on all view classes."""

    def test_thumbnail_widget_has_signal(self):
        from metascan.ui.thumbnail_view import ThumbnailWidget

        self.assertTrue(hasattr(ThumbnailWidget, "find_similar_requested"))

    def test_virtual_scroll_area_has_signal(self):
        from metascan.ui.virtual_thumbnail_view import VirtualScrollArea

        self.assertTrue(hasattr(VirtualScrollArea, "find_similar_requested"))

    def test_virtual_thumbnail_view_has_signal(self):
        from metascan.ui.virtual_thumbnail_view import VirtualThumbnailView

        self.assertTrue(hasattr(VirtualThumbnailView, "find_similar_requested"))


if __name__ == "__main__":
    unittest.main()
