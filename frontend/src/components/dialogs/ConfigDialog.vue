<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { fetchUpscaleConfig, setOriginalFolder } from '../../api/upscale'
import ConfigModelsTab from './ConfigModelsTab.vue'
import ConfigSimilarityTab from './ConfigSimilarityTab.vue'
import ConfigTrainingTab from './ConfigTrainingTab.vue'
import ConfigI2VTab from './ConfigI2VTab.vue'
import ConfigT2ITab from './ConfigT2ITab.vue'
import ConfigUpscaleTab from './ConfigUpscaleTab.vue'
import DirectoryPicker from './DirectoryPicker.vue'
import ConfigTransferTab from './ConfigTransferTab.vue'
import { useScanStore } from '../../stores/scan'
import { useToast } from '../../composables/useToast'
import { useViewport } from '../../composables/useViewport'

const emit = defineEmits<{
  close: []
}>()

interface DirEntry {
  filepath: string
  search_subfolders: boolean
}

type TabKey =
  | 'directories'
  | 'models'
  | 'similarity'
  | 'training'
  | 'i2v'
  | 't2i'
  | 'upscale'
  | 'transfer'
  | 'maintenance'

const activeTab = ref<TabKey>('directories')

const scanStore = useScanStore()
const { isMobile } = useViewport()

// The scan dialog is rendered by LibraryView while the scan store is busy,
// so hand over to it and close this one.
function openFullClean() {
  emit('close')
  void scanStore.prepare()
}

const directories = ref<DirEntry[]>([])
const newPath = ref('')
const loading = ref(true)

// The comfy section carries more keys than the one we expose (in_flight,
// output_root, ...); keep the raw object so saving merges base_url into it
// instead of clobbering the rest (PUT /api/config is a shallow update).
const comfyRaw = ref<Record<string, unknown>>({})
const comfyUrl = ref('')

// Empty means the default under the data folder, shown as the placeholder.
const originalRoot = ref('')
const savedOriginalRoot = ref('')
const defaultOriginalRoot = ref('')
const showOriginalPicker = ref(false)
const saveError = ref<string | null>(null)
const toast = useToast()

onMounted(async () => {
  loading.value = true
  try {
    const config = await fetchConfig()
    directories.value = (config.directories as DirEntry[]) || []
    comfyRaw.value = (config.comfy as Record<string, unknown>) || {}
    comfyUrl.value = String(comfyRaw.value.base_url ?? 'http://127.0.0.1:8188')
    const upscale = await fetchUpscaleConfig()
    originalRoot.value = upscale.original_root
    savedOriginalRoot.value = upscale.original_root
    defaultOriginalRoot.value = upscale.resolved_original_root
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

async function saveDirectories() {
  const comfy = { ...comfyRaw.value, base_url: comfyUrl.value.trim().replace(/\/+$/, '') }
  saveError.value = null
  try {
    await updateConfig({ directories: directories.value, comfy })
    const root = originalRoot.value.trim()
    if (root !== savedOriginalRoot.value) {
      const { status } = await setOriginalFolder(root)
      if (status === 'moving') {
        toast.show('Moving the originals: follow it in the background panel.', 'info', 5000)
      }
    }
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
    return
  }
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>Configuration</h3>

      <nav class="tabs">
        <button
          class="tab"
          :class="{ active: activeTab === 'directories' }"
          @click="activeTab = 'directories'"
        >
          Directories
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'models' }"
          @click="activeTab = 'models'"
        >
          Models
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'similarity' }"
          @click="activeTab = 'similarity'"
        >
          Similarity
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'training' }"
          @click="activeTab = 'training'"
        >
          Training
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'i2v' }"
          @click="activeTab = 'i2v'"
        >
          Video
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 't2i' }"
          @click="activeTab = 't2i'"
        >
          Text to Image
        </button>
        <button
          class="tab"
          :class="{ active: activeTab === 'upscale' }"
          @click="activeTab = 'upscale'"
        >
          Upscale
        </button>
        <button
          v-if="!isMobile"
          class="tab"
          :class="{ active: activeTab === 'transfer' }"
          @click="activeTab = 'transfer'"
        >
          Import / Export
        </button>
        <button
          v-if="!isMobile"
          class="tab"
          :class="{ active: activeTab === 'maintenance' }"
          @click="activeTab = 'maintenance'"
        >
          Maintenance
        </button>
      </nav>

      <div v-if="activeTab === 'directories'" class="tab-panel">
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

          <h4>Original Folder</h4>
          <div class="add-row">
            <input
              v-model="originalRoot"
              type="text"
              :placeholder="defaultOriginalRoot"
              class="add-input"
              spellcheck="false"
            />
            <button class="add-btn" @click="showOriginalPicker = true">Browse…</button>
          </div>
          <p class="hint">
            Where the originals of upscaled files are kept, for compare and revert
            only. It must not be inside a library folder. Leave it empty for the
            default. Changing it moves the files already there.
          </p>

          <h4>ComfyUI Server</h4>
          <div class="add-row">
            <input
              v-model="comfyUrl"
              type="text"
              placeholder="http://127.0.0.1:8188"
              class="add-input"
            />
          </div>
          <p class="hint">
            URL of the ComfyUI server used for storyboard generation. Takes effect
            after a backend restart.
          </p>

          <p v-if="saveError" class="save-error">{{ saveError }}</p>
          <div class="dialog-actions">
            <button class="btn-primary" @click="saveDirectories">Save</button>
            <button class="btn-secondary" @click="emit('close')">Cancel</button>
          </div>
        </template>
      </div>

      <div v-else-if="activeTab === 'models'" class="tab-panel">
        <ConfigModelsTab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 'similarity'" class="tab-panel">
        <ConfigSimilarityTab @close="emit('close')" />
      </div>

      <div v-else-if="activeTab === 'training'" class="tab-panel">
        <ConfigTrainingTab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 'i2v'" class="tab-panel">
        <ConfigI2VTab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 't2i'" class="tab-panel">
        <ConfigT2ITab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 'upscale'" class="tab-panel">
        <ConfigUpscaleTab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 'transfer'" class="tab-panel">
        <ConfigTransferTab />
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>

      <div v-else-if="activeTab === 'maintenance'" class="tab-panel">
        <h4>Full clean &amp; rescan</h4>
        <p class="hint">
          New files in your library folders are imported on their own. A full
          clean wipes the database and reimports every file; your ratings,
          reasons and categories are kept.
        </p>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="openFullClean">Full clean &amp; rescan…</button>
        </div>
        <div class="dialog-actions">
          <button class="btn-secondary" @click="emit('close')">Close</button>
        </div>
      </div>
    </div>

    <DirectoryPicker
      v-if="showOriginalPicker"
      title="Original folder"
      :initial-path="originalRoot || defaultOriginalRoot"
      @select="(path: string) => { originalRoot = path; showOriginalPicker = false }"
      @close="showOriginalPicker = false"
    />
  </div>
</template>

<style scoped>
.save-error { margin: 0 0 10px; font-size: 13px; color: #c33; }
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
  padding: 22px 28px 24px;
  width: 720px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 { margin: 0 0 14px; font-size: 18px; color: var(--text-color); }
h4 { margin: 0 0 10px; font-size: 14px; color: var(--text-color); }
.muted { color: var(--text-color-secondary); font-size: 14px; }

.tabs {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--surface-border);
  margin-bottom: 16px;
}
.tab {
  background: none;
  border: none;
  padding: 8px 14px;
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab:hover { color: var(--text-color); }
.tab.active {
  color: var(--text-color);
  border-bottom-color: var(--primary-color);
}

.tab-panel { min-height: 240px; }

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
