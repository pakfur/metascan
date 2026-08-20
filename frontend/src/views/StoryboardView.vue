<template>
  <div class="storyboard-view">
    <div v-if="isMobile" class="mobile-note">
      <p>The storyboard editor is desktop-only.</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
    <div v-else-if="id === undefined" class="storyboard-shell">
      <StoryboardLanding />
    </div>
    <div v-else-if="notFound" class="storyboard-shell">
      <p>Storyboard not found.</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
    <div v-else class="sb-root">
      <header class="sb-header">
        <RouterLink to="/" class="sb-back">← Library</RouterLink>
        <h2>{{ store.tree?.name }}</h2>
        <span
          v-if="videoLabel"
          class="sb-chip sb-chip--video"
          title="Video model and mode driving the compiled video prompts — change in Storyboard settings"
        >
          {{ videoLabel }}
        </span>
        <span v-if="store.story.running" class="sb-chip">
          Composing {{ store.story.stage }} {{ store.story.done }}/{{ store.story.total }}
        </span>
        <span
          v-else-if="store.story.error"
          class="sb-chip sb-chip--error"
          :title="store.story.error"
        >
          compose failed
        </span>
        <span v-if="store.compile.running" class="sb-chip">
          Compiling {{ store.compile.done }}/{{ store.compile.total }}
        </span>
        <span
          v-else-if="store.compile.error"
          class="sb-chip sb-chip--error"
          :title="store.compile.error"
        >
          compile failed
        </span>
        <span v-if="store.error" class="sb-chip sb-chip--error" :title="store.error">
          <span class="sb-chip-text">{{ store.error }}</span>
          <button
            type="button"
            class="sb-chip-dismiss"
            aria-label="Dismiss error"
            @click="store.error = null"
          >
            ×
          </button>
        </span>
        <div class="sb-actions">
          <Button
            label="Compose"
            icon="pi pi-sparkles"
            text
            :disabled="!store.tree"
            @click="composeOpen = true"
          />
          <Button
            icon="pi pi-ellipsis-h"
            text
            rounded
            aria-label="More actions"
            title="Import text, compile, render, cancel"
            :disabled="!store.tree"
            @click="overflowMenu?.toggle($event)"
          />
          <Button
            icon="pi pi-cog"
            text
            rounded
            aria-label="Storyboard settings"
            title="Storyboard settings"
            :disabled="!store.tree"
            @click="settingsOpen = true"
          />
          <Menu ref="overflowMenu" :model="overflowItems" :popup="true" />
        </div>
      </header>

      <div v-if="store.tree" class="sb-body">
        <aside class="sb-rail"><OutlineRail /></aside>
        <ShotDocument />
      </div>
      <div v-else-if="store.loading" class="sb-loading">Loading…</div>
    </div>

    <ImportTextDialog v-if="importOpen" @close="importOpen = false" />
    <OutlineDialog v-if="composeOpen" :open="composeOpen" @close="composeOpen = false" />
    <StoryboardSettingsDialog v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import Menu from 'primevue/menu'
import type { MenuItem } from 'primevue/menuitem'
import { useViewport } from '../composables/useViewport'
import { useStoryboardStore } from '../stores/storyboard'
import StoryboardLanding from '../components/storyboard/StoryboardLanding.vue'
import OutlineRail from '../components/storyboard/OutlineRail.vue'
import ShotDocument from '../components/storyboard/ShotDocument.vue'
import ImportTextDialog from '../components/storyboard/ImportTextDialog.vue'
import OutlineDialog from '../components/storyboard/OutlineDialog.vue'
import StoryboardSettingsDialog from '../components/storyboard/StoryboardSettingsDialog.vue'

const props = defineProps<{ id?: number }>()
const { isMobile } = useViewport()
const store = useStoryboardStore()
const importOpen = ref(false)
const composeOpen = ref(false)
const settingsOpen = ref(false)
const overflowMenu = ref<InstanceType<typeof Menu> | null>(null)

const stageBusy = computed(() => store.compile.running || store.story.running)

const overflowItems = computed<MenuItem[]>(() => {
  const items: MenuItem[] = [
    {
      label: 'Import text',
      icon: 'pi pi-file-import',
      disabled: !store.tree,
      command: () => (importOpen.value = true),
    },
  ]
  if (store.tree?.video_target) {
    items.push({
      label: 'Compile video prompts',
      icon: 'pi pi-file',
      disabled: stageBusy.value,
      command: () => void store.compileVideo(),
    })
  }
  if (store.tree?.video_target && store.tree?.video_preset_id) {
    items.push({
      label: 'Generate video',
      icon: 'pi pi-video',
      disabled: stageBusy.value,
      command: () => void onGenerateVideo(),
    })
  }
  items.push({ separator: true })
  items.push({
    label: 'Cancel',
    icon: 'pi pi-times',
    class: 'sb-menu-danger',
    disabled: !store.tree,
    command: () => void store.cancelAll(),
  })
  return items
})

// attachWs() registers its cleanup via onUnmounted from inside setup, so it
// must be called exactly once here in the script setup body -- never from
// onMounted or a watcher callback (see storyboard.ts's attachWs comment).
store.attachWs()

const VIDEO_TARGET_LABELS: Record<string, string> = { minimax: 'MiniMax H3' }

const videoLabel = computed(() => {
  const target = store.tree?.video_target
  if (!target) return null
  const name = VIDEO_TARGET_LABELS[target] ?? target
  const mode = store.tree?.video_mode
  return mode ? `${name} · ${mode}` : name
})

// A non-finite id (e.g. /storyboard/abc -> router props does Number('abc')
// -> NaN) and a failed load() (404) both resolve to the same "not found"
// state instead of ever calling store.load(NaN) or rendering a blank shell.
const notFound = computed(() => {
  if (props.id === undefined) return false
  if (!Number.isFinite(props.id)) return true
  return store.error !== null && store.tree === null
})

// Generate-all has no per-item outcome to show beyond store.error (the
// existing sb-chip--error banner above). generate-video's 200 response can
// additionally carry `skipped` (panels the run didn't fail on, but couldn't
// submit) -- fold those into the same banner mechanism, same treatment as a
// thrown error.
async function onGenerateVideo(): Promise<void> {
  const res = await store.generateVideo()
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}

// Single load path: the immediate watcher fires once on mount (covering the
// initial load) and again on every id change (covering navigation between
// boards), so there's no separate onMounted call that could double-load.
watch(
  () => props.id,
  (newId) => {
    if (newId === undefined || !Number.isFinite(newId)) return
    void store.load(newId)
  },
  { immediate: true },
)
</script>

<style scoped>
/* #app is height:100% + overflow:hidden; without an explicit height here
   the view grows with content and every inner overflow-y:auto (rail, shot
   document) has nothing to scroll within. */
.storyboard-view {
  height: 100%;
  min-height: 0;
}

.sb-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.sb-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--surface-border);
  flex-shrink: 0;
}

.sb-back {
  color: var(--text-color-secondary);
  font-size: 13px;
  text-decoration: none;
  flex-shrink: 0;
}

.sb-back:hover {
  color: var(--text-color);
}

.sb-header h2 {
  margin: 0;
  font-size: 16px;
  color: var(--text-color);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-chip {
  font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-hover);
  padding: 4px 10px;
  border-radius: 999px;
  flex-shrink: 0;
}

.sb-chip--video {
  color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 12%, transparent);
}

.sb-chip--error {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 360px;
  color: var(--danger-color, #e53e3e);
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
}

.sb-chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sb-chip-dismiss {
  flex-shrink: 0;
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  color: inherit;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.sb-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.sb-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.sb-rail {
  flex: 0 0 270px;
  min-height: 0;
  overflow-y: auto;
  border-right: 1px solid var(--surface-border);
}

.sb-loading {
  padding: 40px;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 14px;
}

.storyboard-shell {
  height: 100%;
  overflow-y: auto;
  padding: 32px 20px;
}
</style>

<style>
/* Unscoped: the PrimeVue Menu teleports to <body>, outside this
   component's scoped-style boundary. */
.sb-menu-danger .p-menu-item-label,
.sb-menu-danger .p-menu-item-icon {
  color: var(--danger-color);
}
</style>
