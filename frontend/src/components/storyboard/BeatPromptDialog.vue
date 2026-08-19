<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat } from '../../types/storyboard'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ beat: Beat; index: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const draft = ref(props.beat.prompt ?? '')
const dirty = computed(() => draft.value !== (props.beat.prompt ?? ''))
const words = computed(() => (draft.value.trim() ? draft.value.trim().split(/\s+/).length : 0))

const status = computed(() => {
  if (props.beat.prompt_locked === 1) return '🔒 edited'
  if (props.beat.prompt_source === 'llm') return 'synthesized'
  if (props.beat.prompt_source === 'brief') return 'brief fallback'
  return 'not synthesized'
})

const context = computed(() => {
  const cam = props.beat.camera_motion ? ` · ${props.beat.camera_motion.replace(/_/g, ' ')}` : ''
  return `${props.beat.shot_size ?? '—'} · ${props.beat.angle ?? '—'} · ${props.beat.lens ?? '—'}${cam} — ${props.beat.action}`
})

function unlock(): void {
  void store.patchBeatFields(props.beat.id, { prompt_locked: 0 })
}

function commit(): void {
  // Server forces prompt_locked=1 / prompt_source='user' when `prompt` is in
  // the PATCH body -- send only the prompt (see Global Constraints).
  if (dirty.value) void store.patchBeatFields(props.beat.id, { prompt: draft.value })
  emit('close')
}

const copied = ref(false)
async function copy(): Promise<void> {
  if (!draft.value) return
  await copyToClipboard(draft.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

function resynth(): void {
  void store.synthesize([props.beat.id], true)
}
</script>

<template>
  <ModalShell width="576px" @close="emit('close')">
    <div class="bpd-head">
      <h3>Prompt — beat {{ index + 1 }}</h3>
      <span class="bpd-hint">{{ status }}</span>
      <span class="bpd-hint bpd-words">{{ words }} words</span>
      <button v-if="beat.prompt_locked === 1" type="button" class="bpd-link" @click="unlock">Unlock</button>
    </div>
    <p class="bpd-context">{{ context }}</p>
    <textarea v-model="draft" rows="16" class="bpd-body" placeholder="No prompt synthesized yet." />
    <p class="bpd-note">
      Saving marks the prompt user-edited and locks it, so the next Synthesize pass leaves it alone.
    </p>
    <template #actions>
      <button type="button" class="msh-btn msh-btn--primary" @click="commit">{{ dirty ? 'Save prompt' : 'Done' }}</button>
      <button type="button" class="msh-btn" :disabled="!draft" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" :disabled="store.synthesis.running" @click="resynth">Re-synth</button>
      <button type="button" class="msh-btn" @click="emit('close')">Cancel</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.bpd-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.bpd-head h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.bpd-words {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.bpd-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.bpd-link {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  text-decoration: underline;
  font-size: 11px;
  cursor: pointer;
}

.bpd-context {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin: 0 0 8px;
}

.bpd-body {
  width: 100%;
  box-sizing: border-box;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  padding: 14px;
  resize: vertical;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
}

.bpd-body:focus {
  outline: none;
  border-color: var(--primary-color);
}

.bpd-note {
  font-size: 11px;
  color: var(--text-color-secondary);
  margin: 10px 0 0;
}
</style>
