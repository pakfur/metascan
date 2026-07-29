# Mobile-Responsive UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a focused, touch-first mobile web experience (grid browsing, full-screen gesture viewer, one-tap slideshow, folder dropdown, content search, sort/size) that activates by viewport width, while leaving the desktop three-panel UI byte-for-byte unchanged.

**Architecture:** A `useViewport` composable exposes a reactive `isMobile` (via VueUse `useMediaQuery('(max-width: 767px)')`). `App.vue` branches its main content between the existing `ThreePanel` (desktop) and a new `MobileShell.vue` (mobile), and swaps the full-screen viewer between the existing `MediaViewer` and a new touch-first `MobileMediaViewer.vue`. Mobile reuses the existing virtualized `ThumbnailGrid` (via a new additive `mobile` prop), the folders/media/similarity/settings Pinia stores, and the existing `SlideshowViewer` (via a new additive `autoStart` prop). No backend, API, or routing changes.

**Tech Stack:** Vue 3 (`<script setup>` + Composition API), TypeScript (strict, `vue-tsc`), Pinia, PrimeVue (Aura), `@vueuse/core`, scoped CSS with the existing `--surface-*` / `--text-*` CSS variables.

## Global Constraints

- **Formatter/style:** Match existing files — `<script setup lang="ts">`, scoped `<style>`, 2-space indent, single quotes, no semicolons (follow the surrounding files exactly).
- **Type safety:** `vue-tsc -b` must pass with zero errors. TypeScript is strict.
- **Quality gate (there is NO frontend unit-test runner):** the only automated gate is `cd frontend && npm run build` (runs `vue-tsc -b && vite build`). Every task's verification is: `npm run build` passes **plus** a concrete manual check in the browser (Chrome DevTools device toolbar / responsive mode). Do not scaffold vitest.
- **Desktop must stay untouched:** all shared-component changes are additive props defaulting to the current behavior (`mobile?: boolean = false`, `autoStart?: boolean = false`). When `isMobile` is false the rendered desktop tree and its behavior are identical to `main`.
- **Breakpoint:** mobile is `max-width: 767px`. Use this exact query string everywhere (only in `useViewport`).
- **Reuse, don't fork:** reuse `ThumbnailGrid`, `SlideshowViewer`, the stores, and `thumbnailUrl`/`streamUrl` from `api/client`. Only `MobileShell`, `MobileFolderMenu`, `MobileMediaViewer`, `useViewport`, `useContentSearch`, and `utils/gestures` are net-new.
- **Out of scope on mobile:** filter panel, metadata panel, and all management dialogs (config, scan, upscale, upscale queue, duplicates, similarity settings, prompt playground, folder create/edit/kebab). These simply are not reachable (their triggers live in desktop-only chrome).
- **Working directory:** repository root is `/home/jk/gws/metascan`; the frontend is `frontend/`. Branch: `feature/mobile-responsive-ui` (already created).

---

## File Structure

**New files:**
- `frontend/src/composables/useViewport.ts` — reactive `isMobile` singleton.
- `frontend/src/composables/useContentSearch.ts` — content-search submit/coalesce/clear logic shared-ready (used by mobile search).
- `frontend/src/utils/gestures.ts` — pure gesture math (swipe classification, distance, midpoint, clamp).
- `frontend/src/components/mobile/MobileShell.vue` — mobile app shell (top bar, search row, grid body, overflow menu).
- `frontend/src/components/mobile/MobileFolderMenu.vue` — folder selection bottom sheet.
- `frontend/src/components/mobile/MobileMediaViewer.vue` — touch-first full-screen viewer.

**Modified files:**
- `frontend/index.html` — add `viewport-fit=cover` for safe-area insets.
- `frontend/src/App.vue` — branch desktop/mobile shell + viewer; add `gridList` computed.
- `frontend/src/components/thumbnails/ThumbnailGrid.vue` — additive `mobile` prop (tap-to-open, no context menu, no drag).
- `frontend/src/components/viewer/SlideshowViewer.vue` — additive `autoStart` prop + touch-reveal controls + gear-to-setup button.

---

## Task 1: Viewport detection + App shell branch (with placeholder)

**Files:**
- Create: `frontend/src/composables/useViewport.ts`
- Modify: `frontend/index.html:6`
- Modify: `frontend/src/App.vue` (script imports + template main-content branch)

**Interfaces:**
- Produces: `useViewport(): { isMobile: Ref<boolean> }` — a shared reactive boolean, true when `matchMedia('(max-width: 767px)')` matches.

- [ ] **Step 1: Create the `useViewport` composable**

`frontend/src/composables/useViewport.ts`:

```ts
import { useMediaQuery } from '@vueuse/core'

// Module-level singleton: evaluated once so the whole app shares a single
// MediaQueryList listener. `isMobile` flips live on resize / rotation.
const isMobile = useMediaQuery('(max-width: 767px)')

export function useViewport() {
  return { isMobile }
}
```

- [ ] **Step 2: Add `viewport-fit=cover` to the viewport meta**

In `frontend/index.html`, replace line 6:

```html
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
```

with:

```html
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
```

(This makes `env(safe-area-inset-*)` non-zero on notched devices; it has no effect on desktop.)

- [ ] **Step 3: Add a placeholder `MobileShell` and branch `App.vue`**

In `frontend/src/App.vue` `<script setup>`, add the import and composable near the other imports/uses (after line 32's `useFoldersUi` import and after line 40's `const foldersUi = useFoldersUi()`):

```ts
import { useViewport } from './composables/useViewport'
```
```ts
const { isMobile } = useViewport()
```

In the template, wrap the existing `<ThreePanel>…</ThreePanel>` block (lines 184–215) so it only renders on desktop, and add a temporary placeholder for mobile. Change:

```html
    <ThreePanel>
      <template #left>
        <FilterPanel />
      </template>
      ...
      <template #right>
        <MetadataPanel />
      </template>
    </ThreePanel>
```

to:

```html
    <ThreePanel v-if="!isMobile">
      <template #left>
        <FilterPanel />
      </template>
      ...
      <template #right>
        <MetadataPanel />
      </template>
    </ThreePanel>

    <!-- Mobile shell placeholder (replaced in Task 3) -->
    <div v-else class="mobile-placeholder">Mobile mode</div>
```

(Keep the inner `#left`/`#center`/`#right` slots exactly as they are — only add `v-if="!isMobile"` to the `<ThreePanel>` tag and the `v-else` sibling.)

Add to `App.vue`'s scoped `<style>`:

```css
.mobile-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-color-secondary);
  font-size: 16px;
}
```

- [ ] **Step 4: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds, zero `vue-tsc` errors.

- [ ] **Step 5: Manual verification**

Run `npm run dev`, open the app. In DevTools responsive mode:
- Width ≥ 768px → the normal three-panel desktop UI renders (unchanged).
- Width ≤ 767px → the centered "Mobile mode" placeholder renders instead.
- Dragging the viewport across 767/768 flips live without reload.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/composables/useViewport.ts frontend/index.html frontend/src/App.vue
git commit -m "feat(mobile): viewport detection + App shell branch"
```

---

## Task 2: ThumbnailGrid `mobile` prop (tap-to-open, no context menu, no drag)

**Files:**
- Modify: `frontend/src/components/thumbnails/ThumbnailGrid.vue`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ThumbnailGrid` gains prop `mobile?: boolean` (default `false`). When `true`: a single tap on a card emits `open` (in addition to selecting), the right-click/long-press context menu is suppressed, and cards are not draggable. Existing `@open`/`@upscale`/`@playground` emits and `scrollSelectedIntoView()` are unchanged.

- [ ] **Step 1: Add the `mobile` prop**

In `ThumbnailGrid.vue` `<script setup>`, immediately after the `defineEmits` block (currently ends at line 20), add:

```ts
const props = withDefaults(
  defineProps<{
    mobile?: boolean
  }>(),
  { mobile: false },
)
```

- [ ] **Step 2: Make a single tap open the viewer on mobile**

Replace the existing `onSelect` function (lines 236–238):

```ts
function onSelect(media: Media) {
  mediaStore.selectMedia(media)
}
```

with:

```ts
function onSelect(media: Media) {
  mediaStore.selectMedia(media)
  // On touch there is no dblclick affordance — a single tap opens the viewer.
  if (props.mobile) emit('open', media)
}
```

- [ ] **Step 3: Suppress the desktop context menu on mobile**

Replace `onContextMenu` (lines 240–243):

```ts
function onContextMenu(media: Media, e: MouseEvent) {
  mediaStore.selectMedia(media)
  contextMenu.value = { x: e.clientX, y: e.clientY, media }
}
```

with:

```ts
function onContextMenu(media: Media, e: MouseEvent) {
  // The desktop right-click menu (folders, upscale, delete…) is out of scope
  // on mobile; a long-press must not surface it.
  if (props.mobile) return
  mediaStore.selectMedia(media)
  contextMenu.value = { x: e.clientX, y: e.clientY, media }
}
```

- [ ] **Step 4: Disable card dragging on mobile**

In the template, on the `<ThumbnailCard>` element (line 424), change the static attribute:

```html
            draggable="true"
```

to a binding:

```html
            :draggable="!mobile"
```

- [ ] **Step 5: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Manual verification (desktop unchanged)**

Run `npm run dev` at desktop width: single-click still only selects; double-click still opens the viewer; right-click still shows the context menu; drag-to-folder still works. (The `mobile` prop defaults to false, so nothing changed.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/thumbnails/ThumbnailGrid.vue
git commit -m "feat(mobile): add ThumbnailGrid mobile prop (tap-to-open, no ctx menu/drag)"
```

---

## Task 3: MobileShell layout — top bar, grid body, overflow (sort/size/slideshow)

This task builds the real mobile shell and wires thumbnail-open + slideshow up to `App.vue`, reusing the **existing** `MediaViewer` and `SlideshowViewer` for now (the touch viewer arrives in Task 7; one-tap slideshow in Task 6). The folder dropdown trigger shows the current scope but opens nothing yet (Task 4).

**Files:**
- Create: `frontend/src/components/mobile/MobileShell.vue`
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Consumes: `ThumbnailGrid` (`:mobile="true"`, `@open`), `useFoldersStore` (`scope`, `activeFolder()`, `isLibraryScope`), `useMediaStore` (`scopedMedia`, `sortOrder`, `setSortOrder`, `displayedMedia`), `useSettingsStore` (`thumbnailSizeLabel`, `setThumbnailSize`), `useSimilarityStore` (`active`, `filteredResults`).
- Produces: `MobileShell` emits `open: [media: Media]` and `slideshow: []`. `App.vue` gains `gridList` computed = `simStore.active ? simStore.filteredResults : mediaStore.scopedMedia`, and `openViewer` computes the index against `gridList` when `isMobile`.

- [ ] **Step 1: Create `MobileShell.vue`**

`frontend/src/components/mobile/MobileShell.vue`:

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Media } from '../../types/media'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore, type ThumbnailSize } from '../../stores/settings'
import ThumbnailGrid from '../thumbnails/ThumbnailGrid.vue'

const emit = defineEmits<{
  open: [media: Media]
  slideshow: []
}>()

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()

const optionsOpen = ref(false)

const scopeLabel = computed(() => {
  if (foldersStore.isLibraryScope) return 'Library'
  return foldersStore.activeFolder()?.name ?? 'Library'
})

const sizes: { value: ThumbnailSize; label: string }[] = [
  { value: 'small', label: 'S' },
  { value: 'medium', label: 'M' },
  { value: 'large', label: 'L' },
]

const sortOptions = [
  { label: 'Date Added', value: 'date_added' },
  { label: 'Date Modified', value: 'date_modified' },
  { label: 'Name', value: 'file_name' },
]

function onSlideshow() {
  optionsOpen.value = false
  emit('slideshow')
}
</script>

<template>
  <div class="mobile-shell">
    <header class="m-topbar">
      <button class="m-folder-btn" type="button">
        <i class="pi pi-folder" />
        <span class="m-folder-name">{{ scopeLabel }}</span>
        <i class="pi pi-chevron-down" />
      </button>

      <div class="m-topbar-actions">
        <!-- Search toggle is added in Task 5 -->
        <button
          class="m-icon-btn"
          type="button"
          aria-label="Options"
          @click="optionsOpen = !optionsOpen"
        >
          <i class="pi pi-ellipsis-v" />
        </button>
      </div>
    </header>

    <!-- Options sheet: sort, thumbnail size, slideshow -->
    <div v-if="optionsOpen" class="m-sheet-backdrop" @click.self="optionsOpen = false">
      <div class="m-sheet">
        <div class="m-sheet-row">
          <span class="m-sheet-label">Size</span>
          <div class="m-size-presets">
            <button
              v-for="s in sizes"
              :key="s.value"
              type="button"
              :class="['m-size-btn', { active: settingsStore.thumbnailSizeLabel === s.value }]"
              @click="settingsStore.setThumbnailSize(s.value)"
            >
              {{ s.label }}
            </button>
          </div>
        </div>

        <div class="m-sheet-row">
          <span class="m-sheet-label">Sort</span>
          <select
            class="m-select"
            :value="mediaStore.sortOrder"
            @change="mediaStore.setSortOrder(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <button class="m-sheet-action" type="button" @click="onSlideshow">
          <i class="pi pi-play" /> Start slideshow
        </button>
      </div>
    </div>

    <div class="m-grid-wrap">
      <ThumbnailGrid :mobile="true" @open="emit('open', $event)" />
    </div>
  </div>
</template>

<style scoped>
.mobile-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.m-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  padding-top: calc(8px + env(safe-area-inset-top));
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
  flex-shrink: 0;
}

.m-folder-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 15px;
  max-width: 70vw;
}

.m-folder-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-topbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.m-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-color);
  font-size: 18px;
}

.m-icon-btn:active {
  background: var(--surface-hover);
}

.m-sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: flex-end;
}

.m-sheet {
  width: 100%;
  background: var(--surface-section);
  border-radius: 16px 16px 0 0;
  padding: 16px;
  padding-bottom: calc(16px + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.m-sheet-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.m-sheet-label {
  font-size: 14px;
  color: var(--text-color-secondary);
  font-weight: 600;
}

.m-size-presets {
  display: flex;
  gap: 2px;
  background: var(--surface-ground);
  border-radius: 8px;
  overflow: hidden;
}

.m-size-btn {
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 14px;
  font-weight: 600;
}

.m-size-btn.active {
  background: var(--primary-color);
  color: #fff;
}

.m-select {
  padding: 8px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 14px;
}

.m-sheet-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  border: none;
  border-radius: 10px;
  background: var(--primary-color);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
}

.m-grid-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
```

- [ ] **Step 2: Wire `MobileShell` into `App.vue`**

In `App.vue` `<script setup>`, add the import (next to the other component imports):

```ts
import MobileShell from './components/mobile/MobileShell.vue'
```

`App.vue` already imports `useSimilarityStore` and exposes it as `simStore` (lines 9 and 39) — reuse that; do **not** add a second store binding.

Add a `gridList` computed (import `computed` from vue — App.vue currently imports `{ ref, onMounted, nextTick }`, so extend it to `{ ref, computed, onMounted, nextTick }`). Place after the store setup:

```ts
// The list the mobile grid shows, and the list the mobile viewer navigates:
// content-search results when a search is active, otherwise the folder scope.
const gridList = computed(() =>
  simStore.active ? simStore.filteredResults : mediaStore.scopedMedia,
)
```

Update `openViewer` to index against the right list when mobile:

```ts
function openViewer(media: Media) {
  // Viewer navigates within the active scope (library / manual / smart) so
  // prev/next stays inside the folder the user just clicked into.
  const list = isMobile.value ? gridList.value : mediaStore.scopedMedia
  const idx = list.findIndex((m) => m.file_path === media.file_path)
  viewerIndex.value = idx >= 0 ? idx : 0
  viewerOpen.value = true
}
```

Replace the `<div v-else class="mobile-placeholder">Mobile mode</div>` from Task 1 with:

```html
    <MobileShell
      v-else
      @open="openViewer"
      @slideshow="openSlideshow"
    />
```

(You may delete the `.mobile-placeholder` CSS rule added in Task 1.)

- [ ] **Step 3: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual verification**

At mobile width (`npm run dev`, responsive mode ≤ 767px):
- Top bar shows a folder button ("Library") and a `⋮` options button.
- Tapping `⋮` opens a bottom sheet with S/M/L size, a Sort dropdown, and "Start slideshow". Changing size re-densifies the grid; changing sort refetches.
- The thumbnail grid fills the screen and scrolls; a single tap on a thumbnail opens the existing `MediaViewer` overlay.
- "Start slideshow" opens the existing `SlideshowViewer` (setup panel — one-tap comes in Task 6).
- Desktop width still renders the full three-panel UI unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/mobile/MobileShell.vue frontend/src/App.vue
git commit -m "feat(mobile): MobileShell layout with grid, sort/size/slideshow options"
```

---

## Task 4: Folder selection bottom sheet (`MobileFolderMenu`)

**Files:**
- Create: `frontend/src/components/mobile/MobileFolderMenu.vue`
- Modify: `frontend/src/components/mobile/MobileShell.vue`

**Interfaces:**
- Consumes: `useFoldersStore` (`manualFolders`, `smartFolders`, `scope`, `isLibraryScope`, `setScope`, `scopeCount`), `useMediaStore` (`displayedMedia`), `FolderScope` type from `../../types/folders`.
- Produces: `MobileFolderMenu` props `{ modelValue: boolean }`, emits `update:modelValue: [boolean]`. Selecting an entry calls `foldersStore.setScope(...)` and closes.

- [ ] **Step 1: Create `MobileFolderMenu.vue`**

`frontend/src/components/mobile/MobileFolderMenu.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import type { FolderScope } from '../../types/folders'

defineProps<{
  modelValue: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
}>()

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()

const libraryCount = computed(() =>
  foldersStore.scopeCount('library', '', mediaStore.displayedMedia),
)

function isActive(scope: FolderScope): boolean {
  const s = foldersStore.scope
  if (scope.kind !== s.kind) return false
  if (scope.kind === 'library') return true
  return 'id' in s && s.id === scope.id
}

function pick(scope: FolderScope) {
  foldersStore.setScope(scope)
  emit('update:modelValue', false)
}
</script>

<template>
  <div v-if="modelValue" class="m-sheet-backdrop" @click.self="emit('update:modelValue', false)">
    <div class="m-sheet m-folder-sheet">
      <div class="m-sheet-handle" />
      <div class="m-folder-list">
        <button
          type="button"
          class="m-folder-item"
          :class="{ active: isActive({ kind: 'library' }) }"
          @click="pick({ kind: 'library' })"
        >
          <i class="pi pi-images" />
          <span class="m-folder-item-name">Library</span>
          <span class="m-folder-count">{{ libraryCount }}</span>
        </button>

        <template v-if="foldersStore.manualFolders.length">
          <div class="m-folder-group">Folders</div>
          <button
            v-for="f in foldersStore.manualFolders"
            :key="f.id"
            type="button"
            class="m-folder-item"
            :class="{ active: isActive({ kind: 'manual', id: f.id }) }"
            @click="pick({ kind: 'manual', id: f.id })"
          >
            <i class="pi" :class="f.icon || 'pi-folder'" />
            <span class="m-folder-item-name">{{ f.name }}</span>
            <span class="m-folder-count">
              {{ foldersStore.scopeCount('manual', f.id, mediaStore.displayedMedia) }}
            </span>
          </button>
        </template>

        <template v-if="foldersStore.smartFolders.length">
          <div class="m-folder-group">Smart Folders</div>
          <button
            v-for="f in foldersStore.smartFolders"
            :key="f.id"
            type="button"
            class="m-folder-item"
            :class="{ active: isActive({ kind: 'smart', id: f.id }) }"
            @click="pick({ kind: 'smart', id: f.id })"
          >
            <i class="pi" :class="f.icon || 'pi-bolt'" />
            <span class="m-folder-item-name">{{ f.name }}</span>
            <span class="m-folder-count">
              {{ foldersStore.scopeCount('smart', f.id, mediaStore.displayedMedia) }}
            </span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: flex-end;
}

.m-sheet {
  width: 100%;
  background: var(--surface-section);
  border-radius: 16px 16px 0 0;
  padding: 8px 8px calc(8px + env(safe-area-inset-bottom));
}

.m-folder-sheet {
  max-height: 70vh;
  display: flex;
  flex-direction: column;
}

.m-sheet-handle {
  width: 40px;
  height: 4px;
  border-radius: 2px;
  background: var(--surface-border);
  margin: 4px auto 8px;
  flex-shrink: 0;
}

.m-folder-list {
  overflow-y: auto;
}

.m-folder-group {
  padding: 12px 12px 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-color-secondary);
}

.m-folder-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 12px;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 16px;
  text-align: left;
  border-radius: 10px;
}

.m-folder-item.active {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
}

.m-folder-item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-folder-count {
  font-size: 13px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.m-folder-item.active .m-folder-count {
  color: var(--primary-color);
}
</style>
```

- [ ] **Step 2: Wire the sheet into `MobileShell`**

In `MobileShell.vue` `<script setup>`, add the import and a state ref:

```ts
import MobileFolderMenu from './MobileFolderMenu.vue'
```
```ts
const folderMenuOpen = ref(false)
```

Make the folder button open it — change the `<button class="m-folder-btn" type="button">` opening tag to:

```html
      <button class="m-folder-btn" type="button" @click="folderMenuOpen = true">
```

Add the component just before the closing `</div>` of `.mobile-shell` (after the `.m-grid-wrap` block):

```html
    <MobileFolderMenu v-model="folderMenuOpen" />
```

- [ ] **Step 3: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual verification**

At mobile width: tapping the folder button opens a bottom sheet listing Library (with count) plus any manual/smart folders grouped, each with a count. Tapping one closes the sheet, updates the top-bar label, and re-scopes the grid. The active row is highlighted. Tapping the dimmed backdrop dismisses without changing scope.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/mobile/MobileFolderMenu.vue frontend/src/components/mobile/MobileShell.vue
git commit -m "feat(mobile): folder selection bottom sheet"
```

---

## Task 5: Mobile content search

**Files:**
- Create: `frontend/src/composables/useContentSearch.ts`
- Modify: `frontend/src/components/mobile/MobileShell.vue`

**Interfaces:**
- Consumes: `useSimilarityStore` (`searchByText`, `active`, `isContentSearch`, `exit`, `contentQuery`), `useModelsStore` (`inferenceState`, `isInferenceReady`, `startInferenceWorker`).
- Produces: `useContentSearch(): { query: Ref<string>, submit(): void, clear(): void }` — encapsulates submit-with-coalesce (queue query, spawn worker if idle, fire once ready) and clear (reset + exit content search).

- [ ] **Step 1: Create the `useContentSearch` composable**

This lifts the coalescing logic from `ContentSearchBar.vue` (`onSubmit`/`onClear` + the ready-drain watch) so the mobile search input stays thin. `ContentSearchBar.vue` is intentionally left unchanged (desktop-untouched rule).

`frontend/src/composables/useContentSearch.ts`:

```ts
import { ref, watch } from 'vue'
import { useSimilarityStore } from '../stores/similarity'
import { useModelsStore } from '../stores/models'

export function useContentSearch() {
  const simStore = useSimilarityStore()
  const modelsStore = useModelsStore()

  const query = ref(simStore.contentQuery)
  const pendingQuery = ref<string | null>(null)

  // Keep the local box in sync if the query is set elsewhere.
  watch(
    () => simStore.contentQuery,
    (q) => {
      query.value = q
    },
  )

  function run(q: string) {
    if (q) void simStore.searchByText(q)
  }

  // When the inference worker becomes ready, drain any queued query.
  watch(
    () => modelsStore.inferenceState,
    (state) => {
      if (state === 'ready' && pendingQuery.value) {
        const q = pendingQuery.value
        pendingQuery.value = null
        run(q)
      }
    },
  )

  function submit() {
    const q = query.value.trim()
    if (!q) return
    if (modelsStore.isInferenceReady) {
      pendingQuery.value = null
      run(q)
      return
    }
    // Coalesce: remember the query and fire once the worker is ready.
    pendingQuery.value = q
    const s = modelsStore.inferenceState
    if (s === 'idle' || s === 'stopped' || s === 'error') {
      void modelsStore.startInferenceWorker()
    }
  }

  function clear() {
    query.value = ''
    pendingQuery.value = null
    if (simStore.active && simStore.isContentSearch) {
      simStore.exit()
    }
  }

  return { query, submit, clear }
}
```

- [ ] **Step 2: Add a search toggle + collapsible search row to `MobileShell`**

In `MobileShell.vue` `<script setup>`, add:

```ts
import { useContentSearch } from '../../composables/useContentSearch'
```
```ts
const searchOpen = ref(false)
const { query: searchQuery, submit: submitSearch, clear: clearSearch } = useContentSearch()

function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') submitSearch()
}
function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (!searchOpen.value) clearSearch()
}
```

In the template, add a search toggle button in `.m-topbar-actions` **before** the options `⋮` button:

```html
        <button
          class="m-icon-btn"
          type="button"
          aria-label="Search"
          @click="toggleSearch"
        >
          <i class="pi pi-search" />
        </button>
```

Add the collapsible search row immediately after the `</header>` closing tag:

```html
    <div v-if="searchOpen" class="m-search-row">
      <i class="pi pi-search m-search-icon" />
      <input
        v-model="searchQuery"
        class="m-search-input"
        type="search"
        placeholder="Search by content…"
        @keydown="onSearchKeydown"
      />
      <button
        v-if="searchQuery"
        class="m-icon-btn"
        type="button"
        aria-label="Clear"
        @click="clearSearch"
      >
        <i class="pi pi-times" />
      </button>
      <button class="m-search-go" type="button" @click="submitSearch">Search</button>
    </div>
```

Add to `MobileShell.vue`'s scoped `<style>`:

```css
.m-search-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
  flex-shrink: 0;
}

.m-search-icon {
  color: var(--text-color-secondary);
  font-size: 14px;
}

.m-search-input {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 16px; /* 16px avoids iOS Safari zoom-on-focus */
}

.m-search-go {
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: var(--primary-color);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
}
```

- [ ] **Step 3: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual verification**

At mobile width: tapping the search icon reveals an input row. Typing a query and pressing Enter (or "Search") runs a content search; the grid switches to result thumbnails (the grid already reads `simStore.filteredResults` when `simStore.active`). The clear (✕) button and re-tapping the search icon both exit the search and restore the scoped grid. If the CLIP worker is cold, the query fires automatically once the model becomes ready.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/useContentSearch.ts frontend/src/components/mobile/MobileShell.vue
git commit -m "feat(mobile): content search row + useContentSearch composable"
```

---

## Task 6: One-tap slideshow (`SlideshowViewer` autoStart + touch controls)

**Files:**
- Modify: `frontend/src/components/viewer/SlideshowViewer.vue`
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SlideshowViewer` gains prop `autoStart?: boolean` (default `false`). When `true`, it starts immediately (skips the setup panel) using existing defaults (ordered, 5s, fade). Controls reveal on touch (`pointerdown`), and a gear button returns to the setup panel to adjust. `App.vue` passes `:auto-start="isMobile"`.

- [ ] **Step 1: Add the `autoStart` prop and auto-start on mount**

In `SlideshowViewer.vue` `<script setup>`, replace the props block (lines 9–11):

```ts
const props = defineProps<{
  mediaList: Media[]
}>()
```

with:

```ts
const props = withDefaults(
  defineProps<{
    mediaList: Media[]
    autoStart?: boolean
  }>(),
  { autoStart: false },
)
```

Extend the existing `onMounted` (lines 200–203) to auto-start:

```ts
onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('mousemove', onMouseMove)
  if (props.autoStart && props.mediaList.length > 0) {
    startSlideshow()
  }
})
```

- [ ] **Step 2: Reveal controls on touch + add a gear-to-setup button**

Add a handler in `<script setup>` (near `onMouseMove`):

```ts
function onPointerReveal() {
  if (started.value) resetHideControls()
}

function reopenSetup() {
  clearAdvanceTimer()
  started.value = false
}
```

In the template, add `@pointerdown="onPointerReveal"` to the root overlay element (the `<div class="slideshow-overlay" …>` on line 221):

```html
  <div
    class="slideshow-overlay"
    :class="{ 'hide-cursor': !controlsVisible && started }"
    @pointerdown="onPointerReveal"
  >
```

Add a gear button to the controls bar (inside `.slideshow-controls`, e.g. before the exit button on line 320):

```html
        <button class="ss-btn" @click="reopenSetup" title="Slideshow settings">⚙</button>
```

- [ ] **Step 3: Pass `autoStart` from `App.vue`**

In `App.vue`, update the `SlideshowViewer` overlay (lines 232–236) to:

```html
    <SlideshowViewer
      v-if="slideshowOpen"
      :media-list="mediaStore.scopedMedia"
      :auto-start="isMobile"
      @close="closeSlideshow"
    />
```

- [ ] **Step 4: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Manual verification**

- Mobile width: "Start slideshow" from the options sheet begins playback immediately (no setup panel). Tapping the screen reveals the controls (‹ ❚❚ › ★ ⚙ ✕); they auto-hide after 3s. The ⚙ gear returns to the setup panel to change order/duration/transition, then Start resumes. ✕ exits.
- Desktop width: `auto-start` is false → the slideshow still opens to the setup panel exactly as before. The added ⚙ button and `pointerdown` reveal are harmless (pointerdown also fires for mouse; `mousemove` reveal still works).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/viewer/SlideshowViewer.vue frontend/src/App.vue
git commit -m "feat(mobile): one-tap slideshow (autoStart + touch controls + gear)"
```

---

## Task 7: Touch-first media viewer (`MobileMediaViewer`)

**Files:**
- Create: `frontend/src/utils/gestures.ts`
- Create: `frontend/src/components/mobile/MobileMediaViewer.vue`
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Consumes: `Media` type, `useMediaStore` (`selectMedia`, `toggleFavorite`), `streamUrl` from `../../api/client`, `fileName` from `../../utils/path`, gesture helpers from `../../utils/gestures`.
- Produces: `MobileMediaViewer` props `{ mediaList: Media[]; initialIndex: number }`, emits `close: []`. `App.vue` mounts it instead of `MediaViewer` when `isMobile`, bound to `:media-list="gridList"`.

- [ ] **Step 1: Create the pure gesture helpers**

`frontend/src/utils/gestures.ts`:

```ts
export interface Point {
  x: number
  y: number
}

export type SwipeAction = 'next' | 'prev' | 'close' | 'none'

/**
 * Classify a single-finger release (from zoom==1) into a navigation action.
 * Horizontal wins ties. A leftward swipe goes to the next item; a rightward
 * swipe to the previous; a downward swipe closes. Movement below both
 * thresholds is a tap (`none`).
 */
export function classifySwipe(
  dx: number,
  dy: number,
  opts: { hMin: number; vMin: number },
): SwipeAction {
  const adx = Math.abs(dx)
  const ady = Math.abs(dy)
  if (adx < opts.hMin && ady < opts.vMin) return 'none'
  if (adx >= ady) return dx < 0 ? 'next' : 'prev'
  return dy > 0 ? 'close' : 'none'
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

export function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}

export function clampZoom(z: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, z))
}
```

- [ ] **Step 2: Create `MobileMediaViewer.vue`**

`frontend/src/components/mobile/MobileMediaViewer.vue`:

```vue
<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { streamUrl } from '../../api/client'
import { fileName } from '../../utils/path'
import {
  classifySwipe,
  distance,
  midpoint,
  clampZoom,
  type Point,
} from '../../utils/gestures'

const props = defineProps<{
  mediaList: Media[]
  initialIndex: number
}>()

const emit = defineEmits<{
  close: []
}>()

const mediaStore = useMediaStore()
const currentIndex = ref(props.initialIndex)
const current = computed(() => props.mediaList[currentIndex.value] ?? null)
const positionLabel = computed(
  () => `${currentIndex.value + 1} / ${props.mediaList.length}`,
)

// Keep the store selection in sync so favorite state stays reactive.
watch(current, (m) => {
  if (m) mediaStore.selectMedia(m)
})

// --- zoom / pan state -------------------------------------------------
const MIN_ZOOM = 1
const MAX_ZOOM = 6
const SWIPE_H_MIN = 60 // px horizontal to trigger prev/next
const SWIPE_V_MIN = 100 // px vertical to trigger close

const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)

function resetView() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
}

watch(currentIndex, resetView)

function navigate(direction: number) {
  const next = currentIndex.value + direction
  if (next >= 0 && next < props.mediaList.length) {
    currentIndex.value = next
  }
}

async function toggleFavorite() {
  if (current.value) await mediaStore.toggleFavorite(current.value)
}

// --- pointer gesture handling ----------------------------------------
const pointers = new Map<number, Point>()

// One-finger swipe tracking (only meaningful at zoom == 1).
let swipeStart: Point | null = null
// One-finger pan tracking (only when zoomed in).
let panStart: { pan: Point; pointer: Point } | null = null
// Two-finger pinch tracking.
let pinchStart: { dist: number; zoom: number; mid: Point; pan: Point } | null =
  null

function pointsArray(): Point[] {
  return Array.from(pointers.values())
}

function onPointerDown(e: PointerEvent) {
  ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (pointers.size === 2) {
    const [a, b] = pointsArray()
    pinchStart = {
      dist: distance(a, b),
      zoom: zoom.value,
      mid: midpoint(a, b),
      pan: { x: panX.value, y: panY.value },
    }
    swipeStart = null
    panStart = null
  } else if (pointers.size === 1) {
    if (zoom.value > 1) {
      panStart = {
        pan: { x: panX.value, y: panY.value },
        pointer: { x: e.clientX, y: e.clientY },
      }
      swipeStart = null
    } else {
      swipeStart = { x: e.clientX, y: e.clientY }
      panStart = null
    }
  }
}

function onPointerMove(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) return
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (pinchStart && pointers.size >= 2) {
    const [a, b] = pointsArray()
    const ratio = distance(a, b) / (pinchStart.dist || 1)
    zoom.value = clampZoom(pinchStart.zoom * ratio, MIN_ZOOM, MAX_ZOOM)
    const mid = midpoint(a, b)
    panX.value = pinchStart.pan.x + (mid.x - pinchStart.mid.x)
    panY.value = pinchStart.pan.y + (mid.y - pinchStart.mid.y)
    return
  }

  if (panStart && zoom.value > 1) {
    panX.value = panStart.pan.x + (e.clientX - panStart.pointer.x)
    panY.value = panStart.pan.y + (e.clientY - panStart.pointer.y)
  }
  // At zoom == 1 we only classify on release (no live translate), keeping the
  // interaction simple and avoiding jitter.
}

function endPointer(e: PointerEvent) {
  const start = swipeStart
  pointers.delete(e.pointerId)

  if (pointers.size < 2) pinchStart = null
  if (pointers.size === 0) panStart = null

  // Snap an almost-reset zoom back to exactly 1 so swipe re-enables cleanly.
  if (zoom.value <= 1.02) resetView()

  if (start && pointers.size === 0 && zoom.value <= 1) {
    const action = classifySwipe(e.clientX - start.x, e.clientY - start.y, {
      hMin: SWIPE_H_MIN,
      vMin: SWIPE_V_MIN,
    })
    swipeStart = null
    if (action === 'next') navigate(1)
    else if (action === 'prev') navigate(-1)
    else if (action === 'close') emit('close')
  }
  swipeStart = null
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
  else if (e.key === 'ArrowLeft') navigate(-1)
  else if (e.key === 'ArrowRight') navigate(1)
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <div class="mv-overlay">
    <header class="mv-topbar">
      <button
        class="mv-icon"
        :class="{ fav: current?.is_favorite }"
        type="button"
        aria-label="Favorite"
        @click="toggleFavorite"
      >
        {{ current?.is_favorite ? '★' : '☆' }}
      </button>
      <span class="mv-position">{{ positionLabel }}</span>
      <button class="mv-icon" type="button" aria-label="Close" @click="emit('close')">
        ✕
      </button>
    </header>

    <div class="mv-body">
      <video
        v-if="current?.is_video"
        :key="current.file_path"
        class="mv-video"
        :src="streamUrl(current.file_path)"
        controls
        playsinline
      />
      <div
        v-else-if="current"
        class="mv-image-stage"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="endPointer"
        @pointercancel="endPointer"
      >
        <img
          class="mv-image"
          :src="streamUrl(current.file_path)"
          :alt="current.file_name ?? fileName(current.file_path)"
          :style="{ transform: `translate(${panX}px, ${panY}px) scale(${zoom})` }"
          draggable="false"
        />
      </div>
    </div>

    <footer class="mv-nav">
      <button
        class="mv-nav-btn"
        type="button"
        aria-label="Previous"
        :disabled="currentIndex === 0"
        @click="navigate(-1)"
      >
        ‹
      </button>
      <button
        class="mv-nav-btn"
        type="button"
        aria-label="Next"
        :disabled="currentIndex >= mediaList.length - 1"
        @click="navigate(1)"
      >
        ›
      </button>
    </footer>
  </div>
</template>

<style scoped>
.mv-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: #000;
  display: flex;
  flex-direction: column;
}

.mv-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  padding-top: calc(8px + env(safe-area-inset-top));
  background: rgba(0, 0, 0, 0.6);
  flex-shrink: 0;
}

.mv-icon {
  background: none;
  border: none;
  color: #ccc;
  font-size: 22px;
  line-height: 1;
  padding: 6px 10px;
}

.mv-icon.fav {
  color: #fbbf24;
}

.mv-position {
  color: #ccc;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.mv-body {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.mv-video {
  max-width: 100%;
  max-height: 100%;
}

.mv-image-stage {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  touch-action: none; /* we handle all gestures ourselves */
}

.mv-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transform-origin: center center;
  user-select: none;
  -webkit-user-select: none;
}

.mv-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
  background: rgba(0, 0, 0, 0.6);
  flex-shrink: 0;
}

.mv-nav-btn {
  background: none;
  border: none;
  color: #fff;
  font-size: 32px;
  line-height: 1;
  padding: 4px 24px;
}

.mv-nav-btn:disabled {
  color: #555;
}
</style>
```

- [ ] **Step 3: Mount `MobileMediaViewer` from `App.vue` when mobile**

In `App.vue` `<script setup>`, add the import:

```ts
import MobileMediaViewer from './components/mobile/MobileMediaViewer.vue'
```

Replace the existing `MediaViewer` overlay (lines 224–229) with a desktop/mobile pair:

```html
    <!-- Media Viewer overlay (desktop) -->
    <MediaViewer
      v-if="viewerOpen && !isMobile"
      :media-list="mediaStore.scopedMedia"
      :initial-index="viewerIndex"
      @close="closeViewer"
    />

    <!-- Media Viewer overlay (mobile, touch-first) -->
    <MobileMediaViewer
      v-if="viewerOpen && isMobile"
      :media-list="gridList"
      :initial-index="viewerIndex"
      @close="closeViewer"
    />
```

- [ ] **Step 4: Build and type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Manual verification (use DevTools touch emulation / a real phone)**

At mobile width, tap a thumbnail to open `MobileMediaViewer`:
- **Swipe left/right** moves to next/previous (also the ‹ › buttons).
- **Pinch** zooms the image; **drag** pans while zoomed; releasing near 1× snaps back and re-enables swipe.
- **Swipe down** (when not zoomed) closes back to the grid; so does ✕ and Escape.
- The ★ toggles favorite (icon updates); position shows "n / total".
- Opening a **video** shows native controls with `playsinline`; ‹ › still navigate.
- Desktop width still opens the original `MediaViewer` (Galleria) — unchanged.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/gestures.ts frontend/src/components/mobile/MobileMediaViewer.vue frontend/src/App.vue
git commit -m "feat(mobile): touch-first MobileMediaViewer with swipe/pinch/close gestures"
```

---

## Task 8: Polish + full verification

**Files:**
- Modify: `frontend/src/components/mobile/MobileShell.vue` (empty-search-results messaging is already handled by the reused grid's empty state; this task is mostly verification and any small fixes found).

- [ ] **Step 1: Cross-cutting manual checks (mobile width)**

Walk the whole mobile experience and confirm smoothness:
- Grid scrolls smoothly; changing size (S/M/L) reflows; sort refetches.
- Folder sheet switches scope and updates counts + top-bar label.
- Search: run, clear, and re-scope all behave; cold-model coalesce fires once ready.
- Slideshow: one-tap start, touch reveal, gear-to-setup, exit.
- Viewer: swipe/pinch/pan/swipe-down/‹ ›/★/close, images and videos.
- Rotate the device (portrait↔landscape) while the viewer and slideshow are open — they keep working; crossing 767px flips shells without a hard error.
- Safe-area: on an emulated notched device (e.g. iPhone), the top bar and viewer chrome are not obscured by the notch/home indicator.

- [ ] **Step 2: Desktop regression pass (width ≥ 768px)**

Confirm the desktop UI is unchanged: three-panel layout, filter + metadata panels, content search bar, view menubar (sort/size/slideshow), grid single/double-click + right-click context menu + drag-to-folder, the Galleria `MediaViewer`, and the slideshow **setup panel** (auto-start is false on desktop). No mobile chrome appears.

- [ ] **Step 3: Final build**

Run: `cd frontend && npm run build`
Expected: build succeeds with zero errors.

- [ ] **Step 4: Backend quality gate (sanity — no backend files changed)**

Run: `make quality test`
Expected: passes (this change is frontend-only; run it to confirm nothing else regressed).

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "chore(mobile): polish + verification fixes"
```

(If Step 1/2 surfaced no fixes, skip this commit.)

---

## Self-Review

**Spec coverage:**
- Detection & switching (`useViewport`, 767px, live flip, desktop untouched) → Task 1.
- Mobile shell (top bar, folder trigger, grid body, safe-area) → Tasks 3–5.
- Folder dropdown (Library + manual + smart, counts, `setScope`) → Task 4.
- Content search (reuse `searchByText`, coalesce) → Task 5.
- Sort & thumbnail size (reuse stores) → Task 3 (options sheet).
- Mobile viewer (swipe, pinch+pan, swipe-down close, persistent chrome, native video) → Task 7.
- One-tap slideshow (autoStart, defaults, touch controls, gear) → Task 6.
- Out-of-scope surfaces not mounted on mobile (desktop-only chrome hosts their triggers) → Tasks 1 & 3.
- Testing/quality (build + manual, desktop regression) → each task + Task 8.

**Placeholder scan:** No TBD/TODO; every code step contains complete code. Verification steps use concrete commands (`npm run build`) and concrete observations (no test runner exists — this is stated in Global Constraints, so build + manual is the intended gate, not a placeholder).

**Type consistency:** `useViewport(): { isMobile }` used consistently in App.vue. `gridList` defined once in App.vue (Task 3) and consumed by the mobile viewer binding (Task 7). `MobileShell` emits `open`/`slideshow`; App handlers `openViewer`/`openSlideshow` match existing signatures. `mobile`/`autoStart` props are `withDefaults(... false)`. `classifySwipe`/`distance`/`midpoint`/`clampZoom`/`Point` names match between `utils/gestures.ts` and `MobileMediaViewer.vue`. `scopeCount(kind, id, all)` and `setScope(FolderScope)` match `stores/folders.ts`.
