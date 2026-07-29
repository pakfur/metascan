# Mobile-Responsive UI — Design Spec

**Date:** 2026-07-28
**Status:** Approved, ready for implementation planning
**Topic:** A focused mobile web experience for Metascan alongside the full desktop UI.

## Goal

Metascan's UI currently assumes a wide desktop: a fixed three-column shell
(`ThreePanel.vue`) with a filter sidebar, a center content column, and a
metadata sidebar, plus dense horizontal toolbars and mouse-only interactions.
There is no responsive handling today beyond the viewport meta tag.

Provide a **smooth, simple, media-viewing-focused** experience for mobile web
browsers while leaving the desktop experience fully intact. Mobile users
primarily browse and view media, run slideshows, and switch folders.

## Scope

### In scope (mobile)

- Thumbnail grid browsing (reuses the existing virtualized grid).
- Full-screen media viewer with touch gestures.
- Slideshow (one-tap start).
- Folder selection via a dropdown / bottom sheet.
- Text/content search (CLIP text-to-image + keyword).
- Sort order and thumbnail size controls.

### Out of scope (mobile — not mounted when mobile)

- Filter panel.
- Metadata panel (tags / EXIF / prompt / location map).
- Management dialogs: config, scan, upscale, upscale queue, duplicates,
  similarity settings, prompt playground, folder create/edit/kebab.
- Desktop-only interactions: drag-thumbnail-to-folder, resizable panel gutters.

These remain available on desktop, unchanged.

## Architecture

### Detection & switching

- A single reactive source of truth: a `useViewport` composable wrapping
  `window.matchMedia('(max-width: 767px)')`, exposing an `isMobile` ref that
  updates on breakpoint change (listen to the `MediaQueryList` `change` event).
- `App.vue` renders the existing desktop tree (`ThreePanel` + its slots) when
  `!isMobile`, and a new `MobileShell.vue` when `isMobile`. Resizing a browser
  window or rotating a device flips the layout live.
- **The desktop component tree is untouched** — `ThreePanel.vue`,
  `FilterPanel.vue`, `MetadataPanel.vue`, `ContentSearchBar.vue`,
  `ViewMenubar.vue`, `ScopeBreadcrumb.vue` keep their current behavior. Lowest
  regression risk.
- Breakpoint: `767px` (phones and small tablets in portrait get mobile; wider
  gets desktop). Binary switch — no manual override in v1.

### Component inventory (new)

| Component | Role |
|-----------|------|
| `composables/useViewport.ts` | Reactive `isMobile` from `matchMedia`. Single source of truth. |
| `components/mobile/MobileShell.vue` | Mobile app shell: top bar, search row, grid body. |
| `components/mobile/MobileFolderMenu.vue` | Folder dropdown / bottom sheet (Library + manual + smart, with counts). |
| `components/mobile/MobileMediaViewer.vue` | Touch-first full-screen viewer (image + video). |
| `composables/useSwipe.ts` / `usePinch.ts` | Pointer-event gesture helpers (no heavy dependency). |

Reused as-is: `ThumbnailGrid.vue` (virtualized grid), the content-search store
logic, `settingsStore` (sort + thumbnail size), `foldersStore` (scope), and the
existing slideshow playback engine where practical.

## Mobile shell layout (`MobileShell.vue`)

Full-height flex column, respecting safe-area insets (`env(safe-area-inset-*)`)
for notched devices:

1. **Sticky top bar**
   - Left: folder dropdown trigger — shows current scope name + item count;
     tapping opens `MobileFolderMenu`.
   - Right: a search icon (toggles the search row) and an overflow `⋯` menu
     (sort, thumbnail size, slideshow).
2. **Collapsible search row** — slides in under the top bar when the search
   icon is tapped. Reuses the existing content-search store/query logic (CLIP
   text + keyword), stripped to an input + clear button. Same debounce as
   desktop.
3. **Grid body** — the existing virtualized `ThumbnailGrid`, filling remaining
   height and scrolling. The grid already computes columns/cell size from
   container width via `ResizeObserver`, so it adapts to narrow widths without
   change. Tapping a thumbnail opens `MobileMediaViewer`.

Because global CSS sets `overflow:hidden` on `html/body/#app`, the shell must
establish its own internal scroll container for the grid body (the grid already
manages its own scroll).

## Folder dropdown (`MobileFolderMenu.vue`)

- A bottom sheet (or dropdown) listing **Library**, manual folders, and smart
  folders (grouped with headers), each with its count.
- Selecting an entry calls the existing `foldersStore.setScope(next)` — reusing
  all current scope-filtering logic (`scopeMedia`, `scopeCount`). No new
  filtering code.
- This is the primary navigation on mobile.

## Sort & thumbnail size

- Exposed in the overflow `⋯` menu.
- Sort order reuses the existing sort state; thumbnail size reuses
  `settingsStore.thumbnailSize` (S / M / L presets).
- No new persistence — same stores as desktop.

## Mobile viewer (`MobileMediaViewer.vue`)

A dedicated touch-first full-screen viewer. Rationale: the desktop
`MediaViewer` is built on PrimeVue `Galleria`, which manages its own DOM and
does not adapt cleanly to pinch-zoom / swipe-down-to-close. The mobile viewer
is driven by the **same media list + current index** as the grid, so navigation
stays consistent between grid and viewer.

Behaviors:

- **Swipe** left/right → previous / next item.
- **Pinch-to-zoom + drag-pan** on images. Gesture arbitration:
  - When zoomed in (`zoom > 1`), a one-finger drag **pans** rather than swiping
    to the next item.
  - **Swipe-down-to-close** only fires at `zoom == 1`.
- **Swipe down** (at zoom 1) → dismiss the viewer back to the grid.
- **Minimal persistent chrome** (no tap-to-toggle): close (✕), favorite toggle,
  and position indicator ("3 / 50"). Chrome stays visible.
- **Video**: native `<video controls playsinline>` — touch-friendly playback,
  replacing the desktop custom control bar on mobile.
- Gestures implemented with a small pointer-event composable
  (`useSwipe` / `usePinch`). No heavy gesture library.

## Slideshow (mobile)

- One tap from the overflow `⋯` menu starts immediately with sensible defaults:
  **4s interval, current sort order, simple fade transition**.
- A small gear opens a compact sheet to adjust interval / order if the user
  wants; otherwise no setup step.
- Tap to pause / exit.
- Reuses the existing slideshow playback engine where practical (the mobile
  entry point skips the desktop pre-start setup panel).

## Error handling & edge cases

- **Empty scope**: grid shows the existing empty state; folder dropdown still
  usable.
- **Rotation / resize across the breakpoint mid-view**: `isMobile` flips; a
  full-screen viewer/slideshow open at flip time should remain functional
  (both desktop and mobile viewers overlay the same way). Verify the open
  viewer survives or cleanly re-mounts on the flip.
- **Zoomed-in state on item change**: reset zoom/pan to 1 when the active item
  changes so each new image starts un-zoomed.
- **Video vs image**: viewer branches on media type; native controls own their
  own touch handling, so image swipe/pinch handlers must not intercept taps on
  the video control surface.

## Testing & quality

- Type-safe throughout; `vue-tsc --noEmit` and `npm run build` must pass.
- Small unit tests for `useViewport` (breakpoint reactivity) and the gesture
  composables' pure logic (e.g. swipe direction / threshold classification)
  where trivial to isolate.
- Manual verification via browser device emulation (portrait phone, small
  tablet, rotation).
- **Desktop regression check**: confirm the desktop path renders identically —
  `ThreePanel` and its children unmodified; only `App.vue`'s top-level branch
  and shared overlays gain the `isMobile` guard.

## Explicit non-goals (v1)

- No manual desktop/mobile override toggle.
- No mobile access to filters, metadata, or management dialogs.
- No new backend/API changes — mobile reuses existing endpoints and stores.
- No routing introduction — the app stays single-page, state-driven (mobile
  shell is a top-level conditional, not a route).
