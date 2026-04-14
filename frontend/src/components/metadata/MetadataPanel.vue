<script setup lang="ts">
import { computed } from 'vue'
import { useMediaStore } from '../../stores/media'
import MetadataField from './MetadataField.vue'

const mediaStore = useMediaStore()
const media = computed(() => mediaStore.selectedMedia)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(isoStr: string | null): string {
  if (!isoStr) return '-'
  return new Date(isoStr).toLocaleString()
}

function copyAll() {
  if (!media.value) return
  const json = JSON.stringify(media.value, null, 2)
  navigator.clipboard.writeText(json)
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

      <!-- File Information -->
      <details class="meta-section" open>
        <summary class="section-title">File Information</summary>
        <div class="section-body">
          <MetadataField label="Name" :value="media.file_name" />
          <MetadataField label="Path" :value="media.file_path" />
          <MetadataField label="Size" :value="formatSize(media.file_size)" />
          <MetadataField label="Modified" :value="formatDate(media.modified_at)" />
        </div>
      </details>

      <!-- Image/Video Properties -->
      <details class="meta-section" open>
        <summary class="section-title">Properties</summary>
        <div class="section-body">
          <MetadataField label="Resolution" :value="`${media.width} x ${media.height}`" />
          <MetadataField label="Format" :value="media.format" />
          <MetadataField label="Type" :value="media.media_type" />
          <MetadataField v-if="media.frame_rate" label="Frame Rate" :value="`${media.frame_rate} fps`" />
          <MetadataField v-if="media.duration" label="Duration" :value="`${media.duration.toFixed(1)}s`" />
        </div>
      </details>

      <!-- AI Generation -->
      <details v-if="media.metadata_source" class="meta-section" open>
        <summary class="section-title">AI Generation</summary>
        <div class="section-body">
          <MetadataField label="Source" :value="media.metadata_source" />
          <MetadataField v-if="media.model.length" label="Model" :value="media.model.join(', ')" />
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
      <details v-if="media.loras.length" class="meta-section">
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

      <!-- Tags -->
      <details v-if="media.tags.length" class="meta-section">
        <summary class="section-title">Tags ({{ media.tags.length }})</summary>
        <div class="section-body">
          <div class="tags-list">
            <span v-for="tag in media.tags" :key="tag" class="tag-chip">{{ tag }}</span>
          </div>
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
  gap: 4px;
  height: 100%;
  overflow-y: auto;
}

.meta-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
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

.meta-section {
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  overflow: hidden;
}

.section-title {
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
}

.section-title::before {
  content: '▶';
  font-size: 9px;
  transition: transform 0.15s;
}

details[open] > .section-title::before {
  transform: rotate(90deg);
}

.section-body {
  padding: 6px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
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

.no-selection {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-color-secondary);
  font-size: 14px;
}
</style>
