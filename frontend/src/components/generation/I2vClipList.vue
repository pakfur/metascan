<script setup lang="ts">
import type { I2vVideo } from '../../types/i2v'
import I2vClipTile from './I2vClipTile.vue'

// A vertically scrolling list of clip tiles, `columns` wide, that scrolls on
// its own (the caller gives it a bounded height). The `jobs` slot renders
// before the tiles, and a full-width "Show more" follows them.
defineProps<{
  videos: I2vVideo[]
  selectedId: number | null
  columns: 1 | 2
  hasMore?: boolean
  loadingMore?: boolean
  emptyText: string
}>()

const emit = defineEmits<{
  select: [video: I2vVideo, evt: MouseEvent]
  play: [index: number]
  favorite: [video: I2vVideo]
  delete: [video: I2vVideo]
  more: []
}>()
</script>

<template>
  <div class="clip-list">
    <div class="clip-cells" :style="{ '--clip-columns': columns }">
      <slot name="jobs" />
      <I2vClipTile
        v-for="(v, index) in videos"
        :key="v.id"
        :video="v"
        :selected="v.id === selectedId"
        @select="emit('select', v, $event)"
        @play="emit('play', index)"
        @favorite="emit('favorite', v)"
        @delete="emit('delete', v)"
      />
      <button
        v-if="hasMore"
        type="button"
        class="clip-more"
        :disabled="loadingMore"
        @click="emit('more')"
      >{{ loadingMore ? 'Loading…' : 'Show more' }}</button>
      <div v-if="!videos.length && !$slots.jobs && emptyText" class="clip-empty">{{ emptyText }}</div>
    </div>
  </div>
</template>

<style scoped>
/* The gutter is reserved up front, so the list keeps its width whether or not
   there are enough tiles to scroll. */
.clip-list {
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-gutter: stable;
  min-height: 0;
}

/* Padding: room for the selected tile's outline, which sits outside the tile. */
.clip-cells {
  display: grid;
  grid-template-columns: repeat(var(--clip-columns, 1), minmax(0, 1fr));
  gap: 12px 8px;
  align-content: start;
  align-items: start;
  padding: 4px 4px 8px;
}

.clip-more {
  grid-column: 1 / -1;
  height: 34px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
}

.clip-more:hover:not(:disabled) {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.clip-more:disabled {
  opacity: 0.6;
  cursor: default;
}

.clip-empty {
  grid-column: 1 / -1;
  padding: 24px 8px;
  text-align: center;
  color: #888;
  font-size: 13px;
}
</style>
