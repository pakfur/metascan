# Find Similar Performance Improvements

## Problem

The "Find Similar" feature (right-click context menu on media files) blocks the UI thread during execution. The FAISS index is loaded from disk on every invocation, the similarity config is read twice per call, and an expensive on-the-fly CLIP model load+inference path exists as a fallback for unindexed files. At 10K-50K file collections, the FAISS search itself is fast (<10ms), but the surrounding IO and model operations cause noticeable freezes.

## Design

### 1. Cached FaissIndexManager

Cache the `FaissIndexManager` instance on `MainWindow` so it is loaded from disk once and reused across searches.

- Add `self._faiss_mgr: Optional[FaissIndexManager] = None` to `MainWindow.__init__`.
- On the first "Find Similar" call, load from disk and store on `self._faiss_mgr`.
- Subsequent calls skip disk IO entirely.
- Also cache the similarity config dict (`self._similarity_config`) to eliminate the redundant double read of `config.json` within `_on_find_similar`.

### 2. Async Worker Thread

Move FAISS load + search off the UI thread using a QThread-based worker.

- Define `SimilaritySearchWorker(QThread)` in `main_window.py`, co-located with `_on_find_similar`.
- The worker accepts:
  - The cached `FaissIndexManager` (or `None` if not yet loaded, in which case it loads it).
  - The query file path string.
  - `top_k` search parameter.
  - The index directory path (needed if loading for the first time).
- The worker:
  - Calls `faiss_mgr.load()` if the index is not yet loaded. If load fails (no index on disk), emits `error` with a "no index" message.
  - Calls `faiss_mgr.get_embedding(file_path)` to retrieve the query vector. If `None` (file not in index), emits `error` with a "not indexed" message.
  - Otherwise calls `faiss_mgr.search(embedding, top_k)` and emits `results_ready` with the results list and the (possibly newly loaded) `FaissIndexManager`.
- In `_on_find_similar`:
  - If the index is not cached, show a status bar message: "Loading similarity index..."
  - Spawn the worker, connect `results_ready` and `error` signals.
  - Disable the "Find Similar" action while a search is in progress to prevent stacking.
  - Keep a reference to the worker (`self._similarity_worker`) to prevent garbage collection.
- On `results_ready`:
  - Cache the `FaissIndexManager` on `self._faiss_mgr`.
  - Filter `self.all_media` by matching paths and sort by score (runs on UI thread but is a fast list operation).
  - Update the thumbnail view.

### 3. Remove On-the-fly CLIP Computation

Replace the fallback CLIP inference path with a user-friendly message.

- When `get_embedding()` returns `None`, show a `QMessageBox.information`: "This file hasn't been indexed yet. Please rebuild the similarity index via Tools > Similarity Settings to include it."
- Delete the entire block in `_on_find_similar` that instantiates `EmbeddingManager`, calls `compute_image_embedding`/`compute_video_embedding`, and calls `unload_model()`.
- Remove the `EmbeddingManager` import from `_on_find_similar` (keep `FaissIndexManager` only).

### 4. Cache Invalidation

- Add `_invalidate_similarity_cache()` method to `MainWindow` that sets `self._faiss_mgr = None` and `self._similarity_config = None`.
- Call this method after the similarity index is rebuilt via the Similarity Settings dialog.
- Call this method when the user changes the CLIP model in settings.

## Files Changed

| File | Changes |
|---|---|
| `metascan/ui/main_window.py` | Add cache fields in `__init__`, define `SimilaritySearchWorker(QThread)`, rewrite `_on_find_similar` to use async worker + cached index, add `_invalidate_similarity_cache()`, remove on-the-fly CLIP path, fix double config read |
| `metascan/ui/similarity_settings_dialog.py` | Call `_invalidate_similarity_cache()` on parent window after index rebuild completes |

No new files. No changes to `embedding_manager.py` or the FAISS index format.

## What This Does NOT Change

- The FAISS index type remains `IndexFlatIP` (brute-force). At 50K vectors this is <10ms and does not warrant the complexity of an IVF index.
- The index is not pre-loaded at app startup. It loads on first use, then stays cached.
- The embedding worker subprocess and index build pipeline are untouched.
