<script setup lang="ts">
import { SEED_MAX, clampSeed, parseSeedInput, randomSeed, type SeedPolicy } from '../../types/t2i'

// Seed number, a randomise button and the seed policy. Fully controlled
// (v-model:seed / v-model:policy): the input is bound with :value and events
// rather than v-model so a half-typed value never reaches the parent.
const props = withDefaults(
  defineProps<{
    seed: number
    policy: SeedPolicy
    disabled?: boolean
    max?: number
    label?: string
  }>(),
  { disabled: false, max: SEED_MAX, label: 'Seed' },
)

const emit = defineEmits<{
  'update:seed': [value: number]
  'update:policy': [value: SeedPolicy]
}>()

const POLICY_OPTIONS: { value: SeedPolicy; label: string }[] = [
  { value: 'fixed', label: 'Fixed' },
  { value: 'increment', label: 'Increment' },
  { value: 'decrement', label: 'Decrement' },
  { value: 'random', label: 'Randomize' },
]

// While typing, only a complete, in-range integer is reported.
function onInput(e: Event) {
  const value = parseSeedInput((e.target as HTMLInputElement).value, props.max)
  if (value !== null && value !== props.seed) emit('update:seed', value)
}

// On commit (blur / Enter) the box settles: an integer out of range is
// clamped, anything else (blank, "1.5") goes back to the current seed.
function onChange(e: Event) {
  const input = e.target as HTMLInputElement
  const raw = input.value.trim()
  const n = Number(raw)
  if (raw === '' || !Number.isInteger(n)) {
    input.value = String(props.seed)
    return
  }
  const value = clampSeed(n, props.max)
  input.value = String(value)
  if (value !== props.seed) emit('update:seed', value)
}

function onPolicyChange(e: Event) {
  emit('update:policy', (e.target as HTMLSelectElement).value as SeedPolicy)
}
</script>

<template>
  <div class="seed-controls">
    <span v-if="label" class="sc-label">{{ label }}</span>
    <div class="sc-row">
      <input
        class="sc-input"
        type="number"
        min="0"
        step="1"
        :max="max"
        :value="seed"
        :disabled="disabled"
        @input="onInput"
        @change="onChange"
      />
      <button
        type="button"
        class="sc-dice"
        title="Random seed"
        :disabled="disabled"
        @click="emit('update:seed', randomSeed(max))"
      >🎲</button>
      <select
        class="sc-policy"
        title="What happens to the seed after each generated image"
        :disabled="disabled"
        @change="onPolicyChange"
      >
        <option
          v-for="o in POLICY_OPTIONS"
          :key="o.value"
          :value="o.value"
          :selected="o.value === policy"
        >{{ o.label }}</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.seed-controls {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.sc-row {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.sc-input,
.sc-policy {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.sc-input {
  width: 116px;
}

.sc-dice {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 15px;
  padding: 4px;
}

.sc-dice:disabled,
.sc-input:disabled,
.sc-policy:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
