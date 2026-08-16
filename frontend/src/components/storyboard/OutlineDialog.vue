<script setup lang="ts">
import { computed, reactive, ref, watch, type Ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { ApiError } from '../../api/client'
import { COMPOSE_STAGES, type ComposeStage } from '../../types/storyboard'

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
// runs from several WS handlers -- synthesis_complete, panel_images_changed,
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

const checkedStages = computed<ComposeStage[]>(() =>
  COMPOSE_STAGES.filter((s) => stageChecks[s]),
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
        <label class="field-label">Outline</label>
        <textarea
          v-model="outlineText"
          rows="10"
          class="import-textarea outline-textarea"
          :disabled="store.story.running"
        />

        <label class="field-label">Stages to build</label>
        <div class="stage-picker">
          <label v-for="stage in COMPOSE_STAGES" :key="stage" class="stage-checkbox">
            <input
              type="checkbox"
              v-model="stageChecks[stage]"
              :disabled="store.story.running"
            />
            {{ STAGE_LABEL[stage] }}
          </label>
        </div>

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
        This replaces existing content — continue?
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
  font-family: var(--font-family-mono, ui-monospace, monospace);
  font-size: 12px;
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
