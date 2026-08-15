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
        <span v-if="store.synthesis.running" class="sb-chip">
          synthesizing {{ store.synthesis.done }}/{{ store.synthesis.total }}
        </span>
        <span
          v-else-if="store.synthesis.error"
          class="sb-chip sb-chip--error"
          :title="store.synthesis.error"
        >
          synthesis failed
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
            label="Import text"
            icon="pi pi-file-import"
            text
            :disabled="!store.tree"
            @click="importOpen = true"
          />
          <Button
            label="Compose"
            icon="pi pi-sparkles"
            text
            :disabled="!store.tree"
            @click="composeOpen = true"
          />
          <Button
            label="Synthesize"
            text
            :disabled="store.loading || !store.tree"
            @click="store.synthesize()"
          />
          <Button
            label="Generate all"
            :disabled="store.loading || !store.tree"
            @click="store.generate()"
          />
          <Button
            label="Cancel"
            severity="danger"
            text
            :disabled="!store.tree"
            @click="store.cancelAll()"
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
        </div>
      </header>

      <template v-if="store.tree">
        <SceneStrip />
        <PanelGrid />
        <PanelDetail v-if="store.selectedPanel" />
      </template>
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
import { useViewport } from '../composables/useViewport'
import { useStoryboardStore } from '../stores/storyboard'
import StoryboardLanding from '../components/storyboard/StoryboardLanding.vue'
import SceneStrip from '../components/storyboard/SceneStrip.vue'
import PanelGrid from '../components/storyboard/PanelGrid.vue'
import PanelDetail from '../components/storyboard/PanelDetail.vue'
import ImportTextDialog from '../components/storyboard/ImportTextDialog.vue'
import OutlineDialog from '../components/storyboard/OutlineDialog.vue'
import StoryboardSettingsDialog from '../components/storyboard/StoryboardSettingsDialog.vue'

const props = defineProps<{ id?: number }>()
const { isMobile } = useViewport()
const store = useStoryboardStore()
const importOpen = ref(false)
const composeOpen = ref(false)
const settingsOpen = ref(false)

// attachWs() registers its cleanup via onUnmounted from inside setup, so it
// must be called exactly once here in the script setup body -- never from
// onMounted or a watcher callback (see storyboard.ts's attachWs comment).
store.attachWs()

// A non-finite id (e.g. /storyboard/abc -> router props does Number('abc')
// -> NaN) and a failed load() (404) both resolve to the same "not found"
// state instead of ever calling store.load(NaN) or rendering a blank shell.
const notFound = computed(() => {
  if (props.id === undefined) return false
  if (!Number.isFinite(props.id)) return true
  return store.error !== null && store.tree === null
})

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
.sb-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.sb-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 20px;
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

.sb-loading {
  padding: 40px;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 14px;
}

.storyboard-shell {
  padding: 32px 20px;
}
</style>
