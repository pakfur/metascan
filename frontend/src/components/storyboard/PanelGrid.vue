<template>
  <div class="panel-grid">
    <template v-if="store.selectedScene">
      <div
        v-for="panel in store.selectedScene.panels"
        :key="panel.id"
        class="panel-tile"
        :class="{ active: panel.id === store.selectedPanelId }"
        @click="store.selectedPanelId = panel.id"
      >
        <button class="panel-delete" title="Delete panel" @click.stop="onDeletePanel(panel)">
          ×
        </button>

        <div class="panel-thumb">
          <img
            v-if="thumbSrc(panel)"
            :src="thumbSrc(panel)!"
            alt=""
            class="panel-thumb-img"
            :class="{ dimmed: !store.firstBeatKeeper(panel) }"
          />
          <div v-else class="panel-thumb-empty" />

          <span
            v-if="panel.beats[0]?.prompt_locked === 1"
            class="panel-lock"
            title="Prompt locked"
          >🔒</span>

          <span
            v-if="jobState(panel)?.state === 'queued'"
            class="panel-overlay panel-overlay--queued"
          >
            ⏳ queued
          </span>
          <span
            v-else-if="jobState(panel)?.state === 'running'"
            class="panel-overlay panel-overlay--running"
          >
            <span class="pi pi-spin pi-spinner" />
            <span v-if="jobState(panel)?.value != null && jobState(panel)?.max != null">
              {{ jobState(panel)!.value }}/{{ jobState(panel)!.max }}
            </span>
          </span>
          <span
            v-else-if="jobState(panel)?.state === 'failed'"
            class="panel-overlay panel-overlay--failed"
            :title="jobState(panel)?.error ?? 'Generation failed'"
          >
            ⚠
          </span>
        </div>

        <div class="panel-caption">{{ caption(panel) }}</div>
      </div>

      <div class="panel-tile panel-add" @click="onAddTileClick">
        <template v-if="addingPanel">
          <input
            ref="addPanelInput"
            v-model="newPanelAction"
            class="panel-add-input"
            placeholder="Action"
            @click.stop
            @keydown.enter.prevent="submitPanel"
            @keydown.esc.prevent="cancelPanel"
          />
          <div class="panel-add-actions">
            <button class="panel-add-btn" :disabled="!newPanelAction.trim()" @click.stop="submitPanel">
              Add
            </button>
            <button class="panel-add-btn" @click.stop="cancelPanel">Cancel</button>
          </div>
        </template>
        <template v-else>
          <span class="panel-add-plus">+</span>
          <span class="panel-add-label">Panel</span>
        </template>
      </div>
    </template>
    <div v-else class="panel-grid-empty">Select a scene to see its panels.</div>

    <DeleteImagesDialog
      v-if="deleteTarget"
      title="Delete panel?"
      message="This panel has generated images. Delete them permanently, or keep them visible in the media library?"
      :image-count="panelImageCount(deleteTarget)"
      @purge="confirmDelete(true)"
      @keep="confirmDelete(false)"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel } from '../../types/storyboard'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const store = useStoryboardStore()

const addingPanel = ref(false)
const newPanelAction = ref('')
const addPanelInput = ref<HTMLInputElement | null>(null)

// A panel's own video job (still panel-scoped) wins if present, else any of
// its beats' active image-generation job -- image jobs are beat-scoped
// since the shot->beat reorg (see store.beatJobState / jobToBeat).
function jobState(panel: Panel) {
  return store.panelJobBadge(panel)
}

function thumbSrc(panel: Panel): string | null {
  const keeper = store.firstBeatKeeper(panel)
  if (keeper) return thumbnailUrl(keeper.file_path)
  const first = panel.beats[0]?.images[0]
  return first ? thumbnailUrl(first.file_path) : null
}

// Panel-level shot/subject captioning now reads off the first beat -- shots
// (shot size, subjects) are beat-scoped since the shot->beat reorg.
function caption(panel: Panel): string {
  const firstBeat = panel.beats[0]
  const shot = firstBeat?.shot_size ?? '—'
  const names = (firstBeat?.subject_ids ?? [])
    .map((id: number) => store.subjectsById.get(id)?.name)
    .filter((n): n is string => !!n)
    .join(', ')
  return names ? `${shot} · ${names}` : shot
}

function panelImageCount(panel: Panel): number {
  return panel.beats.reduce((n, b) => n + b.images.length, 0)
}

// Deleting a panel with generated images asks what happens to them
// (purge / keep in library / cancel) via DeleteImagesDialog; a panel
// with no images gets a plain confirm.
const deleteTarget = ref<Panel | null>(null)

async function onDeletePanel(panel: Panel): Promise<void> {
  if (panelImageCount(panel) === 0) {
    if (!confirm('Delete this panel?')) return
    await store.removePanel(panel.id)
    return
  }
  deleteTarget.value = panel
}

async function confirmDelete(purgeImages: boolean): Promise<void> {
  const panel = deleteTarget.value
  deleteTarget.value = null
  if (!panel) return
  await store.removePanel(panel.id, purgeImages)
}

function onAddTileClick(): void {
  if (addingPanel.value) return
  addingPanel.value = true
  void nextTick(() => addPanelInput.value?.focus())
}

async function submitPanel(): Promise<void> {
  const action = newPanelAction.value.trim()
  const sceneId = store.selectedScene?.id
  if (!action || sceneId == null) return
  addingPanel.value = false
  newPanelAction.value = ''
  await store.addPanel(sceneId, action)
}

function cancelPanel(): void {
  addingPanel.value = false
  newPanelAction.value = ''
}
</script>

<style scoped>
.panel-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 14px;
  align-content: start;
}

.panel-grid-empty {
  color: var(--text-color-secondary);
  font-size: 14px;
  padding: 24px 0;
  grid-column: 1 / -1;
}

.panel-tile {
  position: relative;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  cursor: pointer;
  overflow: hidden;
}

.panel-tile:hover {
  background: var(--surface-hover);
}

.panel-tile.active {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 1px var(--primary-color);
}

.panel-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 2;
  width: 20px;
  height: 20px;
  line-height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}

.panel-tile:hover .panel-delete {
  opacity: 1;
}

.panel-delete:hover {
  background: var(--danger-color, #e53e3e);
}

.panel-thumb {
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  background: var(--surface-ground);
}

.panel-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.panel-thumb-img.dimmed {
  opacity: 0.45;
}

.panel-thumb-empty {
  width: 100%;
  height: 100%;
  border: 1px dashed var(--surface-border);
  box-sizing: border-box;
}

.panel-lock {
  position: absolute;
  top: 4px;
  left: 4px;
  font-size: 12px;
  filter: drop-shadow(0 1px 1px rgba(0, 0, 0, 0.6));
}

.panel-overlay {
  position: absolute;
  bottom: 4px;
  left: 4px;
  right: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 3px 6px;
  border-radius: 4px;
  font-size: 11px;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
}

.panel-overlay--failed {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 70%, black);
  cursor: help;
}

.panel-caption {
  padding: 6px 8px;
  font-size: 11px;
  color: var(--text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.panel-add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: var(--text-color-secondary);
  min-height: 150px;
  padding: 10px;
}

.panel-add-plus {
  font-size: 20px;
  line-height: 1;
}

.panel-add-label {
  font-size: 12px;
}

.panel-add-input {
  width: 100%;
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 12px;
  font-family: inherit;
  box-sizing: border-box;
  cursor: text;
}

.panel-add-input:focus {
  outline: none;
  border-color: var(--primary-color);
}

.panel-add-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.panel-add-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
}

.panel-add-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.panel-add-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
