<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  fetchSimilaritySettings,
  updateSimilaritySettings,
  type SimilaritySettings,
} from '../../api/similarity'
import { useScanStore } from '../../stores/scan'

const emit = defineEmits<{
  close: []
}>()

const scanStore = useScanStore()
const settings = ref<SimilaritySettings | null>(null)
const loading = ref(true)

// Local form state
const clipModel = ref('small')
const device = ref('auto')
const phashThreshold = ref(10)
const clipThreshold = ref(0.7)
const searchResultsCount = ref(100)
const videoKeyframes = ref(4)

const modelOptions = [
  { label: 'Small (ViT-B-16, 512MB)', value: 'small' },
  { label: 'Medium (ViT-L-14, 1.8GB)', value: 'medium' },
  { label: 'Large (ViT-H-14, 4GB)', value: 'large' },
]

const deviceOptions = [
  { label: 'Auto', value: 'auto' },
  { label: 'CPU', value: 'cpu' },
  { label: 'CUDA (GPU)', value: 'cuda' },
]

async function refreshSettings() {
  loading.value = true
  try {
    settings.value = await fetchSimilaritySettings()
    clipModel.value = settings.value.clip_model
    device.value = settings.value.device
    phashThreshold.value = settings.value.phash_threshold
    clipThreshold.value = settings.value.clip_threshold
    searchResultsCount.value = settings.value.search_results_count
    videoKeyframes.value = settings.value.video_keyframes
  } finally {
    loading.value = false
  }
}

onMounted(refreshSettings)

const missingCount = computed(() => {
  if (!settings.value) return 0
  const s = settings.value.embedding_stats
  return Math.max(0, s.total_media - s.embedded)
})

const embPct = computed(() =>
  scanStore.embeddingTotal > 0
    ? Math.round((scanStore.embeddingCurrent / scanStore.embeddingTotal) * 100)
    : 0
)

// Refresh stats once a build completes
watch(() => scanStore.embeddingPhase, (phase) => {
  if (phase === 'complete' || phase === 'idle') refreshSettings()
})

async function save() {
  await updateSimilaritySettings({
    clip_model: clipModel.value,
    device: device.value,
    phash_threshold: phashThreshold.value,
    clip_threshold: clipThreshold.value,
    search_results_count: searchResultsCount.value,
    video_keyframes: videoKeyframes.value,
  })
  emit('close')
}

async function toggleAutoIndex(value: boolean) {
  if (!settings.value) return
  settings.value.auto_index_after_scan = value
  await updateSimilaritySettings({ auto_index_after_scan: value })
}

async function toggleComputePhash(value: boolean) {
  if (!settings.value) return
  settings.value.compute_phash_during_scan = value
  await updateSimilaritySettings({ compute_phash_during_scan: value })
}

async function buildMissing() {
  try {
    await scanStore.startEmbeddingBuild(false)
  } catch (e) {
    console.error('Build index failed:', e)
  }
}

async function rebuildAll() {
  if (!confirm(
    'Rebuild ALL embeddings? This clears the existing index and re-indexes every media file. May take a long time.',
  )) return
  try {
    await scanStore.startEmbeddingBuild(true)
  } catch (e) {
    console.error('Rebuild index failed:', e)
  }
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>Similarity Settings</h3>

      <div v-if="loading" class="muted">Loading settings...</div>

      <template v-else>
        <!-- CLIP Model -->
        <div class="form-group">
          <label>CLIP Model</label>
          <select v-model="clipModel">
            <option v-for="opt in modelOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <!-- Device -->
        <div class="form-group">
          <label>Device</label>
          <select v-model="device">
            <option v-for="opt in deviceOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <!-- pHash Threshold -->
        <div class="form-group">
          <label>pHash Threshold (0-20)</label>
          <div class="slider-row">
            <input type="range" min="0" max="20" step="1" v-model.number="phashThreshold" />
            <span class="slider-value">{{ phashThreshold }}</span>
          </div>
        </div>

        <!-- CLIP Threshold -->
        <div class="form-group">
          <label>CLIP Similarity Threshold</label>
          <div class="slider-row">
            <input type="range" min="0" max="1" step="0.05" v-model.number="clipThreshold" />
            <span class="slider-value">{{ (clipThreshold * 100).toFixed(0) }}%</span>
          </div>
          <div class="slider-labels">
            <span>Least Similar</span>
            <span>Most Similar</span>
          </div>
        </div>

        <!-- Search Results Count -->
        <div class="form-group">
          <label>Max Search Results</label>
          <input type="number" v-model.number="searchResultsCount" min="10" max="500" step="10" />
        </div>

        <!-- Video Keyframes -->
        <div class="form-group">
          <label>Video Keyframes</label>
          <input type="number" v-model.number="videoKeyframes" min="1" max="16" />
        </div>

        <!-- Embedding Index -->
        <section v-if="settings" class="embed-section">
          <h4>Embedding Index</h4>
          <p class="stats">
            Files:
            <strong>{{ settings.embedding_stats.total_media }}</strong> total |
            <strong>{{ settings.embedding_stats.hashed }}</strong> hashed |
            <strong>{{ settings.embedding_stats.embedded }}</strong> embedded
            <span v-if="missingCount > 0" class="missing">
              ({{ missingCount }} missing)
            </span>
          </p>

          <!-- Embedding progress -->
          <template v-if="scanStore.embeddingPhase === 'building'">
            <div class="progress-section">
              <div class="progress-bar">
                <div class="progress-fill" :style="{ width: embPct + '%' }" />
              </div>
              <span class="progress-text">
                {{ scanStore.embeddingCurrent }} / {{ scanStore.embeddingTotal }} ({{ embPct }}%)
              </span>
            </div>
            <p class="building">
              {{ scanStore.embeddingLabel || 'Indexing...' }}
              <span v-if="scanStore.embeddingTotal > 0">
                — {{ scanStore.embeddingCurrent }} / {{ scanStore.embeddingTotal }}
              </span>
            </p>
          </template>
          <template v-else-if="scanStore.embeddingPhase === 'complete'">
            <p class="success-msg">Index build complete.</p>
          </template>
          <template v-else-if="scanStore.embeddingPhase === 'error'">
            <p class="error-msg">{{ scanStore.embeddingError }}</p>
          </template>

          <div class="embed-actions">
            <button
              class="btn-primary"
              :disabled="missingCount === 0 || scanStore.embeddingPhase === 'building'"
              @click="buildMissing"
            >
              Build Index ({{ missingCount }} missing)
            </button>
            <button
              class="btn-secondary"
              :disabled="scanStore.embeddingPhase === 'building'"
              @click="rebuildAll"
            >
              Rebuild All
            </button>
            <button
              v-if="scanStore.embeddingPhase === 'building'"
              class="btn-danger"
              @click="scanStore.cancelEmbedding()"
            >
              Cancel
            </button>
          </div>

          <label class="setting-toggle">
            <input
              type="checkbox"
              :checked="settings.auto_index_after_scan"
              @change="toggleAutoIndex(($event.target as HTMLInputElement).checked)"
            />
            Auto-index after scan completes
          </label>

          <label class="setting-toggle">
            <input
              type="checkbox"
              :checked="settings.compute_phash_during_scan"
              @change="toggleComputePhash(($event.target as HTMLInputElement).checked)"
            />
            Compute pHash during scan (used for duplicate detection)
          </label>
        </section>

        <!-- Actions -->
        <div class="dialog-actions">
          <button class="btn-primary" @click="save">Save</button>
          <button class="btn-secondary" @click="emit('close')">Cancel</button>
        </div>
      </template>
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
  min-width: 440px;
  max-width: 520px;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 20px;
  font-size: 18px;
  color: var(--text-color);
}

h4 {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--text-color);
}

.muted {
  color: var(--text-color-secondary);
  font-size: 14px;
}

.form-group {
  margin-bottom: 14px;
}

.form-group > label {
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

.slider-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.slider-row input[type='range'] {
  flex: 1;
  accent-color: var(--primary-color);
}

.slider-value {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
  min-width: 36px;
  text-align: right;
}

.slider-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-color-secondary);
  margin-top: 2px;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-color);
}

.embed-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-border);
}

.embed-section h4 { margin: 0 0 8px; font-size: 14px; color: var(--text-color); }

.stats { font-size: 13px; color: var(--text-color-secondary); margin-bottom: 8px; }
.stats strong { color: var(--text-color); }
.missing { color: var(--danger-color, #d33); margin-left: 6px; }
.building { font-size: 12px; color: var(--primary-color); margin-bottom: 8px; }
.embed-actions { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.setting-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
  margin-bottom: 6px;
}

.progress-section {
  margin-bottom: 12px;
}

.progress-bar {
  height: 8px;
  background: var(--surface-ground);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 4px;
}

.progress-fill {
  height: 100%;
  background: var(--primary-color);
  border-radius: 4px;
  transition: width 0.3s;
}

.progress-text {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.success-msg {
  color: #22c55e;
  font-size: 13px;
  margin-bottom: 8px;
}

.error-msg {
  color: var(--danger-color);
  font-size: 13px;
  margin-bottom: 8px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
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

.btn-primary:hover {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}

.btn-secondary:hover {
  background: var(--surface-hover);
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-danger {
  padding: 8px 20px;
  background: var(--danger-color, #d33);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}

.btn-danger:hover {
  opacity: 0.9;
}
</style>
