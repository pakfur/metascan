<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  applyImport,
  discardImport,
  exportLibraryData,
  fetchImportStatus,
  previewImport,
  uploadBundle,
  type ImportPreview,
  type ImportReport,
  type ImportStatus,
  type RootMapping,
} from '../../api/libraryTransfer'

const POLL_MS = 1000

const KIND_LABELS: Record<string, string> = {
  favorites: 'Favorites',
  playback_speeds: 'Playback speeds',
  judgments: 'Grades and views',
  categories: 'Categories',
  media_categories: 'Category links',
  hindsight_briefs: 'Hindsight briefs',
  judgment_events: 'Rating history',
  saved_prompts: 'Saved prompts',
  folders: 'Folders',
  folder_items: 'Folder items',
  t2i_images: 'Text to Image settings',
  i2v_videos: 'Video settings',
  workflow_presets: 'Workflow presets',
  writer_adapters: 'Adapter names',
  tombstones: 'Deleted-image training data',
}

const exporting = ref(false)
const exported = ref('')
const exportError = ref('')

const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const preview = ref<ImportPreview | null>(null)
const mapping = ref<RootMapping>({})
const importError = ref('')
const status = ref<ImportStatus>({ running: false })
const report = ref<ImportReport | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const totals = computed(() => {
  const p = preview.value
  if (!p) return { paths: 0, found: 0 }
  return p.roots.reduce(
    (sum, root) => ({ paths: sum.paths + root.paths, found: sum.found + root.found }),
    { paths: p.outside.paths, found: p.outside.found },
  )
})

const notFound = computed(() => totals.value.paths - totals.value.found)

const reportRows = computed(() =>
  Object.entries(report.value?.counts ?? {})
    .filter(([, c]) => c.added + c.already + c.not_found > 0)
    .map(([kind, c]) => ({ kind, label: KIND_LABELS[kind] ?? kind, ...c })),
)

const phaseText = computed(() => {
  const s = status.value
  if (s.phase === 'ingesting') return `Adding generated files: ${s.done ?? 0} of ${s.total ?? 0}`
  if (s.phase === 'applying') return 'Writing your data…'
  return 'Preparing…'
})

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

async function runExport() {
  exporting.value = true
  exported.value = ''
  exportError.value = ''
  try {
    exported.value = await exportLibraryData()
  } catch (e) {
    exportError.value = messageOf(e)
  } finally {
    exporting.value = false
  }
}

function takePreview(next: ImportPreview) {
  preview.value = next
  mapping.value = Object.fromEntries(next.roots.map((r) => [r.source, r.target]))
}

async function onFileChosen(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploading.value = true
  importError.value = ''
  report.value = null
  preview.value = null
  try {
    takePreview(await uploadBundle(file))
  } catch (e) {
    importError.value = messageOf(e)
  } finally {
    uploading.value = false
  }
}

async function recount() {
  if (!preview.value) return
  importError.value = ''
  try {
    takePreview(await previewImport(preview.value.token, mapping.value))
  } catch (e) {
    importError.value = messageOf(e)
  }
}

async function cancelImport() {
  const token = preview.value?.token
  preview.value = null
  importError.value = ''
  if (token) await discardImport(token).catch(() => {})
}

function schedulePoll() {
  pollTimer = setTimeout(() => void poll(), POLL_MS)
}

async function poll() {
  pollTimer = null
  try {
    status.value = await fetchImportStatus()
  } catch {
    schedulePoll()
    return
  }
  if (status.value.running) {
    schedulePoll()
    return
  }
  if (status.value.error) {
    importError.value = status.value.error
  } else if (status.value.report) {
    report.value = status.value.report
    preview.value = null
  }
}

async function startImport() {
  if (!preview.value) return
  importError.value = ''
  report.value = null
  try {
    await applyImport(preview.value.token, mapping.value)
    status.value = { running: true, phase: 'preparing' }
    schedulePoll()
  } catch (e) {
    importError.value = messageOf(e)
  }
}

onMounted(async () => {
  try {
    const current = await fetchImportStatus()
    status.value = current
    if (current.running) schedulePoll()
    else if (current.report) report.value = current.report
    else if (current.error) importError.value = current.error
  } catch {
    // The buttons report their own failures.
  }
})

onBeforeUnmount(() => {
  if (pollTimer !== null) clearTimeout(pollTimer)
})
</script>

<template>
  <div class="transfer">
    <p class="hint intro">
      Move your ratings and settings to another PC or a new location. Media files are not part of
      this: move them yourself, keeping the folders below your library folder unchanged. Files are
      matched by their path, so a renamed or moved file is not found.
    </p>

    <h4>Export</h4>
    <p class="hint">
      Saves favorites, grades, categories, hindsight briefs, rating history, saved prompts, folders,
      Text to Image and Video settings, workflow presets and the training data of deleted images.
      Metadata, tags, embeddings and thumbnails are rebuilt when the files are imported. Storyboards
      and trained adapters are not included.
    </p>
    <div class="actions">
      <button class="btn-secondary" :disabled="exporting" @click="runExport">
        {{ exporting ? 'Exporting…' : 'Export library data…' }}
      </button>
    </div>
    <p v-if="exported" class="ok">Saved {{ exported }} to your downloads.</p>
    <p v-if="exportError" class="error">{{ exportError }}</p>

    <h4 class="section">Import</h4>
    <p class="hint">
      First add your library folders in the Directories tab and let the media import finish. Your
      existing ratings are never overwritten, and importing the same file again is safe.
    </p>

    <div v-if="status.running" class="progress">{{ phaseText }}</div>

    <template v-else-if="preview">
      <p class="hint">Exported {{ preview.created_at ?? 'at an unknown time' }}</p>
      <div class="roots">
        <div v-for="root in preview.roots" :key="root.source" class="root">
          <div class="root-source" :title="root.source">{{ root.source }}</div>
          <input
            v-model="mapping[root.source]"
            class="root-target"
            type="text"
            list="transfer-targets"
            placeholder="Folder here (leave blank if unchanged)"
            aria-label="Library folder on this PC"
            @change="recount"
          />
          <div class="root-count" :class="{ warn: root.found < root.paths }">
            {{ root.found }} of {{ root.paths }} files found
          </div>
        </div>
        <datalist id="transfer-targets">
          <option v-for="target in preview.targets" :key="target" :value="target" />
        </datalist>
      </div>

      <p v-if="preview.outside.paths" class="hint">
        {{ preview.outside.found }} of {{ preview.outside.paths }} files were outside the library
        folders and keep their original paths.
      </p>
      <p class="summary">{{ totals.found }} of {{ totals.paths }} files found in this library.</p>
      <p v-if="preview.generated_to_ingest" class="hint">
        {{ preview.generated_to_ingest }} generated files on disk are not in the library yet and
        will be added.
      </p>
      <p v-if="notFound" class="warn">
        Data for {{ notFound }} files is skipped for now. Finish the media import or fix the folders
        above, then import this file again to add it.
      </p>

      <div class="actions">
        <button class="btn-primary" @click="startImport">Import</button>
        <button class="btn-secondary" @click="cancelImport">Cancel</button>
      </div>
    </template>

    <div v-else class="actions">
      <input
        ref="fileInput"
        type="file"
        accept=".zip,application/zip"
        hidden
        @change="onFileChosen"
      />
      <button class="btn-secondary" :disabled="uploading" @click="fileInput?.click()">
        {{ uploading ? 'Reading file…' : 'Choose export file…' }}
      </button>
    </div>

    <p v-if="importError" class="error">{{ importError }}</p>

    <div v-if="report" class="report">
      <p class="ok">Import finished.</p>
      <p v-if="report.ingested || report.failed" class="hint">
        Generated files added: {{ report.ingested }}<template v-if="report.failed">
          ({{ report.failed }} could not be read)</template
        >
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Data</th>
            <th scope="col">Added</th>
            <th scope="col">Already here</th>
            <th scope="col">File not found</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in reportRows" :key="row.kind">
            <td>{{ row.label }}</td>
            <td>{{ row.added }}</td>
            <td>{{ row.already }}</td>
            <td>{{ row.not_found }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
h4 { margin: 0 0 10px; font-size: 14px; color: var(--text-color); }
h4.section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-border);
}

.hint { font-size: 12px; color: var(--text-color-secondary); margin: 0 0 8px; }
.intro { margin-bottom: 16px; }
.summary { font-size: 13px; color: var(--text-color); margin: 10px 0 4px; }
.ok { font-size: 13px; color: var(--text-color); margin: 8px 0 0; }
.warn { font-size: 12px; color: var(--warning-color, #d98e04); margin: 4px 0; }
.error { font-size: 13px; color: var(--danger-color); margin: 8px 0 0; }
.progress { font-size: 13px; color: var(--text-color); padding: 8px 0; }

.actions { display: flex; gap: 10px; margin-top: 10px; }

.roots {
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  overflow: hidden;
}

.root {
  display: grid;
  grid-template-columns: 1fr;
  gap: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--surface-border);
}
.root:last-of-type { border-bottom: none; }

.root-source {
  font-size: 12px;
  color: var(--text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.root-target {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}
.root-target:focus { outline: none; border-color: var(--primary-color); }

.root-count { font-size: 12px; color: var(--text-color-secondary); }
.root-count.warn { margin: 0; }

.report { margin-top: 12px; }
.report table { width: 100%; margin-top: 8px; border-collapse: collapse; font-size: 12px; }
.report th, .report td {
  padding: 4px 8px;
  text-align: right;
  color: var(--text-color);
  border-bottom: 1px solid var(--surface-border);
}
.report th { font-weight: 600; color: var(--text-color-secondary); }
.report th:first-child, .report td:first-child { text-align: left; }

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
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
