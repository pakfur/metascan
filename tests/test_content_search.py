"""Tests for content search functionality."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from metascan.core.embedding_manager import FaissIndexManager

# Use dim >= 32 to avoid FAISS SIMD alignment issues on ARM
DIM = 32


def _make_vec(idx):
    """Create a deterministic normalized vector for testing."""
    v = np.zeros(DIM, dtype=np.float32)
    v[idx % DIM] = 1.0
    return v


def _make_similar(base, noise=0.1):
    """Create a vector similar to base with small noise."""
    v = base + np.random.randn(DIM).astype(np.float32) * noise
    return v / np.linalg.norm(v)


class TestContentSearchFlow(unittest.TestCase):
    """Test the end-to-end content search flow using FAISS."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name)
        self.faiss_mgr = FaissIndexManager(self.index_dir)
        self.faiss_mgr.create(embedding_dim=DIM, model_key="test")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_returns_ranked_results(self):
        """Search should return results sorted by similarity."""
        self.faiss_mgr.add("file1.png", _make_vec(0))
        self.faiss_mgr.add("file2.png", _make_vec(1))
        self.faiss_mgr.add("file3.png", _make_similar(_make_vec(0)))

        results = self.faiss_mgr.search(_make_vec(0), top_k=3)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0][0], "file1.png")
        self.assertEqual(results[1][0], "file3.png")
        self.assertEqual(results[2][0], "file2.png")

    def test_search_respects_top_k(self):
        """Search should return at most top_k results."""
        for i in range(10):
            vec = np.random.randn(DIM).astype(np.float32)
            vec = vec / np.linalg.norm(vec)
            self.faiss_mgr.add(f"file{i}.png", vec)

        query = np.random.randn(DIM).astype(np.float32)
        query = query / np.linalg.norm(query)
        results = self.faiss_mgr.search(query, top_k=3)
        self.assertEqual(len(results), 3)

    def test_search_scores_are_float(self):
        """Scores should be float values."""
        self.faiss_mgr.add("file1.png", _make_vec(0))
        results = self.faiss_mgr.search(_make_vec(0), top_k=1)
        self.assertIsInstance(results[0][1], float)

    def test_save_load_search_roundtrip(self):
        """Index should work correctly after save and load."""
        self.faiss_mgr.add("file1.png", _make_vec(0))
        self.faiss_mgr.save()

        new_mgr = FaissIndexManager(self.index_dir)
        new_mgr.load()

        results = new_mgr.search(_make_vec(0), top_k=1)
        self.assertEqual(results[0][0], "file1.png")


if __name__ == "__main__":
    unittest.main()
