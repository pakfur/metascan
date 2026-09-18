<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { listPresets } from '../../api/comfy'
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

onMounted(load)

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
  } finally {
    loading.value = false
  }
}

async function save() {
  const durations = parsedDurations.value.length
    ? parsedDurations.value
    : [6, 10, 15, 20]
  const i2v = {
    ...i2vRaw.value,
    fast_preset_id: fastPresetId.value,
    quality_preset_id: qualityPresetId.value,
    durations,
    default_duration: durations.includes(defaultDuration.value)
      ? defaultDuration.value
      : durations[0],
    default_quality: defaultQuality.value,
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
      <label class="row">
        <span>Default quality</span>
        <select v-model="defaultQuality">
          <option value="fast">Fast</option>
          <option value="quality">High quality</option>
        </select>
      </label>

      <div class="actions">
        <button class="btn primary" @click="save">Save</button>
        <span v-if="saved" class="muted">Saved.</span>
      </div>
    </template>

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
