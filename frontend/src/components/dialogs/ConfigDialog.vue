<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'

const emit = defineEmits<{
  close: []
}>()

interface DirEntry {
  filepath: string
  search_subfolders: boolean
}

const directories = ref<DirEntry[]>([])
const newPath = ref('')
const loading = ref(true)

onMounted(async () => {
  loading.value = true
  try {
    const config = await fetchConfig()
    directories.value = (config.directories as DirEntry[]) || []
  } finally {
    loading.value = false
  }
})

function addDirectory() {
  const path = newPath.value.trim()
  if (!path) return
  if (directories.value.some((d) => d.filepath === path)) return
  directories.value.push({ filepath: path, search_subfolders: true })
  newPath.value = ''
}

function removeDirectory(idx: number) {
  directories.value.splice(idx, 1)
}

function toggleSubfolders(idx: number) {
  directories.value[idx].search_subfolders = !directories.value[idx].search_subfolders
}

async function save() {
  await updateConfig({ directories: directories.value })
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>Configuration</h3>

      <div v-if="loading" class="muted">Loading...</div>

      <template v-else>
        <h4>Scan Directories</h4>

        <div class="dir-table">
          <div v-for="(dir, idx) in directories" :key="idx" class="dir-row">
            <label class="subfolder-toggle" title="Search subfolders">
              <input
                type="checkbox"
                :checked="dir.search_subfolders"
                @change="toggleSubfolders(idx)"
              />
            </label>
            <span class="dir-path" :title="dir.filepath">{{ dir.filepath }}</span>
            <button class="remove-btn" @click="removeDirectory(idx)" title="Remove">
              &times;
            </button>
          </div>

          <div v-if="directories.length === 0" class="empty-msg">
            No directories configured.
          </div>
        </div>

        <div class="add-row">
          <input
            v-model="newPath"
            type="text"
            placeholder="Enter directory path..."
            class="add-input"
            @keydown.enter="addDirectory"
          />
          <button class="add-btn" @click="addDirectory" :disabled="!newPath.trim()">
            Add
          </button>
        </div>

        <p class="hint">Check the box to include subfolders when scanning.</p>

        <div class="dialog-actions">
          <button class="btn-primary" @click="save">Save</button>
          <button class="btn-secondary" @click="emit('close')">Cancel</button>
        </div>
      </template>
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
  padding: 28px 32px;
  min-width: 460px;
  max-width: 580px;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 { margin: 0 0 20px; font-size: 18px; color: var(--text-color); }
h4 { margin: 0 0 10px; font-size: 14px; color: var(--text-color); }
.muted { color: var(--text-color-secondary); font-size: 14px; }

.dir-table {
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 12px;
}

.dir-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--surface-border);
}

.dir-row:last-child { border-bottom: none; }

.subfolder-toggle {
  flex-shrink: 0;
  cursor: pointer;
}

.subfolder-toggle input { accent-color: var(--primary-color); }

.dir-path {
  flex: 1;
  font-size: 13px;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove-btn {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.remove-btn:hover { color: var(--danger-color); }

.empty-msg {
  padding: 16px;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 13px;
}

.add-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.add-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}

.add-input:focus { outline: none; border-color: var(--primary-color); }

.add-btn {
  padding: 6px 16px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.add-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.hint {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin-bottom: 4px;
}

.dialog-actions { display: flex; gap: 10px; margin-top: 20px; }

.btn-primary {
  padding: 8px 20px; background: var(--primary-color); border: none;
  border-radius: 6px; color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
}
.btn-primary:hover { opacity: 0.9; }

.btn-secondary {
  padding: 8px 20px; background: var(--surface-ground);
  border: 1px solid var(--surface-border); border-radius: 6px;
  color: var(--text-color); font-size: 14px; cursor: pointer;
}
.btn-secondary:hover { background: var(--surface-hover); }
</style>
