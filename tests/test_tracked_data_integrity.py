"""The suite redirects app paths to a temp dir, so the tracked files under
``data/`` are no longer exercised implicitly. Load them by repo-relative path."""

from pathlib import Path

from metascan.core.prompt_store import PromptStore
from metascan.core.vocabulary import load_vocabulary

DATA = Path(__file__).resolve().parent.parent / "data"


def test_tracked_meta_prompt_loads():
    store = PromptStore(DATA / "meta_prompt.yml")
    keys = store.keys()
    assert keys
    assert all(store.get(k).strip() for k in keys)


def test_tracked_vocabulary_loads():
    terms, axes, fingerprint = load_vocabulary(DATA / "vocabulary")
    assert terms and len(terms) == len(axes)
    assert fingerprint
