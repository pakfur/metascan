<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { VIDEO_ANCHORS, type Panel } from '../../types/storyboard'
import { copyToClipboard } from '../../utils/clipboard'
import ModalShell from './ModalShell.vue'

const props = defineProps<{ panel: Panel; index: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const store = useStoryboardStore()

const VIDEO_TARGET_LABELS: Record<string, string> = { minimax: 'MiniMax H3' }
const chipLabel = computed(() => {
  const name = VIDEO_TARGET_LABELS[store.tree?.video_target ?? ''] ?? store.tree?.video_target
  return store.tree?.video_mode ? `${name} · ${store.tree.video_mode}` : (name ?? '')
})

const words = computed(() =>
  props.panel.video_prompt ? props.panel.video_prompt.trim().split(/\s+/).length : 0,
)
const sourceLabel = computed(() =>
  props.panel.video_prompt_source === 'user'
    ? 'user edited'
    : (props.panel.video_prompt_source ?? 'not compiled'),
)

// Anchor select only matters when the video mode consumes a first-frame
// anchor (same gate as the old PanelSidePanel).
const anchorRelevant = computed(
  () => store.tree?.video_mode === 'i2va' || store.tree?.video_mode === 'fl2va',
)
const ANCHOR_LABELS: Record<string, string> = {
  keeper: 'First frame from keeper',
  prev_last: 'Continue from previous shot',
}
function onAnchorChange(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  void store.patchPanelFields(props.panel.id, { video_anchor: val || null })
}

function unlock(): void {
  void store.patchPanelFields(props.panel.id, { video_prompt_locked: 0 })
}

const copied = ref(false)
async function copy(): Promise<void> {
  if (!props.panel.video_prompt) return
  await copyToClipboard(props.panel.video_prompt)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

function compile(): void {
  void store.compileVideo([props.panel.id])
}

async function renderVideo(): Promise<void> {
  const res = await store.generateVideo([props.panel.id])
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}
</script>

<template>
  <ModalShell @close="emit('close')">
    <div class="vpd-head">
      <h3>Video prompt — shot {{ index + 1 }}</h3>
      <span class="vpd-chip">{{ chipLabel }}</span>
      <select
        v-if="anchorRelevant"
        class="vpd-anchor"
        title="First-frame anchor for this shot's video"
        :value="panel.video_anchor ?? ''"
        @change="onAnchorChange"
      >
        <option value="">None</option>
        <option v-for="a in VIDEO_ANCHORS" :key="a" :value="a">{{ ANCHOR_LABELS[a] }}</option>
      </select>
      <span class="vpd-hint vpd-meta">
        {{ sourceLabel }}<template v-if="words"> · {{ words }} words</template>
      </span>
      <button v-if="panel.video_prompt_locked === 1" type="button" class="vpd-link" @click="unlock">
        🔒 Unlock
      </button>
    </div>
    <ul v-if="panel.video_prompt_warnings.length" class="vpd-warnings">
      <li v-for="(w, i) in panel.video_prompt_warnings" :key="i">{{ w }}</li>
    </ul>
    <pre v-if="panel.video_prompt" class="vpd-body">{{ panel.video_prompt }}</pre>
    <p v-else class="vpd-hint vpd-empty">
      No video prompt compiled yet — Compile builds it from this shot's beats.
    </p>
    <p class="vpd-hint vpd-note">
      Compiled from the beats below. Edit a beat's framing, cast or dialog and recompile —
      hand-editing this text locks it against the next compile pass.
    </p>
    <template #actions>
      <button type="button" class="msh-btn msh-btn--primary" :disabled="store.compile.running" @click="compile">Compile</button>
      <button type="button" class="msh-btn" :disabled="!panel.video_prompt" @click="copy">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button type="button" class="msh-btn" :disabled="!panel.video_prompt" @click="renderVideo">Render video</button>
      <button type="button" class="msh-btn" @click="emit('close')">Close</button>
    </template>
  </ModalShell>
</template>

<style scoped>
.vpd-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.vpd-head h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.vpd-chip {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 12%, transparent);
}

.vpd-meta {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.vpd-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.vpd-link {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  text-decoration: underline;
  font-size: 11px;
  cursor: pointer;
}

.vpd-warnings {
  list-style: disc;
  padding: 8px 10px 8px 26px;
  margin: 0 0 10px;
  color: var(--warn);
  font-size: 12px;
  line-height: 1.5;
  background: color-mix(in srgb, var(--warn) 14%, transparent);
  border-radius: 6px;
}

.vpd-body {
  margin: 0;
  padding: 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
  max-height: 52vh;
}

.vpd-empty {
  font-size: 13px;
  padding: 24px 0;
  text-align: center;
}

.vpd-note {
  margin-top: 10px;
}

.vpd-anchor {
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
}
</style>
