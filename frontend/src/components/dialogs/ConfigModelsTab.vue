<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useModelsStore } from '../../stores/models'
import type { ModelGroup, ModelRow } from '../../api/models'
import { TIER_COLOR, TIER_LABEL } from '../../types/hardware'
import { startRetag } from '../../api/vlm'
import { useTrainingStore, trainingErrorText } from '../../stores/training'
import { GPU_BUSY_TEXT, isGpuBusyTraining } from '../../utils/gpuBusy'
import ModelStatusRow from './ModelStatusRow.vue'

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

// The writer-training assets live on the Training tab.
const groups: ModelGroup[] = ['Embedding', 'Upscaling', 'NLP', 'Tagging (VLM)']

const tierLabel = computed(() => TIER_LABEL[models.tier])
const tierColor = computed(() => TIER_COLOR[models.tier])

const grouped = computed<Record<string, ModelRow[]>>(() => {
  const out: Record<string, ModelRow[]> = {
    Embedding: [],
    Upscaling: [],
    NLP: [],
    'Tagging (VLM)': [],
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
    // 409 training_uses_base (a paused / failed run still needs the base
    // weights) and gpu_busy_training get their own text.
    alert(trainingErrorText(e))
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

// "Download all missing" skips the opt-in training assets (Training tab).
const missingCount = computed(
  () => models.models.filter((m) => m.status === 'missing' && !m.writer_training).length,
)

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

async function onUnloadClip(row: ModelRow) {
  if (!confirm(`Unload ${row.name} from VRAM?`)) {
    return
  }
  await models.stopInferenceWorker()
}

// A writer LoRA run holds the GPU: the re-tag button waits for it.
const training = useTrainingStore()

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
    alert(isGpuBusyTraining(e) ? GPU_BUSY_TEXT : `Re-tag failed: ${e instanceof Error ? e.message : String(e)}`)
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
        <ModelStatusRow
          v-for="row in grouped[group]"
          :key="row.id"
          :row="row"
          @download="onDownload"
          @delete="onDelete"
          @preload="onTogglePreload"
          @load="onLoad"
          @unload="onUnload"
          @unload-clip="onUnloadClip"
        />
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
          :disabled="!models.isVlmReady || retagInFlight || training.locked"
          :title="training.locked ? GPU_BUSY_TEXT : ''"
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
.vlm-status-block { display: flex; flex-direction: column; gap: 8px; }
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
