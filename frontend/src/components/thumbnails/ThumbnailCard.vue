<script setup lang="ts">
import { computed } from 'vue'
import type { Media } from '../../types/media'
import { thumbnailUrl } from '../../api/client'
import { useMediaStore } from '../../stores/media'

const props = defineProps<{
  media: Media
  size: number
  selected: boolean
}>()

const mediaStore = useMediaStore()
const imgSrc = computed(() => thumbnailUrl(props.media.file_path))

function onFavoriteClick(e: MouseEvent) {
  e.stopPropagation()
  mediaStore.toggleFavorite(props.media)
}

function onImgError(e: Event) {
  const img = e.target as HTMLImageElement
  img.style.display = 'none'
}
</script>

<template>
  <div
    class="thumbnail-card"
    :class="{ selected }"
    :style="{ width: size + 'px', height: size + 'px' }"
  >
    <img
      :src="imgSrc"
      :alt="media.file_name"
      class="thumb-img"
      loading="lazy"
      @error="onImgError"
    />

    <button
      class="fav-btn"
      :class="{ active: media.is_favorite }"
      @click="onFavoriteClick"
      title="Toggle favorite"
    >
      {{ media.is_favorite ? '★' : '☆' }}
    </button>

    <span v-if="media.is_video" class="video-badge">▶</span>

    <div class="filename-overlay" :title="media.file_name">
      {{ media.file_name }}
    </div>

    <div v-if="media.similarity_score != null" class="similarity-badge">
      {{ (media.similarity_score * 100).toFixed(0) }}%
    </div>
  </div>
</template>

<style scoped>
.thumbnail-card {
  position: relative;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-card);
  border: 2px solid transparent;
  cursor: pointer;
  transition: border-color 0.15s;
}

.thumbnail-card:hover {
  border-color: color-mix(in srgb, var(--primary-color) 50%, transparent);
}

.thumbnail-card.selected {
  border-color: var(--primary-color);
}

.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.fav-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  background: rgba(0, 0, 0, 0.5);
  border: none;
  color: #ccc;
  font-size: 16px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  line-height: 1;
  opacity: 0;
  transition: opacity 0.15s;
}

.thumbnail-card:hover .fav-btn,
.fav-btn.active {
  opacity: 1;
}

.fav-btn.active {
  color: #fbbf24;
}

.video-badge {
  position: absolute;
  top: 4px;
  left: 4px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
}

.filename-overlay {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 4px 6px;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.7));
  color: #fff;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0;
  transition: opacity 0.15s;
}

.thumbnail-card:hover .filename-overlay {
  opacity: 1;
}

.similarity-badge {
  position: absolute;
  bottom: 4px;
  right: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #4ade80;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 5px;
  border-radius: 4px;
}
</style>
