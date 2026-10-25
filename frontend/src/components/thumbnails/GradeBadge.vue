<script setup lang="ts">
import { computed } from 'vue'
import type { Grade } from '../../types/judgments'

// Read-only ✓ / ✗ corner badge for grid tiles (library, T2I, I2V): grades
// are visible in grids, but grading itself happens in the viewer (P / X).
// Not a button: a click falls through to the tile. The tile positions it.
const props = defineProps<{ grade: Grade }>()

const label = computed(() => (props.grade === 'pass' ? '✓ Pass' : '✗ Fail'))
</script>

<template>
  <span
    class="grade-badge"
    :class="grade === 'pass' ? 'grade-pass' : 'grade-fail'"
    role="img"
    :aria-label="label"
    :title="label"
    :data-grade="grade"
  >{{ grade === 'pass' ? '✓' : '✗' }}</span>
</template>

<style scoped>
.grade-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9px;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  /* Readable on light and dark thumbnails alike. */
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
}

/* The rating rail's ✓ / ✗ colours, a step brighter for small sizes. */
.grade-pass {
  background: #2f855a;
}

.grade-fail {
  background: #a33a3a;
}
</style>
