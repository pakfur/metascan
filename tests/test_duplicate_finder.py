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


if __name__ == "__main__":
    unittest.main()
