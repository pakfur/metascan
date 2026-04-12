# Find Similar Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate UI freezes in the "Find Similar" feature by caching the FAISS index, moving search to a background thread, and removing the expensive on-the-fly CLIP computation fallback.

**Architecture:** A `SimilaritySearchWorker(QThread)` handles FAISS load + search off the UI thread. The `FaissIndexManager` and similarity config are cached on `MainWindow` after first use and invalidated when the index is rebuilt. The on-the-fly CLIP path is replaced with a user message.

**Tech Stack:** PyQt6 (QThread, pyqtSignal), FAISS, numpy

---

### Task 1: Add SimilaritySearchWorker QThread class

**Files:**
- Modify: `metascan/ui/main_window.py` (add class after line ~605, near existing `ScanPreparationThread`)
- Test: `tests/test_similarity_search.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_similarity_search.py`:

```python
class TestSimilaritySearchWorker(unittest.TestCase):
    """Test the async similarity search worker."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_dir = Path(self.temp_dir.name)
        self.faiss_mgr = FaissIndexManager(self.index_dir)
        self.faiss_mgr.create(embedding_dim=8, model_key="test")

        np.random.seed(42)
        for i in range(10):
            vec = np.random.randn(8).astype(np.float32)
            vec = vec / np.linalg.norm(vec)
            self.faiss_mgr.add(f"file_{i:02d}.png", vec)
        self.faiss_mgr.save()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_worker_returns_results_for_indexed_file(self):
        from metascan.ui.main_window import SimilaritySearchWorker

        worker = SimilaritySearchWorker(
            faiss_mgr=self.faiss_mgr,
            file_path="file_00.png",
            top_k=5,
            index_dir=self.index_dir,
        )
        results = []
        errors = []
        worker.results_ready.connect(lambda r, mgr: results.append(r))
        worker.error.connect(lambda msg: errors.append(msg))
        worker.run()  # Call run() directly instead of start() for synchronous testing

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0][0], "file_00.png")  # Self is best match

    def test_worker_emits_error_for_unindexed_file(self):
        from metascan.ui.main_window import SimilaritySearchWorker

        worker = SimilaritySearchWorker(
            faiss_mgr=self.faiss_mgr,
            file_path="not_in_index.png",
            top_k=5,
            index_dir=self.index_dir,
        )
        results = []
        errors = []
        worker.results_ready.connect(lambda r, mgr: results.append(r))
        worker.error.connect(lambda msg: errors.append(msg))
        worker.run()

        self.assertEqual(len(results), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("hasn't been indexed", errors[0])

    def test_worker_loads_index_from_disk_when_not_cached(self):
        from metascan.ui.main_window import SimilaritySearchWorker

        worker = SimilaritySearchWorker(
            faiss_mgr=None,  # Not cached — worker must load from disk
            file_path="file_05.png",
            top_k=3,
            index_dir=self.index_dir,
        )
        results = []
        returned_mgrs = []
        worker.results_ready.connect(lambda r, mgr: (results.append(r), returned_mgrs.append(mgr)))
        worker.run()

        self.assertEqual(len(results), 1)
        self.assertGreater(len(results[0]), 0)
        # Worker should return the newly loaded manager for caching
        self.assertEqual(len(returned_mgrs), 1)
        self.assertIsNotNone(returned_mgrs[0])

    def test_worker_emits_error_when_no_index_on_disk(self):
        from metascan.ui.main_window import SimilaritySearchWorker

        empty_dir = tempfile.TemporaryDirectory()
        worker = SimilaritySearchWorker(
            faiss_mgr=None,
            file_path="file_00.png",
            top_k=5,
            index_dir=Path(empty_dir.name),
        )
        errors = []
        worker.error.connect(lambda msg: errors.append(msg))
        worker.run()

        self.assertEqual(len(errors), 1)
        self.assertIn("No embedding index found", errors[0])
        empty_dir.cleanup()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_similarity_search.py::TestSimilaritySearchWorker -v`
Expected: FAIL with `ImportError: cannot import name 'SimilaritySearchWorker'`

- [ ] **Step 3: Implement SimilaritySearchWorker**

Add to `metascan/ui/main_window.py` after the `ScanPreparationThread` class (after line ~605):

```python
class SimilaritySearchWorker(QThread):
    """Background worker for FAISS similarity search.

    Loads the index (if not cached) and performs the search off the UI thread.
    """

    results_ready = pyqtSignal(list, object)  # results, faiss_mgr
    error = pyqtSignal(str)  # error message

    def __init__(
        self,
        faiss_mgr,  # Optional[FaissIndexManager]
        file_path: str,
        top_k: int,
        index_dir,  # Path
        parent=None,
    ):
        super().__init__(parent)
        self.faiss_mgr = faiss_mgr
        self.file_path = file_path
        self.top_k = top_k
        self.index_dir = index_dir

    def run(self):
        try:
            from metascan.core.embedding_manager import FaissIndexManager

            # Load index if not cached
            if self.faiss_mgr is None:
                self.faiss_mgr = FaissIndexManager(self.index_dir)
                if not self.faiss_mgr.load():
                    self.error.emit(
                        "No embedding index found. Please build the similarity "
                        "index first via Tools > Similarity Settings."
                    )
                    return

            # Get embedding for query file
            embedding = self.faiss_mgr.get_embedding(self.file_path)
            if embedding is None:
                self.error.emit(
                    "This file hasn't been indexed yet. Please rebuild the "
                    "similarity index via Tools > Similarity Settings to include it."
                )
                return

            # Search
            results = self.faiss_mgr.search(embedding, top_k=self.top_k)
            self.results_ready.emit(results, self.faiss_mgr)

        except Exception as e:
            self.error.emit(f"Similarity search failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_similarity_search.py::TestSimilaritySearchWorker -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add metascan/ui/main_window.py tests/test_similarity_search.py
git commit -m "feat: add SimilaritySearchWorker QThread for async similarity search"
```

---

### Task 2: Add cache fields and invalidation method to MainWindow

**Files:**
- Modify: `metascan/ui/main_window.py:714-715` (add fields after `self.embedding_queue`)
- Modify: `metascan/ui/main_window.py:~1796` (add `_invalidate_similarity_cache` near `_load_similarity_config`)
- Test: `tests/test_similarity_search.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_similarity_search.py`:

```python
class TestSimilarityCacheInvalidation(unittest.TestCase):
    """Test that cache invalidation resets cached state."""

    def test_invalidate_clears_faiss_mgr(self):
        """_invalidate_similarity_cache should set _faiss_mgr to None."""
        from unittest.mock import MagicMock

        window = MagicMock()
        window._faiss_mgr = "something"
        window._similarity_config = {"clip_model": "small"}

        # Call the real method
        from metascan.ui.main_window import MainWindow
        MainWindow._invalidate_similarity_cache(window)

        self.assertIsNone(window._faiss_mgr)
        self.assertIsNone(window._similarity_config)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_similarity_search.py::TestSimilarityCacheInvalidation -v`
Expected: FAIL with `AttributeError: type object 'MainWindow' has no attribute '_invalidate_similarity_cache'`

- [ ] **Step 3: Add cache fields to MainWindow.__init__**

In `metascan/ui/main_window.py`, after line 714 (`self.embedding_queue = EmbeddingQueue(parent=self)`), add:

```python
        # Similarity search cache — loaded on first use, invalidated on index rebuild
        self._faiss_mgr = None  # Optional[FaissIndexManager]
        self._similarity_config = None  # Optional[Dict]
        self._similarity_worker = None  # Optional[SimilaritySearchWorker]
```

- [ ] **Step 4: Add _invalidate_similarity_cache method**

In `metascan/ui/main_window.py`, after `_load_similarity_config` (after line ~1805), add:

```python
    def _invalidate_similarity_cache(self):
        """Clear cached FAISS index and similarity config.

        Called after the similarity index is rebuilt or the CLIP model is changed.
        """
        self._faiss_mgr = None
        self._similarity_config = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_similarity_search.py::TestSimilarityCacheInvalidation -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add metascan/ui/main_window.py tests/test_similarity_search.py
git commit -m "feat: add similarity cache fields and invalidation method to MainWindow"
```

---

### Task 3: Rewrite _on_find_similar to use async worker and cache

**Files:**
- Modify: `metascan/ui/main_window.py:1725-1789` (replace `_on_find_similar`)

- [ ] **Step 1: Replace _on_find_similar**

Replace the entire `_on_find_similar` method (lines 1725-1789) in `metascan/ui/main_window.py` with:

```python
    def _on_find_similar(self, media):
        """Find media similar to the selected item using CLIP embeddings."""
        # Prevent stacking searches
        if self._similarity_worker is not None and self._similarity_worker.isRunning():
            self.statusBar().showMessage("Similarity search already in progress...")
            return

        from metascan.utils.app_paths import get_data_dir

        index_dir = get_data_dir() / "similarity"
        file_path_str = str(media.file_path)

        # Load config once and cache
        if self._similarity_config is None:
            self._similarity_config = self._load_similarity_config()

        top_k = self._similarity_config.get("search_results_count", 50)

        # Show loading status if index not yet cached
        if self._faiss_mgr is None:
            self.statusBar().showMessage("Loading similarity index...")

        self._similarity_search_media = media  # Store for the callback

        self._similarity_worker = SimilaritySearchWorker(
            faiss_mgr=self._faiss_mgr,
            file_path=file_path_str,
            top_k=top_k,
            index_dir=index_dir,
            parent=self,
        )
        self._similarity_worker.results_ready.connect(self._on_similarity_results)
        self._similarity_worker.error.connect(self._on_similarity_error)
        self._similarity_worker.finished.connect(self._on_similarity_finished)
        self._similarity_worker.start()

    def _on_similarity_results(self, results, faiss_mgr):
        """Handle results from the similarity search worker."""
        # Cache the manager for future searches
        self._faiss_mgr = faiss_mgr

        if not results:
            self.statusBar().showMessage("No similar items found.")
            return

        # Filter the view to show matching files
        score_map = {r[0]: r[1] for r in results}
        matching_paths = set(score_map.keys())

        filtered_media = [
            m for m in self.all_media if str(m.file_path) in matching_paths
        ]
        filtered_media.sort(
            key=lambda m: score_map.get(str(m.file_path), 0), reverse=True
        )

        media = self._similarity_search_media
        self.thumbnail_view.set_media_list(filtered_media)
        self.statusBar().showMessage(
            f"Showing {len(filtered_media)} items similar to '{media.file_name}'"
        )

    def _on_similarity_error(self, error_msg):
        """Handle errors from the similarity search worker."""
        from PyQt6.QtWidgets import QMessageBox

        self.statusBar().clearMessage()
        QMessageBox.information(self, "Find Similar", error_msg)

    def _on_similarity_finished(self):
        """Clean up after similarity search worker completes."""
        self._similarity_worker = None
```

- [ ] **Step 2: Remove the old on-the-fly CLIP import**

The old method had `from metascan.core.embedding_manager import EmbeddingManager, FaissIndexManager` at line 1727. The new `_on_find_similar` no longer imports anything from `embedding_manager` — the import is inside `SimilaritySearchWorker.run()`. Verify the old import line is gone after the replacement.

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/test_similarity_search.py -v`
Expected: All tests PASS

- [ ] **Step 4: Run type checker**

Run: `mypy metascan/ui/main_window.py`
Expected: No new errors (UI modules have relaxed type checking)

- [ ] **Step 5: Commit**

```bash
git add metascan/ui/main_window.py
git commit -m "feat: rewrite _on_find_similar to use async worker with cached FAISS index"
```

---

### Task 4: Wire up cache invalidation from SimilaritySettingsDialog

**Files:**
- Modify: `metascan/ui/similarity_settings_dialog.py:355-363` (in `_on_complete`)
- Modify: `metascan/ui/similarity_settings_dialog.py:290-322` (in `_rebuild_index`)
- Test: `tests/test_similarity_search.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_similarity_search.py`:

```python
class TestCacheInvalidationOnIndexRebuild(unittest.TestCase):
    """Test that index rebuild invalidates the MainWindow similarity cache."""

    def test_on_complete_invalidates_cache(self):
        """_on_complete in SimilaritySettingsDialog should call _invalidate_similarity_cache."""
        from unittest.mock import MagicMock, patch

        mock_parent = MagicMock()
        mock_parent._invalidate_similarity_cache = MagicMock()

        mock_db = MagicMock()
        mock_db.get_embedding_stats.return_value = {
            "total_media": 0, "hashed": 0, "embedded": 0, "clip_model": None
        }
        mock_queue = MagicMock()
        mock_queue.is_indexing.return_value = False

        with patch("metascan.ui.similarity_settings_dialog.get_data_dir"):
            from metascan.ui.similarity_settings_dialog import SimilaritySettingsDialog
            dialog = SimilaritySettingsDialog(mock_db, mock_queue, parent=mock_parent)
            dialog._on_complete(100)

        mock_parent._invalidate_similarity_cache.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_similarity_search.py::TestCacheInvalidationOnIndexRebuild -v`
Expected: FAIL with `AssertionError: Expected '_invalidate_similarity_cache' to be called`

- [ ] **Step 3: Add invalidation call to _on_complete**

In `metascan/ui/similarity_settings_dialog.py`, modify the `_on_complete` method (line 355). Add after `self._update_index_status()` (line 362):

```python
        # Invalidate MainWindow's cached FAISS index so next search picks up changes
        parent = self.parent()
        if parent is not None and hasattr(parent, "_invalidate_similarity_cache"):
            parent._invalidate_similarity_cache()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_similarity_search.py::TestCacheInvalidationOnIndexRebuild -v`
Expected: PASS

- [ ] **Step 5: Run all tests and quality checks**

Run: `make quality test`
Expected: All checks pass, no mypy errors, all tests pass

- [ ] **Step 6: Commit**

```bash
git add metascan/ui/similarity_settings_dialog.py tests/test_similarity_search.py
git commit -m "feat: invalidate similarity cache when index rebuild completes"
```

---

### Task 5: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run code quality checks**

Run: `make quality`
Expected: No formatting or type errors

- [ ] **Step 3: Verify no remaining references to old on-the-fly CLIP path**

Run: `grep -n "compute_image_embedding\|compute_video_embedding\|unload_model" metascan/ui/main_window.py`
Expected: No matches (the old CLIP computation path is fully removed)

- [ ] **Step 4: Verify the EmbeddingManager import is removed from _on_find_similar**

Run: `grep -n "EmbeddingManager" metascan/ui/main_window.py`
Expected: No matches in the `_on_find_similar` area (may still exist elsewhere for other features — that's fine)

- [ ] **Step 5: Review the diff**

Run: `git diff HEAD~4 --stat` to confirm only the expected files were changed:
- `metascan/ui/main_window.py`
- `metascan/ui/similarity_settings_dialog.py`
- `tests/test_similarity_search.py`
