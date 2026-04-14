<script setup lang="ts">
import { ref } from 'vue'
import { useSimilarityStore } from '../../stores/similarity'

const simStore = useSimilarityStore()
const query = ref('')

function onSubmit() {
  const q = query.value.trim()
  if (q) {
    simStore.searchByText(q)
  }
}

function clear() {
  query.value = ''
  if (simStore.active && simStore.isContentSearch) {
    simStore.exit()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    onSubmit()
  }
}
</script>

<template>
  <div class="content-search">
    <div class="search-input-wrapper">
      <input
        v-model="query"
        type="text"
        placeholder="Search by content (CLIP)..."
        class="search-input"
        @keydown="onKeydown"
      />
      <button v-if="query" class="clear-btn" @click="clear">&times;</button>
    </div>
  </div>
</template>

<style scoped>
.content-search {
  padding-bottom: 4px;
}

.search-input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.search-input {
  width: 100%;
  padding: 6px 28px 6px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  font-size: 13px;
  background: var(--surface-card);
  color: var(--text-color);
}

.search-input:focus {
  outline: none;
  border-color: var(--primary-color);
}

.clear-btn {
  position: absolute;
  right: 4px;
  background: none;
  border: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 4px;
}
</style>
