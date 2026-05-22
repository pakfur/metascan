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
const cardRef = ref<HTMLElement | null>(null)

type Mode = 'generate' | 'transform' | 'clean'

const mode = ref<Mode>('generate')
const target = ref<promptApi.TargetModel>(promptStore.settings.target_model)
const architecture = ref<promptApi.Architecture>(promptStore.settings.architecture)
const extras = ref<promptApi.ExtraOption[]>([...promptStore.settings.extras])
const temperature = ref(promptStore.settings.temperature)
const maxTokens = ref(promptStore.settings.max_tokens)
const prefix = ref(promptStore.settings.prefix)
const suffix = ref(promptStore.settings.suffix)
const sourcePrompt = ref(props.media.prompt ?? '')
const showExtras = ref(true)

const generated = ref('')
const generatedNegative = ref('')
const generating = ref(false)
const error = ref<string | null>(null)
const elapsedMs = ref<number | null>(null)
const dirty = ref(false)  // generated text modified since last save / clear

let abortCtrl: AbortController | null = null

const TARGET_OPTIONS: promptApi.TargetModel[] = [...promptApi.TARGET_MODEL_ORDER]

const hasExistingPrompt = computed(() => sourcePrompt.value.trim().length > 0)
const transformDisabled = computed(() => !hasExistingPrompt.value)
const cleanDisabled = computed(() => !hasExistingPrompt.value)

const currentPreset = computed(() => promptApi.TARGET_PRESETS[target.value])
const showTargetControls = computed(() => mode.value !== 'clean')
const showExtrasControls = computed(() => mode.value !== 'clean')
// Negative-prompt textarea is only relevant for generate-mode targets
// whose meta-prompts ask Qwen3 for a Negative block (sd / pony /
// chroma / qwen). Hidden for Flux.1, Flux.2, Z-Image, transform, clean.
const showNegative = computed(
  () => mode.value === 'generate' && currentPreset.value.hasNegative,
)

// Per-element policy table. Each row carries the row's read-only title,
// the user-selected policy (Extract / Override / Auto), and an override
// value used only when policy === 'override'. ``defaultPolicy`` controls
// whether the row is locked (AUTO rows from the meta-prompt's structural
// elements like Pony's score/source/rating block). ``defaultBody`` is
// shown as placeholder/help text — it's the meta-prompt's instruction
// for the row, useful as a hint when typing an override value.
//
// Reset on every target change — per spec, edits do not survive
// switching models. Only used in generate mode; transform/clean use the
// legacy builders.
type ElementRow = {
  title: string
  defaultBody: string
  defaultPolicy: promptApi.Policy
  policy: promptApi.Policy
  body: string
}
const elementRows = ref<ElementRow[]>([])
const showElementTable = computed(
  () => mode.value === 'generate' && elementRows.value.length > 0,
)

function setRowPolicy(row: ElementRow, next: promptApi.Policy) {
  if (row.defaultPolicy === 'auto') return  // locked
  row.policy = next
}

async function loadElementsForTarget(t: promptApi.TargetModel) {
  try {
    const resp = await promptApi.getElements(t)
    elementRows.value = resp.elements.map((e) => ({
      title: e.title,
      defaultBody: e.default_body,
      defaultPolicy: e.default_policy,
      policy: e.default_policy,
      body: '',
    }))
  } catch {
    // Non-fatal: the table just stays empty and the meta-prompt's
    // built-in defaults are used. Generate still works.
    elementRows.value = []
  }
}

const fullImageUrl = computed(() => streamUrl(props.media.file_path))

const savedPrompts = computed(() =>
  promptStore.savedByPath[props.media.file_path] ?? [],
)

const assembledPrompt = computed(() => {
  const body = generated.value
  if (!body.trim()) return ''
  return `${prefix.value}${body}${suffix.value}`
})

function isExtraDisabled(_key: promptApi.ExtraOption): boolean {
  // All targets currently support all 16 options. The hook is here so future
  // targets can opt out by narrowing TARGET_PRESETS.supportedOptions.
  return false
}

function counterpartFor(key: promptApi.ExtraOption): promptApi.ExtraOption | null {
  for (const [a, b] of promptApi.MUTEX_PAIRS) {
    if (key === a) return b
    if (key === b) return a
  }
  return null
}

function isExtraChecked(key: promptApi.ExtraOption): boolean {
  return extras.value.includes(key)
}

function toggleExtra(key: promptApi.ExtraOption) {
  if (isExtraDisabled(key)) return
  const idx = extras.value.indexOf(key)
  if (idx >= 0) {
    extras.value.splice(idx, 1)
    return
  }
  // Adding — drop the mutex counterpart if it's currently checked.
  const counter = counterpartFor(key)
  if (counter) {
    const cidx = extras.value.indexOf(counter)
    if (cidx >= 0) extras.value.splice(cidx, 1)
  }
  extras.value.push(key)
}

onMounted(() => {
  promptStore.loadSavedPrompts(props.media.file_path).catch(() => {/* non-fatal */})
  // Populate the element table for the initially-selected target.
  // Fire-and-forget; the table just stays empty if the request fails.
  void loadElementsForTarget(target.value)
  cardRef.value?.focus()
})

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
})

// When target changes, refill prefix/suffix to the new preset defaults.
// Caption-length clamping is gone — the meta-prompts embed their own
// length guidance and the dropdown was removed.
watch(target, (next, prev) => {
  if (next === prev) return
  const preset = promptApi.TARGET_PRESETS[next]
  prefix.value = preset.prefix
  suffix.value = preset.suffix
  // A negative produced for the previous target won't apply to the new
  // target's UI (different model conventions); clear it to avoid stale
  // negatives leaking into a save.
  if (!preset.hasNegative) {
    generatedNegative.value = ''
  }
  // Per spec: edits to the element table are scoped to the current
  // target. Switching models refreshes from defaults; any in-progress
  // edits from the previous target are dropped.
  void loadElementsForTarget(next)
})

watch(
  [mode, target, architecture, extras, temperature, maxTokens, prefix, suffix],
  () => {
    promptStore.settings.target_model = target.value
    promptStore.settings.architecture = architecture.value
    promptStore.settings.extras = [...extras.value]
    promptStore.settings.temperature = temperature.value
    promptStore.settings.max_tokens = maxTokens.value
    promptStore.settings.prefix = prefix.value
    promptStore.settings.suffix = suffix.value
    promptStore.persistSettings()
  },
  { deep: true },
)

async function run() {
  if (generating.value) return
  generating.value = true
  error.value = null
  elapsedMs.value = null
  abortCtrl = new AbortController()
  try {
    let resp: promptApi.GenerateResponse
    if (mode.value === 'generate') {
      // Send per-row policy + override value. element_overrides[i] is
      // null for non-override rows so the backend can ignore them
      // cleanly. If the table didn't populate (e.g. /elements failed at
      // mount), both arrays are empty and the backend falls back to
      // per-row defaults.
      const policies = elementRows.value.map((r) => r.policy)
      const overrides = elementRows.value.map((r) =>
        r.policy === 'override' ? r.body : null,
      )
      resp = await promptApi.generatePrompt(
        {
          file_path: props.media.file_path,
          target_model: target.value,
          architecture: architecture.value,
          extras: [...extras.value],
          element_policies: policies,
          element_overrides: overrides,
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
          extras: [...extras.value],
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
    // resp.negative is populated only for generate mode against negative-
    // bearing targets; transform/clean leave it null. Clear stale values
    // so repeat runs don't carry an old negative from a prior target.
    generatedNegative.value = resp.negative ?? ''
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

async function copyAssembled() {
  if (!assembledPrompt.value) return
  await copyToClipboard(assembledPrompt.value)
  toast.show('Copied to clipboard', 'success')
}

async function regenerate() {
  generated.value = ''
  generatedNegative.value = ''
  await run()
}

async function saveCurrent() {
  if (!assembledPrompt.value.trim()) return
  const name = window.prompt('Name this prompt:')
  if (!name) return
  // Only persist a negative if (a) the target supports one and (b) the
  // user actually has text in the box. Empty strings save as null so
  // listeners can rely on truthiness without trimming.
  const trimmedNegative = generatedNegative.value.trim()
  const negativeToSave =
    showNegative.value && trimmedNegative ? trimmedNegative : null
  try {
    await promptStore.savePrompt({
      file_path: props.media.file_path,
      name,
      prompt: assembledPrompt.value,
      target_model: target.value,
      architecture: architecture.value,
      styles: [],
      temperature: temperature.value,
      max_tokens: maxTokens.value,
      source_prompt: mode.value !== 'generate' ? sourcePrompt.value : null,
      mode: mode.value,
      negative: negativeToSave,
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

function targetLabel(t: promptApi.TargetModel): string {
  return promptApi.TARGET_MODEL_LABELS[t]
}
</script>

<template>
  <div class="dialog-overlay" @click.self="tryClose">
    <div class="dialog-card playground-card" tabindex="-1" @keydown.esc.stop="tryClose" ref="cardRef">
      <div class="dialog-header">
        <h3>Prompt Playground</h3>
        <button class="close-btn" @click="tryClose" title="Close" aria-label="Close">×</button>
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

            <div class="ctrl-row" v-if="showTargetControls">
              <span class="ctrl-label">Target model</span>
              <select v-model="target">
                <option v-for="t in TARGET_OPTIONS" :key="t" :value="t">
                  {{ targetLabel(t) }}
                </option>
              </select>
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

        <!-- Extra options -->
        <div class="section" v-if="showExtrasControls">
          <button class="extras-toggle" type="button" @click="showExtras = !showExtras">
            <span class="chevron">{{ showExtras ? '▼' : '▶' }}</span>
            <span class="section-label">Extra options ({{ extras.length }})</span>
          </button>
          <div v-if="showExtras" class="extras-grid">
            <label
              v-for="opt in promptApi.EXTRA_OPTIONS"
              :key="opt.key"
              class="extra-row"
              :class="{ disabled: isExtraDisabled(opt.key) }"
              :title="opt.full"
            >
              <input
                type="checkbox"
                :disabled="isExtraDisabled(opt.key)"
                :checked="isExtraChecked(opt.key)"
                @change="toggleExtra(opt.key)"
              />
              <span>{{ opt.short }}</span>
            </label>
          </div>
        </div>

        <!-- Prefix / suffix -->
        <div class="section" v-if="showTargetControls">
          <div class="ctrl-row prefix-row">
            <span class="ctrl-label">Prefix</span>
            <input
              type="text"
              v-model="prefix"
              class="affix-input"
              placeholder="(prepended on copy / save)"
            />
          </div>
          <div class="ctrl-row prefix-row">
            <span class="ctrl-label">Suffix</span>
            <input
              type="text"
              v-model="suffix"
              class="affix-input"
              placeholder="(appended on copy / save)"
            />
          </div>
        </div>

        <!-- Per-element policy table. Each row picks Extract (pull from
             the image), Override (use the textarea value), or — for
             rows the meta-prompt structurally fixes (Pony's score/
             source/rating block) — Auto (locked, model-managed).
             Refreshes on every target change. Generate mode only. -->
        <div class="section" v-if="showElementTable">
          <label class="section-label">Prompt elements ({{ elementRows.length }})</label>
          <div class="element-table">
            <div
              v-for="(row, idx) in elementRows"
              :key="idx"
              class="element-row"
              :class="{ 'is-locked': row.defaultPolicy === 'auto' }"
            >
              <div class="element-title" :title="row.title">{{ row.title }}</div>

              <div class="element-policy">
                <template v-if="row.defaultPolicy === 'auto'">
                  <span class="policy-locked" title="This element is structurally fixed by the meta-prompt and cannot be overridden.">Auto · model-managed</span>
                </template>
                <template v-else>
                  <div class="policy-toggle" role="radiogroup" :aria-label="`Policy for ${row.title}`">
                    <button
                      type="button"
                      class="policy-btn"
                      :class="{ active: row.policy === 'extract' }"
                      :aria-pressed="row.policy === 'extract'"
                      @click="setRowPolicy(row, 'extract')"
                      title="Pull this element from the image"
                    >Extract</button>
                    <button
                      type="button"
                      class="policy-btn"
                      :class="{ active: row.policy === 'override' }"
                      :aria-pressed="row.policy === 'override'"
                      @click="setRowPolicy(row, 'override')"
                      title="Replace this element with the value below"
                    >Override</button>
                  </div>
                  <textarea
                    v-if="row.policy === 'override'"
                    v-model="row.body"
                    class="element-body"
                    rows="2"
                    spellcheck="false"
                    :placeholder="row.defaultBody"
                  />
                </template>
              </div>
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
          <div v-if="generated.trim() && (prefix || suffix)" class="assembled-preview">
            <span class="assembled-label">With prefix/suffix:</span>
            <code>{{ assembledPrompt }}</code>
          </div>
          <div class="action-row">
            <button :disabled="!assembledPrompt.trim()" @click="copyAssembled">Copy</button>
            <button :disabled="!assembledPrompt.trim()" @click="saveCurrent">Save…</button>
            <button :disabled="!generated.trim()" @click="regenerate">Regenerate</button>
          </div>
        </div>

        <!-- Negative prompt (only for sd / pony / chroma / qwen in generate mode) -->
        <div class="section" v-if="showNegative">
          <label class="section-label">Negative prompt</label>
          <textarea
            v-model="generatedNegative"
            @input="dirty = true"
            class="prompt-area"
            rows="3"
            placeholder="(negative prompt will appear here when the target supports one)"
          />
        </div>

        <!-- Saved list -->
        <div class="section" v-if="savedPrompts.length">
          <label class="section-label">Saved for this image</label>
          <div v-for="p in savedPrompts" :key="p.id" class="saved-row">
            <span class="saved-name">{{ p.name }}</span>
            <span class="saved-meta">{{ p.target_model }} · {{ p.architecture }}</span>
            <button class="link-btn" @click="generated = p.prompt; generatedNegative = p.negative ?? ''; dirty = false">Load</button>
            <button class="link-btn danger" @click="promptStore.deleteSavedPrompt(p.id, media.file_path)">Delete</button>
          </div>
        </div>
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
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.playground-card { width: min(960px, 95vw); max-height: 90vh; display: flex; flex-direction: column; }
.dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--surface-border); }
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

.section { display: flex; flex-direction: column; gap: 4px; }
.section-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }
.prompt-area { width: 100%; box-sizing: border-box; resize: vertical; font-family: inherit; font-size: 13px; padding: 8px; background: var(--surface-card); color: inherit; border: 1px solid var(--surface-border); border-radius: 4px; }

.extras-toggle { background: none; border: none; padding: 0; display: flex; align-items: center; gap: 6px; cursor: pointer; color: inherit; }
.extras-toggle .chevron { font-size: 10px; opacity: 0.6; }
.extras-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px; padding: 8px 4px 0 16px; }
.extra-row { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
.extra-row.disabled { opacity: 0.4; cursor: not-allowed; }
.extra-row input[type="checkbox"] { margin: 0; }

.prefix-row .affix-input { flex: 1; min-width: 200px; padding: 4px 8px; background: var(--surface-card); color: inherit; border: 1px solid var(--surface-border); border-radius: 4px; font-family: inherit; font-size: 12px; }

.assembled-preview { margin-top: 4px; padding: 6px 8px; background: var(--surface-card); border: 1px dashed var(--surface-border); border-radius: 4px; font-size: 11px; opacity: 0.85; word-break: break-word; }
.assembled-preview .assembled-label { display: block; opacity: 0.6; margin-bottom: 2px; }
.assembled-preview code { font-family: inherit; }

.run-row { display: flex; align-items: center; gap: 12px; }
.run-row .primary { padding: 6px 14px; background: var(--primary-color); color: white; border: none; border-radius: 4px; cursor: pointer; }
.run-row .primary:disabled { opacity: 0.6; cursor: not-allowed; }
.run-row .secondary { padding: 6px 14px; background: transparent; border: 1px solid var(--surface-border); color: inherit; border-radius: 4px; cursor: pointer; }
.run-row .elapsed { font-size: 12px; opacity: 0.7; }
.run-row .error { color: var(--danger-color); font-size: 12px; }
.action-row { display: flex; gap: 8px; margin-top: 4px; }
.action-row button { padding: 4px 10px; background: transparent; border: 1px solid var(--surface-border); color: inherit; border-radius: 4px; cursor: pointer; font-size: 12px; }
.action-row button:disabled { opacity: 0.4; cursor: not-allowed; }

.saved-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--surface-border); }
.saved-name { font-weight: 600; flex: 1; }
.saved-meta { font-size: 11px; opacity: 0.7; }
.link-btn { background: none; border: none; color: var(--primary-color); cursor: pointer; font-size: 12px; }
.link-btn.danger { color: var(--danger-color); }

.element-table { display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--surface-border); border-radius: 4px; padding: 4px; background: var(--surface-card); max-height: 360px; overflow-y: auto; }
.element-row { display: grid; grid-template-columns: 160px 1fr; gap: 8px; align-items: start; padding: 6px 4px; border-bottom: 1px solid var(--surface-border); }
.element-row:last-child { border-bottom: none; }
.element-row.is-locked { opacity: 0.7; }
.element-title { font-weight: 600; font-size: 12px; padding-top: 6px; overflow: hidden; text-overflow: ellipsis; word-break: break-word; }
.element-policy { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.policy-toggle { display: inline-flex; gap: 0; border: 1px solid var(--surface-border); border-radius: 4px; overflow: hidden; align-self: flex-start; }
.policy-btn { background: transparent; border: none; color: inherit; font-size: 11px; padding: 3px 10px; cursor: pointer; font-family: inherit; }
.policy-btn + .policy-btn { border-left: 1px solid var(--surface-border); }
.policy-btn.active { background: var(--primary-color); color: white; }
.policy-btn:not(.active):hover { background: var(--surface-section); }
.policy-locked { font-size: 11px; opacity: 0.75; padding-top: 4px; font-style: italic; }
.element-body { width: 100%; box-sizing: border-box; resize: vertical; font-family: inherit; font-size: 12px; padding: 6px 8px; background: var(--surface-section); color: inherit; border: 1px solid var(--surface-border); border-radius: 3px; min-height: 40px; }
</style>
