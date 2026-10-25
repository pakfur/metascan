<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useModelsStore } from '../../stores/models'
import type { ModelRow } from '../../api/models'
import { useTasteStore } from '../../stores/taste'
import { ApiError } from '../../api/client'
import { useWebSocket } from '../../composables/useWebSocket'
import type { TasteReembedEvent, TasteWsEvent, WriterWindowStat } from '../../types/taste'
import { useTrainingStore, trainingErrorText } from '../../stores/training'
import { useCategoriesStore } from '../../stores/categories'
import { ratePct } from '../../types/writers'
import ModelStatusRow from './ModelStatusRow.vue'

const models = useModelsStore()

const loading = ref(true)

onMounted(async () => {
  try {
    await Promise.all([models.loadStatus(), loadTasteStatus(), loadWriterStats()])
  } finally {
    loading.value = false
  }
})

// ---- Writer training: base weights, converter, gate, adapters ----
// The two writer-training assets (base weights, llama.cpp converter) are the
// only model rows on this tab; every other model is listed under Config → Models.
const trainingAssetRows = computed<ModelRow[]>(() =>
  models.models.filter((m) => m.writer_training),
)

async function onDownload(row: ModelRow) {
  await models.startDownload(row.id)
}

async function onDelete(row: ModelRow) {
  if (!confirm(`Delete cached weights for ${row.name}?`)) return
  try {
    await models.removeCache(row.id)
  } catch (e) {
    // 409 training_uses_base (a paused / failed run still needs the base
    // weights) and gpu_busy_training get their own text.
    alert(trainingErrorText(e))
  }
}

// Writer LoRA training: the gate's reasons (CUDA, deps, base, converter)
// shown under the group; downloads stay allowed on every machine.
const writerTrainingGate = computed(() => models.gates['writer_training'] ?? null)

// The adapters trained so far and the button that opens the training window
// (which manages them: rename, delete, resume).
const training = useTrainingStore()
const categoriesStore = useCategoriesStore()
function adapterScope(scope: string): string {
  if (scope === 'all') return 'all'
  return categoriesStore.categories.find((c) => String(c.id) === scope)?.name ?? `category ${scope}`
}
const writerAdapters = computed(() => [...training.adapters].sort((a, b) => b.id - a.id))
// Re-run the training dependency probe; its gate_changed event reloads the rows.
const rechecking = ref(false)
async function recheckTraining(): Promise<void> {
  rechecking.value = true
  try {
    await training.load(true)
  } catch (e) {
    console.warn('training re-check failed', (e as Error).message)
  } finally {
    rechecking.value = false
  }
}
function adapterAgreement(h: number | null): string {
  return typeof h === 'number' ? `holdout ${Math.round(h * 100)}%` : ''
}

// ---- Taste scorer: status and retrain ----
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
// Deleted graded images getting vectors for the current CLIP model.
const tasteReembed = computed(() => tasteStatus.value?.reembed ?? null)

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
          ? `Not enough grades: need ${m.positives} more ✓ / ${m.negatives} more ✗ to train.`
          : 'Not enough grades to train yet.'
      } else if (detail?.code !== 'already_training') {
        tasteTrainError.value = e.message
      }
    } else {
      tasteTrainError.value = e instanceof Error ? e.message : String(e)
    }
  }
}

// ---- Writers: pass rate per writer per 14-day window + ✓ diversity ----
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

// Diversity is computed by training runs: refresh when one ends. Re-embed
// progress patches the status; when the pass ends (null) the status is
// reloaded, since what the scorer is missing may have changed.
useWebSocket('taste', (event, data) => {
  if (
    event === ('training_complete' satisfies TasteWsEvent) ||
    event === ('training_failed' satisfies TasteWsEvent)
  ) {
    void loadWriterStats()
  } else if (event === ('reembed_progress' satisfies TasteWsEvent)) {
    const reembed = (data as unknown as TasteReembedEvent).reembed ?? null
    taste.setReembed(reembed)
    if (reembed === null) void loadTasteStatus()
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

// Scorecard rows: each adapter vs the base writer on its blind scorecard.
const scorecardRows = computed(() => writerStats.value?.scorecards ?? [])
function scoreText(v: number | null): string {
  return v === null ? '—' : v.toFixed(2)
}

function rateText(cell: WriterWindowStat | undefined): string {
  if (!cell || cell.rate === null) return '—'
  return `${Math.round(cell.rate * 100)}%`
}

function rateTitle(cell: WriterWindowStat | undefined): string {
  if (!cell || cell.n_graded === 0) return 'No graded generations in this window'
  return `${cell.n_pass} ✓ pass of ${cell.n_graded} graded`
}
</script>

<template>
  <div v-if="loading" class="muted">Loading training status…</div>
  <template v-else>
    <section class="group-block">
      <div class="group-title">Writer training</div>
      <div class="table">
        <div class="thead">
          <div class="col name">Model</div>
          <div class="col status">Status</div>
          <div class="col size">Size</div>
          <div class="col vram">VRAM</div>
          <div class="col preload">Preload</div>
          <div class="col actions">Actions</div>
        </div>
        <ModelStatusRow
          v-for="row in trainingAssetRows"
          :key="row.id"
          :row="row"
          @download="onDownload"
          @delete="onDelete"
        />
        <div v-if="trainingAssetRows.length === 0" class="empty-msg">No models in this group.</div>
      </div>
      <div
        v-if="writerTrainingGate && !writerTrainingGate.available"
        class="muted writer-training-gate"
        data-testid="writer-training-gate"
      >
        Prompt-writer training is not available yet:
        <ul>
          <li v-for="r in writerTrainingGate.reasons ?? [writerTrainingGate.reason]" :key="r">{{ r }}</li>
        </ul>
        Downloads work on any machine.
        <button
          class="btn-secondary"
          data-testid="writer-training-recheck"
          :disabled="rechecking"
          @click="recheckTraining"
        >
          {{ rechecking ? 'Checking…' : 'Re-check' }}
        </button>
      </div>
      <div class="writer-adapters" data-testid="writer-adapters">
        <div class="writer-adapters-head">
          <span class="muted">Prompt-writer adapters ({{ writerAdapters.length }})</span>
          <button class="btn-secondary" data-testid="open-training-window" @click="training.openWindow()">
            Train prompt writer
          </button>
        </div>
        <div v-if="training.chipText" class="muted">{{ training.chipText }} — GPU busy: training.</div>
        <ul v-if="writerAdapters.length" class="writer-adapter-list">
          <li v-for="a in writerAdapters" :key="a.id">
            <b>{{ a.name }}</b>
            <span class="chip chip-muted">{{ a.status }}</span>
            <span class="muted">{{ adapterScope(a.scope) }}</span>
            <span class="muted">{{ adapterAgreement(a.holdout_agreement) }}</span>
          </li>
        </ul>
        <div v-else class="muted">No adapters yet.</div>
      </div>
    </section>

    <section class="preload-hint taste-block">
      <div class="section-heading">Taste scorer</div>
      <div v-if="tasteStatus?.active_model" class="taste-model">
        <template v-if="tasteStatus.labels_outdated">
          Active model trained {{ tasteTrainedAt }} on favorites and views, before
          ✓ pass / ✗ fail grades, holdout AUC <b>{{ tasteAuc }}</b>. Its scores stay until it
          retrains on grades.
        </template>
        <template v-else>
          Active model trained {{ tasteTrainedAt }} on
          <b>{{ tasteStatus.active_model.n_pos }}</b> ✓ pass /
          <b>{{ tasteStatus.active_model.n_neg }}</b> ✗ fail, holdout AUC
          <b>{{ tasteAuc }}</b>.
        </template>
        <span v-if="tasteStatus.stale" class="err-text">
          Retrain needed (the CLIP model changed).
        </span>
      </div>
      <div v-else-if="tasteStatus" class="taste-model">No taste model yet.</div>
      <div v-if="tasteMissing" class="taste-missing">
        Need {{ tasteMissing.positives }} more ✓ / {{ tasteMissing.negatives }} more ✗
        <span class="taste-hint">(only grades train it: ★ favorites and views never do)</span>
        <template v-if="tasteStatus?.missing_reason === 'vectors'">
          with a CLIP vector to train: build the similarity index
          (Similarity Settings &rarr; Build Index) and let it finish.
        </template>
        <template v-else>to train.</template>
      </div>
      <div v-if="tasteStatus && (tasteStatus.new_events ?? 0) > 0" class="taste-new">
        {{ plural(tasteStatus.new_events ?? 0, 'new rating', 'new ratings') }} since last training.
      </div>
      <div v-if="tasteReembed" class="taste-reembed">
        <i v-if="tasteReembed.running" class="pi pi-spin pi-spinner" />
        re-embedding deleted images, {{ tasteReembed.done }} / {{ tasteReembed.total }}
        <span v-if="!tasteReembed.running" class="taste-hint">
          (paused while the GPU is busy: writer training, a generation or an index build)
        </span>
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
        Pass rate (✓ out of ✓ + ✗) among graded generations, by the writer that drafted the
        prompt, per 14-day window of generation date. ★ favorites and views don't count.
      </div>
      <div v-if="writerStats?.collapse_warning" class="warn-banner writers-warning">
        ✓ passed images are getting less diverse (latest window more than 25% below the recent
        median) while the pass rate rises: the writer may be narrowing toward a few looks.
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
                  <span class="n-count">n={{ row.cells[s]?.n_graded ?? 0 }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="writerStats.diversity.length" class="writers-scroll">
          <table class="writers-table">
            <thead>
              <tr>
                <th class="writer-col">Pass diversity</th>
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
                  :title="`Mean pairwise CLIP distance between ${d.n} ✓ passed T2I images`"
                >
                  <span class="rate">
                    {{ d.mean_distance === null ? '—' : d.mean_distance.toFixed(2) }}
                  </span>
                  <span class="n-count">n={{ d.n }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="taste-hint">
          Pass diversity appears after the next taste training run.
        </div>
        <div v-if="scorecardRows.length" class="writers-scroll" data-testid="writer-scorecards">
          <table class="writers-table">
            <thead>
              <tr>
                <th class="writer-col">Scorecard (blind, same seeds)</th>
                <th title="Pass rate (✓) of the base writer's images over judged pairs">Base pass</th>
                <th title="Pass rate (✓) of the adapter's images over judged pairs">Adapter pass</th>
                <th title="Mean taste score: base vs adapter">Mean score</th>
                <th title="Pairs whose two images both have a grade, ✓ pass or ✗ fail (★ and a view alone do not count; deleted images still do)">Judged</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in scorecardRows" :key="r.adapter_id">
                <th class="writer-col" scope="row">
                  {{ r.name ?? `adapter:${r.adapter_id}` }}
                  <span v-if="r.status" class="writer-tag">{{ r.status }}</span>
                </th>
                <td><span class="rate">{{ ratePct(r.pass_rate_base) }}</span></td>
                <td><span class="rate">{{ ratePct(r.pass_rate_adapter) }}</span></td>
                <td>
                  <span class="rate">{{ scoreText(r.mean_score_base) }} / {{ scoreText(r.mean_score_adapter) }}</span>
                </td>
                <td>
                  <span class="rate">{{ r.n_judged }}</span>
                  <span class="n-count">of {{ r.n_pairs }}</span>
                </td>
              </tr>
            </tbody>
          </table>
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

.preload-hint, .group-block, .warn-banner {
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

.chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 11px;
  text-transform: lowercase;
}
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
.thead {
  display: grid;
  /* The column template must match .trow in ModelStatusRow. */
  grid-template-columns: 2fr 1.1fr 0.8fr 0.7fr 0.6fr 1.2fr;
  gap: 8px;
  align-items: center;
  padding: 6px 4px;
  font-size: 11px;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--surface-border);
}
.col { overflow: hidden; }

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

.empty-msg { padding: 10px; color: var(--text-color-secondary); font-size: 12px; text-align: center; }

.preload-hint { font-size: 12px; color: var(--text-color-secondary); }

.writer-training-gate { margin-top: 6px; font-size: 12px; }
.writer-training-gate ul { margin: 4px 0; padding-left: 18px; }
.writer-adapters { margin-top: 8px; font-size: 12px; }
.writer-adapters-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.writer-adapter-list { list-style: none; margin: 6px 0 0; padding: 0; }
.writer-adapter-list li { display: flex; gap: 8px; align-items: center; padding: 2px 0; }

.taste-block { display: flex; flex-direction: column; gap: 6px; }
.taste-actions { display: flex; align-items: center; gap: 10px; }
.taste-spinner { display: inline-flex; align-items: center; gap: 6px; }
.taste-reembed { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
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
.writers-table .n-count { color: var(--text-color-secondary); font-size: 11px; margin-left: 4px; }
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
</style>
