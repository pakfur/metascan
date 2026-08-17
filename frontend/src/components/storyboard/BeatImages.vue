<script setup lang="ts">
import { computed, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, BeatImage } from '../../types/storyboard'
import type { Media } from '../../types/media'
import { isVideoPath } from '../../utils/path'
import MediaViewer from '../viewer/MediaViewer.vue'

const props = defineProps<{ beat: Beat }>()
const store = useStoryboardStore()

// Generation state for THIS beat only (image jobs are beat-scoped since the
// shot->beat reorg -- see stores/storyboard.ts's beatJobState).
const hasActiveJob = computed(() => store.beatJobState.has(props.beat.id))

function reroll(): void {
  void store.generate([props.beat.id])
}

function candidateTooltip(img: BeatImage): string {
  return `seed ${img.seed ?? '—'} · variant ${img.variant_index}`
}

// Keeper click toggles selection: clicking the current keeper again clears
// it (selected_image_id -> null), matching the pre-reorg PanelDetail
// behavior this was lifted from.
function onCandidateClick(img: BeatImage): void {
  const next = props.beat.selected_image_id === img.id ? null : img.id
  void store.selectImage(props.beat.id, next)
}

const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  props.beat.images.map(
    (img) =>
      ({
        file_path: img.file_path,
        is_favorite: false,
        is_video: isVideoPath(img.file_path),
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
  <div class="bi-root">
    <div class="bi-header">
      <label class="bf-label">Candidates</label>
      <button type="button" class="bf-reroll-btn" :disabled="hasActiveJob" @click="reroll">
        {{ hasActiveJob ? 'Generating…' : 'Reroll' }}
      </button>
    </div>
    <div class="candidates-row">
      <div
        v-for="(img, idx) in beat.images"
        :key="img.id"
        class="candidate-tile"
        :class="{ selected: img.id === beat.selected_image_id }"
        :title="candidateTooltip(img)"
        @click="onCandidateClick(img)"
      >
        <img :src="thumbnailUrl(img.file_path)" alt="" class="candidate-img" />
        <span v-if="img.id === beat.selected_image_id" class="candidate-check">✓</span>
        <button
          type="button"
          class="candidate-expand"
          title="View full size"
          @click.stop="viewerIndex = idx"
        >
          <span class="pi pi-search-plus" />
        </button>
      </div>
      <div v-if="beat.images.length === 0" class="candidates-empty">No candidates yet.</div>
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
.bi-root {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bi-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.bf-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.bf-reroll-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}

.bf-reroll-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.bf-reroll-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.candidates-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.candidate-tile {
  position: relative;
  flex: 0 0 auto;
  width: 96px;
  height: 96px;
  border-radius: 6px;
  overflow: hidden;
  border: 2px solid transparent;
  background: var(--surface-ground);
  cursor: pointer;
}

.candidate-tile.selected {
  border-color: var(--primary-color);
}

.candidate-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.candidate-check {
  position: absolute;
  top: 4px;
  left: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--primary-color);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.candidate-expand {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.candidate-tile:hover .candidate-expand {
  opacity: 1;
}

.candidates-empty {
  color: var(--text-color-secondary);
  font-size: 12px;
  padding: 8px 0;
}
</style>
