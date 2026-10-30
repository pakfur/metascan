<script setup lang="ts">
import { computed } from 'vue'
import { thumbnailUrl } from '../../api/client'
import type { ThumbItem } from '../../types/jobs'
import { useDelayedClick } from '../../composables/useDelayedClick'
import GradeBadge from '../thumbnails/GradeBadge.vue'

// One image tile: thumbnail, hover overlays (favourite, delete), a read-only
// ✓ / ✗ grade badge and a label underneath. It only reports what the user
// did; ThumbGrid adds the item. The star is ★ only, never a grade.
const props = defineProps<{ item: ThumbItem; selected?: boolean; top?: boolean }>()

// "v2 · 0.82": the prompt variant the image came from, then the taste score;
// either part alone when only it is known.
const badge = computed(() => {
  const parts: string[] = []
  if (props.item.variant != null) parts.push(`v${props.item.variant}`)
  if (props.item.score != null) parts.push(props.item.score.toFixed(2))
  return parts.join(' · ')
})
const badgeTitle = computed(() => {
  const parts: string[] = []
  if (props.item.variant != null) parts.push(`prompt variant ${props.item.variant}`)
  if (props.item.score != null) {
    parts.push(`taste score ${props.item.score.toFixed(2)}: chance you'd pass this`)
  }
  return parts.join(' · ')
})
const emit = defineEmits<{
  select: [evt: MouseEvent]
  open: []
  favorite: []
  delete: []
}>()

// The overlay buttons stop click AND dblclick (in the template): a fast
// double-tap on one must neither select nor open the tile underneath.
//
// A single click waits out the double-click window, and a double-click
// cancels it, so opening the viewer never selects first.
const { onClick, onDblclick } = useDelayedClick(
  (evt) => emit('select', evt),
  () => emit('open'),
)

function onImgError(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}
</script>

<template>
  <div class="thumb-wrap">
    <div
      class="thumb-tile"
      :class="{ selected, top }"
      :title="item.title"
      @click="onClick"
      @dblclick="onDblclick"
    >
      <img :src="thumbnailUrl(item.file_path)" alt="" loading="lazy" decoding="async" @error="onImgError" />
      <span v-if="badge" class="score-badge" :title="badgeTitle">{{ badge }}</span>
      <span v-if="item.blind" class="blind-badge" :title="item.title">judge to reveal</span>
      <GradeBadge v-else-if="item.grade" class="grade-corner" :grade="item.grade" />
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

.thumb-tile.top {
  outline: 2px solid #c9a227;
  outline-offset: 1px;
}

/* Declared after .top so a selected tile keeps the selection colour. */
.thumb-tile.top.selected {
  outline-color: var(--primary-color, #6366f1);
}

.score-badge {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 10px;
  line-height: 1.5;
  /* Hoverable for its tooltip; a click still reaches the tile under it. */
  cursor: pointer;
}

/* The blind badge's corner: a blind tile has no grade, so they never meet. */
.grade-corner {
  position: absolute;
  right: 4px;
  bottom: 4px;
}

.blind-badge {
  position: absolute;
  right: 4px;
  bottom: 4px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(201, 162, 39, 0.85);
  color: #111;
  font-size: 10px;
  line-height: 1.5;
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
