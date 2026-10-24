<script setup lang="ts">
import { computed } from 'vue'
import { REASONS } from '../../types/judgments'
import type { CategoryRef, JudgmentState, Reason } from '../../types/judgments'

// Display only: the media viewer owns every API call and key binding and
// passes the current item's judgment down. The default slot holds the
// category picker while it is open.
const props = defineProps<{
  judgment: JudgmentState | null
  hint: string | null
  sticky: CategoryRef | null
  // Learned taste score for the current image, null when there is none
  // (no model, stale model, unscored item, or a video).
  score: number | null
}>()

// ★ favorite and the grade are independent: both can show at once.
const favorite = computed(() => props.judgment?.favorite ?? false)
const grade = computed(() => props.judgment?.grade ?? null)

const reasonsHeader = computed(() => {
  if (grade.value === 'pass') return 'worked on…'
  if (grade.value === 'fail') return 'missed on…'
  return 'reasons (X first)'
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

    <span v-if="favorite" class="marker fav">★ Favorite</span>
    <span v-if="grade === 'pass'" class="marker grade-pass">✓ Pass</span>
    <span v-else-if="grade === 'fail'" class="marker grade-fail">✗ Fail</span>
    <span v-else class="marker none">No grade</span>

    <div v-if="score !== null" class="taste-score" title="Learned taste score (0–1)">
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

.marker.fav {
  background: #c9a227;
  color: #111;
}

.marker.grade-pass {
  background: #2f6f4f;
  color: #fff;
}

.marker.grade-fail {
  background: #6f2f2f;
  color: #fff;
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
