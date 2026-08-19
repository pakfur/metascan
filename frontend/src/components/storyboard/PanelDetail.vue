<template>
  <div v-if="panel" class="panel-detail">
    <div class="pd-header">
      <h4>Panel {{ (panel.sort_order ?? 0) + 1 }}</h4>
      <div class="pd-header-actions">
        <button
          class="pd-btn"
          :disabled="hasActiveGenJob || panel.beats.length === 0"
          @click="reroll"
        >
          Reroll shot
        </button>
        <button
          class="pd-btn"
          :disabled="hasActiveGenJob || store.synthesis.running || panel.beats.length === 0"
          @click="resynth"
        >
          Re-synth shot
        </button>
      </div>
    </div>

    <div class="pd-body">
      <div class="pd-field">
        <label class="pd-label">Action</label>
        <input type="text" :value="actionVal" @change="commitAction" />
      </div>

      <div class="pd-field">
        <label class="pd-label">Duration (s)</label>
        <input
          type="number"
          step="0.5"
          min="0.5"
          :value="durationVal"
          @change="commitDuration"
        />
      </div>

      <div class="pd-field">
        <LoraListEditor
          label="Image LoRAs"
          :entries="panel.image_loras"
          @change="commitImageLoras"
        />
      </div>

      <!-- Per-beat framing/subjects/prompt/candidates now live in BeatForm.vue
           (PanelSidePanel's Edit tab) -- a beat is the image-generation unit
           since the shot->beat reorg. "Reroll shot" / "Re-synth shot" above
           are the panel-scoped bulk actions across every beat below; each
           beat also has its own Reroll/Re-synth in BeatForm for one-off
           regeneration. -->
      <BeatsEditor :panel="panel" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, type Ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { LoraEntry } from '../../types/storyboard'
import BeatsEditor from './BeatsEditor.vue'
import LoraListEditor from './LoraListEditor.vue'

const store = useStoryboardStore()
const panel = computed(() => store.selectedPanel)

// Any beat of this panel with an active image-generation job -- gates the
// panel-scoped Reroll/Re-synth bulk actions above.
const hasActiveGenJob = computed(() =>
  panel.value ? panel.value.beats.some((b) => store.beatJobState.has(b.id)) : false,
)

// Local editable copies of the "commit on change" text/number fields --
// Panel keeps its own action/duration_s (the shot's overall action summary
// and target duration; each beat has its own action/duration_s too, which
// must sum to roughly this value -- see BeatsEditor's total/cap warning).
//
// Each field also carries a "last synced from server" snapshot -- commit*
// functions update local + snapshot together, so a field with no pending
// edit always has local === snapshot. The watcher further down uses that
// equality to decide, on every server-driven refresh, whether it's safe to
// overwrite a field: adopt the new server value when local === snapshot (no
// pending edit), leave it alone otherwise (an uncommitted PATCH is in
// flight for that field).
const actionVal = ref('')
const durationVal = ref('0')

const actionSnap = ref('')
const durationSnap = ref('0')

function syncField(local: Ref<string>, snap: Ref<string>, serverVal: string): void {
  if (local.value === snap.value) {
    local.value = serverVal
    snap.value = serverVal
  }
}

// Resyncs on a panel switch (by id) AND on any server-side rewrite of the
// CURRENTLY selected panel (detected via updated_at -- bumped on every
// successful PATCH, including edits from another tab).
watch(
  () => [panel.value?.id, panel.value?.updated_at],
  () => {
    const p = panel.value
    if (!p) return
    syncField(actionVal, actionSnap, p.action ?? '')
    syncField(durationVal, durationSnap, String(p.duration_s))
  },
  { immediate: true },
)

function reroll(): void {
  if (!panel.value || panel.value.beats.length === 0) return
  void store.generate(panel.value.beats.map((b) => b.id))
}

function resynth(): void {
  if (!panel.value || panel.value.beats.length === 0) return
  void store.synthesize(
    panel.value.beats.map((b) => b.id),
    true,
  )
}

// Every commit* handler updates its local ref AND snapshot together
// (before the optimistic patch resolves) so the field is never mistaken
// for "someone else's pending edit" by the resync watcher above once the
// round trip's updated_at bump comes back through.
function commitAction(e: Event): void {
  const val = (e.target as HTMLInputElement).value
  actionVal.value = val
  actionSnap.value = val
  if (!panel.value || val === panel.value.action) return
  void store.patchPanelFields(panel.value.id, { action: val })
}

// LoraListEditor rows are fully controlled + commit-on-change, so unlike
// the text fields above there is no local copy to snapshot -- every change
// event already carries the complete next list.
function commitImageLoras(entries: LoraEntry[]): void {
  if (!panel.value) return
  void store.patchPanelFields(panel.value.id, { image_loras: entries })
}

function commitDuration(e: Event): void {
  const val = (e.target as HTMLInputElement).value
  durationVal.value = val
  durationSnap.value = val
  if (!panel.value) return
  const next = Math.max(0.5, Number(val) || panel.value.duration_s)
  if (next === panel.value.duration_s) return
  void store.patchPanelFields(panel.value.id, { duration_s: next })
}
</script>

<style scoped>
.panel-detail {
  flex-shrink: 0;
  max-height: 44vh;
  overflow-y: auto;
  border-top: 1px solid var(--surface-border);
  background: var(--surface-card);
  padding: 12px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pd-header h4 {
  margin: 0;
  font-size: 14px;
  color: var(--text-color);
}

.pd-header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.pd-btn {
  padding: 5px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.pd-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.pd-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pd-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pd-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pd-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

input[type='text'],
input[type='number'] {
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
  max-width: 320px;
}

input[type='text']:focus,
input[type='number']:focus {
  outline: none;
  border-color: var(--primary-color);
}
</style>
