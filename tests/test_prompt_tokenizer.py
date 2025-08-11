import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from metascan.core.prompt_tokenizer import PromptTokenizer


class TestPromptTokenizer(unittest.TestCase):
    """Test cases for PromptTokenizer class."""

    def setUp(self):
        """Set up test fixtures."""
        # Default config for testing
        self.test_config = {
            "filler_words": ["beautiful", "nice", "awesome", "lovely"],
            "tokenization": {
                "heuristics": {
                    "comma_density": {"threshold": 0.2, "weight": 0.6},
                    "stopword_frequency": {"threshold": 0.15, "weight": 0.4},
                },
                "classification_threshold": 0.5,
            },
        }

    def test_initialization_with_defaults(self):
        """Test tokenizer initialization with default values."""
        tokenizer = PromptTokenizer()

        # Check that stop words are loaded
        self.assertIsNotNone(tokenizer.stop_words)
        self.assertGreater(len(tokenizer.stop_words), 0)

        # Check default config values
        self.assertEqual(tokenizer.comma_density_threshold, 0.2)
        self.assertEqual(tokenizer.comma_density_weight, 0.6)
        self.assertEqual(tokenizer.stopword_frequency_threshold, 0.15)
        self.assertEqual(tokenizer.stopword_frequency_weight, 0.4)
        self.assertEqual(tokenizer.classification_threshold, 0.5)

    def test_initialization_with_custom_config(self):
        """Test tokenizer initialization with custom configuration."""
        custom_config = {
            "filler_words": ["test1", "test2"],
            "tokenization": {
                "heuristics": {
                    "comma_density": {"threshold": 0.3, "weight": 0.7},
                    "stopword_frequency": {"threshold": 0.2, "weight": 0.3},
                },
                "classification_threshold": 0.6,
            },
        }

        tokenizer = PromptTokenizer(config=custom_config)

        self.assertEqual(tokenizer.filler_words, {"test1", "test2"})
        self.assertEqual(tokenizer.comma_density_threshold, 0.3)
        self.assertEqual(tokenizer.comma_density_weight, 0.7)
        self.assertEqual(tokenizer.stopword_frequency_threshold, 0.2)
        self.assertEqual(tokenizer.stopword_frequency_weight, 0.3)
        self.assertEqual(tokenizer.classification_threshold, 0.6)

    def test_calculate_comma_density(self):
        """Test comma density calculation."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Test various cases
        test_cases = [
            ("cat, dog, bird, fish", 0.75),  # 3 commas, 4 words
            ("a beautiful sunset", 0.0),  # 0 commas, 3 words
            ("one, two", 0.5),  # 1 comma, 2 words
            ("", 0.0),  # empty string
            ("word", 0.0),  # single word
        ]

        for prompt, expected in test_cases:
            with self.subTest(prompt=prompt):
                result = tokenizer._calculate_comma_density(prompt)
                self.assertAlmostEqual(result, expected, places=3)

    def test_calculate_stopword_frequency(self):
        """Test stopword frequency calculation."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Test various cases
        test_cases = [
            ("The cat is on the mat", 0.666),  # 4 stopwords out of 6
            ("cat dog bird", 0.0),  # no stopwords
            ("is the a an", 1.0),  # all stopwords
            ("", 0.0),  # empty string
        ]

        for prompt, expected in test_cases:
            with self.subTest(prompt=prompt):
                result = tokenizer._calculate_stopword_frequency(prompt)
                self.assertAlmostEqual(result, expected, places=2)

    def test_classify_prompt_tags(self):
        """Test classification of tag-based prompts."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # High comma density, low stopwords -> tags
        tag_prompts = [
            "portrait, woman, oil painting, renaissance",
            "cat, fluffy, cute, sleeping",
            "landscape, mountains, sunset, 4k",
        ]

        for prompt in tag_prompts:
            with self.subTest(prompt=prompt):
                prompt_type, confidence = tokenizer.classify_prompt(prompt)
                self.assertEqual(prompt_type, "tags")
                self.assertGreater(confidence, 0.5)

    def test_classify_prompt_captions(self):
        """Test classification of caption-based prompts."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Low comma density, high stopwords -> caption
        caption_prompts = [
            "A beautiful woman sitting in the garden",
            "The cat is sleeping on the warm blanket",
            "An old man walking through the forest",
        ]

        for prompt in caption_prompts:
            with self.subTest(prompt=prompt):
                prompt_type, confidence = tokenizer.classify_prompt(prompt)
                self.assertEqual(prompt_type, "caption")
                self.assertGreater(confidence, 0.5)

    def test_tokenize_tags(self):
        """Test tag tokenization strategy."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Test cases with expected results
        test_cases = [
            (
                "portrait of a woman, oil painting, renaissance style",
                {"portrait woman", "oil painting", "renaissance style"},
            ),
            ("cat, cute and fluffy, sleeping", {"cat", "cute fluffy", "sleeping"}),
            ("beautiful, nice, awesome", set()),  # all filler words
            ("the, a, an", set()),  # all stop words
            ("", set()),  # empty
        ]

        for prompt, expected in test_cases:
            with self.subTest(prompt=prompt):
                result = tokenizer._tokenize_tags(prompt)
                self.assertEqual(result, expected)

    def test_tokenize_caption(self):
        """Test caption tokenization strategy."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Test cases with expected results
        test_cases = [
            (
                "A beautiful sunset over the ocean",
                {"sunset", "ocean"},  # "beautiful" is filtered as filler word
            ),
            ("The quick brown fox jumps", {"quick", "brown", "fox", "jumps"}),
            ("a an the is are", set()),  # all stop words
            ("", set()),  # empty
        ]

        for prompt, expected in test_cases:
            with self.subTest(prompt=prompt):
                result = tokenizer._tokenize_caption(prompt)
                self.assertEqual(result, expected)

    def test_tokenize_integration(self):
        """Test the main tokenize method with automatic classification."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Test tag prompt
        tag_prompt = "portrait, woman, oil painting, renaissance"
        with patch.object(tokenizer, "classify_prompt", return_value=("tags", 0.9)):
            result = tokenizer.tokenize(tag_prompt)
            # Should use tag tokenization
            self.assertIn("oil painting", result)
            self.assertIn("portrait", result)

        # Test caption prompt
        caption_prompt = "A woman sitting in the garden"
        with patch.object(tokenizer, "classify_prompt", return_value=("caption", 0.9)):
            result = tokenizer.tokenize(caption_prompt)
            # Should use caption tokenization
            self.assertIn("woman", result)
            self.assertIn("sitting", result)
            self.assertIn("garden", result)

    def test_edge_cases(self):
        """Test edge cases and boundary conditions."""
        tokenizer = PromptTokenizer(config=self.test_config)

        # Empty prompt
        self.assertEqual(tokenizer.tokenize(""), set())

        # Only punctuation
        result = tokenizer._tokenize_tags(",,,,")
        self.assertEqual(result, set())

        # Mixed punctuation in tags
        result = tokenizer._tokenize_tags("cat., dog!, bird?")
        self.assertEqual(result, {"cat", "dog", "bird"})

        # Very long words (should be kept)
        result = tokenizer._tokenize_caption("supercalifragilisticexpialidocious")
        self.assertEqual(result, {"supercalifragilisticexpialidocious"})

        # Short words (should be filtered)
        result = tokenizer._tokenize_caption("a ab abc abcd")
        self.assertEqual(
            result, {"abc", "abcd"}
        )  # only words > 2 chars (len > 2 means 3+)

    def test_config_loading_from_file(self):
        """Test loading configuration from a file."""
        # This test verifies that config can be loaded from the actual config.json file
        tokenizer = PromptTokenizer()

        # Just verify that the tokenizer initializes properly
        # The actual config loading is tested implicitly
        self.assertIsNotNone(tokenizer.stop_words)
        self.assertIsNotNone(tokenizer.filler_words)
        self.assertGreater(tokenizer.comma_density_threshold, 0)
        self.assertGreater(tokenizer.stopword_frequency_threshold, 0)


class TestPromptTokenizerIntegration(unittest.TestCase):
    """Integration tests for PromptTokenizer with DatabaseManager."""

    def test_database_integration(self):
        """Test that tokenizer works correctly with database indexing."""
        from metascan.core.database_sqlite import DatabaseManager
        from metascan.core.media import Media
        from datetime import datetime

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir)
            db_manager = DatabaseManager(db_path)

            # Create test media with tag-style prompt
            test_media = Media(
                file_path=Path("/test/image.png"),
                file_size=1024,
                width=512,
                height=512,
                format="PNG",
                created_at=datetime.now(),
                modified_at=datetime.now(),
                metadata_source="test",
                prompt="portrait, woman, oil painting, renaissance style",
            )

            # Save and verify indexing
            success = db_manager.save_media(test_media)
            self.assertTrue(success)

            # Search for multi-word tags
            results = db_manager.search_by_index("prompt", "oil painting")
            self.assertEqual(len(results), 1)

            results = db_manager.search_by_index("prompt", "renaissance style")
            self.assertEqual(len(results), 1)

            # Individual words from tags should not be indexed
            results = db_manager.search_by_index("prompt", "oil")
            self.assertEqual(len(results), 0)

            db_manager.close()


if __name__ == "__main__":
    unittest.main()
