<script setup lang="ts">
import { computed } from 'vue'
import { REASONS } from '../../types/judgments'
import type { CategoryRef, Grade, JudgmentState, Reason } from '../../types/judgments'

// The media viewer owns every API call and key binding and passes the
// current item's judgment down. The rail's ★ / ✓ / ✗ buttons are the mouse
// twins of F / P / X: they only emit, and the viewer runs the same toggle
// as the key. The default slot holds the category picker while it is open.
const props = defineProps<{
  judgment: JudgmentState | null
  // What the item's own record says, shown until its judgment arrives (the
  // header star does the same): its ★, and its grade where it carries one.
  fallbackFavorite?: boolean
  fallbackGrade?: Grade | null
  hint: string | null
  sticky: CategoryRef | null
  // Learned taste score for the current image, null when there is none
  // (no model, stale model, unscored item, or a video).
  score: number | null
}>()

const emit = defineEmits<{
  favorite: []
  grade: [grade: Grade]
}>()

// ★ favorite and the grade are independent: both can be on at once.
const favorite = computed(() =>
  props.judgment ? props.judgment.favorite : (props.fallbackFavorite ?? false),
)
const grade = computed(() =>
  props.judgment ? props.judgment.grade : (props.fallbackGrade ?? null),
)

const reasonsHeader = computed(() => {
  if (grade.value === 'pass') return 'worked on…'
  if (grade.value === 'fail') return 'missed on…'
  return 'reasons (P or X first)'
})

function isOn(reason: Reason): boolean {
  return props.judgment?.reasons.includes(reason) ?? false
}
</script>

<template>
  <aside class="rating-rail">
    <div v-if="sticky" class="sticky" title="Sticky category: added on every grade (C, Shift+Enter)">
      📌 {{ sticky.name }}
    </div>

    <!-- mousedown.prevent + tabindex -1: a click must not move focus off
         the viewer, or the next key press would land on the button; the
         keys F / P / X are the keyboard path. -->
    <button
      type="button"
      class="marker toggle fav"
      :class="{ on: favorite }"
      :aria-pressed="favorite"
      title="★ Favorite (F): to look at again. Never trains anything."
      tabindex="-1"
      @mousedown.prevent
      @click="emit('favorite')"
    >
      <span class="k">F</span>★ Favorite
    </button>
    <div class="grade-group" role="group" aria-label="Grade">
      <button
        type="button"
        class="marker toggle grade-pass"
        :class="{ on: grade === 'pass' }"
        :aria-pressed="grade === 'pass'"
        title="✓ Pass (P): the prompt did what you wanted. Press again to clear."
        tabindex="-1"
        @mousedown.prevent
        @click="emit('grade', 'pass')"
      >
        <span class="k">P</span>✓ Pass
      </button>
      <button
        type="button"
        class="marker toggle grade-fail"
        :class="{ on: grade === 'fail' }"
        :aria-pressed="grade === 'fail'"
        title="✗ Fail (X): it didn't. Press again to clear."
        tabindex="-1"
        @mousedown.prevent
        @click="emit('grade', 'fail')"
      >
        <span class="k">X</span>✗ Fail
      </button>
      <span v-if="!grade" class="marker none">No grade</span>
    </div>

    <div v-if="score !== null" class="taste-score" title="Taste score: the chance you'd pass this (0–1)">
      taste {{ score.toFixed(2) }}
    </div>
    <div v-if="judgment?.batch" class="batch-pos" title="Position in its T2I generation batch">
      batch {{ judgment.batch.index }} of {{ judgment.batch.size }}
    </div>

    <div class="reasons-header">{{ reasonsHeader }}</div>
    <span
      v-for="(reason, i) in REASONS"
      :key="reason"
      class="chip"
      :class="{
        on: isOn(reason) && grade === 'pass',
        neg: isOn(reason) && grade === 'fail',
      }"
    >
      <span class="k">{{ i + 1 }}</span>{{ reason }}
    </span>

    <div v-if="judgment && judgment.categories.length" class="categories">
      <span v-for="c in judgment.categories" :key="c.id" class="cat-chip">{{ c.name }}</span>
    </div>

    <div v-if="hint" class="hint">{{ hint }}</div>

    <slot />

    <div class="rail-footer">
      <span v-if="judgment?.seen_at" class="seen">● seen</span>
      <span class="footer-keys"><span class="k">C</span>categories</span>
    </div>
  </aside>
</template>

<style scoped>
.rating-rail {
  width: 170px;
  flex-shrink: 0;
  background: rgba(0, 0, 0, 0.78);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #ddd;
  font-size: 12px;
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.sticky {
  color: #fbbf24;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.marker {
  align-self: flex-start;
  padding: 3px 10px;
  border-radius: 12px;
  font-weight: 600;
}

.grade-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* ★ / ✓ / ✗: outlined while off, filled while on. */
.marker.toggle {
  align-self: stretch;
  text-align: left;
  font: inherit;
  font-weight: 600;
  background: transparent;
  border: 1px solid #555;
  color: #bbb;
  cursor: pointer;
}

.marker.toggle:hover {
  border-color: #888;
  color: #eee;
}

.marker.toggle:focus-visible {
  outline: 2px solid #60a5fa;
  outline-offset: 1px;
}

.marker.fav.on {
  background: #c9a227;
  border-color: #c9a227;
  color: #111;
}

.marker.grade-pass.on {
  background: #2f6f4f;
  border-color: #3c9;
  color: #fff;
}

.marker.grade-fail.on {
  background: #6f2f2f;
  border-color: #c55;
  color: #fff;
}

.marker.fav.on .k {
  border-color: #6b5a1a;
  color: #111;
}

.marker.none {
  border: 1px dashed #555;
  color: #888;
  font-weight: 400;
}

.taste-score,
.batch-pos {
  color: #aab;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.reasons-header {
  color: #999;
  margin-top: 4px;
}

.chip {
  display: block;
  border: 1px solid #555;
  border-radius: 12px;
  padding: 2px 8px;
  color: #bbb;
}

.chip.on {
  background: #2f6f4f;
  border-color: #3c9;
  color: #fff;
}

.chip.neg {
  background: #6f2f2f;
  border-color: #c55;
  color: #fff;
}

.k {
  display: inline-block;
  border: 1px solid #666;
  border-radius: 4px;
  padding: 0 5px;
  font-family: monospace;
  font-size: 11px;
  color: #eee;
  margin-right: 5px;
}

.categories {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.cat-chip {
  border: 1px solid #4a6a8a;
  background: #1f3347;
  color: #cde;
  border-radius: 10px;
  padding: 1px 7px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hint {
  color: #fbbf24;
  font-size: 12px;
}

.rail-footer {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #888;
  font-size: 11px;
  padding-top: 6px;
}

.seen {
  color: #9a9;
}

.footer-keys {
  margin-left: auto;
}
</style>
