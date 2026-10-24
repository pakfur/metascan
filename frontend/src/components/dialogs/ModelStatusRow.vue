<script setup lang="ts">
import type { ModelRow } from '../../api/models'
import { useModelsStore } from '../../stores/models'

// One row of a model table, shared by Config → Models and Config → Training
// (the writer-training assets). It renders the row's state (status and gate
// chips, size, VRAM, preload) and its action buttons. What an action does
// (confirmations, store calls, error text) is the parent's business: every
// button just emits.
defineProps<{ row: ModelRow }>()
const emit = defineEmits<{
  download: [row: ModelRow]
  delete: [row: ModelRow]
  preload: [row: ModelRow, ev: Event]
  load: [row: ModelRow]
  unload: [row: ModelRow]
  unloadClip: [row: ModelRow]
}>()

const models = useModelsStore()

function formatSize(bytes: number | null): string {
  if (!bytes || bytes <= 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb.toFixed(1)} MB`
  return `${(mb / 1024).toFixed(2)} GB`
}

function statusLabel(row: ModelRow): { label: string; color: string } {
  if (models.downloading[row.id]) {
    const p = models.downloading[row.id]
    const pct = Math.round((p.percent || 0) * 100)
    return {
      label: pct > 0 ? `${p.stage} ${pct}%` : p.stage,
      color: 'var(--primary-color)',
    }
  }
  if (models.downloadErrors[row.id]) {
    return { label: 'error', color: 'var(--danger-color)' }
  }
  if (row.status === 'available') return { label: 'ready', color: '#22c55e' }
  if (row.status === 'missing') return { label: 'missing', color: 'var(--text-color-secondary)' }
  return { label: row.status, color: 'var(--text-color-secondary)' }
}

function gateChip(row: ModelRow): string | null {
  const g = models.gates[row.id]
  if (!g) return null
  if (!g.available) return 'unsupported'
  if (g.recommended) return 'recommended'
  return null
}

function gateChipClass(row: ModelRow): string {
  const g = models.gates[row.id]
  if (!g) return ''
  if (!g.available) return 'gate-bad'
  if (g.recommended) return 'gate-good'
  return ''
}

function isModelDownloadable(row: ModelRow): boolean {
  // We still allow downloading models the gate marks unavailable —
  // sometimes the user wants the weights even though they can't run
  // them efficiently. Only block downloads when the model has a
  // hard environmental block (e.g. punkt vs punkt_tab on new NLTK).
  const g = models.gates[row.id]
  if (!g) return true
  // Block only NLTK punkt variants where the registered id is wrong
  // for the running NLTK version — downloading the wrong one is
  // pointless.
  if (row.id === 'nltk-punkt' || row.id === 'nltk-punkt-tab') {
    return g.available
  }
  return true
}

function canDelete(row: ModelRow): boolean {
  if (row.writer_training) return row.status === 'available' || row.partial === true
  return row.status === 'available'
}

function isVlmLoaded(row: ModelRow): boolean {
  return (
    models.vlmModelId === row.id &&
    models.vlmState !== 'idle' &&
    models.vlmState !== 'stopped'
  )
}

function isClipLoaded(row: ModelRow): boolean {
  // Row ids are `clip-<key>` while the inference worker reports the bare
  // model key (e.g. "small" / "large"). Trim the prefix to compare.
  const key = row.id.startsWith('clip-') ? row.id.slice('clip-'.length) : row.id
  return (
    models.inferenceModelKey === key &&
    models.inferenceState !== 'idle' &&
    models.inferenceState !== 'stopped'
  )
}
</script>

<template>
  <div class="trow">
    <div class="col name">
      <div class="name-line">{{ row.name }}</div>
      <div class="desc">{{ row.description }}</div>
      <div v-if="row.writer_training && row.release" class="desc" data-testid="converter-release">
        llama.cpp {{ row.release }}
        <span v-if="row.release_mismatch" class="warn-inline">
          — different from the server release ({{ row.server_release }}); download again to match.
        </span>
      </div>
      <div v-if="row.writer_training && row.hint" class="desc" data-testid="writer-training-hint">
        {{ row.hint }}
      </div>
    </div>
    <div class="col status">
      <span class="chip" :style="{ color: statusLabel(row).color, borderColor: statusLabel(row).color }">
        {{ statusLabel(row).label }}
      </span>
      <span
        v-if="gateChip(row)"
        class="chip gate-chip"
        :class="gateChipClass(row)"
        :title="row.id in models.gates ? models.gates[row.id].reason : ''"
      >
        {{ gateChip(row) }}
      </span>
      <div v-if="models.downloadErrors[row.id]" class="err-text" :title="models.downloadErrors[row.id]">
        {{ models.downloadErrors[row.id] }}
      </div>
    </div>
    <div class="col size">{{ formatSize(row.size_bytes) }}</div>
    <div class="col vram">
      {{ row.required_vram_mb ? `${row.required_vram_mb} MB` : '—' }}
    </div>
    <div class="col preload">
      <input
        v-if="!row.writer_training"
        type="checkbox"
        :checked="row.preload_at_startup"
        @change="emit('preload', row, $event)"
      />
      <span v-else class="muted">—</span>
    </div>
    <div class="col actions">
      <button
        v-if="row.download_allowed !== false"
        class="btn-small"
        :disabled="row.status === 'available' || !!models.downloading[row.id] || !isModelDownloadable(row)"
        @click="emit('download', row)"
      >
        Download
      </button>
      <button
        v-if="!row.id.startsWith('clip-') && row.managed !== false"
        class="btn-small btn-small-danger"
        :disabled="!canDelete(row) || !!models.downloading[row.id]"
        @click="emit('delete', row)"
      >
        Delete
      </button>
      <button
        v-if="row.is_vlm"
        class="btn-small"
        :disabled="row.status !== 'available' || isVlmLoaded(row)"
        :title="row.status !== 'available'
          ? 'Download the model first.'
          : isVlmLoaded(row)
            ? 'This is the active VLM.'
            : 'Load this model into VRAM.'"
        @click="emit('load', row)"
      >
        {{ isVlmLoaded(row) ? 'Loaded' : 'Load' }}
      </button>
      <button
        v-if="row.is_vlm"
        class="btn-small"
        :disabled="!isVlmLoaded(row)"
        :title="isVlmLoaded(row)
          ? 'Unload this model from VRAM.'
          : 'Model is not currently loaded.'"
        @click="emit('unload', row)"
      >
        Unload
      </button>
      <button
        v-if="row.id.startsWith('clip-')"
        class="btn-small"
        :disabled="!isClipLoaded(row)"
        :title="isClipLoaded(row)
          ? 'Unload the CLIP inference worker. It will respawn on the next content search.'
          : 'This CLIP model is not currently loaded.'"
        @click="emit('unloadClip', row)"
      >
        Unload
      </button>
    </div>
  </div>
</template>

<style scoped>
.muted { color: var(--text-color-secondary); font-size: 13px; }

.chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 11px;
  text-transform: lowercase;
}

/* The column template must match .thead in ConfigModelsTab / ConfigTrainingTab. */
.trow {
  display: grid;
  grid-template-columns: 2fr 1.1fr 0.8fr 0.7fr 0.6fr 1.2fr;
  gap: 8px;
  align-items: center;
  padding: 6px 4px;
  font-size: 12.5px;
  border-bottom: 1px solid var(--surface-border);
}
.trow:last-child { border-bottom: none; }
.col { overflow: hidden; }
.col.name .name-line { color: var(--text-color); font-weight: 500; }
.col.name .desc { font-size: 11.5px; color: var(--text-color-secondary); margin-top: 2px; }

.err-text {
  font-size: 11px; color: var(--danger-color); margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.btn-small {
  padding: 3px 8px;
  border: 1px solid var(--surface-border);
  background: var(--surface-ground);
  color: var(--text-color);
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  margin-right: 4px;
}
.btn-small:hover:not(:disabled) { background: var(--surface-hover); }
.btn-small:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-small-danger:hover:not(:disabled) {
  border-color: var(--danger-color);
  color: var(--danger-color);
}

.warn-inline { color: #eab308; }

.gate-chip { margin-left: 4px; }
.gate-good { color: #22c55e; border-color: #22c55e; }
.gate-bad { color: var(--danger-color); border-color: var(--danger-color); }
</style>
