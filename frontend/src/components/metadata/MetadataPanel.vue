<script setup lang="ts">
import { computed } from 'vue'
import { useMediaStore } from '../../stores/media'
import { useFoldersStore } from '../../stores/folders'
import { useToast } from '../../composables/useToast'
import MetadataField from './MetadataField.vue'
import CameraSection from './CameraSection.vue'
import LocationSection from './LocationSection.vue'
import SavedPromptsSection from './SavedPromptsSection.vue'
import { fileName } from '../../utils/path'
import { copyToClipboard } from '../../utils/clipboard'
import type { AnyFolder } from '../../types/folders'

const mediaStore = useMediaStore()
const foldersStore = useFoldersStore()
const toast = useToast()
const media = computed(() => mediaStore.selectedMedia)

const memberFolders = computed<AnyFolder[]>(() => {
  if (!media.value) return []
  return foldersStore.foldersContaining(media.value, mediaStore.allMedia)
})

function removeFromFolder(folderId: string) {
  if (!media.value) return
  const path = media.value.file_path
  const removed = foldersStore.removeFromManualFolder(folderId, [path])
  const f = foldersStore.manualFolders.find((x) => x.id === folderId)
  if (removed > 0 && f) toast.show(`Removed from ${f.name}`)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(isoStr: string | null): string {
  if (!isoStr) return '-'
  return new Date(isoStr).toLocaleString()
}

async function copyAll() {
  if (!media.value) return
  const json = JSON.stringify(media.value, null, 2)
  await copyToClipboard(json)
}
</script>

<template>
  <div class="metadata-panel">
    <template v-if="media">
      <div class="meta-header">
        <span class="meta-title">Details</span>
        <button class="copy-all-btn" @click="copyAll" title="Copy all as JSON">
          Copy All
        </button>
      </div>

      <!-- In Folders -->
      <details v-if="memberFolders.length" class="meta-section" open>
        <summary class="section-title">In folders</summary>
        <div class="section-body">
          <div class="in-folder-chips">
            <span
              v-for="f in memberFolders"
              :key="f.id"
              class="in-folder-chip"
              :class="{ smart: f.kind === 'smart' }"
            >
              <i class="pi" :class="f.icon" />{{ f.name }}
              <span
                v-if="f.kind === 'manual'"
                class="x"
                @click="removeFromFolder(f.id)"
                title="Remove from this folder"
                >×</span
              >
            </span>
          </div>
        </div>
      </details>

      <!-- Tags (merged prompt + CLIP from the indices table; unrelated to AI metadata) -->
      <details v-if="media.tags?.length" class="meta-section">
        <summary class="section-title">Tags ({{ media.tags.length }})</summary>
        <div class="section-body">
          <div class="tags-list">
            <span v-for="tag in media.tags" :key="tag" class="tag-chip">{{ tag }}</span>
          </div>
        </div>
      </details>

      <!-- File Information -->
      <details class="meta-section" open>
        <summary class="section-title">File Information</summary>
        <div class="section-body">
          <MetadataField label="Name" :value="media.file_name ?? fileName(media.file_path)" />
          <MetadataField label="Path" :value="media.file_path" />
          <MetadataField label="Size" :value="formatSize(media.file_size)" />
          <MetadataField label="Modified" :value="formatDate(media.modified_at ?? null)" />
        </div>
      </details>

      <!-- Image/Video Properties -->
      <details class="meta-section" open>
        <summary class="section-title">Properties</summary>
        <div class="section-body">
          <MetadataField label="Resolution" :value="`${media.width} x ${media.height}`" />
          <MetadataField label="Format" :value="media.format ?? '-'" />
          <MetadataField label="Type" :value="media.media_type ?? (media.is_video ? 'video' : 'image')" />
          <MetadataField v-if="media.frame_rate" label="Frame Rate" :value="`${media.frame_rate} fps`" />
          <MetadataField v-if="media.duration" label="Duration" :value="`${media.duration.toFixed(1)}s`" />
        </div>
      </details>

      <CameraSection :media="media" />
      <LocationSection :media="media" />
      <SavedPromptsSection />

      <!-- AI Generation -->
      <details v-if="media.metadata_source" class="meta-section" open>
        <summary class="section-title">AI Generation</summary>
        <div class="section-body">
          <MetadataField label="Source" :value="media.metadata_source" />
          <MetadataField v-if="media.model?.length" label="Model" :value="media.model.join(', ')" />
          <MetadataField v-if="media.sampler" label="Sampler" :value="media.sampler" />
          <MetadataField v-if="media.scheduler" label="Scheduler" :value="media.scheduler" />
          <MetadataField v-if="media.steps" label="Steps" :value="String(media.steps)" />
          <MetadataField v-if="media.cfg_scale" label="CFG Scale" :value="String(media.cfg_scale)" />
          <MetadataField v-if="media.seed" label="Seed" :value="String(media.seed)" />
          <MetadataField v-if="media.prompt" label="Prompt" :value="media.prompt" multiline />
          <MetadataField v-if="media.negative_prompt" label="Negative" :value="media.negative_prompt" multiline />
        </div>
      </details>

      <!-- LoRAs -->
      <details v-if="media.loras?.length" class="meta-section">
        <summary class="section-title">LoRAs ({{ media.loras.length }})</summary>
        <div class="section-body">
          <MetadataField
            v-for="lora in media.loras"
            :key="lora.lora_name"
            :label="lora.lora_name"
            :value="`weight: ${lora.lora_weight}`"
          />
        </div>
      </details>
    </template>

    <div v-else class="no-selection">
      Select a file to view its details
    </div>
  </div>
</template>

<style scoped>
.metadata-panel {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  box-sizing: border-box;
}

.meta-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
  flex: 0 0 auto;
}

.meta-title {
  font-weight: 700;
  font-size: 15px;
  color: var(--text-color);
}

.copy-all-btn {
  font-size: 12px;
  padding: 3px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  background: var(--surface-card);
  color: var(--text-color);
  cursor: pointer;
}

.copy-all-btn:hover {
  background: var(--surface-hover);
}

:deep(.meta-section) {
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  overflow: hidden;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
}

:deep(.section-title) {
  padding: 6px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  cursor: pointer;
  background: var(--surface-section);
  list-style: none;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

:deep(.section-title::before) {
  content: '▶';
  font-size: 9px;
  transition: transform 0.15s;
}

:deep(details[open] > .section-title::before) {
  transform: rotate(90deg);
}

:deep(.section-body) {
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 40px;
  max-height: 320px;
  overflow-y: auto;
  overflow-x: hidden;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tag-chip {
  padding: 2px 8px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
  font-size: 11px;
}

.in-folder-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.in-folder-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
  font-size: 11px;
}

.in-folder-chip.smart {
  background: color-mix(in srgb, #a855f7 15%, transparent);
  color: #a855f7;
}

.in-folder-chip .pi {
  font-size: 10px;
}

.in-folder-chip .x {
  margin-left: 2px;
  cursor: pointer;
  opacity: 0.7;
}

.in-folder-chip .x:hover {
  opacity: 1;
}

.no-selection {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-color-secondary);
  font-size: 14px;
}
</style>
