<script setup lang="ts">
import { ref, onMounted } from 'vue'
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
const computePhashDuringScan = ref(true)

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

onMounted(async () => {
  loading.value = true
  try {
    settings.value = await fetchSimilaritySettings()
    clipModel.value = settings.value.clip_model
    device.value = settings.value.device
    phashThreshold.value = settings.value.phash_threshold
    clipThreshold.value = settings.value.clip_threshold
    searchResultsCount.value = settings.value.search_results_count
    videoKeyframes.value = settings.value.video_keyframes
    computePhashDuringScan.value = settings.value.compute_phash_during_scan
  } finally {
    loading.value = false
  }
})

async function save() {
  await updateSimilaritySettings({
    clip_model: clipModel.value,
    device: device.value,
    phash_threshold: phashThreshold.value,
    clip_threshold: clipThreshold.value,
    search_results_count: searchResultsCount.value,
    video_keyframes: videoKeyframes.value,
    compute_phash_during_scan: computePhashDuringScan.value,
  })
  emit('close')
}

function buildIndex(rebuild: boolean) {
  scanStore.startEmbeddingBuild(rebuild)
}

const embeddingProgressPct = ref(0)
// Reactively compute from scanStore
import { computed } from 'vue'
const embPct = computed(() =>
  scanStore.embeddingTotal > 0
    ? Math.round((scanStore.embeddingCurrent / scanStore.embeddingTotal) * 100)
    : 0
)
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

        <!-- pHash during scan -->
        <div class="form-group">
          <label class="checkbox-label">
            <input type="checkbox" v-model="computePhashDuringScan" />
            Compute pHash during scan
          </label>
        </div>

        <!-- Embedding Index -->
        <div class="section-divider" />
        <h4>Embedding Index</h4>

        <div v-if="settings?.embedding_stats" class="stats-grid">
          <div class="stat-item">
            <span class="stat-val">{{ settings.embedding_stats.total_media }}</span>
            <span class="stat-lbl">Total Media</span>
          </div>
          <div class="stat-item">
            <span class="stat-val">{{ settings.embedding_stats.embedded }}</span>
            <span class="stat-lbl">Embedded</span>
          </div>
          <div class="stat-item">
            <span class="stat-val">{{ settings.embedding_stats.clip_model || 'none' }}</span>
            <span class="stat-lbl">Model</span>
          </div>
        </div>

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
        </template>
        <template v-else-if="scanStore.embeddingPhase === 'complete'">
          <p class="success-msg">Index build complete.</p>
        </template>
        <template v-else-if="scanStore.embeddingPhase === 'error'">
          <p class="error-msg">{{ scanStore.embeddingError }}</p>
        </template>

        <div class="index-actions">
          <button
            class="btn-secondary"
            @click="buildIndex(false)"
            :disabled="scanStore.embeddingPhase === 'building'"
          >
            Build Index
          </button>
          <button
            class="btn-secondary"
            @click="buildIndex(true)"
            :disabled="scanStore.embeddingPhase === 'building'"
          >
            Rebuild All
          </button>
        </div>

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

.section-divider {
  border-top: 1px solid var(--surface-border);
  margin: 16px 0;
}

.stats-grid {
  display: flex;
  gap: 20px;
  margin-bottom: 12px;
}

.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-val {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary-color);
}

.stat-lbl {
  font-size: 11px;
  color: var(--text-color-secondary);
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

.index-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
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
</style>
