"""Tests for similarity settings and configuration."""

import json
import unittest
from pathlib import Path

from metascan.core.embedding_manager import CLIP_MODELS
from metascan.core.embedding_queue import EmbeddingQueue


class TestSimilarityConfig(unittest.TestCase):
    """Test similarity configuration structure."""

    def test_default_config_structure(self):
        """Default config should have all required fields."""
        default = {
            "clip_model": "small",
            "device": "auto",
            "search_results_count": 100,
            "video_keyframes": 4,
            "compute_phash_during_scan": True,
        }

        self.assertIn(default["clip_model"], CLIP_MODELS)
        self.assertIn(default["device"], ["auto", "cpu", "cuda"])
        self.assertGreaterEqual(default["search_results_count"], 10)
        self.assertGreaterEqual(default["video_keyframes"], 1)

    def test_config_roundtrip(self):
        """Config should survive JSON serialization."""
        config = {
            "similarity": {
                "clip_model": "medium",
                "device": "cuda",
                "search_results_count": 200,
                "video_keyframes": 8,
                "compute_phash_during_scan": False,
            }
        }

        serialized = json.dumps(config)
        loaded = json.loads(serialized)

        sim = loaded["similarity"]
        self.assertEqual(sim["clip_model"], "medium")
        self.assertEqual(sim["device"], "cuda")
        self.assertEqual(sim["search_results_count"], 200)
        self.assertEqual(sim["video_keyframes"], 8)
        self.assertFalse(sim["compute_phash_during_scan"])

    def test_config_example_has_similarity(self):
        """config_example.json should include similarity section."""
        config_path = Path(__file__).parent.parent / "config_example.json"
        with open(config_path, "r") as f:
            config = json.load(f)

        self.assertIn("similarity", config)
        sim = config["similarity"]
        self.assertIn("clip_model", sim)
        self.assertIn("device", sim)
        self.assertIn("search_results_count", sim)
        self.assertIn("video_keyframes", sim)
        self.assertIn("compute_phash_during_scan", sim)


class TestClipModelOptions(unittest.TestCase):
    """Test that CLIP model options are valid for settings UI."""

    def test_all_models_have_description(self):
        for key, model in CLIP_MODELS.items():
            self.assertIn("description", model)
            self.assertIsInstance(model["description"], str)
            self.assertGreater(len(model["description"]), 0)

    def test_models_have_increasing_vram(self):
        """Models should be ordered by VRAM requirement."""
        keys = list(CLIP_MODELS.keys())
        vrams = [CLIP_MODELS[k]["vram_mb"] for k in keys]
        self.assertEqual(vrams, sorted(vrams))

    def test_models_have_increasing_dim(self):
        """Models should have increasing embedding dimensions."""
        keys = list(CLIP_MODELS.keys())
        dims = [CLIP_MODELS[k]["embedding_dim"] for k in keys]
        self.assertEqual(dims, sorted(dims))


class TestEmbeddingQueueTaskGeneration(unittest.TestCase):
    """Test that the embedding queue generates valid task files."""

    def test_start_empty_list_completes(self):
        """Starting indexing with empty list should emit complete immediately."""
        eq = EmbeddingQueue()
        result = eq.start_indexing(
            file_paths=[],
            clip_model_key="small",
            device="cpu",
            db_path="/tmp/test",
        )
        self.assertTrue(result)
        self.assertFalse(eq.is_indexing())


if __name__ == "__main__":
    unittest.main()
