<template>
  <div class="scene-strip">
    <template v-if="store.tree">
      <div
        v-for="scene in store.tree.scenes"
        :key="scene.id"
        class="scene-card"
        :class="{ active: scene.id === store.selectedSceneId }"
        @click="selectScene(scene)"
      >
        <button
          class="scene-delete"
          title="Delete scene"
          @click.stop="onDeleteScene(scene)"
        >
          ×
        </button>
        <div class="scene-name">{{ scene.name }}</div>
        <div class="scene-subtitle">{{ subtitle(scene) }}</div>
        <div class="scene-thumbs">
          <div v-for="panel in scene.panels.slice(0, 6)" :key="panel.id" class="scene-thumb">
            <img
              v-if="keeperSrc(panel)"
              :src="keeperSrc(panel)!"
              alt=""
              class="scene-thumb-img"
            />
            <div v-else class="scene-thumb-empty" />
          </div>
        </div>
      </div>

      <div class="scene-card scene-add" @click="onAddTileClick">
        <template v-if="addingScene">
          <input
            ref="addSceneInput"
            v-model="newSceneName"
            class="scene-add-input"
            placeholder="Scene name"
            @click.stop
            @keydown.enter.prevent="submitScene"
            @keydown.esc.prevent="cancelScene"
          />
          <div class="scene-add-actions">
            <button class="scene-add-btn" :disabled="!newSceneName.trim()" @click.stop="submitScene">
              Add
            </button>
            <button class="scene-add-btn" @click.stop="cancelScene">Cancel</button>
          </div>
        </template>
        <template v-else>
          <span class="scene-add-plus">+</span>
          <span class="scene-add-label">Scene</span>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, Scene } from '../../types/storyboard'

const store = useStoryboardStore()

const addingScene = ref(false)
const newSceneName = ref('')
const addSceneInput = ref<HTMLInputElement | null>(null)

function subtitle(scene: Scene): string {
  return [scene.location, scene.time_of_day].filter(Boolean).join(' · ') || '—'
}

function keeperSrc(panel: Panel): string | null {
  const img = store.keeperImage(panel)
  return img ? thumbnailUrl(img.file_path) : null
}

function selectScene(scene: Scene): void {
  store.selectedSceneId = scene.id
  store.selectedPanelId = scene.panels[0]?.id ?? null
}

async function onDeleteScene(scene: Scene): Promise<void> {
  if (!confirm(`Delete scene "${scene.name}"? Its panels and images are removed too.`)) return
  await store.removeScene(scene.id)
}

function onAddTileClick(): void {
  if (addingScene.value) return
  addingScene.value = true
  void nextTick(() => addSceneInput.value?.focus())
}

async function submitScene(): Promise<void> {
  const name = newSceneName.value.trim()
  if (!name) return
  addingScene.value = false
  newSceneName.value = ''
  await store.addScene(name)
}

function cancelScene(): void {
  addingScene.value = false
  newSceneName.value = ''
}
</script>

<style scoped>
.scene-strip {
  display: flex;
  gap: 10px;
  padding: 12px 20px;
  overflow-x: auto;
  border-bottom: 1px solid var(--surface-border);
  flex-shrink: 0;
}

.scene-card {
  position: relative;
  flex: 0 0 auto;
  width: 190px;
  padding: 10px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  cursor: pointer;
}

.scene-card:hover {
  background: var(--surface-hover);
}

.scene-card.active {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 1px var(--primary-color);
}

.scene-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 18px;
  height: 18px;
  line-height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}

.scene-card:hover .scene-delete {
  opacity: 1;
}

.scene-delete:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 18%, transparent);
  color: var(--danger-color, #e53e3e);
}

.scene-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scene-subtitle {
  font-size: 11px;
  color: var(--text-color-secondary);
  margin: 2px 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.scene-thumbs {
  display: flex;
  gap: 4px;
}

.scene-thumb {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface-ground);
  flex-shrink: 0;
}

.scene-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.scene-thumb-empty {
  width: 100%;
  height: 100%;
  border: 1px dashed var(--surface-border);
  box-sizing: border-box;
  border-radius: 4px;
}

.scene-add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: var(--text-color-secondary);
  min-height: 76px;
}

.scene-add-plus {
  font-size: 18px;
  line-height: 1;
}

.scene-add-label {
  font-size: 12px;
}

.scene-add-input {
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

.scene-add-input:focus {
  outline: none;
  border-color: var(--primary-color);
}

.scene-add-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.scene-add-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
}

.scene-add-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.scene-add-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
