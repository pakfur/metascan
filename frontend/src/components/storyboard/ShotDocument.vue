<script setup lang="ts">
import { computed, ref, watch, type ComponentPublicInstance } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import ShotHeader from './ShotHeader.vue'
import BeatCard from './BeatCard.vue'

const store = useStoryboardStore()
const panel = computed(() => store.selectedPanel)
const scene = computed(() => store.selectedScene)
const shotIndex = computed(() => {
  const s = scene.value
  const p = panel.value
  if (!s || !p) return 0
  return Math.max(0, s.panels.findIndex((x) => x.id === p.id))
})

const scroller = ref<HTMLElement | null>(null)
const cardEls = new Map<number, HTMLElement>()

function registerCard(beatId: number, el: Element | ComponentPublicInstance | null): void {
  if (el && '$el' in (el as ComponentPublicInstance)) {
    cardEls.set(beatId, (el as ComponentPublicInstance).$el as HTMLElement)
  } else if (el instanceof HTMLElement) {
    cardEls.set(beatId, el)
  } else {
    cardEls.delete(beatId)
  }
}

// Selecting a shot from the rail resets the document scroll to 0 (spec
// "Interactions"): watch the panel id rather than coupling to the rail.
watch(
  () => store.selectedPanelId,
  () => {
    if (scroller.value) scroller.value.scrollTop = 0
  },
)

// Pacing-segment click: select the beat and bring its card up. NOT
// scrollIntoView (spec) -- the container owns the offset maths.
function selectBeat(beatId: number): void {
  store.selectedBeatId = beatId
  const el = cardEls.get(beatId)
  const box = scroller.value
  if (el && box) box.scrollTop = Math.max(0, el.offsetTop - box.offsetTop - 12)
}

// Position-based sort_order swap, ported verbatim from BeatsEditor.vue:69-78
// -- stored sort_order values can collide on hand-added beats, so tie cases
// swap by array position instead.
async function moveBeat(index: number, dir: -1 | 1): Promise<void> {
  const beats = panel.value?.beats ?? []
  const otherIndex = index + dir
  if (otherIndex < 0 || otherIndex >= beats.length) return
  const a = beats[index]
  const b = beats[otherIndex]
  const tie = a.sort_order === b.sort_order
  await store.patchBeatFields(a.id, { sort_order: tie ? otherIndex : b.sort_order })
  await store.patchBeatFields(b.id, { sort_order: tie ? index : a.sort_order })
}

// Ported from BeatsEditor.vue:49-60 -- addBeat's refresh() lands the new
// beat last; select it (spec: "Adding selects the new beat").
async function addBeat(): Promise<void> {
  const p = panel.value
  if (!p) return
  const panelId = p.id
  await store.addBeat(panelId, 'new beat')
  const beats = store.panelById(panelId)?.beats ?? []
  const last = beats[beats.length - 1]
  if (last) store.selectedBeatId = last.id
}
</script>

<template>
  <main ref="scroller" class="sd-scroll">
    <div v-if="panel && scene" class="sd-col">
      <ShotHeader :panel="panel" :scene="scene" :index="shotIndex" @select-beat="selectBeat" />
      <BeatCard
        v-for="(b, i) in panel.beats"
        :key="b.id"
        :ref="(el) => registerCard(b.id, el)"
        :beat="b"
        :index="i"
        :subjects="store.tree?.subjects ?? []"
        :selected="b.id === store.selectedBeatId"
        :can-up="i > 0"
        :can-down="i < panel.beats.length - 1"
        @select="store.selectedBeatId = b.id"
        @move="moveBeat(i, $event)"
      />
      <button type="button" class="sd-add" @click="addBeat">+ Beat</button>
    </div>
    <div v-else-if="store.tree && store.tree.scenes.length === 0" class="sd-empty">
      No scenes yet — use <b>Compose</b> to build the board from a premise, or <b>Import text</b>
      to parse a script.
    </div>
    <p v-else class="sd-hint">Select a shot from the outline.</p>
  </main>
</template>

<style scoped>
.sd-scroll {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 40px;
}

.sd-col {
  max-width: 1040px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sd-add {
  align-self: flex-start;
  padding: 6px 14px;
  font-size: 13px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
}

.sd-add:hover {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.sd-empty {
  border: 1px dashed var(--surface-border);
  border-radius: 10px;
  padding: 48px 16px;
  text-align: center;
  font-size: 14px;
  color: var(--text-color-secondary);
}

.sd-hint {
  font-size: 14px;
  color: var(--text-color-secondary);
}
</style>
