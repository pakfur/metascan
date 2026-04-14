<script setup lang="ts">
import { computed } from 'vue'
import { useScanStore } from '../../stores/scan'

const emit = defineEmits<{
  close: []
}>()

const scanStore = useScanStore()

const fileProgressPct = computed(() =>
  scanStore.fileTotal > 0
    ? Math.round((scanStore.fileCurrent / scanStore.fileTotal) * 100)
    : 0
)

const embeddingProgressPct = computed(() =>
  scanStore.embeddingTotal > 0
    ? Math.round((scanStore.embeddingCurrent / scanStore.embeddingTotal) * 100)
    : 0
)

const currentFileName = computed(() => {
  const f = scanStore.currentFile
  if (!f) return ''
  const parts = f.split('/')
  return parts[parts.length - 1]
})

function handleClose() {
  scanStore.reset()
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="handleClose">
    <div class="dialog-card">
      <!-- Preparing -->
      <template v-if="scanStore.phase === 'preparing'">
        <h3>Preparing Scan...</h3>
        <p class="muted">Counting files in configured directories</p>
      </template>

      <!-- Confirmation -->
      <template v-else-if="scanStore.phase === 'confirming' && scanStore.prepareResult">
        <h3>Scan Directories</h3>

        <div class="dir-list">
          <div
            v-for="dir in scanStore.prepareResult.directories"
            :key="dir.path"
            class="dir-item"
          >
            <span class="dir-path" :title="dir.path">{{ dir.path }}</span>
            <span class="dir-count">{{ dir.file_count }} files</span>
            <span v-if="dir.search_subfolders" class="dir-tag">subfolders</span>
          </div>
        </div>

        <div class="stats-row">
          <span>Total files: <strong>{{ scanStore.prepareResult.total_files }}</strong></span>
          <span>Already in DB: <strong>{{ scanStore.prepareResult.existing_in_db }}</strong></span>
        </div>

        <label class="cleanup-toggle">
          <input type="checkbox" v-model="scanStore.fullCleanup" />
          Full cleanup (remove stale entries)
        </label>

        <div class="dialog-actions">
          <button class="btn-primary" @click="scanStore.start()">Start Scan</button>
          <button class="btn-secondary" @click="handleClose">Cancel</button>
        </div>
      </template>

      <!-- Scanning -->
      <template v-else-if="scanStore.phase === 'scanning'">
        <h3>Scanning...</h3>

        <div v-if="scanStore.dirTotal > 0" class="step-indicator">
          Directory {{ scanStore.dirCurrent }} / {{ scanStore.dirTotal }}
        </div>

        <p v-if="scanStore.currentDir" class="current-dir" :title="scanStore.currentDir">
          {{ scanStore.currentDir }}
        </p>

        <div class="progress-section">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: fileProgressPct + '%' }" />
          </div>
          <span class="progress-text">
            {{ scanStore.fileCurrent }} / {{ scanStore.fileTotal }} files ({{ fileProgressPct }}%)
          </span>
        </div>

        <p v-if="currentFileName" class="current-file" :title="scanStore.currentFile">
          {{ currentFileName }}
        </p>

        <div class="dialog-actions">
          <button class="btn-danger" @click="scanStore.cancel()">Cancel Scan</button>
        </div>
      </template>

      <!-- Stale cleanup -->
      <template v-else-if="scanStore.phase === 'stale_cleanup'">
        <h3>Cleaning Up...</h3>
        <p class="muted">Removing stale database entries for deleted files</p>
        <div class="progress-bar indeterminate">
          <div class="progress-fill" />
        </div>
      </template>

      <!-- Complete -->
      <template v-else-if="scanStore.phase === 'complete'">
        <h3>Scan Complete</h3>

        <div class="result-stats">
          <div class="stat">
            <span class="stat-value">{{ scanStore.processedCount }}</span>
            <span class="stat-label">Files processed</span>
          </div>
          <div v-if="scanStore.staleRemoved > 0" class="stat">
            <span class="stat-value">{{ scanStore.staleRemoved }}</span>
            <span class="stat-label">Stale removed</span>
          </div>
        </div>

        <!-- Embedding prompt -->
        <div class="embedding-prompt">
          <p>Build/update similarity embeddings?</p>
          <div class="dialog-actions">
            <button class="btn-primary" @click="scanStore.startEmbeddingBuild()">
              Build Embeddings
            </button>
            <button class="btn-secondary" @click="handleClose">Close</button>
          </div>
        </div>
      </template>

      <!-- Embedding building -->
      <template v-else-if="scanStore.embeddingPhase === 'building'">
        <h3>Building Embeddings...</h3>

        <div class="progress-section">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: embeddingProgressPct + '%' }" />
          </div>
          <span class="progress-text">
            {{ scanStore.embeddingCurrent }} / {{ scanStore.embeddingTotal }}
            ({{ embeddingProgressPct }}%)
          </span>
        </div>
      </template>

      <!-- Error -->
      <template v-else-if="scanStore.phase === 'error'">
        <h3>Scan Error</h3>
        <p class="error-msg">{{ scanStore.errorMessage }}</p>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>

      <!-- Cancelled -->
      <template v-else-if="scanStore.phase === 'cancelled'">
        <h3>Scan Cancelled</h3>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
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
  min-width: 420px;
  max-width: 560px;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 16px;
  font-size: 18px;
  color: var(--text-color);
}

.muted {
  color: var(--text-color-secondary);
  font-size: 14px;
}

/* Directory list */
.dir-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
  max-height: 200px;
  overflow-y: auto;
}

.dir-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: var(--surface-ground);
  border-radius: 6px;
  font-size: 13px;
}

.dir-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-color);
}

.dir-count {
  color: var(--text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.dir-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
}

/* Stats */
.stats-row {
  display: flex;
  gap: 24px;
  margin-bottom: 12px;
  font-size: 14px;
  color: var(--text-color-secondary);
}

.stats-row strong {
  color: var(--text-color);
}

.cleanup-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
  margin-bottom: 8px;
}

/* Progress */
.step-indicator {
  font-size: 13px;
  color: var(--text-color-secondary);
  margin-bottom: 8px;
}

.current-dir {
  font-size: 13px;
  color: var(--text-color);
  margin-bottom: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-section {
  margin-bottom: 12px;
}

.progress-bar {
  height: 8px;
  background: var(--surface-ground);
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 6px;
}

.progress-fill {
  height: 100%;
  background: var(--primary-color);
  border-radius: 4px;
  transition: width 0.3s;
}

.progress-bar.indeterminate .progress-fill {
  width: 40%;
  animation: indeterminate 1.5s ease-in-out infinite;
}

@keyframes indeterminate {
  0% { margin-left: 0; }
  50% { margin-left: 60%; }
  100% { margin-left: 0; }
}

.progress-text {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.current-file {
  font-size: 12px;
  color: var(--text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Results */
.result-stats {
  display: flex;
  gap: 32px;
  margin-bottom: 20px;
}

.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--primary-color);
}

.stat-label {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.embedding-prompt {
  border-top: 1px solid var(--surface-border);
  padding-top: 16px;
}

.embedding-prompt p {
  font-size: 14px;
  color: var(--text-color);
  margin-bottom: 12px;
}

.error-msg {
  color: var(--danger-color);
  font-size: 14px;
  margin-bottom: 16px;
}

/* Actions */
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

.btn-danger {
  padding: 8px 20px;
  background: var(--danger-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.btn-danger:hover {
  opacity: 0.9;
}
</style>
