<script setup lang="ts">
// Writer training window (Models tab → "Train prompt writer", or the header
// chip). Non-modal: the run lives on the server, so closing this window (or
// the browser) never stops it. Example text is shown ONLY in the Preview
// panel below — never logged, never rendered anywhere else.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { useTrainingStore, trainingErrorText } from '../../stores/training'
import { useCategoriesStore } from '../../stores/categories'
import { anyViewerOpen } from '../../composables/useViewerPresence'
import { fetchDatasetPreview, fetchTrainingProfiles } from '../../api/training'
import {
  DEFAULT_CLAMPS,
  OVERRIDE_KEYS,
  TRAINING_EFFORTS,
  clampOverride,
  formatDuration,
  formatEta,
  type DatasetPreview,
  type Hyperparams,
  type OverrideKey,
  type Overrides,
  type ResumableRun,
  type TrainingEffort,
} from '../../types/training'
import {
  SCORECARD_READY_PAIRS,
  SERVABLE_STATUSES,
  favText,
  judgedPairs,
  ratePct,
  type ScorecardResult,
  type WriterAdapter,
} from '../../types/writers'

const training = useTrainingStore()
const categories = useCategoriesStore()

const POLL_MS = 5000
const BASE_LABEL = 'Base Qwen3-VL-8B'

const status = computed(() => training.status)
const dataset = computed(() => status.value?.dataset ?? null)
const counts = computed(() => dataset.value?.counts ?? null)
const gate = computed(() => status.value?.gate ?? null)
const run = computed(() => training.run)
const busy = ref(false)
const actionError = ref<string | null>(null)

if (categories.categories.length === 0) {
  categories.load().catch((e) => console.warn('Training: could not load categories', e instanceof Error ? e.message : e))
}
// categories_changed reloads the list (App.vue): a deleted scope category
// falls back to All.
watch(
  () => categories.categories,
  (list) => training.checkScope(list.map((c) => c.id)),
)
watch(
  () => training.scope,
  () => {
    preview.value = null
  },
)

// ---- polling: only while open and a gate check or a run is in flight ----
let pollTimer: ReturnType<typeof setInterval> | null = null
const shouldPoll = computed(() => !!gate.value?.checking || training.runActive)
watchEffect(() => {
  if (shouldPoll.value && pollTimer === null) {
    pollTimer = setInterval(() => {
      training.load().catch(() => {})
    }, POLL_MS)
  } else if (!shouldPoll.value && pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
onBeforeUnmount(() => {
  if (pollTimer !== null) clearInterval(pollTimer)
})

// A media viewer (library, T2I, I2V, slideshow) takes the screen: the window
// steps aside (v-show, so its state survives; the header chip still shows a
// run) and comes back when the last viewer closes. Escape closes the window
// only when nothing else owns the keyboard: no viewer open, and focus inside
// the window or nowhere in particular (body). Rating keys never reach it.
const rootEl = ref<HTMLElement | null>(null)
const hidden = computed(() => anyViewerOpen.value)
function focusWindow(): void {
  void nextTick(() => rootEl.value?.focus({ preventScroll: true }))
}
watch(hidden, (h) => {
  if (!h) focusWindow()
})
onMounted(focusWindow)

function onKey(e: KeyboardEvent) {
  if (e.key !== 'Escape' || renamingId.value || hidden.value) return
  const active = document.activeElement
  if (active && active !== document.body && !rootEl.value?.contains(active)) return
  training.closeWindow()
}
window.addEventListener('keydown', onKey)
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// ---- dataset panel ------------------------------------------------------
function fmtInt(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString()
}

const byTargetModel = computed(() => {
  const m = counts.value?.by_target_model ?? {}
  const total = Object.values(m).reduce((a, b) => a + b, 0)
  if (total === 0) return '—'
  return Object.entries(m)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${Math.round((v / total) * 100)}%`)
    .join(' · ')
})

function monthLabel(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

const dateSpan = computed(() => {
  const span = counts.value?.date_span
  if (!span || !span[0]) return '—'
  const end = new Date(span[1])
  const today = !Number.isNaN(end.getTime()) && Date.now() - end.getTime() < 36 * 3600 * 1000
  return `${monthLabel(span[0])} – ${today ? 'today' : monthLabel(span[1])}`
})

const readiness = computed(() => dataset.value?.readiness ?? null)
const readinessText = computed(() => {
  const r = readiness.value
  if (!r) return '—'
  if (!r.trainable) return `Needs ${fmtInt(r.missing)} more pair${r.missing === 1 ? '' : 's'}`
  if (r.enough_new) return 'Enough new data ✓'
  return 'Trainable · little new data since the last adapter'
})

const holdoutText = computed(() => {
  const d = dataset.value
  if (!d) return '—'
  return `${Math.round(d.holdout_fraction * 100)}% (${fmtInt(d.holdout_pairs)} pairs)`
})

const scopeOptions = computed(() => [
  { id: 'all', label: 'All' },
  ...categories.categories.map((c) => ({ id: String(c.id), label: c.name })),
])

function scopeLabel(scope: string): string {
  if (scope === 'all') return 'all'
  return categories.categories.find((c) => String(c.id) === scope)?.name ?? `category ${scope}`
}

function onScope(ev: Event) {
  training.setScope((ev.target as HTMLSelectElement).value)
}

// Hindsight briefs job
const briefs = computed(() => training.briefs)
const briefsText = computed(() => {
  const b = briefs.value
  if (!b) return ''
  if (b.running) return b.total > 0 ? `writing ${b.done} / ${b.total}…` : 'starting…'
  if (b.result === 'done') return `last run: ${b.done} written${b.failed ? `, ${b.failed} failed` : ''}`
  if (b.result === 'cancelled') return `cancelled at ${b.done} / ${b.total}`
  if (b.result?.startsWith('error')) return `stopped: ${b.result.slice(6) || 'error'}`
  return ''
})
async function onBriefs() {
  actionError.value = null
  try {
    if (briefs.value?.running) await training.stopBriefs()
    else await training.startBriefs()
  } catch (e) {
    actionError.value = trainingErrorText(e)
  }
}

// Preview (owner only; never logged)
const preview = ref<DatasetPreview | null>(null)
const previewLoading = ref(false)
async function onPreview() {
  if (preview.value) {
    preview.value = null
    return
  }
  previewLoading.value = true
  actionError.value = null
  try {
    preview.value = await fetchDatasetPreview(training.scope)
  } catch (e) {
    actionError.value = trainingErrorText(e)
  } finally {
    previewLoading.value = false
  }
}
// Up to ten, balanced by the server across preference / edit / hindsight.
const previewItems = computed(() => preview.value?.examples ?? [])
const KIND_LABEL: Record<string, string> = {
  preference: 'preference pair',
  edit: 'your edit',
  hindsight: 'hindsight',
}

// ---- run panel -----------------------------------------------------------
const effort = ref<TrainingEffort>('standard')
const startFrom = ref<'fresh' | number>('fresh')
const renderScorecard = ref(true)
const advancedOpen = ref(false)
const overrides = reactive<Record<OverrideKey, string>>({ rank: '', lr: '', epochs: '', dpo_lr: '' })

const clamps = computed(() => status.value?.clamps ?? DEFAULT_CLAMPS)
const preset = computed<Hyperparams | null>(() => status.value?.presets?.[effort.value] ?? null)

function parsedOverrides(): Overrides {
  const out: Overrides = {}
  for (const k of OVERRIDE_KEYS) {
    const raw = overrides[k].trim()
    if (raw === '') continue
    const n = Number(raw)
    if (Number.isFinite(n)) out[k] = clampOverride(k, n, clamps.value)
  }
  return out
}

function onOverrideBlur(k: OverrideKey) {
  const raw = overrides[k].trim()
  if (raw === '') return
  const n = Number(raw)
  overrides[k] = Number.isFinite(n) ? String(clampOverride(k, n, clamps.value)) : ''
}

// VRAM-cap rehearsal: '' = the config default, '0' = off, else GB.
const vramCap = ref('')
const capInfo = computed(() => status.value?.vram_cap ?? { default: null, choices: [16, 20, 24, 28], min: 8 })
const capDefaultLabel = computed(() =>
  capInfo.value.default ? `Default (config: ${capInfo.value.default} GB)` : 'Default (off)',
)

const resolved = computed<Hyperparams | null>(() => (preset.value ? { ...preset.value, ...parsedOverrides() } : null))
const overridesDiffer = computed(() => {
  const p = preset.value
  if (!p) return false
  const o = parsedOverrides()
  return OVERRIDE_KEYS.some((k) => o[k] !== undefined && o[k] !== p[k])
})

/** 2 significant digits: 1e-4, 1.5e-4, 2.5e-5. */
function fmtLr(v: unknown): string {
  return typeof v === 'number' && Number.isFinite(v) ? Number(v.toPrecision(2)).toExponential() : '—'
}
function fmtClamp(k: OverrideKey, v: number): string {
  return k === 'lr' || k === 'dpo_lr' ? fmtLr(v) : String(v)
}
function hpText(h: Partial<Hyperparams> | Record<string, unknown> | null | undefined): string {
  if (!h || typeof h.rank !== 'number') return '—'
  return `rank ${h.rank} · lr ${fmtLr(h.lr)} · ${h.epochs} epoch${h.epochs === 1 ? '' : 's'} · DPO lr ${fmtLr(h.dpo_lr)}`
}

const continuable = computed(() =>
  training.adapters.filter((a) => a.kind === 't2i' && SERVABLE_STATUSES.includes(a.status)),
)
watch(continuable, (list) => {
  if (startFrom.value !== 'fresh' && !list.some((a) => a.id === startFrom.value)) startFrom.value = 'fresh'
})

const runName = computed(() => run.value?.name ?? status.value?.next_name ?? '—')

const gateBlocked = computed(() => !!gate.value && !gate.value.available)
// While the dependency probe runs, its own "Checking…" reason is shown as the heading.
const gateReasons = computed(() => {
  const g = gate.value
  if (!g) return []
  const list = g.reasons?.length ? g.reasons : g.reason ? [g.reason] : []
  return g.checking ? list.filter((r) => !r.startsWith('Checking')) : list
})
const canStart = computed(
  () => !busy.value && !training.runActive && !!status.value && !gateBlocked.value && !!readiness.value?.trainable,
)
const startTitle = computed(() => {
  if (training.runActive) return 'A run is in progress.'
  if (gate.value?.checking) return 'Checking training setup…'
  if (gateBlocked.value) return 'Training is not available on this machine (see below).'
  if (readiness.value && !readiness.value.trainable) return readinessText.value
  return 'Start training'
})

const latestResumable = computed<ResumableRun | null>(() => training.resumable[0] ?? null)

async function act(fn: () => Promise<void>) {
  busy.value = true
  actionError.value = null
  try {
    await fn()
  } catch (e) {
    actionError.value = trainingErrorText(e)
    training.load().catch(() => {})
  } finally {
    busy.value = false
  }
}

function onStart() {
  void act(() =>
    training.start({
      scope: training.scope,
      effort: effort.value,
      overrides: overridesDiffer.value ? parsedOverrides() : undefined,
      start_from: startFrom.value,
      render_scorecard: renderScorecard.value,
      ...(vramCap.value === '' ? {} : { vram_cap_gb: Number(vramCap.value) }),
    }),
  )
}

function onPause() {
  if (!confirm('Pause training? It checkpoints and stops; Resume continues from the last checkpoint (up to 50 steps are redone).')) return
  void act(() => training.pause())
}

function onResume(r: ResumableRun | null = latestResumable.value) {
  if (!r) return
  void act(() => training.resume(r.run_id))
}

function onCancel() {
  const r = run.value
  if (r && training.runActive) {
    if (!confirm(`Cancel training ${r.name}? Its checkpoints are deleted and the adapter is discarded.`)) return
    void act(() => training.cancel())
    return
  }
  const p = latestResumable.value
  if (!p) return
  if (!confirm(`Discard the ${p.status} run ${p.name}? Its checkpoints are deleted and the adapter is removed.`)) return
  void act(() => training.cancel(p.run_id))
}

// progress
const progress = computed(() => run.value?.progress ?? null)
const stageLabel = computed(() => {
  const s = progress.value?.stage
  const map: Record<string, string> = {
    sft: 'Stage 1 · supervised (SFT)',
    dpo: 'Stage 2 · preferences (DPO)',
    eval: 'Checking holdout agreement',
    convert: 'Converting to GGUF',
  }
  if (run.value?.state === 'starting') return 'Starting…'
  if (run.value?.state === 'pausing') return 'Pausing at the next step…'
  if (run.value?.state === 'cancelling') return 'Cancelling…'
  return s ? (map[s] ?? s) : 'Loading the model…'
})
function pctText(v: number | null | undefined): string {
  return typeof v === 'number' ? `${Math.round(v * 100)}%` : '—'
}

// "checkpoint 12 min ago" ticks without new events.
const now = ref(Date.now())
const clock = setInterval(() => (now.value = Date.now()), 15000)
onBeforeUnmount(() => clearInterval(clock))
const checkpointAgo = computed(() => {
  const at = progress.value?.last_checkpoint_at
  if (!at) return 'no checkpoint yet'
  const t = Date.parse(at)
  if (Number.isNaN(t)) return 'checkpoint saved'
  const mins = Math.max(0, Math.round((now.value - t) / 60000))
  return mins < 1 ? 'checkpoint just now' : `checkpoint ${mins} min ago`
})

const sparkPath = computed(() => {
  const pts = training.lossPoints
  if (pts.length < 2) return ''
  const lo = Math.min(...pts)
  const hi = Math.max(...pts)
  const span = hi - lo || 1
  return pts
    .map((v, i) => {
      const x = (i / (pts.length - 1)) * 100
      const y = 36 - ((v - lo) / span) * 32
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
})
function fmtGib(v: number | null | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(1) : '—'
}
const vramText = computed(() => {
  const p = progress.value
  if (!p || (typeof p.alloc_gib !== 'number' && typeof p.peak_gib !== 'number')) return null
  return `VRAM ${fmtGib(p.alloc_gib)} / peak ${fmtGib(p.peak_gib)} GiB`
})
const capText = computed(() => {
  const cap = progress.value?.vram_cap_gb
  if (typeof cap === 'number') return `VRAM cap: ${cap} GB (rehearsal)`
  const total = progress.value?.vram_cap_ignored_total_gib
  return typeof total === 'number' ? `VRAM cap ignored (card has ${total.toFixed(1)} GiB)` : null
})

const lastLoss = computed(() => {
  const l = progress.value?.loss
  return typeof l === 'number' ? l.toFixed(3) : null
})

// ---- adapters panel ------------------------------------------------------
const adapterRows = computed(() => [...training.adapters].sort((a, b) => b.id - a.id))
const runByAdapter = computed(() => {
  const m = new Map<number, ResumableRun>()
  for (const r of training.resumable) m.set(r.adapter_id, r)
  return m
})

const STATUS_CLASS: Record<string, string> = {
  ready: 'st-ok',
  untested: 'st-un',
  training: 'st-tr',
  paused: 'st-un',
  failed: 'st-fl',
}

// ---- scorecard: base vs adapter on fixed briefs, judged blind ------------
const scorecardByAdapter = computed(() => {
  const m = new Map<number, ScorecardResult>()
  for (const r of training.scorecards?.adapters ?? []) m.set(r.adapter_id, r)
  return m
})
const readyPairs = computed(() => training.scorecards?.ready_pairs ?? SCORECARD_READY_PAIRS)

/** The adapter's favorite rate on its blind scorecard ("31% fav"), or "— fav". */
function scorecardFav(a: WriterAdapter): string {
  return favText(scorecardByAdapter.value.get(a.id)?.fav_rate_adapter)
}

const baseFav = computed(() => favText(training.scorecards?.base.fav_rate))

function scorecardTitle(a: WriterAdapter): string {
  const r = scorecardByAdapter.value.get(a.id)
  if (!r || r.n_pairs === 0) return 'No scorecard yet'
  const parts = [
    `${r.n_judged} of ${r.n_pairs} pairs judged`,
    `base ${ratePct(r.fav_rate_base)} vs this ${ratePct(r.fav_rate_adapter)} favorites`,
  ]
  if (r.mean_score_base !== null || r.mean_score_adapter !== null) {
    const f = (v: number | null) => (v === null ? '—' : v.toFixed(2))
    parts.push(`mean taste score base ${f(r.mean_score_base)} vs ${f(r.mean_score_adapter)}`)
  }
  return parts.join(' · ')
}

/** "12/30 judged" while untested (ready at `readyPairs` judged pairs). */
function judgedText(a: WriterAdapter): string {
  if (a.status !== 'untested' || a.kind !== 't2i') return ''
  return `${judgedPairs(scorecardByAdapter.value.get(a.id)?.n_judged ?? 0, readyPairs.value)} judged`
}

const scorecardRun = computed(() => training.scorecardRun)
const scorecardRunText = computed(() => {
  const r = scorecardRun.value
  if (!r) return null
  const name = training.adapters.find((a) => a.id === r.adapter_id)?.name ?? `adapter ${r.adapter_id}`
  const what = r.phase === 'rendering' ? 'rendering' : 'writing prompts'
  return `Scorecard for ${name}: ${what} ${r.done}/${r.total}`
})

type ScorecardAction = { label: string; fresh: boolean; title: string }
function scorecardAction(a: WriterAdapter): ScorecardAction | null {
  if (a.kind !== 't2i' || !SERVABLE_STATUSES.includes(a.status)) return null
  const latest = scorecardByAdapter.value.get(a.id)?.latest ?? null
  if (!latest) {
    return { label: 'render scorecard', fresh: false, title: 'Render every scorecard brief with the base writer and with this adapter (same seeds), to judge blind' }
  }
  if (latest.status !== 'complete' && latest.status !== 'running') {
    return { label: 'resume scorecard', fresh: false, title: 'Render the briefs that do not have both images yet' }
  }
  return { label: 're-render', fresh: true, title: 'Render a new scorecard (new seeds) for this adapter' }
}

function onScorecard(a: WriterAdapter) {
  const action = scorecardAction(a)
  if (!action) return
  void act(() => training.renderScorecard(a.id, action.fresh))
}

function onCancelScorecard() {
  if (!confirm('Cancel the scorecard? Images already rendered are kept; Resume renders the rest.')) return
  void act(() => training.cancelScorecard())
}

function adapterNote(a: WriterAdapter): string {
  if (a.status === 'training' && run.value?.adapter_id === a.id) {
    return training.pct !== null ? `${training.pct}%` : 'training'
  }
  const r = runByAdapter.value.get(a.id)
  if (a.status === 'paused' && r) return r.step !== null ? `step ${r.step}${r.total_steps ? ` / ${r.total_steps}` : ''}` : 'paused'
  if (a.status === 'failed') {
    if (!a.error) return 'failed'
    return a.error.startsWith('Ran out of GPU memory') ? a.error : a.error.slice(0, 60)
  }
  if (typeof a.holdout_agreement === 'number') {
    const base = typeof a.base_agreement === 'number' ? ` (base ${pctText(a.base_agreement)})` : ''
    return `holdout ${pctText(a.holdout_agreement)}${base}`
  }
  return ''
}

/** "peak 27.3 GiB · 1h 05m" from the training profiles (when recorded). */
function profileText(a: WriterAdapter): string {
  const st = training.profileStats.get(a.id)
  if (!st) return ''
  const parts: string[] = []
  if (st.peak_gib !== null) parts.push(`peak ${st.peak_gib.toFixed(1)} GiB`)
  const t = formatDuration(st.train_s)
  if (t && st.train_s > 0) parts.push(t)
  return parts.join(' · ')
}

// Download the profiles (numbers only, safe to share) as a JSON file.
const profilesBusy = ref(false)
async function onDownloadProfiles() {
  profilesBusy.value = true
  actionError.value = null
  try {
    const data = await fetchTrainingProfiles()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `writer-training-profiles-${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) {
    actionError.value = trainingErrorText(e)
  } finally {
    profilesBusy.value = false
  }
}

const renamingId = ref<number | null>(null)
const renameValue = ref('')
// `autofocus` only fires on page load, not for an input inserted later.
// (A function ref: a plain one inside v-for would collect an array.)
let renameInput: HTMLInputElement | null = null
function setRenameInput(el: unknown) {
  renameInput = el instanceof HTMLInputElement ? el : null
}
async function beginRename(a: WriterAdapter) {
  renamingId.value = a.id
  renameValue.value = a.name
  await nextTick()
  renameInput?.focus()
  renameInput?.select()
}
async function commitRename(a: WriterAdapter) {
  const name = renameValue.value.trim()
  renamingId.value = null
  if (!name || name === a.name) return
  await act(() => training.rename(a.id, name))
}

function onDelete(a: WriterAdapter) {
  const extra = a.status === 'paused' || a.status === 'failed' ? ' Its unfinished run and checkpoints are discarded too.' : ''
  if (!confirm(`Delete adapter ${a.name}? Its files are removed; past generations keep its name.${extra}`)) return
  void act(() => training.remove(a.id))
}

const failedLogOpen = ref<number | null>(null)

function onRecheck() {
  void act(() => training.load(true))
}
</script>

<template>
  <Teleport to="body">
    <div
      v-show="!hidden"
      ref="rootEl"
      class="tw"
      role="dialog"
      aria-label="Train prompt writer"
      tabindex="-1"
      data-testid="writer-training-window"
    >
      <div class="tw-hd">
        <b>Train prompt writer</b>
        <button class="tw-close" title="Close — training continues on the server" @click="training.closeWindow()">
          ✕ close <span class="dim">(training continues)</span>
        </button>
      </div>

      <div v-if="!status?.dataset" class="tw-loading dim">
        {{ training.loadError ?? 'Loading training status…' }}
      </div>

      <div v-else-if="training.loadError" class="err tw-load-err" data-testid="training-load-error">
        Could not refresh the training status: {{ training.loadError }}
      </div>

      <div v-if="status?.dataset" class="tw-bd">
        <div class="col-left">
          <!-- Dataset -->
          <section class="pn" data-testid="training-dataset">
            <h4>Dataset (as of now)</h4>
            <div class="kv">
              <span>Scope</span>
              <select class="sel" :value="training.scope" :disabled="training.runActive" @change="onScope">
                <option v-for="o in scopeOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
            </div>
            <div class="kv"><span>Variant preference pairs</span><span>{{ fmtInt(counts?.pref_pairs) }}</span></div>
            <div class="kv"><span>Your edits of drafts</span><span>{{ fmtInt(counts?.edit_pairs) }}</span></div>
            <div class="kv"><span>Hindsight briefs (favorites)</span><span>{{ fmtInt(counts?.hindsight) }}</span></div>
            <div class="kv">
              <span>New since the last adapter</span>
              <span>+{{ fmtInt(counts?.new_since_last) }} <span class="dim">pairs</span></span>
            </div>
            <div class="kv"><span>By target model</span><span class="right">{{ byTargetModel }}</span></div>
            <div class="kv"><span>Ratings span</span><span>{{ dateSpan }}</span></div>
            <div class="kv"><span>Held out for checking</span><span>{{ holdoutText }}</span></div>
            <div class="kv">
              <span>Readiness</span>
              <span :class="readiness?.enough_new ? 'good' : readiness?.trainable ? '' : 'warn'" data-testid="training-readiness">
                {{ readinessText }}
              </span>
            </div>
            <div class="kv">
              <span>Write hindsight briefs <span class="dim">(VLM)</span></span>
              <span class="briefs">
                <span class="dim">{{ briefsText }}</span>
                <button class="b" :disabled="training.locked && !briefs?.running" @click="onBriefs">
                  {{ briefs?.running ? 'Cancel briefs' : 'Write briefs' }}
                </button>
              </span>
            </div>
            <div class="btns">
              <button class="b" :disabled="previewLoading" data-testid="training-preview" @click="onPreview">
                {{ preview ? 'Hide preview' : previewLoading ? 'Loading…' : 'Preview 10 examples' }}
              </button>
            </div>
            <div v-if="preview" class="preview" data-testid="training-preview-list">
              <div v-if="previewItems.length === 0" class="dim">No examples in this scope yet.</div>
              <details v-for="(ex, i) in previewItems" :key="i" class="ex">
                <summary>
                  <span class="tag">{{ KIND_LABEL[ex.kind] ?? ex.kind }}</span>
                  {{ ex.target_model }}<span v-if="ex.categories.length" class="dim"> · {{ ex.categories.join(', ') }}</span>
                </summary>
                <div class="ex-k">Request</div>
                <pre class="ex-v">{{ ex.user }}</pre>
                <div class="ex-k">{{ ex.rejected !== undefined ? 'Chosen' : 'Target' }}</div>
                <pre class="ex-v">{{ ex.chosen }}</pre>
                <template v-if="ex.rejected !== undefined">
                  <div class="ex-k">Rejected</div>
                  <pre class="ex-v">{{ ex.rejected }}</pre>
                </template>
              </details>
            </div>
          </section>

          <!-- Run -->
          <section class="pn" data-testid="training-run">
            <h4>Run</h4>
            <div class="kv">
              <span>Name</span>
              <span :title="run ? '' : 'Assigned when the run starts; rename it any time.'">{{ runName }}</span>
            </div>
            <div class="kv">
              <span>Effort</span>
              <span class="seg">
                <button
                  v-for="e in TRAINING_EFFORTS"
                  :key="e"
                  class="seg-b"
                  :class="{ on: effort === e }"
                  :disabled="training.runActive"
                  @click="effort = e"
                >
                  {{ e[0].toUpperCase() + e.slice(1) }}
                </button>
              </span>
            </div>
            <div class="kv"><span class="dim">Resolved</span><span class="dim" data-testid="training-resolved">{{ hpText(resolved) }}</span></div>
            <div class="kv">
              <span>Start from</span>
              <select v-model="startFrom" class="sel" :disabled="training.runActive">
                <option value="fresh">Fresh (all data)</option>
                <option v-for="a in continuable" :key="a.id" :value="a.id">Continue {{ a.name }}</option>
              </select>
            </div>
            <div class="kv">
              <span>After training</span>
              <label class="chk">
                <input v-model="renderScorecard" type="checkbox" :disabled="training.runActive" />
                Render scorecard briefs (blind)
              </label>
            </div>
            <button class="adv-toggle dim" @click="advancedOpen = !advancedOpen">
              {{ advancedOpen ? '▾' : '▸' }} Advanced (learning rate, rank, epochs)
            </button>
            <div v-if="advancedOpen" class="adv" data-testid="training-advanced">
              <label v-for="k in OVERRIDE_KEYS" :key="k" class="adv-f">
                <span>{{ k === 'dpo_lr' ? 'DPO lr' : k === 'lr' ? 'lr' : k }}</span>
                <input
                  v-model="overrides[k]"
                  type="text"
                  inputmode="decimal"
                  :placeholder="preset ? String(preset[k]) : ''"
                  :disabled="training.runActive"
                  @blur="onOverrideBlur(k)"
                />
                <span class="dim">{{ fmtClamp(k, clamps[k][0]) }}–{{ fmtClamp(k, clamps[k][1]) }}</span>
              </label>
              <label class="adv-f">
                <span>Simulate VRAM cap (GB)</span>
                <select v-model="vramCap" class="sel" :disabled="training.runActive" data-testid="training-vram-cap">
                  <option value="">{{ capDefaultLabel }}</option>
                  <option value="0">Off</option>
                  <option v-for="c in capInfo.choices" :key="c" :value="String(c)">{{ c }} GB</option>
                </select>
                <span class="dim" title="Limits this run's GPU memory to rehearse a smaller card; the 32 GB check still applies to the real card">rehearsal</span>
              </label>
              <div v-if="overridesDiffer" class="warn adv-warn" data-testid="training-override-warning">
                ⚠ Differs from the {{ effort }} preset. Untested settings can overfit or stall; the server clamps them into range.
              </div>
            </div>

            <template v-if="run && training.runActive">
              <div class="bar"><i :style="{ width: `${training.pct ?? 0}%` }" /></div>
              <div class="dim" data-testid="training-progress">
                {{ stageLabel }}
                <template v-if="progress?.total_steps">
                  · Step {{ fmtInt(progress?.step) }} / {{ fmtInt(progress?.total_steps) }}
                </template>
                · holdout agreement {{ pctText(progress?.holdout_agreement) }}
                (base {{ pctText(progress?.base_agreement) }})
                · {{ checkpointAgo }}
                <template v-if="formatEta(progress?.eta_s)"> · ~{{ formatEta(progress?.eta_s) }} left</template>
              </div>
              <div v-if="vramText || capText" class="dim" data-testid="training-vram">
                <template v-if="vramText">{{ vramText }}</template>
                <template v-if="vramText && capText"> · </template>
                <template v-if="capText">{{ capText }}</template>
              </div>
              <div class="spark" :title="lastLoss ? `loss ${lastLoss}` : 'loss'">
                <svg v-if="sparkPath" viewBox="0 0 100 40" preserveAspectRatio="none">
                  <path :d="sparkPath" fill="none" stroke="currentColor" stroke-width="1.2" vector-effect="non-scaling-stroke" />
                </svg>
                <span v-else class="dim spark-empty">loss appears after the first steps</span>
                <span v-if="lastLoss" class="spark-label dim">loss {{ lastLoss }}</span>
              </div>
            </template>
            <div
              v-else-if="training.lastOutcome"
              class="outcome"
              :class="training.lastOutcome.kind === 'failed' ? 'warn' : 'dim'"
              data-testid="training-outcome"
            >
              {{ training.lastOutcome.text }}
            </div>
            <div v-if="!training.runActive && latestResumable" class="dim">
              {{ latestResumable.status === 'failed' ? 'Failed' : 'Paused' }} run: {{ latestResumable.name }}
              <template v-if="latestResumable.step !== null">at step {{ latestResumable.step }}<template v-if="latestResumable.total_steps"> / {{ latestResumable.total_steps }}</template></template>
            </div>

            <div class="btns">
              <button class="b p" :disabled="!canStart" :title="startTitle" data-testid="training-start" @click="onStart">Start</button>
              <button
                class="b w"
                :disabled="busy || run?.state !== 'running'"
                data-testid="training-pause"
                @click="onPause"
              >
                Pause (keep checkpoint)
              </button>
              <button
                class="b"
                :disabled="busy || training.runActive || !latestResumable || gateBlocked"
                :title="latestResumable ? `Resume ${latestResumable.name}` : 'Nothing to resume'"
                data-testid="training-resume"
                @click="onResume()"
              >
                Resume
              </button>
              <button
                class="b d"
                :disabled="busy || (training.runActive ? run?.state === 'cancelling' : !latestResumable)"
                data-testid="training-cancel"
                @click="onCancel"
              >
                Cancel (discard)
              </button>
            </div>

            <div v-if="actionError" class="err" data-testid="training-error">{{ actionError }}</div>

            <div v-if="gateBlocked" class="gate" data-testid="training-gate">
              <div v-if="gate?.checking" class="dim" data-testid="training-gate-checking">Checking training setup…</div>
              <template v-if="gateReasons.length">
                {{ gate?.checking ? 'So far, missing:' : 'Training is not available on this machine:' }}
                <ul>
                  <li v-for="r in gateReasons" :key="r">{{ r }}</li>
                </ul>
              </template>
              <button v-if="!gate?.checking" class="b" :disabled="busy" @click="onRecheck">Re-check</button>
            </div>
          </section>
        </div>

        <!-- Adapters -->
        <section class="pn col-right" data-testid="training-adapters">
          <h4>Adapters</h4>
          <table class="al">
            <tbody>
              <tr>
                <td>{{ BASE_LABEL }}</td>
                <td><span class="st st-ok">ready</span></td>
                <td class="dim">—</td>
                <td class="dim nowrap" :title="`Base writer on every judged scorecard pair (${training.scorecards?.base.n_judged ?? 0})`">{{ baseFav }}</td>
                <td></td>
              </tr>
              <tr v-for="a in adapterRows" :key="a.id" :data-testid="`adapter-row-${a.id}`">
                <td>
                  <input
                    v-if="renamingId === a.id"
                    :ref="setRenameInput"
                    v-model="renameValue"
                    class="rename"
                    @keydown.enter="commitRename(a)"
                    @keydown.esc="renamingId = null"
                    @blur="commitRename(a)"
                  />
                  <template v-else>
                    <b>{{ a.name }}</b>
                    <div class="dim small">{{ scopeLabel(a.scope) }} · {{ hpText(a.hyperparams) }}</div>
                  </template>
                </td>
                <td><span class="st" :class="STATUS_CLASS[a.status] ?? ''">{{ a.status }}</span></td>
                <td class="dim small note" :title="a.error ?? ''">
                  {{ adapterNote(a) }}
                  <div v-if="profileText(a)" class="small" :data-testid="`adapter-profile-${a.id}`">{{ profileText(a) }}</div>
                </td>
                <td class="dim nowrap" :title="scorecardTitle(a)" :data-testid="`adapter-fav-${a.id}`">
                  {{ scorecardFav(a) }}
                  <div v-if="judgedText(a)" class="small">{{ judgedText(a) }}</div>
                </td>
                <td class="acts">
                  <button v-if="a.status !== 'training'" class="lnk" @click="beginRename(a)">rename</button>
                  <button
                    v-if="(a.status === 'paused' || a.status === 'failed') && runByAdapter.get(a.id)"
                    class="lnk"
                    :disabled="busy || training.runActive || gateBlocked"
                    @click="onResume(runByAdapter.get(a.id)!)"
                  >
                    resume
                  </button>
                  <button v-if="a.status === 'failed' && a.error" class="lnk" @click="failedLogOpen = failedLogOpen === a.id ? null : a.id">
                    log
                  </button>
                  <button
                    v-if="scorecardAction(a)"
                    class="lnk"
                    :title="scorecardAction(a)!.title"
                    :disabled="busy || !!scorecardRun || training.locked"
                    @click="onScorecard(a)"
                  >
                    {{ scorecardAction(a)!.label }}
                  </button>
                  <button v-if="a.status !== 'training'" class="lnk danger" :disabled="busy" @click="onDelete(a)">delete</button>
                </td>
              </tr>
              <template v-for="a in adapterRows" :key="`log-${a.id}`">
                <tr v-if="failedLogOpen === a.id">
                  <td colspan="5"><pre class="log">{{ a.error }}</pre></td>
                </tr>
              </template>
            </tbody>
          </table>
          <div v-if="adapterRows.length === 0" class="dim small">No adapters yet.</div>
          <div v-if="scorecardRunText" class="small scorecard-run" data-testid="scorecard-run">
            {{ scorecardRunText }}
            <button class="lnk danger" :disabled="busy" @click="onCancelScorecard">cancel</button>
          </div>
          <div v-else-if="training.scorecardOutcome" class="small dim" data-testid="scorecard-outcome">
            {{ training.scorecardOutcome }}
          </div>
          <div class="dim small foot">
            "% fav" = your favorite rate on its blind scorecard ({{ training.scorecards?.briefs ?? 40 }} briefs,
            base vs adapter, same seeds). Scorecard images hide their writer until you favorite or pass them;
            an untested adapter becomes ready at {{ readyPairs }} judged pairs (both images favorited or
            passed; seen alone does not count).
          </div>
          <div class="dim small foot">
            <button
              class="lnk"
              :disabled="profilesBusy"
              title="VRAM, time and data shape of recent training attempts (numbers only, no prompt text) as JSON"
              data-testid="training-profiles-download"
              @click="onDownloadProfiles"
            >
              Training profiles
            </button>
            (download: VRAM and timing of recent runs)
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.scorecard-run {
  margin-top: 6px;
}

.tw {
  position: fixed;
  top: 6vh;
  left: 50%;
  transform: translateX(-50%);
  width: min(1040px, calc(100vw - 32px));
  max-height: 88vh;
  overflow: auto;
  z-index: 950;
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
  font-size: 12.5px;
}
.tw:focus {
  outline: none;
}
.tw-hd {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--surface-ground);
  border-bottom: 1px solid var(--surface-border);
}
.tw-close {
  background: none;
  border: none;
  color: var(--text-color);
  cursor: pointer;
  font-size: 12px;
}
.tw-loading { padding: 24px; }
.tw-load-err { padding: 6px 14px 0; }
.tw-bd {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 12px;
  padding: 12px;
}
@media (max-width: 760px) {
  .tw-bd { grid-template-columns: 1fr; }
}
.col-left { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.pn {
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 10px;
  min-width: 0;
}
.pn h4 {
  margin: 0 0 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-color-secondary);
}
.kv {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  border-bottom: 1px dashed var(--surface-border);
}
.kv .right { text-align: right; }
.dim { color: var(--text-color-secondary); }
.small { font-size: 11px; }
.good { color: #22c55e; }
.warn { color: #f59e0b; }
.err { color: var(--danger-color); margin-top: 6px; }
.sel {
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  font-size: 12px;
  padding: 2px 4px;
  max-width: 60%;
}
.btns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.b {
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid var(--surface-border);
  background: var(--surface-section);
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}
.b:hover:not(:disabled) { background: var(--surface-hover); }
.b:disabled { opacity: 0.4; cursor: not-allowed; }
.b.p { background: #2d5a8a; border-color: #2d5a8a; color: #fff; }
.b.w { background: #7a5a1a; border-color: #7a5a1a; color: #fff; }
.b.d { background: #7a2a2a; border-color: #7a2a2a; color: #fff; }
.briefs { display: flex; align-items: center; gap: 6px; }
.seg { display: inline-flex; gap: 2px; }
.seg-b {
  padding: 2px 8px;
  border: 1px solid var(--surface-border);
  background: var(--surface-section);
  color: var(--text-color-secondary);
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
}
.seg-b.on { color: var(--text-color); font-weight: 600; border-color: var(--primary-color); }
.seg-b:disabled { cursor: not-allowed; }
.chk { display: inline-flex; align-items: center; gap: 4px; }
.adv-toggle {
  background: none;
  border: none;
  padding: 4px 0;
  margin-top: 4px;
  cursor: pointer;
  font-size: 12px;
}
.adv { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; padding: 4px 0 6px; }
.adv-f { display: grid; grid-template-columns: 52px 1fr auto; gap: 6px; align-items: center; }
.adv-f input {
  min-width: 0;
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 12px;
}
.adv-warn { grid-column: 1 / -1; font-size: 11.5px; }
.bar {
  height: 8px;
  background: var(--surface-border);
  border-radius: 4px;
  overflow: hidden;
  margin: 8px 0 6px;
}
.bar i { display: block; height: 100%; background: #33cc99; transition: width 0.4s; }
.spark {
  position: relative;
  height: 40px;
  margin-top: 6px;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  color: #33cc99;
}
.spark svg { width: 100%; height: 100%; display: block; }
.spark-empty { position: absolute; left: 6px; top: 12px; font-size: 11px; }
.spark-label { position: absolute; right: 6px; top: 2px; font-size: 10.5px; }
.outcome { margin-top: 8px; }
.gate { margin-top: 8px; font-size: 12px; }
.gate ul { margin: 4px 0 6px; padding-left: 18px; }
.preview { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; max-height: 320px; overflow: auto; }
.ex { border: 1px solid var(--surface-border); border-radius: 4px; padding: 4px 6px; }
.ex summary { cursor: pointer; }
.ex .tag { font-size: 10.5px; padding: 0 5px; border-radius: 6px; background: var(--surface-hover); margin-right: 4px; }
.ex-k { font-size: 10.5px; text-transform: uppercase; color: var(--text-color-secondary); margin-top: 4px; }
.ex-v { white-space: pre-wrap; word-break: break-word; margin: 2px 0; font-size: 11.5px; font-family: inherit; }
table.al { width: 100%; border-collapse: collapse; }
table.al td { padding: 5px 4px; border-bottom: 1px solid var(--surface-border); vertical-align: top; }
.st { border-radius: 8px; padding: 0 6px; font-size: 10.5px; white-space: nowrap; }
.st-ok { background: #24402f; color: #b9f0cc; }
.st-un { background: #40381f; color: #f0deb0; }
.st-tr { background: #1f3550; color: #cde; }
.st-fl { background: #4a2020; color: #f5c2c2; }
.acts { white-space: nowrap; text-align: right; }
.nowrap { white-space: nowrap; }
table.al td.note { max-width: 120px; }
.lnk {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 11.5px;
  padding: 0 2px;
}
.lnk:hover:not(:disabled) { color: var(--text-color); text-decoration: underline; }
.lnk:disabled { opacity: 0.4; cursor: not-allowed; }
.lnk.danger:hover:not(:disabled) { color: var(--danger-color); }
.rename {
  width: 100%;
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--primary-color);
  border-radius: 4px;
  padding: 2px 4px;
  font-size: 12px;
}
.log { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 11px; color: var(--danger-color); }
.foot { margin-top: 8px; }
</style>
