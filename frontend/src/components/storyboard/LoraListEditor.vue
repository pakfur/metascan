<template>
  <div class="lle-root">
    <label class="lle-label">{{ label }}</label>
    <div v-for="(entry, i) in entries" :key="`${i}-${entry.name}`" class="lle-row">
      <input
        class="lle-name"
        type="text"
        :list="listId"
        :value="entry.name"
        @change="commitName(i, $event)"
      />
      <input
        class="lle-strength"
        type="number"
        step="0.05"
        title="Strength"
        :value="entry.strength"
        @change="commitStrength(i, $event)"
      />
      <button class="lle-remove" type="button" title="Remove" @click="removeEntry(i)">✕</button>
    </div>
    <div v-if="drafting" class="lle-row">
      <input
        ref="draftInput"
        class="lle-name"
        type="text"
        :list="listId"
        placeholder="lora file…"
        @change="commitDraft"
        @keydown.esc="drafting = false"
      />
      <button class="lle-remove" type="button" title="Cancel" @click="drafting = false">✕</button>
    </div>
    <button v-else class="lle-add" type="button" @click="startDraft">+ Add LoRA</button>
    <datalist :id="listId">
      <option v-for="o in options" :key="o" :value="o" />
    </datalist>
  </div>
</template>

<script lang="ts">
// Shared, once-per-page-load fetch of the ComfyUI lora list -- both the
// image and video editors (and every shot switch) reuse the same promise.
// A failed fetch resolves to [] (the picker degrades to free text).
import { listLoras } from '../../api/comfy'

let loraOptionsPromise: Promise<string[]> | null = null
function fetchLoraOptions(): Promise<string[]> {
  if (!loraOptionsPromise) loraOptionsPromise = listLoras().catch(() => [])
  return loraOptionsPromise
}

let uid = 0
</script>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import type { LoraEntry } from '../../types/storyboard'

const props = defineProps<{
  label: string
  entries: LoraEntry[]
}>()

const emit = defineEmits<{
  (e: 'change', entries: LoraEntry[]): void
}>()

const listId = `lle-options-${++uid}`
const options = ref<string[]>([])
onMounted(async () => {
  options.value = await fetchLoraOptions()
})

// Rows are fully controlled from `entries` (`:value` + `@change`, no
// v-model) so a server-driven refresh never clobbers text mid-edit. A new
// entry starts as a local draft row and only enters the committed list --
// and hits the server -- once it has a name.
const drafting = ref(false)
const draftInput = ref<HTMLInputElement | null>(null)

function startDraft(): void {
  drafting.value = true
  void nextTick(() => draftInput.value?.focus())
}

function commitDraft(e: Event): void {
  const name = (e.target as HTMLInputElement).value.trim()
  if (!name) return
  drafting.value = false
  emit('change', [...props.entries, { name, strength: 1.0 }])
}

function commitName(i: number, e: Event): void {
  const input = e.target as HTMLInputElement
  const name = input.value.trim()
  if (!name) {
    input.value = props.entries[i]?.name ?? ''
    return
  }
  if (name === props.entries[i]?.name) return
  emit(
    'change',
    props.entries.map((en, j) => (j === i ? { ...en, name } : en)),
  )
}

function commitStrength(i: number, e: Event): void {
  const input = e.target as HTMLInputElement
  const strength = Number(input.value)
  if (!Number.isFinite(strength)) {
    input.value = String(props.entries[i]?.strength ?? 1)
    return
  }
  if (strength === props.entries[i]?.strength) return
  emit(
    'change',
    props.entries.map((en, j) => (j === i ? { ...en, strength } : en)),
  )
}

function removeEntry(i: number): void {
  emit(
    'change',
    props.entries.filter((_, j) => j !== i),
  )
}
</script>

<style scoped>
.lle-root {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lle-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.lle-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.lle-name,
.lle-strength {
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  box-sizing: border-box;
}

.lle-name {
  flex: 1;
  min-width: 0;
}

.lle-strength {
  width: 72px;
  flex-shrink: 0;
}

.lle-name:focus,
.lle-strength:focus {
  outline: none;
  border-color: var(--primary-color);
}

.lle-remove {
  border: none;
  background: none;
  color: var(--text-color-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 4px;
  flex-shrink: 0;
}

.lle-remove:hover {
  color: var(--text-color);
}

.lle-add {
  align-self: flex-start;
  padding: 3px 8px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 12px;
  cursor: pointer;
}

.lle-add:hover {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}
</style>
