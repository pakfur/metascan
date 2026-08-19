# Storyboard Workspace Redesign (Direction B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the storyboard editor's four-region layout (scene strip / panel grid / bottom detail pane / right tabbed panel) with Direction B's two regions — a 270px outline rail and the selected shot rendered as a document of beat cards — with zero backend or store-action changes.

**Architecture:** Presentational-only rewrite of `views/StoryboardView.vue` plus the `components/storyboard/` working set. Nine existing components are deleted or absorbed; eight new components are created (OutlineRail, ShotDocument, ShotHeader, PacingStrip, BeatCard, JobBadge, ModalShell, and three dialogs — VideoPromptDialog, BeatPromptDialog, ShotScriptDialog). All server state stays in the existing Pinia store (`stores/storyboard.ts`); only local selection/scroll state changes.

**Tech Stack:** Vue 3 `<script setup lang="ts">` + scoped CSS, Pinia, PrimeVue 4.5 (Button, Menu) + PrimeIcons 7, Vite. No test framework exists for the frontend — verification is `vue-tsc --noEmit`, `npm run build`, `make quality test` (CI parity), and a live visual pass.

**Spec:** `docs/design_handoff_storyboard_workspace/README.md` (the design handoff — read it in full before starting; every CSS value below that says "per spec" is enumerated there with exact numbers). Prototype source of truth for behavior: `docs/design_handoff_storyboard_workspace/redesign/RedesignWorkspace.jsx`. The rejected-directions rationale: `docs/design_handoff_storyboard_workspace/redesign/README.md`.

## Global Constraints

- **No backend, API, store-action, or type changes.** Every mutation used already exists in `frontend/src/stores/storyboard.ts` (verified: `load`, `refresh`, `attachWs`, `patchPanelFields`, `patchBeatFields`, `addBeat`, `removeBeat`, `addPanel`, `removePanel`, `removeScene`, `selectImage`, `synthesize`, `generate`, `compileVideo`, `generateVideo`, `composeStory`, `removePanelVideo`, `cancelAll`, plus `selectedSceneId/selectedPanelId/selectedBeatId`, `beatJobState`, `panelJobState`, `keeperImage`, `synthesis/compile/story` banners).
- **Pixel fidelity:** every number comes from the spec; off-grid values (5px, 6px, 10px, 14px, 22px, 236px, 374px) are deliberate. Do not round.
- **Colors via CSS variables only** (`--primary-color`, `--surface-*`, `--text-color*`, `--danger-color`, `--warn`, `--font-mono`); state tints via `color-mix` at the spec's percentages (primary 10/12/18%, danger 12/40/70%, warn 14%). Never a new hex.
- **Elevation is border-first:** cards have NO shadow; selection = primary border + `box-shadow: 0 0 0 1px var(--primary-color)`; the only real shadow is the dialog's `0 20px 60px rgba(0,0,0,0.3)`.
- **Motion:** `0.15s` hover/reveal only. No press effects, no entrance animations, no skeletons.
- **Icons:** PrimeIcons class names (`pi pi-cog` etc.). Unicode glyphs typed literally (`× ✕ ✓ ★ ✎ ▶ ↑ ↓ ← ▼ ⏳ ⚠ +`). Emoji `🔒 🎬` are load-bearing state markers — keep them.
- **Copy buttons flip their own label to `Copied` for 1500ms. Never fire a toast.**
- **Text fields commit on `change` (blur/Enter)** with the local-copy + last-synced-snapshot pattern (see Task 8's ported code); the resync watcher keys on `[id, updated_at]`.
- **Prompt lock is server-authoritative:** PATCHing a beat with `prompt` in the body forces `prompt_locked=1, prompt_source='user'` server-side; same for panels with `video_prompt`. Client sends `{ prompt }` only — the response merge brings the lock back. (Matches current `BeatForm.commitPrompt`.)
- **Desktop-only stays:** the mobile note and landing (`StoryboardLanding.vue`) are unchanged and out of scope. Also unchanged: `CreateStoryboardDialog`, `OutlineDialog`, `ImportTextDialog`, `SceneEditDialog`, `StoryboardSettingsDialog`, `PresetRegistrationDialog`, `DeleteImagesDialog`, `ReferenceImagePicker`, `stores/storyboard.ts`, `types/storyboard.ts`, `api/*`.
- **Quality gate before every commit:** `cd frontend && npx vue-tsc --noEmit` passes. Final gate: `npm run build` + `make quality test` (backend must stay green — it is untouched, so any failure is pre-existing flake; see memory note re `test_file_watcher_triggers_reload` on WSL2).
- Branch: all work on `feature/storyboard-workspace-redesign` off `main`.

## Resolved design decisions (user-approved 2026-08-19)

1. **Shot script** gets a `Shot script` button beside `Video prompt` in the shot header, opening a dialog with the existing per-beat block rendering + whole-script Copy (spec's open decision 1, recommended option).
2. **Video anchor select** lives in the Video-prompt dialog's head; the `recompile suggested` warn chip lives on the shot header next to the lint chip (spec's open decision 2, as prescribed).
3. **`⋯` overflow** uses PrimeVue `Menu` in popup mode (spec's open decision 3, as prescribed). `Menu` is NOT yet imported anywhere — import it locally in the components that use it (`import Menu from 'primevue/menu'`), do not register globally.
4. **Scene actions**: hover-revealed `⋯` kebab on the outline-rail scene header opens a Menu: Render scene video / Edit scene / Delete scene (spec's open decision 4, kebab option).
5. **Empty board**: dashed empty-state box in the document area pointing at Compose / Import text (spec's open decision 5, as prescribed).

## Deliberate deviations from the prototype (flagged, do not "fix" back)

- **Dialog-line editor keeps the conditional `voice` input** (shown only when speaker = `other voice`, i.e. `subject_id === null`) **and the `language` input** — the prototype's dialog row drops both, but they are real `DialogLine` fields the app persists; dropping the inputs would orphan the data. Keep them sized to fit the 340px column (voice `flex: 0 0 64px`, language `flex: 0 0 64px`).
- **The 15s cap is dynamic**: use `VIDEO_TARGET_CAPS[store.tree?.video_target ?? ''] ?? DEFAULT_SHOT_CAP` (existing `BeatsEditor` behavior), not the literal 15 the prototype hardcodes.
- **The re-beat 409 `confirm_required` banner** (currently in `BeatsEditor`) moves to the shot header, under Row 1 — the prototype's Re-beat button is a no-op so it has no equivalent; the flow must survive.
- **`video_prompt_source` display mapping**: `'compiled'` → `compiled`, `'user'` → `user edited`, `null` → `not compiled` (spec copy list).

## File structure (end state)

```
frontend/src/
  views/StoryboardView.vue                     REWRITE — shell: header + rail + document
  components/storyboard/
    OutlineRail.vue                            NEW — region 1 (replaces SceneStrip + PanelGrid nav)
    ShotDocument.vue                           NEW — region 2 scroll container: ShotHeader + BeatCards + "+ Beat"
    ShotHeader.vue                             NEW — shot rows 1–4 (absorbs PanelDetail + PanelVideos + PanelSidePanel's video-prompt strip)
    PacingStrip.vue                            NEW — proportional beat segments + cap bar
    BeatCard.vue                               NEW — one card per beat (absorbs BeatRow + BeatForm + BeatImages)
    JobBadge.vue                               NEW — fill-layout job overlay (rail thumb, pacing segment)
    ModalShell.vue                             NEW — shared dialog scrim + card chrome for the three dialogs below
    VideoPromptDialog.vue                      NEW — read-only compiled prompt + anchor select + Compile/Copy/Render
    BeatPromptDialog.vue                       NEW — editable draft prompt (Save/Copy/Re-synth/Cancel)
    ShotScriptDialog.vue                       NEW — per-beat script blocks + Copy (decision 1)
    LoraListEditor.vue                         KEEP as-is (container width changes only, in ShotHeader)
    SceneStrip.vue, PanelGrid.vue,
    PanelSidePanel.vue, BeatRow.vue,
    PanelDetail.vue, BeatsEditor.vue,
    BeatForm.vue, BeatImages.vue,
    PanelVideos.vue                            DELETE (Task 11)
  style.css                                    +--warn, +--font-mono tokens (Task 1)
  components/storyboard/OutlineDialog.vue      one-line var rename (Task 1)
```

Component interfaces (authoritative for all tasks):

- `JobBadge`: props `{ state: JobState; error?: string | null }`. Renders absolutely-positioned fill overlay; parent supplies `position: relative`.
- `ModalShell`: props `{ width?: string /* default '640px' */ }`, emits `close` (scrim click), slots: default (body), `actions` (footer buttons).
- `VideoPromptDialog`: props `{ panel: Panel; index: number }`, emits `close`.
- `BeatPromptDialog`: props `{ beat: Beat; index: number }`, emits `close`.
- `ShotScriptDialog`: props `{ panel: Panel; scene: Scene }`, emits `close`.
- `PacingStrip`: props `{ panel: Panel }`, emits `select-beat(beatId: number)`.
- `BeatCard`: props `{ beat: Beat; index: number; subjects: Subject[]; selected: boolean; canUp: boolean; canDown: boolean }`, emits `select`, `move(dir: -1 | 1)`. Delete is handled internally.
- `ShotHeader`: props `{ panel: Panel; scene: Scene; index: number }`, emits `select-beat(beatId: number)` (re-emit from PacingStrip).
- `ShotDocument`: no props (reads `store.selectedPanel` / `store.selectedScene`). Owns the scroll container and the beat-id → card-element map.
- `OutlineRail`: no props (reads the store).

---

### Task 0: Branch

- [ ] **Step 1: Create the feature branch**

```bash
cd /home/jk/gws/metascan
git checkout -b feature/storyboard-workspace-redesign
```

### Task 1: Design tokens — `--warn` and `--font-mono`

**Files:**
- Modify: `frontend/src/style.css` (token blocks at lines 1–29)
- Modify: `frontend/src/components/storyboard/OutlineDialog.vue:268` (rename `--font-family-mono` → `--font-mono`)

**Interfaces:**
- Produces: `var(--warn)` and `var(--font-mono)` usable without fallbacks by every later task.

- [ ] **Step 1: Declare the tokens in both theme blocks**

In `frontend/src/style.css`, add to the light `:root` block (after `--danger-color: #ef4444;`):

```css
  --warn: #e0a030;
  --font-mono: ui-monospace, Menlo, Consolas, monospace;
```

Add the same two lines to the dark block (after `--danger-color: #f87171;`). `--warn` is the value every component already falls back to; `--font-mono` matches `PanelSidePanel`'s existing fallback chain. (Same value in both themes — that is what the shipping fallbacks render today; the spec calls only for declaring them properly.)

- [ ] **Step 2: Unify the mono-font variable name**

In `frontend/src/components/storyboard/OutlineDialog.vue` line 268, change `var(--font-family-mono, ui-monospace, monospace)` to `var(--font-mono)`.

- [ ] **Step 3: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/style.css frontend/src/components/storyboard/OutlineDialog.vue
git commit -m "feat(storyboard): declare --warn and --font-mono design tokens"
```

### Task 2: JobBadge

**Files:**
- Create: `frontend/src/components/storyboard/JobBadge.vue`

**Interfaces:**
- Produces: `JobBadge` with props `{ state: JobState; error?: string | null }` — the fill-layout job overlay per spec "Job states". Parent element must be `position: relative; overflow: hidden`.

- [ ] **Step 1: Write the component**

```vue
<script setup lang="ts">
import type { JobState } from '../../types/storyboard'

// Fill-layout job overlay (spec "Job states"): absolute inset-0 over a
// thumbnail. queued -> hourglass glyph, running -> PrimeIcons spinner,
// failed -> warning glyph with the error in title. No badge for done.
defineProps<{ state: JobState; error?: string | null }>()
</script>

<template>
  <span
    class="jb"
    :class="{ failed: state === 'failed' }"
    :title="state === 'failed' ? (error ?? 'failed') : undefined"
  >
    <span v-if="state === 'queued'">⏳</span>
    <span v-else-if="state === 'running'" class="pi pi-spin pi-spinner" />
    <span v-else-if="state === 'failed'">⚠</span>
  </span>
</template>

<style scoped>
.jb {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}

.jb.failed {
  background: color-mix(in srgb, var(--danger-color) 70%, black);
  cursor: help;
}
</style>
```

- [ ] **Step 2: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/JobBadge.vue
git commit -m "feat(storyboard): fill-layout JobBadge overlay"
```

### Task 3: ModalShell + VideoPromptDialog

**Files:**
- Create: `frontend/src/components/storyboard/ModalShell.vue`
- Create: `frontend/src/components/storyboard/VideoPromptDialog.vue`

**Interfaces:**
- Consumes: store actions `compileVideo(panelIds?)`, `generateVideo(panelIds?)`, `patchPanelFields(panelId, body)`; `copyToClipboard(text)` from `frontend/src/utils/clipboard.ts`; `VIDEO_ANCHORS` from `types/storyboard`.
- Produces: `ModalShell` (props `{ width?: string }`, emit `close`, slots default + `actions`); `VideoPromptDialog` (props `{ panel: Panel; index: number }`, emit `close`).

- [ ] **Step 1: Write ModalShell**

Spec "Dialogs" section gives the exact shell. One shared component instead of a seventh copy of the overlay/card CSS:

```vue
<script setup lang="ts">
withDefaults(defineProps<{ width?: string }>(), { width: '640px' })
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div class="msh-overlay" @click.self="emit('close')">
    <div class="msh-card" :style="{ width }">
      <slot />
      <div class="msh-actions"><slot name="actions" /></div>
    </div>
  </div>
</template>

<style scoped>
.msh-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 900;
}

.msh-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.msh-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

/* Footer buttons per spec: padding 8px 20px, font-size 14px. Provided as
   deep classes so each dialog's buttons share one definition. */
.msh-actions :deep(.msh-btn) {
  padding: 8px 20px;
  font-size: 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  cursor: pointer;
}

.msh-actions :deep(.msh-btn:hover:not(:disabled)) {
  background: var(--surface-hover);
}

.msh-actions :deep(.msh-btn:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}

.msh-actions :deep(.msh-btn--primary) {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: #fff;
}
</style>
```

- [ ] **Step 2: Write VideoPromptDialog**

Per spec "Video prompt dialog — read-only" plus decision 2 (anchor select in the head). Behavior from `RedesignWorkspace.jsx:109-146` and the existing `PanelSidePanel.vue` anchor logic:

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { VIDEO_ANCHORS, type Panel } from '../../types/storyboard'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ panel: Panel; index: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const VIDEO_TARGET_LABELS: Record<string, string> = { minimax: 'MiniMax H3' }
const chipLabel = computed(() => {
  const name = VIDEO_TARGET_LABELS[store.tree?.video_target ?? ''] ?? store.tree?.video_target
  return store.tree?.video_mode ? `${name} · ${store.tree.video_mode}` : (name ?? '')
})

const words = computed(() =>
  props.panel.video_prompt ? props.panel.video_prompt.trim().split(/\s+/).length : 0,
)
const sourceLabel = computed(() =>
  props.panel.video_prompt_source === 'user'
    ? 'user edited'
    : (props.panel.video_prompt_source ?? 'not compiled'),
)

// Anchor select only matters when the video mode consumes a first-frame
// anchor (same gate as the old PanelSidePanel).
const anchorRelevant = computed(
  () => store.tree?.video_mode === 'i2va' || store.tree?.video_mode === 'fl2va',
)
const ANCHOR_LABELS: Record<string, string> = {
  keeper: 'First frame from keeper',
  prev_last: 'Continue from previous shot',
}
function onAnchorChange(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  void store.patchPanelFields(props.panel.id, { video_anchor: val || null })
}

function unlock(): void {
  void store.patchPanelFields(props.panel.id, { video_prompt_locked: 0 })
}

const copied = ref(false)
async function copy(): Promise<void> {
  if (!props.panel.video_prompt) return
  await copyToClipboard(props.panel.video_prompt)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

function compile(): void {
  void store.compileVideo([props.panel.id])
}

async function renderVideo(): Promise<void> {
  const res = await store.generateVideo([props.panel.id])
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}
</script>

<template>
  <ModalShell @close="emit('close')">
    <div class="vpd-head">
      <h3>Video prompt — shot {{ index + 1 }}</h3>
      <span class="vpd-chip">{{ chipLabel }}</span>
      <select
        v-if="anchorRelevant"
        class="vpd-anchor"
        title="First-frame anchor for this shot's video"
        :value="panel.video_anchor ?? ''"
        @change="onAnchorChange"
      >
        <option v-for="a in VIDEO_ANCHORS" :key="a" :value="a">{{ ANCHOR_LABELS[a] }}</option>
      </select>
      <span class="vpd-hint vpd-meta">
        {{ sourceLabel }}<template v-if="words"> · {{ words }} words</template>
      </span>
      <button v-if="panel.video_prompt_locked === 1" type="button" class="vpd-link" @click="unlock">
        🔒 Unlock
      </button>
    </div>
    <ul v-if="panel.video_prompt_warnings.length" class="vpd-warnings">
      <li v-for="(w, i) in panel.video_prompt_warnings" :key="i">{{ w }}</li>
    </ul>
    <pre v-if="panel.video_prompt" class="vpd-body">{{ panel.video_prompt }}</pre>
    <p v-else class="vpd-hint vpd-empty">
      No video prompt compiled yet — Compile builds it from this shot's beats.
    </p>
    <p class="vpd-hint vpd-note">
      Compiled from the beats below. Edit a beat's framing, cast or dialog and recompile —
      hand-editing this text locks it against the next compile pass.
    </p>
    <template #actions>
      <button type="button" class="msh-btn msh-btn--primary" :disabled="store.compile.running" @click="compile">Compile</button>
      <button type="button" class="msh-btn" :disabled="!panel.video_prompt" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" :disabled="!panel.video_prompt" @click="renderVideo">Render video</button>
      <button type="button" class="msh-btn" @click="emit('close')">Close</button>
    </template>
  </ModalShell>
</template>
```

Scoped CSS (exact values from spec "Video prompt dialog"): `.vpd-head { display:flex; align-items:center; gap:10px; margin-bottom:10px }` with `h3 { margin:0; font-size:18px; font-weight:600 }`; `.vpd-chip { font-size:12px; padding:4px 10px; border-radius:999px; color:var(--primary-color); background:color-mix(in srgb, var(--primary-color) 12%, transparent) }`; `.vpd-meta { margin-left:auto; font-variant-numeric:tabular-nums }`; `.vpd-hint { font-size:11px; color:var(--text-color-secondary) }`; `.vpd-link { background:none; border:none; padding:0; color:var(--primary-color); text-decoration:underline; font-size:11px; cursor:pointer }`; `.vpd-warnings { list-style:disc; padding:8px 10px 8px 26px; margin:0 0 10px; color:var(--warn); font-size:12px; line-height:1.5; background:color-mix(in srgb, var(--warn) 14%, transparent); border-radius:6px }`; `.vpd-body { margin:0; padding:14px; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); font-family:var(--font-mono); font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; overflow-y:auto; max-height:52vh }`; `.vpd-empty { font-size:13px; padding:24px 0; text-align:center }`; `.vpd-note { margin-top:10px }`; `.vpd-anchor { padding:5px 8px; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); color:var(--text-color); font-size:12px }`.

- [ ] **Step 3: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/ModalShell.vue frontend/src/components/storyboard/VideoPromptDialog.vue
git commit -m "feat(storyboard): ModalShell + read-only VideoPromptDialog"
```

### Task 4: BeatPromptDialog

**Files:**
- Create: `frontend/src/components/storyboard/BeatPromptDialog.vue`

**Interfaces:**
- Consumes: `ModalShell`, `patchBeatFields`, `synthesize([id], true)`, `copyToClipboard`.
- Produces: `BeatPromptDialog` with props `{ beat: Beat; index: number }`, emit `close`.

- [ ] **Step 1: Write the component**

Draft semantics per spec "Beat prompt dialog — editable": the dialog holds a LOCAL draft; Cancel/scrim discard; Save commits `{ prompt: draft }` (server locks it). Behavior from `RedesignWorkspace.jsx:150-189`:

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat } from '../../types/storyboard'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ beat: Beat; index: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const draft = ref(props.beat.prompt ?? '')
const dirty = computed(() => draft.value !== (props.beat.prompt ?? ''))
const words = computed(() => (draft.value.trim() ? draft.value.trim().split(/\s+/).length : 0))

const status = computed(() => {
  if (props.beat.prompt_locked === 1) return '🔒 edited'
  if (props.beat.prompt_source === 'llm') return 'synthesized'
  if (props.beat.prompt_source === 'brief') return 'brief fallback'
  return 'not synthesized'
})

const context = computed(() => {
  const cam = props.beat.camera_motion ? ` · ${props.beat.camera_motion.replace(/_/g, ' ')}` : ''
  return `${props.beat.shot_size ?? '—'} · ${props.beat.angle ?? '—'} · ${props.beat.lens ?? '—'}${cam} — ${props.beat.action}`
})

function unlock(): void {
  void store.patchBeatFields(props.beat.id, { prompt_locked: 0 })
}

function commit(): void {
  // Server forces prompt_locked=1 / prompt_source='user' when `prompt` is in
  // the PATCH body -- send only the prompt (see Global Constraints).
  if (dirty.value) void store.patchBeatFields(props.beat.id, { prompt: draft.value })
  emit('close')
}

const copied = ref(false)
async function copy(): Promise<void> {
  if (!draft.value) return
  await copyToClipboard(draft.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

function resynth(): void {
  void store.synthesize([props.beat.id], true)
}
</script>

<template>
  <ModalShell width="576px" @close="emit('close')">
    <div class="bpd-head">
      <h3>Prompt — beat {{ index + 1 }}</h3>
      <span class="bpd-hint">{{ status }}</span>
      <span class="bpd-hint bpd-words">{{ words }} words</span>
      <button v-if="beat.prompt_locked === 1" type="button" class="bpd-link" @click="unlock">Unlock</button>
    </div>
    <p class="bpd-context">{{ context }}</p>
    <textarea v-model="draft" rows="16" class="bpd-body" placeholder="No prompt synthesized yet." />
    <p class="bpd-note">
      Saving marks the prompt user-edited and locks it, so the next Synthesize pass leaves it alone.
    </p>
    <template #actions>
      <button type="button" class="msh-btn msh-btn--primary" @click="commit">{{ dirty ? 'Save prompt' : 'Done' }}</button>
      <button type="button" class="msh-btn" :disabled="!draft" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" :disabled="store.synthesis.running" @click="resynth">Re-synth</button>
      <button type="button" class="msh-btn" @click="emit('close')">Cancel</button>
    </template>
  </ModalShell>
</template>
```

Scoped CSS: `.bpd-head` as `.vpd-head` (gap 10, margin-bottom 8, h3 18px/600); `.bpd-words { margin-left:auto; font-variant-numeric:tabular-nums }`; `.bpd-hint { font-size:11px; color:var(--text-color-secondary) }`; `.bpd-link` as `.vpd-link`; `.bpd-context { font-size:12px; color:var(--text-color-secondary); margin:0 0 8px }`; `.bpd-body { width:100%; box-sizing:border-box; font-family:var(--font-mono); font-size:13px; line-height:1.6; padding:14px; resize:vertical; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); color:var(--text-color) }` with `:focus { outline:none; border-color:var(--primary-color) }`; `.bpd-note { font-size:11px; color:var(--text-color-secondary); margin:10px 0 0 }`.

- [ ] **Step 2: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/BeatPromptDialog.vue
git commit -m "feat(storyboard): editable BeatPromptDialog with local-draft semantics"
```

### Task 5: ShotScriptDialog

**Files:**
- Create: `frontend/src/components/storyboard/ShotScriptDialog.vue`

**Interfaces:**
- Consumes: `buildShotScript` / `buildShotScriptBlocks` from `frontend/src/utils/shotScript.ts` (signatures: `(panel: Panel, scene: Scene, subjects: Subject[])`); `store.selectedBeatId`; `copyToClipboard`; `ModalShell`.
- Produces: `ShotScriptDialog` with props `{ panel: Panel; scene: Scene }`, emit `close`.

- [ ] **Step 1: Write the component**

Ports `PanelSidePanel.vue`'s Preview-tab script rendering (per-beat blocks, click selects the beat, whole-script Copy) into the dialog shell:

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, Scene } from '../../types/storyboard'
import { buildShotScript, buildShotScriptBlocks } from '../../utils/shotScript'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ panel: Panel; scene: Scene }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const blocks = computed(() =>
  buildShotScriptBlocks(props.panel, props.scene, store.tree?.subjects ?? []),
)

const copied = ref(false)
async function copy(): Promise<void> {
  await copyToClipboard(buildShotScript(props.panel, props.scene, store.tree?.subjects ?? []))
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<template>
  <ModalShell @close="emit('close')">
    <div class="ssd-head">
      <h3>Shot script — shot {{ (panel.sort_order ?? 0) + 1 }}</h3>
    </div>
    <div class="ssd-body">
      <pre class="ssd-block ssd-header-block">{{ blocks.header }}</pre>
      <pre
        v-for="b in blocks.beats"
        :key="b.beatId"
        class="ssd-block"
        :class="{ selected: b.beatId === store.selectedBeatId }"
        @click="store.selectedBeatId = b.beatId"
      >{{ b.text }}</pre>
      <p v-if="blocks.beats.length === 0" class="ssd-hint">(no beats)</p>
    </div>
    <template #actions>
      <button type="button" class="msh-btn" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" @click="emit('close')">Close</button>
    </template>
  </ModalShell>
</template>
```

Scoped CSS: `.ssd-head h3 { margin:0 0 10px; font-size:18px; font-weight:600 }`; `.ssd-body { display:flex; flex-direction:column; gap:6px; overflow-y:auto; max-height:52vh }`; `.ssd-block { margin:0; padding:10px 12px; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); font-family:var(--font-mono); font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; cursor:pointer }`; `.ssd-block.selected { border-color:var(--primary-color); box-shadow:0 0 0 1px var(--primary-color) }`; `.ssd-header-block { cursor:default }`; `.ssd-hint { font-size:12px; color:var(--text-color-secondary) }`.

- [ ] **Step 2: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/ShotScriptDialog.vue
git commit -m "feat(storyboard): ShotScriptDialog re-homes the per-beat script blocks"
```

### Task 6: PacingStrip

**Files:**
- Create: `frontend/src/components/storyboard/PacingStrip.vue`

**Interfaces:**
- Consumes: `JobBadge`, `thumbnailUrl` from `../../api/client`, `store.beatJobState`, `VIDEO_TARGET_CAPS` / `DEFAULT_SHOT_CAP`.
- Produces: `PacingStrip` with props `{ panel: Panel }`, emit `select-beat(beatId: number)`.

- [ ] **Step 1: Write the component**

Spec "Row 2 — pacing strip" + `RedesignWorkspace.jsx:52-105`. Key rules: `scale = max(total, cap)`; segment width `(duration/scale)*100%` with `min-width:30px`; the duration label renders only when the segment's computed pixel width ≥ 44px (measured track width, re-measured on resize — use a `ResizeObserver`); suppressed durations move into the `title`; keeper image at opacity 1 (keeper picked) / 0.45 (fallback first image); dashed inset box when no images; warn left edge on `is_cut`; trailing dashed unused-budget block when under cap; 3px cap bar beneath.

```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import {
  DEFAULT_SHOT_CAP,
  VIDEO_TARGET_CAPS,
  type Beat,
  type BeatImage,
  type Panel,
} from '../../types/storyboard'
import JobBadge from './JobBadge.vue'

const props = defineProps<{ panel: Panel }>()
const emit = defineEmits<{ (e: 'select-beat', beatId: number): void }>()
const store = useStoryboardStore()

const cap = computed(() => VIDEO_TARGET_CAPS[store.tree?.video_target ?? ''] ?? DEFAULT_SHOT_CAP)
const total = computed(() => props.panel.beats.reduce((s, b) => s + b.duration_s, 0))
const over = computed(() => total.value > cap.value)
const scale = computed(() => (over.value ? total.value : cap.value))

// Measured track width so a segment can decide whether its duration label
// fits before rendering it -- a 0.5s beat is only ~30px wide and the label
// would collide with the index ("30.5s").
const track = ref<HTMLElement | null>(null)
const trackW = ref(1040)
let ro: ResizeObserver | null = null
onMounted(() => {
  ro = new ResizeObserver(() => {
    trackW.value = track.value?.clientWidth || 1040
  })
  if (track.value) ro.observe(track.value)
})
onBeforeUnmount(() => ro?.disconnect())

function keeper(b: Beat): BeatImage | null {
  return b.images.find((i) => i.id === b.selected_image_id) ?? b.images[0] ?? null
}
function pct(b: Beat): number {
  return (b.duration_s / scale.value) * 100
}
function showDuration(b: Beat): boolean {
  return (b.duration_s / scale.value) * trackW.value >= 44
}
function segTitle(b: Beat): string {
  return showDuration(b) ? b.action : `${b.duration_s.toFixed(1)}s — ${b.action}`
}
</script>

<template>
  <div>
    <div ref="track" class="ps-track">
      <button
        v-for="(b, i) in panel.beats"
        :key="b.id"
        type="button"
        class="ps-seg"
        :class="{ selected: b.id === store.selectedBeatId, cut: b.is_cut === 1 }"
        :style="{ width: pct(b) + '%' }"
        :title="segTitle(b)"
        @click="emit('select-beat', b.id)"
      >
        <img
          v-if="keeper(b)"
          :src="thumbnailUrl(keeper(b)!.file_path)"
          alt=""
          class="ps-img"
          :style="{ opacity: b.selected_image_id != null ? 1 : 0.45 }"
        />
        <span v-else class="ps-empty" />
        <span class="ps-label">
          <span>{{ i + 1 }}</span>
          <span v-if="showDuration(b)">{{ b.duration_s.toFixed(1) }}s</span>
        </span>
        <JobBadge
          v-if="store.beatJobState.get(b.id)"
          :state="store.beatJobState.get(b.id)!.state"
          :error="store.beatJobState.get(b.id)!.error"
        />
      </button>
      <div
        v-if="!over && total < cap"
        class="ps-budget"
        :style="{ width: ((cap - total) / scale) * 100 + '%' }"
        :title="`${(cap - total).toFixed(1)}s of clip budget unused`"
      />
    </div>
    <div class="ps-capbar">
      <div class="ps-captrack">
        <div
          class="ps-capfill"
          :class="{ over }"
          :style="{ width: Math.min(100, (total / cap) * 100) + '%' }"
        />
      </div>
      <span class="ps-caplabel" :class="{ over }">
        {{ total.toFixed(1) }}s / {{ cap }}s{{ over ? ' — exceeds H3 clip cap' : '' }}
      </span>
    </div>
  </div>
</template>
```

Scoped CSS (spec values): `.ps-track { display:flex; gap:2px; height:48px }`; `.ps-seg { position:relative; min-width:30px; flex-shrink:0; padding:0; overflow:hidden; cursor:pointer; border:2px solid transparent; border-radius:5px; background:var(--surface-ground) }`; `.ps-seg.selected { border-color:var(--primary-color) }`; `.ps-seg.cut { border-left:3px solid var(--warn) }`; `.ps-img { width:100%; height:100%; object-fit:cover; display:block }`; `.ps-empty { position:absolute; inset:2px; border:1px dashed var(--surface-border); border-radius:3px }`; `.ps-label { position:absolute; inset:0; display:flex; align-items:flex-end; justify-content:space-between; gap:6px; padding:2px 4px; background:linear-gradient(transparent, rgba(0,0,0,0.7)); color:#fff; font-size:10px; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden }`; `.ps-budget { border:1px dashed var(--surface-border); border-radius:5px }`; `.ps-capbar { display:flex; align-items:center; gap:8px; margin-top:4px }`; `.ps-captrack { flex:1; height:3px; border-radius:2px; background:var(--surface-hover); overflow:hidden }`; `.ps-capfill { height:100%; background:var(--primary-color) }`; `.ps-capfill.over { background:var(--warn) }`; `.ps-caplabel { font-size:11px; color:var(--text-color-secondary); font-variant-numeric:tabular-nums }`; `.ps-caplabel.over { color:var(--warn) }`.

- [ ] **Step 2: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/PacingStrip.vue
git commit -m "feat(storyboard): PacingStrip with proportional beat segments and cap bar"
```

### Task 7: BeatCard

**Files:**
- Create: `frontend/src/components/storyboard/BeatCard.vue`

**Interfaces:**
- Consumes: `BeatPromptDialog`, `DeleteImagesDialog` (props `{title, message, imageCount?}`, emits `purge/keep/cancel`), `MediaViewer` (props `media-list`, `initial-index`, `:allow-destructive="false"`, emit `close`), `JobBadge` is NOT used here (the head row uses a text chip per spec), store (`patchBeatFields`, `removeBeat`, `selectImage`, `generate`, `synthesize`, `beatJobState`, `synthesis`), enums `SHOT_SIZES/ANGLES/LENSES/CAMERA_MOTIONS/CAMERA_AMPLITUDES/CAMERA_SPEEDS`, `thumbnailUrl`, `isVideoPath` from `../../utils/path`.
- Produces: `BeatCard` — props `{ beat: Beat; index: number; subjects: Subject[]; selected: boolean; canUp: boolean; canDown: boolean }`, emits `select`, `move(dir: -1 | 1)`.

- [ ] **Step 1: Write the script — port BeatForm's logic wholesale**

Copy the following from `BeatForm.vue` (currently at the listed lines) into the new component, unchanged: the local-copy/snapshot refs + `sync()` + the `[id, updated_at]` watcher (L27-84), `subjectName/toggleSubject/promoteSubject` (L86-102), `commitShotSize/commitAngle/commitLens/commitPrompt/unlockPrompt` (L104-141), `promptStatusLabel` (L143-148 — note the card's label set per spec is `synthesized / brief fallback / 🔒 edited / —`, which is exactly what it computes), `hasActiveJob` + `resynth` (L150-154), `commit`/`onActionInput`/`onActionChange`/`onDurationInput`/`onDurationChange`/`onSoundInput`/`onSoundChange` (L156-194), `commitSelect`/`onCameraChange`/`toggleCut` (L196-212), the dialog-line handlers (L214-252), and the delete flow `pendingDelete`/`remove`/`confirmDelete` (L258-272).

Copy from `BeatImages.vue`: `candidateTooltip` (L21-23), the keeper click/dblclick debounce pair `onCandidateClick`/`onCandidateDblClick` with `clickTimer` (L30-47), and `viewerIndex`/`viewerMedia` (L49-65). The reroll button calls `store.generate([beat.id])` and shows `Generating…` while `hasActiveJob`.

Add the pieces new to the card:

```ts
const promptOpen = ref(false)

const jobChip = computed(() => store.beatJobState.get(props.beat.id)?.state ?? null)

const emit = defineEmits<{ (e: 'select'): void; (e: 'move', dir: -1 | 1): void }>()
```

- [ ] **Step 2: Write the template — spec §2b exactly**

`<article class="bc" :class="{ selected }" @click="emit('select')">` containing:

**Head row** (`display:flex; align-items:center; gap:8px`): `BEAT {{ index + 1 }}` label (12px/600, secondary → primary when selected); `hard cut` (11px/600, `var(--warn)`) when `beat.is_cut === 1`; duration `{{ beat.duration_s.toFixed(1) }}s` (11px secondary tabular); neutral job chip `<span v-if="jobChip" class="bc-chip">{{ jobChip }}</span>`; right group (`margin-left:auto; display:flex; gap:4px`) of three 22×22 outline icon buttons `↑ ↓ ✕` — `↑` disabled `!canUp` → `@click.stop="emit('move', -1)"`, `↓` disabled `!canDown` → `@click.stop="emit('move', 1)"`, `✕` → `@click.stop="remove()"` with hover color `var(--danger-color)`. All three `.stop` so they don't also select.

**Body** (`display:flex; gap:14px; align-items:flex-start`):

*Left column* (`flex:1; min-width:0; display:flex; flex-direction:column; gap:10px`):
1. `Action` — eyebrow label + 2-row textarea bound `:value="actionVal" @input="onActionInput" @change="onActionChange"`.
2. Field row (`display:flex; align-items:flex-end; gap:10px`): Shot size / Angle / Lens selects (each `flex:1`, first option `—` with value `""`, options from the enum consts), `Dur (s)` number input `flex:0 0 84px` `step="0.5" min="0.5"` bound to `durationVal`, and the `Cut` toggle button (`padding:6px 14px; font-size:12px`; off `background:var(--surface-ground); color:var(--text-color-secondary)`; on `.active` primary border + primary text + 12% primary fill) → `toggleCut`.
3. `Camera move` — ONE eyebrow label over `display:flex; gap:6px`: motion select `flex:2` (empty option `—`), amplitude select `flex:1`, speed select `flex:1` (amplitude/speed also keep the `—` empty option — current data can be null).
4. `Cast` — promoted chips row (`display:flex; flex-wrap:wrap; gap:6px; margin-bottom:2px`; chip = pill button 12px, primary index-0 carries `★` at 10px + primary border/text; click → `promoteSubject`) over the membership checklist (`display:flex; flex-wrap:wrap; gap:4px 12px; padding:4px 0`; each label 12px with checkbox → `toggleSubject`); `No subjects defined yet.` hint when the roster is empty.

*Right column* (`flex:0 0 340px; display:flex; flex-direction:column; gap:10px`):
1. `Prompt` — label row: eyebrow label, status hint (`flex:1`), `Unlock` link when locked → `unlockPrompt`, and a 22px outline icon button `pi pi-window-maximize` `title="Open prompt in a larger editor"` → `@click.stop="promptOpen = true"`. Below: 6-row textarea `:value="promptVal" @change="commitPrompt"`.
2. `Candidates` — label row: eyebrow label (`flex:1`), `Re-synth` xs button (`padding:3px 10px; font-size:11px`, disabled `hasActiveJob || store.synthesis.running`) → `resynth`, `Reroll` xs button (disabled `hasActiveJob`, label `Generating…` while active) → `store.generate([beat.id])`. Then the candidates row (`display:flex; gap:10px; overflow-x:auto; padding-bottom:4px`) of 96×96 tiles: `border:2px solid transparent; border-radius:6px; overflow:hidden`; keeper → primary border + 18px primary disc top-LEFT with `✓` (11px, white); hover-revealed 22px scrim circle `pi pi-search-plus` top-right (`@click.stop="viewerIndex = idx"`); tile `@click="onCandidateClick(img)" @dblclick="onCandidateDblClick(idx)"`, `title="candidateTooltip(img)"`. Empty: `No candidates yet.` (12px secondary, `padding:8px 0`).
3. `Sound` — eyebrow label + 2-row textarea, placeholder `ambient, effects, music`, bound `soundVal` handlers.
4. `Dialog` — `display:flex; flex-direction:column; gap:6px; padding-left:8px; border-left:2px solid var(--surface-border)`. Per line: row (`display:flex; gap:6px`) of speaker select `flex:1` (first option `other voice` value `""`, then subjects) → `onDialogSubjectChange`; conditional voice input (`v-if="line.subject_id === null"`, `flex:0 0 64px`, placeholder `voice`) → `onDialogVoiceChange`; delivery input `flex:0 0 84px` placeholder `delivery` → `onDialogDeliveryChange`; language input `flex:0 0 64px` placeholder `language` → `onDialogLanguageChange`; 24px `✕` icon button → `removeDialogLine(i)`. Beneath: 2-row textarea placeholder `spoken line` → `onDialogTextChange`. Then `+ line` dashed xs button → `addDialogLine`. (Voice/language kept per the Deviations section.)

After `</article>`: mount `BeatPromptDialog` (`v-if="promptOpen"` with `:beat`, `:index`, `@close="promptOpen = false"`), `DeleteImagesDialog` (`v-if="pendingDelete"`, title `Delete beat?`, message and `:image-count` as in `BeatForm.vue:475-483`), and `MediaViewer` (`v-if="viewerIndex !== null"`, `:media-list="viewerMedia"`, `:initial-index="viewerIndex"`, `:allow-destructive="false"`, `@close="viewerIndex = null"`).

- [ ] **Step 3: Write the scoped CSS**

Card: `.bc { border:1px solid var(--surface-border); border-radius:8px; background:var(--surface-card); padding:12px 14px 14px; display:flex; flex-direction:column; gap:12px }`; `.bc.selected { border-color:var(--primary-color); box-shadow:0 0 0 1px var(--primary-color) }`. NO shadow when unselected. Icon buttons: `width:22px; height:22px; border:1px solid var(--surface-border); border-radius:5px; background:var(--surface-card); color:var(--text-color-secondary); font-size:11px; cursor:pointer` with `:hover { background:var(--surface-hover) }`, the `✕` variant `:hover { color:var(--danger-color) }`, `:disabled { opacity:.5; cursor:not-allowed }`. Neutral chip: `.bc-chip { font-size:12px; color:var(--text-color-secondary); background:var(--surface-hover); padding:4px 10px; border-radius:999px }`. Eyebrow label, inputs/selects/textarea base styles: copy the blocks from `BeatForm.vue:514-647` verbatim (`.bf-label` → `.bc-label`, the element-selector input/select/textarea block, `textarea { resize:vertical }`, focus = primary border). Candidate tile / check / expand CSS: copy `BeatImages.vue:159-223` (check disc stays top-left per spec). Subject chips/checklist: copy `BeatForm.vue:527-574`. Cut button: copy `BeatForm.vue:649-664`. Dialog editor: adapt `BeatForm.vue:666-737` with the new flex bases (speaker `flex:1`, voice/language `flex:0 0 64px`, delivery `flex:0 0 84px`). Hover reveals `opacity 0→1` over `0.15s`.

- [ ] **Step 4: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/BeatCard.vue
git commit -m "feat(storyboard): BeatCard absorbs BeatRow + BeatForm + BeatImages"
```

### Task 8: ShotHeader

**Files:**
- Create: `frontend/src/components/storyboard/ShotHeader.vue`

**Interfaces:**
- Consumes: `PacingStrip`, `LoraListEditor` (props `{ label, entries }`, emit `change(entries)`), `VideoPromptDialog`, `ShotScriptDialog`, `MediaViewer`, store (`patchPanelFields`, `generate`, `synthesize`, `composeStory`, `compileVideo`, `generateVideo`, `removePanelVideo`, `beatJobState`, `synthesis`, `story`, `compile`, `tree`), `ApiError` from `../../api/client`, `thumbnailUrl`.
- Produces: `ShotHeader` — props `{ panel: Panel; scene: Scene; index: number }`, emit `select-beat(beatId: number)`.

- [ ] **Step 1: Write the script**

Port from `PanelDetail.vue`: `hasActiveGenJob` (L88-90), the action local/snap refs + `syncField` + the `[id, updated_at]` watcher (L104-137, action field only — duration is now the pacing strip's cap label), `reroll`/`resynth` (L139-150), `commitAction` (L156-162), `commitImageLoras`/`commitVideoLoras` (L167-175).

Port from `BeatsEditor.vue`: the re-beat flow — `confirmPending`/`rebeatError` refs and `rebeat(confirm = false)` with its `ApiError` 409 `confirm_required` inspection (L28-47), retargeted at `props.panel.id`.

Port from `PanelVideos.vue`: `tooltip` (L15-17), `onDelete` with its native `confirm()` (L21-27), `viewerIndex`/`viewerMedia` (L29-45).

Add:

```ts
const videoPromptOpen = ref(false)
const scriptOpen = ref(false)

const sourceLabel = computed(() =>
  props.panel.video_prompt_source === 'user'
    ? 'user edited'
    : (props.panel.video_prompt_source ?? 'not compiled'),
)
const lintCount = computed(() => props.panel.video_prompt_warnings.length)
const anchorRelevant = computed(
  () => store.tree?.video_mode === 'i2va' || store.tree?.video_mode === 'fl2va',
)
const recompileSuggested = computed(
  () => anchorRelevant.value && props.panel.video_anchor !== props.panel.video_compiled_anchor,
)
const videoReady = computed(
  () => !!store.tree?.video_target && !!store.tree?.video_preset_id,
)

function compile(): void {
  void store.compileVideo([props.panel.id])
}
async function renderVideo(): Promise<void> {
  const res = await store.generateVideo([props.panel.id])
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}
```

- [ ] **Step 2: Write the template — spec §2a row by row**

Root: `display:flex; flex-direction:column; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--surface-border)`.

**Row 1** (`display:flex; align-items:baseline; gap:10px`): breadcrumb `{{ scene.name }} ›` (11px secondary); `<h3>Shot {{ index + 1 }}</h3>` (18px/600, margin 0); meta `{{ panel.beats.length }} beats` (11px secondary); right group (`margin-left:auto; display:flex; gap:8px`) of three secondary buttons (`padding:5px 12px; font-size:12px`): `Reroll shot` (disabled `hasActiveGenJob || panel.beats.length === 0`) → `reroll`, `Re-synth shot` (disabled `hasActiveGenJob || store.synthesis.running || panel.beats.length === 0`) → `resynth`, `Re-beat shot` (disabled `store.story.running`) → `rebeat()`.

**Re-beat confirm banner** directly under Row 1 (deviation section): port the `confirmPending` / `rebeatError` markup from `BeatsEditor.vue:108-129` verbatim (danger-tinted banner, Continue → `rebeat(true)`, Cancel → `confirmPending = false`; error paragraph otherwise), reusing its CSS (`BeatsEditor.vue:200-220`).

**Row 2**: `<PacingStrip :panel="panel" @select-beat="emit('select-beat', $event)" />`.

**Row 3** (`display:flex; gap:16px; align-items:flex-end`): `Shot action` field (`flex:1; min-width:220px` — eyebrow label + text input `:value="actionVal" @change="commitAction"`); video-prompt group (`flex-shrink:0; display:flex; align-items:center; gap:10px`), rendered only when `store.tree?.video_target`:
- `Video prompt` secondary button (`padding:5px 12px; font-size:12px`), icon `pi pi-file` → `videoPromptOpen = true`
- `Shot script` secondary button (same sizing) → `scriptOpen = true` (decision 1)
- status `{{ sourceLabel }}` (11px secondary)
- `🔒` (11px, `title="Locked against the next compile"`) when `panel.video_prompt_locked === 1`
- warn chip `{{ lintCount }} lint {{ lintCount === 1 ? 'warning' : 'warnings' }}` when `lintCount > 0` (`font-size:12px; padding:4px 10px; border-radius:999px; color:var(--warn); background:color-mix(in srgb, var(--warn) 14%, transparent)`)
- `recompile suggested` chip (same warn-chip style, `title="Anchor changed since the last compile"`) when `recompileSuggested`
- `Compile` secondary sm (disabled `store.compile.running`) → `compile`
- `Render video` PRIMARY sm, icon `pi pi-play`, disabled `!panel.video_prompt || !videoReady` → `renderVideo`

(When the board has no `video_target`, the group collapses to just the `Shot script` button.)

**Row 4** (`display:flex; gap:16px; align-items:flex-start`): `Image LoRAs` wrapper `flex:1; min-width:236px` and `Video LoRAs` wrapper `flex:1; min-width:236px`, each hosting `<LoraListEditor :label :entries @change>` wired to `commitImageLoras`/`commitVideoLoras`; then the `Takes ({{ panel.videos.length }})` field at `flex:0 0 374px` with aside hint `newest last · double-click to play` (11px secondary, only when takes exist). Takes row (`display:flex; gap:10px; overflow-x:auto; padding-bottom:4px`) of tiles at **176×99** (`border-radius:6px; overflow:hidden; background:var(--surface-ground); position:relative`): thumbnail `object-fit:cover`; hover-revealed centred `▶` (20px white over `rgba(0,0,0,0.25)`, `@click.stop="viewerIndex = idx"`) and 20px scrim `×` delete top-right (`@click.stop="onDelete(video)"`), both `opacity 0→1 0.15s`; `@dblclick="viewerIndex = idx"`; `title="tooltip(video)"`. Empty state: `height:99px` box, `1px dashed var(--surface-border); border-radius:6px`, centred `No takes rendered yet.` (12px secondary).

After the root div: `VideoPromptDialog` (`v-if="videoPromptOpen"` `:panel` `:index` `@close`), `ShotScriptDialog` (`v-if="scriptOpen"` `:panel` `:scene` `@close`), `MediaViewer` for takes (`v-if="viewerIndex !== null"`, as in `PanelVideos.vue:73-79`).

- [ ] **Step 3: Scoped CSS**

Secondary button (shared class `.sh-btn`): `padding:5px 12px; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); color:var(--text-color); font-size:12px; cursor:pointer` + hover/disabled per Global Constraints. Primary variant `.sh-btn--primary { background:var(--primary-color); border-color:var(--primary-color); color:#fff }`. Eyebrow label + input base: copy from `PanelDetail.vue:242-268` (drop the `max-width:320px` — the field is `flex:1`). Take tile / play / delete CSS: copy `PanelVideos.vue:104-164` changing width/height to 176/99. Re-beat banner CSS from `BeatsEditor.vue:200-220`.

- [ ] **Step 4: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/ShotHeader.vue
git commit -m "feat(storyboard): ShotHeader with pacing strip, LoRA stacks, takes, video-prompt group"
```

### Task 9: ShotDocument

**Files:**
- Create: `frontend/src/components/storyboard/ShotDocument.vue`

**Interfaces:**
- Consumes: `ShotHeader`, `BeatCard`, store (`selectedPanel`, `selectedScene`, `selectedPanelId`, `selectedBeatId`, `addBeat`, `patchBeatFields`, `panelById`, `tree`).
- Produces: `ShotDocument` — no props; the region-2 scroll container. Exposes nothing.

- [ ] **Step 1: Write the component**

```vue
<script setup lang="ts">
import { computed, ref, watch, type ComponentPublicInstance } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import ShotHeader from './ShotHeader.vue'
import BeatCard from './BeatCard.vue'

const store = useStoryboardStore()
const panel = computed(() => store.selectedPanel)
const scene = computed(() => store.selectedScene)
const shotIndex = computed(() => {
  const s = scene.value
  const p = panel.value
  if (!s || !p) return 0
  return Math.max(0, s.panels.findIndex((x) => x.id === p.id))
})

const scroller = ref<HTMLElement | null>(null)
const cardEls = new Map<number, HTMLElement>()

function registerCard(beatId: number, el: Element | ComponentPublicInstance | null): void {
  if (el && '$el' in (el as ComponentPublicInstance)) {
    cardEls.set(beatId, (el as ComponentPublicInstance).$el as HTMLElement)
  } else if (el instanceof HTMLElement) {
    cardEls.set(beatId, el)
  } else {
    cardEls.delete(beatId)
  }
}

// Selecting a shot from the rail resets the document scroll to 0 (spec
// "Interactions"): watch the panel id rather than coupling to the rail.
watch(
  () => store.selectedPanelId,
  () => {
    if (scroller.value) scroller.value.scrollTop = 0
  },
)

// Pacing-segment click: select the beat and bring its card up. NOT
// scrollIntoView (spec) -- the container owns the offset maths.
function selectBeat(beatId: number): void {
  store.selectedBeatId = beatId
  const el = cardEls.get(beatId)
  const box = scroller.value
  if (el && box) box.scrollTop = Math.max(0, el.offsetTop - box.offsetTop - 12)
}

// Position-based sort_order swap, ported verbatim from BeatsEditor.vue:69-78
// -- stored sort_order values can collide on hand-added beats, so tie cases
// swap by array position instead.
async function moveBeat(index: number, dir: -1 | 1): Promise<void> {
  const beats = panel.value?.beats ?? []
  const otherIndex = index + dir
  if (otherIndex < 0 || otherIndex >= beats.length) return
  const a = beats[index]
  const b = beats[otherIndex]
  const tie = a.sort_order === b.sort_order
  await store.patchBeatFields(a.id, { sort_order: tie ? otherIndex : b.sort_order })
  await store.patchBeatFields(b.id, { sort_order: tie ? index : a.sort_order })
}

// Ported from BeatsEditor.vue:49-60 -- addBeat's refresh() lands the new
// beat last; select it (spec: "Adding selects the new beat").
async function addBeat(): Promise<void> {
  const p = panel.value
  if (!p) return
  const panelId = p.id
  await store.addBeat(panelId, 'new beat')
  const beats = store.panelById(panelId)?.beats ?? []
  const last = beats[beats.length - 1]
  if (last) store.selectedBeatId = last.id
}
</script>

<template>
  <main ref="scroller" class="sd-scroll">
    <div v-if="panel && scene" class="sd-col">
      <ShotHeader :panel="panel" :scene="scene" :index="shotIndex" @select-beat="selectBeat" />
      <BeatCard
        v-for="(b, i) in panel.beats"
        :key="b.id"
        :ref="(el) => registerCard(b.id, el)"
        :beat="b"
        :index="i"
        :subjects="store.tree?.subjects ?? []"
        :selected="b.id === store.selectedBeatId"
        :can-up="i > 0"
        :can-down="i < panel.beats.length - 1"
        @select="store.selectedBeatId = b.id"
        @move="moveBeat(i, $event)"
      />
      <button type="button" class="sd-add" @click="addBeat">+ Beat</button>
    </div>
    <div v-else-if="store.tree && store.tree.scenes.length === 0" class="sd-empty">
      No scenes yet — use <b>Compose</b> to build the board from a premise, or <b>Import text</b>
      to parse a script.
    </div>
    <p v-else class="sd-hint">Select a shot from the outline.</p>
  </main>
</template>

<style scoped>
.sd-scroll {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 40px;
}

.sd-col {
  max-width: 1040px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sd-add {
  align-self: flex-start;
  padding: 6px 14px;
  font-size: 13px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
}

.sd-add:hover {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.sd-empty {
  border: 1px dashed var(--surface-border);
  border-radius: 10px;
  padding: 48px 16px;
  text-align: center;
  font-size: 14px;
  color: var(--text-color-secondary);
}

.sd-hint {
  font-size: 14px;
  color: var(--text-color-secondary);
}
</style>
```

Note on the scroll maths: both the cards and `.sd-scroll` resolve `offsetTop` against the same offset parent (no positioned wrapper sits between them — keep `.sd-col` and the cards `position: static`), so the spec's `el.offsetTop - box.offsetTop - 12` formula holds. Do not add `position: relative` to `.sd-scroll` or `.sd-col`.

- [ ] **Step 2: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/ShotDocument.vue
git commit -m "feat(storyboard): ShotDocument scroll region with beat cards"
```

### Task 10: OutlineRail

**Files:**
- Create: `frontend/src/components/storyboard/OutlineRail.vue`

**Interfaces:**
- Consumes: `JobBadge`, `SceneEditDialog` (props `{ scene: Scene | null }` — null = create; emit `close`), `DeleteImagesDialog`, PrimeVue `Menu` (`import Menu from 'primevue/menu'`), store (`tree`, `selectedSceneId/PanelId/BeatId`, `keeperImage`, `beatJobState`, `panelJobState`, `addPanel`, `removeScene`, `generateVideo`, `panelById`), `thumbnailUrl`, `nextTick`.
- Produces: `OutlineRail` — no props, no emits (writes selection into the store; `ShotDocument` reacts via its watcher).

- [ ] **Step 1: Write the script**

```ts
import { computed, ref, nextTick } from 'vue'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, Panel, Scene } from '../../types/storyboard'
import JobBadge from './JobBadge.vue'
import SceneEditDialog from './SceneEditDialog.vue'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const store = useStoryboardStore()

// Disclosure: collapsed scene ids, local UI state only.
const collapsed = ref(new Set<number>())
function toggleScene(id: number): void {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

function firstBeatKeeperSrc(p: Panel): string | null {
  const beat: Beat | undefined = p.beats[0]
  const img = beat ? store.keeperImage(beat) : null
  return img ? thumbnailUrl(img.file_path) : null
}

function shotSecs(p: Panel): number {
  return p.beats.reduce((s, b) => s + b.duration_s, 0)
}

// A shot row's job badge: the panel's own video job wins, else the first
// beat with an active image job (same collapse as the old panelJobBadge).
function rowJob(p: Panel) {
  const video = store.panelJobState.get(p.id)
  if (video) return video
  for (const b of p.beats) {
    const s = store.beatJobState.get(b.id)
    if (s) return s
  }
  return null
}

// Selecting a shot: scene + panel synchronously; the store's
// watch(selectedPanelId) nulls selectedBeatId on the NEXT flush, so the
// first-beat selection must land after nextTick or the watcher would undo
// it.
async function selectShot(scene: Scene, p: Panel): Promise<void> {
  store.selectedSceneId = scene.id
  store.selectedPanelId = p.id
  await nextTick()
  store.selectedBeatId = p.beats[0]?.id ?? null
}

async function addShot(scene: Scene): Promise<void> {
  await store.addPanel(scene.id, 'new shot')
  const panels = store.tree?.scenes.find((s) => s.id === scene.id)?.panels ?? []
  const last = panels[panels.length - 1]
  if (last) await selectShot(scene, last)
}

// Scene kebab (decision 4): one Menu instance retargeted per scene.
const sceneMenu = ref<InstanceType<typeof Menu> | null>(null)
const menuScene = ref<Scene | null>(null)
const videoReady = computed(
  () => !!store.tree?.video_target && !!store.tree?.video_preset_id,
)
const menuItems = computed<MenuItem[]>(() => {
  const scene = menuScene.value
  if (!scene) return []
  const items: MenuItem[] = []
  if (videoReady.value) {
    items.push({
      label: 'Render scene video',
      icon: 'pi pi-play',
      disabled: scene.panels.length === 0 || scene.panels.some((p) => store.panelJobState.get(p.id) !== undefined),
      command: () => void renderScene(scene),
    })
  }
  items.push({ label: 'Edit scene', icon: 'pi pi-pencil', command: () => openEditor(scene) })
  items.push({
    label: 'Delete scene',
    icon: 'pi pi-trash',
    class: 'or-menu-danger',
    command: () => void onDeleteScene(scene),
  })
  return items
})
function openSceneMenu(scene: Scene, e: Event): void {
  menuScene.value = scene
  sceneMenu.value?.toggle(e)
}

async function renderScene(scene: Scene): Promise<void> {
  const res = await store.generateVideo(scene.panels.map((p) => p.id))
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}

// Scene edit/create + three-way delete: ported from SceneStrip.vue:86-164.
const editorOpen = ref(false)
const editorScene = ref<Scene | null>(null)
function openEditor(scene: Scene): void {
  editorScene.value = scene
  editorOpen.value = true
}
function openCreator(): void {
  editorScene.value = null
  editorOpen.value = true
}

const deleteTarget = ref<Scene | null>(null)
function sceneImageCount(scene: Scene): number {
  return scene.panels.reduce(
    (n, p) => n + p.videos.length + p.beats.reduce((m, b) => m + b.images.length, 0),
    0,
  )
}
async function onDeleteScene(scene: Scene): Promise<void> {
  if (sceneImageCount(scene) === 0) {
    if (!confirm(`Delete scene "${scene.name}"? Its panels are removed too.`)) return
    await store.removeScene(scene.id)
    return
  }
  deleteTarget.value = scene
}
async function confirmDelete(purgeImages: boolean): Promise<void> {
  const scene = deleteTarget.value
  deleteTarget.value = null
  if (!scene) return
  await store.removeScene(scene.id, purgeImages)
}
```

- [ ] **Step 2: Write the template — spec "Region 1 — Outline rail"**

Root `<div class="or-root">` (`padding:10px 8px 24px`). Per scene (`v-for="scene in store.tree?.scenes ?? []"`, block `margin-bottom:10px`):

**Scene header row** (`display:flex; align-items:center; gap:6px; padding:5px 6px`, class gets `or-scene-head` for the hover reveal): caret button (literal `▼`/`▶` by `collapsed.has(scene.id)`, `font-size:10px; color:var(--text-color-secondary)`, bare button) → `toggleScene(scene.id)`; scene name (12px/600, `flex:1; min-width:0`, ellipsised); shot count `{{ scene.panels.length }}` (11px secondary tabular); kebab — an 18px circular corner button with glyph `⋯` (`background:var(--surface-ground); color:var(--text-color-secondary)`; hover 18% primary tint + primary color; revealed on row hover via `opacity 0→1 0.15s`) → `@click.stop="openSceneMenu(scene, $event)"`.

**Shot rows** (`v-if="!collapsed.has(scene.id)"`, `v-for="(p, i) in scene.panels"`): `<button type="button" class="or-shot" :class="{ active: p.id === store.selectedPanelId }" @click="selectShot(scene, p)">` per spec: `display:flex; align-items:center; gap:8px; width:100%; padding:5px 6px; margin-bottom:1px; text-align:left; border:1px solid transparent; border-radius:6px; background:transparent; cursor:pointer; color:var(--text-color); font-family:inherit`; `.active { border-color:var(--primary-color); background:color-mix(in srgb, var(--primary-color) 10%, transparent) }`. Inside: thumb span (`position:relative; flex-shrink:0; width:34px; height:34px; border-radius:4px; overflow:hidden; background:var(--surface-ground)`) with keeper `<img>` (`object-fit:cover`) or a dashed-border empty box, plus `<JobBadge v-if="rowJob(p)" :state="rowJob(p)!.state" :error="rowJob(p)!.error" />`; text span (`flex:1; min-width:0`) with line 1 `{{ i + 1 }}. {{ p.action }}` (12px, ellipsised) and line 2 (10px secondary tabular) `{{ p.beats.length }} beats · {{ shotSecs(p).toFixed(1) }}s` + `<template v-if="p.videos.length"> · 🎬{{ p.videos.length }}</template>`.

**`+ Shot`** dashed button after the rows (`padding:3px 10px; font-size:11px; border:1px dashed var(--surface-border); border-radius:5px; background:none; color:var(--text-color-secondary); margin:4px 0 0 6px`) → `addShot(scene)`. **`+ Scene`** same style (`margin-left:6px`) after the last scene block → `openCreator()`.

After the loop: `<Menu ref="sceneMenu" :model="menuItems" :popup="true" />`; `SceneEditDialog` (`v-if="editorOpen"` `:key="editorScene?.id ?? 'new'"` `:scene="editorScene"` `@close="editorOpen = false"`); `DeleteImagesDialog` (`v-if="deleteTarget"`, title/message/count as `SceneStrip.vue:63-71`).

Danger menu item styling: global (non-scoped) rule in this SFC — `.or-menu-danger .p-menu-item-label, .or-menu-danger .p-menu-item-icon { color: var(--danger-color) }` inside an unscoped `<style>` block (PrimeVue teleports the menu to body, scoped styles cannot reach it).

- [ ] **Step 3: Type-check and commit**

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
git add frontend/src/components/storyboard/OutlineRail.vue
git commit -m "feat(storyboard): OutlineRail with scene kebab and shot rows"
```

### Task 11: StoryboardView rewrite + delete the replaced components

**Files:**
- Modify: `frontend/src/views/StoryboardView.vue` (full rewrite of the editor branch)
- Delete: `frontend/src/components/storyboard/SceneStrip.vue`, `PanelGrid.vue`, `PanelSidePanel.vue`, `BeatRow.vue`, `PanelDetail.vue`, `BeatsEditor.vue`, `BeatForm.vue`, `BeatImages.vue`, `PanelVideos.vue`

**Interfaces:**
- Consumes: `OutlineRail`, `ShotDocument`, PrimeVue `Button` (already global) + `Menu` (local import), existing dialogs (`ImportTextDialog`, `OutlineDialog`, `StoryboardSettingsDialog`), store.

- [ ] **Step 1: Rewrite the template's editor branch**

Keep unchanged: the mobile note, the landing branch, the not-found branch, `notFound`/`videoLabel` computeds, `store.attachWs()` placement, the immediate `watch(() => props.id, ...)` loader, and `onGenerateVideo` (lines 254-259). Delete the divider machinery: `savedDetailHeight` module script block, `detailHeight`, `detailStyle`, `startDetailDrag`, and their CSS.

New `sb-root` structure:

```html
<div v-else class="sb-root">
  <header class="sb-header">
    <RouterLink to="/" class="sb-back">← Library</RouterLink>
    <h2>{{ store.tree?.name }}</h2>
    <!-- KEEP the existing chip run verbatim (video chip, synthesis /
         compose / compile progress+error chips, dismissible store.error
         chip) — lines 18-65 of the current file, unchanged. -->
    <div class="sb-actions">
      <Button label="Compose" icon="pi pi-sparkles" text :disabled="!store.tree" @click="composeOpen = true" />
      <Button label="Generate all" icon="pi pi-play" :disabled="store.loading || !store.tree" @click="store.generate()" />
      <Button icon="pi pi-ellipsis-h" text rounded aria-label="More actions"
        title="Import text, synthesize, compile, render, cancel"
        :disabled="!store.tree" @click="overflowMenu?.toggle($event)" />
      <Button icon="pi pi-cog" text rounded aria-label="Storyboard settings"
        title="Storyboard settings" :disabled="!store.tree" @click="settingsOpen = true" />
      <Menu ref="overflowMenu" :model="overflowItems" :popup="true" />
    </div>
  </header>

  <div v-if="store.tree" class="sb-body">
    <aside class="sb-rail"><OutlineRail /></aside>
    <ShotDocument />
  </div>
  <div v-else-if="store.loading" class="sb-loading">Loading…</div>
</div>
```

Header CSS change: `padding: 10px 20px; gap: 12px` (spec Shell). `.sb-rail { flex: 0 0 270px; min-height: 0; overflow-y: auto; border-right: 1px solid var(--surface-border) }`. `.sb-body { display:flex; flex:1; min-height:0 }` stays. Keep `.sb-chip*`, `.sb-back`, `.sb-loading`, `.storyboard-shell` CSS; drop `.sb-main`, `.sb-side`, `.sb-divider`, `.sb-detail-wrap`.

- [ ] **Step 2: Add the overflow menu model**

```ts
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'

const overflowMenu = ref<InstanceType<typeof Menu> | null>(null)
const stageBusy = computed(
  () => store.compile.running || store.synthesis.running || store.story.running,
)
const overflowItems = computed<MenuItem[]>(() => {
  const items: MenuItem[] = [
    { label: 'Import text', icon: 'pi pi-file-import', disabled: !store.tree, command: () => (importOpen.value = true) },
    { label: 'Synthesize', icon: 'pi pi-sparkles', disabled: store.loading || !store.tree, command: () => void store.synthesize() },
  ]
  if (store.tree?.video_target) {
    items.push({ label: 'Compile video prompts', icon: 'pi pi-file', disabled: stageBusy.value, command: () => void store.compileVideo() })
  }
  if (store.tree?.video_target && store.tree?.video_preset_id) {
    items.push({ label: 'Generate video', icon: 'pi pi-video', disabled: stageBusy.value, command: () => void onGenerateVideo() })
  }
  items.push({ separator: true })
  items.push({ label: 'Cancel', icon: 'pi pi-times', class: 'sb-menu-danger', disabled: !store.tree, command: () => void store.cancelAll() })
  return items
})
```

Unscoped `<style>` block for the teleported menu item: `.sb-menu-danger .p-menu-item-label, .sb-menu-danger .p-menu-item-icon { color: var(--danger-color) }`.

Imports: remove `SceneStrip`, `PanelGrid`, `PanelDetail`, `PanelSidePanel`; add `OutlineRail`, `ShotDocument`, `Menu`.

- [ ] **Step 3: Delete the replaced components and check for stale references**

```bash
git rm frontend/src/components/storyboard/{SceneStrip,PanelGrid,PanelSidePanel,BeatRow,PanelDetail,BeatsEditor,BeatForm,BeatImages,PanelVideos}.vue
grep -rn "SceneStrip\|PanelGrid\|PanelSidePanel\|BeatRow\|PanelDetail\|BeatsEditor\|BeatForm\|BeatImages\|PanelVideos" frontend/src --include=*.vue --include=*.ts
```

Expected: no hits outside comments. If `utils/shotScript.ts` or any store comment references them, leave comments alone (they document history), but any live import is a bug to fix.

- [ ] **Step 4: Type-check, build, commit**

```bash
cd frontend && npx vue-tsc --noEmit && npm run build && cd ..
git add -A frontend/src
git commit -m "feat(storyboard): two-region workspace layout — outline rail + shot document"
```

### Task 12: Verification pass (live app, both themes)

**Files:** none (fixes fold back into the components above)

- [ ] **Step 1: Full quality gate**

```bash
make quality test          # backend parity — untouched, must stay green
cd frontend && npm run build
```

(Known WSL2 flake: `test_file_watcher_triggers_reload` can fail in full-suite runs — rerun in isolation before treating it as a regression.)

- [ ] **Step 2: Run the app and walk the spec's interaction table**

```bash
source venv/bin/activate && python run_server.py   # terminal 1 (or background)
cd frontend && npm run dev                          # terminal 2
```

Open `http://localhost:5173/#/storyboard/<id>` for an existing board (list ids via `curl -s localhost:8700/api/storyboard | head`). Verify each row of the spec's "Interactions & behaviour" table, plus:

- Outline rail: shot select → first beat selected, document scrolled to top; collapse caret; kebab (render/edit/delete); `+ Shot` / `+ Scene`.
- Pacing strip: segment widths proportional; duration labels suppressed below 44px and present in `title`; cut edge; unused-budget block; cap bar flips to warn when over; segment click scrolls the beat card to ~12px below the header.
- Beat card: every field commits on change and survives a WS refresh mid-edit (type in Action, don't blur, trigger a re-synth of another beat → your text must survive); reorder at the ends disabled; delete flows (plain confirm imageless, three-way with images); keeper toggle vs double-click viewer; prompt maximize dialog draft semantics (Cancel discards, Save locks).
- Shot header: Reroll/Re-synth/Re-beat (incl. 409 confirm banner when beats have images/locked prompts); Video prompt dialog (Compile/Copy/Render, warnings list, anchor select on an i2va board, unlock); Shot script dialog (block click selects beat, Copy); recompile-suggested chip after changing the anchor; takes hover play/delete, double-click viewer.
- Header: ⋯ menu items behave and disable correctly; Cancel is red; progress chips still appear during synthesize/compose/compile.
- Empty board (create a fresh one): dashed empty state + rail shows `+ Scene` only.
- **Both themes:** toggle OS `prefers-color-scheme` (or DevTools emulation) — dark is canonical, review dark first, then light.
- Landing and mobile note unchanged.

- [ ] **Step 3: Fix anything found, re-run step 1, commit**

```bash
git add -A && git commit -m "fix(storyboard): verification-pass fixes for workspace redesign"
```

---

## Self-review notes (already applied)

- Spec coverage checked section by section: Shell → Task 11; Outline rail → Task 10; Shot header rows 1–4 → Tasks 6/8; Beat card → Task 7; Dialogs → Tasks 3/4/5; Job states → Task 2 (fill) + Task 7 (chip); Interactions table → Tasks 7–11 + Task 12 walkthrough; State management (removed local state: side-panel tab, divider heights, video-prompt snapshot pair) → Task 11; Design tokens → Task 1; open decisions 1–5 → "Resolved design decisions".
- Type consistency: `select-beat(beatId: number)` is the emit name on both PacingStrip and ShotHeader; `move(dir: -1 | 1)` on BeatCard matches ShotDocument's `moveBeat(i, $event)`; dialog props/emits match their mount sites.
- The store's `watch(selectedPanelId) → selectedBeatId = null` flush-timing hazard is handled in exactly one place (OutlineRail.selectShot's `nextTick`); ShotDocument's beat selections never change the panel id so they are unaffected.
