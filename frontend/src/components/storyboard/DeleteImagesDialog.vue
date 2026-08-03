<script setup lang="ts">
// Three-way confirm shown before any delete that destroys panels (panel,
// scene, or whole storyboard): the caller decides what happens to the
// generated images. 'purge' → delete with purge_images=true (media rows
// removed, files moved to OS trash); 'keep' → delete with the default
// release-into-library semantics (images unhidden in the main grid);
// 'cancel' → nothing happens.
defineProps<{
  title: string
  message: string
  imageCount?: number
}>()

const emit = defineEmits<{ purge: []; keep: []; cancel: [] }>()
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('cancel')">
    <div class="dialog-card">
      <h3>{{ title }}</h3>
      <p class="message">{{ message }}</p>
      <p v-if="imageCount !== undefined" class="count">
        {{ imageCount }} generated image{{ imageCount === 1 ? '' : 's' }} affected.
      </p>

      <div class="dialog-actions">
        <button class="btn-danger" @click="emit('purge')">
          Delete images permanently
        </button>
        <button class="btn-primary" @click="emit('keep')">
          Keep images in library
        </button>
        <button class="btn-secondary" @click="emit('cancel')">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 950;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 480px;
  max-width: 92vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 10px;
  font-size: 18px;
  color: var(--text-color);
}

.message {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--text-color);
  line-height: 1.5;
}

.count {
  margin: 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.dialog-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.btn-danger {
  padding: 8px 16px;
  background: none;
  border: 1px solid var(--danger-color, #e53e3e);
  border-radius: 6px;
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.btn-danger:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
}

.btn-primary {
  padding: 8px 16px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover {
  opacity: 0.9;
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
.btn-secondary:hover {
  background: var(--surface-hover);
}
</style>
