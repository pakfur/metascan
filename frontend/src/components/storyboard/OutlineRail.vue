<template>
  <div class="or-root">
    <template v-for="scene in store.tree?.scenes ?? []" :key="scene.id">
      <div class="or-scene-block">
        <div class="or-scene-head">
          <button type="button" class="or-caret" @click="toggleScene(scene.id)">
            {{ collapsed.has(scene.id) ? '▶' : '▼' }}
          </button>
          <span class="or-scene-name">{{ scene.name }}</span>
          <span
            v-if="scene.charge_in !== null && scene.charge_out !== null"
            class="or-charge"
            :class="chargeClass(scene)"
            :title="`Emotional charge ${scene.charge_in} → ${scene.charge_out}`"
          >{{ scene.charge_in }} → {{ scene.charge_out }}</span>
          <span v-if="scene.template_id" class="or-template" :title="`Shots from template ${scene.template_id}`">⧉</span>
          <span v-if="(scene.template_problems ?? []).length" class="or-problem" :title="(scene.template_problems ?? []).join('\n')">!</span>
          <span v-else-if="scene.outline_stale" class="or-stale" title="Outline changed since this scene was built">↻</span>
          <span class="or-scene-count">{{ scene.panels.length }}</span>
          <button
            type="button"
            class="or-kebab"
            title="Scene actions"
            @click.stop="openSceneMenu(scene, $event)"
          >
            ⋯
          </button>
        </div>

        <template v-if="!collapsed.has(scene.id)">
          <button
            v-for="(p, i) in scene.panels"
            :key="p.id"
            type="button"
            class="or-shot"
            :class="{ active: p.id === store.selectedPanelId }"
            @click="selectShot(scene, p)"
          >
            <span v-if="rowJob(p)" class="or-jobbox">
              <JobBadge :state="rowJob(p)!.state" :error="rowJob(p)!.error" />
            </span>
            <span class="or-shot-text">
              <span class="or-shot-line1">
                <span v-if="p.is_turn === 1" class="or-turn" title="Story turn">★</span>
                {{ i + 1 }}. {{ p.action }}
              </span>
              <span class="or-shot-line2">
                {{ p.beats.length }} beats · {{ shotSecs(p).toFixed(1) }}s
                <template v-if="p.videos.length"> · 🎬{{ p.videos.length }}</template>
              </span>
            </span>
          </button>
        </template>

        <button type="button" class="or-add-shot" @click="addShot(scene)">+ Shot</button>
      </div>
    </template>

    <button type="button" class="or-add-scene" @click="openCreator">+ Scene</button>

    <Menu ref="sceneMenu" :model="menuItems" :popup="true" />

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
import { computed, ref, nextTick } from 'vue'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, Scene } from '../../types/storyboard'
import JobBadge from './JobBadge.vue'
import SceneEditDialog from './SceneEditDialog.vue'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const store = useStoryboardStore()

// Disclosure: collapsed scene ids, local UI state only.
const collapsed = ref(new Set<number>())
function toggleScene(id: number): void {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

function shotSecs(p: Panel): number {
  return p.beats.reduce((s, b) => s + b.duration_s, 0)
}

function chargeClass(scene: Scene): string {
  const ci = scene.charge_in ?? 0
  const co = scene.charge_out ?? 0
  return co > ci ? 'up' : co < ci ? 'down' : 'flat'
}

// A shot row's job badge: the panel's active video job, if any.
function rowJob(p: Panel) {
  return store.panelJobState.get(p.id) ?? null
}

// Selecting a shot: scene + panel synchronously; the store's
// watch(selectedPanelId) nulls selectedBeatId on the NEXT flush, so the
// first-beat selection must land after nextTick or the watcher would undo
// it.
async function selectShot(scene: Scene, p: Panel): Promise<void> {
  store.selectedSceneId = scene.id
  store.selectedPanelId = p.id
  await nextTick()
  store.selectedBeatId = p.beats[0]?.id ?? null
}

async function addShot(scene: Scene): Promise<void> {
  await store.addPanel(scene.id, 'new shot')
  const panels = store.tree?.scenes.find((s) => s.id === scene.id)?.panels ?? []
  const last = panels[panels.length - 1]
  if (last) await selectShot(scene, last)
}

// Scene kebab (decision 4): one Menu instance retargeted per scene.
const sceneMenu = ref<InstanceType<typeof Menu> | null>(null)
const menuScene = ref<Scene | null>(null)
const videoReady = computed(
  () => !!store.tree?.video_target && !!store.tree?.video_preset_id,
)
const menuItems = computed<MenuItem[]>(() => {
  const scene = menuScene.value
  if (!scene) return []
  const items: MenuItem[] = []
  if (videoReady.value) {
    items.push({
      label: 'Render scene video',
      icon: 'pi pi-play',
      disabled: scene.panels.length === 0 || scene.panels.some((p) => store.panelJobState.get(p.id) !== undefined),
      command: () => void renderScene(scene),
    })
  }
  items.push({ label: 'Edit scene', icon: 'pi pi-pencil', command: () => openEditor(scene) })
  const scenes = store.tree?.scenes ?? []
  const next = scenes[scenes.findIndex((s) => s.id === scene.id) + 1]
  items.push({
    label: 'Merge with next scene',
    icon: 'pi pi-arrow-down',
    disabled: !next || store.story.running,
    command: () => void (next && store.mergeScenes([scene.id, next.id])),
  })
  items.push({
    label: 'Delete scene',
    icon: 'pi pi-trash',
    class: 'or-menu-danger',
    command: () => void onDeleteScene(scene),
  })
  return items
})
function openSceneMenu(scene: Scene, e: Event): void {
  menuScene.value = scene
  sceneMenu.value?.toggle(e)
}

async function renderScene(scene: Scene): Promise<void> {
  const res = await store.generateVideo(scene.panels.map((p) => p.id))
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}

// Scene edit/create + three-way delete: ported from SceneStrip.vue:86-164.
const editorOpen = ref(false)
const editorScene = ref<Scene | null>(null)
function openEditor(scene: Scene): void {
  editorScene.value = scene
  editorOpen.value = true
}
function openCreator(): void {
  editorScene.value = null
  editorOpen.value = true
}

const deleteTarget = ref<Scene | null>(null)
function sceneImageCount(scene: Scene): number {
  return scene.panels.reduce(
    (n, p) => n + p.videos.length + p.beats.reduce((m, b) => m + b.images.length, 0),
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
</script>

<style scoped>
.or-root {
  padding: 10px 8px 24px;
}

.or-scene-block {
  margin-bottom: 10px;
}

.or-scene-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
}

.or-caret {
  background: none;
  border: none;
  padding: 0;
  font-size: 10px;
  color: var(--text-color-secondary);
  cursor: pointer;
}

.or-scene-name {
  font-size: 12px;
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.or-scene-count {
  font-size: 11px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.or-charge {
  font-size: 10px;
  padding: 0 4px;
  border-radius: 3px;
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}

.or-charge.up {
  color: var(--ok, #4caf50);
}

.or-charge.down {
  color: var(--danger-color, #e57373);
}

.or-charge.flat {
  color: var(--text-color-secondary);
  opacity: 0.5;
}

.or-template {
  font-size: 11px;
  opacity: 0.7;
}

.or-problem {
  color: var(--red-500);
  font-weight: 700;
}

.or-stale {
  color: var(--yellow-500, #d4a017);
}

.or-kebab {
  width: 18px;
  height: 18px;
  line-height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.15s,
    background 0.15s,
    color 0.15s;
}

.or-scene-head:hover .or-kebab {
  opacity: 1;
}

.or-kebab:hover {
  background: color-mix(in srgb, var(--primary-color) 18%, transparent);
  color: var(--primary-color);
}

.or-shot {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 6px;
  margin-bottom: 1px;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
  color: var(--text-color);
  font-family: inherit;
}

.or-shot.active {
  border-color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 10%, transparent);
}

.or-jobbox {
  position: relative;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface-ground);
}

.or-shot-text {
  flex: 1;
  min-width: 0;
}

.or-shot-line1 {
  display: block;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.or-turn {
  color: var(--primary-color);
  margin-right: 2px;
}

.or-shot-line2 {
  display: block;
  font-size: 10px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.or-add-shot {
  display: block;
  padding: 3px 10px;
  font-size: 11px;
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
  background: none;
  color: var(--text-color-secondary);
  margin: 4px 0 0 6px;
  cursor: pointer;
}

.or-add-scene {
  padding: 3px 10px;
  font-size: 11px;
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
  background: none;
  color: var(--text-color-secondary);
  margin-left: 6px;
  cursor: pointer;
}
</style>

<style>
.or-menu-danger .p-menu-item-label,
.or-menu-danger .p-menu-item-icon {
  color: var(--danger-color);
}
</style>
