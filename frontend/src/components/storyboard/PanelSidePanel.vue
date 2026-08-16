<script setup lang="ts">
import { computed, ref, watch, type Ref } from 'vue'
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

// ---- video prompt (H3 compiler) ------------------------------------------

// Local editable copy of video_prompt, paired with a "last synced from
// server" snapshot -- mirrors PanelDetail.vue's commit-on-change pattern
// (see CLAUDE.md "Detail editors with local commit-on-change copies must
// resync on id + updated_at"). The resync watcher only overwrites when the
// local value still equals its snapshot (no pending uncommitted edit), so a
// compile pass that rewrites video_prompt on the currently-selected panel
// still reaches the textarea, and a stray blur never stomps it back.
const videoPromptVal = ref('')
const videoPromptSnap = ref('')
const videoPromptCopied = ref(false)

function syncField(local: Ref<string>, snap: Ref<string>, serverVal: string): void {
  if (local.value === snap.value) {
    local.value = serverVal
    snap.value = serverVal
  }
}

watch(
  () => [panel.value?.id, panel.value?.updated_at],
  () => {
    const p = panel.value
    if (!p) return
    syncField(videoPromptVal, videoPromptSnap, p.video_prompt ?? '')
  },
  { immediate: true },
)

const videoPromptStatusLabel = computed(() => {
  const p = panel.value
  if (!p) return '—'
  if (p.video_prompt_source === 'user') return 'user edited'
  if (p.video_prompt_source === 'compiled') return 'compiled'
  return '—'
})

function commitVideoPrompt(e: Event): void {
  const val = (e.target as HTMLTextAreaElement).value
  videoPromptVal.value = val
  videoPromptSnap.value = val
  if (!panel.value) return
  const next = val === '' ? null : val
  if (next === (panel.value.video_prompt ?? null)) return
  void store.patchPanelFields(panel.value.id, { video_prompt: next })
}

function unlockVideoPrompt(): void {
  if (!panel.value) return
  void store.patchPanelFields(panel.value.id, { video_prompt_locked: 0 })
}

function compilePanel(): void {
  if (!panel.value) return
  void store.compileVideo([panel.value.id], panel.value.video_prompt_locked === 1)
}

async function copyVideoPrompt(): Promise<void> {
  if (!panel.value?.video_prompt) return
  if (!(await copyToClipboard(panel.value.video_prompt))) return
  videoPromptCopied.value = true
  setTimeout(() => (videoPromptCopied.value = false), 1500)
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
      <div v-if="store.tree?.video_target" class="sp-section">
        <div class="sp-section-header">
          <label class="sp-label">Video prompt</label>
          <span class="sp-video-status">{{ videoPromptStatusLabel }}</span>
          <button
            v-if="panel?.video_prompt_locked === 1"
            type="button"
            class="sp-link-btn"
            @click="unlockVideoPrompt"
          >
            🔒 Unlock
          </button>
        </div>
        <ul v-if="panel?.video_prompt_warnings.length" class="sp-warnings">
          <li v-for="(w, i) in panel.video_prompt_warnings" :key="i">{{ w }}</li>
        </ul>
        <textarea
          class="sp-video-textarea"
          rows="8"
          placeholder="No video prompt compiled yet."
          :value="videoPromptVal"
          @change="commitVideoPrompt"
        />
        <div class="sp-video-actions">
          <button
            type="button"
            class="sp-copy-btn"
            :disabled="store.compile.running"
            @click="compilePanel"
          >
            Compile
          </button>
          <button
            type="button"
            class="sp-copy-btn"
            :disabled="!panel?.video_prompt"
            @click="copyVideoPrompt"
          >
            {{ videoPromptCopied ? 'Copied' : 'Copy' }}
          </button>
        </div>
      </div>

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

.sp-video-status {
  font-size: 11px;
  color: var(--text-color-secondary);
  flex: 1;
}

.sp-link-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  cursor: pointer;
  font-size: 11px;
  text-decoration: underline;
  flex-shrink: 0;
}

.sp-warnings {
  margin: 0;
  padding: 0 0 0 16px;
  list-style: disc;
  color: var(--warn, #e0a030);
  font-size: 11px;
  line-height: 1.5;
}

.sp-video-textarea {
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  font-family: var(--font-mono, ui-monospace, monospace);
  resize: vertical;
  width: 100%;
  box-sizing: border-box;
}

.sp-video-textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

.sp-video-actions {
  display: flex;
  gap: 8px;
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
