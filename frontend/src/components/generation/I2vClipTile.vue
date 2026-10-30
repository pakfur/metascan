<script setup lang="ts">
import { computed } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { i2vVideoDetails, type I2vVideo } from '../../types/i2v'
import { useDelayedClick } from '../../composables/useDelayedClick'
import GradeBadge from '../thumbnails/GradeBadge.vue'

// One finished clip: thumbnail, hover overlays (play, favourite, delete), a
// read-only ✓ / ✗ grade badge and the three detail lines. It only reports what
// the user did. The star is ★ only, never a grade. ▶ opens the viewer at once;
// a plain click waits out the double-click window, so playing never loads.
const props = defineProps<{ video: I2vVideo; selected: boolean }>()

const emit = defineEmits<{
  select: [evt: MouseEvent]
  play: []
  favorite: []
  delete: []
}>()

const details = computed(() => i2vVideoDetails(props.video))
const title = computed(() => {
  const v = props.video
  return (
    `Click to load this clip's idea, settings and prompt · double-click to play\n` +
    `seed ${v.seed ?? '—'} · ${v.duration_s ?? '—'}s · ${v.quality ?? ''}` +
    `${v.width && v.height ? ` · ${v.width}×${v.height}` : ''}`
  )
})

// The overlay buttons stop click AND dblclick: a fast double-tap on one must
// neither select nor play the tile underneath.
const { onClick, onDblclick } = useDelayedClick(
  (evt) => emit('select', evt),
  () => emit('play'),
)
</script>

<template>
  <div class="clip-wrap">
    <div
      class="clip-tile"
      :class="{ selected }"
      :title="title"
      @click="onClick"
      @dblclick="onDblclick"
    >
      <img :src="thumbnailUrl(video.file_path)" alt="" loading="lazy" decoding="async" />
      <button
        type="button"
        class="overlay play"
        title="Play"
        @click.stop="emit('play')"
        @dblclick.stop
      >▶</button>
      <button
        type="button"
        class="overlay star"
        :class="{ active: video.is_favorite }"
        title="Favorite"
        @click.stop="emit('favorite')"
        @dblclick.stop
      >★</button>
      <button
        type="button"
        class="overlay del"
        title="Delete"
        @click.stop="emit('delete')"
        @dblclick.stop
      >✕</button>
      <GradeBadge v-if="video.grade" class="grade-corner" :grade="video.grade" />
    </div>
    <div class="clip-meta">
      <span class="clip-meta-quality" :title="details.quality">{{ details.quality }}</span>
      <span :title="details.timing">{{ details.timing }}</span>
      <span>{{ details.generated }}</span>
    </div>
  </div>
</template>

<style scoped>
.clip-wrap {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.clip-tile {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 6px;
  overflow: hidden;
  background: #111;
  cursor: pointer;
}

.clip-tile img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* outline, not border: a border would shrink the thumbnail. */
.clip-tile.selected {
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

.clip-tile:hover .overlay {
  opacity: 1;
}

.play {
  inset: 0;
  margin: auto;
  width: 36px;
  height: 36px;
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

.grade-corner {
  position: absolute;
  right: 4px;
  bottom: 4px;
}

.clip-meta {
  display: flex;
  flex-direction: column;
  font-size: 11px;
  line-height: 1.35;
  color: var(--text-color-secondary);
}

.clip-meta span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.clip-meta-quality {
  color: var(--text-color);
  font-weight: 600;
}
</style>
