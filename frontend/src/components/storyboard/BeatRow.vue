<script setup lang="ts">
import { computed } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, Subject } from '../../types/storyboard'

const props = defineProps<{
  beat: Beat
  subjects: Subject[]
  index: number
  canMoveUp?: boolean
  canMoveDown?: boolean
}>()
const emit = defineEmits<{ moveUp: []; moveDown: [] }>()
const store = useStoryboardStore()

const selected = computed(() => store.selectedBeatId === props.beat.id)

const motionLabel = computed(() =>
  props.beat.camera_motion ? props.beat.camera_motion.replace(/_/g, ' ') : null,
)

function select(): void {
  store.selectedBeatId = props.beat.id
}

async function remove(): Promise<void> {
  await store.removeBeat(props.beat.id)
}
</script>

<template>
  <div class="beat-pill" :class="{ selected }" @click="select">
    <span class="beat-pill-index">#{{ index + 1 }}</span>
    <span class="beat-pill-duration">{{ beat.duration_s }}s</span>
    <span v-if="motionLabel" class="beat-pill-motion">{{ motionLabel }}</span>
    <span v-if="beat.is_cut" class="beat-pill-cut">(cut)</span>
    <span class="beat-pill-action">{{ beat.action }}</span>
    <span v-if="beat.dialog.length" class="beat-pill-dialog" title="Dialog lines">
      💬{{ beat.dialog.length }}
    </span>
    <button
      type="button"
      class="beat-icon-btn"
      title="Move beat up"
      :disabled="!canMoveUp"
      @click.stop="emit('moveUp')"
    >
      ↑
    </button>
    <button
      type="button"
      class="beat-icon-btn"
      title="Move beat down"
      :disabled="!canMoveDown"
      @click.stop="emit('moveDown')"
    >
      ↓
    </button>
    <button type="button" class="beat-icon-btn beat-icon-btn-x" title="Delete beat" @click.stop="remove">
      ✕
    </button>
  </div>
</template>

<style scoped>
.beat-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  cursor: pointer;
  min-width: 0;
}

.beat-pill:hover {
  background: var(--surface-hover);
}

.beat-pill.selected {
  border-color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 10%, transparent);
}

.beat-pill-index {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

.beat-pill-duration {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beat-pill-motion {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-card);
}

.beat-pill-cut {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--warn, #e0a030);
}

.beat-pill-action {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.beat-pill-dialog {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beat-icon-btn {
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  width: 22px;
  height: 22px;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.beat-icon-btn:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--text-color);
}

.beat-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.beat-icon-btn-x:hover:not(:disabled) {
  color: var(--danger-color, #e53e3e);
}
</style>
