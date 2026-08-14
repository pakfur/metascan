# Content Search as a Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move content search (CLIP text→image) and Find Similar (image→image) out of their parallel "grid swap" universe and into the standard filter pipeline: a SEARCH section in the left filter panel whose results intersect with view presets, FILTERS, and folder scope, unbounded by score threshold, with a new tag-AND search and a Relevance sort option.

**Architecture:** Backend search endpoints become light and unbounded (`[{file_path, similarity_score}]`, threshold applied server-side, k = index size). A new Pinia `useSearchStore` holds a `Map<path, score>` and a tag-AND `Set<path>`; `mediaStore.displayedMedia` intersects both exactly like `filteredPaths`. The old similarity store, SimilarityBanner, and every `simStore.active ? … : …` grid swap are deleted.

**Tech Stack:** FastAPI + FAISS (backend), Vue 3 `<script setup>` + Pinia + PrimeVue (frontend, AutoComplete already globally registered).

**Spec:** `docs/superpowers/specs/2026-08-14-content-search-filter-integration-design.md`

## Global Constraints

- `make quality test` (flake8 + black --check + mypy + pytest) must pass at every commit. `mypy` is strict on `metascan/core/*`.
- `cd frontend && npm run build` (vue-tsc + vite) must pass at every commit.
- Python 3.11; `black` v25.11.0 formatting.
- API responses carry **native** paths (`metascan.utils.path_utils.to_native_path`), matching `/api/media`.
- Thresholds: text↔image default **0.2**, slider range 0–0.45 step 0.01; image↔image default **0.7**, range 0–1 step 0.05. Threshold is applied **server-side**; slider changes re-run the active search.
- Known full-suite flake on WSL2: `test_file_watcher_triggers_reload` may fail in a full run and pass in isolation — not a regression signal.
- Transitional note: Tasks 2–4 build alongside the legacy similarity store; each commit type-checks and builds, but the legacy search UI is only fully replaced at Task 5. Don't "fix" the legacy path in between.

---

### Task 1: Backend — light unbounded search endpoints

**Files:**
- Modify: `metascan/core/embedding_manager.py` (FaissIndexManager, ~line 555–600: add `size` property)
- Modify: `backend/api/similarity.py` (request models ~line 231–239, `/search` route ~line 278–325, `/content-search` route ~line 328–360)
- Test: `tests/test_search_endpoints_light.py` (new)

**Interfaces:**
- Consumes: existing `FaissIndexManager.search(vec, top_k) -> List[Tuple[str, float]]`, `fm.is_loaded`, `fm.meta`; `InferenceClient.encode_text/encode_image/encode_video`.
- Produces: `POST /api/similarity/content-search` body `{query: str, threshold: float = 0.0, max_results: Optional[int] = None}` and `POST /api/similarity/search` body `{file_path: str, threshold: float = 0.7, max_results: Optional[int] = None}`; both return `[{"file_path": <native str>, "similarity_score": <float>}]` sorted by score descending, unbounded when `max_results` is null. New `FaissIndexManager.size: int` property. Dim-mismatch 409 and 503-no-index behaviors unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_search_endpoints_light.py`:

```python
"""API tests for the light unbounded similarity search endpoints."""

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from metascan.core.embedding_manager import FaissIndexManager

DIM = 32  # FAISS test vectors must use dim >= 32 (ARM SIMD alignment)


def _vec_with_cos(c: float) -> np.ndarray:
    """Unit vector whose cosine against the base query vector is exactly c."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = c
    v[1] = np.sqrt(max(0.0, 1.0 - c * c))
    return v


BASE = _vec_with_cos(1.0)


class FakeInferenceClient:
    async def encode_text(self, query):
        return BASE

    async def encode_image(self, file_path):
        return BASE

    async def encode_video(self, file_path, num_keyframes=4):
        return BASE


@pytest.fixture()
def light_api(monkeypatch):
    from backend.api import similarity as sim_api

    tmp = tempfile.TemporaryDirectory()
    fm = FaissIndexManager(Path(tmp.name))
    fm.create(embedding_dim=DIM, model_key="test")
    fm.add("/lib/high.png", _vec_with_cos(0.9))
    fm.add("/lib/mid.png", _vec_with_cos(0.5))
    fm.add("/lib/low.png", _vec_with_cos(0.1))

    async def noop_ready(client):
        pass

    monkeypatch.setattr(sim_api, "_faiss_manager", fm)
    monkeypatch.setattr(sim_api, "_inference_client", FakeInferenceClient())
    monkeypatch.setattr(sim_api, "_ensure_worker_ready", noop_ready)
    yield sim_api
    tmp.cleanup()


def test_faiss_manager_size_property(light_api):
    from backend.api import similarity as sim_api

    assert sim_api._get_faiss_manager().size == 3


def test_content_search_unbounded_light_shape(light_api):
    body = light_api.ContentSearchRequest(query="anything")
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == [
        "/lib/high.png",
        "/lib/mid.png",
        "/lib/low.png",
    ]
    assert all(set(o) == {"file_path", "similarity_score"} for o in out)
    assert out[0]["similarity_score"] == pytest.approx(0.9, abs=1e-3)


def test_content_search_threshold_filters(light_api):
    body = light_api.ContentSearchRequest(query="anything", threshold=0.4)
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == ["/lib/high.png", "/lib/mid.png"]


def test_content_search_max_results_caps(light_api):
    body = light_api.ContentSearchRequest(query="anything", max_results=1)
    out = asyncio.run(light_api.content_search(body))
    assert [o["file_path"] for o in out] == ["/lib/high.png"]


def test_search_similar_light_shape(light_api):
    body = light_api.SimilaritySearchRequest(file_path="/lib/query.png", threshold=0.0)
    out = asyncio.run(light_api.search_similar(body))
    assert [o["file_path"] for o in out] == [
        "/lib/high.png",
        "/lib/mid.png",
        "/lib/low.png",
    ]
    assert all(set(o) == {"file_path", "similarity_score"} for o in out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && pytest tests/test_search_endpoints_light.py -v`
Expected: FAIL — `size` attribute missing; `content_search()` missing required `service` argument / wrong response shape.

- [ ] **Step 3: Implement**

In `metascan/core/embedding_manager.py`, inside `FaissIndexManager` next to the existing `is_loaded` property (~line 558):

```python
    @property
    def size(self) -> int:
        """Number of vectors currently in the index (0 if not loaded)."""
        return int(self._index.ntotal) if self._index is not None else 0
```

In `backend/api/similarity.py`:

1. Add to imports: `from metascan.utils.path_utils import to_native_path` and extend the typing import with `List` (`from typing import Any, Dict, List, Optional`).
2. Update request models:

```python
class SimilaritySearchRequest(BaseModel):
    file_path: str
    threshold: float = 0.7
    max_results: Optional[int] = None


class ContentSearchRequest(BaseModel):
    query: str
    threshold: float = 0.0
    max_results: Optional[int] = None
```

3. Replace both route bodies. Drop the `service: MediaService = Depends(_get_service)` parameter from **both** routes (the light shape needs no DB lookups). Keep the existing `is_video` suffix check, `encode_*` calls, `InferenceError` → 400, and `_assert_dim_matches` exactly as they are; only the search/format tail changes. Shared tail for both routes:

```python
    _assert_dim_matches(fm, int(vec.shape[0]))

    k = body.max_results if body.max_results else fm.size
    raw = await asyncio.to_thread(fm.search, vec, k)

    return [
        {"file_path": to_native_path(p), "similarity_score": float(s)}
        for p, s in raw
        if float(s) >= body.threshold
    ]
```

Annotate both routes `-> List[Dict[str, Any]]`. `MediaService` / `Depends` / `_get_service` stay imported — other routes in the module still use them.

- [ ] **Step 4: Run tests and the quality gate**

Run: `pytest tests/test_search_endpoints_light.py -v` → all PASS.
Run: `make quality test` → PASS (flake8/black/mypy included; watcher flake exempt per Global Constraints).

- [ ] **Step 5: Commit**

```bash
git add metascan/core/embedding_manager.py backend/api/similarity.py tests/test_search_endpoints_light.py
git commit -m "feat(search): light unbounded similarity endpoints with server-side threshold"
```

---

### Task 2: Frontend — API layer + `useSearchStore`

**Files:**
- Modify: `frontend/src/api/similarity.ts` (replace `SimilarityResult`, `searchSimilar`, `contentSearch`)
- Create: `frontend/src/stores/search.ts`

**Interfaces:**
- Consumes: Task 1's endpoint contract; existing `fetchTagPaths(keys: string[]): Promise<Record<string, string[]>>` from `frontend/src/api/filters.ts`; `useModelsStore` (`inferenceState`, `isInferenceReady`, `inferenceProgress`, `inferenceError`, `startInferenceWorker()`, `rebuildIndex()`); `useMediaStore` (`sortOrder`, `setSortOrder`).
- Produces: `useSearchStore` with state `textQuery: string`, `similarTo: Media | null`, `tagChips: string[]`, `textThreshold: number`, `imageThreshold: number`, `scores: Map<string, number> | null`, `tagPaths: Set<string> | null`, `loading`, `pending`, `dimMismatch`, `searchError`; computeds `similarityActive`, `active`, `isTextSearch`, `threshold`; actions `submitText(query)`, `submitSimilar(media)`, `searchTags(keys)`, `setThreshold(value)`, `clearSimilarity()`, `clearTags()`, `clearAll()`, `clearSearchError()`. Exported constants `TEXT_THRESHOLD_MAX = 0.45`, `TEXT_THRESHOLD_DEFAULT = 0.2`, `IMAGE_THRESHOLD_DEFAULT = 0.7`. Also `SearchHit {file_path, similarity_score}` from `api/similarity.ts`.

- [ ] **Step 1: Update `frontend/src/api/similarity.ts`**

Delete the `SimilarityResult` interface and replace the two search functions (everything else in the file is unchanged):

```ts
export interface SearchHit {
  file_path: string
  similarity_score: number
}

export function searchSimilar(
  filePath: string,
  threshold = 0.7,
  maxResults: number | null = null,
): Promise<SearchHit[]> {
  return post<SearchHit[]>('/similarity/search', {
    file_path: filePath,
    threshold,
    ...(maxResults !== null ? { max_results: maxResults } : {}),
  })
}

export function contentSearch(
  query: string,
  threshold = 0,
  maxResults: number | null = null,
): Promise<SearchHit[]> {
  return post<SearchHit[]>('/similarity/content-search', {
    query,
    threshold,
    ...(maxResults !== null ? { max_results: maxResults } : {}),
  })
}
```

(Defaulted params keep the legacy `stores/similarity.ts` compiling until Task 5 deletes it.)

- [ ] **Step 2: Create `frontend/src/stores/search.ts`**

```ts
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { Media } from '../types/media'
import { searchSimilar, contentSearch } from '../api/similarity'
import { fetchTagPaths } from '../api/filters'
import { ApiError } from '../api/client'
import { useMediaStore } from './media'
import { useModelsStore } from './models'

export interface DimMismatch {
  message: string
  index_dim: number
  model_dim: number
  index_model_key: string | null
}

// Text↔image CLIP cosine scores live on a much lower scale (~0.15–0.35)
// than image↔image (~0.4–0.9), so the two modes keep separate thresholds
// and slider calibrations.
export const TEXT_THRESHOLD_MAX = 0.45
export const TEXT_THRESHOLD_DEFAULT = 0.2
export const IMAGE_THRESHOLD_DEFAULT = 0.7

type PendingSearch =
  | { kind: 'text'; query: string }
  | { kind: 'similar'; media: Media }

export const useSearchStore = defineStore('search', () => {
  const textQuery = ref('')
  const similarTo = ref<Media | null>(null)
  const tagChips = ref<string[]>([])
  const textThreshold = ref(TEXT_THRESHOLD_DEFAULT)
  const imageThreshold = ref(IMAGE_THRESHOLD_DEFAULT)
  const scores = ref<Map<string, number> | null>(null)
  const tagPaths = ref<Set<string> | null>(null)
  const loading = ref(false)
  const pending = ref<PendingSearch | null>(null)
  const dimMismatch = ref<DimMismatch | null>(null)
  const searchError = ref<string | null>(null)

  const similarityActive = computed(() => scores.value !== null)
  const active = computed(() => scores.value !== null || tagPaths.value !== null)
  const isTextSearch = computed(() => similarTo.value === null)
  const threshold = computed(() =>
    isTextSearch.value ? textThreshold.value : imageThreshold.value,
  )

  const modelsStore = useModelsStore()

  // Coalesce: a search submitted before the inference worker is ready is
  // remembered (most-recent wins) and fired once the worker reaches ready.
  watch(
    () => modelsStore.inferenceState,
    (state) => {
      if (state === 'ready' && pending.value) {
        const p = pending.value
        pending.value = null
        if (p.kind === 'text') void runText(p.query)
        else void runSimilar(p.media)
      }
    },
  )

  function extractDimMismatch(e: unknown): DimMismatch | null {
    if (!(e instanceof ApiError) || e.status !== 409) return null
    const d = e.detail as Record<string, unknown> | null
    if (!d || d.code !== 'dim_mismatch') return null
    return {
      message: typeof d.message === 'string' ? d.message : 'index/model dim mismatch',
      index_dim: Number(d.index_dim) || 0,
      model_dim: Number(d.model_dim) || 0,
      index_model_key:
        typeof d.index_model_key === 'string' ? d.index_model_key : null,
    }
  }

  // Sort handshake: a successful similarity search flips the grid to
  // Relevance, remembering the prior sort; clearing the search restores it.
  let savedSort: string | null = null
  function enterRelevanceSort() {
    const media = useMediaStore()
    if (media.sortOrder !== 'relevance') {
      savedSort = media.sortOrder
      media.setSortOrder('relevance')
    }
  }
  function restoreSort() {
    const media = useMediaStore()
    if (media.sortOrder === 'relevance') {
      media.setSortOrder(savedSort ?? 'date_added')
    }
    savedSort = null
  }

  async function runText(query: string) {
    textQuery.value = query
    similarTo.value = null
    loading.value = true
    searchError.value = null
    dimMismatch.value = null
    try {
      const hits = await contentSearch(query, textThreshold.value)
      scores.value = new Map(hits.map((h) => [h.file_path, h.similarity_score]))
      enterRelevanceSort()
    } catch (e) {
      const mismatch = extractDimMismatch(e)
      if (mismatch) dimMismatch.value = mismatch
      else searchError.value = e instanceof Error ? e.message : String(e)
      scores.value = null
      console.error('Content search failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function runSimilar(media: Media) {
    similarTo.value = media
    textQuery.value = ''
    loading.value = true
    searchError.value = null
    dimMismatch.value = null
    try {
      const hits = await searchSimilar(media.file_path, imageThreshold.value)
      scores.value = new Map(hits.map((h) => [h.file_path, h.similarity_score]))
      enterRelevanceSort()
    } catch (e) {
      const mismatch = extractDimMismatch(e)
      if (mismatch) dimMismatch.value = mismatch
      else searchError.value = e instanceof Error ? e.message : String(e)
      scores.value = null
      console.error('Similarity search failed:', e)
    } finally {
      loading.value = false
    }
  }

  function submitViaWorker(p: PendingSearch) {
    if (modelsStore.isInferenceReady) {
      pending.value = null
      if (p.kind === 'text') void runText(p.query)
      else void runSimilar(p.media)
      return
    }
    pending.value = p
    const s = modelsStore.inferenceState
    if (s === 'idle' || s === 'stopped' || s === 'error') {
      void modelsStore.startInferenceWorker()
    }
  }

  function submitText(query: string) {
    const q = query.trim()
    if (!q) return
    submitViaWorker({ kind: 'text', query: q })
  }

  function submitSimilar(media: Media) {
    submitViaWorker({ kind: 'similar', media })
  }

  async function searchTags(keys: string[]) {
    tagChips.value = keys
    if (keys.length === 0) {
      tagPaths.value = null
      return
    }
    loading.value = true
    try {
      const byKey = await fetchTagPaths(keys)
      let acc: Set<string> | null = null
      for (const key of keys) {
        const s = new Set(byKey[key] ?? [])
        acc = acc === null ? s : new Set([...acc].filter((p) => s.has(p)))
      }
      tagPaths.value = acc ?? new Set()
    } catch (e) {
      searchError.value = e instanceof Error ? e.message : String(e)
      console.error('Tag search failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function setThreshold(value: number) {
    if (isTextSearch.value) {
      textThreshold.value = Math.min(value, TEXT_THRESHOLD_MAX)
    } else {
      imageThreshold.value = value
    }
    // Threshold is applied server-side — re-run the active search.
    if (scores.value !== null) {
      if (similarTo.value) await runSimilar(similarTo.value)
      else if (textQuery.value) await runText(textQuery.value)
    }
  }

  function clearSimilarity() {
    restoreSort()
    scores.value = null
    textQuery.value = ''
    similarTo.value = null
    pending.value = null
  }

  function clearTags() {
    tagChips.value = []
    tagPaths.value = null
  }

  function clearSearchError() {
    searchError.value = null
    dimMismatch.value = null
  }

  function clearAll() {
    clearSimilarity()
    clearTags()
    clearSearchError()
  }

  return {
    textQuery,
    similarTo,
    tagChips,
    textThreshold,
    imageThreshold,
    scores,
    tagPaths,
    loading,
    pending,
    dimMismatch,
    searchError,
    similarityActive,
    active,
    isTextSearch,
    threshold,
    submitText,
    submitSimilar,
    searchTags,
    setThreshold,
    clearSimilarity,
    clearTags,
    clearSearchError,
    clearAll,
  }
})
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: PASS (legacy `stores/similarity.ts` still compiles against the defaulted-param API functions).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/similarity.ts frontend/src/stores/search.ts
git commit -m "feat(search): SearchHit API shape + useSearchStore with coalesce and tag-AND"
```

---

### Task 3: Media store integration + Relevance sort

**Files:**
- Modify: `frontend/src/stores/media.ts` (`displayedMedia` computed ~line 24–37, `setSortOrder` ~line 145–151)
- Modify: `frontend/src/components/layout/ViewMenubar.vue` (`sortOptions` ~line 22–26)

**Interfaces:**
- Consumes: `useSearchStore` (`scores`, `tagPaths`, `similarityActive`) from Task 2.
- Produces: `displayedMedia` intersects search layers and sorts by score when `sortOrder === 'relevance'`; `setSortOrder('relevance')` sorts client-side without refetching; the sort dropdown offers "Relevance" only while a similarity search is active (or already selected).

- [ ] **Step 1: Integrate the search layers in `stores/media.ts`**

Add `import { useSearchStore } from './search'` next to the folders import, then replace the `displayedMedia` computed:

```ts
  const displayedMedia = computed(() => {
    let items = allMedia.value

    if (favoritesOnly.value) {
      items = items.filter((m) => m.is_favorite)
    }

    if (filteredPaths.value) {
      const paths = filteredPaths.value
      items = items.filter((m) => paths.has(m.file_path))
    }

    // Content search + tag-AND search are two more path-set layers, composed
    // like filteredPaths. Stale index paths (deleted files) drop out here
    // naturally because they no longer appear in allMedia.
    const search = useSearchStore()
    if (search.scores) {
      const s = search.scores
      items = items.filter((m) => s.has(m.file_path))
    }
    if (search.tagPaths) {
      const t = search.tagPaths
      items = items.filter((m) => t.has(m.file_path))
    }

    if (sortOrder.value === 'relevance' && search.scores) {
      const s = search.scores
      items = [...items].sort(
        (a, b) => (s.get(b.file_path) ?? 0) - (s.get(a.file_path) ?? 0),
      )
    }

    return items
  })
```

Replace `setSortOrder`:

```ts
  function setSortOrder(order: string) {
    if (order === sortOrder.value) return
    sortOrder.value = order
    // Relevance sorts client-side from the search score map; every other
    // order defers to the server and refetches.
    if (order === 'relevance') return
    loadAllMedia()
  }
```

- [ ] **Step 2: Offer Relevance in `ViewMenubar.vue`**

Add `import { useSearchStore } from '../../stores/search'` and `const searchStore = useSearchStore()`, then replace the static `sortOptions` array with a computed:

```ts
const sortOptions = computed(() => {
  const base = [
    { label: 'Date Added', value: 'date_added' },
    { label: 'Date Modified', value: 'date_modified' },
    { label: 'Name', value: 'file_name' },
  ]
  // Relevance only means something while a similarity search has scores;
  // keep it listed if it's the current selection so the <select> stays valid.
  if (searchStore.similarityActive || mediaStore.sortOrder === 'relevance') {
    return [{ label: 'Relevance', value: 'relevance' }, ...base]
  }
  return base
})
```

(`computed` is already imported in this file.)

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/media.ts frontend/src/components/layout/ViewMenubar.vue
git commit -m "feat(search): intersect search layers in displayedMedia + relevance sort"
```

---

### Task 4: SearchSection component + FilterPanel layout

**Files:**
- Create: `frontend/src/components/filters/SearchSection.vue`
- Modify: `frontend/src/components/filters/FilterPanel.vue`

**Interfaces:**
- Consumes: `useSearchStore` (Task 2 API), `useModelsStore` (`inferenceState`, `inferenceProgress`, `inferenceError`, `rebuildIndex()`), `useFilterStore` (`filterData.tag` for autocomplete). PrimeVue `InputText`, `Button`, `AutoComplete` are globally registered.
- Produces: the SEARCH panel section (text search row, tag-AND row, threshold slider, model chip, pending pill, inline errors with Rebuild CTA, "Similar to …" chip). FilterPanel gains SEARCH/COLLECTIONS group labels and an uppercased FILTERS title.

- [ ] **Step 1: Create `frontend/src/components/filters/SearchSection.vue`**

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSearchStore, TEXT_THRESHOLD_MAX } from '../../stores/search'
import { useFilterStore } from '../../stores/filters'
import { useModelsStore } from '../../stores/models'

const searchStore = useSearchStore()
const filterStore = useFilterStore()
const modelsStore = useModelsStore()

const expanded = ref(true)

// Local commit-on-submit copy of the query; resyncs when the store's query
// changes elsewhere (e.g. mobile, clearAll).
const query = ref(searchStore.textQuery)
watch(
  () => searchStore.textQuery,
  (q) => {
    query.value = q
  },
)

// Tag AND search — chips autocompleted from the known tag vocabulary.
const tagModel = ref<string[]>([...searchStore.tagChips])
const tagSuggestions = ref<string[]>([])

function completeTags(event: { query: string }) {
  const all = (filterStore.filterData.tag ?? []).map((t) => t.key)
  const q = event.query.toLowerCase()
  tagSuggestions.value = all
    .filter((k) => k.toLowerCase().includes(q) && !tagModel.value.includes(k))
    .slice(0, 20)
}

function runTagSearch() {
  void searchStore.searchTags([...tagModel.value])
}

function onSubmitText() {
  searchStore.submitText(query.value)
}

function onClearText() {
  query.value = ''
  searchStore.clearSimilarity()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') onSubmitText()
}

// Threshold slider — scale switches with the active search kind.
const sliderMax = computed(() => (searchStore.isTextSearch ? TEXT_THRESHOLD_MAX : 1))
const sliderStep = computed(() => (searchStore.isTextSearch ? 0.01 : 0.05))
const sliderValue = ref(searchStore.threshold)
watch(
  () => searchStore.threshold,
  (v) => {
    sliderValue.value = v
  },
)
function onSliderCommit() {
  void searchStore.setThreshold(sliderValue.value)
}

const statusChip = computed(() => {
  const s = modelsStore.inferenceState
  if (s === 'ready') return { dot: '#22c55e', label: 'Model ready' }
  if (s === 'loading' || s === 'spawning') {
    const pct = modelsStore.inferenceProgress.percent
    const stage = modelsStore.inferenceProgress.stage || 'Loading'
    const pctLabel =
      typeof pct === 'number' && pct > 0 ? ` ${Math.round(pct * 100)}%` : ''
    return { dot: '#eab308', label: `${stage}${pctLabel}` }
  }
  if (s === 'error') {
    return { dot: 'var(--danger-color)', label: modelsStore.inferenceError || 'Model error' }
  }
  return { dot: 'var(--text-color-secondary)', label: 'Model not loaded' }
})

async function onRebuildIndex() {
  if (!confirm('Rebuild the embedding index with the current CLIP model? This may take a while.')) return
  try {
    await modelsStore.rebuildIndex()
    searchStore.clearSearchError()
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}
</script>

<template>
  <div class="search-section">
    <button class="section-header" @click="expanded = !expanded">
      <span class="expand-icon">{{ expanded ? '▼' : '▶' }}</span>
      <span class="section-label">Search</span>
      <button
        v-if="searchStore.active"
        class="section-clear"
        title="Clear search"
        @click.stop="searchStore.clearAll()"
      >
        &times;
      </button>
    </button>

    <div v-if="expanded" class="section-body">
      <div class="search-row">
        <i class="pi pi-search row-icon" />
        <InputText
          v-model="query"
          class="row-input"
          placeholder="Search by content..."
          @keydown="onKeydown"
        />
        <Button
          v-if="query"
          icon="pi pi-times"
          severity="secondary"
          text
          aria-label="Clear content search"
          @click="onClearText"
        />
        <Button
          icon="pi pi-search"
          text
          aria-label="Run content search"
          @click="onSubmitText"
        />
      </div>

      <div class="search-row">
        <i class="pi pi-tags row-icon" />
        <AutoComplete
          v-model="tagModel"
          class="row-input tag-input"
          multiple
          :suggestions="tagSuggestions"
          placeholder="tag AND tag..."
          @complete="completeTags"
          @update:model-value="runTagSearch"
        />
        <Button
          icon="pi pi-search"
          text
          aria-label="Run tag search"
          @click="runTagSearch"
        />
      </div>

      <div v-if="searchStore.similarTo" class="similar-chip">
        <i class="pi pi-clone" />
        <span class="similar-name" :title="searchStore.similarTo.file_path">
          Similar to {{ searchStore.similarTo.file_name ?? searchStore.similarTo.file_path.split('/').pop() }}
        </span>
        <button
          class="section-clear"
          title="Clear Find Similar"
          @click="searchStore.clearSimilarity()"
        >
          &times;
        </button>
      </div>

      <div class="threshold-row">
        <span class="threshold-label">Threshold</span>
        <input
          v-model.number="sliderValue"
          type="range"
          min="0"
          :max="sliderMax"
          :step="sliderStep"
          class="threshold-slider"
          @change="onSliderCommit"
        />
        <span class="threshold-value">{{ sliderValue.toFixed(2) }}</span>
      </div>

      <div class="model-chip" :title="statusChip.label">
        <span class="dot" :style="{ background: statusChip.dot }"></span>
        <span class="chip-label">{{ statusChip.label }}</span>
      </div>

      <div v-if="searchStore.pending" class="pending-pill">
        Queued: waiting for model…
      </div>

      <div v-if="searchStore.searchError" class="search-error">
        {{ searchStore.searchError }}
        <button class="section-clear" @click="searchStore.clearSearchError()">&times;</button>
      </div>

      <div v-if="searchStore.dimMismatch" class="search-error">
        <b>Index / model mismatch.</b>
        {{ searchStore.dimMismatch.message }}
        <div class="error-actions">
          <Button label="Rebuild index" size="small" severity="warn" @click="onRebuildIndex" />
          <Button label="Dismiss" size="small" severity="secondary" text @click="searchStore.clearSearchError()" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-section {
  border-bottom: 1px solid var(--surface-border);
  padding-bottom: 4px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 4px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color);
  font-size: 13px;
  font-weight: 600;
  text-align: left;
}

.section-header:hover {
  background: var(--surface-hover);
  border-radius: 4px;
}

.expand-icon {
  font-size: 10px;
  width: 12px;
}

.section-clear {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 4px;
}

.section-clear:hover {
  color: var(--danger-color, #ef4444);
}

.section-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px;
}

.search-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.row-icon {
  color: var(--text-color-secondary);
  font-size: 13px;
  width: 16px;
  flex-shrink: 0;
}

.row-input {
  flex: 1;
  min-width: 0;
  font-size: 13px;
}

.tag-input :deep(.p-autocomplete-input-multiple) {
  width: 100%;
}

.similar-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  font-size: 12px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.similar-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.threshold-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.threshold-slider {
  flex: 1;
  accent-color: var(--primary-color);
}

.threshold-value {
  font-variant-numeric: tabular-nums;
  width: 34px;
  text-align: right;
}

.model-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chip-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-pill {
  font-size: 12px;
  color: var(--text-color-secondary);
  font-style: italic;
}

.search-error {
  font-size: 12px;
  color: var(--danger-color, #ef4444);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.error-actions {
  display: flex;
  gap: 6px;
}
</style>
```

- [ ] **Step 2: Rework `FilterPanel.vue`'s layout**

In the script block add `import SearchSection from './SearchSection.vue'`. Replace the template's body between the title block and the filter sections, and uppercase the FILTERS title:

```html
    <div class="app-title-block">
      <span class="app-title">Metascan</span>
    </div>

    <div class="group-label">SEARCH</div>
    <SearchSection />

    <div class="group-label">COLLECTIONS</div>
    <div class="folders-stack">
      <FoldersSection kind="manual" label="FOLDERS" />
      <FoldersSection kind="smart" label="SMART FOLDERS" />
    </div>

    <div class="filter-header">
      <span class="filter-title">FILTERS</span>
      ...existing Clear All button unchanged...
    </div>
```

Add to the scoped styles:

```css
.group-label {
  font-weight: 700;
  font-size: 15px;
  color: var(--text-color);
  padding-top: 4px;
}
```

- [ ] **Step 3: Verify the build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/filters/SearchSection.vue frontend/src/components/filters/FilterPanel.vue
git commit -m "feat(search): SEARCH section in filter panel with tag-AND and threshold slider"
```

---

### Task 5: Rewire consumers, delete the legacy similarity path

**Files:**
- Modify: `frontend/src/components/thumbnails/ThumbnailGrid.vue`
- Modify: `frontend/src/views/LibraryView.vue`
- Modify: `frontend/src/composables/useContentSearch.ts`
- Modify: `frontend/src/components/layout/ContentSearchBar.vue`
- Delete: `frontend/src/components/thumbnails/SimilarityBanner.vue`, `frontend/src/stores/similarity.ts`

**Interfaces:**
- Consumes: `useSearchStore` (`active`, `submitText`, `submitSimilar`, `clearAll`, `textQuery`).
- Produces: single grid path (`mediaStore.scopedMedia` everywhere); mobile search working through the store; header bar reduced to the action buttons.

- [ ] **Step 1: `ThumbnailGrid.vue`**

- Remove `import SimilarityBanner from './SimilarityBanner.vue'` (line 8) and the `<SimilarityBanner v-if="simStore.active" />` element (line 404).
- Replace `import { useSimilarityStore } from '../../stores/similarity'` with `import { useSearchStore } from '../../stores/search'`, and `const simStore = useSimilarityStore()` with `const searchStore = useSearchStore()`.
- Replace the `displayList` computed (line 60–62) with:

```ts
// Search is now one of the standard path-set layers inside displayedMedia,
// so the grid always renders the folder-scoped view.
const displayList = computed(() => mediaStore.scopedMedia)
```

- In the context-menu handler (~line 263), replace `simStore.findSimilar(contextMenu.value.media)` with `searchStore.submitSimilar(contextMenu.value.media)`.

- [ ] **Step 2: `LibraryView.vue`**

- Replace `import { useSimilarityStore } from '../stores/similarity'` with `import { useSearchStore } from '../stores/search'`, and `const simStore = useSimilarityStore()` with `const searchStore = useSearchStore()`.
- Replace the `gridList` computed (lines 45–49) with:

```ts
// Search now narrows scopedMedia itself, so mobile and desktop share the
// same list.
const gridList = computed(() => mediaStore.scopedMedia)
```

- In the Escape keyboard handler (lines 152–160), replace the `simStore` branch:

```ts
    handler: () => {
      if (searchStore.active) {
        searchStore.clearAll()
      } else if (!viewerOpen.value && !slideshowOpen.value) {
        mediaStore.selectMedia(null)
      }
    },
```

- [ ] **Step 3: `useContentSearch.ts` (mobile bridge)**

Replace the whole file — coalesce now lives in the store:

```ts
import { ref, watch } from 'vue'
import { useSearchStore } from '../stores/search'

export function useContentSearch() {
  const searchStore = useSearchStore()

  const query = ref(searchStore.textQuery)

  // Keep the local box in sync if the query is set elsewhere.
  watch(
    () => searchStore.textQuery,
    (q) => {
      query.value = q
    },
  )

  function submit() {
    searchStore.submitText(query.value)
  }

  function clear() {
    query.value = ''
    searchStore.clearAll()
  }

  return { query, submit, clear }
}
```

(`MobileShell.vue` consumes `{ query, submit, clear }` — signature unchanged, no edit needed there.)

- [ ] **Step 4: Slim down `ContentSearchBar.vue`**

Remove from the script: the `useSimilarityStore` import and `simStore`, `query`, `pendingQuery`, both `watch`es, `statusChip`, `submitDisabled`, `runSearch`, `onSubmit`, `onClear`, `onKeydown`, and `onRebuildIndex`. Keep: the emits, `useModelsStore` bootstrap in `onMounted` (the models store still feeds the SearchSection chip via WS), `router`, and `openStoryboards`.

In the template remove: the `<InputGroup>` block (lines 130–153), the pending pill (155–157), and the `<Teleport>` dim-mismatch banner (225–239). Keep the `action-group` div with all seven buttons. Delete now-unused styles (`.search-group`, `.model-chip`, `.dot`, `.chip-label`, `.pending-pill`, `.banner*`, `.btn-banner`) — vue-tsc won't flag dead CSS, so check manually.

- [ ] **Step 5: Delete the legacy files**

```bash
git rm frontend/src/components/thumbnails/SimilarityBanner.vue frontend/src/stores/similarity.ts
```

Then verify nothing references them:

```bash
grep -rn "useSimilarityStore\|SimilarityBanner\|stores/similarity" frontend/src
```

Expected: no matches.

- [ ] **Step 6: Verify the build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A frontend/src
git commit -m "refactor(search): single grid path — remove similarity store, banner, header search"
```

---

### Task 6: Full verification

**Files:** none new — verification only.

- [ ] **Step 1: Backend quality gate**

Run: `source venv/bin/activate && make quality test`
Expected: PASS (flake8 fatal = 0, black clean, mypy clean, pytest green; `test_file_watcher_triggers_reload` may flake in a full WSL2 run — rerun in isolation before treating it as a failure).

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 3: Manual smoke test**

Start `python run_server.py` + `cd frontend && npm run dev`, open http://localhost:5173 and verify:

1. SEARCH section renders at the top of the left panel with COLLECTIONS and uppercase FILTERS below it.
2. Text search narrows the grid; item count in the menubar drops; sort dropdown flips to Relevance; best matches first.
3. With the search active, clicking Video / Images / Favorites presets narrows the search results further (intersection), and selecting a folder scopes them further still.
4. Tag chips: typing autocompletes known tags; two chips AND together (result count ≤ either tag alone); combining with a text search intersects both.
5. Threshold slider: raising it shrinks the result set after release (search re-runs).
6. Right-click → Find Similar on a thumbnail fills the "Similar to …" chip, grid shows similar items, slider switches to the 0–1 scale.
7. Clearing the search (✕ in the section header or Escape) restores the previous sort and full grid.
8. Narrow the window below 768 px (or use device emulation): mobile search box still narrows the grid; clearing restores it.
9. With the backend stopped mid-search, a friendly error shows inline in the SEARCH section.

- [ ] **Step 4: Update `CLAUDE.md` and commit**

In `CLAUDE.md`: update the frontend tree/annotations — `stores/` list gains `search` and drops `similarity`; `components/thumbnails/` drops `SimilarityBanner`; `filters/` gains `SearchSection`. Replace the **"Similarity threshold is bimodal"** bullet's banner reference: the two thresholds now live in `stores/search.ts` (`textThreshold` 0–0.45 default 0.2, `imageThreshold` 0–1 default 0.7) with the slider in `SearchSection.vue`, and search results are path-set layers inside `mediaStore.displayedMedia` (unbounded, server-side threshold, light `[{file_path, similarity_score}]` responses).

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for search-as-filter architecture"
```

---

## Self-Review Notes

- Spec §1 (panel layout) → Task 4. §2 (path-set semantics, light endpoints, tag AND) → Tasks 1–3. §3 (Relevance sort) → Task 3. §4 (threshold) → Tasks 1, 2, 4. §5 (moves/removals) → Task 5. §6 (store shape) → Task 2. §7 (backend) → Task 1. §8 (errors) → Tasks 2, 4. §9 (testing) → Tasks 1, 6.
- Names used across tasks: `useSearchStore`, `submitText`, `submitSimilar`, `searchTags`, `setThreshold`, `clearSimilarity`, `clearTags`, `clearAll`, `clearSearchError`, `similarityActive`, `active`, `isTextSearch`, `threshold`, `SearchHit`, `TEXT_THRESHOLD_MAX`, `FaissIndexManager.size` — verified consistent between Tasks 1–5.
