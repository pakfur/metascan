---
paths:
  - "frontend/**"
---

# Frontend conventions and gotchas

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **Smart-folder evaluator is synchronous and client-side.** Rules are a JSON blob evaluated per Media in `stores/folders.ts::evaluateCondition`. Tag conditions can't rely on `m.tags` because the summary endpoint omits it — the store fetches only the tag keys referenced by saved smart folders via `POST /api/filters/tag_paths` with `{keys: […]}` and evaluates against those path sets. A previous bulk-GET version fetched the entire inverted index and blocked the media list endpoint for 20+ s; never restore that shape. The "Has I2V video" rule (`field: 'i2v'`, bool) follows the same cache pattern: `GET /api/i2v/sources` returns the source images that have one or more clips still in the library (`db.list_i2v_source_paths`, JOINed on `media`). It's fetched only while a saved folder or the open editor uses the rule, and refetched on the `i2v` channel's `i2v_videos_changed`, after a clip delete, and on a forced `loadTagPaths`. It's deliberately not a column on `/api/media`, so the covering indexes don't change.
- **Search is a filter layer, not a separate results view.** `useSearchStore` (`frontend/src/stores/search.ts`) holds text search, Find-Similar, and tag-AND as independent path-set layers that `mediaStore.displayedMedia` intersects with the folder/preset scope — there is no dedicated search results screen. The backend endpoints are light and unbounded (`[{file_path, similarity_score}]`, threshold applied server-side), and a Relevance sort option orders by `similarity_score` when a search layer is active. `SearchSection.vue` (in `components/filters/`, top of the left panel) is the only UI: it renders the query/chip input, the "Similar to …" chip, and the threshold slider. **Similarity threshold is bimodal.** Text↔image search uses `searchStore.textThreshold` (default 0.2, slider 0-0.45) because CLIP text/image cosine scores live on a much lower scale than image↔image, which uses `searchStore.imageThreshold` (default 0.7, slider 0-1). `SearchSection.vue` switches slider range + formatting based on `isTextSearch`.
- **The Similarity Settings dialog has no pHash/CLIP threshold controls.** They were removed because nothing consumed the saved values: the duplicate finder hardcodes a Hamming distance of 10 in `backend/api/duplicates.py`, and similarity search uses `useSearchStore`'s in-memory `textThreshold` / `imageThreshold` per-session, never the persisted `clip_threshold`. If you re-add a threshold UI, wire it through to those consumers — don't just round-trip through `/api/similarity/settings`.
- **`LocationSection`'s map container must never hit `display:none`.** When the MapLibre canvas is inside a `display:none` element, browsers pause its `requestAnimationFrame` and the render loop wedges — the next `flyTo` updates camera state but no tiles ever fetch, so the panel becomes a permanently blank gray canvas until page refresh. `v-if`/`v-show` (which both end up at `display:none`) on the section wrapper triggered this on every GPS → no-GPS → GPS toggle. The component instead applies a `meta-section--offscreen` class (`position:absolute; visibility:hidden; top:-10000px`) for non-GPS media, keeping the canvas painted offscreen so its rAF loop and WebGL context stay alive across toggles. The watcher waits one `requestAnimationFrame` after the offscreen→onscreen flip before calling `map.resize()` so `clientWidth` reflects the visible size. Because the section now renders for non-GPS media too, GPS-only computeds (`coordsLabel`, `osmUrl`) must early-return when `!hasGps` to avoid `null.toFixed`. `onBeforeUnmount(destroyMap)` still releases the WebGL context when the panel itself unmounts.
- **Routing is hash-history vue-router (`frontend/src/router/index.ts`), added in Phase C.** `/` is `LibraryView` — the pre-Phase-C `App.vue` moved verbatim, still the desktop/mobile grid shell. `/storyboard/:id?` is `StoryboardView`, a desktop-only authoring canvas (no mobile layout). `App.vue` itself is now a thin router shell: `<router-view>` plus `ToastHost` and the folders WS bridge (`folder_created`/`folder_updated`/`folder_deleted`/`folder_items_changed`) — it holds no dialog or page state, so grep `views/LibraryView.vue` or `views/StoryboardView.vue` for that, not `App.vue`.
- **Mobile UI is a viewport-selected shell, not responsive CSS on the desktop tree.** `composables/useViewport.ts` exposes a reactive `isMobile` (VueUse `useMediaQuery('(max-width: 767px)')`, the only place that query string lives). `LibraryView.vue` renders `ThreePanel` (desktop) or `MobileShell` (mobile), and swaps the full-screen viewer between `MediaViewer` (Galleria, desktop) and the touch-first `MobileMediaViewer` (`components/mobile/`). The mobile experience is deliberately narrow — grid browsing, folder bottom sheet (`MobileFolderMenu`), content search (`composables/useContentSearch.ts`, a port of `ContentSearchBar`'s coalesce logic), sort/size, one-tap slideshow, and the gesture viewer; filters, the metadata panel, and all management dialogs are simply never mounted on mobile. The mobile port itself didn't touch `ContentSearchBar.vue`; Phase C later added the Storyboards nav button there, so the desktop component has moved since — read it directly rather than assuming it's frozen. **Desktop stays behaviorally identical** because every shared-component change is an additive prop defaulting to the old behavior: `ThumbnailGrid`'s `mobile` (tap-to-open, no context menu, no drag) and `SlideshowViewer`'s `autoStart`. Two consistency rules matter: search now narrows `scopedMedia` itself (composed upstream into `displayedMedia`), so `gridList` in `LibraryView.vue` is just `computed(() => mediaStore.scopedMedia)` and mobile and desktop already share one list — `openViewer` indexes against that same `scopedMedia`-backed list regardless of `isMobile`; and `LibraryView.vue` watches `isMobile` to close any open viewer/slideshow on a breakpoint flip (the two viewers bind different lists behind the same index). Touch gestures (`components/mobile/MobileMediaViewer.vue` + pure helpers in `utils/gestures.ts`) use pointer events: swipe prev/next, pinch-zoom + pan, swipe-down-to-close, with the zoom snap-to-1 running before the pinch→single-finger re-arm so a swipe survives pinch jitter. See `docs/superpowers/specs/2026-07-28-mobile-responsive-ui-design.md`.
- **Vite proxy** forwards `/api/*` and `/ws` to the backend during development. The EPIPE error handler silences broken pipe from cancelled browser requests.
- **Detail editors with local commit-on-change copies must resync on id +
  updated_at, not id alone.** `ShotHeader.vue` (the shot header — action,
  subtext) and `BeatCard.vue` (framing, subject picker, camera/dialog/
  sound — the per-beat editing surface since the shot/beat reorg moved
  those fields off panels) each keep a local
  editable ref
  per text/select field (bound `:value` + `@change`, not `v-model`) so an
  in-flight edit survives the store's optimistic `Object.assign`. Resyncing
  only when the selected panel's/beat's *id* changes misses every
  server-side rewrite of the panel/beat currently open — a compose pass
  or another tab's PATCH
  that rewrites a field in place never reaches the
  textarea, and a later blur then PATCHes the stale (often empty) local
  value back over the server's write, destroying it. Each field pairs its
  local ref with a "last synced from server" snapshot ref, updated
  together by the field's own commit handler; the resync watcher fires on
  `[panel.value?.id, panel.value?.updated_at]` (updated_at bumps on every
  successful PATCH, including server-driven ones) and only overwrites a
  field whose local ref still equals its snapshot — i.e. no pending
  uncommitted edit for that specific field. Apply the same pattern to any
  other detail editor that caches server fields in local commit-on-change
  refs.

## Common tasks

### Adding a new frontend dialog
1. Create `frontend/src/components/dialogs/MyDialog.vue`
2. Add state/open flag in `views/LibraryView.vue` (or `views/StoryboardView.vue` for a storyboard-only dialog) — `App.vue` is just the router shell (`<router-view>` + `ToastHost` + the folders WS bridge) and holds no dialog state.
3. Add button/shortcut trigger in `ContentSearchBar.vue` (the header action row).
4. If it needs a store, create `frontend/src/stores/my.ts`

### Adding a smart-folder rule field
1. Add the field identifier to `RuleField` in `frontend/src/types/folders.ts`.
2. Add a `FIELD_DEFS[<field>]` entry in `frontend/src/stores/folders.ts` (label, ops, value type, default).
3. Add a `case '<field>':` to `evaluateCondition` — keep it synchronous; async work belongs in a precomputed path-set cache (see the tags pattern).
4. If the rule reads a column not already on the `/api/media` summary, add it to the SELECT in `get_all_media_summaries` **and** to every covering index (`idx_media_summary_added`, `idx_media_summary_modified`) — otherwise `/api/media` falls back to the main-table scan. `Media` frontend type gets the new field too.
5. If conditions carry server-resolved references (e.g. tag keys, later CLIP queries), add an endpoint that takes an explicit key list and cache responses in the store keyed by referenced values. Don't bulk-GET the whole universe.
6. `migrateSmartFolder` in `stores/folders.ts` only runs for the legacy localStorage import (`readLegacyLocalStorage`); rules persisted through `/api/folders` are opaque JSON and never pass through it, so removing or renaming a field needs its own handling where server rules are loaded.
