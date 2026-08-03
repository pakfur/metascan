<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useStoryboardStore } from '../../stores/storyboard'
import CreateStoryboardDialog from './CreateStoryboardDialog.vue'
import PresetRegistrationDialog from './PresetRegistrationDialog.vue'

const store = useStoryboardStore()
const router = useRouter()

const showCreate = ref(false)
const showPresets = ref(false)

onMounted(() => {
  void store.loadList()
})

function open(id: number) {
  router.push({ name: 'storyboard', params: { id } })
}

async function onDelete(id: number, name: string) {
  if (!confirm(`Delete storyboard "${name}"? The folder and its media are kept.`)) return
  await store.remove(id)
}

function onCreated(id: number) {
  showCreate.value = false
  router.push({ name: 'storyboard', params: { id } })
}

// CreateStoryboardDialog's "none yet" hint asks the landing page to open
// preset registration -- dialogs never nest each other, so it bubbles the
// request up here and closes itself first.
function onOpenPresetsFromCreate() {
  showCreate.value = false
  showPresets.value = true
}

function formatDate(raw: string): string {
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString()
}
</script>

<template>
  <div class="storyboard-landing">
    <div class="header-row">
      <h2>Storyboards</h2>
      <div class="header-actions">
        <Button label="New storyboard" icon="pi pi-plus" @click="showCreate = true" />
        <Button
          label="Workflow presets…"
          severity="secondary"
          outlined
          @click="showPresets = true"
        />
      </div>
    </div>

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <div v-if="store.loading" class="muted">Loading…</div>
    <div v-else-if="store.list.length === 0" class="empty-state">
      No storyboards yet — create one and paste your scene text.
    </div>
    <div v-else class="board-list">
      <div v-for="b in store.list" :key="b.id" class="board-row">
        <div class="board-info">
          <div class="board-name">{{ b.name }}</div>
          <div class="board-meta">
            {{ b.aspect_ratio }} &middot; {{ b.target_model }} &middot; updated
            {{ formatDate(b.updated_at) }}
          </div>
        </div>
        <div class="board-actions">
          <button class="btn btn-secondary" @click="open(b.id)">Open</button>
          <button class="btn btn-danger" @click="onDelete(b.id, b.name)">Delete</button>
        </div>
      </div>
    </div>

    <CreateStoryboardDialog
      v-if="showCreate"
      @close="showCreate = false"
      @created="onCreated"
      @open-presets="onOpenPresetsFromCreate"
    />
    <PresetRegistrationDialog v-if="showPresets" @close="showPresets = false" />
  </div>
</template>

<style scoped>
.storyboard-landing {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 20px 60px;
}

.header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.header-row h2 {
  margin: 0;
  font-size: 20px;
  color: var(--text-color);
}

.header-actions {
  display: flex;
  gap: 10px;
}

.muted {
  color: var(--text-color-secondary);
  font-size: 14px;
  padding: 24px 0;
}

.empty-state {
  color: var(--text-color-secondary);
  font-size: 14px;
  text-align: center;
  padding: 48px 16px;
  border: 1px dashed var(--surface-border);
  border-radius: 10px;
}

.board-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.board-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
}

.board-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-color);
}

.board-meta {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin-top: 2px;
}

.board-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.btn {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  font-family: inherit;
}

.btn-secondary {
  background: var(--surface-ground);
  border-color: var(--surface-border);
  color: var(--text-color);
}

.btn-secondary:hover {
  background: var(--surface-hover);
}

.btn-danger {
  background: none;
  border-color: var(--surface-border);
  color: var(--danger-color, #e53e3e);
}

.btn-danger:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 0 0 12px;
}
</style>
