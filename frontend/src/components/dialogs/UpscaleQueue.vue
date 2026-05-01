<script setup lang="ts">
import { onMounted } from 'vue'
import { useUpscaleStore } from '../../stores/upscale'

const emit = defineEmits<{
  close: []
}>()

const upscaleStore = useUpscaleStore()

onMounted(() => {
  upscaleStore.loadQueue()
})

function statusColor(status: string): string {
  switch (status) {
    case 'pending': return 'var(--text-color-secondary)'
    case 'processing': return 'var(--primary-color)'
    case 'complete': return '#22c55e'
    case 'error': return 'var(--danger-color)'
    default: return 'var(--text-color-secondary)'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'pending': return 'Pending'
    case 'processing': return 'Processing'
    case 'complete': return 'Complete'
    case 'error': return 'Error'
    default: return status
  }
}

function fileName(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1]
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card queue-dialog">
      <div class="queue-header">
        <h3>Upscale Queue</h3>
        <span class="queue-status" :class="{ paused: upscaleStore.paused }">
          {{ upscaleStore.paused ? 'Paused' : upscaleStore.tasks.some(t => t.status === 'processing') ? 'Processing' : 'Idle' }}
        </span>
      </div>

      <div v-if="upscaleStore.loading" class="muted">Loading queue...</div>

      <div v-else-if="upscaleStore.tasks.length === 0" class="muted">
        Queue is empty.
      </div>

      <div v-else class="queue-list">
        <div
          v-for="task in upscaleStore.tasks"
          :key="task.task_id"
          class="queue-item"
        >
          <div class="item-info">
            <span class="item-name" :title="task.file_path">
              {{ task.file_name || fileName(task.file_path) }}
            </span>
            <span class="item-status" :style="{ color: statusColor(task.status) }">
              {{ statusLabel(task.status) }}
            </span>
          </div>

          <div v-if="task.status === 'processing'" class="item-progress">
            <div class="progress-bar">
              <div
                class="progress-fill"
                :style="{ width: (task.progress * 100) + '%' }"
              />
            </div>
            <span class="progress-pct">{{ (task.progress * 100).toFixed(0) }}%</span>
          </div>

          <div v-if="task.status === 'error' && task.error" class="item-error">
            {{ task.error }}
          </div>

          <button
            v-if="task.status !== 'processing'"
            class="remove-btn"
            @click="upscaleStore.removeTask(task.task_id)"
            title="Remove"
          >
            &times;
          </button>
        </div>
      </div>

      <div class="queue-actions">
        <button
          class="btn-secondary"
          @click="upscaleStore.paused ? upscaleStore.resumeAll() : upscaleStore.pauseAll()"
        >
          {{ upscaleStore.paused ? 'Resume' : 'Pause' }}
        </button>
        <button
          class="btn-secondary"
          @click="upscaleStore.clearCompleted()"
          :disabled="!upscaleStore.tasks.some(t => t.status === 'complete')"
        >
          Clear Completed
        </button>
        <button class="btn-secondary" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card.queue-dialog {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 24px 28px;
  min-width: 480px;
  max-width: 600px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.queue-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

h3 {
  margin: 0;
  font-size: 18px;
  color: var(--text-color);
}

.queue-status {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 10px;
  background: color-mix(in srgb, #22c55e 15%, transparent);
  color: #22c55e;
}

.queue-status.paused {
  background: color-mix(in srgb, #f59e0b 15%, transparent);
  color: #f59e0b;
}

.muted {
  color: var(--text-color-secondary);
  font-size: 14px;
  padding: 20px 0;
  text-align: center;
}

.queue-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
}

.queue-item {
  position: relative;
  padding: 10px 36px 10px 12px;
  background: var(--surface-ground);
  border-radius: 8px;
}

.item-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 4px;
}

.item-name {
  font-size: 13px;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.item-status {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.item-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.progress-bar {
  flex: 1;
  height: 6px;
  background: var(--surface-border);
  border-radius: 3px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--primary-color);
  border-radius: 3px;
  transition: width 0.3s;
}

.progress-pct {
  font-size: 11px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
  min-width: 32px;
  text-align: right;
}

.item-error {
  font-size: 12px;
  color: var(--danger-color);
  margin-top: 4px;
}

.remove-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 18px;
  cursor: pointer;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
}

.remove-btn:hover {
  color: var(--danger-color);
  background: color-mix(in srgb, var(--danger-color) 10%, transparent);
}

.queue-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.btn-secondary {
  padding: 8px 16px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
}

.btn-secondary:hover { background: var(--surface-hover); }
.btn-secondary:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
