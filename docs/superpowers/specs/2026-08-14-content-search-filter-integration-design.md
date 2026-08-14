# Content Search as a Filter — Design

**Date:** 2026-08-14
**Status:** Approved
**Scope:** Desktop frontend + small backend changes. Mobile UI unchanged (shared store must keep it working).

## Problem

Content search (CLIP text→image) and Find Similar (image→image) run in a
parallel universe from the rest of the UI: results replace the grid via a
`simStore.active` swap, ignore the Video/Images/Favorites view presets, the
FILTERS sections, and folder scope, and are capped at the server's top-100.
This is confusing and inconsistent with how every other narrowing mechanism
in the app works.

## Goal

Search behaves externally like a filter: it narrows `displayedMedia` through
the same path-set intersection pipeline as FILTERS and folder scope, composes
with all of them, and is unbounded (defined by a score threshold, not a
top-N cap). The search UI moves from the header into a new SEARCH section at
the top of the left filter panel.

## Decisions (settled with user)

| Question | Decision |
|---|---|
| Result bounding / ordering | Score threshold defines membership; "Relevance" added to sort dropdown, auto-selected on search |
| Threshold control | Slider in the SEARCH panel; text default 0.2 (range 0–0.45), image-similar default 0.7 (range 0–1) |
| Folder scope × search | Intersect — searching inside a folder searches that folder |
| Tag search | Autocomplete chips, AND semantics, combinable (AND) with text search and all filters |
| Find Similar | Converted to the same filter model; banner/grid-swap machinery deleted |
| Mobile | Desktop-only UI change; mobile keeps its current search box driven by the shared store |
| Architecture | Client-side path-set layer (Approach 1) |

## 1. Filter panel layout

`FilterPanel.vue` becomes:

```
Metascan
─────────
SEARCH
▼ Search                          ← collapsible section
  [🔍] [ text query        ] [→]  ← similarity (CLIP) search
  [🏷] [ tag chips…        ] [→]  ← tag AND search (autocomplete)
  [——●——] threshold slider        ← scale switches by search kind
  ● Model ready                   ← inference status chip (moved from header)
─────────
COLLECTIONS                       ← new label
▼ FOLDERS ##  +                   ← existing FoldersSection, unchanged
▼ SMART FOLDERS ##  +             ← existing, unchanged
─────────
FILTERS            Clear All      ← label uppercased; sections unchanged
  Camera Make / Camera Model / Has Location / Model / LoRA / Tags
```

- Tag box autocompletes against `filterStore.filterData.tag`; selections
  render as removable chips.
- Inline error states (search failure, dim-mismatch with its "Rebuild
  index" button) render inside the Search section.
- The model-not-ready coalesce behavior (submit once, query fires when the
  worker reaches ready; idle/stopped/error triggers a spawn) moves from
  `ContentSearchBar.vue` intact.
- An active Find Similar shows a "Similar to <filename>" chip with an ✕.

## 2. Search semantics — a third path-set layer

Search results become path sets intersected in `mediaStore.displayedMedia`,
exactly like `filteredPaths`:

```
displayedMedia = allMedia ∩ favoritesOnly ∩ filteredPaths(FILTERS + view presets)
                          ∩ searchPaths(text OR Find Similar) ∩ tagAndPaths
scopedMedia    = folder scope applied on top (unchanged mechanism)
```

- **Text search** → `POST /api/similarity/content-search {query, threshold,
  max_results: null}` → lightweight unbounded `[{file_path,
  similarity_score}]` (no media dicts, no per-path DB lookups). The store
  keeps `scores: Map<path, score>`; `searchPaths` is its key set.
- **Find Similar** → `POST /api/similarity/search` with the image-similar
  threshold; fills the same layer.
- **Tag AND** → `POST /api/filters/tag_paths {keys}` (existing endpoint,
  returns per-key path sets); sets are intersected client-side into
  `tagAndPaths`. Purely additive AND with everything else.
- Text search and Find Similar are mutually exclusive (one `scores` map);
  starting one replaces the other. Tag AND search composes with either.
- Stale index paths (deleted files still in FAISS) drop out naturally in
  the intersection with `allMedia`.

## 3. Sorting

- "Relevance" is added to the sort dropdown (`ViewMenubar.vue`).
- Executing a similarity search auto-selects Relevance, remembering the
  prior sort; clearing the search restores it.
- Relevance sorts client-side by score descending inside the
  `displayedMedia` computed — no server refetch. All other sort options
  keep their existing refetch behavior (`setSortOrder` must skip the
  refetch for `relevance`).
- Tag-only searches have no scores: Relevance is only offered/enabled while
  a similarity search (text or Find Similar) is active.

## 4. Threshold

One slider in the SEARCH section, two remembered values with different
scales (same bimodal principle as the old banner):

| Mode | Range | Default |
|---|---|---|
| Text↔image | 0–0.45 | 0.2 |
| Image↔image (Find Similar) | 0–1 | 0.7 |

The threshold is applied **server-side** (keeps payloads bounded on large
libraries); moving the slider re-runs the active search on release.

## 5. Moves and removals

- `ContentSearchBar.vue`: search input, status chip, and coalesce logic move
  to the new Search section component; the header row keeps its action
  buttons (Storyboards, scan, refresh, upscale queue, duplicates,
  similarity settings, config).
- `SimilarityBanner.vue`: deleted.
- Grid-swap bindings (`simStore.active ? simStore.filteredResults :
  mediaStore.scopedMedia`) in `ThumbnailGrid.vue`, `LibraryView.vue`, and
  the mobile shell collapse to just `scopedMedia`.
- The menubar item count needs no change — it reads `displayedMedia`.
- FILTERS "Clear All" keeps clearing only filters; the Search section has
  its own clear affordances (✕ in the text box, chip removal, similar-to
  chip ✕).

## 6. Store shape

`stores/similarity.ts` is reworked into `stores/search.ts`
(`useSearchStore`):

- State: `textQuery`, `similarTo: Media | null`, `tagChips: string[]`,
  `textThreshold`, `imageThreshold`, `scores: Map<string, number> | null`,
  `tagPaths: Set<string> | null`, `pendingQuery` (coalesce), `dimMismatch`,
  `searchError`, `loading`.
- `active` computed: any of scores/tagPaths non-null.
- `mediaStore.displayedMedia` reads the search store the same way
  `scopedMedia` reads the folders store (import inside the computed to
  avoid init cycles).
- Actions: `searchText(query)`, `findSimilar(media)`, `searchTags(keys)`,
  `setThreshold(value)` (re-runs active search), `clearText/clearTags/
  clearAll`, plus the sort save/restore handshake with `mediaStore`.

## 7. Backend changes

Both `POST /api/similarity/content-search` and `POST /api/similarity/search`:

- Accept `threshold` (content-search gains it; search already has it) and
  an unbounded `max_results` (null/absent → all above threshold).
- Return the light shape `[{file_path, similarity_score}]` — no
  `service.get_media` per hit, no media dicts.
- FAISS searches with k = index size (IndexFlatIP is exhaustive either
  way).

The mobile UI and any other caller go through the updated store, so the
response-shape change is coordinated in the same commit.

## 8. Error handling

- Inference worker not ready → coalesce + spawn (existing behavior,
  relocated).
- Dim mismatch (HTTP 409) → inline error in the Search section with the
  Rebuild index CTA (existing `rebuildIndex` flow).
- Search failure → inline error message in the Search section.
- Empty result set → grid's existing empty state.

## 9. Testing

- Backend: pytest coverage for the new `threshold`/unbounded
  `max_results` params and the light response shape on both endpoints.
- Frontend: `npm run build` (vue-tsc) must pass; manual verification of
  compose behavior (search × view preset × folder × FILTERS), relevance
  sort auto-select/restore, threshold re-query, tag AND, Find Similar
  chip, mobile search still narrowing the grid.
- `make quality test` must pass.

## Out of scope

- Mobile UI redesign (mobile keeps its current search box UX).
- Any change to smart folders, FILTERS sections, or folder behavior.
- Persisting search state across reloads.
