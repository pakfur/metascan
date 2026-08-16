<script setup lang="ts">
import { computed } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { VIDEO_TARGET_CAPS, DEFAULT_SHOT_CAP, type Panel } from '../../types/storyboard'
import BeatRow from './BeatRow.vue'

const props = defineProps<{ panel: Panel }>()
const store = useStoryboardStore()

const total = computed(() => props.panel.beats.reduce((s, b) => s + b.duration_s, 0))
const overShot = computed(() => total.value > props.panel.duration_s + 0.5)
const shotCap = computed(
  () => VIDEO_TARGET_CAPS[store.tree?.video_target ?? ''] ?? DEFAULT_SHOT_CAP,
)
const overH3 = computed(() => total.value > shotCap.value)
const composing = computed(() => store.story.running)

async function rebeat(): Promise<void> {
  await store.composeStory({ stages: ['beats'], panel_ids: [props.panel.id] })
}

async function add(): Promise<void> {
  const panelId = props.panel.id
  await store.addBeat(panelId, 'new beat')
  // addBeat() awaits refresh(), which reassigns store.tree.value
  // synchronously before its promise resolves -- reading through the store
  // (rather than props.panel, whose update depends on Vue's async render
  // scheduling of the parent) is guaranteed fresh here. The newly added beat
  // is always last (its sort_order was set to the pre-add beat count).
  const beats = store.panelById(panelId)?.beats ?? []
  const last = beats[beats.length - 1]
  if (last) store.selectedBeatId = last.id
}

// Swap sort_order for the beats at `index` and `index + dir` (adjacent
// positions in the displayed, already sort_order-sorted array). Computing
// the swap from array positions rather than trusting the stored values to
// already be a contiguous/distinct sequence keeps this correct even for
// hand-added beats whose sort_order may collide with a neighbor's -- in
// that case position indices are used instead of the (identical) stored
// values, so the write always actually changes the order.
async function moveBeat(index: number, dir: -1 | 1): Promise<void> {
  const beats = props.panel.beats
  const otherIndex = index + dir
  if (otherIndex < 0 || otherIndex >= beats.length) return
  const a = beats[index]
  const b = beats[otherIndex]
  const tie = a.sort_order === b.sort_order
  await store.patchBeatFields(a.id, { sort_order: tie ? otherIndex : b.sort_order })
  await store.patchBeatFields(b.id, { sort_order: tie ? index : a.sort_order })
}
</script>

<template>
  <div class="pd-field beats-editor">
    <label class="pd-label">
      Beats — {{ total.toFixed(1) }}s / {{ panel.duration_s }}s
      <span v-if="overH3" class="beats-warn">exceeds H3 15s clip cap</span>
      <span v-else-if="overShot" class="beats-warn">exceeds shot duration</span>
    </label>
    <div class="beats-list">
      <BeatRow
        v-for="(b, i) in panel.beats"
        :key="b.id"
        :beat="b"
        :subjects="store.tree?.subjects ?? []"
        :index="i"
        :can-move-up="i > 0"
        :can-move-down="i < panel.beats.length - 1"
        @move-up="moveBeat(i, -1)"
        @move-down="moveBeat(i, 1)"
      />
      <span v-if="panel.beats.length === 0" class="pd-hint">No beats yet.</span>
    </div>
    <div class="beats-actions">
      <button type="button" class="pd-btn" @click="add">+ Beat</button>
      <button type="button" class="pd-btn" :disabled="composing" @click="rebeat">
        Re-beat shot
      </button>
    </div>
  </div>
</template>

<style scoped>
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

.pd-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beats-warn {
  color: var(--warn, #e0a030);
  margin-left: 0.5rem;
  text-transform: none;
  font-weight: 600;
}

.beats-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.beats-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
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
</style>
