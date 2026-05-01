<script setup lang="ts">
import { ref, computed } from 'vue'
import type { FilterItem } from '../../types/filters'

const props = defineProps<{
  label: string
  type: string
  items: FilterItem[]
  selected: string[]
}>()

const emit = defineEmits<{
  'update:selected': [keys: string[]]
  clear: []
}>()

const collapsed = ref(true)
const maxVisible = 20

const visibleItems = computed(() =>
  collapsed.value ? props.items.slice(0, maxVisible) : props.items
)

const hasMore = computed(() => props.items.length > maxVisible && collapsed.value)

function toggle(key: string) {
  const current = new Set(props.selected)
  if (current.has(key)) {
    current.delete(key)
  } else {
    current.add(key)
  }
  emit('update:selected', Array.from(current))
}

const expanded = ref(props.items.length > 0)

function toggleExpand() {
  expanded.value = !expanded.value
}
</script>

<template>
  <div class="filter-section">
    <button class="section-header" @click="toggleExpand">
      <span class="expand-icon">{{ expanded ? '▼' : '▶' }}</span>
      <span class="section-label">{{ label }}</span>
      <span class="section-count">({{ items.length }})</span>
      <button
        v-if="selected.length > 0"
        class="section-clear"
        @click.stop="emit('clear')"
        title="Clear"
      >
        &times;
      </button>
    </button>

    <div v-if="expanded" class="section-items">
      <label
        v-for="item in visibleItems"
        :key="item.key"
        class="filter-item"
        :class="{ checked: selected.includes(item.key) }"
      >
        <input
          type="checkbox"
          :checked="selected.includes(item.key)"
          @change="toggle(item.key)"
        />
        <span class="item-key" :title="item.key">{{ item.key }}</span>
        <span class="item-count">{{ item.count }}</span>
      </label>

      <button v-if="hasMore" class="show-more" @click="collapsed = false">
        Show {{ items.length - maxVisible }} more...
      </button>
      <button v-if="!collapsed && items.length > maxVisible" class="show-more" @click="collapsed = true">
        Show less
      </button>
    </div>
  </div>
</template>

<style scoped>
.filter-section {
  border-bottom: 1px solid var(--surface-border);
  padding-bottom: 4px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 4px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color);
  font-size: 13px;
  font-weight: 600;
  text-align: left;
}

.section-header:hover {
  background: var(--surface-hover);
  border-radius: 4px;
}

.expand-icon {
  font-size: 10px;
  width: 12px;
}

.section-count {
  color: var(--text-color-secondary);
  font-weight: 400;
}

.section-clear {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 0 4px;
}

.section-clear:hover {
  color: var(--danger-color, #ef4444);
}

.section-items {
  display: flex;
  flex-direction: column;
  gap: 1px;
  max-height: 300px;
  overflow-y: auto;
  padding-left: 4px;
}

.filter-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-color);
}

.filter-item:hover {
  background: var(--surface-hover);
}

.filter-item.checked {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.filter-item input[type='checkbox'] {
  margin: 0;
  accent-color: var(--primary-color);
}

.item-key {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-count {
  color: var(--text-color-secondary);
  font-size: 11px;
  flex-shrink: 0;
}

.show-more {
  padding: 4px 8px;
  font-size: 12px;
  color: var(--primary-color);
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
}

.show-more:hover {
  text-decoration: underline;
}
</style>
