<script setup lang="ts">
import { ref } from 'vue'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

// Popup editor for single-line text inputs. The host input rides in the
// slot untouched (keeping its own commit-on-change wiring); the icon
// button to its right opens a large editor whose Save emits the new text
// back through the host's `@save` handler. Newlines collapse to spaces on
// save -- the target control is single-line. Multi-line textareas don't
// use this component.
const props = defineProps<{ value: string | null | undefined; title?: string }>()
const emit = defineEmits<{ (e: 'save', value: string): void }>()

const open = ref(false)
const draft = ref('')
const editor = ref<HTMLTextAreaElement | null>(null)
const copied = ref(false)
const pasteBlocked = ref(false)

function openDialog(): void {
  draft.value = props.value ?? ''
  copied.value = false
  pasteBlocked.value = false
  open.value = true
}

async function copy(): Promise<void> {
  if (!draft.value) return
  await copyToClipboard(draft.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

// `navigator.clipboard.readText` needs a secure context AND a permission
// grant -- unlike copy there is no execCommand fallback for reading, so a
// refusal just surfaces the Ctrl+V hint instead of failing silently.
async function paste(): Promise<void> {
  let text: string
  try {
    text = await navigator.clipboard.readText()
  } catch {
    pasteBlocked.value = true
    return
  }
  const el = editor.value
  if (el) {
    el.setRangeText(text, el.selectionStart, el.selectionEnd, 'end')
    draft.value = el.value
    el.focus()
  } else {
    draft.value += text
  }
}

function save(): void {
  emit('save', draft.value.replace(/\s*\n+\s*/g, ' ').trim())
  open.value = false
}
</script>

<template>
  <span class="tep">
    <slot />
    <button
      type="button"
      class="tep-btn"
      :title="`Edit ${title ?? 'text'} in a popup`"
      @click.stop.prevent="openDialog"
    >
      <span class="pi pi-window-maximize" />
    </button>

    <ModalShell v-if="open" width="560px" @close="open = false">
      <h3 class="tep-title">{{ title ?? 'Edit text' }}</h3>
      <textarea ref="editor" v-model="draft" rows="8" class="tep-body" />
      <p v-if="pasteBlocked" class="tep-hint">
        The browser blocked clipboard read — press Ctrl+V in the editor instead.
      </p>
      <template #actions>
        <button type="button" class="msh-btn msh-btn--primary" @click="save">Save</button>
        <button type="button" class="msh-btn" :disabled="!draft" @click="copy">
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
        <button type="button" class="msh-btn" @click="paste">Paste</button>
        <button type="button" class="msh-btn" @click="open = false">Cancel</button>
      </template>
    </ModalShell>
  </span>
</template>

<style scoped>
.tep {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.tep :slotted(input) {
  flex: 1;
  min-width: 0;
}

.tep-btn {
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
  transition:
    background 0.15s,
    color 0.15s;
}

.tep-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.tep-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-color);
}

.tep-body {
  width: 100%;
  box-sizing: border-box;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.6;
  padding: 12px;
  resize: vertical;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
}

.tep-body:focus {
  outline: none;
  border-color: var(--primary-color);
}

.tep-hint {
  font-size: 11px;
  color: var(--warn, #ffb300);
  margin: 6px 0 0;
}
</style>
