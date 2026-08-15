<script setup lang="ts">
import { computed } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel } from '../../types/storyboard'
import BeatRow from './BeatRow.vue'

const props = defineProps<{ panel: Panel }>()
const store = useStoryboardStore()

const total = computed(() => props.panel.beats.reduce((s, b) => s + b.duration_s, 0))
const overShot = computed(() => total.value > props.panel.duration_s + 0.5)
const overH3 = computed(() => total.value > 15)
const composing = computed(() => store.story.running)

async function rebeat(): Promise<void> {
  await store.composeStory({ stages: ['beats'], panel_ids: [props.panel.id] })
}

async function add(): Promise<void> {
  await store.addBeat(props.panel.id, 'new beat')
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
        v-for="b in panel.beats"
        :key="b.id"
        :beat="b"
        :subjects="store.tree?.subjects ?? []"
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
