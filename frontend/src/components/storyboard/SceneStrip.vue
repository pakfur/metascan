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
        <div class="scene-corner">
          <button
            v-if="videoReady"
            class="scene-render"
            :disabled="sceneRendering(scene) || scene.panels.length === 0"
            :title="sceneRendering(scene) ? 'Rendering…' : 'Render scene videos'"
            @click.stop="renderScene(scene)"
          >
            ▶
          </button>
          <button class="scene-edit" title="Edit scene" @click.stop="openEditor(scene)">
            ✎
          </button>
          <button
            class="scene-delete"
            title="Delete scene"
            @click.stop="onDeleteScene(scene)"
          >
            ×
          </button>
        </div>
        <div class="scene-name">{{ scene.name }}</div>
        <div class="scene-subtitle">{{ subtitle(scene) }}</div>
        <div v-if="scene.setting" class="scene-setting" :title="scene.setting">
          {{ scene.setting }}
        </div>
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

      <div class="scene-card scene-add" @click="openCreator">
        <span class="scene-add-plus">+</span>
        <span class="scene-add-label">Scene</span>
      </div>
    </template>

    <SceneEditDialog
      v-if="editorOpen"
      :key="editorScene?.id ?? 'new'"
      :scene="editorScene"
      @close="editorOpen = false"
    />

    <DeleteImagesDialog
      v-if="deleteTarget"
      :title="`Delete scene &quot;${deleteTarget.name}&quot;?`"
      message="Its panels have generated images. Delete them permanently, or keep them visible in the media library?"
      :image-count="sceneImageCount(deleteTarget)"
      @purge="confirmDelete(true)"
      @keep="confirmDelete(false)"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, Scene } from '../../types/storyboard'
import SceneEditDialog from './SceneEditDialog.vue'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const store = useStoryboardStore()

// null scene = create mode.
const editorOpen = ref(false)
const editorScene = ref<Scene | null>(null)

function subtitle(scene: Scene): string {
  return (
    scene.subtitle ||
    [scene.location, scene.time_of_day].filter(Boolean).join(' · ') ||
    '—'
  )
}

function keeperSrc(panel: Panel): string | null {
  const img = store.firstBeatKeeper(panel)
  return img ? thumbnailUrl(img.file_path) : null
}

// Scene-card render action — same semantics as the side panel's "Render
// scene" button: every shot in the scene through generateVideo(panelIds),
// skipped entries folded into the header error banner. Only shown when the
// storyboard is video-ready (target + preset), mirroring the header button.
const videoReady = computed(
  () => !!store.tree?.video_target && !!store.tree?.video_preset_id,
)

function sceneRendering(scene: Scene): boolean {
  return scene.panels.some((p) => store.panelJobState.get(p.id) !== undefined)
}

async function renderScene(scene: Scene): Promise<void> {
  const res = await store.generateVideo(scene.panels.map((p) => p.id))
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}

function selectScene(scene: Scene): void {
  store.selectedSceneId = scene.id
  store.selectedPanelId = scene.panels[0]?.id ?? null
}

// Deleting a scene whose panels have generated images asks what happens
// to them (purge / keep in library / cancel); an image-less scene gets a
// plain confirm.
const deleteTarget = ref<Scene | null>(null)

function sceneImageCount(scene: Scene): number {
  return scene.panels.reduce(
    (n, p) => n + p.beats.reduce((m, b) => m + b.images.length, 0),
    0,
  )
}

async function onDeleteScene(scene: Scene): Promise<void> {
  if (sceneImageCount(scene) === 0) {
    if (!confirm(`Delete scene "${scene.name}"? Its panels are removed too.`)) return
    await store.removeScene(scene.id)
    return
  }
  deleteTarget.value = scene
}

async function confirmDelete(purgeImages: boolean): Promise<void> {
  const scene = deleteTarget.value
  deleteTarget.value = null
  if (!scene) return
  await store.removeScene(scene.id, purgeImages)
}

function openCreator(): void {
  editorScene.value = null
  editorOpen.value = true
}

function openEditor(scene: Scene): void {
  editorScene.value = scene
  editorOpen.value = true
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

.scene-corner {
  position: absolute;
  top: 4px;
  right: 4px;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.scene-card:hover .scene-corner {
  opacity: 1;
}

.scene-render,
.scene-edit,
.scene-delete {
  width: 18px;
  height: 18px;
  line-height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  cursor: pointer;
}

.scene-edit {
  font-size: 10px;
}

.scene-render {
  font-size: 8px;
}

.scene-render:hover:not(:disabled) {
  background: color-mix(in srgb, var(--primary-color) 18%, transparent);
  color: var(--primary-color);
}

.scene-render:disabled {
  opacity: 0.45;
  cursor: default;
}

.scene-delete {
  font-size: 13px;
}

.scene-edit:hover {
  background: color-mix(in srgb, var(--primary-color) 18%, transparent);
  color: var(--primary-color);
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

.scene-setting {
  font-size: 11px;
  color: var(--text-color-secondary);
  margin: -6px 0 8px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  font-style: italic;
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
</style>
