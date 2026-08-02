# Phase C — Storyboard Authoring UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Vue authoring/review view for storyboards — three-level navigation (scene strip → panel grid → panel detail with candidate picker), text import, preset registration, and live progress over the `storyboard`/`comfy` WS channels — on top of the Phase B backend.

**Architecture:** Activate the already-installed but unwired `vue-router` with **hash history**, shrink `App.vue` to a router shell, move the existing library UI verbatim into `views/LibraryView.vue` (route `/`), and add `views/StoryboardView.vue` (route `/storyboard/:id?`). A new `stores/storyboard.ts` Pinia store owns the tree, selection, and job↔panel correlation; typed fetchers in `api/storyboard.ts` / `api/comfy.ts` mirror the Phase A/B REST contract exactly.

**Tech Stack:** Vue 3 `<script setup>` + TypeScript strict, Pinia, vue-router 5 (hash history), PrimeVue (Aura) for Buttons/InputText only (dialogs are hand-rolled per house convention), existing `useWebSocket` multiplexed composable.

**Spec:** `docs/superpowers/specs/2026-08-01-storyboard-generator-design.md` §8 (plus §4.4 hidden-media toggle).

## Decisions (recorded deviations / resolutions)

1. **Hash history, not HTML5 history.** Spec §8.1 wants `/storyboard/7` to survive reload. `createWebHashHistory` (`/#/storyboard/7`) satisfies that with zero backend/Vite fallback configuration; HTML5 history would 404 on reload under any static production serving. If the user later wants clean URLs, it's a one-line swap plus a backend catch-all.
2. **No frontend test framework.** The repo has none (CI = `vue-tsc --noEmit` + `vite build` only) and the spec's §10 test list is backend-only. Adding vitest is out of scope. Each task's gate is `npx vue-tsc --noEmit` + `npm run build`, plus reviewer code-reading.
3. **The §4.4 "filter toggle reveals hidden media" UI lands here** (Task 7) — backend support (`include_hidden`) shipped in Phase B.
4. **Desktop-only view.** Per spec §8.4 the storyboard view is not mounted on mobile: the route renders a short "desktop only" note with a home link when `isMobile`.
5. **Candidate tiles do not reuse `ThumbnailCard`** (it requires a full `Media` object and store-coupled favorite behavior). A small local `CandidateTile` using `thumbnailUrl()` is more honest. Full-screen inspection DOES reuse `MediaViewer` with minimal `Media`-shaped objects built from `panel_images` rows.

## Global Constraints

- **Desktop library behavior must remain identical.** The App.vue → LibraryView extraction is a verbatim move; no logic, template, or style changes beyond what routing mechanically requires.
- `cd frontend && npx vue-tsc --noEmit && npm run build` must pass at every commit. `make quality test` must still pass after any backend file touch (none is planned; if a task ends up touching backend code, run it).
- Vue 3 Composition API `<script setup>` + TypeScript strict everywhere. New shared UI strings/components live under `frontend/src/components/storyboard/`.
- Dialogs follow the house convention: hand-rolled `.dialog-overlay` / `.dialog-card` scoped styles, `@click.self="emit('close')"` overlay close, `×` header button (copy the `ConfigDialog.vue` / `PromptPlayground.vue` skeleton).
- API access only through `api/client.ts` helpers (`get/post/patch/del`, `thumbnailUrl`, `streamUrl`); never raw `fetch`.
- WS subscriptions only through `useWebSocket(channel, handler)`; the `storyboard` channel events are `synthesis_progress` `{storyboard_id, panel_id, done, total, prompt_source}`, `synthesis_complete` `{storyboard_id, synthesized, fallback, skipped_locked}`, `synthesis_error` `{storyboard_id, error}`, `panel_images_changed` `{storyboard_id, panel_id, files: string[]}`; the `comfy` channel events are `job_update` `{job_id, state, error}`, `job_progress` `{job_id, value, max}`, `job_outputs` `{job_id, files}`.
- Backend REST contract (mirror exactly, do not invent fields): routes and Pydantic models in `backend/api/storyboard.py` and `backend/api/comfy.py`; tree shape from `DatabaseManager.get_storyboard_tree`. `PanelPatch` deliberately has no `selected_image_id` — keeper selection only via `POST /api/storyboard/panels/{id}/select`.
- The store uses the `stores/folders.ts` optimistic-update-with-rollback idiom for PATCH-style edits; destructive/creative server actions (parse, synthesize, generate, select) are await-then-refresh, not optimistic.
- Mobile: every new entry point no-ops or hides under `isMobile.value` (the `useViewport()` singleton).

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/types/storyboard.ts` (create) | TS interfaces for tree, requests, comfy jobs/presets |
| `frontend/src/api/storyboard.ts` (create) | Typed fetchers for `/api/storyboard/*` |
| `frontend/src/api/comfy.ts` (create) | Typed fetchers for `/api/comfy/*` (presets, jobs) |
| `frontend/src/router/index.ts` (create) | Hash-history router, 2 routes |
| `frontend/src/views/LibraryView.vue` (create) | Verbatim home of today's App.vue library UI |
| `frontend/src/views/StoryboardView.vue` (create) | Route component: landing (no id) / authoring (id) |
| `frontend/src/App.vue` (modify) | Thin shell: `<router-view>` + `ToastHost` + global WS/folder dialog glue that must survive route changes |
| `frontend/src/main.ts` (modify) | `app.use(router)` |
| `frontend/src/stores/storyboard.ts` (create) | Tree, selection, job↔panel map, WS handlers, actions |
| `frontend/src/components/storyboard/StoryboardLanding.vue` (create) | List/create/open/delete storyboards |
| `frontend/src/components/storyboard/CreateStoryboardDialog.vue` (create) | New-storyboard form |
| `frontend/src/components/storyboard/PresetRegistrationDialog.vue` (create) | Register ComfyUI workflow preset |
| `frontend/src/components/storyboard/SceneStrip.vue` (create) | Horizontal scene cards + add scene |
| `frontend/src/components/storyboard/PanelGrid.vue` (create) | Panel tiles (keeper/progress/error) + add panel |
| `frontend/src/components/storyboard/PanelDetail.vue` (create) | Panel editor + candidates + keeper select |
| `frontend/src/components/storyboard/ImportTextDialog.vue` (create) | Paste text → parse (409 confirm flow) |
| `frontend/src/components/storyboard/StoryboardSettingsDialog.vue` (create) | Storyboard fields + subjects editor |
| `frontend/src/components/layout/AppHeader.vue` (modify) | Desktop-only "Storyboards" nav button |
| `frontend/src/api/media.ts`, `stores/media.ts`, `ViewMenubar.vue` (modify, Task 7) | `include_hidden` toggle |
| `CLAUDE.md`, `docs/features.md` (modify, Task 7) | Routing + storyboard-view docs |

---

### Task 1: Types + API fetchers

**Files:**
- Create: `frontend/src/types/storyboard.ts`, `frontend/src/api/storyboard.ts`, `frontend/src/api/comfy.ts`
- Verify: `cd frontend && npx vue-tsc --noEmit`

**Interfaces:**
- Consumes: `api/client.ts` `get/post/patch/del` exactly as exported today; backend contract (read `backend/api/storyboard.py` and `backend/api/comfy.py` to confirm every field before writing — the shapes below were transcribed from them at plan time).
- Produces: everything below, byte-for-byte — later tasks import these names.

- [ ] **Step 1: Write `types/storyboard.ts`**

```ts
export interface PanelImage {
  id: number
  panel_id: number
  file_path: string
  seed: number | null
  variant_index: number
  prompt_used: string | null
  preset_id: number | null
  comfy_prompt_id: string | null
  created_at: string
}

export interface Panel {
  id: number
  scene_id: number
  sort_order: number
  shot_size: string | null
  angle: string | null
  lens: string | null
  action: string
  subject_ids: number[]
  notes: string | null
  brief: string | null
  prompt: string | null
  prompt_locked: number // 0 | 1 from SQLite
  prompt_source: 'llm' | 'brief' | 'user' | null
  negative: string | null
  selected_image_id: number | null
  created_at: string
  updated_at: string
  images: PanelImage[]
}

export interface Scene {
  id: number
  storyboard_id: number
  sort_order: number
  name: string
  location: string | null
  time_of_day: string | null
  mood: string | null
  lighting: string | null
  notes: string | null
  panels: Panel[]
}

export interface Subject {
  id: number
  storyboard_id: number
  name: string
  description: string
  lora_name: string | null
  lora_strength: number | null
  reference_path: string | null
  sort_order: number
}

export interface StoryboardSummary {
  id: number
  name: string
  aspect_ratio: string
  target_model: string
  architecture: string
  preset_id: number | null
  base_seed: number
  batch_size: number
  folder_id: string | null
  created_at: string
  updated_at: string
}

export interface StoryboardTree extends StoryboardSummary {
  source_text: string | null
  style_block: string | null
  negative: string | null
  subjects: Subject[]
  scenes: Scene[]
}

export const SHOT_SIZES = ['ECU', 'CU', 'MCU', 'MS', 'MLS', 'WS', 'EWS'] as const
export const ANGLES = ['eye', 'low', 'high', 'overhead', 'dutch', 'ots', 'pov'] as const
export const LENSES = ['wide', 'normal', 'tele', 'macro'] as const
export const ASPECT_RATIOS = ['1:1', '4:3', '16:9', '2.39:1', '9:16'] as const
export const TARGET_MODELS = ['sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen'] as const

// ---- comfy ----
export interface WorkflowPreset {
  id: number
  name: string
  kind: 't2i' | 'ref'
  created_at?: string
  updated_at?: string
}

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface GenerationJob {
  id: number
  preset_id: number
  panel_id: number | null
  state: JobState
  comfy_prompt_id: string | null
  params: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  output_dir: string | null
}
```

(Adjust nullable/optional markers only if the backend rows genuinely differ — verify against `get_storyboard_tree` and the `workflow_presets` SELECT in `backend/services/comfy_service.py`. If `list_presets` returns extra keys like `bindings`, include them as optional.)

- [ ] **Step 2: Write `api/storyboard.ts`**

```ts
import { get, post, patch, del } from './client'
import type { Panel, StoryboardSummary, StoryboardTree } from '@/types/storyboard'
```

(Use the repo's actual import-alias convention — check how existing files import across src/; if there is no `@` alias, use relative paths.) Exports, one thin function per route:

```ts
export function listStoryboards(): Promise<StoryboardSummary[]>
export function createStoryboard(body: {
  name: string; target_model: string; architecture?: string; aspect_ratio?: string;
  style_block?: string | null; negative?: string | null; preset_id?: number | null;
  base_seed?: number | null; batch_size?: number
}): Promise<{ id: number }>
export function fetchStoryboard(id: number): Promise<StoryboardTree>
export function patchStoryboard(id: number, body: Partial<{
  name: string; source_text: string; aspect_ratio: string; style_block: string;
  negative: string; target_model: string; architecture: string;
  preset_id: number; base_seed: number; batch_size: number
}>): Promise<{ status: string }>
export function deleteStoryboard(id: number): Promise<{ status: string }>
export function parseStoryboard(id: number, text: string, confirm = false): Promise<StoryboardTree>
export function synthesizeStoryboard(id: number, body: { panel_ids?: number[]; force?: boolean } = {}): Promise<{ status: string; total: number }>
export function generateStoryboard(id: number, body: { panel_ids?: number[]; only_failed?: boolean } = {}): Promise<{ jobs: number[] }>
export function cancelStoryboard(id: number): Promise<{ cancelled: number }>
export function createSubject(storyboardId: number, body: { name: string; description: string; lora_name?: string | null; lora_strength?: number; reference_path?: string | null; sort_order?: number }): Promise<{ id: number }>
export function patchSubject(subjectId: number, body: Record<string, unknown>): Promise<{ status: string }>
export function deleteSubject(subjectId: number): Promise<{ status: string }>
export function createScene(storyboardId: number, body: { name: string; sort_order?: number; location?: string | null; time_of_day?: string | null; mood?: string | null; lighting?: string | null; notes?: string | null }): Promise<{ id: number }>
export function patchScene(sceneId: number, body: Record<string, unknown>): Promise<{ status: string }>
export function deleteScene(sceneId: number): Promise<{ status: string }>
export function createPanel(sceneId: number, body: { action: string; sort_order?: number; shot_size?: string | null; angle?: string | null; lens?: string | null; subject_ids?: number[]; notes?: string | null }): Promise<{ id: number }>
export function patchPanel(panelId: number, body: Record<string, unknown>): Promise<Panel>
export function deletePanel(panelId: number): Promise<{ status: string }>
export function selectPanelImage(panelId: number, imageId: number | null): Promise<Panel>
```

Route paths exactly as in `backend/api/storyboard.py` (`/storyboard/{id}/parse`, `/storyboard/subjects/{sid}`, `/storyboard/scenes/{sid}/panels`, `/storyboard/panels/{pid}/select`, …). Note `patchPanel`/`selectPanelImage` return the updated panel dict WITHOUT `images` (the route returns `db.get_panel`) — type the return as `Panel` but document that `images` may be absent and callers must merge, or type it `Omit<Panel, 'images'>` if that's what the backend actually returns (verify by reading the route; prefer the accurate type).

- [ ] **Step 3: Write `api/comfy.ts`**

```ts
export function listPresets(): Promise<WorkflowPreset[]>
export function createPreset(body: { name: string; kind: 't2i' | 'ref'; workflow: Record<string, unknown> }): Promise<{ id: number }>
export function deletePreset(id: number): Promise<{ status: string }>
export function listJobs(state?: JobState, limit = 100): Promise<GenerationJob[]>
export function getJob(id: number): Promise<GenerationJob>
```

`listJobs` builds the query string (`/comfy/jobs?state=running&limit=100`, omitting `state` when undefined).

- [ ] **Step 4: Verify + commit**

Run: `cd frontend && npx vue-tsc --noEmit` → clean.
```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/api/comfy.ts
git commit -m "feat(storyboard-ui): typed API fetchers and tree types"
```

---

### Task 2: Router activation + LibraryView extraction

**Files:**
- Create: `frontend/src/router/index.ts`, `frontend/src/views/LibraryView.vue`, `frontend/src/views/StoryboardView.vue` (placeholder)
- Modify: `frontend/src/App.vue`, `frontend/src/main.ts`, `frontend/src/components/layout/AppHeader.vue`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: routes `/` (name `library`) and `/storyboard/:id?` (name `storyboard`, prop `id` as number|undefined); `StoryboardView.vue` shell that Task 5 fills; the AppHeader "Storyboards" button.

**This is the highest-risk refactor of the phase: the desktop library must remain behaviorally identical.** The move is mechanical:

- [ ] **Step 1: `router/index.ts`**

```ts
import { createRouter, createWebHashHistory } from 'vue-router'
import LibraryView from '../views/LibraryView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'library', component: LibraryView },
    {
      path: '/storyboard/:id?',
      name: 'storyboard',
      component: () => import('../views/StoryboardView.vue'),
      props: (route) => ({ id: route.params.id ? Number(route.params.id) : undefined }),
    },
  ],
})
export default router
```

- [ ] **Step 2: Extract `views/LibraryView.vue`**

Move App.vue's ENTIRE current content (template, script setup, styles) into `LibraryView.vue` **except**: `ToastHost` (stays in App.vue) and the `useWebSocket('folders', ...)` subscription (stays in App.vue so folder sync survives on the storyboard route — the folders store is a singleton, handlers dispatch to it identically from either location; move the handler function along with it). Everything else — ThreePanel/MobileShell, viewer/slideshow mounting, all dialog flags and their components, keyboard shortcuts, watches — moves verbatim. Do not rename refs, do not reformat, do not "improve".

- [ ] **Step 3: Shrink `App.vue`**

```vue
<template>
  <router-view />
  <ToastHost />
</template>

<script setup lang="ts">
import ToastHost from './components/layout/ToastHost.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useFoldersStore } from './stores/folders'
// … the folders WS handler moved from the old App.vue, byte-identical …
</script>
```

(If the old App.vue's folders handler references anything else — e.g. types — carry those imports too. If other WS subscriptions live in App.vue today, leave them wherever their consumers are: check before moving.)

- [ ] **Step 4: `views/StoryboardView.vue` placeholder**

```vue
<template>
  <div class="storyboard-view">
    <div v-if="isMobile" class="mobile-note">
      <p>The storyboard editor is desktop-only.</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
    <div v-else class="storyboard-shell">
      <p>Storyboard view — id: {{ id ?? 'none' }}</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useViewport } from '../composables/useViewport'
defineProps<{ id?: number }>()
const { isMobile } = useViewport()
</script>
```

- [ ] **Step 5: `main.ts`** — add `import router from './router'` and `app.use(router)` after `app.use(pinia)`.

- [ ] **Step 6: AppHeader button**

Read `AppHeader.vue` and add a desktop-only "Storyboards" `Button` (`icon="pi pi-images"`, text style matching the neighboring buttons, `v-if="!isMobile"` if the header renders on mobile at all — check) that calls `router.push({ name: 'storyboard' })` via `useRouter()`. Place it beside the existing right-side action buttons, following the file's own idiom.

- [ ] **Step 7: Verify**

`cd frontend && npx vue-tsc --noEmit && npm run build` → clean. Then a behavioral self-check: `grep -c` the moved file for every dialog component name and ref that existed in the old App.vue (git show HEAD:frontend/src/App.vue) to confirm nothing was dropped; state the diff-audit result in your report.

- [ ] **Step 8: Commit**

```bash
git add -A frontend/src
git commit -m "feat(storyboard-ui): activate vue-router, extract LibraryView, storyboard route shell"
```

---

### Task 3: `stores/storyboard.ts`

**Files:**
- Create: `frontend/src/stores/storyboard.ts`

**Interfaces:**
- Consumes: Task 1 fetchers/types verbatim; `useWebSocket` from `composables/useWebSocket`.
- Produces (exact store surface — later tasks bind to these names):

```ts
export const useStoryboardStore = defineStore('storyboard', () => {
  // state
  const list = ref<StoryboardSummary[]>([])
  const tree = ref<StoryboardTree | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const selectedSceneId = ref<number | null>(null)
  const selectedPanelId = ref<number | null>(null)
  const jobToPanel = ref<Map<number, number>>(new Map())
  const panelJobState = ref<Map<number, { state: JobState; error: string | null; value?: number; max?: number }>>(new Map())
  const synthesis = ref<{ running: boolean; done: number; total: number; error: string | null }>({ running: false, done: 0, total: 0, error: null })

  // getters
  const selectedScene = computed<Scene | null>(...)
  const selectedPanel = computed<Panel | null>(...)
  const subjectsById = computed<Map<number, Subject>>(...)
  function panelById(id: number): Panel | null
  function keeperImage(panel: Panel): PanelImage | null   // images.find(i => i.id === panel.selected_image_id)

  // actions
  async function loadList(): Promise<void>
  async function load(id: number): Promise<void>          // fetch tree, default-select first scene/panel, then refreshActiveJobs()
  async function refresh(): Promise<void>                 // re-fetch current tree, preserve selection
  async function create(body: Parameters<typeof api.createStoryboard>[0]): Promise<number>
  async function remove(id: number): Promise<void>
  async function patchStoryboardFields(body: ...): Promise<void>   // optimistic w/ rollback on tree.value fields
  async function patchPanelFields(panelId: number, body: ...): Promise<void>  // optimistic w/ rollback; server response merged (keep local images array)
  async function patchSceneFields(sceneId: number, body: ...): Promise<void>  // optimistic w/ rollback
  async function addScene(name: string): Promise<void>              // await-then-refresh
  async function addPanel(sceneId: number, action: string): Promise<void>
  async function removeScene(id: number): Promise<void>
  async function removePanel(id: number): Promise<void>
  async function importText(text: string, confirm: boolean): Promise<void>    // parseStoryboard; replaces tree; resets selection; rethrows ApiError so the dialog can catch 409
  async function synthesize(panelIds?: number[], force?: boolean): Promise<void>  // sets synthesis={running:true,done:0,total:res.total,error:null}
  async function generate(panelIds?: number[], onlyFailed?: boolean): Promise<void> // then refreshActiveJobs()
  async function cancelAll(): Promise<void>
  async function selectImage(panelId: number, imageId: number | null): Promise<void> // await-then-merge response into panel
  async function refreshActiveJobs(): Promise<void>       // listJobs('queued')+listJobs('running'); rebuild jobToPanel + panelJobState for panels of the current tree
  // subject CRUD: addSubject/patchSubjectFields/removeSubject — await-then-refresh
  function attachWs(): void   // idempotent; called once from StoryboardView onMounted
})
```

- [ ] **Step 1: Implement state/getters/actions** as above. Optimistic pattern copied from `stores/folders.ts`: snapshot the target object (`{ ...panel }`), mutate in place, `await api.patch...`, on catch restore the snapshot and set `error.value`. `patchPanelFields` merges the server's returned panel over the local one but preserves the local `images` array if the response lacks it.

- [ ] **Step 2: WS wiring in `attachWs()`** (guard with a module-level `let attached = false`):

```ts
useWebSocket('storyboard', (event, data) => {
  const d = data as Record<string, unknown>
  if (tree.value && d.storyboard_id !== tree.value.id) return
  if (event === 'synthesis_progress') {
    synthesis.value = { running: true, done: d.done as number, total: d.total as number, error: null }
    // also patch the affected panel's prompt lazily: refresh() is too heavy per panel; instead
    // fetch nothing — the panel text updates on synthesis_complete's refresh()
  } else if (event === 'synthesis_complete') {
    synthesis.value = { running: false, done: synthesis.value.total, total: synthesis.value.total, error: null }
    void refresh()
  } else if (event === 'synthesis_error') {
    synthesis.value = { running: false, done: synthesis.value.done, total: synthesis.value.total, error: String(d.error ?? 'synthesis failed') }
  } else if (event === 'panel_images_changed') {
    void refreshPanelImages(d.panel_id as number)   // re-fetch tree OR just refresh(); simplest correct: void refresh()
  }
})
useWebSocket('comfy', (event, data) => {
  const d = data as Record<string, unknown>
  const jobId = d.job_id as number
  const panelId = jobToPanel.value.get(jobId)
  if (panelId === undefined) return
  if (event === 'job_update') {
    const state = d.state as JobState
    if (state === 'done' || state === 'cancelled') panelJobState.value.delete(panelId)
    else panelJobState.value.set(panelId, { state, error: (d.error as string) ?? null })
  } else if (event === 'job_progress') {
    const prev = panelJobState.value.get(panelId)
    panelJobState.value.set(panelId, { state: 'running', error: null, value: d.value as number, max: d.max as number })
  }
  // job_outputs: ignored — panel_images_changed carries the panel-scoped refresh
})
```

(`useWebSocket` registers `onUnmounted` cleanup — since the store outlives components, call `attachWs()` from `StoryboardView`'s setup so the subscription lifecycle follows the view; re-attaching on each mount is fine because `attached` guard… **no** — if the handler unregisters when the view unmounts, the guard would block re-attach. Resolution: drop the `attached` guard and simply call `useWebSocket` inside `attachWs()` invoked from `StoryboardView`'s `<script setup>` body each mount; unmount cleans up automatically. Document this in a comment.)

- [ ] **Step 3: `refreshActiveJobs()`** — after `load()` and `generate()`: fetch `listJobs('queued', 1000)` + `listJobs('running', 1000)`, keep only jobs whose `panel_id` belongs to the current tree, populate `jobToPanel` and `panelJobState` (`{state, error: null}`). This is what survives a page reload mid-generation.

- [ ] **Step 4: Verify + commit** — `npx vue-tsc --noEmit` clean.

```bash
git add frontend/src/stores/storyboard.ts
git commit -m "feat(storyboard-ui): storyboard Pinia store with WS correlation"
```

---

### Task 4: Landing, creation, preset registration

**Files:**
- Create: `frontend/src/components/storyboard/StoryboardLanding.vue`, `CreateStoryboardDialog.vue`, `PresetRegistrationDialog.vue`
- Modify: `frontend/src/views/StoryboardView.vue` (mount landing when `id === undefined`)

**Interfaces:**
- Consumes: store from Task 3 (`loadList`, `list`, `create`, `remove`); `api/comfy.ts` (`listPresets`, `createPreset`, `deletePreset`); router (`useRouter().push({name:'storyboard', params:{id}})`); types constants (`ASPECT_RATIOS`, `TARGET_MODELS`).
- Produces: `StoryboardLanding` (no props; emits nothing — navigates via router); `CreateStoryboardDialog` (emits `close`, `created(id: number)`); `PresetRegistrationDialog` (emits `close`, `registered`).

- [ ] **Step 1: `StoryboardLanding.vue`** — a centered column list: header row ("Storyboards" + `Button` "New storyboard" + `Button` "Workflow presets…"); each storyboard row shows name, aspect ratio, target model, updated_at, an Open button (`router.push`), and a delete button with a `confirm()` guard calling `store.remove`. `onMounted(store.loadList)`. Empty state: "No storyboards yet — create one and paste your scene text."

- [ ] **Step 2: `CreateStoryboardDialog.vue`** — house dialog skeleton. Fields: name (`InputText`, required), target model (`<select>` over `TARGET_MODELS`), aspect ratio (`<select>` over `ASPECT_RATIOS`), preset (`<select>` from `listPresets()` fetched on mount, with a "none yet" hint linking to preset registration), batch size (number 1–16, default 4), style block (textarea, optional), negative (textarea, optional). Submit → `store.create(...)` → emit `created(id)` → parent navigates to the new board. Show `ApiError.message` inline on failure (400 = invalid AR×model combination from the backend).

- [ ] **Step 3: `PresetRegistrationDialog.vue`** — fields: name, kind (`t2i`/`ref` radio), workflow JSON textarea + a file input (`<input type="file" accept=".json">` read via `FileReader.text()`). On submit: `JSON.parse` locally (inline error on bad JSON), then `createPreset({name, kind, workflow})`; a 400 from the backend lists missing `MS_*` titles — render `ApiError.message` verbatim in a `.error` block (that message is the product's main preset-authoring feedback). Also list existing presets with a delete button; a 409 on delete renders the backend's in-use message.

- [ ] **Step 4: Mount in `StoryboardView.vue`** — `v-if="id === undefined"` → `<StoryboardLanding />`; else keep the Task 2 placeholder (Task 5 replaces it).

- [ ] **Step 5: Verify + commit** — `npx vue-tsc --noEmit && npm run build`.

```bash
git add -A frontend/src
git commit -m "feat(storyboard-ui): landing list, creation dialog, preset registration"
```

---

### Task 5: Authoring layout — SceneStrip + PanelGrid

**Files:**
- Create: `frontend/src/components/storyboard/SceneStrip.vue`, `PanelGrid.vue`
- Modify: `frontend/src/views/StoryboardView.vue`

**Interfaces:**
- Consumes: store (tree, selection state, `keeperImage`, `panelJobState`, `patchSceneFields`, `addScene`, `addPanel`, `removeScene`, `removePanel`); `thumbnailUrl` from `api/client.ts`.
- Produces: the three-row authoring shell per spec §8.2 — header bar, `SceneStrip`, `PanelGrid`, and a bottom slot where Task 6's `PanelDetail` mounts. Components take no props (they read the store singleton) and emit nothing.

- [ ] **Step 1: `StoryboardView.vue` authoring shell** (replacing the placeholder branch):

```
<div class="sb-root" v-else>
  <header class="sb-header">
    <RouterLink to="/" class="sb-back">← Library</RouterLink>
    <h2>{{ store.tree?.name }}</h2>
    <span v-if="store.synthesis.running" class="sb-chip">synthesizing {{ store.synthesis.done }}/{{ store.synthesis.total }}</span>
    <span v-else-if="store.synthesis.error" class="sb-chip sb-chip--error" :title="store.synthesis.error">synthesis failed</span>
    <div class="sb-actions">
      <Button label="Import text" @click="importOpen = true" text />
      <Button label="Synthesize" @click="store.synthesize()" text />
      <Button label="Generate all" @click="store.generate()" />
      <Button label="Cancel" severity="danger" text @click="store.cancelAll()" />
      <Button icon="pi pi-cog" text @click="settingsOpen = true" />
    </div>
  </header>
  <SceneStrip />
  <PanelGrid />
  <PanelDetail v-if="store.selectedPanel" />   <!-- Task 6; keep commented/absent until then -->
  <ImportTextDialog v-if="importOpen" @close="importOpen = false" />        <!-- Task 6 -->
  <StoryboardSettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />  <!-- Task 6 -->
</div>
```

`onMounted`/`watch(() => props.id)` → `store.load(id)`; call `store.attachWs()` in setup. Guard every action button with `:disabled` when `store.loading`.

- [ ] **Step 2: `SceneStrip.vue`** — horizontal scroll row (`overflow-x: auto`). One card per `store.tree.scenes` (already sort-ordered by the backend): scene name + location/time subtitle, a row of up to ~6 keeper micro-thumbs (`keeperImage(panel)` → `thumbnailUrl(img.file_path)`, empty slot box otherwise), click card → `store.selectedSceneId = scene.id; store.selectedPanelId = scene.panels[0]?.id ?? null`. Selected card gets an accent border. Trailing "+ Scene" card → `prompt()`-free inline mini-form or a simple `window.prompt('Scene name')` is NOT acceptable — use a small inline input that appears on click (input + Add/Cancel), calling `store.addScene(name)`. A kebab-less small `×` on hover deletes the scene after `confirm()`.

- [ ] **Step 3: `PanelGrid.vue`** — grid of tiles for `store.selectedScene?.panels`:
  - keeper thumb via `keeperImage` + `thumbnailUrl`, else first candidate's thumb dimmed, else an empty placeholder;
  - overlay states from `store.panelJobState.get(panel.id)`: `queued` → "⏳ queued", `running` → spinner + `value/max` when present; latest failure → "⚠" badge with `error` as `title` (job state map holds `failed` until refreshed);
  - caption lines: `shot_size ?? '—'` + first subject name(s) resolved via `subjectsById`;
  - a lock glyph when `panel.prompt_locked === 1`;
  - click → `store.selectedPanelId = panel.id`; selected tile accent border;
  - trailing "+ Panel" tile → inline input for `action` → `store.addPanel(sceneId, action)`;
  - hover `×` deletes panel after `confirm()`.

- [ ] **Step 4: Verify + commit** — `npx vue-tsc --noEmit && npm run build` (leave Task 6 component tags out until they exist — add them in Task 6).

```bash
git add -A frontend/src
git commit -m "feat(storyboard-ui): authoring shell with scene strip and panel grid"
```

---

### Task 6: PanelDetail, import dialog, settings/subjects dialog

**Files:**
- Create: `frontend/src/components/storyboard/PanelDetail.vue`, `ImportTextDialog.vue`, `StoryboardSettingsDialog.vue`
- Modify: `frontend/src/views/StoryboardView.vue` (mount them per Task 5's shell sketch)

**Interfaces:**
- Consumes: store (`selectedPanel`, `subjectsById`, `patchPanelFields`, `synthesize`, `generate`, `selectImage`, `importText`, `patchStoryboardFields`, subject CRUD); `MediaViewer` (props `mediaList: Media[]`, `initialIndex: number`, emit `close`); `ApiError` from `api/client.ts`; constants `SHOT_SIZES/ANGLES/LENSES`.
- Produces: the three dialogs/panels wired into the shell.

- [ ] **Step 1: `PanelDetail.vue`** — bottom panel for `store.selectedPanel`:
  - Header: `Panel {{ sort_order + 1 }} · {{ shot_size ?? '—' }} · {{ angle ?? '—' }}` + buttons `Reroll` (`store.generate([panel.id])`), `Re-synth` (`store.synthesize([panel.id], true)`), disabled while that panel has an active job.
  - Editable fields, each committing on change via `store.patchPanelFields(panel.id, {...})`: `action` (input), `shot_size`/`angle`/`lens` (`<select>` with an empty "—" option → null), subjects (checkbox list over `store.tree.subjects`, ordered; first checked = primary — render an ordered chips row where clicking a chip moves it to front, and persist as `subject_ids`), `notes` (input), `negative` (input, placeholder "storyboard default").
  - Prompt block: textarea bound to `panel.prompt`; on user edit-commit send `patchPanel({prompt})` (backend force-locks). Status line: `🔒 edited` when `prompt_locked === 1`, else `prompt_source === 'brief' ? 'brief fallback' : prompt_source === 'llm' ? 'synthesized' : '—'` (spec §6.4 labeling). An unlock button (`patchPanelFields(panel.id, { prompt_locked: false })`) when locked.
  - Candidates row: for `panel.images` (variant-ordered) render `CandidateTile` (local component in this file): `thumbnailUrl(file_path)` image, `seed`/`variant_index` tooltip, a `✓` overlay when `id === panel.selected_image_id`. Click tile → `store.selectImage(panel.id, image.id)`; clicking the already-selected tile → `store.selectImage(panel.id, null)` (deselect). A separate expand icon (`pi pi-search-plus`) on hover opens the viewer:

```ts
const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  (store.selectedPanel?.images ?? []).map((img) => ({
    file_path: img.file_path,
    is_favorite: false,
    is_video: false,
    playback_speed: null,
    width: 0, height: 0, file_size: 0, frame_rate: null, duration: null,
  }) as Media)
)
```

`<MediaViewer v-if="viewerIndex !== null" :media-list="viewerMedia" :initial-index="viewerIndex" @close="viewerIndex = null" />`.

- [ ] **Step 2: `ImportTextDialog.vue`** — house dialog: big textarea, helper copy ("Paste your scene text — subjects, locations, one or two sentences per shot."), Import button → `store.importText(text, false)`; catch `ApiError`: `status === 409` → show a destructive-confirm block ("This storyboard already has scenes. Re-parsing replaces all scenes, panels and hand-edited prompts.") with a "Replace structure" button → `store.importText(text, true)`; `status === 422` → "The model couldn't parse this text" + message; `status === 503` → message verbatim (no VLM). Busy spinner while awaiting (parse takes ~seconds-minutes); disable Import while busy.

- [ ] **Step 3: `StoryboardSettingsDialog.vue`** — house dialog, two sections:
  - Storyboard fields: name, aspect ratio select, target model select, preset select (from `listPresets()`), batch size, base seed (number), style block textarea, negative textarea — commit all on Save via `store.patchStoryboardFields`, render 400s inline.
  - Subjects editor: list of `store.tree.subjects` with name/description inputs (commit on change via `patchSubjectFields`), LoRA name + strength inputs, reference path (plain text input for v1, with helper text "path of a library image"; 400 from the backend renders inline), delete button, and an "Add subject" row (name + description → `addSubject`).

- [ ] **Step 4: Mount all three in `StoryboardView.vue`** per Task 5's sketch (`importOpen`, `settingsOpen` refs).

- [ ] **Step 5: Verify + commit** — `npx vue-tsc --noEmit && npm run build`.

```bash
git add -A frontend/src
git commit -m "feat(storyboard-ui): panel detail editor, candidate picker, import and settings dialogs"
```

---

### Task 7: Hidden-media toggle, docs, final polish

**Files:**
- Modify: `frontend/src/api/media.ts` (add `include_hidden` param to the list fetcher), `frontend/src/stores/media.ts` (a `showHidden` ref threaded into the fetch + refetch on toggle), `frontend/src/components/layout/ViewMenubar.vue` (an "eye" toggle item labeled "Show hidden", desktop only), `frontend/src/types/media.ts` (`hidden?: boolean` on `Media`)
- Modify: `CLAUDE.md`, `docs/features.md`, `docs/api-reference.md` (if the frontend section mentions routes)

**Interfaces:**
- Consumes: Phase B's `GET /api/media?include_hidden=true` and the summary rows' `hidden` field.
- Produces: grid toggle; thumbnails of hidden media get a subtle "hidden" corner badge in `ThumbnailCard.vue` when `media.hidden === true` (one small, additive prop-free conditional — hidden rows only appear when the toggle is on).

- [ ] **Step 1:** Read `api/media.ts` + `stores/media.ts` to find the exact list-fetch call chain; add `include_hidden` as an optional query param defaulting to false; store gains `showHidden = ref(false)` and a `toggleShowHidden()` that refetches. ViewMenubar toggle item reflects `showHidden` state (check how existing toggle-ish items in that menubar render state).
- [ ] **Step 2:** `ThumbnailCard.vue`: tiny badge (`title="Hidden from library"`, e.g. a `pi pi-eye-slash` glyph, bottom-left) rendered `v-if="media.hidden"`.
- [ ] **Step 3: Docs.** `CLAUDE.md`: extend the mobile/architecture bullets with — routing is hash-history vue-router with `/` (LibraryView, the pre-Phase-C App.vue moved verbatim) and `/storyboard/:id?` (desktop-only authoring view); App.vue is a thin router shell holding ToastHost + the folders WS bridge; the storyboard store correlates comfy jobs to panels via `GET /api/comfy/jobs` + `job_update`/`job_progress`, and treats `panel_images_changed`/`synthesis_*` as refresh triggers; keeper selection goes only through `/panels/{id}/select` (never PATCH). `docs/features.md`: user-facing storyboard section (creation, import, synthesize, generate, candidates, keeper, hidden-media toggle).
- [ ] **Step 4: Final gates** — `cd frontend && npx vue-tsc --noEmit && npm run build`; `make quality test` (backend untouched but run it — docs commits shouldn't break it; the only acceptable failure is the known `test_file_watcher_triggers_reload` flake).
- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(storyboard-ui): hidden-media grid toggle and Phase C docs"
```

---

## Self-Review Notes

- Spec §8.1 routing → Task 2 (hash-history deviation recorded). §8.2 layout → Tasks 5–6. §8.3 reuse (MediaViewer, dialog conventions, AbortController-adjacent busy handling, folders-store optimistic pattern, storyboard store, typed fetchers, import dialog, preset dialog) → Tasks 1, 3, 4, 6. §8.3 WS channels → Task 3. §8.4 mobile → Tasks 2, 4 (entry points gated). §4.4 toggle → Task 7. §6.4 prompt-source labeling → Task 6 status line.
- Type-consistency: store surface names in Task 3 match usages quoted in Tasks 4–6 (`patchPanelFields`, `selectImage`, `synthesize(panelIds, force)`, `generate(panelIds, onlyFailed)`, `keeperImage`, `panelJobState`, `subjectsById`, `attachWs`); fetcher names in Task 1 match store usage.
- Known simplifications, deliberate: candidate viewer builds minimal Media objects (width/height 0 — Galleria/ImageViewer render from `streamUrl` regardless; verify ImageViewer doesn't divide by width, adjust if so); per-panel synthesis progress does not live-update panel text until `synthesis_complete` refresh; reference-image picking is a text path field in v1.
