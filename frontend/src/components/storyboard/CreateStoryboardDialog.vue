<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { listPresets } from '../../api/comfy'
import { patchStoryboard } from '../../api/storyboard'
import { ApiError } from '../../api/client'
import {
  ASPECT_RATIOS,
  TARGET_MODELS,
  VIDEO_MODES,
  STORY_SCALES,
  STORY_SCALE_LABELS,
} from '../../types/storyboard'
import type { WorkflowPreset } from '../../types/storyboard'
import TextEditPopup from './TextEditPopup.vue'

const emit = defineEmits<{
  close: []
  created: [id: number]
  'open-presets': []
}>()

const store = useStoryboardStore()

const name = ref('')
const aspectRatio = ref<string>('16:9')
const storyScale = ref<string>('standard')

const videoTarget = ref<string>('minimax')
const videoMode = ref<string>('ref2va')
const videoPresetId = ref<number | null>(null)

const presets = ref<WorkflowPreset[]>([])
const presetsLoading = ref(true)
const submitting = ref(false)
const errorMsg = ref<string | null>(null)

const videoPresets = computed(() => presets.value.filter((p) => p.kind === 'ref2v'))

onMounted(async () => {
  presetsLoading.value = true
  try {
    presets.value = await listPresets()
  } catch {
    // Non-fatal: the "no presets" hint still shows a usable path forward.
    presets.value = []
  } finally {
    presetsLoading.value = false
  }
})

function close() {
  emit('close')
}

function openPresets() {
  // Registration is a full dialog, not something CreateStoryboardDialog
  // nests -- hand off to the landing page and close this one.
  emit('open-presets')
  emit('close')
}

async function submit() {
  const trimmedName = name.value.trim()
  if (!trimmedName) return
  errorMsg.value = null
  submitting.value = true
  try {
    // target_model is required by the create route (NOT NULL column) but no
    // longer user-facing -- the UX is video-only, so a default is sent
    // silently and only aspect_ratio remains a real choice.
    const body: {
      name: string
      target_model: string
      aspect_ratio: string
    } = {
      name: trimmedName,
      target_model: TARGET_MODELS[0],
      aspect_ratio: aspectRatio.value,
    }

    const id = await store.create(body)
    // Video config and story length ride a follow-up PATCH (the create
    // route doesn't accept them). The board already exists at this
    // point, so a failure here must not strand the user on the create
    // dialog — Settings can finish the setup.
    const patch: Record<string, unknown> = {}
    if (videoTarget.value) {
      patch.video_target = videoTarget.value
      patch.video_mode = videoMode.value
      patch.video_preset_id = videoPresetId.value
    }
    if (storyScale.value !== 'standard') {
      patch.story_scale = storyScale.value
    }
    if (Object.keys(patch).length > 0) {
      try {
        await patchStoryboard(id, patch)
      } catch {
        // Non-fatal: finish the setup later via Settings.
      }
    }
    emit('created', id)
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close">
    <div class="dialog-card">
      <h3>New storyboard</h3>

      <div class="field">
        <label for="sb-name">Name</label>
        <TextEditPopup title="Name" :value="name" @save="name = $event">
          <InputText id="sb-name" v-model="name" placeholder="e.g. Coffee shop meet-cute" />
        </TextEditPopup>
      </div>

      <div class="field">
        <label for="sb-ar">Aspect ratio</label>
        <select id="sb-ar" v-model="aspectRatio">
          <option v-for="ar in ASPECT_RATIOS" :key="ar" :value="ar">{{ ar }}</option>
        </select>
      </div>

      <div class="field">
        <label for="sb-scale">Story length</label>
        <select id="sb-scale" v-model="storyScale">
          <option v-for="s in STORY_SCALES" :key="s" :value="s">
            {{ STORY_SCALE_LABELS[s] }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="sb-video-target">Video target</label>
        <select id="sb-video-target" v-model="videoTarget">
          <option value="">None</option>
          <option value="minimax">MiniMax H3</option>
        </select>
      </div>

      <div v-if="videoTarget" class="field-row">
        <div class="field">
          <label for="sb-video-mode">Video mode</label>
          <select id="sb-video-mode" v-model="videoMode">
            <option v-for="m in VIDEO_MODES" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>

        <div class="field">
          <label for="sb-video-preset">Video workflow preset</label>
          <select id="sb-video-preset" v-model="videoPresetId" :disabled="presetsLoading">
            <option :value="null">None</option>
            <option v-for="p in videoPresets" :key="p.id" :value="p.id">
              {{ p.name }}
            </option>
          </select>
          <p v-if="!presetsLoading && videoPresets.length === 0" class="hint">
            No ref2v presets yet.
            <button type="button" class="link-btn" @click="openPresets">Register one</button>
          </p>
        </div>
      </div>

      <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

      <div class="dialog-actions">
        <button
          class="btn-primary"
          :disabled="!name.trim() || submitting"
          @click="submit"
        >
          {{ submitting ? 'Creating…' : 'Create' }}
        </button>
        <button class="btn-secondary" @click="close">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 480px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 16px;
  font-size: 18px;
  color: var(--text-color);
}

.field {
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-row {
  display: flex;
  gap: 12px;
}

.field-row .field {
  flex: 1;
}

label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

select,
input[type='number'],
textarea {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

select:focus,
input:focus,
textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

textarea {
  resize: vertical;
}

.hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.link-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  cursor: pointer;
  font-size: 12px;
  text-decoration: underline;
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 4px 0 0;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}
.btn-secondary:hover {
  background: var(--surface-hover);
}
</style>
