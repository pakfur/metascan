<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useModelsStore } from '../../stores/models'
import type { ModelGroup, ModelRow } from '../../api/models'
import { TIER_COLOR, TIER_LABEL } from '../../types/hardware'
import { startRetag } from '../../api/vlm'
import { useTasteStore } from '../../stores/taste'
import { ApiError } from '../../api/client'
import { useWebSocket } from '../../composables/useWebSocket'
import type { TasteWsEvent, WriterWindowStat } from '../../types/taste'

const taste = useTasteStore()
const tasteStatus = computed(() => taste.status)
const tasteTraining = computed(() => tasteStatus.value?.training === true)
const tasteMissing = computed(() => tasteStatus.value?.missing ?? null)
const tasteTrainError = ref<string | null>(null)
const tasteTrainedAt = computed(() => {
  const raw = tasteStatus.value?.active_model?.created_at
  if (!raw) return ''
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? raw : d.toLocaleString()
})
const tasteAuc = computed(() => {
  const auc = tasteStatus.value?.active_model?.holdout_auc
  return typeof auc === 'number' ? auc.toFixed(2) : 'n/a'
})

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

const tasteLoadError = ref<string | null>(null)
async function loadTasteStatus() {
  try {
    await taste.loadStatus()
    tasteLoadError.value = null
  } catch (e) {
    console.error('Failed to load taste status', e)
    tasteLoadError.value = e instanceof Error ? e.message : String(e)
  }
}

async function onTasteRetrain() {
  tasteTrainError.value = null
  try {
    await taste.train()
  } catch (e) {
    // 409 already_training just means a run is in flight; status shows it.
    if (e instanceof ApiError && e.status === 409) {
      await loadTasteStatus()
      const detail = e.detail as { code?: string; missing?: { positives: number; negatives: number } } | null
      if (detail?.code === 'not_enough_data') {
        const m = detail.missing
        tasteTrainError.value = m
          ? `Not enough ratings: need ${plural(m.positives, 'more favorite', 'more favorites')} / ${plural(m.negatives, 'more negative', 'more negatives')} (a negative is a pass, or an image seen in the viewer and not favorited).`
          : 'Not enough ratings to train yet.'
      } else if (detail?.code !== 'already_training') {
        tasteTrainError.value = e.message
      }
    } else {
      tasteTrainError.value = e instanceof Error ? e.message : String(e)
    }
  }
}

// ---- Writers: favorite rate per writer per 14-day window + diversity ----
// The spec's success baseline: the 8B writer with variants, no guidance.
// Rows are per (writer, model): a base draft from another model is not it.
const BASELINE_WRITER = 'base'
const BASELINE_MODEL = 'qwen3vl-8b'
const WRITER_ORDER = ['base', 'base+taste', 'fallback', 'manual']

const writerStats = computed(() => taste.writerStats)
const writerStatsError = ref<string | null>(null)
async function loadWriterStats() {
  try {
    await taste.loadWriterStats()
    writerStatsError.value = null
  } catch (e) {
    console.error('Failed to load writer stats', e)
    writerStatsError.value = e instanceof Error ? e.message : String(e)
  }
}

// Diversity is computed by training runs: refresh when one ends.
useWebSocket('taste', (event) => {
  if (
    event === ('training_complete' satisfies TasteWsEvent) ||
    event === ('training_failed' satisfies TasteWsEvent)
  ) {
    void loadWriterStats()
  }
})

function windowLabel(start: string): string {
  const d = new Date(`${start}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return start
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

function writerRank(id: string): number {
  const i = WRITER_ORDER.indexOf(id)
  if (i >= 0) return i
  return id === 'unknown' ? WRITER_ORDER.length + 1 : WRITER_ORDER.length
}

interface WriterRow {
  key: string
  kind: 't2i' | 'i2v'
  writer: string
  model: string | null
  baseline: boolean
  cells: Record<string, WriterWindowStat>
}

const writerRows = computed<WriterRow[]>(() => {
  const byKey = new Map<string, WriterRow>()
  for (const w of writerStats.value?.windows ?? []) {
    const model = w.writer_model ?? null
    const key = `${w.kind}:${w.writer_id}:${model ?? ''}`
    let row = byKey.get(key)
    if (!row) {
      row = {
        key,
        kind: w.kind,
        writer: w.writer_id,
        model,
        baseline: w.kind === 't2i' && w.writer_id === BASELINE_WRITER && model === BASELINE_MODEL,
        cells: {},
      }
      byKey.set(key, row)
    }
    row.cells[w.window_start] = w
  }
  return [...byKey.values()].sort(
    (a, b) =>
      (a.kind === b.kind ? 0 : a.kind === 't2i' ? -1 : 1) ||
      writerRank(a.writer) - writerRank(b.writer) ||
      a.writer.localeCompare(b.writer) ||
      (a.model === BASELINE_MODEL ? -1 : b.model === BASELINE_MODEL ? 1 : 0) ||
      (a.model ?? '\uffff').localeCompare(b.model ?? '\uffff'),
  )
})

// Diversity is stored by training runs, with that run's window starts.
const diversityAsOf = computed(() => {
  const raw = writerStats.value?.diversity[0]?.computed_at
  if (!raw) return 'the last training run'
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? raw : d.toLocaleDateString()
})

function rateText(cell: WriterWindowStat | undefined): string {
  if (!cell || cell.rate === null) return '—'
  return `${Math.round(cell.rate * 100)}%`
}

function rateTitle(cell: WriterWindowStat | undefined): string {
  if (!cell || cell.n_seen === 0) return 'No seen generations in this window'
  return `${cell.n_fav} favorited of ${cell.n_seen} seen`
}

const models = useModelsStore()

const hfTokenInput = ref('')
const hfTokenMessage = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const hfBusy = ref(false)

const loading = ref(true)

onMounted(async () => {
  try {
    await Promise.all([models.loadStatus(), loadTasteStatus(), loadWriterStats()])
  } finally {
    loading.value = false
  }
})

const groups: ModelGroup[] = ['Embedding', 'Upscaling', 'NLP', 'Tagging (VLM)', 'Writer training']

const tierLabel = computed(() => TIER_LABEL[models.tier])
const tierColor = computed(() => TIER_COLOR[models.tier])

const grouped = computed<Record<string, ModelRow[]>>(() => {
  const out: Record<string, ModelRow[]> = {
    Embedding: [],
    Upscaling: [],
    NLP: [],
    'Tagging (VLM)': [],
    'Writer training': [],
  }
  for (const row of models.models) (out[row.group] ??= []).push(row)
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

// "Download all missing" skips the opt-in writer training assets.
const missingCount = computed(
  () => models.models.filter((m) => m.status === 'missing' && !m.writer_training).length,
)

// Writer LoRA training: the gate's reasons (CUDA, deps, base, converter)
// shown under the group; downloads stay allowed on every machine.
const writerTrainingGate = computed(() => models.gates['writer_training'] ?? null)

function canDelete(row: ModelRow): boolean {
  if (row.writer_training) return row.status === 'available' || row.partial === true
  return row.status === 'available'
}

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

async function onLoad(row: ModelRow) {
  if (!confirm(`Switch active VLM to ${row.name}? Current model will be unloaded.`)) {
    return
  }
  await models.setActiveVlmModel(row.id)
}

async function onUnload(row: ModelRow) {
  if (!confirm(`Unload ${row.name} from VRAM?`)) {
    return
  }
  await models.unloadVlmModel(row.id)
}

type RoleKey = 'writer' | 'general'

const vlmRoles = computed(() => models.vlmRoles)
const vlmRows = computed(() => grouped.value['Tagging (VLM)'] ?? [])
const installedVlms = computed(() => vlmRows.value.filter((r) => r.status === 'available'))
// The backend rejects qwen38-27b as writer (422); don't offer it.
const writerChoices = computed(() => installedVlms.value.filter((r) => r.writer_allowed !== false))
const generalChoices = computed(() => installedVlms.value)

function roleChoices(role: RoleKey): ModelRow[] {
  return role === 'writer' ? writerChoices.value : generalChoices.value
}

// A configured model that is not installed (or not offered) still needs an
// option, otherwise the select would render blank and hide the real value.
function roleOptions(role: RoleKey): { id: string; label: string }[] {
  const out = roleChoices(role).map((r) => ({ id: r.id, label: r.name }))
  const cur = vlmRoles.value?.[role].model_id ?? null
  if (cur && !out.some((o) => o.id === cur)) {
    const row = vlmRows.value.find((r) => r.id === cur)
    out.unshift({ id: cur, label: `${row?.name ?? cur} (not installed)` })
  }
  return out
}

function roleValue(role: RoleKey): string {
  return vlmRoles.value?.[role].model_id ?? ''
}

function roleStateLabel(role: RoleKey): string {
  const r = vlmRoles.value?.[role]
  if (!r || !r.model_id) return 'none'
  switch (r.state) {
    case 'ready': return 'ready'
    case 'spawning':
    case 'loading': return 'loading'
    case 'error': return 'error'
    default: return 'stopped'
  }
}

async function onRoleChange(role: RoleKey, ev: Event) {
  const id = (ev.target as HTMLSelectElement).value
  if (!id) return
  const ok = await models.setVlmRoleModel(role, id)
  // Failure: snap the select back to the server's value.
  if (!ok) (ev.target as HTMLSelectElement).value = roleValue(role)
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

async function onUnloadClip(row: ModelRow) {
  if (!confirm(`Unload ${row.name} from VRAM?`)) {
    return
  }
  await models.stopInferenceWorker()
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
              @change="onTogglePreload(row, $event)"
            />
            <span v-else class="muted">—</span>
          </div>
          <div class="col actions">
            <button
              v-if="row.download_allowed !== false"
              class="btn-small"
              :disabled="row.status === 'available' || !!models.downloading[row.id] || !isModelDownloadable(row)"
              @click="onDownload(row)"
            >
              Download
            </button>
            <button
              v-if="!row.id.startsWith('clip-') && row.managed !== false"
              class="btn-small btn-small-danger"
              :disabled="!canDelete(row) || !!models.downloading[row.id]"
              @click="onDelete(row)"
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
              @click="onLoad(row)"
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
              @click="onUnload(row)"
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
              @click="onUnloadClip(row)"
            >
              Unload
            </button>
          </div>
        </div>
        <div v-if="grouped[group].length === 0" class="empty-msg">No models in this group.</div>
      </div>
      <div
        v-if="group === 'Writer training' && writerTrainingGate && !writerTrainingGate.available"
        class="muted writer-training-gate"
        data-testid="writer-training-gate"
      >
        Prompt-writer training is not available yet:
        <ul>
          <li v-for="r in writerTrainingGate.reasons ?? [writerTrainingGate.reason]" :key="r">{{ r }}</li>
        </ul>
        Downloads work on any machine.
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
      <div class="vlm-roles">
        <label class="role-row">
          <span class="role-label">Prompt writer</span>
          <select
            class="role-select"
            data-testid="role-writer"
            :value="roleValue('writer')"
            @change="onRoleChange('writer', $event)"
          >
            <option v-for="o in roleOptions('writer')" :key="o.id" :value="o.id">{{ o.label }}</option>
            <option v-if="roleOptions('writer').length === 0" value="" disabled>No installed model</option>
          </select>
          <span class="role-state" :class="`role-state-${roleStateLabel('writer')}`" data-testid="role-writer-state">{{ roleStateLabel('writer') }}</span>
        </label>
        <div class="muted role-hint">Fine-tunes build on this model.</div>
        <label class="role-row">
          <span class="role-label">Tagging &amp; chat</span>
          <select
            class="role-select"
            data-testid="role-general"
            :value="roleValue('general')"
            @change="onRoleChange('general', $event)"
          >
            <option v-if="!roleValue('general')" value="" disabled>None</option>
            <option v-for="o in roleOptions('general')" :key="o.id" :value="o.id">{{ o.label }}</option>
          </select>
          <span class="role-state" :class="`role-state-${roleStateLabel('general')}`" data-testid="role-general-state">{{ roleStateLabel('general') }}</span>
        </label>
        <div v-if="vlmRoles?.shared" class="muted role-hint">Sharing one model</div>
        <div v-for="n in vlmRoles?.notes ?? []" :key="n" class="muted role-hint" data-testid="role-note">{{ n }}</div>
        <div v-if="models.vlmRolesError" class="err-text" data-testid="role-error">{{ models.vlmRolesError }}</div>
      </div>
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

    <section class="preload-hint taste-block">
      <div class="section-heading">Taste scorer</div>
      <div v-if="tasteStatus?.active_model" class="taste-model">
        Active model trained {{ tasteTrainedAt }} on
        <b>{{ tasteStatus.active_model.n_pos }}</b> favorites /
        <b>{{ tasteStatus.active_model.n_neg }}</b> negatives
        <span class="taste-hint">(passes, or seen without a favorite)</span>, holdout AUC
        <b>{{ tasteAuc }}</b>.
        <span v-if="tasteStatus.stale" class="err-text">
          Retrain needed (the CLIP model changed).
        </span>
      </div>
      <div v-else-if="tasteStatus" class="taste-model">No taste model yet.</div>
      <div v-if="tasteMissing" class="taste-missing">
        Need {{ plural(tasteMissing.positives, 'more favorite', 'more favorites') }} /
        {{ plural(tasteMissing.negatives, 'more negative', 'more negatives') }}
        <span class="taste-hint">(a pass, or an image seen in the viewer and not favorited)</span>
        <template v-if="tasteStatus?.missing_reason === 'vectors'">
          with a CLIP vector to train: build the similarity index
          (Similarity Settings &rarr; Build Index) and let it finish.
        </template>
        <template v-else>to train.</template>
      </div>
      <div v-if="tasteStatus && (tasteStatus.new_events ?? 0) > 0" class="taste-new">
        {{ plural(tasteStatus.new_events ?? 0, 'new rating', 'new ratings') }} since last training.
      </div>
      <div class="taste-actions">
        <button
          class="btn-small"
          :disabled="tasteTraining || tasteMissing !== null || !tasteStatus"
          @click="onTasteRetrain"
        >
          Retrain now
        </button>
        <span v-if="tasteTraining" class="taste-spinner">
          <i class="pi pi-spin pi-spinner" /> Training…
        </span>
      </div>
      <div v-if="tasteLoadError" class="err-text">
        Could not load taste status: {{ tasteLoadError }}
        <button class="btn-small" @click="loadTasteStatus">Retry</button>
      </div>
      <div v-if="tasteTrainError" class="err-text">{{ tasteTrainError }}</div>
      <div v-if="tasteStatus?.last_error" class="err-text">
        Last error: {{ tasteStatus.last_error }}
      </div>
    </section>

    <section class="preload-hint writers-block">
      <div class="section-heading">
        Writers
        <button class="btn-small writers-refresh" @click="loadWriterStats">Refresh</button>
      </div>
      <div class="taste-hint">
        Favorite rate among generations you have seen (viewed, favorited or passed), by the
        writer that drafted the prompt, per 14-day window of generation date.
      </div>
      <div v-if="writerStats?.collapse_warning" class="warn-banner writers-warning">
        Favorites are getting less diverse (latest window more than 25% below the recent median)
        while the favorite rate rises: the writer may be narrowing toward a few looks.
      </div>
      <div v-if="writerStatsError" class="err-text">
        Could not load writer stats: {{ writerStatsError }}
      </div>
      <template v-if="writerStats">
        <div v-if="writerRows.length === 0" class="taste-hint">
          No generations in the last
          {{ writerStats.window_starts.length * writerStats.window_days }} days.
        </div>
        <div v-else class="writers-scroll">
          <table class="writers-table">
            <thead>
              <tr>
                <th class="writer-col">Writer</th>
                <th v-for="s in writerStats.window_starts" :key="s">{{ windowLabel(s) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in writerRows" :key="row.key" :class="{ baseline: row.baseline }">
                <th class="writer-col" scope="row">
                  {{ row.writer }}
                  <span
                    v-if="row.model || row.writer === 'base' || row.writer === 'base+taste'"
                    class="writer-model"
                    :title="row.model ? `drafted by ${row.model}` : 'model not recorded'"
                    >{{ row.model ?? 'model unknown' }}</span
                  >
                  <span v-if="row.kind === 'i2v'" class="writer-tag">I2V</span>
                  <span v-if="row.baseline" class="writer-tag writer-tag-baseline">baseline</span>
                </th>
                <td
                  v-for="s in writerStats.window_starts"
                  :key="s"
                  :title="rateTitle(row.cells[s])"
                >
                  <span class="rate">{{ rateText(row.cells[s]) }}</span>
                  <span class="n-seen">n={{ row.cells[s]?.n_seen ?? 0 }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="writerStats.diversity.length" class="writers-scroll">
          <table class="writers-table">
            <thead>
              <tr>
                <th class="writer-col">Favorite diversity</th>
                <th v-for="d in writerStats.diversity" :key="d.window_start">
                  {{ windowLabel(d.window_start) }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th class="writer-col" scope="row">
                  <span class="taste-hint">as of {{ diversityAsOf }}</span>
                </th>
                <td
                  v-for="d in writerStats.diversity"
                  :key="d.window_start"
                  :title="`Mean pairwise CLIP distance between ${d.n} favorited T2I images`"
                >
                  <span class="rate">
                    {{ d.mean_distance === null ? '—' : d.mean_distance.toFixed(2) }}
                  </span>
                  <span class="n-seen">n={{ d.n }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="taste-hint">
          Favorite diversity appears after the next taste training run.
        </div>
      </template>
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
.writer-training-gate { margin-top: 6px; font-size: 12px; }
.writer-training-gate ul { margin: 4px 0; padding-left: 18px; }

.gate-chip { margin-left: 4px; }
.gate-good { color: #22c55e; border-color: #22c55e; }
.gate-bad { color: var(--danger-color); border-color: var(--danger-color); }

.vlm-status-block { display: flex; flex-direction: column; gap: 8px; }
.taste-block { display: flex; flex-direction: column; gap: 6px; }
.taste-actions { display: flex; align-items: center; gap: 10px; }
.taste-spinner { display: inline-flex; align-items: center; gap: 6px; }
.taste-hint { color: var(--text-color-secondary); font-size: 12px; }
.writers-block { display: flex; flex-direction: column; gap: 6px; }
.writers-refresh { margin-left: auto; }
.writers-warning { font-size: 12px; }
.writers-scroll { overflow-x: auto; }
.writers-table {
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
}
.writers-table th,
.writers-table td {
  padding: 3px 10px;
  text-align: right;
  white-space: nowrap;
  border-bottom: 1px solid var(--surface-border);
}
.writers-table thead th { font-weight: 600; color: var(--text-color-secondary); }
.writers-table .writer-col { text-align: left; font-weight: 600; }
.writers-table tr.baseline { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
.writers-table .rate { display: inline-block; min-width: 3.2em; }
.writers-table .n-seen { color: var(--text-color-secondary); font-size: 11px; margin-left: 4px; }
.writer-model {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-color-secondary);
}
.writer-tag {
  margin-left: 4px;
  padding: 0 5px;
  border: 1px solid var(--surface-border);
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-color-secondary);
}
.writer-tag-baseline { border-color: var(--primary-color); color: var(--primary-color); }
.vlm-roles { display: flex; flex-direction: column; gap: 6px; }
.role-row { display: flex; align-items: center; gap: 10px; }
.role-label { width: 110px; font-weight: 600; }
.role-select {
  min-width: 220px; padding: 4px 8px; border-radius: 4px;
  background: var(--surface-ground, transparent); color: inherit;
  border: 1px solid var(--surface-border, #444);
}
.role-state { font-size: 12px; color: var(--text-color-secondary); }
.role-state-ready { color: #22c55e; }
.role-state-loading { color: #eab308; }
.role-state-error { color: var(--danger-color); }
.role-hint { font-size: 12px; }
.vlm-current { font-size: 14px; }
.vlm-retag { display: flex; align-items: center; gap: 12px; }
</style>
