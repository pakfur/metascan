<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Panel, Scene } from '../../types/storyboard'
import { buildShotScript, buildShotScriptBlocks } from '../../utils/shotScript'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ panel: Panel; scene: Scene }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const blocks = computed(() =>
  buildShotScriptBlocks(props.panel, props.scene, store.tree?.subjects ?? []),
)

const copied = ref(false)
async function copy(): Promise<void> {
  await copyToClipboard(buildShotScript(props.panel, props.scene, store.tree?.subjects ?? []))
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}
</script>

<template>
  <ModalShell @close="emit('close')">
    <div class="ssd-head">
      <h3>Shot script — shot {{ (panel.sort_order ?? 0) + 1 }}</h3>
    </div>
    <div class="ssd-body">
      <pre class="ssd-block ssd-header-block">{{ blocks.header }}</pre>
      <pre
        v-for="b in blocks.beats"
        :key="b.beatId"
        class="ssd-block"
        :class="{ selected: b.beatId === store.selectedBeatId }"
        @click="store.selectedBeatId = b.beatId"
      >{{ b.text }}</pre>
      <p v-if="blocks.beats.length === 0" class="ssd-hint">(no beats)</p>
    </div>
    <template #actions>
      <button type="button" class="msh-btn" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" @click="emit('close')">Close</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.ssd-head h3 {
  margin: 0 0 10px;
  font-size: 18px;
  font-weight: 600;
}

.ssd-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  max-height: 52vh;
}

.ssd-block {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  cursor: pointer;
}

.ssd-block.selected {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 1px var(--primary-color);
}

.ssd-header-block {
  cursor: default;
}

.ssd-hint {
  font-size: 12px;
  color: var(--text-color-secondary);
}
</style>
