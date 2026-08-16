<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { buildShotScript } from '../../utils/shotScript'
import { copyToClipboard } from '../../utils/clipboard'
import BeatForm from './BeatForm.vue'

type TabKey = 'edit' | 'preview'

const store = useStoryboardStore()
const activeTab = ref<TabKey>('edit')

const panel = computed(() => store.selectedPanel)
const scene = computed(() => store.selectedScene)

const shotScript = computed(() => {
  if (!panel.value || !scene.value) return ''
  return buildShotScript(panel.value, scene.value, store.tree?.subjects ?? [])
})

const scriptCopied = ref(false)
const promptCopied = ref(false)

async function copyScript(): Promise<void> {
  if (!(await copyToClipboard(shotScript.value))) return
  scriptCopied.value = true
  setTimeout(() => (scriptCopied.value = false), 1500)
}

async function copyPrompt(): Promise<void> {
  if (!panel.value?.prompt) return
  if (!(await copyToClipboard(panel.value.prompt))) return
  promptCopied.value = true
  setTimeout(() => (promptCopied.value = false), 1500)
}
</script>

<template>
  <div class="sp-root">
    <nav class="sp-tabs">
      <button class="sp-tab" :class="{ active: activeTab === 'edit' }" @click="activeTab = 'edit'">
        Edit
      </button>
      <button
        class="sp-tab"
        :class="{ active: activeTab === 'preview' }"
        @click="activeTab = 'preview'"
      >
        Preview
      </button>
    </nav>

    <div v-if="activeTab === 'edit'" class="sp-panel">
      <BeatForm v-if="store.selectedBeat" :beat="store.selectedBeat" :subjects="store.tree?.subjects ?? []" />
      <p v-else class="sp-hint">Select a beat to edit.</p>
    </div>

    <div v-else class="sp-panel sp-preview">
      <div class="sp-section">
        <div class="sp-section-header">
          <label class="sp-label">Shot script</label>
          <button type="button" class="sp-copy-btn" @click="copyScript">
            {{ scriptCopied ? 'Copied' : 'Copy' }}
          </button>
        </div>
        <pre class="sp-script">{{ shotScript }}</pre>
      </div>

      <div class="sp-section">
        <div class="sp-section-header">
          <label class="sp-label">Image prompt</label>
          <button
            type="button"
            class="sp-copy-btn"
            :disabled="!panel?.prompt"
            @click="copyPrompt"
          >
            {{ promptCopied ? 'Copied' : 'Copy' }}
          </button>
        </div>
        <pre v-if="panel?.prompt" class="sp-script">{{ panel.prompt }}</pre>
        <p v-else class="sp-hint">No prompt synthesized yet.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sp-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border-left: 1px solid var(--surface-border);
  background: var(--surface-card);
}

.sp-tabs {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--surface-border);
  flex-shrink: 0;
  padding: 0 12px;
}

.sp-tab {
  background: none;
  border: none;
  padding: 10px 14px;
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.sp-tab:hover {
  color: var(--text-color);
}

.sp-tab.active {
  color: var(--text-color);
  border-bottom-color: var(--primary-color);
}

.sp-panel {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px;
}

.sp-hint {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin: 0;
}

.sp-preview {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.sp-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.sp-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sp-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.sp-copy-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}

.sp-copy-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.sp-copy-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sp-script {
  margin: 0;
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  font-family: var(--font-mono, ui-monospace, monospace);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
  max-height: 45vh;
}
</style>
