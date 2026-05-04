<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ApiError, streamUrl } from '../../api/client'
import * as promptApi from '../../api/prompt'
import { usePromptStore } from '../../stores/prompt'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import type { Media } from '../../types/media'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const promptStore = usePromptStore()
const toast = useToast()

type Mode = 'generate' | 'transform' | 'clean'

const mode = ref<Mode>('generate')
const target = ref<promptApi.TargetModel>(promptStore.settings.target_model)
const architecture = ref<promptApi.Architecture>(promptStore.settings.architecture)
const styles = ref<promptApi.StyleEnhancement[]>([...promptStore.settings.styles])
const temperature = ref(promptStore.settings.temperature)
const maxTokens = ref(promptStore.settings.max_tokens)
const sourcePrompt = ref(props.media.prompt ?? '')

const generated = ref('')
const generating = ref(false)
const error = ref<string | null>(null)
const elapsedMs = ref<number | null>(null)
const dirty = ref(false)  // generated text modified since last save / clear

let abortCtrl: AbortController | null = null

const STYLE_OPTIONS: promptApi.StyleEnhancement[] = [
  'anime', 'photorealistic', 'cinematic', 'cartoon',
  'watercolor', 'oil-painting', 'comic',
  'hyperdetailed', 'minimalist', 'moody-lighting',
]
const TARGET_OPTIONS: promptApi.TargetModel[] = [
  'sdxl', 'flux-chroma', 'qwen-t2i', 'pony',
]

const hasExistingPrompt = computed(() => sourcePrompt.value.trim().length > 0)
const transformDisabled = computed(() => !hasExistingPrompt.value)
const cleanDisabled = computed(() => !hasExistingPrompt.value)
const stylesAtMax = computed(() => styles.value.length >= 3)

const fullImageUrl = computed(() => streamUrl(props.media.file_path))

const savedPrompts = computed(() =>
  promptStore.savedByPath[props.media.file_path] ?? [],
)

onMounted(() => {
  promptStore.loadSavedPrompts(props.media.file_path).catch(() => {/* non-fatal */})
})

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
})

watch(
  [mode, target, architecture, styles, temperature, maxTokens],
  () => {
    promptStore.settings.target_model = target.value
    promptStore.settings.architecture = architecture.value
    promptStore.settings.styles = [...styles.value]
    promptStore.settings.temperature = temperature.value
    promptStore.settings.max_tokens = maxTokens.value
    promptStore.persistSettings()
  },
  { deep: true },
)

function toggleStyle(s: promptApi.StyleEnhancement) {
  const idx = styles.value.indexOf(s)
  if (idx >= 0) {
    styles.value.splice(idx, 1)
  } else if (!stylesAtMax.value) {
    styles.value.push(s)
  } else {
    toast.show('Up to 3 styles', 'warn')
  }
}

async function run() {
  if (generating.value) return
  generating.value = true
  error.value = null
  elapsedMs.value = null
  abortCtrl = new AbortController()
  try {
    let resp: promptApi.GenerateResponse
    if (mode.value === 'generate') {
      resp = await promptApi.generatePrompt(
        {
          file_path: props.media.file_path,
          target_model: target.value,
          architecture: architecture.value,
          styles: [...styles.value],
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    } else if (mode.value === 'transform') {
      resp = await promptApi.transformPrompt(
        {
          source_prompt: sourcePrompt.value,
          target_model: target.value,
          architecture: architecture.value,
          file_path: props.media.file_path,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    } else {
      resp = await promptApi.cleanPrompt(
        {
          source_prompt: sourcePrompt.value,
          temperature: temperature.value,
          max_tokens: maxTokens.value,
        },
        abortCtrl.signal,
      )
    }
    generated.value = resp.prompt
    elapsedMs.value = resp.elapsed_ms
    dirty.value = true
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      // user-initiated stop; not an error
      return
    }
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    generating.value = false
    abortCtrl = null
  }
}

function stop() {
  if (abortCtrl) abortCtrl.abort()
}

async function copyGenerated() {
  if (!generated.value) return
  await copyToClipboard(generated.value)
  toast.show('Copied to clipboard', 'success')
}

async function regenerate() {
  generated.value = ''
  await run()
}

async function saveCurrent() {
  if (!generated.value.trim()) return
  const name = window.prompt('Name this prompt:')
  if (!name) return
  try {
    await promptStore.savePrompt({
      file_path: props.media.file_path,
      name,
      prompt: generated.value,
      target_model: target.value,
      architecture: architecture.value,
      styles: [...styles.value],
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      source_prompt: mode.value !== 'generate' ? sourcePrompt.value : null,
      mode: mode.value,
      negative: null,
      vlm_model_id: null,
    })
    dirty.value = false
    toast.show(`Saved "${name}"`, 'success')
  } catch (e) {
    toast.show(`Save failed: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  }
}

function tryClose() {
  if (generating.value && abortCtrl) abortCtrl.abort()
  if (dirty.value && generated.value.trim()) {
    if (!window.confirm('Discard unsaved generated prompt?')) return
  }
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="tryClose">
    <div class="dialog-card playground-card">
      <div class="dialog-header">
        <h3>Prompt Playground</h3>
        <button class="close-btn" @click="tryClose" title="Close">×</button>
      </div>

      <div class="playground-body">
        <!-- Top row: image + controls -->
        <div class="top-row">
          <img class="preview-img" :src="fullImageUrl" :alt="media.file_name ?? ''" />

          <div class="controls">
            <div class="ctrl-row">
              <span class="ctrl-label">Mode</span>
              <label><input type="radio" v-model="mode" value="generate" /> Generate</label>
              <label :class="{ disabled: transformDisabled }">
                <input type="radio" v-model="mode" value="transform" :disabled="transformDisabled" />
                Transform
              </label>
              <label :class="{ disabled: cleanDisabled }">
                <input type="radio" v-model="mode" value="clean" :disabled="cleanDisabled" />
                Clean
              </label>
            </div>

            <div class="ctrl-row" v-if="mode !== 'clean'">
              <span class="ctrl-label">Target model</span>
              <select v-model="target">
                <option v-for="t in TARGET_OPTIONS" :key="t" :value="t">
                  {{ promptApi.TARGET_MODEL_LABELS[t] }}
                </option>
              </select>
            </div>

            <div class="ctrl-row" v-if="mode === 'generate'">
              <span class="ctrl-label">Styles ({{ styles.length }}/3)</span>
              <div class="style-chips">
                <button
                  v-for="s in STYLE_OPTIONS"
                  :key="s"
                  type="button"
                  class="chip"
                  :class="{ active: styles.includes(s), disabled: !styles.includes(s) && stylesAtMax }"
                  @click="toggleStyle(s)"
                >{{ promptApi.STYLE_LABELS[s] }}</button>
              </div>
            </div>

            <div class="ctrl-row">
              <span class="ctrl-label">Temperature</span>
              <input type="range" min="0" max="1.5" step="0.05" v-model.number="temperature" />
              <span class="ctrl-value">{{ temperature.toFixed(2) }}</span>
            </div>

            <div class="ctrl-row">
              <span class="ctrl-label">Max tokens</span>
              <input type="range" min="50" max="1000" step="10" v-model.number="maxTokens" />
              <span class="ctrl-value">{{ maxTokens }}</span>
            </div>
          </div>
        </div>

        <!-- Existing prompt (for transform/clean mode) -->
        <div class="section" v-if="mode !== 'generate'">
          <label class="section-label">Existing prompt</label>
          <textarea
            v-model="sourcePrompt"
            class="prompt-area"
            rows="4"
            :placeholder="hasExistingPrompt ? '' : '(no embedded prompt — paste one to transform)'"
          />
        </div>

        <!-- Run controls -->
        <div class="run-row">
          <button class="primary" :disabled="generating" @click="run">
            {{ generating ? 'Generating…' : (generated ? 'Re-run' : 'Generate') }}
          </button>
          <button v-if="generating" class="secondary" @click="stop">Stop</button>
          <span v-if="elapsedMs !== null && !generating" class="elapsed">
            {{ (elapsedMs / 1000).toFixed(1) }}s
          </span>
          <span v-if="error" class="error">{{ error }}</span>
        </div>

        <!-- Generated -->
        <div class="section">
          <label class="section-label">Generated prompt</label>
          <textarea
            v-model="generated"
            @input="dirty = true"
            class="prompt-area"
            rows="6"
            placeholder="(generated prompt will appear here)"
          />
          <div class="action-row">
            <button :disabled="!generated.trim()" @click="copyGenerated">Copy</button>
            <button :disabled="!generated.trim()" @click="saveCurrent">Save…</button>
            <button :disabled="!generated.trim()" @click="regenerate">Regenerate</button>
          </div>
        </div>

        <!-- Saved list -->
        <div class="section" v-if="savedPrompts.length">
          <label class="section-label">Saved for this image</label>
          <div v-for="p in savedPrompts" :key="p.id" class="saved-row">
            <span class="saved-name">{{ p.name }}</span>
            <span class="saved-meta">{{ p.target_model }} · {{ p.architecture }}</span>
            <button class="link-btn" @click="generated = p.prompt; dirty = false">Load</button>
            <button class="link-btn danger" @click="promptStore.deleteSavedPrompt(p.id, media.file_path)">Delete</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.playground-card { width: min(960px, 95vw); max-height: 90vh; display: flex; flex-direction: column; }
.dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border, #2a2a2a); }
.dialog-header h3 { margin: 0; }
.close-btn { background: none; border: none; font-size: 20px; cursor: pointer; color: inherit; }
.playground-body { padding: 12px 16px; overflow: auto; display: flex; flex-direction: column; gap: 14px; }

.top-row { display: flex; gap: 16px; align-items: flex-start; }
.preview-img { width: 320px; height: auto; max-height: 320px; object-fit: contain; background: #000; border-radius: 6px; flex-shrink: 0; }
.controls { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.ctrl-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ctrl-label { font-size: 12px; opacity: 0.75; min-width: 110px; }
.ctrl-value { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; opacity: 0.8; }
.ctrl-row label.disabled { opacity: 0.4; }

.style-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { font-size: 11px; padding: 3px 8px; border-radius: 12px; border: 1px solid var(--border, #444); background: transparent; color: inherit; cursor: pointer; }
.chip.active { background: var(--primary, #3b82f6); color: white; border-color: var(--primary, #3b82f6); }
.chip.disabled { opacity: 0.35; cursor: not-allowed; }

.section { display: flex; flex-direction: column; gap: 4px; }
.section-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }
.prompt-area { width: 100%; box-sizing: border-box; resize: vertical; font-family: inherit; font-size: 13px; padding: 8px; background: var(--bg-2, #1a1a1a); color: inherit; border: 1px solid var(--border, #2a2a2a); border-radius: 4px; }

.run-row { display: flex; align-items: center; gap: 12px; }
.run-row .primary { padding: 6px 14px; background: var(--primary, #3b82f6); color: white; border: none; border-radius: 4px; cursor: pointer; }
.run-row .primary:disabled { opacity: 0.6; cursor: not-allowed; }
.run-row .secondary { padding: 6px 14px; background: transparent; border: 1px solid var(--border, #444); color: inherit; border-radius: 4px; cursor: pointer; }
.run-row .elapsed { font-size: 12px; opacity: 0.7; }
.run-row .error { color: var(--danger, #ef4444); font-size: 12px; }
.action-row { display: flex; gap: 8px; margin-top: 4px; }
.action-row button { padding: 4px 10px; background: transparent; border: 1px solid var(--border, #444); color: inherit; border-radius: 4px; cursor: pointer; font-size: 12px; }
.action-row button:disabled { opacity: 0.4; cursor: not-allowed; }

.saved-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border, #2a2a2a); }
.saved-name { font-weight: 600; flex: 1; }
.saved-meta { font-size: 11px; opacity: 0.7; }
.link-btn { background: none; border: none; color: var(--primary, #3b82f6); cursor: pointer; font-size: 12px; }
.link-btn.danger { color: var(--danger, #ef4444); }
</style>
