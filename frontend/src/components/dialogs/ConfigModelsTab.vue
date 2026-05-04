<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useModelsStore } from '../../stores/models'
import type { ModelGroup, ModelRow } from '../../api/models'
import { TIER_COLOR, TIER_LABEL } from '../../types/hardware'
import { startRetag } from '../../api/vlm'

const models = useModelsStore()

const hfTokenInput = ref('')
const hfTokenMessage = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const hfBusy = ref(false)

const loading = ref(true)

onMounted(async () => {
  try {
    await models.loadStatus()
  } finally {
    loading.value = false
  }
})

const groups: ModelGroup[] = ['Embedding', 'Upscaling', 'NLP', 'Tagging (Qwen3-VL)']

const tierLabel = computed(() => TIER_LABEL[models.tier])
const tierColor = computed(() => TIER_COLOR[models.tier])

const grouped = computed<Record<ModelGroup, ModelRow[]>>(() => {
  const out: Record<ModelGroup, ModelRow[]> = { Embedding: [], Upscaling: [], NLP: [], 'Tagging (Qwen3-VL)': [] }
  for (const row of models.models) out[row.group].push(row)
  return out
})

const dimMismatchIndexDim = ref<number | null>(null)
// The dim mismatch banner is populated opportunistically when the user
// tries a content search and the server returns 409. That plumbing lives
// in the search bar component; we just render if the store exposes it.
// TODO: expose a shared state once we have the first end-to-end test.
void dimMismatchIndexDim

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

async function onTogglePreload(row: ModelRow, ev: Event) {
  const target = ev.target as HTMLInputElement
  try {
    await models.togglePreload(row.id, target.checked)
  } catch (e) {
    target.checked = !target.checked
    hfTokenMessage.value = { kind: 'err', text: e instanceof Error ? e.message : String(e) }
  }
}

async function onDownload(row: ModelRow) {
  await models.startDownload(row.id)
}

async function onDelete(row: ModelRow) {
  if (!confirm(`Delete cached weights for ${row.name}?`)) return
  try {
    await models.removeCache(row.id)
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}

async function onDownloadAllMissing() {
  await models.startDownloadAllMissing()
}

async function onSaveHfToken() {
  hfBusy.value = true
  hfTokenMessage.value = null
  try {
    await models.saveHfToken(hfTokenInput.value.trim())
    hfTokenInput.value = ''
    hfTokenMessage.value = { kind: 'ok', text: 'Token saved.' }
  } catch (e) {
    hfTokenMessage.value = { kind: 'err', text: e instanceof Error ? e.message : String(e) }
  } finally {
    hfBusy.value = false
  }
}

async function onClearHfToken() {
  if (!confirm('Remove the stored HuggingFace token?')) return
  hfBusy.value = true
  try {
    await models.removeHfToken()
    hfTokenMessage.value = { kind: 'ok', text: 'Token removed.' }
  } catch (e) {
    hfTokenMessage.value = { kind: 'err', text: e instanceof Error ? e.message : String(e) }
  } finally {
    hfBusy.value = false
  }
}

async function onTestHfToken() {
  hfBusy.value = true
  hfTokenMessage.value = null
  try {
    const r = await models.verifyHfToken()
    if (r.ok) {
      hfTokenMessage.value = { kind: 'ok', text: `Authenticated as ${r.name ?? 'user'}` }
    } else {
      hfTokenMessage.value = { kind: 'err', text: r.error ?? 'Token rejected' }
    }
  } catch (e) {
    hfTokenMessage.value = { kind: 'err', text: e instanceof Error ? e.message : String(e) }
  } finally {
    hfBusy.value = false
  }
}

const missingCount = computed(() => models.models.filter((m) => m.status === 'missing').length)

// Banner: the currently-selected CLIP model's dim vs the index on disk.
const indexDim = computed(() => {
  const stats = models.models.find((m) => m.id === `clip-${models.currentClipModel}`)
  return stats?.embedding_dim ?? null
})

// If the inference worker is initialized with a model_key that differs from
// the currently-selected one, surface that — it means the user changed
// clip_model but the worker is still running the old one.
const inferenceMismatch = computed(
  () =>
    models.inferenceModelKey &&
    models.inferenceModelKey !== models.currentClipModel,
)

async function onRebuildIndex() {
  if (!confirm('Rebuild the embedding index with the current CLIP model? This may take a while.')) return
  try {
    await models.rebuildIndex()
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}

async function onActivate(row: ModelRow) {
  if (!confirm(`Switch active VLM to ${row.name}? Current model will be unloaded.`)) {
    return
  }
  await models.setActiveVlmModel(row.id)
}

const retagInFlight = ref(false)
const lastRetagTotal = ref<number | null>(null)

async function onRetagLibrary() {
  if (!confirm(
    'Re-tag every CLIP-tagged file with Qwen3-VL? This may take hours on large libraries.'
  )) {
    return
  }
  retagInFlight.value = true
  try {
    const job = await startRetag({ scope: 'all_clip' })
    lastRetagTotal.value = job.total
  } catch (e) {
    alert(`Re-tag failed: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    retagInFlight.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="muted">Loading model status…</div>
  <template v-else>
    <section class="hw-block">
      <div class="hw-row">
        <div class="hw-tier" :style="{ borderColor: tierColor, color: tierColor }">
          {{ tierLabel }}
        </div>
        <div class="hw-value" v-if="models.hardware">
          <span>{{ models.hardware.report.os }}</span>
          <span>{{ models.hardware.report.machine }}</span>
          <span>CPUs: {{ models.hardware.report.cpu_count ?? '—' }}</span>
          <span v-if="models.hardware.report.ram_gb">
            RAM: {{ models.hardware.report.ram_gb }} GB
          </span>
          <span v-if="models.hardware.report.cuda">
            CUDA: {{ models.hardware.report.cuda.name }}
            ({{ models.hardware.report.cuda.vram_gb }} GB)
          </span>
          <span v-else-if="models.hardware.report.mps">MPS available</span>
          <span v-else>CPU only</span>
          <span v-if="models.hardware.report.vulkan?.has_real_device">Vulkan ✓</span>
          <span v-else-if="models.hardware.report.vulkan?.available" class="warn-inline">
            Vulkan: software only
          </span>
        </div>
      </div>
      <div
        v-for="(w, i) in (models.hardware?.report.warnings ?? [])"
        :key="i"
        class="hw-warning"
      >
        {{ w }}
      </div>
    </section>

    <section class="hf-block">
      <div class="section-heading">
        <span>HuggingFace token</span>
        <span class="chip" :class="models.hfTokenSet ? 'chip-ok' : 'chip-muted'">
          {{ models.hfTokenSet ? 'configured' : 'not set' }}
        </span>
      </div>
      <div class="hf-row">
        <input
          v-model="hfTokenInput"
          type="password"
          class="token-input"
          placeholder="hf_XXXXXXXXXXXXXXXXXXXXXX"
          :disabled="hfBusy"
          @keydown.enter="onSaveHfToken"
        />
        <button class="btn-secondary" @click="onSaveHfToken" :disabled="!hfTokenInput.trim() || hfBusy">
          Save
        </button>
        <button class="btn-secondary" @click="onTestHfToken" :disabled="!models.hfTokenSet || hfBusy">
          Test
        </button>
        <button class="btn-danger" @click="onClearHfToken" :disabled="!models.hfTokenSet || hfBusy">
          Clear
        </button>
      </div>
      <div v-if="hfTokenMessage" class="hf-msg" :class="hfTokenMessage.kind === 'ok' ? 'ok' : 'err'">
        {{ hfTokenMessage.text }}
      </div>
    </section>

    <section v-if="inferenceMismatch" class="warn-banner">
      Content-search worker is running CLIP <b>{{ models.inferenceModelKey }}</b>
      but the selected model is <b>{{ models.currentClipModel }}</b>. Save
      similarity settings again to reload the worker.
    </section>

    <section class="bulk-row">
      <div class="section-heading">
        <span>Models ({{ missingCount }} missing)</span>
        <div class="bulk-actions">
          <button class="btn-secondary" :disabled="missingCount === 0" @click="onDownloadAllMissing">
            Download all missing
          </button>
          <button class="btn-secondary" @click="onRebuildIndex">Rebuild index</button>
        </div>
      </div>
    </section>

    <section v-for="group in groups" :key="group" class="group-block">
      <div class="group-title">{{ group }}</div>
      <div class="table">
        <div class="thead">
          <div class="col name">Model</div>
          <div class="col status">Status</div>
          <div class="col size">Size</div>
          <div class="col vram">VRAM</div>
          <div class="col preload">Preload</div>
          <div class="col actions">Actions</div>
        </div>
        <div v-for="row in grouped[group]" :key="row.id" class="trow">
          <div class="col name">
            <div class="name-line">{{ row.name }}</div>
            <div class="desc">{{ row.description }}</div>
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
              type="checkbox"
              :checked="row.preload_at_startup"
              @change="onTogglePreload(row, $event)"
            />
          </div>
          <div class="col actions">
            <button
              class="btn-small"
              :disabled="row.status === 'available' || !!models.downloading[row.id] || !isModelDownloadable(row)"
              @click="onDownload(row)"
            >
              Download
            </button>
            <button
              v-if="!row.id.startsWith('clip-')"
              class="btn-small btn-small-danger"
              :disabled="row.status !== 'available'"
              @click="onDelete(row)"
            >
              Delete
            </button>
            <button
              v-if="row.id.startsWith('qwen3vl-')"
              class="btn-small"
              :disabled="row.status !== 'available' || models.vlmModelId === row.id"
              :title="row.status !== 'available'
                ? 'Download the model first.'
                : models.vlmModelId === row.id
                  ? 'This is the active VLM.'
                  : 'Switch the loaded VLM to this model.'"
              @click="onActivate(row)"
            >
              {{ models.vlmModelId === row.id ? 'Active' : 'Activate' }}
            </button>
          </div>
        </div>
        <div v-if="grouped[group].length === 0" class="empty-msg">No models in this group.</div>
      </div>
    </section>

    <section class="preload-hint">
      <div v-if="indexDim">
        Current CLIP model: <b>{{ models.currentClipModel }}</b> (dim {{ indexDim }}).
        Inference worker state: <b>{{ models.inferenceState }}</b>
        <span v-if="models.inferenceDevice"> on {{ models.inferenceDevice }}</span>.
      </div>
    </section>

    <section class="preload-hint vlm-status-block">
      <div v-if="models.vlmState !== 'idle'" class="vlm-current">
        <b>VLM tagger:</b>
        <span v-if="models.vlmState === 'ready'">
          Active — <b>{{ models.vlmModelId }}</b>
        </span>
        <span v-else-if="models.isVlmLoading">
          Loading <b>{{ models.vlmModelId }}</b>…
        </span>
        <span v-else-if="models.vlmState === 'error'" class="err-text">
          Error: {{ models.vlmError ?? 'unknown' }}
        </span>
        <span v-else>{{ models.vlmState }}</span>
      </div>
      <div class="vlm-retag">
        <button
          class="btn-small"
          :disabled="!models.isVlmReady || retagInFlight"
          @click="onRetagLibrary"
        >
          {{ retagInFlight ? 'Re-tagging library…' : 'Re-tag library with VLM' }}
        </button>
        <span v-if="lastRetagTotal !== null" class="muted">
          Started {{ lastRetagTotal }} files; track progress in scan logs.
        </span>
      </div>
    </section>
  </template>
</template>

<style scoped>
.muted { color: var(--text-color-secondary); font-size: 13px; }

section { margin-bottom: 14px; }

.section-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
}

.bulk-actions { margin-left: auto; display: flex; gap: 6px; }

.hw-block, .hf-block, .preload-hint, .bulk-row, .group-block, .warn-banner {
  border: 1px solid var(--surface-border);
  background: var(--surface-card);
  padding: 10px 12px;
  border-radius: 8px;
}

.warn-banner {
  border-color: #eab308;
  background: rgba(234, 179, 8, 0.08);
  color: var(--text-color);
  font-size: 12.5px;
}

.hw-value {
  display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--text-color);
}

.hf-row {
  display: flex; gap: 8px; align-items: center;
}
.token-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 13px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.token-input:focus { outline: none; border-color: var(--primary-color); }

.hf-msg { font-size: 12px; margin-top: 6px; }
.hf-msg.ok { color: #22c55e; }
.hf-msg.err { color: var(--danger-color); }

.chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 11px;
  text-transform: lowercase;
}
.chip-ok { color: #22c55e; }
.chip-muted { color: var(--text-color-secondary); }

.group-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}

.table { display: flex; flex-direction: column; }
.thead, .trow {
  display: grid;
  grid-template-columns: 2fr 1.1fr 0.8fr 0.7fr 0.6fr 1.2fr;
  gap: 8px;
  align-items: center;
  padding: 6px 4px;
  font-size: 12.5px;
}
.thead {
  font-size: 11px;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--surface-border);
}
.trow { border-bottom: 1px solid var(--surface-border); }
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

.btn-secondary {
  padding: 5px 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 12.5px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) { background: var(--surface-hover); }
.btn-secondary:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-danger {
  padding: 5px 12px;
  background: transparent;
  border: 1px solid var(--danger-color);
  border-radius: 6px;
  color: var(--danger-color);
  font-size: 12.5px;
  cursor: pointer;
}
.btn-danger:disabled { opacity: 0.35; cursor: not-allowed; }

.empty-msg { padding: 10px; color: var(--text-color-secondary); font-size: 12px; text-align: center; }

.preload-hint { font-size: 12px; color: var(--text-color-secondary); }

.hw-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}

.hw-tier {
  padding: 3px 12px;
  border: 1.5px solid currentColor;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.hw-warning {
  margin-top: 6px;
  font-size: 11.5px;
  color: #eab308;
  background: rgba(234, 179, 8, 0.08);
  padding: 4px 8px;
  border-radius: 4px;
}

.warn-inline { color: #eab308; }

.gate-chip { margin-left: 4px; }
.gate-good { color: #22c55e; border-color: #22c55e; }
.gate-bad { color: var(--danger-color); border-color: var(--danger-color); }

.vlm-status-block { display: flex; flex-direction: column; gap: 8px; }
.vlm-current { font-size: 14px; }
.vlm-retag { display: flex; align-items: center; gap: 12px; }
</style>
