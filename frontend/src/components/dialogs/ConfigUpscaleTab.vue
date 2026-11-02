<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { listPresets } from '../../api/comfy'
import type { WorkflowPreset } from '../../types/storyboard'
import PresetRegistrationDialog from '../storyboard/PresetRegistrationDialog.vue'

const loading = ref(true)
// Keep the raw section: PUT /api/config is a shallow top-level merge, so we
// spread-merge to avoid clobbering keys we don't edit (original_root).
const raw = ref<Record<string, unknown>>({})
const presets = ref<WorkflowPreset[]>([])
const imagePresetId = ref<number | null>(null)
const videoPresetId = ref<number | null>(null)
const maxCount = ref(8)
const windowSize = ref(2)
const scalesText = ref('2, 4')
const defaultScale = ref(2)
const registering = ref<'upscale_image' | 'upscale_video' | null>(null)
const saved = ref(false)
const saveError = ref<string | null>(null)

const imagePresets = computed(() => presets.value.filter((p) => p.kind === 'upscale_image'))
const videoPresets = computed(() => presets.value.filter((p) => p.kind === 'upscale_video'))
const parsedScales = computed(() =>
  scalesText.value
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && n > 0),
)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [config, rows] = await Promise.all([fetchConfig(), listPresets()])
    presets.value = rows
    raw.value = (config.upscale as Record<string, unknown>) || {}
    imagePresetId.value = (raw.value.default_image_preset_id as number) ?? null
    videoPresetId.value = (raw.value.default_video_preset_id as number) ?? null
    maxCount.value = (raw.value.max_count as number) ?? 8
    windowSize.value = (raw.value.window as number) ?? 2
    const scales = (raw.value.scales as number[]) || [2, 4]
    scalesText.value = scales.join(', ')
    defaultScale.value = (raw.value.default_scale as number) ?? scales[0]
  } finally {
    loading.value = false
  }
}

async function save() {
  saveError.value = null
  const scales = parsedScales.value.length ? parsedScales.value : [2, 4]
  const upscale = {
    ...raw.value,
    default_image_preset_id: imagePresetId.value,
    default_video_preset_id: videoPresetId.value,
    max_count: Math.max(1, Math.floor(maxCount.value) || 1),
    window: Math.max(1, Math.floor(windowSize.value) || 1),
    scales,
    default_scale: scales.includes(defaultScale.value) ? defaultScale.value : scales[0],
  }
  try {
    await updateConfig({ upscale })
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
    return
  }
  raw.value = upscale
  saved.value = true
  setTimeout(() => (saved.value = false), 1500)
}

async function onRegistered() {
  registering.value = null
  presets.value = await listPresets()
}
</script>

<template>
  <div class="up-tab">
    <div v-if="loading" class="muted">Loading...</div>
    <template v-else>
      <h4>Image workflows</h4>
      <p class="muted">
        Any registered image workflow can be picked for any image. Title the
        loader MS_SOURCE_IMAGE and the save node MS_SAVE.
      </p>
      <label class="row">
        <span>Default</span>
        <select v-model="imagePresetId">
          <option :value="null">— first in the list —</option>
          <option v-for="p in imagePresets" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </label>
      <p v-if="!imagePresets.length" class="muted">No image workflows registered.</p>
      <button class="btn" @click="registering = 'upscale_image'">Manage image workflows…</button>

      <h4>Video workflows</h4>
      <p class="muted">
        Any registered video workflow can be picked for any video. Title the
        loader MS_SOURCE_VIDEO and the save node MS_SAVE, and wire the audio
        through, or the upscaled video has no soundtrack.
      </p>
      <label class="row">
        <span>Default</span>
        <select v-model="videoPresetId">
          <option :value="null">— first in the list —</option>
          <option v-for="p in videoPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </label>
      <p v-if="!videoPresets.length" class="muted">No video workflows registered.</p>
      <button class="btn" @click="registering = 'upscale_video'">Manage video workflows…</button>

      <h4>Optional inputs</h4>
      <p class="muted">
        A workflow with an MS_SEED, MS_POSITIVE or MS_SCALE node gets that
        control in the Upscale window.
      </p>
      <label class="row">
        <span>Scales (comma-separated)</span>
        <input v-model="scalesText" />
      </label>
      <label class="row">
        <span>Default scale</span>
        <select v-model.number="defaultScale">
          <option v-for="s in parsedScales" :key="s" :value="s">×{{ s }}</option>
        </select>
      </label>

      <h4>Runs</h4>
      <label class="row">
        <span>Most results in one run</span>
        <input v-model.number="maxCount" type="number" min="1" />
      </label>
      <label class="row">
        <span>Jobs in ComfyUI at once, per run</span>
        <input v-model.number="windowSize" type="number" min="1" />
      </label>

      <div class="actions">
        <button class="btn primary" @click="save">Save</button>
        <span v-if="saved" class="muted">Saved.</span>
        <span v-if="saveError" class="warn">{{ saveError }}</span>
      </div>
    </template>

    <PresetRegistrationDialog
      v-if="registering"
      :kind="registering"
      @close="registering = null"
      @registered="onRegistered"
    />
  </div>
</template>

<style scoped>
.up-tab { display: flex; flex-direction: column; gap: 10px; }
.up-tab h4 { margin: 12px 0 4px; font-size: 14px; color: var(--text-color); }
.up-tab h4:first-child { margin-top: 0; }
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
.actions { margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.muted { margin: 0; color: var(--text-color-secondary, #888); font-size: 13px; }
.warn { font-size: 12px; color: var(--warn, #c90); }
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
