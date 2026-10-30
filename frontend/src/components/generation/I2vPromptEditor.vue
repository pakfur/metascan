<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import TextEditPopup from '../storyboard/TextEditPopup.vue'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import {
  parseI2vPrompt,
  replaceI2vSection,
  type I2vSectionKey,
} from '../../utils/i2vPromptSections'
import { promptViewState, type I2vPromptView } from '../../utils/i2vPromptView'

// The I2V prompt document as RAW (one box) or STRUCTURED (four panels). The
// document stays ONE string: the toggle is a view switch and never writes
// `modelValue`; each editable panel splices only its own span back in.
const prompt = defineModel<string>({ required: true })

const toast = useToast()

const STORAGE_KEY = 'metascan.i2v.promptView.v1'

function loadPreferred(): I2vPromptView {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'raw' ? 'raw' : 'structured'
  } catch {
    return 'structured'
  }
}

const preferred = ref<I2vPromptView>(loadPreferred())
// Set when the document stops parsing while STRUCTURED is preferred, so
// fixing the labels while typing does not flip the view under the cursor.
const locked = ref(false)

const state = computed(() => promptViewState(prompt.value, preferred.value, locked.value))
const sections = computed(() => parseI2vPrompt(prompt.value))

watch(
  () => prompt.value,
  (text) => {
    if (text && preferred.value === 'structured' && !parseI2vPrompt(text)) locked.value = true
  },
)

function choose(view: I2vPromptView): void {
  if (view === 'structured' && !state.value.structuredEnabled) return
  preferred.value = view
  locked.value = false
  try {
    localStorage.setItem(STORAGE_KEY, view)
  } catch {
    // storage unavailable: the choice just lasts for this session
  }
}

/** A clip load replaced the prompt: its view follows the remembered choice. */
function resetView(): void {
  locked.value = false
}
defineExpose({ resetView })

function sectionModel(key: I2vSectionKey) {
  return computed({
    get: () => sections.value?.[key] ?? '',
    set: (value: string) => {
      prompt.value = replaceI2vSection(prompt.value, key, value)
    },
  })
}

const promptBody = sectionModel('prompt')
const soundscapeBody = sectionModel('soundscape')
const musicBody = sectionModel('music')

async function copyPrompt(): Promise<void> {
  if (!prompt.value) return
  if (await copyToClipboard(prompt.value)) toast.show('Prompt copied')
}
</script>

<template>
  <div class="ipe">
    <div class="ipe-bar">
      <div class="seg" role="radiogroup" aria-label="Prompt view">
        <label class="seg-opt">
          <input
            type="radio"
            name="i2v-prompt-view"
            :checked="state.view === 'raw'"
            @change="choose('raw')"
          />
          <span>RAW</span>
        </label>
        <label class="seg-opt">
          <input
            type="radio"
            name="i2v-prompt-view"
            :checked="state.view === 'structured'"
            :disabled="!state.structuredEnabled"
            @change="choose('structured')"
          />
          <span>STRUCTURED</span>
        </label>
      </div>
    </div>

    <div v-if="state.view === 'structured' && sections" class="i2v-panels">
      <div class="fld i2v-panel">
        <span>Header — fixed by the MiniMax format, not editable</span>
        <textarea :value="sections.header" rows="3" readonly spellcheck="false" />
      </div>
      <div class="fld i2v-panel">
        <span>Prompt (editable — Generate uses this text)</span>
        <TextEditPopup
          title="Prompt"
          width="900px"
          :rows="22"
          multiline
          :value="promptBody"
          @save="promptBody = $event"
        >
          <textarea v-model="promptBody" rows="8" spellcheck="false" />
        </TextEditPopup>
      </div>
      <div class="fld i2v-panel">
        <span>Soundscape</span>
        <TextEditPopup
          title="Soundscape"
          width="700px"
          :rows="8"
          multiline
          :value="soundscapeBody"
          @save="soundscapeBody = $event"
        >
          <textarea v-model="soundscapeBody" rows="2" spellcheck="false" />
        </TextEditPopup>
      </div>
      <div class="fld i2v-panel">
        <span>Non diegetic music</span>
        <TextEditPopup
          title="Non diegetic music"
          width="700px"
          :rows="8"
          multiline
          :value="musicBody"
          @save="musicBody = $event"
        >
          <textarea v-model="musicBody" rows="2" spellcheck="false" />
        </TextEditPopup>
      </div>
    </div>
    <div v-else class="fld i2v-panel i2v-prompt">
      <span>MiniMax I2VA prompt (editable — Generate uses this text)</span>
      <div class="ipe-raw">
        <TextEditPopup
          title="Prompt"
          width="900px"
          :rows="22"
          multiline
          :value="prompt"
          @save="prompt = $event"
        >
          <textarea v-model="prompt" rows="10" spellcheck="false" />
        </TextEditPopup>
        <button
          type="button"
          class="ipe-copy"
          title="Copy the whole prompt"
          :disabled="!prompt"
          @click="copyPrompt"
        >
          <span class="pi pi-copy" />
        </button>
      </div>
      <small v-if="state.note" class="dims-hint">{{ state.note }}</small>
    </div>
  </div>
</template>

<style scoped>
.ipe { margin-top: 14px; }
.ipe-bar { display: flex; justify-content: flex-end; margin-bottom: 6px; }

/* The same segmented control as T2IDialog's Manual | Random Caption. The
   radios stay real and focusable; only their circles are hidden. */
.seg {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--surface-border);
  border-radius: 7px;
  background: var(--surface-ground);
}
.seg-opt { position: relative; display: inline-flex; }
.seg-opt input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}
.seg-opt span {
  padding: 4px 12px;
  border-radius: 5px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-color-secondary);
  white-space: nowrap;
}
.seg-opt:hover input:not(:disabled):not(:checked) + span { color: var(--text-color); }
.seg-opt input:checked + span {
  background: var(--surface-section);
  color: var(--primary-color);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}
.seg-opt input:focus-visible + span {
  outline: 2px solid var(--primary-color);
  outline-offset: 1px;
}
.seg-opt input:disabled { cursor: default; }
.seg-opt input:disabled + span { opacity: 0.5; }

.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}
.fld textarea {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.i2v-panels { display: flex; flex-direction: column; gap: 10px; }
.i2v-panel textarea {
  width: 100%;
  box-sizing: border-box;
  font-family: monospace;
  font-size: 12px;
}
.i2v-panel textarea[readonly] {
  color: var(--text-color-secondary);
  background: var(--surface-ground);
}
/* TextEditPopup centres its button against a single-line input; a textarea
   host wants the button at the top and the box filling the row. */
.i2v-panel :deep(.tep) { align-items: flex-start; width: 100%; }
.dims-hint { color: var(--text-muted, #888); font-size: 11px; margin-top: 2px; }

.ipe-raw { display: flex; align-items: flex-start; gap: 4px; }
.ipe-raw :deep(.tep) { flex: 1; }
.ipe-copy {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.ipe-copy:hover:not(:disabled) { background: var(--surface-hover); color: var(--text-color); }
.ipe-copy:disabled { opacity: 0.5; cursor: default; }
</style>
