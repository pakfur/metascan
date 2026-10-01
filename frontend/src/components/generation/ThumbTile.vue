<script setup lang="ts">
import { thumbnailUrl } from '../../api/client'
import type { ThumbItem } from '../../types/jobs'

// One image tile: thumbnail, hover overlays (favourite, delete) and a label
// underneath. It only reports what the user did; ThumbGrid adds the item.
defineProps<{ item: ThumbItem; selected?: boolean }>()
const emit = defineEmits<{
  select: [evt: MouseEvent]
  open: []
  favorite: []
  delete: []
}>()

// The overlay buttons stop click AND dblclick (in the template): a fast
// double-tap on one must neither select nor open the tile underneath.
//
// detail > 1 is the second click of a double-click, which opens the viewer;
// the first click already selected, so it must not select again.
function onClick(evt: MouseEvent) {
  if (evt.detail > 1) return
  emit('select', evt)
}

function onImgError(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}
</script>

<template>
  <div class="thumb-wrap">
    <div
      class="thumb-tile"
      :class="{ selected }"
      :title="item.title"
      @click="onClick"
      @dblclick="emit('open')"
    >
      <img :src="thumbnailUrl(item.file_path)" alt="" loading="lazy" decoding="async" @error="onImgError" />
      <button
        type="button"
        class="overlay star"
        :class="{ active: item.is_favorite }"
        title="Favorite"
        @click.stop="emit('favorite')"
        @dblclick.stop
      >{{ item.is_favorite ? '★' : '☆' }}</button>
      <button
        type="button"
        class="overlay del"
        title="Delete"
        @click.stop="emit('delete')"
        @dblclick.stop
      >✕</button>
    </div>
    <div class="thumb-label" :title="item.label">{{ item.label }}</div>
  </div>
</template>

<style scoped>
.thumb-wrap {
  flex: 0 0 var(--tile-size, 132px);
  width: var(--tile-size, 132px);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.thumb-tile {
  position: relative;
  width: 100%;
  height: var(--tile-size, 132px);
  border-radius: 6px;
  overflow: hidden;
  background: #111;
  cursor: pointer;
}

/* contain, not cover: generated images come in portrait and landscape, and
   the whole frame is what the user is judging. */
.thumb-tile img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* outline, not border: a border would shrink the fixed-size thumbnail. */
.thumb-tile.selected {
  outline: 2px solid var(--primary-color, #6366f1);
  outline-offset: 1px;
}

.overlay {
  position: absolute;
  opacity: 0;
  transition: opacity 0.15s;
  border: none;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  padding: 3px 5px;
}

.thumb-tile:hover .overlay {
  opacity: 1;
}

.star {
  top: 4px;
  left: 4px;
}

.star.active {
  opacity: 1;
  color: gold;
}

.del {
  top: 4px;
  right: 4px;
}

.thumb-label {
  font-size: 11px;
  line-height: 1.35;
  color: var(--text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
