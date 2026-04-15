<script setup lang="ts">
import { computed } from 'vue'
import { useScanStore } from '../../stores/scan'

const emit = defineEmits<{ close: [] }>()
const scanStore = useScanStore()

const steps = [
  { key: 'preparing', label: 'Prepare' },
  { key: 'confirming', label: 'Confirm' },
  { key: 'scanning', label: 'Scan' },
  { key: 'embedding', label: 'Embed' },
  { key: 'complete', label: 'Done' },
]

function stepIndex(phase: string): number {
  switch (phase) {
    case 'preparing': return 0
    case 'confirming': return 1
    case 'scanning':
    case 'stale_cleanup': return 2
    case 'embedding': return 3
    case 'complete':
    case 'cancelled':
    case 'error': return 4
    default: return 0
  }
}
const activeStep = computed(() => stepIndex(scanStore.phase))

const fileProgressPct = computed(() =>
  scanStore.fileTotal > 0
    ? Math.round((scanStore.fileCurrent / scanStore.fileTotal) * 100)
    : 0,
)
const embeddingProgressPct = computed(() =>
  scanStore.embeddingTotal > 0
    ? Math.round((scanStore.embeddingCurrent / scanStore.embeddingTotal) * 100)
    : 0,
)

const currentFileName = computed(() => {
  const f = scanStore.currentFile
  if (!f) return ''
  const parts = f.split('/')
  return parts[parts.length - 1]
})

const embedFileName = computed(() => {
  const f = scanStore.embeddingCurrentFile
  if (!f) return ''
  const parts = f.split('/')
  return parts[parts.length - 1]
})

const embedHumanLabel = computed(() => {
  switch (scanStore.embeddingStatus) {
    case 'downloading_model': return 'Downloading CLIP model...'
    case 'loading_model': return 'Loading CLIP model...'
    case 'starting': return 'Starting...'
    case 'processing':
      return `Indexing ${scanStore.embeddingCurrent} / ${scanStore.embeddingTotal}` +
        (embedFileName.value ? ` — ${embedFileName.value}` : '') +
        (scanStore.embeddingErrorsCount > 0
          ? ` (${scanStore.embeddingErrorsCount} errors)`
          : '')
    default: return scanStore.embeddingLabel || ''
  }
})

function handleClose() {
  scanStore.reset()
  scanStore.resetEmbedding()
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="handleClose">
    <div class="dialog-card">
      <ol class="step-tracker">
        <li
          v-for="(step, idx) in steps"
          :key="step.key"
          :class="{ done: idx < activeStep, active: idx === activeStep }"
        >
          <span class="step-circle">{{ idx < activeStep ? '✓' : idx + 1 }}</span>
          <span class="step-label">{{ step.label }}</span>
        </li>
      </ol>

      <template v-if="scanStore.phase === 'preparing'">
        <h3>Preparing Scan...</h3>
        <p class="muted">Counting files in configured directories</p>
        <div class="progress-bar indeterminate"><div class="progress-fill" /></div>
      </template>

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
          <span>Total: <strong>{{ scanStore.prepareResult.total_files }}</strong></span>
          <span>In DB: <strong>{{ scanStore.prepareResult.existing_in_db }}</strong></span>
        </div>

        <fieldset class="mode-fieldset">
          <legend>Mode</legend>
          <label class="mode-radio">
            <input type="radio" value="incremental" v-model="scanStore.scanMode" />
            <span>
              <strong>Incremental</strong>
              <em>Skip files already in the database</em>
            </span>
          </label>
          <label class="mode-radio">
            <input type="radio" value="full_clean" v-model="scanStore.scanMode" />
            <span>
              <strong>Full clean &amp; rescan</strong>
              <em>Wipe the database and rescan every file. Favorites are preserved.</em>
            </span>
          </label>
        </fieldset>

        <label class="cleanup-toggle">
          <input type="checkbox" v-model="scanStore.fullCleanup" />
          Also remove DB entries for files that no longer exist on disk
        </label>

        <div class="dialog-actions">
          <button class="btn-primary" @click="scanStore.start()">Start Scan</button>
          <button class="btn-secondary" @click="handleClose">Cancel</button>
        </div>
      </template>

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

      <template v-else-if="scanStore.phase === 'stale_cleanup'">
        <h3>Cleaning Up...</h3>
        <p class="muted">Removing stale database entries for deleted files</p>
        <div class="progress-bar indeterminate"><div class="progress-fill" /></div>
      </template>

      <template v-else-if="scanStore.phase === 'embedding'">
        <h3>Building Embeddings...</h3>
        <p class="muted">{{ embedHumanLabel }}</p>
        <div class="progress-section">
          <div
            class="progress-bar"
            :class="{
              indeterminate:
                scanStore.embeddingStatus === 'downloading_model' ||
                scanStore.embeddingStatus === 'loading_model' ||
                scanStore.embeddingStatus === 'starting',
            }"
          >
            <div
              class="progress-fill"
              :style="
                scanStore.embeddingStatus === 'processing'
                  ? { width: embeddingProgressPct + '%' }
                  : undefined
              "
            />
          </div>
          <span
            v-if="scanStore.embeddingStatus === 'processing'"
            class="progress-text"
          >
            {{ embeddingProgressPct }}%
          </span>
        </div>
        <div class="dialog-actions">
          <button class="btn-danger" @click="scanStore.cancelEmbedding()">
            Cancel Embedding
          </button>
        </div>
      </template>

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
          <div v-if="scanStore.embeddingTotal > 0" class="stat">
            <span class="stat-value">{{ scanStore.embeddingCurrent }}</span>
            <span class="stat-label">Embeddings built</span>
          </div>
        </div>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>

      <template v-else-if="scanStore.phase === 'error'">
        <h3>Scan Error</h3>
        <p class="error-msg">{{ scanStore.errorMessage }}</p>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="handleClose">Close</button>
        </div>
      </template>

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
  position: fixed; inset: 0; z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center;
}
.dialog-card {
  background: var(--surface-section); border-radius: 12px;
  padding: 24px 28px; min-width: 480px; max-width: 600px;
  max-height: 80vh; overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.step-tracker { display: flex; gap: 4px; list-style: none; margin: 0 0 24px; padding: 0; }
.step-tracker li {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  gap: 6px; position: relative; font-size: 11px; color: var(--text-color-secondary);
}
.step-tracker li::after {
  content: ''; position: absolute; top: 12px; left: 60%;
  width: 80%; height: 2px; background: var(--surface-border); z-index: 0;
}
.step-tracker li:last-child::after { display: none; }
.step-circle {
  position: relative; z-index: 1; width: 24px; height: 24px;
  border-radius: 50%; background: var(--surface-ground);
  border: 2px solid var(--surface-border);
  display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 11px; color: var(--text-color-secondary);
}
.step-tracker li.done .step-circle {
  background: var(--primary-color); border-color: var(--primary-color); color: #fff;
}
.step-tracker li.active .step-circle {
  background: var(--surface-section); border-color: var(--primary-color); color: var(--primary-color);
}
.step-tracker li.active .step-label,
.step-tracker li.done .step-label { color: var(--text-color); }

h3 { margin: 0 0 12px; font-size: 18px; color: var(--text-color); }
.muted { color: var(--text-color-secondary); font-size: 13px; margin-bottom: 12px; }

.dir-list {
  display: flex; flex-direction: column; gap: 6px;
  margin-bottom: 16px; max-height: 200px; overflow-y: auto;
}
.dir-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; background: var(--surface-ground);
  border-radius: 6px; font-size: 13px;
}
.dir-path { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-color); }
.dir-count { color: var(--text-color-secondary); font-size: 12px; white-space: nowrap; }
.dir-tag { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: color-mix(in srgb, var(--primary-color) 15%, transparent); color: var(--primary-color); }

.stats-row { display: flex; gap: 24px; margin-bottom: 12px; font-size: 14px; color: var(--text-color-secondary); }
.stats-row strong { color: var(--text-color); }

.mode-fieldset {
  border: 1px solid var(--surface-border); border-radius: 8px;
  padding: 10px 14px; margin: 8px 0; background: var(--surface-ground);
}
.mode-fieldset legend { padding: 0 6px; font-size: 12px; color: var(--text-color-secondary); }
.mode-radio { display: flex; gap: 10px; padding: 4px 0; cursor: pointer; }
.mode-radio span { display: flex; flex-direction: column; }
.mode-radio strong { font-size: 13px; color: var(--text-color); }
.mode-radio em { font-size: 11px; color: var(--text-color-secondary); font-style: normal; }

.cleanup-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-color); cursor: pointer; margin-bottom: 8px; }

.step-indicator { font-size: 13px; color: var(--text-color-secondary); margin-bottom: 8px; }
.current-dir, .current-file {
  font-size: 12px; color: var(--text-color-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-bottom: 8px;
}
.current-dir { color: var(--text-color); }

.progress-section { margin-bottom: 12px; }
.progress-bar { height: 8px; background: var(--surface-ground); border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
.progress-fill { height: 100%; background: var(--primary-color); border-radius: 4px; transition: width 0.3s; }
.progress-bar.indeterminate .progress-fill { width: 40%; animation: indeterminate 1.5s ease-in-out infinite; }
@keyframes indeterminate { 0% { margin-left: 0; } 50% { margin-left: 60%; } 100% { margin-left: 0; } }
.progress-text { font-size: 12px; color: var(--text-color-secondary); }

.result-stats { display: flex; gap: 32px; margin-bottom: 20px; }
.stat { display: flex; flex-direction: column; align-items: center; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--primary-color); }
.stat-label { font-size: 12px; color: var(--text-color-secondary); }

.error-msg { color: var(--danger-color, #d33); font-size: 14px; margin-bottom: 16px; }

.dialog-actions { display: flex; gap: 10px; margin-top: 16px; }
.btn-primary, .btn-secondary, .btn-danger {
  padding: 8px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; border: none;
}
.btn-primary { background: var(--primary-color); color: #fff; font-weight: 600; }
.btn-primary:hover { opacity: 0.9; }
.btn-secondary { background: var(--surface-ground); border: 1px solid var(--surface-border); color: var(--text-color); }
.btn-secondary:hover { background: var(--surface-hover); }
.btn-danger { background: var(--danger-color, #d33); color: #fff; font-weight: 600; }
.btn-danger:hover { opacity: 0.9; }
</style>
