<script setup lang="ts">
// Three-way confirm shown when deleting a subject that beats still cast
// or voice. Whatever the choice, the server strips every non-text
// reference (beat cast lists, dialog speaker ids). 'unlink' keeps the
// scenes/shots/beats and their text; 'content' deletes the referencing
// beats (and any shot/scene left empty), releasing generated images into
// the library; 'purge' does the same and moves the media to the OS trash.
import type { SubjectDeleteMode } from '../../api/storyboard'

defineProps<{
  subjectName: string
  beatCount: number
  imageCount: number
}>()

const emit = defineEmits<{ choose: [mode: SubjectDeleteMode]; cancel: [] }>()
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('cancel')">
    <div class="dialog-card">
      <h3>Delete subject “{{ subjectName }}”?</h3>
      <p class="message">
        {{ beatCount }} beat{{ beatCount === 1 ? '' : 's' }} cast or voice this subject.
        Choose what happens to them. Mentions in scene, shot, and beat text are never
        changed.
      </p>
      <p v-if="imageCount > 0" class="count">
        {{ imageCount }} generated image{{ imageCount === 1 ? '' : 's' }} affected.
      </p>

      <div class="dialog-actions">
        <button class="btn-primary" @click="emit('choose', 'unlink')">
          Remove subject only
        </button>
        <button class="btn-danger" @click="emit('choose', 'content')">
          Remove subject and its beats
        </button>
        <button class="btn-danger" @click="emit('choose', 'purge')">
          Remove subject, beats and media
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
  width: 520px;
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
