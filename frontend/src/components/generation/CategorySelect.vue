<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useEventListener } from '@vueuse/core'
import { useCategoriesStore } from '../../stores/categories'
import { useToast } from '../../composables/useToast'

// Compact multi-select over the user's categories, with an inline "new
// category" entry. Fully controlled (v-model). The list opens IN FLOW below
// the button rather than as a floating panel, so a scrolling dialog form can
// never clip it.
const props = withDefaults(
  defineProps<{
    modelValue: number[]
    disabled?: boolean
    label?: string
  }>(),
  { disabled: false, label: 'Categories' },
)

const emit = defineEmits<{ 'update:modelValue': [value: number[]] }>()

const store = useCategoriesStore()
const toast = useToast()

const root = ref<HTMLElement | null>(null)
const newInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const newName = ref('')
const creating = ref(false)

const selectedSet = computed(() => new Set(props.modelValue))
const summary = computed(() => {
  const names = store.categories.filter((c) => selectedSet.value.has(c.id)).map((c) => c.name)
  return names.length ? names.join(', ') : 'None'
})

function toggle(id: number) {
  emit(
    'update:modelValue',
    selectedSet.value.has(id) ? props.modelValue.filter((v) => v !== id) : [...props.modelValue, id],
  )
}

function clear() {
  if (props.modelValue.length) emit('update:modelValue', [])
}

async function onCreate() {
  const name = newName.value.trim()
  if (!name || creating.value) return
  // A name that already exists (case-insensitively) is just selected.
  const existing = store.categories.find((c) => c.name.toLowerCase() === name.toLowerCase())
  if (existing) {
    if (!selectedSet.value.has(existing.id)) toggle(existing.id)
    newName.value = ''
    return
  }
  creating.value = true
  try {
    const created = await store.create(name)
    if (!props.modelValue.includes(created.id)) {
      emit('update:modelValue', [...props.modelValue, created.id])
    }
    newName.value = ''
  } catch (e) {
    toast.show(`Could not create category: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  } finally {
    creating.value = false
    await nextTick()
    newInput.value?.focus()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && open.value) {
    // Close the list, not the dialog around it.
    e.stopPropagation()
    open.value = false
  }
}

useEventListener(document, 'pointerdown', (e: PointerEvent) => {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
})
</script>

<template>
  <div ref="root" class="cat-select" @keydown="onKeydown">
    <span v-if="label" class="cs-label">{{ label }}</span>
    <div class="cs-row">
      <button
        type="button"
        class="cs-toggle"
        :disabled="disabled"
        :aria-expanded="open"
        :title="`Everything this dialog generates is tagged with: ${summary}`"
        @click="open = !open"
      >
        <span class="cs-summary" :class="{ none: !modelValue.length }">{{ summary }}</span>
        <span class="cs-caret">{{ open ? '▴' : '▾' }}</span>
      </button>
      <button
        v-if="modelValue.length"
        type="button"
        class="cs-clear"
        title="Tag nothing"
        :disabled="disabled"
        @click="clear"
      >✕</button>
    </div>
    <div v-if="open && !disabled" class="cs-panel">
      <ul class="cs-list">
        <li v-for="c in store.categories" :key="c.id">
          <label class="cs-option">
            <input type="checkbox" :checked="selectedSet.has(c.id)" @change="toggle(c.id)" />
            <span class="cs-name" :title="c.name">{{ c.name }}</span>
          </label>
        </li>
        <li v-if="!store.categories.length" class="cs-empty">No categories yet</li>
      </ul>
      <div class="cs-new">
        <input
          ref="newInput"
          v-model="newName"
          type="text"
          maxlength="40"
          spellcheck="false"
          autocomplete="off"
          placeholder="New category…"
          aria-label="New category name"
          @keydown.enter.prevent="onCreate"
        />
        <button type="button" class="cs-add" :disabled="!newName.trim() || creating" @click="onCreate">Add</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cat-select {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.cs-row {
  display: flex;
  gap: 6px;
  min-width: 0;
}

.cs-toggle,
.cs-clear,
.cs-add,
.cs-new input {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.cs-toggle {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  text-align: left;
  cursor: pointer;
}

.cs-summary {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cs-summary.none {
  color: var(--text-color-secondary);
}

.cs-caret {
  flex: none;
  color: var(--text-color-secondary);
}

.cs-clear,
.cs-add {
  flex: none;
  cursor: pointer;
}

.cs-toggle:focus,
.cs-clear:focus,
.cs-add:focus,
.cs-new input:focus {
  outline: none;
  border-color: var(--primary-color);
}

.cs-toggle:disabled,
.cs-clear:disabled,
.cs-add:disabled {
  opacity: 0.6;
  cursor: default;
}

.cs-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
}

.cs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 160px;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.cs-option {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
  color: var(--text-color);
}

.cs-option:hover {
  background: var(--surface-hover, rgba(127, 127, 127, 0.12));
}

.cs-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cs-empty {
  padding: 2px 4px;
  font-style: italic;
}

.cs-new {
  display: flex;
  gap: 6px;
}

.cs-new input {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--surface-card, var(--surface-ground));
}
</style>
