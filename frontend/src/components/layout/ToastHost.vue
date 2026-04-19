<script setup lang="ts">
import { computed } from 'vue'
import { useToast } from '../../composables/useToast'

const toast = useToast()

const iconClass = computed(() => {
  const kind = toast.state.value?.kind ?? 'success'
  if (kind === 'warn') return 'pi pi-exclamation-circle'
  if (kind === 'info') return 'pi pi-info-circle'
  return 'pi pi-check'
})
</script>

<template>
  <Transition name="toast">
    <div v-if="toast.state.value" class="toast" :class="toast.state.value.kind">
      <i :class="iconClass" />
      <span>{{ toast.state.value.message }}</span>
    </div>
  </Transition>
</template>

<style scoped>
.toast {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: #1e293b;
  color: #f1f5f9;
  font-size: 13px;
  padding: 10px 16px;
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  z-index: 1800;
  display: flex;
  align-items: center;
  gap: 10px;
  pointer-events: none;
}

.toast.success .pi {
  color: #22c55e;
}

.toast.warn .pi {
  color: #eab308;
}

.toast.info .pi {
  color: #60a5fa;
}

.toast-enter-active,
.toast-leave-active {
  transition:
    opacity 0.2s,
    transform 0.2s;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}
</style>
