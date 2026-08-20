<script setup lang="ts">
withDefaults(defineProps<{ width?: string }>(), { width: '640px' })
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div class="msh-overlay" @click.self="emit('close')">
    <div class="msh-card" :style="{ width }">
      <slot />
      <div class="msh-actions"><slot name="actions" /></div>
    </div>
  </div>
</template>

<style scoped>
.msh-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 900;
}

.msh-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.msh-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

/* Footer buttons per spec: padding 8px 20px, font-size 14px. Provided as
   deep classes so each dialog's buttons share one definition. */
.msh-actions :deep(.msh-btn) {
  padding: 8px 20px;
  font-size: 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  cursor: pointer;
  transition: background 0.15s;
}

.msh-actions :deep(.msh-btn:hover:not(:disabled)) {
  background: var(--surface-hover);
}

.msh-actions :deep(.msh-btn:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}

.msh-actions :deep(.msh-btn--primary) {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: #fff;
}
</style>
