# Folders & Smart Folders

This document describes the folders feature that was ported from
`docs/metascan-design-system/project/07_folders_prototype.html` into the Vue
frontend, and captures everything a follow-up contributor needs in order to
finish the feature — most importantly, the work left to move folder state from
the browser to the backend and to let smart folders reference content-search
(CLIP) queries.

The implementation landed in commit `874f62b` on `feature/vue-ui`.

## Feature summary

Two new kinds of folder sit above the standard filter list in the left
sidebar. Both are **virtual** — they do not move files on disk.

- **Manual folders (`kind: 'manual'`).** A named bag of `file_path`s. The
  user adds/removes members by drag-and-drop onto a folder row, by the
  thumbnail context menu's "Add to folder ▶" submenu, or by removing a chip
  in the Details panel.
- **Smart folders (`kind: 'smart'`).** A named ruleset `{ match, conditions }`
  where `match` is `all` / `any` and each condition is
  `{ field, op, value }`. Membership is computed live from the current
  library — there is nothing stored per-member.

Selecting any folder (including the synthetic "Library" row that always
represents the full library) sets an app-wide **scope**. The Thumbnail grid,
Media Viewer, and Slideshow all read from `mediaStore.scopedMedia`, so every
surface stays in sync with the active scope. A breadcrumb above the grid
shows the current scope, its kind chip (`MANUAL` blue, `SMART` violet), item
count, and — for smart scopes — an **Edit rules** button.

## Frontend architecture

### File map

```
frontend/src/
  types/
    folders.ts                 RuleField, RuleOp, SmartCondition, SmartRules,
                               ManualFolder, SmartFolder, AnyFolder, FolderScope

  stores/
    folders.ts                 Pinia store: manualFolders, smartFolders,
                               scope, CRUD, evaluator, scopeMedia / scopeCount /
                               foldersContaining. Exports FIELD_DEFS and
                               OP_LABELS for the editor.
    media.ts                   scopedMedia computed (NEW): narrows
                               displayedMedia by foldersStore.scope.

  composables/
    useFoldersUi.ts            Shared dialog/overlay state (new folder dialog,
                               rename, smart editor, kebab menu) so that deeply
                               nested components open overlays without emitting
                               events up through three parents.
    useToast.ts                Single-slot, auto-dismissing toast.

  components/
    filters/
      FilterPanel.vue          Adds two <FoldersSection> above the existing
                               filter list.
      FoldersSection.vue       One section — takes kind='manual'|'smart'.
                               Header (+ add button), list of <FolderRow>s,
                               empty-state hint.
      FolderRow.vue            Row with icon/name/count/kebab, click-to-scope,
                               drag-over/drop for manual folders (smart rows
                               reject the drop).
      FolderKebabMenu.vue      Absolute-positioned context menu launched from
                               the row kebab.

    dialogs/
      NewFolderDialog.vue      Used for new AND rename (keyed off
                               ui.renameFolderId).
      SmartFolderEditor.vue    Rule builder with live match count.

    layout/
      ScopeBreadcrumb.vue      Breadcrumb strip above the grid.
      ToastHost.vue            Bottom-centered toast.

    thumbnails/
      ThumbnailGrid.vue        Drag source + context-menu extensions +
                               drag-count pill.
      ThumbnailCard.vue        In-folder dot indicator.

    metadata/
      MetadataPanel.vue        "In folders" chips section for selected media.
```

### Data flow

Scope resolution is one-way:

```
foldersStore.scope           (Pinia state)
    │
    ▼
mediaStore.scopedMedia       (computed; uses foldersStore.scopeMedia)
    │
    ├───▶ ThumbnailGrid.displayList (falls back to scopedMedia)
    ├───▶ MediaViewer  (mediaList prop)
    ├───▶ SlideshowViewer (mediaList prop)
    └───▶ ScopeBreadcrumb (count display)
```

Similarity search still overrides the grid's display list
(`simStore.active ? simStore.filteredResults : mediaStore.scopedMedia`) —
running a similarity search pops the user out of folder scope temporarily
without mutating `foldersStore.scope`.

Folder mutations propagate through the store. `removeMedia` (in the media
store) now calls `foldersStore.purgePath` so a file deleted from disk never
lingers as a dangling member of a manual folder.

### Persistence

Folders are currently stored in `localStorage` under the key
`metascan.folders.v1`:

```json
{
  "manual": [ { "id": "f_abc123", "name": "References", "kind": "manual",
                "icon": "pi-folder", "items": ["/abs/path/a.png"],
                "createdAt": 1713571200000 } ],
  "smart":  [ { "id": "s_def456", "name": "Favorites ★", "kind": "smart",
                "icon": "pi-star-fill",
                "rules": { "match": "all",
                           "conditions": [ { "field": "favorite",
                                             "op": "is", "value": true } ] },
                "createdAt": 1713571200000 } ]
}
```

Writing is driven by a deep `watch` on both arrays in `stores/folders.ts`.
Failures (quota, private mode) are swallowed; the folders stay in memory for
the session.

### Smart-folder rule model

Rule fields currently supported — see `FIELD_DEFS` in `stores/folders.ts` for
the canonical list, operators, and value types:

| Field        | Ops                                                  | Value kind |
|--------------|------------------------------------------------------|------------|
| `favorite`   | `is`, `is_not`                                       | bool       |
| `type`       | `is`, `is_not`                                       | `image` / `video` |
| `model`      | `is`, `is_not`, `contains`                           | text       |
| `prompt`     | `contains`, `does_not_contain`, `starts_with`        | text       |
| `filename`   | `contains`, `does_not_contain`, `starts_with`        | text       |
| `tags`       | `contains`, `contains_any`, `does_not_contain`       | tag list   |
| `modified`   | `within_days`, `older_than_days`                     | integer    |
| `dimensions` | `is`                                                 | `landscape` / `portrait` / `square` |

The evaluator (`evaluateCondition`) runs synchronously against a `Media`
summary. Summary records from `/api/media` carry everything needed by every
field except `model` / `prompt` / `tags`, which come from the detail endpoint
— a smart folder using one of those fields against a media summary that has
not been detail-loaded will fail to match even if the record would qualify.
In practice the store operates on `mediaStore.allMedia` (summaries plus any
detail that happened to be loaded); when we move the evaluator server-side
(see below) this distinction disappears.

### UI plumbing worth knowing

- **useFoldersUi** is a module-scope composable (shared refs, no Pinia). It
  owns `newFolderOpen`, `renameFolderId`, `smartEditorOpen`, and
  `kebabMenu`. `App.vue` mounts the overlays; leaf components call
  `ui.openNewFolder(...)` etc. This keeps the deeply nested
  FolderRow → FoldersSection → FilterPanel → App event chain from needing
  to forward events.
- **Drag payload** is JSON under the MIME `application/x-metascan-paths`.
  Grid sets it on `dragstart`; `FolderRow` reads it on `drop`. A single
  drag-count pill (Teleported to `body` from `ThumbnailGrid`) follows the
  cursor.
- **In-folder dot** (`ThumbnailCard.in-folder`) is shown only when the card
  is a member of any manual folder AND the user is not currently inside
  that folder's scope — otherwise every thumb would wear it and it would
  become noise.
- The thumbnail grid's existing context menu was extended with an "Add to
  folder ▶" nested submenu, "New smart folder from selection…", and
  "Remove from this folder" (shown only when in a manual scope).

## Known scope limits (as of this commit)

1. **No backend persistence.** Folders live in `localStorage` only — a user
   who clears site data or switches browsers loses everything. This is the
   largest item on the follow-up list.
2. **No `contentPrompt` / CLIP content-search rule field.** The prototype
   referenced one; it was dropped because the current `Media` schema does
   not expose a stable prompt-independent description, and running CLIP
   queries synchronously inside a rule evaluator is not viable.
3. **"Convert to smart folder"** is a stub: it opens the smart editor with a
   placeholder `favorite is true` rule. A true conversion would require a
   chosen heuristic (e.g. "tags contain X for every member").
4. **Multi-select drag source.** Grid selection is currently a single
   `selectedMedia` — the dragstart payload is always a one-element array.
   When multi-select lands (see `docs/future_work.md`), update
   `onThumbDragStart` to send the full selection.
5. **Rule evaluator uses already-loaded summaries.** Rules over detail-only
   fields (`model`, `prompt`, `tags`) only fire for detail-loaded records.
6. **No delete-behind-the-scenes for smart folders.** If a user deletes a
   file, it stays in smart folders until the next library refresh because
   `foldersStore.purgePath` only touches manual folders.

## Future work

### 1. Backend storage + API for manual folders

**Goal.** Move manual folders off `localStorage` and onto the SQLite
database so they are durable and shareable across clients.

**Suggested schema** (additions to `metascan/core/database_sqlite.py`):

```sql
CREATE TABLE folders (
    id          TEXT PRIMARY KEY,          -- UUID; matches current client id shape
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT 'pi-folder',
    created_at  REAL NOT NULL,              -- unix seconds
    updated_at  REAL NOT NULL
);

CREATE TABLE folder_items (
    folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL REFERENCES media(file_path) ON DELETE CASCADE,
    added_at    REAL NOT NULL,
    PRIMARY KEY (folder_id, file_path)
);

CREATE INDEX idx_folder_items_by_file ON folder_items(file_path);
```

`folder_items.file_path` is the existing `media.file_path` natural key. The
`ON DELETE CASCADE` on both sides makes `foldersStore.purgePath` redundant
server-side — a file removal in `media` sweeps its folder memberships.

**Suggested REST surface** (add a new `backend/api/folders.py`,
register in `backend/main.py`). Match the shape the frontend already
produces so the `folders.ts` store can swap its persistence layer without
changing the rest of the UI:

```
GET    /api/folders                         → [{id,name,icon,items:[path],
                                                created_at,updated_at,
                                                count}]
POST   /api/folders                         body {name,icon?,items?:[path]}
                                            → folder record
PATCH  /api/folders/{id}                    body {name?,icon?} → folder record
DELETE /api/folders/{id}                    → 204
POST   /api/folders/{id}/items              body {paths:[path]}   → {added:int}
DELETE /api/folders/{id}/items              body {paths:[path]}   → {removed:int}
```

Authentication is already handled by the bearer-token middleware in
`backend/main.py` — no changes needed. All DB work should go through
`asyncio.to_thread`, matching the rest of the service layer.

**Frontend migration path.**

1. Add an `api/folders.ts` wrapper (fetch + types mirror of today's store
   shape).
2. Replace the `localStorage` load/save in `stores/folders.ts` with calls
   to the new API. Keep the existing store surface (`manualFolders`,
   `addToManualFolder`, …) so every consumer keeps working unchanged.
3. On startup, fetch `GET /api/folders` alongside `loadAllMedia`. Populate
   `manualFolders.value` from the response. Drop the `watch` that writes to
   `localStorage`; every mutation now funnels through the API.
4. Broadcast changes on the `folders` WebSocket channel (see below) so
   other tabs / clients stay in sync.
5. Migration-of-existing-data: on first load after the backend lands, if
   the server returns an empty list AND `localStorage` has entries, POST
   them up, then clear `localStorage`. One-time helper; safe to remove in
   a later release.

**WebSocket channel.** Follow the existing `ws_manager.broadcast` pattern
(already used for `scan`, `upscale`, `embedding`, `models`, `watcher`).
Messages:

| Event             | Payload                                      |
|-------------------|----------------------------------------------|
| `folder_created`  | `{folder: <record>}`                         |
| `folder_updated`  | `{folder: <record>}`                         |
| `folder_deleted`  | `{id: string}`                               |
| `folder_items_changed` | `{folder_id, added:[path], removed:[path]}` |

The frontend's `useWebSocket('folders', …)` handler patches the store in
place — this keeps optimistic UI and cross-client sync cheap.

**Testing.** Add `tests/test_folders_api.py` covering CRUD + the
cascade-on-media-delete case. Use the existing fixtures in
`tests/conftest.py` (temp DB, small media corpus).

### 2. Smart folders with content-search conditions

**Goal.** Let a smart folder rule use CLIP text similarity — e.g. "prompt
matches `lighthouse at dusk`" implemented as the existing
`POST /api/similarity/content-search` rather than a substring match.

**Why this is non-trivial.** The current smart-folder evaluator is
synchronous and runs in the browser against in-memory `Media` records. A
CLIP text query is a model inference + FAISS search — multi-second, runs in
the backend. It cannot be evaluated live per-condition for every row.

**Suggested architecture.**

- Add a new `RuleField` variant: `'content'` with ops
  `['matches', 'does_not_match']`, `value: { query: string; threshold: number }`.
- Server-side, every smart folder with a `content` condition gets a
  cached **membership snapshot** stored in a new table:

  ```sql
  CREATE TABLE smart_folder_matches (
      folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
      file_path   TEXT NOT NULL REFERENCES media(file_path) ON DELETE CASCADE,
      score       REAL NOT NULL,
      computed_at REAL NOT NULL,
      PRIMARY KEY (folder_id, file_path)
  );
  ```

  The evaluator joins this cache with the non-`content` conditions at
  query time. Membership is the intersection (for `match: all`) or union
  (`match: any`) as usual.

- **Refresh trigger.** A background task re-runs the CLIP query whenever:
  1. The smart folder's rules change (save from editor).
  2. New files land in the library (hook into the existing
     `watcher` channel / scan complete).
  3. The active CLIP model changes (a dim-mismatch would invalidate the
     cache anyway; see `_assert_dim_matches` in
     `backend/api/similarity.py`).
  Batch refreshes via an asyncio queue so multiple edits in quick
  succession collapse into one.

- **Async status in the UI.** The smart-editor footer already has a "live
  match count" field — extend it to show `syncing… / N items` when a
  content condition is being recomputed. Drive it off a `folders`
  WebSocket event (`smart_folder_refreshing`, `smart_folder_refreshed`).

- **Editor UI.** In `SmartFolderEditor.vue`, add a new value-input branch
  `case 'content'` that renders a prompt textarea + threshold slider
  (reuse `SimilarityBanner.vue`'s content-threshold slider styling — 0.0
  to 0.45 at step 0.01, since CLIP text/image cosine lives on a lower
  scale than image/image).

- **Evaluator change.** `evaluateCondition` in `stores/folders.ts` returns
  `true` / `false` today; for `content` it needs to consult the cache.
  Options: (a) fetch the snapshot up front and pass it as an extra arg to
  `matches(m, rules, contentHits)`, or (b) resolve the whole
  smart-folder membership on the server (`POST /api/folders/smart/{id}/preview`)
  and cache on the client. (b) is simpler and scales better — smart
  folders with content rules become server-evaluated, everything else
  stays client-side.

**Related existing infra to reuse.**

- `InferenceClient.encode_text` (see `metascan/core/inference_client.py`)
  — same path the existing content-search uses.
- `FaissIndexManager.search` in `metascan/core/embedding_manager.py`.
- `warm_faiss_index` in `backend/api/similarity.py` (landed `90a5f3d`) —
  smart-folder refresh should use the same singleton so we don't pay the
  faiss import cost twice.

### 3. Smaller follow-ups

- **Re-evaluate smart folders on demand.** Add a "Refresh" icon to the
  breadcrumb for smart scopes so a user can force recomputation without
  closing and reopening.
- **Drag-to-reorder manual folders.** The prototype doesn't do this;
  requires a persistent `sort_order` column if added server-side.
- **Folder icons.** The data model already carries an `icon` string
  (`pi-folder` default). Surface an icon picker in the editor.
- **Rating field.** `SmartCondition` types reference rating in comments
  but the live set is `favorite`-only. If `rating` lands on `Media`, add
  the field (`value: 'rating'` — number input 0–5).
- **Keyboard shortcuts on folder rows.** `F2` to rename,
  `Delete` to delete; currently mouse-only.

## Quick verification checklist (for reviewers)

- Open the app → "Library" row is active by default, grid shows all media.
- `+` on FOLDERS → name dialog → create → new row appears, scope switches
  to it, grid goes empty.
- Drag a thumb onto the new folder → toast "1 item added", count bumps
  to 1, the blue dot appears on the thumb in Library scope.
- `+` on SMART FOLDERS → editor → pick `favorite is true` → save →
  membership computed live, matches `is_favorite=true` rows only.
- Right-click a thumb → "Add to folder ▶" submenu → pick a manual folder →
  toast confirms.
- Select a thumb that's in a folder → Details panel shows "In folders" chip
  with `×`; clicking `×` removes the membership.
- Reload the page → folders survive (localStorage).
- `make quality test` → 112 pytest pass, no new flake8 fatal/ black / mypy
  issues.
