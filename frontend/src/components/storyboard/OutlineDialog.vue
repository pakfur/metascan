<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch, type Ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { ApiError } from '../../api/client'
import TextEditPopup from './TextEditPopup.vue'
import {
  COMPOSE_STAGES,
  STORY_SCALES,
  STORY_SCALE_LABELS,
  type ComposeStage,
} from '../../types/storyboard'

const STAGE_LABEL: Record<ComposeStage, string> = {
  outline: 'Outline',
  scenes: 'Scenes',
  shots: 'Shots',
  beats: 'Beats',
}

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const store = useStoryboardStore()

// Local editable copies of the premise/outline fields, each paired with a
// "last synced from server" snapshot -- mirrors PanelDetail.vue's
// commit-on-change pattern (see CLAUDE.md "Detail editors with local
// commit-on-change copies must resync on id + updated_at"). The resync
// watcher below only overwrites a field whose local value still equals its
// snapshot (no pending edit); `run()` updates both together on save so the
// round trip doesn't get mistaken for a foreign change. Without this, a
// background store.tree replacement unrelated to this dialog (refresh()
// runs from several WS handlers -- synthesis_complete, beat_images_changed,
// story_stage_complete, story_complete) would silently discard whatever the
// user was mid-typing.
const premise = ref('')
const premiseSnap = ref('')
const outlineText = ref('') // pretty-printed JSON, directly editable
const outlineSnap = ref('')
const confirmPending = ref<ComposeStage[] | null>(null)
const error = ref<string | null>(null)

function syncField(local: Ref<string>, snap: Ref<string>, serverVal: string): void {
  if (local.value === snap.value) {
    local.value = serverVal
    snap.value = serverVal
  }
}

// One checkbox per COMPOSE_STAGES entry. Defaults to scenes+shots+beats
// checked the first time an outline exists (spec §6: partial re-runs) --
// `stagesInitialized` makes that a one-shot default so a user who
// deliberately unchecks everything doesn't get overridden on the next
// outline watcher tick.
const stageChecks = reactive<Record<ComposeStage, boolean>>({
  outline: false,
  scenes: false,
  shots: false,
  beats: false,
})
let stagesInitialized = false

// Keyed on tree id + updated_at (not on `store.tree.outline` directly) so it
// resyncs on a board switch AND on any server-side rewrite of the current
// board, but does not treat every `tree.value` reassignment as a reason to
// stomp an in-progress edit -- see the field comment above.
watch(
  () => [props.open, store.tree?.id, store.tree?.updated_at],
  () => {
    if (!props.open || !store.tree) return
    syncField(premise, premiseSnap, store.tree.source_text ?? '')
    let prettyOutline: string
    try {
      prettyOutline = store.tree.outline
        ? JSON.stringify(JSON.parse(store.tree.outline), null, 2)
        : ''
    } catch {
      prettyOutline = store.tree.outline ?? ''
    }
    syncField(outlineText, outlineSnap, prettyOutline)
    if (store.tree.outline && !stagesInitialized) {
      stageChecks.scenes = true
      stageChecks.shots = true
      stageChecks.beats = true
      stagesInitialized = true
    }
  },
  { immediate: true },
)

const hasOutline = computed(() => !!store.tree?.outline)

// Structured view of the outline, parsed from the local editable copy
// (outlineText) rather than store.tree.outline directly -- keeps the raw
// textarea and the structured arc list as one source of truth so an edit
// in either view is reflected in the other without an extra round trip.
interface ArcEntry {
  beat: string
  summary: string
}
const parsedOutline = computed(() => {
  try {
    const o = JSON.parse(outlineText.value || '{}')
    return {
      logline: String(o.logline ?? ''),
      tone: String(o.tone ?? ''),
      pacing: String(o.pacing ?? ''),
      duration: Number(o.duration_target_s ?? 0),
      arc: (Array.isArray(o.arc) ? o.arc : []) as ArcEntry[],
    }
  } catch {
    return null
  }
})
const showRaw = ref(false)

// Structured edits patch the parsed outline and write it back to
// outlineText -- the same local ref the raw textarea binds to and that
// run() PATCHes before any non-outline stage.
function patchOutline(mutate: (o: Record<string, unknown>) => void): void {
  try {
    const o = JSON.parse(outlineText.value)
    mutate(o)
    outlineText.value = JSON.stringify(o, null, 2)
  } catch {
    // outlineText isn't valid JSON right now (mid hand-edit in raw mode) --
    // nothing sane to patch, leave it alone.
  }
}

function editLogline(text: string): void {
  patchOutline((o) => {
    o.logline = text
  })
}

function editArcSummary(i: number, text: string): void {
  patchOutline((o) => {
    const arc = o.arc as { summary: string }[]
    if (arc[i]) arc[i].summary = text
  })
}

onMounted(() => {
  void store.loadTemplates()
})

function onSceneTemplate(sceneId: number, e: Event): void {
  const val = (e.target as HTMLSelectElement).value || null
  void store.patchSceneFields(sceneId, { template_id: val })
}

const hasTemplateProblems = computed(() =>
  (store.tree?.scenes ?? []).some((s) => (s.template_problems ?? []).length > 0),
)

// Mirrors the backend gate (`check_compose_gates`): the template selection
// check is skipped when the scenes stage runs too, because that stage
// recreates every scene row with template_id NULL and the selections being
// validated are about to be discarded. Blocking `shots` regardless would
// silently drop it from the default scenes+shots+beats run and leave the
// rebuilt board with no shots at all.
const shotsBlocked = computed(() => hasTemplateProblems.value && !stageChecks.scenes)

const checkedStages = computed<ComposeStage[]>(() =>
  COMPOSE_STAGES.filter((s) => stageChecks[s] && !(s === 'shots' && shotsBlocked.value)),
)

async function run(stages: ComposeStage[], confirm = false): Promise<void> {
  if (stages.length === 0) return
  error.value = null
  confirmPending.value = null
  try {
    if (premise.value !== (store.tree?.source_text ?? '')) {
      await store.patchStoryboardFields({ source_text: premise.value })
      premiseSnap.value = premise.value
    }
    if (outlineText.value && stages[0] !== 'outline') {
      await store.patchStoryboardFields({ outline: outlineText.value })
      outlineSnap.value = outlineText.value
    }
    await store.composeStory({ stages, confirm })
    confirmPending.value = null
  } catch (e: unknown) {
    if (
      e instanceof ApiError &&
      e.status === 409 &&
      (e.detail as { code?: string } | undefined)?.code === 'confirm_required'
    ) {
      confirmPending.value = stages
    } else {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }
}

// Story length is persisted immediately (like a settings field, one
// source of truth on the storyboard) so the very next compose run —
// including a confirm retry — picks it up. The select is fully
// controlled from store.tree; a select has no in-progress edit state to
// protect, so no local/snapshot pair is needed.
function onScaleChange(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  if (val === store.tree?.story_scale) return
  void store.patchStoryboardFields({ story_scale: val })
}

function close(): void {
  if (store.story.running) return
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close">
    <div class="dialog-card">
      <h3>Compose story</h3>
      <p class="hint">
        Write a premise, generate an outline, then build scenes, shots and beats from it.
      </p>

      <p v-if="store.tree?.video_target" class="hint video-echo">
        Video: MiniMax H3 · {{ store.tree.video_mode || 'ref2va' }} (change in Settings)
      </p>

      <label class="field-label" for="od-scale">Story length</label>
      <select
        id="od-scale"
        class="scale-select"
        :value="store.tree?.story_scale ?? 'standard'"
        :disabled="store.story.running"
        @change="onScaleChange"
      >
        <option v-for="s in STORY_SCALES" :key="s" :value="s">
          {{ STORY_SCALE_LABELS[s] }}
        </option>
      </select>

      <label class="field-label">Premise</label>
      <textarea
        v-model="premise"
        rows="6"
        class="import-textarea"
        placeholder="A one- or two-paragraph premise for the story…"
        :disabled="store.story.running"
      />

      <div class="dialog-actions">
        <button
          class="btn-primary"
          :disabled="store.story.running || !premise.trim()"
          @click="run(['outline'])"
        >
          Generate outline
        </button>
      </div>

      <template v-if="hasOutline">
        <label class="field-label"
          >Outline
          <button type="button" class="link" @click="showRaw = !showRaw">
            {{ showRaw ? 'structured' : 'raw JSON' }}
          </button>
        </label>
        <textarea
          v-if="showRaw || !parsedOutline"
          v-model="outlineText"
          rows="10"
          class="import-textarea outline-textarea"
          :disabled="store.story.running"
        />
        <div v-else class="outline-view">
          <TextEditPopup title="Logline" :value="parsedOutline.logline" @save="editLogline($event)">
            <input
              class="logline"
              :value="parsedOutline.logline"
              :disabled="store.story.running"
              placeholder="Logline"
              @change="editLogline(($event.target as HTMLInputElement).value)"
            />
          </TextEditPopup>
          <p class="meta">{{ parsedOutline.tone }} · {{ parsedOutline.pacing }} · {{ parsedOutline.duration }}s</p>
          <ol class="arc">
            <li v-for="(e, i) in parsedOutline.arc" :key="i">
              <span class="arc-chip">{{ e.beat }}</span>
              <TextEditPopup :title="`Arc: ${e.beat}`" :value="e.summary" @save="editArcSummary(i, $event)">
                <input
                  :value="e.summary"
                  :disabled="store.story.running"
                  @change="editArcSummary(i, ($event.target as HTMLInputElement).value)"
                />
              </TextEditPopup>
            </li>
          </ol>
        </div>

        <template v-if="store.tree?.scenes.length">
          <label class="field-label">Scenes</label>
          <table class="scene-table">
            <tr v-for="s in store.tree.scenes" :key="s.id">
              <td class="name">{{ s.name }}<div class="brief">{{ s.brief }}</div></td>
              <td><span v-for="b in s.arc_beats" :key="b" class="arc-chip">{{ b }}</span></td>
              <td class="charge" v-if="s.charge_in !== null">{{ s.charge_in }} → {{ s.charge_out }}</td>
              <td v-else />
              <td>
                <select
                  :value="s.template_id ?? ''"
                  :disabled="store.story.running"
                  @change="onSceneTemplate(s.id, $event)"
                >
                  <option value="">Free-form</option>
                  <option v-for="t in store.templates" :key="t.id" :value="t.id">
                    {{ t.function === s.function ? '✓ ' : '' }}{{ t.id }}
                  </option>
                </select>
                <p v-for="m in s.template_problems ?? []" :key="m" class="error inline">{{ m }}</p>
                <p v-for="m in s.template_warnings ?? []" :key="m" class="warn inline">{{ m }}</p>
                <p v-if="s.outline_stale" class="warn inline" title="Outline changed since this scene was built">
                  ↻ Outline changed since this scene was built
                </p>
              </td>
            </tr>
          </table>
        </template>

        <label class="field-label">Stages to build</label>
        <div class="stage-picker">
          <label v-for="stage in COMPOSE_STAGES" :key="stage" class="stage-checkbox">
            <input
              type="checkbox"
              v-model="stageChecks[stage]"
              :disabled="store.story.running || (stage === 'shots' && shotsBlocked)"
            />
            {{ STAGE_LABEL[stage] }}
          </label>
        </div>
        <p v-if="shotsBlocked" class="error">Fix the template problems above before building shots.</p>

        <div class="dialog-actions">
          <button
            class="btn-primary"
            :disabled="store.story.running || checkedStages.length === 0"
            @click="run(checkedStages)"
          >
            Build checked stages
          </button>
        </div>
      </template>

      <p v-if="confirmPending" class="warn">
        This replaces existing content and deletes its generated images and clips (moved to the
        OS trash) — continue?
        <span class="confirm-actions">
          <button class="btn-danger" :disabled="store.story.running" @click="run(confirmPending!, true)">
            Continue
          </button>
          <button class="btn-secondary" :disabled="store.story.running" @click="confirmPending = null">
            Cancel
          </button>
        </span>
      </p>
      <p v-else-if="error" class="error">{{ error }}</p>

      <p v-if="store.story.running" class="busy-line">
        <span class="pi pi-spin pi-spinner" />
        Composing {{ store.story.stage }} {{ store.story.done }}/{{ store.story.total }}
      </p>

      <div class="dialog-actions">
        <button class="btn-secondary" :disabled="store.story.running" @click="close">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 640px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--text-color);
}

.hint {
  margin: 0 0 14px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.field-label {
  display: block;
  margin: 14px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

.scale-select {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
}

.scale-select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.scale-select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.import-textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
}

.outline-textarea {
  font-family: var(--font-mono);
  font-size: 12px;
}

.link {
  float: right;
  border: none;
  background: none;
  padding: 0;
  color: inherit;
  text-decoration: underline;
  font-size: 11px;
  cursor: pointer;
}

.outline-view {
  padding: 8px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
}

.logline {
  width: 100%;
  box-sizing: border-box;
  margin: 0;
  padding: 4px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  font-size: 13px;
  font-family: inherit;
  color: var(--text-color);
}

.meta {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.arc {
  padding-left: 18px;
}

.arc li {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 4px 0;
}

.arc li > :deep(.tep) {
  flex: 1;
  min-width: 0;
}

.arc input {
  flex: 1;
  box-sizing: border-box;
  padding: 4px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 12px;
  font-family: inherit;
}

.scene-table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.scene-table td {
  padding: 6px 4px;
  vertical-align: top;
  border-top: 1px solid var(--surface-border);
}

.scene-table .brief {
  color: var(--text-color-secondary);
}

.arc-chip {
  display: inline-block;
  padding: 0 6px;
  margin-right: 4px;
  border-radius: 8px;
  background: var(--surface-border);
  font-size: 11px;
}

.import-textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

.import-textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.stage-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.stage-checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
}

.warn {
  margin: 10px 0 0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-color);
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--danger-color, #e53e3e) 40%, transparent);
}

.warn.inline {
  color: var(--yellow-500, #d4a017);
  font-size: 12px;
  margin: 4px 0 0;
  padding: 0;
  border: none;
  background: none;
}

.confirm-actions {
  display: inline-flex;
  gap: 8px;
  margin-left: 10px;
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 10px 0 0;
}

.error.inline {
  font-size: 12px;
  margin: 4px 0 0;
}

.busy-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-danger {
  padding: 8px 20px;
  background: var(--danger-color, #e53e3e);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-danger:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
