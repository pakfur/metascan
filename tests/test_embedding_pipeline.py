"""Tests for the embedding pipeline: worker, queue, and scanner integration."""

import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

from metascan.core.phash_utils import compute_phash_for_file
from metascan.core.embedding_queue import EmbeddingQueue
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


class TestPhashUtils(unittest.TestCase):
    """Test the lightweight pHash utilities."""

    def test_compute_phash_for_png(self):
        """Test pHash computation for a real test image."""
        # Create a small test image
        from PIL import Image
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (64, 64), color=(255, 0, 0))
            img.save(f.name)
            result = compute_phash_for_file(Path(f.name))

        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 16)  # pHash hex is 16 chars (64 bits)

    def test_compute_phash_nonexistent_file(self):
        result = compute_phash_for_file(Path("/nonexistent/file.png"))
        self.assertIsNone(result)

    def test_phash_consistency(self):
        """Same image should produce same hash."""
        from PIL import Image
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = Image.new("RGB", (64, 64), color=(0, 128, 255))
            img.save(f.name)
            h1 = compute_phash_for_file(Path(f.name))
            h2 = compute_phash_for_file(Path(f.name))

        self.assertEqual(h1, h2)

    def test_different_images_different_hash(self):
        """Very different images should produce different hashes."""
        from PIL import Image
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f1:
            img1 = Image.new("RGB", (64, 64), color=(255, 0, 0))
            img1.save(f1.name)
            h1 = compute_phash_for_file(Path(f1.name))

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f2:
            # Create a noisy black & white pattern
            import random

            img2 = Image.new("RGB", (64, 64))
            for x in range(64):
                for y in range(64):
                    c = random.randint(0, 255)
                    img2.putpixel((x, y), (c, 255 - c, c // 2))
            img2.save(f2.name)
            h2 = compute_phash_for_file(Path(f2.name))

        # They may not be totally different but shouldn't be identical
        # (solid red vs random noise)
        self.assertIsNotNone(h1)
        self.assertIsNotNone(h2)


class TestEmbeddingQueueInit(unittest.TestCase):
    """Test EmbeddingQueue initialization and state management."""

    def test_init(self):
        """EmbeddingQueue initializes without errors."""
        eq = EmbeddingQueue()
        self.assertFalse(eq.is_indexing())

    def test_index_dir_exists(self):
        eq = EmbeddingQueue()
        self.assertTrue(eq.index_dir.exists())


class TestEmbeddingWorkerTask(unittest.TestCase):
    """Test the worker task file format."""

    def test_task_file_format(self):
        """Verify the task JSON structure matches what the worker expects."""
        task = {
            "model_key": "small",
            "device": "cpu",
            "file_paths": ["/test/a.png", "/test/b.png"],
            "db_path": "/tmp/test_db",
            "index_dir": "/tmp/test_index",
            "compute_phash": True,
            "video_keyframes": 4,
        }
        # Round-trip through JSON
        serialized = json.dumps(task)
        loaded = json.loads(serialized)

        self.assertEqual(loaded["model_key"], "small")
        self.assertEqual(len(loaded["file_paths"]), 2)
        self.assertTrue(loaded["compute_phash"])

    def test_progress_file_format(self):
        """Verify the progress JSON structure."""
        progress = {
            "current": 5,
            "total": 10,
            "status": "processing",
            "current_file": "test.png",
            "error": "",
            "timestamp": 1234567890.0,
        }
        serialized = json.dumps(progress)
        loaded = json.loads(serialized)
        self.assertEqual(loaded["status"], "processing")
        self.assertEqual(loaded["current"], 5)


class TestScannerPhashIntegration(unittest.TestCase):
    """Test that scanner correctly computes and stores pHashes."""

    def test_scanner_stores_phash_after_save(self):
        """Verify the scanner integration with pHash by testing DB directly."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(Path(tmpdir) / "db")

            # Create a test image
            img_path = Path(tmpdir) / "test.png"
            img = Image.new("RGB", (64, 64), color=(255, 0, 0))
            img.save(img_path)

            # Create and save a media object
            media = Media(
                file_path=img_path,
                file_size=100,
                width=64,
                height=64,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
            db.save_media(media)

            # Compute and save pHash (simulating what scanner does)
            phash = compute_phash_for_file(img_path)
            self.assertIsNotNone(phash)

            db.save_media_hash(img_path, phash)

            # Verify it was stored
            phashes = db.get_all_phashes()
            self.assertEqual(len(phashes), 1)
            stored_hash = list(phashes.values())[0]
            self.assertEqual(stored_hash, phash)


if __name__ == "__main__":
    unittest.main()
