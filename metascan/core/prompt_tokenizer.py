import logging
from typing import Set, Dict, Any, Tuple, Optional
from pathlib import Path
import json
import sys

from metascan.utils.startup_profiler import log_startup

log_startup("prompt_tokenizer.py: Module loading started")

logger = logging.getLogger(__name__)

# Flag to track if NLTK has been initialized
_nltk_initialized = False
_nltk_stopwords: Optional[Set[str]] = None


def _ensure_nltk_initialized() -> Set[str]:
    """
    Lazily initialize NLTK and load stopwords.
    Returns the set of English stopwords.
    """
    global _nltk_initialized, _nltk_stopwords

    if _nltk_initialized:
        return _nltk_stopwords or set()

    log_startup("prompt_tokenizer.py: Importing NLTK (lazy)...")

    import nltk
    from nltk.corpus import stopwords

    log_startup("prompt_tokenizer.py: NLTK import complete")

    # Setup NLTK data path for bundled application
    if getattr(sys, "frozen", False):
        # In bundled app, use user directory for NLTK data
        nltk_data_dir = Path.home() / ".metascan" / "nltk_data"
        nltk_data_dir.mkdir(parents=True, exist_ok=True)
        nltk.data.path.insert(0, str(nltk_data_dir))

    log_startup("prompt_tokenizer.py: Checking NLTK stopwords data...")
    try:
        nltk.data.find("corpora/stopwords")
    except LookupError:
        try:
            log_startup("prompt_tokenizer.py: Downloading NLTK stopwords...")
            nltk.download("stopwords", quiet=True)
        except:
            # If download fails in bundled app, use empty set
            logger.warning("Could not download NLTK stopwords, using empty set")

    # Load stopwords
    try:
        log_startup("prompt_tokenizer.py: Loading NLTK stopwords...")
        _nltk_stopwords = set(stopwords.words("english"))
    except LookupError:
        logger.warning("NLTK stopwords not available, using empty set")
        _nltk_stopwords = set()

    _nltk_initialized = True
    log_startup("prompt_tokenizer.py: NLTK initialization complete")

    return _nltk_stopwords


log_startup("prompt_tokenizer.py: Module loading complete")


class PromptTokenizer:
    def __init__(
        self,
        stop_words: Optional[Set[str]] = None,
        filler_words: Optional[Set[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        log_startup("      PromptTokenizer.__init__: Starting")
        if stop_words is not None:
            self.stop_words = stop_words
        else:
            # Lazy load NLTK stopwords
            self.stop_words = _ensure_nltk_initialized()

        if config is not None:
            self.config = config
        else:
            log_startup("      PromptTokenizer: Loading config...")
            self.config = self._load_config()

        if filler_words is not None:
            self.filler_words = filler_words
        else:
            self.filler_words = set(
                word.lower() for word in self.config.get("filler_words", [])
            )

        self.tokenization_config = self.config.get("tokenization", {})
        self.heuristics_config = self.tokenization_config.get("heuristics", {})

        comma_config = self.heuristics_config.get("comma_density", {})
        self.comma_density_threshold = comma_config.get("threshold", 0.2)
        self.comma_density_weight = comma_config.get("weight", 0.6)

        stopword_config = self.heuristics_config.get("stopword_frequency", {})
        self.stopword_frequency_threshold = stopword_config.get("threshold", 0.15)
        self.stopword_frequency_weight = stopword_config.get("weight", 0.4)

        self.classification_threshold = self.tokenization_config.get(
            "classification_threshold", 0.5
        )

    def _load_config(self) -> Dict[str, Any]:
        from metascan.utils.app_paths import get_config_path

        config = {}
        config_path = get_config_path()
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")
        return config

    def _calculate_comma_density(self, prompt: str) -> float:
        if not prompt:
            return 0.0

        comma_count = prompt.count(",")
        words = prompt.split()
        word_count = len(words)

        if word_count == 0:
            return 0.0

        return comma_count / word_count

    def _calculate_stopword_frequency(self, prompt: str) -> float:
        if not prompt:
            return 0.0

        words = [word.lower().strip(".,!?;:()[]{}\"'-") for word in prompt.split()]
        word_count = len(words)

        if word_count == 0:
            return 0.0

        stopword_count = sum(1 for word in words if word in self.stop_words)
        return stopword_count / word_count

    def classify_prompt(self, prompt: str) -> Tuple[str, float]:
        comma_density = self._calculate_comma_density(prompt)
        stopword_frequency = self._calculate_stopword_frequency(prompt)

        comma_score = 1.0 if comma_density > self.comma_density_threshold else 0.0

        stopword_score = (
            0.0 if stopword_frequency > self.stopword_frequency_threshold else 1.0
        )

        tag_score = (
            comma_score * self.comma_density_weight
            + stopword_score * self.stopword_frequency_weight
        )

        if tag_score > self.classification_threshold:
            return ("tags", tag_score)
        else:
            return ("caption", 1.0 - tag_score)

    def _tokenize_tags(self, prompt: str) -> Set[str]:
        tags = set()

        raw_tags = [tag.strip() for tag in prompt.split(",")]

        for tag in raw_tags:
            if not tag:
                continue

            words = tag.lower().split()

            filtered_words = []
            for word in words:
                # Remove punctuation
                word = word.strip(".,!?;:()[]{}\"'-")

                # Keep the word if it's not a stop word or filler word
                if (
                    word
                    and word not in self.stop_words
                    and word not in self.filler_words
                ):
                    filtered_words.append(word)

            if filtered_words:
                normalized_tag = " ".join(filtered_words)
                tags.add(normalized_tag)

        return tags

    def _tokenize_caption(self, prompt: str) -> Set[str]:
        words = [word.lower() for word in prompt.split()]

        filtered_words = set()
        for word in words:
            word = word.strip(".,!?;:()[]{}\"'-")

            if (
                len(word) > 2
                and word not in self.stop_words
                and word not in self.filler_words
            ):
                filtered_words.add(word)

        return filtered_words

    def tokenize(self, prompt: str) -> Set[str]:
        if not prompt:
            return set()

        prompt_type, confidence = self.classify_prompt(prompt)
        logger.info(
            f"Prompt classified as '{prompt_type}' with confidence {confidence:.2f}"
        )

        if prompt_type == "tags":
            tokens = self._tokenize_tags(prompt)
            logger.debug(f"Tag tokenization produced {len(tokens)} tags")
        else:
            tokens = self._tokenize_caption(prompt)
            logger.debug(f"Caption tokenization produced {len(tokens)} words")

        return tokens
