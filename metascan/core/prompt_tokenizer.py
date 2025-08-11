import logging
from typing import Set, Dict, Any, Tuple
from pathlib import Path
import json
import nltk
from nltk.corpus import stopwords
import sys

logger = logging.getLogger(__name__)

# Setup NLTK data path for bundled application
if getattr(sys, 'frozen', False):
    # In bundled app, use user directory for NLTK data
    nltk_data_dir = Path.home() / '.metascan' / 'nltk_data'
    nltk_data_dir.mkdir(parents=True, exist_ok=True)
    nltk.data.path.insert(0, str(nltk_data_dir))

# Download stopwords if not already available
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    try:
        nltk.download('stopwords', quiet=True)
    except:
        # If download fails in bundled app, use empty set
        logger.warning("Could not download NLTK stopwords, using empty set")


class PromptTokenizer:
    """Handles tokenization and filtering of prompts for indexing."""
    
    def __init__(self, stop_words: Set[str] = None, filler_words: Set[str] = None, config: Dict[str, Any] = None):
        """
        Initialize the PromptTokenizer.
        
        Args:
            stop_words: Set of stop words to filter out. If None, uses NLTK English stopwords.
            filler_words: Set of additional filler words to filter out. If None, loads from config.
            config: Configuration dict. If None, loads from config.json.
        """
        # Load stop words
        if stop_words is not None:
            self.stop_words = stop_words
        else:
            try:
                self.stop_words = set(stopwords.words('english'))
            except LookupError:
                # Fallback to empty set if stopwords not available
                logger.warning("NLTK stopwords not available, using empty set")
                self.stop_words = set()
        
        # Load configuration
        if config is not None:
            self.config = config
        else:
            self.config = self._load_config()
        
        # Load filler words
        if filler_words is not None:
            self.filler_words = filler_words
        else:
            self.filler_words = set(word.lower() for word in self.config.get('filler_words', []))
        
        # Load tokenization settings
        self.tokenization_config = self.config.get('tokenization', {})
        self.heuristics_config = self.tokenization_config.get('heuristics', {})
        
        # Comma density heuristic settings
        comma_config = self.heuristics_config.get('comma_density', {})
        self.comma_density_threshold = comma_config.get('threshold', 0.2)
        self.comma_density_weight = comma_config.get('weight', 0.6)
        
        # Stopword frequency heuristic settings
        stopword_config = self.heuristics_config.get('stopword_frequency', {})
        self.stopword_frequency_threshold = stopword_config.get('threshold', 0.15)
        self.stopword_frequency_weight = stopword_config.get('weight', 0.4)
        
        # Classification threshold
        self.classification_threshold = self.tokenization_config.get('classification_threshold', 0.5)
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from config file."""
        from metascan.utils.app_paths import get_config_path
        config = {}
        config_path = get_config_path()
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")
        return config
    
    def _calculate_comma_density(self, prompt: str) -> float:
        """Calculate the ratio of commas to words in the prompt."""
        if not prompt:
            return 0.0
        
        comma_count = prompt.count(',')
        words = prompt.split()
        word_count = len(words)
        
        if word_count == 0:
            return 0.0
        
        return comma_count / word_count
    
    def _calculate_stopword_frequency(self, prompt: str) -> float:
        """Calculate the ratio of stopwords to total words in the prompt."""
        if not prompt:
            return 0.0
        
        words = [word.lower().strip('.,!?;:()[]{}"\'-') for word in prompt.split()]
        word_count = len(words)
        
        if word_count == 0:
            return 0.0
        
        stopword_count = sum(1 for word in words if word in self.stop_words)
        return stopword_count / word_count
    
    def classify_prompt(self, prompt: str) -> Tuple[str, float]:
        """
        Classify a prompt as 'tags' or 'caption' based on heuristics.
        
        Returns:
            Tuple of (prompt_type, confidence_score)
        """
        # Calculate heuristic scores
        comma_density = self._calculate_comma_density(prompt)
        stopword_frequency = self._calculate_stopword_frequency(prompt)
        
        # Calculate weighted scores
        # For tags: high comma density (above threshold) contributes positively
        # For captions: low comma density (below threshold) contributes positively
        comma_score = 1.0 if comma_density > self.comma_density_threshold else 0.0
        
        # For tags: low stopword frequency (below threshold) contributes positively  
        # For captions: high stopword frequency (above threshold) contributes positively
        stopword_score = 0.0 if stopword_frequency > self.stopword_frequency_threshold else 1.0
        
        # Calculate weighted average for tag classification
        tag_score = (comma_score * self.comma_density_weight + 
                     stopword_score * self.stopword_frequency_weight)
        
        # Determine classification
        if tag_score > self.classification_threshold:
            return ("tags", tag_score)
        else:
            return ("caption", 1.0 - tag_score)
    
    def _tokenize_tags(self, prompt: str) -> Set[str]:
        """
        Tokenize a tag-based prompt.
        Each tag (text between commas) is normalized and kept as a unit.
        """
        tags = set()
        
        # Split by commas to get individual tags
        raw_tags = [tag.strip() for tag in prompt.split(',')]
        
        for tag in raw_tags:
            if not tag:
                continue
            
            # Normalize: lowercase and split into words
            words = tag.lower().split()
            
            # Remove stop words and filler words from within the tag
            filtered_words = []
            for word in words:
                # Remove punctuation
                word = word.strip('.,!?;:()[]{}"\'-')
                
                # Keep the word if it's not a stop word or filler word
                if word and word not in self.stop_words and word not in self.filler_words:
                    filtered_words.append(word)
            
            # Join the filtered words back into a tag
            if filtered_words:
                normalized_tag = ' '.join(filtered_words)
                tags.add(normalized_tag)
        
        return tags
    
    def _tokenize_caption(self, prompt: str) -> Set[str]:
        """
        Tokenize a caption-based prompt.
        Individual words are extracted after filtering.
        """
        # Tokenize and convert to lowercase
        words = [word.lower() for word in prompt.split()]
        
        # Filter out stop words, filler words, and short words
        filtered_words = set()
        for word in words:
            # Remove punctuation from word edges
            word = word.strip('.,!?;:()[]{}"\'-')
            
            # Skip if word is too short, is a stop word, or is a filler word
            if (len(word) > 2 and 
                word not in self.stop_words and 
                word not in self.filler_words):
                filtered_words.add(word)
        
        return filtered_words
    
    def tokenize(self, prompt: str) -> Set[str]:
        """
        Tokenize and filter a prompt string using appropriate strategy.
        
        Args:
            prompt: The prompt string to tokenize
            
        Returns:
            Set of filtered tokens suitable for indexing
        """
        if not prompt:
            return set()
        
        # Classify the prompt
        prompt_type, confidence = self.classify_prompt(prompt)
        logger.info(f"Prompt classified as '{prompt_type}' with confidence {confidence:.2f}")
        
        # Use appropriate tokenization strategy
        if prompt_type == "tags":
            tokens = self._tokenize_tags(prompt)
            logger.debug(f"Tag tokenization produced {len(tokens)} tags")
        else:
            tokens = self._tokenize_caption(prompt)
            logger.debug(f"Caption tokenization produced {len(tokens)} words")
        
        return tokens