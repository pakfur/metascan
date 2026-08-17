<script setup lang="ts">
import { computed, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, Subject } from '../../types/storyboard'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const props = defineProps<{
  beat: Beat
  subjects: Subject[]
  index: number
  canMoveUp?: boolean
  canMoveDown?: boolean
}>()
const emit = defineEmits<{ moveUp: []; moveDown: [] }>()
const store = useStoryboardStore()

const selected = computed(() => store.selectedBeatId === props.beat.id)

const motionLabel = computed(() =>
  props.beat.camera_motion ? props.beat.camera_motion.replace(/_/g, ' ') : null,
)

const keeperSrc = computed(() => {
  const img = store.keeperImage(props.beat)
  return img ? thumbnailUrl(img.file_path) : null
})

const jobBadge = computed(() => store.beatJobState.get(props.beat.id))

function select(): void {
  store.selectedBeatId = props.beat.id
}

// Deleting a beat with generated images asks what happens to them (purge /
// keep in library / cancel) via DeleteImagesDialog, mirroring PanelGrid's /
// SceneStrip's panel/scene delete flow; a beat with no images gets a plain
// confirm.
const pendingDelete = ref(false)

function onDeleteClick(): void {
  if (props.beat.images.length === 0) {
    if (!confirm('Delete this beat?')) return
    void store.removeBeat(props.beat.id)
    return
  }
  pendingDelete.value = true
}

async function confirmDelete(purgeImages: boolean): Promise<void> {
  pendingDelete.value = false
  await store.removeBeat(props.beat.id, purgeImages)
}
</script>

<template>
  <div class="beat-pill" :class="{ selected }" @click="select">
    <div class="beat-pill-thumb">
      <img v-if="keeperSrc" :src="keeperSrc" alt="" class="beat-pill-thumb-img" />
      <div v-else class="beat-pill-thumb-empty" />
      <span
        v-if="jobBadge?.state === 'queued'"
        class="beat-pill-job beat-pill-job--queued"
        title="Queued"
      >
        ⏳
      </span>
      <span
        v-else-if="jobBadge?.state === 'running'"
        class="beat-pill-job beat-pill-job--running"
        title="Generating"
      >
        <span class="pi pi-spin pi-spinner" />
      </span>
      <span
        v-else-if="jobBadge?.state === 'failed'"
        class="beat-pill-job beat-pill-job--failed"
        :title="jobBadge?.error ?? 'Generation failed'"
      >
        ⚠
      </span>
    </div>
    <span class="beat-pill-index">#{{ index + 1 }}</span>
    <span v-if="beat.images.length" class="beat-pill-images" title="Generated images">
      🖼{{ beat.images.length }}
    </span>
    <span class="beat-pill-duration">{{ beat.duration_s }}s</span>
    <span v-if="motionLabel" class="beat-pill-motion">{{ motionLabel }}</span>
    <span v-if="beat.is_cut" class="beat-pill-cut">(cut)</span>
    <span class="beat-pill-action">{{ beat.action }}</span>
    <span v-if="beat.dialog.length" class="beat-pill-dialog" title="Dialog lines">
      💬{{ beat.dialog.length }}
    </span>
    <button
      type="button"
      class="beat-icon-btn"
      title="Move beat up"
      :disabled="!canMoveUp"
      @click.stop="emit('moveUp')"
    >
      ↑
    </button>
    <button
      type="button"
      class="beat-icon-btn"
      title="Move beat down"
      :disabled="!canMoveDown"
      @click.stop="emit('moveDown')"
    >
      ↓
    </button>
    <button
      type="button"
      class="beat-icon-btn beat-icon-btn-x"
      title="Delete beat"
      @click.stop="onDeleteClick"
    >
      ✕
    </button>
  </div>

  <DeleteImagesDialog
    v-if="pendingDelete"
    title="Delete beat?"
    message="This beat has generated images. Delete them permanently, or keep them visible in the media library?"
    :image-count="beat.images.length"
    @purge="confirmDelete(true)"
    @keep="confirmDelete(false)"
    @cancel="pendingDelete = false"
  />
</template>

<style scoped>
.beat-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  cursor: pointer;
  min-width: 0;
}

.beat-pill:hover {
  background: var(--surface-hover);
}

.beat-pill.selected {
  border-color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 10%, transparent);
}

.beat-pill-thumb {
  position: relative;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface-card);
}

.beat-pill-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.beat-pill-thumb-empty {
  width: 100%;
  height: 100%;
  border: 1px dashed var(--surface-border);
  box-sizing: border-box;
  border-radius: 4px;
}

.beat-pill-job {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}

.beat-pill-job--failed {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 70%, black);
  cursor: help;
}

.beat-pill-index {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

.beat-pill-images {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beat-pill-duration {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beat-pill-motion {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface-card);
}

.beat-pill-cut {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--warn, #e0a030);
}

.beat-pill-action {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.beat-pill-dialog {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.beat-icon-btn {
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  width: 22px;
  height: 22px;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.beat-icon-btn:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--text-color);
}

.beat-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.beat-icon-btn-x:hover:not(:disabled) {
  color: var(--danger-color, #e53e3e);
}
</style>
