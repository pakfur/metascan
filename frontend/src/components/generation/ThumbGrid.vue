<script setup lang="ts">
import type { ThumbItem } from '../../types/jobs'
import ThumbTile from './ThumbTile.vue'

// A vertically scrolling grid of image tiles, `columns` wide, that scrolls on
// its own (the caller gives it a bounded height). The `jobs` slot renders
// before the tiles (JobTile placeholders), and a full-width "Show more"
// follows them; the grid itself knows nothing about jobs or about any
// feature's row type.
//
// `emptyText` shows only when there are no items and `loading` is false, so
// the caller passes loading=true until its first list load has landed and
// while a job placeholder is on screen, which is what prevents an
// empty-state flash.
const props = withDefaults(
  defineProps<{
    items: ThumbItem[]
    selectedId: number | string | null
    emptyText?: string
    loading?: boolean
    // More rows exist beyond the ones shown: a "Show more" button ends the grid.
    hasMore?: boolean
    loadingMore?: boolean
    tileSize?: number
    columns?: number
  }>(),
  { emptyText: '', loading: false, hasMore: false, loadingMore: false, tileSize: 132, columns: 3 },
)

const emit = defineEmits<{
  select: [item: ThumbItem, evt: MouseEvent]
  open: [index: number]
  favorite: [item: ThumbItem]
  delete: [item: ThumbItem]
  more: []
}>()
</script>

<template>
  <div class="thumb-grid" :style="{ '--tile-size': `${props.tileSize}px`, '--grid-columns': props.columns }">
    <div class="thumb-grid-cells">
      <slot name="jobs" />
      <ThumbTile
        v-for="(item, index) in items"
        :key="item.id"
        :item="item"
        :selected="item.id === selectedId"
        @select="emit('select', item, $event)"
        @open="emit('open', index)"
        @favorite="emit('favorite', item)"
        @delete="emit('delete', item)"
      />
      <button
        v-if="hasMore"
        type="button"
        class="thumb-more"
        :disabled="loadingMore"
        @click="emit('more')"
      >{{ loadingMore ? 'Loading…' : 'Show more' }}</button>
      <div v-if="!items.length && !loading && emptyText" class="thumb-grid-empty">{{ emptyText }}</div>
    </div>
  </div>
</template>

<style scoped>
/* The gutter is reserved up front, so the grid keeps its width whether or not
   there are enough tiles to scroll. No scrollbar-width here: setting it makes
   Chrome drop the app's ::-webkit-scrollbar styling (style.css). */
.thumb-grid {
  overflow-x: hidden;
  overflow-y: auto;
  scrollbar-gutter: stable;
  min-height: 0;
}

/* Padding: room for the selected tile's outline, which sits outside the tile. */
.thumb-grid-cells {
  display: grid;
  grid-template-columns: repeat(var(--grid-columns, 3), var(--tile-size, 132px));
  gap: 12px 8px;
  align-content: start;
  align-items: start;
  padding: 4px 4px 8px;
}

.thumb-more {
  grid-column: 1 / -1;
  height: 34px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
}

.thumb-more:hover:not(:disabled) {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.thumb-more:disabled {
  opacity: 0.6;
  cursor: default;
}

.thumb-grid-empty {
  grid-column: 1 / -1;
  padding: 32px 12px;
  text-align: center;
  color: #888;
  font-size: 13px;
}
</style>
