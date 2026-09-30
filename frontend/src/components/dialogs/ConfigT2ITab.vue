<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { listPresets } from '../../api/comfy'
import { fetchT2iConfig, t2iOutputPreview } from '../../api/t2i'
import type { T2iConfig, T2iOutputPreview } from '../../types/t2i'
import type { WorkflowPreset } from '../../types/storyboard'
import {
  CONTENT_MODE_OPTIONS,
  buildT2iSection,
  parseMegapixels,
  presetChoices,
  rawSection,
  type T2iContentMode,
} from '../../utils/t2iConfigForm'
import DirectoryPicker from './DirectoryPicker.vue'
import PresetRegistrationDialog from '../storyboard/PresetRegistrationDialog.vue'

const loading = ref(true)
const loadError = ref<string | null>(null)
const saveError = ref<string | null>(null)
// Keep the raw section: PUT /api/config is a shallow top-level merge, so we
// spread-merge to avoid clobbering keys this tab does not edit (window, the
// batch limits, identity, ...). The form itself is filled from the effective
// config (GET /api/t2i/config), which the server has already defaulted and
// sanitised.
const t2iRaw = ref<Record<string, unknown>>({})
const models = ref<T2iConfig['models']>([])
const modelWorkflows = ref<Record<string, number | null>>({})
const defaultModel = ref('')
const megapixelsText = ref('')
const defaultMegapixels = ref(1)
const contentMode = ref<T2iContentMode>('uncensored')
// Must match backend/config.py::T2I_DEFAULT_OUTPUT_PREFIX.
const DEFAULT_OUTPUT_PREFIX = '/%Y-%m-%d/t2i_'
const outputRoot = ref('')
const outputPrefix = ref(DEFAULT_OUTPUT_PREFIX)
const showPicker = ref(false)
const preview = ref<T2iOutputPreview>({ path: null, error: null, warnings: [] })
const presets = ref<WorkflowPreset[]>([])
const showRegister = ref(false)
const saved = ref(false)

// The presets a T2I model can render through.
const t2iPresets = computed(() => presets.value.filter((p) => p.kind === 't2i'))

const parsedMegapixels = computed(() => parseMegapixels(megapixelsText.value))
const canSave = computed(() => parsedMegapixels.value.length > 0 && !!defaultModel.value)

// A finished edit of the size list (blur / Enter, not every keystroke) can
// drop the current default out of it; move it to the first entry then.
function syncDefaultSize() {
  const list = parsedMegapixels.value
  if (list.length && !list.includes(defaultMegapixels.value)) defaultMegapixels.value = list[0]
}

onMounted(load)

// Live "would be saved as" preview, resolved by the server so the date
// expansion and path rules have exactly one implementation. Debounced:
// it fires on every keystroke in the prefix box.
let previewTimer: ReturnType<typeof setTimeout> | null = null
let previewSeq = 0
async function refreshPreview() {
  const seq = ++previewSeq
  try {
    const res = await t2iOutputPreview(outputRoot.value, outputPrefix.value)
    if (seq === previewSeq) preview.value = res // drop out-of-order replies
  } catch (e) {
    if (seq === previewSeq) {
      preview.value = {
        path: null,
        error: e instanceof Error ? e.message : String(e),
        warnings: [],
      }
    }
  }
}
watch([outputRoot, outputPrefix], () => {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 250)
})
onBeforeUnmount(() => {
  if (previewTimer) clearTimeout(previewTimer)
})

function onPickRoot(path: string) {
  outputRoot.value = path
  showPicker.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [config, presetRows, cfg] = await Promise.all([
      fetchConfig(),
      listPresets(),
      fetchT2iConfig(),
    ])
    presets.value = presetRows
    t2iRaw.value = rawSection(config.t2i)
    models.value = cfg.models
    modelWorkflows.value = Object.fromEntries(
      cfg.models.map((m) => [m.id, cfg.model_workflows[m.id] ?? null]),
    )
    defaultModel.value = cfg.default_model
    megapixelsText.value = cfg.megapixels.join(', ')
    defaultMegapixels.value = cfg.default_megapixels
    contentMode.value = cfg.content_mode
    outputRoot.value = cfg.output_root
    // An explicit '' is a saved choice; only a missing value gets the default.
    outputPrefix.value =
      typeof cfg.output_prefix === 'string' ? cfg.output_prefix : DEFAULT_OUTPUT_PREFIX
    void refreshPreview()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function save() {
  saveError.value = null
  const t2i = buildT2iSection(t2iRaw.value, {
    outputRoot: outputRoot.value,
    outputPrefix: outputPrefix.value,
    megapixels: parsedMegapixels.value,
    defaultMegapixels: defaultMegapixels.value,
    defaultModel: defaultModel.value,
    contentMode: contentMode.value,
    modelWorkflows: modelWorkflows.value,
  })
  try {
    await updateConfig({ t2i })
    t2iRaw.value = t2i
    saved.value = true
    setTimeout(() => (saved.value = false), 1500)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
  }
}

// Only re-list: unlike the video tab this leaves the registration dialog
// open, so the "Saved with N warnings" notice (a workflow without
// MS_NEGATIVE / MS_LORA_STACK warns) stays readable. Close is its own button.
async function onRegistered() {
  presets.value = await listPresets()
}
</script>

<template>
  <div class="t2i-tab">
    <div v-if="loading" class="muted">Loading...</div>
    <template v-else-if="loadError">
      <p class="warn">⚠ Could not load the text-to-image settings: {{ loadError }}</p>
      <button type="button" class="btn" @click="load">Retry</button>
    </template>
    <template v-else>
      <h4>Output files</h4>
      <p class="muted">
        Where generated images are saved. Leave the directory empty to use the
        default location, a t2i folder under the ComfyUI output root.
      </p>
      <label class="row">
        <span>Root directory</span>
        <span class="root-row">
          <input
            v-model="outputRoot"
            spellcheck="false"
            placeholder="(default — t2i folder in the ComfyUI output root)"
          />
          <button type="button" class="btn" @click="showPicker = true">Browse…</button>
          <button
            type="button"
            class="btn"
            title="Back to the default location"
            :disabled="!outputRoot"
            @click="outputRoot = ''"
          >Clear</button>
        </span>
      </label>
      <label class="row">
        <span>File prefix</span>
        <input v-model="outputPrefix" spellcheck="false" :placeholder="DEFAULT_OUTPUT_PREFIX" />
      </label>
      <p class="muted hint">
        A path under the root; the last part is the file name prefix and a
        unique number is appended. Date tokens: <code>%Y</code> year,
        <code>%m</code> month, <code>%d</code> day, <code>%H</code> hour,
        <code>%M</code> minute, <code>%S</code> second.
      </p>
      <p v-for="(w, i) in preview.warnings" :key="i" class="warn">⚠ {{ w }}</p>
      <p v-if="preview.error" class="warn">⚠ {{ preview.error }}</p>
      <p v-else-if="preview.path" class="muted hint">
        Next image: <code class="preview-path">{{ preview.path }}</code>
      </p>

      <h4>Workflows</h4>
      <p class="muted">
        Each model renders through a registered text-to-image workflow. Pick
        the default for each model; the T2I dialog can still switch to any
        registered one.
      </p>
      <p v-if="t2iPresets.length" class="muted hint">
        Registered: {{ t2iPresets.map((p) => p.name).join(', ') }}
      </p>
      <p v-else class="muted hint">No text-to-image workflows registered yet.</p>
      <label v-for="m in models" :key="m.id" class="row">
        <span>{{ m.label }}</span>
        <select v-model="modelWorkflows[m.id]">
          <option :value="null">— none —</option>
          <option
            v-for="c in presetChoices(t2iPresets, modelWorkflows[m.id] ?? null)"
            :key="c.id"
            :value="c.id"
          >
            {{ c.label }}
          </option>
        </select>
      </label>
      <p class="muted hint">
        A workflow is an API-format ComfyUI graph with nodes titled MS_POSITIVE,
        MS_SEED, MS_LATENT and MS_SAVE. MS_NEGATIVE and MS_LORA_STACK are
        optional; without them the negative prompt and LoRAs do not apply.
      </p>
      <button type="button" class="btn" @click="showRegister = true">Register workflow…</button>

      <h4>Defaults</h4>
      <label class="row">
        <span>Default model</span>
        <select v-model="defaultModel">
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label }}</option>
        </select>
      </label>
      <label class="row">
        <span>Size choices (megapixels, comma-separated)</span>
        <input v-model="megapixelsText" @change="syncDefaultSize" />
      </label>
      <p v-if="!parsedMegapixels.length" class="warn">
        ⚠ Enter at least one size, for example 0.5, 1, 2.
      </p>
      <label class="row">
        <span>Default size</span>
        <select v-model.number="defaultMegapixels">
          <option v-for="m in parsedMegapixels" :key="m" :value="m">{{ m }} MP</option>
        </select>
      </label>
      <label class="row">
        <span>Prompt content</span>
        <select v-model="contentMode">
          <option v-for="[value, label] in CONTENT_MODE_OPTIONS" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </label>
      <p class="muted hint">
        How the prompt writer treats explicit content. Uncensored describes
        anything the caption states plainly, Safe for work keeps prompts clean,
        and Model default adds no directive.
      </p>

      <div class="actions">
        <button type="button" class="btn primary" :disabled="!canSave" @click="save">Save</button>
        <span v-if="saved" class="muted">Saved.</span>
        <span v-if="saveError" class="warn">⚠ {{ saveError }}</span>
      </div>
    </template>

    <DirectoryPicker
      v-if="showPicker"
      title="Root directory for generated images"
      :initial-path="outputRoot"
      @select="onPickRoot"
      @close="showPicker = false"
    />

    <PresetRegistrationDialog
      v-if="showRegister"
      kind="t2i"
      @close="showRegister = false"
      @registered="onRegistered"
    />
  </div>
</template>

<style scoped>
.t2i-tab { display: flex; flex-direction: column; gap: 10px; }
.t2i-tab h4 { margin: 12px 0 4px; font-size: 14px; color: var(--text-color); }
.t2i-tab h4:first-child { margin-top: 0; }
.row { display: flex; align-items: center; gap: 10px; }
.row > span { min-width: 220px; color: var(--text-color); }
.row select,
.row input {
  flex: 1;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}
.root-row { flex: 1; display: flex; gap: 6px; min-width: 0; }
.root-row input { min-width: 0; font-family: monospace; font-size: 12px; }
.hint { margin: 0; font-size: 12px; }
.hint code, .preview-path { font-family: monospace; font-size: 12px; color: var(--text-color); }
.preview-path { word-break: break-all; }
.warn { margin: 0; font-size: 12px; color: var(--warn, #c90); }
.actions { margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.muted { color: var(--text-color-secondary, #888); font-size: 13px; }

.btn {
  align-self: flex-start;
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
}
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}
</style>
