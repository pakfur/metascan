"""Tests for duplicate detection logic."""

import unittest

from metascan.ui.duplicate_finder_dialog import find_phash_duplicate_groups


class TestFindPhashDuplicateGroups(unittest.TestCase):
    """Test the pHash duplicate grouping algorithm."""

    def test_identical_hashes(self):
        """Identical hashes should form a group."""
        phashes = {
            "file1.png": "abcdef1234567890",
            "file2.png": "abcdef1234567890",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=0)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)
        # First item should have distance 0
        self.assertEqual(groups[0][0][1], 0)
        self.assertEqual(groups[0][1][1], 0)

    def test_no_duplicates(self):
        """Completely different hashes should not form groups."""
        phashes = {
            "file1.png": "0000000000000000",
            "file2.png": "ffffffffffffffff",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=5)
        self.assertEqual(len(groups), 0)

    def test_threshold_filtering(self):
        """Groups should respect the hamming distance threshold."""
        # These two hex hashes differ by exactly 1 bit
        phashes = {
            "file1.png": "0000000000000000",
            "file2.png": "0000000000000001",
        }
        # With threshold=0, they should NOT be grouped
        groups_strict = find_phash_duplicate_groups(phashes, threshold=0)
        self.assertEqual(len(groups_strict), 0)

        # With threshold=5, they SHOULD be grouped
        groups_loose = find_phash_duplicate_groups(phashes, threshold=5)
        self.assertEqual(len(groups_loose), 1)

    def test_single_file_no_group(self):
        """A single file should not form a group."""
        phashes = {"file1.png": "abcdef1234567890"}
        groups = find_phash_duplicate_groups(phashes, threshold=10)
        self.assertEqual(len(groups), 0)

    def test_empty_input(self):
        groups = find_phash_duplicate_groups({}, threshold=10)
        self.assertEqual(groups, [])

    def test_multiple_groups(self):
        """Files should be grouped into separate clusters."""
        phashes = {
            "a1.png": "0000000000000000",
            "a2.png": "0000000000000000",
            "b1.png": "ffffffffffffffff",
            "b2.png": "ffffffffffffffff",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=5)
        self.assertEqual(len(groups), 2)

        # Each group should have 2 files
        for g in groups:
            self.assertEqual(len(g), 2)

    def test_group_distances(self):
        """Distance values in groups should be correct."""
        phashes = {
            "file1.png": "0000000000000000",
            "file2.png": "0000000000000000",
            "file3.png": "0000000000000001",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=10)
        self.assertEqual(len(groups), 1)
        # First element always has distance 0
        self.assertEqual(groups[0][0][1], 0)


class TestMediaTypeSeparation(unittest.TestCase):
    """Test that images and videos are grouped separately."""

    def test_images_and_videos_separate_groups(self):
        """Identical hashes across images and videos should be in separate groups."""
        same_hash = "abcdef1234567890"
        phashes = {
            "img1.png": same_hash,
            "img2.jpg": same_hash,
            "img3.webp": same_hash,
            "vid1.mp4": same_hash,
            "vid2.webm": same_hash,
            "vid3.mp4": same_hash,
            "vid4.mp4": same_hash,
        }
        groups = find_phash_duplicate_groups(phashes, threshold=0)
        self.assertEqual(len(groups), 2)

        # Sort groups by size for predictable assertion
        groups.sort(key=len)
        image_group = groups[0]
        video_group = groups[1]

        self.assertEqual(len(image_group), 3)
        self.assertEqual(len(video_group), 4)

        # Verify all items in each group are the same type
        for path, _ in image_group:
            self.assertFalse(path.endswith(".mp4") or path.endswith(".webm"))
        for path, _ in video_group:
            self.assertTrue(path.endswith(".mp4") or path.endswith(".webm"))

    def test_only_images_still_works(self):
        """Groups with only images should work normally."""
        phashes = {
            "a.png": "0000000000000000",
            "b.jpg": "0000000000000000",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=0)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_only_videos_still_works(self):
        """Groups with only videos should work normally."""
        phashes = {
            "a.mp4": "0000000000000000",
            "b.webm": "0000000000000000",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=0)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_mixed_no_cross_type_grouping(self):
        """An image and video with identical hash should NOT be grouped together."""
        phashes = {
            "photo.png": "0000000000000000",
            "clip.mp4": "0000000000000000",
        }
        groups = find_phash_duplicate_groups(phashes, threshold=0)
        # Each type only has 1 file, so no groups (need 2+ to form a group)
        self.assertEqual(len(groups), 0)


class TestProgressCallback(unittest.TestCase):
    """Test the progress callback in find_phash_duplicate_groups."""

    def test_progress_callback_called(self):
        """Progress callback should be invoked during comparison."""
        phashes = {f"file{i}.png": f"{i:016x}" for i in range(20)}
        calls = []

        def cb(current, total):
            calls.append((current, total))
            return True

        find_phash_duplicate_groups(phashes, threshold=5, progress_callback=cb)
        # Should have been called at least once (final call)
        self.assertGreater(len(calls), 0)
        # Last call should be (total, total)
        last_current, last_total = calls[-1]
        self.assertEqual(last_current, last_total)

    def test_progress_callback_total_correct(self):
        """Total comparisons should be n*(n-1)/2."""
        n = 10
        phashes = {f"file{i}.png": f"{i:016x}" for i in range(n)}
        expected_total = n * (n - 1) // 2
        totals = []

        def cb(current, total):
            totals.append(total)
            return True

        find_phash_duplicate_groups(phashes, threshold=5, progress_callback=cb)
        # All reported totals should be the same
        for t in totals:
            self.assertEqual(t, expected_total)

    def test_cancellation_via_callback(self):
        """Returning False from callback should stop early."""
        phashes = {f"file{i}.png": f"{i:016x}" for i in range(100)}
        call_count = 0

        def cb(current, total):
            nonlocal call_count
            call_count += 1
            # Cancel after the first progress report
            return call_count < 2

        groups = find_phash_duplicate_groups(phashes, threshold=5, progress_callback=cb)
        # Should have stopped early — not all comparisons done
        self.assertLessEqual(call_count, 5)
        # Should still return a valid (possibly empty) list
        self.assertIsInstance(groups, list)

    def test_no_callback_works(self):
        """None callback should work (backward compatible)."""
        phashes = {"a.png": "0000000000000000", "b.png": "0000000000000000"}
        groups = find_phash_duplicate_groups(
            phashes, threshold=5, progress_callback=None
        )
        self.assertEqual(len(groups), 1)


if __name__ == "__main__":
    unittest.main()
