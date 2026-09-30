<script setup lang="ts">
import type { ThumbItem } from '../../types/jobs'
import ThumbTile from './ThumbTile.vue'

// A horizontally scrolling strip of image tiles. The `jobs` slot renders
// before the tiles (JobTile placeholders); the strip itself knows nothing
// about jobs or about any feature's row type.
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
    // More rows exist beyond the ones held: shows an "Older" tile at the end.
    hasMore?: boolean
    loadingMore?: boolean
    tileSize?: number
  }>(),
  { emptyText: '', loading: false, hasMore: false, loadingMore: false, tileSize: 132 },
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
  <div class="thumb-strip" :style="{ '--tile-size': `${props.tileSize}px` }">
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
    >{{ loadingMore ? 'Loading…' : 'Older ›' }}</button>
    <div v-if="!items.length && !loading && emptyText" class="thumb-strip-empty">{{ emptyText }}</div>
  </div>
</template>

<style scoped>
.thumb-strip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 0;
  min-height: calc(var(--tile-size, 132px) + 30px);
}

.thumb-more {
  flex: 0 0 auto;
  align-self: flex-start;
  height: var(--tile-size, 132px);
  padding: 0 14px;
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

.thumb-strip-empty {
  align-self: center;
  color: #888;
  font-size: 13px;
}
</style>
