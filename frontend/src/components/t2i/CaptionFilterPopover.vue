<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  activeFilterCount,
  emptyFilter,
  isFilterEmpty,
  type CaptionFilter,
  type CaptionMeta,
} from '../../types/t2i'
import {
  chosenValues,
  clothingGroupOf,
  editableColumns,
  isNumericColumn,
  pickedClothing,
  rangeEndValue,
  searchOptions,
  setRangeEnd,
  toggleChoice,
  toggleClothing,
  type ClothingGroup,
  type NumericColumn,
  type RangeEnd,
} from '../../utils/captionFilter'

// The panel that narrows which caption-file rows Random Caption draws from.
// It only edits a filter: the parent owns the value (v-model), debounces the
// "how many rows match" request and passes the answer back in `count`
// (null while it is unknown). Positioning is the parent's job as well.
//
// Only the columns present in `meta` are shown, so a caption file without,
// say, a Clothing column simply has no clothing field.
const props = defineProps<{
  meta: CaptionMeta | null
  modelValue: CaptionFilter
  count: number | null
  total: number
}>()
const emit = defineEmits<{
  'update:modelValue': [value: CaptionFilter]
  close: []
}>()

const columns = computed(() => editableColumns(props.meta))
const active = computed(() => activeFilterCount(props.modelValue))
const empty = computed(() => isFilterEmpty(props.modelValue))
const picked = computed(() => pickedClothing(props.modelValue))

// One search box narrows the clothing list (there is a single tags column).
const clothingQuery = ref('')
const visibleClothing = computed(() => {
  const column = columns.value.find((c) => c.type === 'tags')
  return column && column.type === 'tags' ? searchOptions(column.options, clothingQuery.value) : []
})

const fmt = (n: number): string => n.toLocaleString()

function onChoice(key: string, value: string) {
  emit('update:modelValue', toggleChoice(props.modelValue, key, value))
}

// Range boxes commit on change (blur / Enter / the spinner), not per
// keystroke: a controlled number box that re-rendered mid-word would eat the
// decimal point of "0.".
function onRange(column: NumericColumn, end: RangeEnd, e: Event) {
  const input = e.target as HTMLInputElement
  const next = setRangeEnd(props.modelValue, column, end, input.value)
  emit('update:modelValue', next)
  // Settle the box on what the filter holds now: clamped, dropped at the
  // column's own extreme, or unchanged when the text was not a number.
  input.value = String(rangeEndValue(next, column.key, end) ?? '')
}

function onClothing(group: ClothingGroup, item: string) {
  emit('update:modelValue', toggleClothing(props.modelValue, group, item))
}

function clear() {
  emit('update:modelValue', emptyFilter())
}
</script>

<template>
  <div class="cfp" role="dialog" aria-label="Caption filter" @keydown.esc.stop="emit('close')">
    <header class="cfp-head">
      <strong class="cfp-title">Caption filter</strong>
      <span
        v-if="active > 0"
        class="cfp-badge"
        :title="`${active} filter${active === 1 ? '' : 's'} active`"
      >{{ active }}</span>
      <button type="button" class="cfp-x" title="Close" aria-label="Close filter" @click="emit('close')">✕</button>
    </header>

    <div class="cfp-body">
      <p v-if="!meta" class="cfp-note">Loading filters…</p>
      <p v-else-if="!columns.length" class="cfp-note">
        This caption file has no columns to filter on.
      </p>

      <template v-for="col in columns" :key="col.key">
        <!-- min - max: the scores (decimals) and the male / female counts (integers) -->
        <div v-if="isNumericColumn(col)" class="cfp-field cfp-range" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <input
            class="cfp-num"
            type="number"
            :min="col.min"
            :max="col.max"
            :step="col.type === 'range' ? col.step : 1"
            :value="rangeEndValue(modelValue, col.key, 'min') ?? ''"
            :placeholder="String(col.min)"
            :aria-label="`${col.label} minimum`"
            @change="onRange(col, 'min', $event)"
          />
          <span class="cfp-dash" aria-hidden="true">–</span>
          <input
            class="cfp-num"
            type="number"
            :min="col.min"
            :max="col.max"
            :step="col.type === 'range' ? col.step : 1"
            :value="rangeEndValue(modelValue, col.key, 'max') ?? ''"
            :placeholder="String(col.max)"
            :aria-label="`${col.label} maximum`"
            @change="onRange(col, 'max', $event)"
          />
        </div>

        <!-- has any / has none: one searchable list, two tick columns -->
        <div v-else-if="col.type === 'tags'" class="cfp-field" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <div v-if="picked.length" class="cfp-chips">
            <button
              v-for="p in picked"
              :key="`${p.group}:${p.value}`"
              type="button"
              class="cfp-chip"
              :class="p.group"
              :title="(p.group === 'any' ? 'Must have this item' : 'Must not have this item') + ' - click to remove'"
              @click="onClothing(p.group, p.value)"
            >{{ p.group === 'any' ? '+' : '−' }} {{ p.value }} ✕</button>
          </div>
          <input
            v-model="clothingQuery"
            class="cfp-search"
            type="search"
            placeholder="Search clothing…"
            aria-label="Search clothing"
          />
          <div class="cfp-tags-head" aria-hidden="true">
            <span title="Captions whose clothing includes at least one ticked item">Has any</span>
            <span title="Captions whose clothing includes none of the ticked items">Has none</span>
          </div>
          <div class="cfp-list">
            <div v-for="o in visibleClothing" :key="o.value" class="cfp-row">
              <span class="cfp-opt">{{ o.value }} <span class="cfp-n">{{ fmt(o.count) }}</span></span>
              <input
                type="checkbox"
                :checked="clothingGroupOf(modelValue, o.value) === 'any'"
                :aria-label="`${o.value}: has any`"
                @change="onClothing('any', o.value)"
              />
              <input
                type="checkbox"
                :checked="clothingGroupOf(modelValue, o.value) === 'none'"
                :aria-label="`${o.value}: has none`"
                @change="onClothing('none', o.value)"
              />
            </div>
            <p v-if="!visibleClothing.length" class="cfp-note">
              Nothing matches “{{ clothingQuery.trim() }}”.
            </p>
          </div>
        </div>

        <!-- pick any of: nudity, aspect ratio -->
        <div v-else class="cfp-field" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <div class="cfp-checks">
            <label v-for="o in col.options" :key="o.value" class="cfp-check">
              <input
                type="checkbox"
                :checked="chosenValues(modelValue, col.key).includes(o.value)"
                :aria-label="`${col.label}: ${o.value}`"
                @change="onChoice(col.key, o.value)"
              />
              <span class="cfp-opt">{{ o.value }}</span>
              <span class="cfp-n">{{ fmt(o.count) }}</span>
            </label>
          </div>
        </div>
      </template>
    </div>

    <footer class="cfp-foot">
      <span class="cfp-count" role="status" aria-live="polite">
        <strong>{{ count === null ? '...' : fmt(count) }}</strong> captions match
        <span v-if="total > 0" class="cfp-total">of {{ fmt(total) }}</span>
      </span>
      <button type="button" class="cfp-clear" :disabled="empty" @click="clear">Clear</button>
    </footer>
  </div>
</template>

<style scoped>
.cfp {
  display: flex;
  flex-direction: column;
  width: 360px;
  max-width: 92vw;
  max-height: min(72vh, 560px);
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
  font-size: 13px;
}

.cfp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--surface-border);
}

.cfp-title {
  font-size: 13px;
}

.cfp-badge {
  min-width: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--primary-color);
  color: var(--primary-color-text, #fff);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.cfp-x {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 4px;
}

.cfp-x:hover {
  color: var(--text-color);
}

.cfp-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cfp-note {
  margin: 0;
  color: var(--text-color-secondary);
  font-size: 12px;
}

.cfp-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cfp-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  color: var(--text-color-secondary);
}

.cfp-range {
  flex-direction: row;
  align-items: center;
  gap: 6px;
}

.cfp-range .cfp-label {
  flex: 0 0 148px;
}

.cfp-num,
.cfp-search {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 4px 6px;
  box-sizing: border-box;
}

.cfp-num {
  width: 72px;
}

.cfp-search {
  width: 100%;
}

.cfp-num:focus,
.cfp-search:focus {
  outline: none;
  border-color: var(--primary-color);
}

.cfp-dash {
  color: var(--text-color-secondary);
}

.cfp-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.cfp-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}

.cfp-n {
  color: var(--text-color-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.cfp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cfp-chip {
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  padding: 1px 8px;
  cursor: pointer;
}

.cfp-chip.any {
  border-color: var(--primary-color);
}

.cfp-chip.none {
  border-color: #c33;
  color: #c33;
}

.cfp-tags-head {
  display: grid;
  grid-template-columns: 1fr 56px 56px;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.cfp-tags-head span {
  text-align: center;
}

.cfp-tags-head span:first-child {
  grid-column: 2;
}

/* The list is the only part that can be long (over a hundred items), so it
   scrolls on its own and the rest of the panel stays in view. */
.cfp-list {
  max-height: 148px;
  overflow-y: auto;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
}

.cfp-row {
  display: grid;
  grid-template-columns: 1fr 56px 56px;
  align-items: center;
  padding: 2px 8px;
}

.cfp-row input {
  justify-self: center;
}

.cfp-row:hover {
  background: var(--surface-hover);
}

.cfp-list .cfp-note {
  padding: 6px 8px;
}

.cfp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px 10px;
  border-top: 1px solid var(--surface-border);
}

.cfp-total {
  color: var(--text-color-secondary);
  font-size: 11px;
}

.cfp-clear {
  padding: 4px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.cfp-clear:disabled {
  opacity: 0.55;
  cursor: default;
}
</style>
