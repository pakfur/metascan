<script setup lang="ts">
import { useBackgroundStore } from '../../stores/background'
import ActivityPanel from './ActivityPanel.vue'

const store = useBackgroundStore()
</script>

<template>
  <div class="grid" data-testid="activity-grid">
    <ActivityPanel
      v-for="p in store.processes"
      :key="p.id"
      :process="p"
      :all="store.processes"
      @command="(a) => store.command(p.id, a)"
    />
    <p v-if="!store.processes.length" class="empty">No background processes reported.</p>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.empty { grid-column: 1 / -1; color: var(--text-color-secondary); font-size: 12px; }
@media (max-width: 767px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
