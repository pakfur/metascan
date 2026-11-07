<script setup lang="ts">
// A plain textarea (TextEditPopup collapses newlines) that offers the
// caption tokens after `__`: type `__` and a list of the matching tokens
// opens under the box; Up / Down choose, Enter or Tab inserts, Escape
// closes, and a click inserts. The text is the parent's (`value` in,
// `input` out), as with the bare textarea it replaces.
import { computed, nextTick, ref } from 'vue'
import { tokenCompletion, type CaptionToken } from '../../types/t2i'

const props = withDefaults(
  defineProps<{
    value: string
    tokens: readonly CaptionToken[]
    readonly?: boolean
    placeholder?: string
    rows?: number
    id?: string
  }>(),
  { readonly: false, placeholder: '', rows: 3, id: undefined },
)
const emit = defineEmits<{ (e: 'input', text: string): void }>()

const box = ref<HTMLTextAreaElement | null>(null)
const open = ref<{ start: number; matches: CaptionToken[] } | null>(null)
const active = ref(0)
const listId = computed(() => (props.id ? `${props.id}-tokens` : undefined))

function refresh() {
  const el = box.value
  if (!el || props.readonly || el.selectionStart !== el.selectionEnd) {
    open.value = null
    return
  }
  const found = tokenCompletion(el.value, el.selectionStart, props.tokens)
  if (!found) {
    open.value = null
    return
  }
  // Keep the highlighted token when it still matches.
  const keep = open.value?.matches[active.value]?.token
  open.value = found
  const at = keep ? found.matches.findIndex((t) => t.token === keep) : -1
  active.value = at >= 0 ? at : 0
}

function onInput(event: Event) {
  emit('input', (event.target as HTMLTextAreaElement).value)
  refresh()
}

async function insert(token: CaptionToken) {
  const el = box.value
  const found = open.value
  if (!el || !found) return
  const caret = el.selectionStart
  const text = el.value
  const next = text.slice(0, found.start) + token.token + text.slice(caret)
  open.value = null
  emit('input', next)
  await nextTick()
  const at = found.start + token.token.length
  el.focus()
  el.setSelectionRange(at, at)
}

function onKeydown(event: KeyboardEvent) {
  const found = open.value
  if (!found) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const n = found.matches.length
    active.value = (active.value + (event.key === 'ArrowDown' ? 1 : n - 1)) % n
  } else if (event.key === 'Enter' || event.key === 'Tab') {
    event.preventDefault()
    void insert(found.matches[active.value])
  } else if (event.key === 'Escape') {
    // Close the list only; the dialog's own Escape handling stays untouched
    // when no list is open.
    event.preventDefault()
    event.stopPropagation()
    open.value = null
  }
}
</script>

<template>
  <div class="token-area">
    <textarea
      :id="id"
      ref="box"
      class="area"
      :rows="rows"
      spellcheck="false"
      :value="value"
      :readonly="readonly"
      :placeholder="placeholder"
      role="combobox"
      aria-autocomplete="list"
      :aria-expanded="open !== null"
      :aria-controls="listId"
      @input="onInput"
      @keydown="onKeydown"
      @click="refresh"
      @keyup.left="refresh"
      @keyup.right="refresh"
      @blur="open = null"
    />
    <ul v-if="open" :id="listId" class="token-list" role="listbox">
      <li
        v-for="(t, i) in open.matches"
        :key="t.token"
        role="option"
        :aria-selected="i === active"
        :class="{ active: i === active }"
        @mousedown.prevent="insert(t)"
        @mouseenter="active = i"
      >
        <span class="tok">{{ t.token }}</span>
        <span class="note">{{ t.note }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.token-area {
  position: relative;
}
/* The T2I dialog's .area look (its scoped style cannot reach in here). */
.token-area .area {
  width: 100%;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
  resize: vertical;
}
.token-area .area:read-only {
  color: var(--text-color-secondary);
}
.token-area .area:focus {
  outline: none;
  border-color: var(--primary-color);
}
.token-list {
  position: absolute;
  left: 0;
  top: 100%;
  z-index: 20;
  margin: 2px 0 0;
  padding: 4px 0;
  list-style: none;
  min-width: 220px;
  max-height: 220px;
  overflow-y: auto;
  background: var(--surface-overlay, var(--surface-card));
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
}
.token-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 3px 10px;
  cursor: pointer;
  font-size: 0.85rem;
}
.token-list li.active {
  background: var(--surface-hover);
}
.tok {
  font-family: monospace;
}
.note {
  opacity: 0.65;
}
</style>
