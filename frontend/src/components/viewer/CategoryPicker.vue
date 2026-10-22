<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { useCategoriesStore } from '../../stores/categories'

// Keyboard-first category picker shown inside the rating rail. The input
// holds focus, so the viewer's own key handler (which ignores INPUT
// targets) stays out of the way; every key handled here is also stopped so
// it never reaches a window listener.
const props = defineProps<{
  assigned: number[]
}>()

const emit = defineEmits<{
  toggle: [id: number]
  create: [name: string]
  sticky: [id: number]
  rename: [id: number, name: string]
  delete: [id: number]
  close: []
}>()

const store = useCategoriesStore()
const query = ref('')
const highlight = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return store.categories
  return store.categories.filter((c) => c.name.toLowerCase().includes(q))
})

const assignedSet = computed(() => new Set(props.assigned))

watch(filtered, (list) => {
  if (highlight.value >= list.length) highlight.value = Math.max(0, list.length - 1)
})
watch(query, () => {
  highlight.value = 0
})

onMounted(() => {
  inputEl.value?.focus()
})

function scrollHighlightIntoView() {
  const row = listEl.value?.children[highlight.value] as HTMLElement | undefined
  row?.scrollIntoView({ block: 'nearest' })
}

function onKeyDown(e: KeyboardEvent) {
  switch (e.key) {
    case 'Escape':
      e.preventDefault()
      e.stopPropagation()
      emit('close')
      return
    case 'ArrowDown':
      e.preventDefault()
      e.stopPropagation()
      if (filtered.value.length) {
        highlight.value = (highlight.value + 1) % filtered.value.length
        scrollHighlightIntoView()
      }
      return
    case 'ArrowUp':
      e.preventDefault()
      e.stopPropagation()
      if (filtered.value.length) {
        highlight.value =
          (highlight.value - 1 + filtered.value.length) % filtered.value.length
        scrollHighlightIntoView()
      }
      return
    case 'Enter': {
      e.preventDefault()
      e.stopPropagation()
      const hit = filtered.value[highlight.value]
      if (e.shiftKey) {
        if (hit) emit('sticky', hit.id)
        return
      }
      if (hit) {
        emit('toggle', hit.id)
        return
      }
      const name = query.value.trim()
      if (name) emit('create', name)
      return
    }
    default:
      // Typing stays in the input; keep it from reaching window listeners.
      e.stopPropagation()
  }
}

function onRename(id: number, current: string) {
  const name = window.prompt('Rename category', current)
  inputEl.value?.focus()
  if (name === null) return
  const trimmed = name.trim()
  if (trimmed && trimmed !== current) emit('rename', id, trimmed)
}

function onDelete(id: number, name: string, count: number) {
  const items = count === 1 ? '1 item' : `${count} items`
  const ok = window.confirm(
    `Delete category "${name}"? It is on ${items}; the items themselves are kept.`,
  )
  inputEl.value?.focus()
  if (ok) emit('delete', id)
}
</script>

<template>
  <div class="category-picker" @click.stop>
    <input
      ref="inputEl"
      v-model="query"
      class="picker-input"
      type="text"
      placeholder="Category…"
      maxlength="40"
      spellcheck="false"
      autocomplete="off"
      @keydown="onKeyDown"
    />
    <ul ref="listEl" class="picker-list">
      <li
        v-for="(c, i) in filtered"
        :key="c.id"
        class="picker-row"
        :class="{ highlighted: i === highlight, assigned: assignedSet.has(c.id) }"
        @mouseenter="highlight = i"
        @click="emit('toggle', c.id); inputEl?.focus()"
      >
        <span class="mark">{{ assignedSet.has(c.id) ? '✓' : '' }}</span>
        <span class="name" :title="c.name">
          <span v-if="store.stickyId === c.id">📌 </span>{{ c.name }}
        </span>
        <button class="row-btn" title="Rename" @click.stop="onRename(c.id, c.name)">✎</button>
        <button
          class="row-btn"
          title="Delete category"
          @click.stop="onDelete(c.id, c.name, c.item_count)"
        >
          🗑
        </button>
      </li>
      <li v-if="!filtered.length" class="picker-empty">
        {{ query.trim() ? `Enter: create "${query.trim()}"` : 'No categories yet — type a name' }}
      </li>
    </ul>
    <div class="picker-help">
      ↑↓ move · Enter toggle · Shift+Enter sticky · Esc close
    </div>
  </div>
</template>

<style scoped>
.category-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
  border-top: 1px solid #333;
  padding-top: 6px;
  min-height: 0;
}

.picker-input {
  width: 100%;
  box-sizing: border-box;
  background: #1b1b1b;
  border: 1px solid #555;
  border-radius: 4px;
  color: #eee;
  font-size: 12px;
  padding: 4px 6px;
  outline: none;
}

.picker-input:focus {
  border-color: #60a5fa;
}

.picker-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
  color: #ccc;
}

.picker-row.highlighted {
  background: rgba(96, 165, 250, 0.25);
  color: #fff;
}

.picker-row.assigned .name {
  color: #9fd8b8;
}

.mark {
  width: 12px;
  flex-shrink: 0;
  color: #3c9;
}

.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-btn {
  background: none;
  border: none;
  color: #888;
  cursor: pointer;
  font-size: 11px;
  padding: 0 2px;
  visibility: hidden;
}

.picker-row:hover .row-btn {
  visibility: visible;
}

.row-btn:hover {
  color: #fff;
}

.picker-empty {
  color: #888;
  padding: 2px 4px;
  font-style: italic;
}

.picker-help {
  color: #777;
  font-size: 10px;
  line-height: 1.3;
}
</style>
