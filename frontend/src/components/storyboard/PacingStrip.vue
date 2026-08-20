<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import {
  DEFAULT_SHOT_CAP,
  VIDEO_TARGET_CAPS,
  type Beat,
  type Panel,
} from '../../types/storyboard'

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
        <span class="ps-empty" />
        <span class="ps-label">
          <span>{{ i + 1 }}</span>
          <span v-if="showDuration(b)">{{ b.duration_s.toFixed(1) }}s</span>
        </span>
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

<style scoped>
.ps-track {
  display: flex;
  gap: 2px;
  height: 48px;
}

.ps-seg {
  position: relative;
  min-width: 30px;
  flex-shrink: 0;
  padding: 0;
  overflow: hidden;
  cursor: pointer;
  border: 2px solid transparent;
  border-radius: 5px;
  background: var(--surface-ground);
}

.ps-seg.selected {
  border-color: var(--primary-color);
}

.ps-seg.cut {
  border-left: 3px solid var(--warn);
}

.ps-empty {
  position: absolute;
  inset: 2px;
  border: 1px dashed var(--surface-border);
  border-radius: 3px;
}

.ps-label {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 6px;
  padding: 2px 4px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.7));
  color: #fff;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
}

.ps-budget {
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
}

.ps-capbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.ps-captrack {
  flex: 1;
  height: 3px;
  border-radius: 2px;
  background: var(--surface-hover);
  overflow: hidden;
}

.ps-capfill {
  height: 100%;
  background: var(--primary-color);
}

.ps-capfill.over {
  background: var(--warn);
}

.ps-caplabel {
  font-size: 11px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.ps-caplabel.over {
  color: var(--warn);
}
</style>
