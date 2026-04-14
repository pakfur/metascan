<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Media } from '../../types/media'
import { submitUpscale } from '../../api/upscale'

const props = defineProps<{
  mediaItems: Media[]
}>()

const emit = defineEmits<{
  close: []
  submitted: []
}>()

const scaleFactor = ref(2)
const modelType = ref('general')
const faceEnhance = ref(false)
const interpolateFrames = ref(false)
const fpsMultiplier = ref(2)
const customFps = ref<number | null>(null)
const useCustomFps = ref(false)
const concurrentWorkers = ref(1)
const submitting = ref(false)

const hasVideos = computed(() => props.mediaItems.some((m) => m.is_video))
const imageCount = computed(() => props.mediaItems.filter((m) => !m.is_video).length)
const videoCount = computed(() => props.mediaItems.filter((m) => m.is_video).length)

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function submit() {
  submitting.value = true
  try {
    await submitUpscale({
      tasks: props.mediaItems.map((m) => ({
        file_path: m.file_path,
        scale_factor: scaleFactor.value,
        model_type: modelType.value,
        face_enhance: faceEnhance.value,
        interpolate_frames: m.is_video ? interpolateFrames.value : false,
        fps_multiplier: fpsMultiplier.value,
        custom_fps: useCustomFps.value ? customFps.value : null,
      })),
      concurrent_workers: concurrentWorkers.value,
    })
    emit('submitted')
    emit('close')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>Upscale</h3>

      <!-- Selected files summary -->
      <div class="file-summary">
        <span>{{ mediaItems.length }} file{{ mediaItems.length > 1 ? 's' : '' }} selected</span>
        <span v-if="imageCount > 0" class="file-tag">{{ imageCount }} image{{ imageCount > 1 ? 's' : '' }}</span>
        <span v-if="videoCount > 0" class="file-tag video">{{ videoCount }} video{{ videoCount > 1 ? 's' : '' }}</span>
      </div>

      <!-- File list (up to 10) -->
      <div v-if="mediaItems.length <= 10" class="file-list">
        <div v-for="m in mediaItems" :key="m.file_path" class="file-row">
          <span class="file-name">{{ m.file_name }}</span>
          <span class="file-meta">{{ m.width }}x{{ m.height }}</span>
          <span class="file-meta">{{ formatSize(m.file_size) }}</span>
        </div>
      </div>

      <!-- Options -->
      <div class="options-section">
        <div class="form-group">
          <label>Scale Factor</label>
          <select v-model.number="scaleFactor">
            <option :value="2">2x</option>
            <option :value="4">4x</option>
          </select>
        </div>

        <div class="form-group">
          <label>Model Type</label>
          <select v-model="modelType">
            <option value="general">General</option>
            <option value="anime">Anime</option>
          </select>
        </div>

        <div class="form-group">
          <label class="checkbox-label">
            <input type="checkbox" v-model="faceEnhance" />
            Face Enhancement (GFPGAN)
          </label>
        </div>

        <template v-if="hasVideos">
          <div class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="interpolateFrames" />
              Interpolate Frames (RIFE)
            </label>
          </div>

          <div v-if="interpolateFrames" class="form-group">
            <label>FPS Multiplier</label>
            <select v-model.number="fpsMultiplier">
              <option :value="2">2x</option>
              <option :value="4">4x</option>
              <option :value="8">8x</option>
            </select>
          </div>

          <div v-if="interpolateFrames" class="form-group">
            <label class="checkbox-label">
              <input type="checkbox" v-model="useCustomFps" />
              Custom FPS
            </label>
            <input
              v-if="useCustomFps"
              type="number"
              v-model.number="customFps"
              min="1"
              max="120"
              placeholder="e.g. 60"
              class="custom-fps-input"
            />
          </div>
        </template>

        <div class="form-group">
          <label>Concurrent Workers</label>
          <select v-model.number="concurrentWorkers">
            <option :value="1">1</option>
            <option :value="2">2</option>
            <option :value="3">3</option>
            <option :value="4">4</option>
          </select>
        </div>
      </div>

      <p class="note">Original files will be moved to trash after upscaling.</p>

      <div class="dialog-actions">
        <button class="btn-primary" @click="submit" :disabled="submitting">
          {{ submitting ? 'Submitting...' : 'Begin' }}
        </button>
        <button class="btn-secondary" @click="emit('close')">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 28px 32px;
  min-width: 420px;
  max-width: 520px;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 16px;
  font-size: 18px;
  color: var(--text-color);
}

.file-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  font-size: 14px;
  color: var(--text-color);
}

.file-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
}

.file-tag.video {
  background: color-mix(in srgb, #8b5cf6 15%, transparent);
  color: #8b5cf6;
}

.file-list {
  max-height: 160px;
  overflow-y: auto;
  margin-bottom: 16px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 10px;
  font-size: 12px;
  border-bottom: 1px solid var(--surface-border);
}

.file-row:last-child {
  border-bottom: none;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-color);
}

.file-meta {
  color: var(--text-color-secondary);
  white-space: nowrap;
  font-size: 11px;
}

.options-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
}

.form-group label:not(.checkbox-label) {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color-secondary);
  margin-bottom: 4px;
}

.form-group select,
.form-group input[type='number'] {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-color);
}

.custom-fps-input {
  margin-top: 6px;
  width: 100px !important;
}

.note {
  font-size: 12px;
  color: var(--text-color-secondary);
  font-style: italic;
  margin-bottom: 4px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}

.btn-secondary:hover { background: var(--surface-hover); }
</style>
