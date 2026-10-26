<script setup lang="ts">
// The T2I result viewer's Details panel (MediaViewer's `details` slot): what
// made the image on show. The dialog hands the store's live row (`imageAt`),
// never the viewer's frozen copy, so a grade that lifts a scorecard blind
// fills the prompt and writer in once the refetch lands. A blind scorecard
// image shows its brief and its model, workflow and seed: both images of a
// pair share them, so they tell nothing about who wrote which. Its prompt and
// writer stay hidden while the viewer's judgment or the row says it is blind
// (the server left them out of a row fetched blind anyway).
import { computed } from 'vue'
import { t2iBlind, t2iImageDetails, t2iWriterLabel } from '../../types/t2i'
import type { T2iImage, T2iModelInfo } from '../../types/t2i'
import type { WriterAdapter } from '../../types/writers'
import type { WorkflowPreset } from '../../types/storyboard'

const props = defineProps<{
  image: T2iImage | null
  /**
   * The viewer's judgment says a scorecard image with no grade: a second
   * check beside the row's own grade, which the store keeps current.
   */
  blind: boolean
  presets: WorkflowPreset[]
  /** The writer-adapter registry, null until it has loaded. */
  adapters: WriterAdapter[] | null
  models: T2iModelInfo[]
}>()

// What the server strips from a blind row stays out of sight here too.
const hidden = computed(() => props.blind || (props.image ? t2iBlind(props.image) : false))
const isScorecard = computed(() => !!props.image?.scorecard_id)
// Graded, but the row on hand is still the copy fetched blind: the full one
// is on its way (the store's fillIfRevealed fetches it).
const loading = computed(() => !hidden.value && !!props.image?.scorecard_blind)

/** The caption as the writer saw it, when wildcards or characters changed it. */
const resolvedCaption = computed(() => {
  if (hidden.value) return null
  const resolved = props.image?.resolved_caption?.trim()
  if (!resolved) return null
  return resolved !== (props.image?.caption ?? '').trim() ? resolved : null
})

const writer = computed(() => t2iWriterLabel(props.image?.writer_id, props.adapters))

const modelLabel = computed(() => {
  const id = props.image?.model
  if (!id) return null
  return props.models.find((m) => m.id === id)?.label ?? id
})

const workflow = computed(() => {
  const id = props.image?.preset_id
  if (id == null) return null
  return props.presets.find((p) => p.id === id)?.name ?? `Workflow #${id} (no longer listed)`
})

const loras = computed(() => (props.image?.loras ?? []).map((l) => `${l.name} × ${l.strength}`))
</script>

<template>
  <div class="t2i-info" data-testid="t2i-image-info">
    <p v-if="!image" class="muted">No generation details for this image.</p>
    <template v-else>
      <p v-if="isScorecard" class="note">
        <template v-if="hidden">Scorecard image: its prompt and writer show once you grade it (P or X).</template>
        <template v-else>Scorecard image</template>
      </p>

      <section>
        <h4>{{ isScorecard ? 'Brief' : 'Caption' }}</h4>
        <p v-if="image.caption" class="text">{{ image.caption }}</p>
        <p v-else class="muted">No caption recorded</p>
        <template v-if="resolvedCaption">
          <h5>As the writer saw it</h5>
          <p class="text">{{ resolvedCaption }}</p>
        </template>
      </section>

      <section>
        <h4>Prompt</h4>
        <p v-if="hidden" class="muted">Hidden until graded</p>
        <p v-else-if="loading" class="muted">Loading…</p>
        <template v-else>
          <p v-if="image.prompt_used" class="text">{{ image.prompt_used }}</p>
          <p v-else class="muted">—</p>
          <template v-if="image.negative_used">
            <h5>Negative</h5>
            <p class="text">{{ image.negative_used }}</p>
          </template>
        </template>
      </section>

      <section>
        <h4>Writer</h4>
        <p v-if="hidden" class="muted">Hidden until graded</p>
        <p v-else-if="loading" class="muted">Loading…</p>
        <template v-else>
          <p>
            {{ writer ?? 'Not recorded' }}
            <span v-if="image.edited" class="tag" title="The writer's prompt was changed before rendering">edited</span>
          </p>
          <p v-if="image.writer_model" class="muted">on {{ image.writer_model }}</p>
        </template>
      </section>

      <section>
        <h4>Image model</h4>
        <p>{{ modelLabel ?? '—' }}</p>
      </section>

      <section>
        <h4>Workflow</h4>
        <p>{{ workflow ?? '—' }}</p>
        <ul v-if="loras.length" class="loras">
          <li v-for="(lora, i) in loras" :key="i">{{ lora }}</li>
        </ul>
      </section>

      <section>
        <h4>Seed · size</h4>
        <p>{{ t2iImageDetails(image) }}</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.t2i-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 12px;
  color: #ddd;
  /* Long unbroken names (a LoRA, a workflow) wrap instead of widening the panel. */
  overflow-wrap: anywhere;
}

section {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

h4,
h5 {
  margin: 0;
  font-weight: 600;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

h4 {
  font-size: 10.5px;
}

h5 {
  margin-top: 4px;
  font-size: 10px;
}

p {
  margin: 0;
  line-height: 1.45;
}

.text {
  color: #eee;
  white-space: pre-wrap;
  user-select: text;
}

.muted {
  color: #888;
}

.note {
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(201, 162, 39, 0.18);
  color: #e6c768;
}

.tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 8px;
  border: 1px solid #555;
  color: #bbb;
  font-size: 10.5px;
}

.loras {
  margin: 2px 0 0;
  padding-left: 16px;
  color: #ccc;
}
</style>
