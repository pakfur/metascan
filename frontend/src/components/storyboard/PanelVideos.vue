<script setup lang="ts">
import { computed, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, PanelVideo } from '../../types/storyboard'
import type { Media } from '../../types/media'
import MediaViewer from '../viewer/MediaViewer.vue'

// Rendered clips for the whole shot (panel_videos) — panel-scoped, unlike
// the per-beat image candidates in BeatImages.vue. No keeper concept; this
// strip is for reviewing takes and discarding the bad ones.
const props = defineProps<{ panel: Panel }>()
const store = useStoryboardStore()

function tooltip(v: PanelVideo): string {
  return `seed ${v.seed ?? '—'} · take ${v.variant_index + 1}`
}

// Delete is total: panel_videos row, media row, and the file to the OS
// trash — hence the confirm.
async function onDelete(video: PanelVideo): Promise<void> {
  const name = video.file_path.split(/[\\/]/).pop() ?? video.file_path
  if (!confirm(`Delete take ${video.variant_index + 1} (${name})?\nThis removes it from the library and moves the file to the trash.`)) {
    return
  }
  await store.removePanelVideo(video.id)
}

const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  props.panel.videos.map(
    (v) =>
      ({
        file_path: v.file_path,
        is_favorite: false,
        is_video: true,
        playback_speed: null,
        width: 0,
        height: 0,
        file_size: 0,
        frame_rate: null,
        duration: null,
      }) as Media,
  ),
)
</script>

<template>
  <div v-if="panel.videos.length > 0" class="pv-root">
    <label class="pv-label">Video takes</label>
    <div class="pv-row">
      <div
        v-for="(video, idx) in panel.videos"
        :key="video.id"
        class="pv-tile"
        :title="tooltip(video)"
        @dblclick="viewerIndex = idx"
      >
        <img :src="thumbnailUrl(video.file_path)" alt="" class="pv-img" />
        <span class="pv-play" title="Play" @click.stop="viewerIndex = idx">▶</span>
        <button
          type="button"
          class="pv-delete"
          title="Delete take"
          @click.stop="onDelete(video)"
        >
          ×
        </button>
      </div>
    </div>
  </div>

  <MediaViewer
    v-if="viewerIndex !== null"
    :media-list="viewerMedia"
    :initial-index="viewerIndex"
    :allow-destructive="false"
    @close="viewerIndex = null"
  />
</template>

<style scoped>
.pv-root {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pv-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.pv-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.pv-tile {
  position: relative;
  flex: 0 0 auto;
  width: 128px;
  height: 72px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-ground);
  cursor: pointer;
}

.pv-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.pv-play {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: #fff;
  background: rgba(0, 0, 0, 0.25);
  opacity: 0;
  transition: opacity 0.15s;
}

.pv-tile:hover .pv-play {
  opacity: 1;
}

.pv-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 13px;
  line-height: 18px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 1;
}

.pv-tile:hover .pv-delete {
  opacity: 1;
}

.pv-delete:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 75%, black);
}
</style>
