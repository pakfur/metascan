<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useSimilarityStore } from '../../stores/similarity'
import { useModelsStore } from '../../stores/models'

const emit = defineEmits<{
  scan: []
  refresh: []
  'upscale-queue': []
  'find-duplicates': []
  'similarity-settings': []
  config: []
}>()

const simStore = useSimilarityStore()
const modelsStore = useModelsStore()
const query = ref(simStore.contentQuery)

// Query that was submitted while the inference worker wasn't ready.
// Fired automatically once the worker reaches STATE_READY, so the user
// only has to click Search once.
const pendingQuery = ref<string | null>(null)

watch(
  () => simStore.contentQuery,
  (q) => {
    query.value = q
  },
)

onMounted(() => {
  // Bootstrap the models store so the status chip reflects reality on first
  // render rather than waiting for a WS event.
  void modelsStore.loadStatus().catch(() => {
    // non-fatal — the chip will stay grey and WS events will fill in later
  })
})

// State drives both the chip color and whether submit is enabled.
const statusChip = computed(() => {
  const s = modelsStore.inferenceState
  if (s === 'ready') return { dot: '#22c55e', label: 'Model ready' }
  if (s === 'loading' || s === 'spawning') {
    const pct = modelsStore.inferenceProgress.percent
    const stage = modelsStore.inferenceProgress.stage || 'Loading'
    const pctLabel =
      typeof pct === 'number' && pct > 0 ? ` ${Math.round(pct * 100)}%` : ''
    return { dot: '#eab308', label: `${stage}${pctLabel}` }
  }
  if (s === 'error') {
    return { dot: 'var(--danger-color)', label: modelsStore.inferenceError || 'Model error' }
  }
  return { dot: 'var(--text-color-secondary)', label: 'Model not loaded' }
})

const submitDisabled = computed(() => {
  if (!query.value.trim()) return true
  if (modelsStore.inferenceState === 'error') return false  // allow retry — will respawn
  return false  // allow submission any time; coalesce if not ready yet
})

function runSearch(q: string) {
  if (q) void simStore.searchByText(q)
}

// When inference transitions to ready, drain any pending query.
watch(
  () => modelsStore.inferenceState,
  (state) => {
    if (state === 'ready' && pendingQuery.value) {
      const q = pendingQuery.value
      pendingQuery.value = null
      runSearch(q)
    }
  },
)

function onSubmit() {
  const q = query.value.trim()
  if (!q) return
  if (modelsStore.isInferenceReady) {
    pendingQuery.value = null
    runSearch(q)
    return
  }
  // Coalesce: remember the query and fire once the worker is ready.
  // Replacing an existing pending query is intentional — the most-recent
  // input wins.
  pendingQuery.value = q
  // If the worker is idle/stopped/error, nothing will ever reach the
  // ready state on its own — kick off a spawn. When the worker is
  // already loading/spawning we just wait.
  const s = modelsStore.inferenceState
  if (s === 'idle' || s === 'stopped' || s === 'error') {
    void modelsStore.startInferenceWorker()
  }
}

function onClear() {
  query.value = ''
  pendingQuery.value = null
  if (simStore.active && simStore.isContentSearch) {
    simStore.exit()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') onSubmit()
}

async function onRebuildIndex() {
  if (!confirm('Rebuild the embedding index with the current CLIP model? This may take a while.')) return
  try {
    await modelsStore.rebuildIndex()
    simStore.clearSearchError()
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}
</script>

<template>
  <div class="content-search-bar">
    <InputGroup class="search-group">
      <div class="model-chip" :title="statusChip.label">
        <span class="dot" :style="{ background: statusChip.dot }"></span>
        <span class="chip-label">{{ statusChip.label }}</span>
      </div>
      <InputText
        v-model="query"
        placeholder="Search by content..."
        @keydown="onKeydown"
      />
      <Button
        v-if="query"
        icon="pi pi-times"
        severity="secondary"
        aria-label="Clear search"
        @click="onClear"
      />
      <Button
        icon="pi pi-search"
        :disabled="submitDisabled"
        aria-label="Search"
        @click="onSubmit"
      />
    </InputGroup>

    <div v-if="pendingQuery" class="pending-pill" :title="pendingQuery">
      Queued: waiting for model…
    </div>

    <Button label="Scan" class="scan-btn" @click="emit('scan')" />

    <div class="action-group">
      <Button
        v-tooltip.bottom="'Refresh (F5)'"
        icon="pi pi-refresh"
        severity="secondary"
        text
        rounded
        aria-label="Refresh"
        @click="emit('refresh')"
      />
      <Button
        v-tooltip.bottom="'Upscale Queue'"
        icon="pi pi-list"
        severity="secondary"
        text
        rounded
        aria-label="Upscale Queue"
        @click="emit('upscale-queue')"
      />
      <Button
        v-tooltip.bottom="'Find Duplicates (Ctrl+Shift+D)'"
        icon="pi pi-clone"
        severity="secondary"
        text
        rounded
        aria-label="Duplicates"
        @click="emit('find-duplicates')"
      />
      <Button
        v-tooltip.bottom="'Similarity Settings'"
        icon="pi pi-ellipsis-v"
        severity="secondary"
        text
        rounded
        aria-label="Similarity"
        @click="emit('similarity-settings')"
      />
      <Button
        v-tooltip.bottom="'Configuration'"
        icon="pi pi-cog"
        severity="secondary"
        text
        rounded
        aria-label="Config"
        @click="emit('config')"
      />
    </div>

    <Teleport to="body">
      <div v-if="simStore.dimMismatch" class="banner banner-warn">
        <div class="banner-body">
          <b>Index / model mismatch.</b>
          {{ simStore.dimMismatch.message }}
          <span v-if="simStore.dimMismatch.index_model_key">
            (built with <code>{{ simStore.dimMismatch.index_model_key }}</code>)
          </span>
        </div>
        <div class="banner-actions">
          <button class="btn-banner" @click="onRebuildIndex">Rebuild index</button>
          <button class="btn-banner" @click="simStore.clearSearchError()">Dismiss</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.content-search-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
}

.search-group {
  flex: 1;
  min-width: 0;
  max-width: 680px;
}

.scan-btn {
  flex-shrink: 0;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
}

.model-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid var(--surface-border);
  border-right: none;
  border-radius: 6px 0 0 6px;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  max-width: 220px;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chip-label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.pending-pill {
  font-size: 11px;
  color: var(--text-color-secondary);
  border: 1px dashed var(--surface-border);
  padding: 2px 8px;
  border-radius: 999px;
  white-space: nowrap;
}

.banner {
  position: fixed;
  top: 64px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 800;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  border-radius: 8px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.2);
  max-width: 92vw;
}

.banner-warn {
  background: rgba(234, 179, 8, 0.1);
  border: 1px solid #eab308;
  color: var(--text-color);
}

.banner-body { font-size: 12.5px; }
.banner-body code {
  background: rgba(0, 0, 0, 0.1);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 11.5px;
}

.banner-actions { display: flex; gap: 6px; }

.btn-banner {
  padding: 4px 10px;
  font-size: 12px;
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  cursor: pointer;
}

.btn-banner:hover { background: var(--surface-hover); }
</style>
