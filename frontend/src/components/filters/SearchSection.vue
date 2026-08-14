<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSearchStore, TEXT_THRESHOLD_MAX } from '../../stores/search'
import { useFilterStore } from '../../stores/filters'
import { useModelsStore } from '../../stores/models'

const searchStore = useSearchStore()
const filterStore = useFilterStore()
const modelsStore = useModelsStore()

const expanded = ref(true)

// Local commit-on-submit copy of the query; resyncs when the store's query
// changes elsewhere (e.g. mobile, clearAll).
const query = ref(searchStore.textQuery)
watch(
  () => searchStore.textQuery,
  (q) => {
    query.value = q
  },
)

// Tag AND search — chips autocompleted from the known tag vocabulary.
const tagModel = ref<string[]>([...searchStore.tagChips])
const tagSuggestions = ref<string[]>([])

function completeTags(event: { query: string }) {
  const all = (filterStore.filterData.tag ?? []).map((t) => t.key)
  const q = event.query.toLowerCase()
  tagSuggestions.value = all
    .filter((k) => k.toLowerCase().includes(q) && !tagModel.value.includes(k))
    .slice(0, 20)
}

function runTagSearch() {
  void searchStore.searchTags([...tagModel.value])
}

function onSubmitText() {
  searchStore.submitText(query.value)
}

function onClearText() {
  query.value = ''
  searchStore.clearSimilarity()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') onSubmitText()
}

// Threshold slider — scale switches with the active search kind.
const sliderMax = computed(() => (searchStore.isTextSearch ? TEXT_THRESHOLD_MAX : 1))
const sliderStep = computed(() => (searchStore.isTextSearch ? 0.01 : 0.05))
const sliderValue = ref(searchStore.threshold)
watch(
  () => searchStore.threshold,
  (v) => {
    sliderValue.value = v
  },
)
function onSliderCommit() {
  void searchStore.setThreshold(sliderValue.value)
}

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

async function onRebuildIndex() {
  if (!confirm('Rebuild the embedding index with the current CLIP model? This may take a while.')) return
  try {
    await modelsStore.rebuildIndex()
    searchStore.clearSearchError()
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e))
  }
}
</script>

<template>
  <div class="search-section">
    <button class="section-header" @click="expanded = !expanded">
      <span class="expand-icon">{{ expanded ? '▼' : '▶' }}</span>
      <span class="section-label">Search</span>
      <button
        v-if="searchStore.active"
        class="section-clear"
        title="Clear search"
        @click.stop="searchStore.clearAll()"
      >
        &times;
      </button>
    </button>

    <div v-if="expanded" class="section-body">
      <div class="search-row">
        <i class="pi pi-search row-icon" />
        <InputText
          v-model="query"
          class="row-input"
          placeholder="Search by content..."
          @keydown="onKeydown"
        />
        <Button
          v-if="query"
          icon="pi pi-times"
          severity="secondary"
          text
          aria-label="Clear content search"
          @click="onClearText"
        />
        <Button
          icon="pi pi-search"
          text
          aria-label="Run content search"
          @click="onSubmitText"
        />
      </div>

      <div class="search-row">
        <i class="pi pi-tags row-icon" />
        <AutoComplete
          v-model="tagModel"
          class="row-input tag-input"
          multiple
          :suggestions="tagSuggestions"
          placeholder="tag AND tag..."
          @complete="completeTags"
          @update:model-value="runTagSearch"
        />
        <Button
          icon="pi pi-search"
          text
          aria-label="Run tag search"
          @click="runTagSearch"
        />
      </div>

      <div v-if="searchStore.similarTo" class="similar-chip">
        <i class="pi pi-clone" />
        <span class="similar-name" :title="searchStore.similarTo.file_path">
          Similar to {{ searchStore.similarTo.file_name ?? searchStore.similarTo.file_path.split('/').pop() }}
        </span>
        <button
          class="section-clear"
          title="Clear Find Similar"
          @click="searchStore.clearSimilarity()"
        >
          &times;
        </button>
      </div>

      <div class="threshold-row">
        <span class="threshold-label">Threshold</span>
        <input
          v-model.number="sliderValue"
          type="range"
          min="0"
          :max="sliderMax"
          :step="sliderStep"
          class="threshold-slider"
          @change="onSliderCommit"
        />
        <span class="threshold-value">{{ sliderValue.toFixed(2) }}</span>
      </div>

      <div class="model-chip" :title="statusChip.label">
        <span class="dot" :style="{ background: statusChip.dot }"></span>
        <span class="chip-label">{{ statusChip.label }}</span>
      </div>

      <div v-if="searchStore.pending" class="pending-pill">
        Queued: waiting for model…
      </div>

      <div v-if="searchStore.searchError" class="search-error">
        {{ searchStore.searchError }}
        <button class="section-clear" @click="searchStore.clearSearchError()">&times;</button>
      </div>

      <div v-if="searchStore.dimMismatch" class="search-error">
        <b>Index / model mismatch.</b>
        {{ searchStore.dimMismatch.message }}
        <div class="error-actions">
          <Button label="Rebuild index" size="small" severity="warn" @click="onRebuildIndex" />
          <Button label="Dismiss" size="small" severity="secondary" text @click="searchStore.clearSearchError()" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-section {
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

.section-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px;
}

.search-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.row-icon {
  color: var(--text-color-secondary);
  font-size: 13px;
  width: 16px;
  flex-shrink: 0;
}

.row-input {
  flex: 1;
  min-width: 0;
  font-size: 13px;
}

.tag-input :deep(.p-autocomplete-input-multiple) {
  width: 100%;
}

.similar-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  font-size: 12px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.similar-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.threshold-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.threshold-slider {
  flex: 1;
  accent-color: var(--primary-color);
}

.threshold-value {
  font-variant-numeric: tabular-nums;
  width: 34px;
  text-align: right;
}

.model-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chip-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-pill {
  font-size: 12px;
  color: var(--text-color-secondary);
  font-style: italic;
}

.search-error {
  font-size: 12px;
  color: var(--danger-color, #ef4444);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.error-actions {
  display: flex;
  gap: 6px;
}
</style>
