<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchAllMedia } from '../../api/media'
import { thumbnailUrl } from '../../api/client'
import type { Media } from '../../types/media'

const emit = defineEmits<{ select: [path: string]; close: [] }>()

const loading = ref(true)
const error = ref<string | null>(null)
const images = ref<Media[]>([])
const query = ref('')

onMounted(async () => {
  try {
    images.value = (await fetchAllMedia()).filter((m) => !m.is_video)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})

const MAX_SHOWN = 200

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return images.value
  return images.value.filter((m) => m.file_path.toLowerCase().includes(q))
})

const shown = computed(() => filtered.value.slice(0, MAX_SHOWN))

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}
</script>

<template>
  <div class="picker-overlay" @click.self="emit('close')">
    <div class="picker-card">
      <div class="picker-header">
        <h4>Choose a reference image</h4>
        <input
          v-model="query"
          type="text"
          class="picker-search"
          placeholder="Filter by file name…"
        />
        <button type="button" class="close-btn" title="Close" @click="emit('close')">
          &times;
        </button>
      </div>

      <p v-if="error" class="picker-msg error">{{ error }}</p>
      <p v-else-if="loading" class="picker-msg">Loading library…</p>
      <p v-else-if="shown.length === 0" class="picker-msg">No matching images.</p>

      <div v-else class="picker-grid">
        <button
          v-for="m in shown"
          :key="m.file_path"
          type="button"
          class="picker-tile"
          :title="m.file_path"
          @click="emit('select', m.file_path)"
        >
          <img :src="thumbnailUrl(m.file_path)" alt="" loading="lazy" />
          <span class="tile-name">{{ basename(m.file_path) }}</span>
        </button>
      </div>

      <p v-if="!loading && filtered.length > MAX_SHOWN" class="picker-msg hint">
        Showing first {{ MAX_SHOWN }} of {{ filtered.length }} — refine the filter to narrow
        down.
      </p>
    </div>
  </div>
</template>

<style scoped>
.picker-overlay {
  position: fixed;
  inset: 0;
  z-index: 950;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.picker-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 16px 20px 18px;
  width: 720px;
  max-width: 94vw;
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.picker-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

h4 {
  margin: 0;
  font-size: 14px;
  color: var(--text-color);
  flex-shrink: 0;
}

.picker-search {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
}

.picker-search:focus {
  outline: none;
  border-color: var(--primary-color);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 20px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.close-btn:hover {
  color: var(--text-color);
}

.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 10px;
  overflow-y: auto;
  min-height: 120px;
}

.picker-tile {
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}

.picker-tile:hover {
  border-color: var(--primary-color);
}

.picker-tile img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}

.tile-name {
  font-size: 11px;
  color: var(--text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-msg {
  padding: 16px 0;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 13px;
  margin: 0;
}

.picker-msg.hint {
  padding: 8px 0 0;
  font-size: 12px;
}

.picker-msg.error {
  color: var(--danger-color, #e53e3e);
}
</style>
