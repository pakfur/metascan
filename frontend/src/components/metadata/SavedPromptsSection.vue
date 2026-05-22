<script setup lang="ts">
import { computed, watch } from 'vue'
import { useMediaStore } from '../../stores/media'
import { usePromptStore } from '../../stores/prompt'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'

const mediaStore = useMediaStore()
const promptStore = usePromptStore()
const toast = useToast()

const media = computed(() => mediaStore.selectedMedia)
const saved = computed(() =>
  media.value ? (promptStore.savedByPath[media.value.file_path] ?? []) : [],
)

watch(
  media,
  async (m) => {
    if (m && !(m.file_path in promptStore.savedByPath)) {
      try {
        await promptStore.loadSavedPrompts(m.file_path)
      } catch {
        /* non-fatal */
      }
    }
  },
  { immediate: true },
)

async function handleCopy(text: string) {
  await copyToClipboard(text)
  toast.show('Copied to clipboard', 'success')
}

async function handleDelete(id: number) {
  if (!media.value) return
  if (!window.confirm('Delete this saved prompt?')) return
  try {
    await promptStore.deleteSavedPrompt(id, media.value.file_path)
    toast.show('Deleted')
  } catch (e) {
    toast.show(`Delete failed: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  }
}
</script>

<template>
  <details v-if="saved.length" class="meta-section" open>
    <summary>Saved Prompts ({{ saved.length }})</summary>
    <div v-for="p in saved" :key="p.id" class="saved-prompt-row">
      <div class="saved-prompt-header">
        <span class="saved-prompt-name">{{ p.name }}</span>
        <span class="saved-prompt-meta">{{ p.target_model }} · {{ p.architecture }}</span>
        <button class="icon-btn" :title="'Copy'" aria-label="Copy" @click="handleCopy(p.prompt)">⧉</button>
        <button class="icon-btn danger" :title="'Delete'" aria-label="Delete" @click="handleDelete(p.id)">×</button>
      </div>
      <pre class="saved-prompt-body">{{ p.prompt }}</pre>
    </div>
  </details>
</template>

<style scoped>
.saved-prompt-row {
  padding: 6px 0;
  border-bottom: 1px solid var(--surface-border);
}
.saved-prompt-row:last-child {
  border-bottom: none;
}
.saved-prompt-header {
  display: flex;
  gap: 8px;
  align-items: center;
}
.saved-prompt-name {
  font-weight: 600;
  flex: 1;
  color: var(--text-color);
}
.saved-prompt-meta {
  font-size: 11px;
  color: var(--text-color-secondary);
}
.icon-btn {
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  font-size: 14px;
  padding: 0 4px;
}
.icon-btn.danger {
  color: var(--danger-color);
}
.saved-prompt-body {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 4px 0 0;
  padding: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  border-radius: 4px;
  font-size: 12px;
  font-family: inherit;
}
</style>
