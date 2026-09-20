"""Pure tests for i2v output placement: root + date-expanded prefix."""

import unittest
from datetime import datetime
from pathlib import Path

from metascan.core.i2v_output import (
    I2vOutputError,
    output_prefix_warnings,
    resolve_output_target,
)

NOW = datetime(2026, 9, 20, 13, 43, 10)


class TestResolveOutputTarget(unittest.TestCase):
    def _resolve(self, prefix, root="/mnt/d/Media/images", number=1789930000):
        return resolve_output_target(root, prefix, NOW, number)

    def test_the_documented_example(self):
        t = self._resolve("/%Y-%m-%d/minimax_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/2026-09-20"))
        self.assertEqual(t.stem, "minimax_1789930000")

    def test_leading_slash_is_relative_to_root_not_the_filesystem(self):
        self.assertEqual(
            self._resolve("/clips/x_").directory, Path("/mnt/d/Media/images/clips")
        )
        self.assertEqual(
            self._resolve("clips/x_").directory, Path("/mnt/d/Media/images/clips")
        )

    def test_nested_directories(self):
        t = self._resolve("%Y/%m/%d/take_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/2026/09/20"))
        self.assertEqual(t.stem, "take_1789930000")

    def test_no_directory_part(self):
        t = self._resolve("minimax_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images"))
        self.assertEqual(t.stem, "minimax_1789930000")

    def test_trailing_slash_means_no_file_prefix(self):
        t = self._resolve("%Y-%m-%d/")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/2026-09-20"))
        self.assertEqual(t.stem, "1789930000")

    def test_empty_prefix(self):
        for prefix in ("", "   ", None, "/"):
            t = self._resolve(prefix)
            self.assertEqual(t.directory, Path("/mnt/d/Media/images"))
            self.assertEqual(t.stem, "1789930000")

    def test_date_tokens_in_the_file_prefix(self):
        self.assertEqual(self._resolve("mm_%H%M_").stem, "mm_1343_1789930000")

    def test_backslashes_are_separators(self):
        t = self._resolve("\\%Y-%m-%d\\minimax_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/2026-09-20"))

    def test_empty_and_dot_components_are_dropped(self):
        t = self._resolve("//a/./b//x_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/a/b"))

    def test_parent_traversal_rejected(self):
        for prefix in ("../x_", "a/../../x_", "/%Y/../..//x_"):
            with self.assertRaises(I2vOutputError):
                self._resolve(prefix)

    def test_illegal_filename_characters_are_replaced_per_component(self):
        # %D expands to 09/20/26 -- a slash born from expansion must not
        # create directories; ':' and '?' are illegal on Windows.
        t = self._resolve("a:b/%D/x?_")
        self.assertEqual(t.directory, Path("/mnt/d/Media/images/a-b/09-20-26"))
        self.assertEqual(t.stem, "x-_1789930000")

    def test_blank_root_rejected(self):
        for root in ("", "   ", None):
            with self.assertRaises(I2vOutputError):
                self._resolve("x_", root=root)

    def test_relative_root_rejected(self):
        with self.assertRaises(I2vOutputError):
            self._resolve("x_", root="relative/dir")


class TestPrefixWarnings(unittest.TestCase):
    def test_minutes_token_without_hours_warns(self):
        """The classic slip: %M is minutes, %m is the month."""
        warnings = output_prefix_warnings("/%Y-%M-%d/minimax_")
        self.assertEqual(len(warnings), 1)
        self.assertIn("%m", warnings[0])

    def test_minutes_alongside_hours_is_deliberate(self):
        self.assertEqual(output_prefix_warnings("%Y-%m-%d/%H%M_"), [])

    def test_clean_prefix(self):
        self.assertEqual(output_prefix_warnings("/%Y-%m-%d/minimax_"), [])
        self.assertEqual(output_prefix_warnings(""), [])
        self.assertEqual(output_prefix_warnings(None), [])

    def test_escaped_percent_is_not_a_token(self):
        self.assertEqual(output_prefix_warnings("100%%M_"), [])


if __name__ == "__main__":
    unittest.main()
