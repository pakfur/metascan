<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { listPresets } from '../../api/comfy'
import { previewI2vOutput } from '../../api/i2v'
import DirectoryPicker from './DirectoryPicker.vue'
import type { WorkflowPreset } from '../../types/storyboard'
import PresetRegistrationDialog from '../storyboard/PresetRegistrationDialog.vue'

const loading = ref(true)
// Keep the raw section: PUT /api/config is a shallow top-level merge, so
// we spread-merge to avoid clobbering keys we don't edit.
const i2vRaw = ref<Record<string, unknown>>({})
const fastPresetId = ref<number | null>(null)
const qualityPresetId = ref<number | null>(null)
const durationsText = ref('6, 10, 15, 20')
const defaultDuration = ref(6)
const defaultQuality = ref<'fast' | 'quality'>('fast')
const megapixelsText = ref('0.25, 0.5, 0.75, 1')
const defaultMegapixels = ref(0.75)
// Must match backend/config.py::I2V_DEFAULT_OUTPUT_PREFIX.
const DEFAULT_OUTPUT_PREFIX = '/%Y-%m-%d/i2v_'
const outputRoot = ref('')
const outputPrefix = ref(DEFAULT_OUTPUT_PREFIX)
const showPicker = ref(false)
const preview = ref<{ path: string | null; error: string | null; warnings: string[] }>({
  path: null,
  error: null,
  warnings: [],
})
const presets = ref<WorkflowPreset[]>([])
const showRegister = ref(false)
const saved = ref(false)

// ref2v presets usable by the i2v flow: tagged minimax/i2va or untagged.
const eligible = computed(() =>
  presets.value.filter(
    (p) =>
      p.kind === 'ref2v' &&
      (!p.video_mode || (p.video_target === 'minimax' && p.video_mode === 'i2va')),
  ),
)

const parsedDurations = computed(() =>
  durationsText.value
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && n > 0),
)

const parsedMegapixels = computed(() =>
  megapixelsText.value
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && n > 0),
)

onMounted(load)

// Live "would be saved as" preview, resolved by the server so the date
// expansion and path rules have exactly one implementation. Debounced:
// it fires on every keystroke in the prefix box.
let previewTimer: ReturnType<typeof setTimeout> | null = null
let previewSeq = 0
async function refreshPreview() {
  const seq = ++previewSeq
  try {
    const res = await previewI2vOutput(outputRoot.value, outputPrefix.value)
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
  try {
    const [config, presetRows] = await Promise.all([fetchConfig(), listPresets()])
    presets.value = presetRows
    i2vRaw.value = (config.i2v as Record<string, unknown>) || {}
    fastPresetId.value = (i2vRaw.value.fast_preset_id as number) ?? null
    qualityPresetId.value = (i2vRaw.value.quality_preset_id as number) ?? null
    const durations = (i2vRaw.value.durations as number[]) || [6, 10, 15, 20]
    durationsText.value = durations.join(', ')
    defaultDuration.value = (i2vRaw.value.default_duration as number) ?? durations[0]
    defaultQuality.value =
      (i2vRaw.value.default_quality as 'fast' | 'quality') ?? 'fast'
    const mps = (i2vRaw.value.megapixels as number[]) || [0.25, 0.5, 0.75, 1]
    megapixelsText.value = mps.join(', ')
    defaultMegapixels.value =
      (i2vRaw.value.default_megapixels as number) ?? 0.75
    outputRoot.value =
      typeof i2vRaw.value.output_root === 'string' ? i2vRaw.value.output_root : ''
    // An explicit '' is a saved choice; only an absent key gets the default.
    outputPrefix.value =
      typeof i2vRaw.value.output_prefix === 'string'
        ? i2vRaw.value.output_prefix
        : DEFAULT_OUTPUT_PREFIX
    void refreshPreview()
  } finally {
    loading.value = false
  }
}

async function save() {
  const durations = parsedDurations.value.length
    ? parsedDurations.value
    : [6, 10, 15, 20]
  const megapixels = parsedMegapixels.value.length
    ? parsedMegapixels.value
    : [0.25, 0.5, 0.75, 1]
  const i2v = {
    ...i2vRaw.value,
    fast_preset_id: fastPresetId.value,
    quality_preset_id: qualityPresetId.value,
    durations,
    default_duration: durations.includes(defaultDuration.value)
      ? defaultDuration.value
      : durations[0],
    default_quality: defaultQuality.value,
    megapixels,
    default_megapixels: megapixels.includes(defaultMegapixels.value)
      ? defaultMegapixels.value
      : megapixels[0],
    output_root: outputRoot.value.trim(),
    output_prefix: outputPrefix.value.trim(),
  }
  await updateConfig({ i2v })
  i2vRaw.value = i2v
  saved.value = true
  setTimeout(() => (saved.value = false), 1500)
}

async function onRegistered() {
  showRegister.value = false
  presets.value = await listPresets()
}
</script>

<template>
  <div class="i2v-tab">
    <div v-if="loading" class="muted">Loading...</div>
    <template v-else>
      <h4>Image to Video (MiniMax H3)</h4>

      <label class="row">
        <span>Fast preset (turbo lora)</span>
        <select v-model="fastPresetId">
          <option :value="null">— none —</option>
          <option v-for="p in eligible" :key="p.id" :value="p.id">
            {{ p.name }}{{ p.video_mode ? '' : ' (untagged)' }}
          </option>
        </select>
      </label>
      <label class="row">
        <span>High-quality preset</span>
        <select v-model="qualityPresetId">
          <option :value="null">— none —</option>
          <option v-for="p in eligible" :key="p.id" :value="p.id">
            {{ p.name }}{{ p.video_mode ? '' : ' (untagged)' }}
          </option>
        </select>
      </label>
      <p v-if="!eligible.length" class="muted">
        No eligible presets. Register a ref2v workflow tagged minimax / i2va.
      </p>
      <button class="btn" @click="showRegister = true">Register workflow…</button>

      <h4>Durations</h4>
      <label class="row">
        <span>Choices (seconds, comma-separated)</span>
        <input v-model="durationsText" />
      </label>
      <label class="row">
        <span>Default duration</span>
        <select v-model.number="defaultDuration">
          <option v-for="d in parsedDurations" :key="d" :value="d">{{ d }}s</option>
        </select>
      </label>
      <h4>Output size</h4>
      <p class="muted">
        Orientation always follows the source image; only the pixel budget is
        chosen. Needs an MS_RESOLUTION node in the workflow.
      </p>
      <label class="row">
        <span>Choices (megapixels, comma-separated)</span>
        <input v-model="megapixelsText" />
      </label>
      <label class="row">
        <span>Default size</span>
        <select v-model.number="defaultMegapixels">
          <option v-for="m in parsedMegapixels" :key="m" :value="m">{{ m }} MP</option>
        </select>
      </label>

      <label class="row">
        <span>Default quality</span>
        <select v-model="defaultQuality">
          <option value="fast">Fast</option>
          <option value="quality">High quality</option>
        </select>
      </label>

      <h4>Output files</h4>
      <p class="muted">
        Where generated clips are saved. Leave the directory empty to keep the
        default location under the ComfyUI output root.
      </p>
      <label class="row">
        <span>Root directory</span>
        <span class="root-row">
          <input
            v-model="outputRoot"
            spellcheck="false"
            placeholder="(default — ComfyUI output root)"
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
        <input
          v-model="outputPrefix"
          spellcheck="false"
          :disabled="!outputRoot.trim()"
          :placeholder="DEFAULT_OUTPUT_PREFIX"
        />
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
        Next clip: <code class="preview-path">{{ preview.path }}</code>
      </p>

      <div class="actions">
        <button class="btn primary" @click="save">Save</button>
        <span v-if="saved" class="muted">Saved.</span>
      </div>
    </template>

    <DirectoryPicker
      v-if="showPicker"
      title="Root directory for generated videos"
      :initial-path="outputRoot"
      @select="onPickRoot"
      @close="showPicker = false"
    />

    <PresetRegistrationDialog
      v-if="showRegister"
      initial-mode="i2va"
      @close="showRegister = false"
      @registered="onRegistered"
    />
  </div>
</template>

<style scoped>
.i2v-tab { display: flex; flex-direction: column; gap: 10px; }
.i2v-tab h4 { margin: 12px 0 4px; font-size: 14px; color: var(--text-color); }
.i2v-tab h4:first-child { margin-top: 0; }
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
.row input:disabled { opacity: 0.55; }
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
.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}
</style>
