<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Scene } from '../../types/storyboard'

// `scene` null means create mode: Save POSTs a new scene. Otherwise Save
// PATCHes only the changed fields of the given scene.
const props = defineProps<{ scene: Scene | null }>()
const emit = defineEmits<{ close: [] }>()

const store = useStoryboardStore()

const name = ref(props.scene?.name ?? '')
const subtitle = ref(props.scene?.subtitle ?? '')
const setting = ref(props.scene?.setting ?? '')

const saving = ref(false)
const saveError = ref<string | null>(null)

const isCreate = computed(() => props.scene === null)
const canSave = computed(() => name.value.trim().length > 0 && !saving.value)

async function save(): Promise<void> {
  const trimmedName = name.value.trim()
  if (!trimmedName) return
  saving.value = true
  saveError.value = null
  store.error = null
  try {
    if (props.scene === null) {
      await store.addScene({
        name: trimmedName,
        subtitle: subtitle.value.trim() || null,
        setting: setting.value.trim() || null,
      })
    } else {
      // Changed fields only; empty text clears the nullable columns via an
      // explicit null (the PATCH route honors it — exclude_unset semantics).
      const body: Record<string, unknown> = {}
      if (trimmedName !== props.scene.name) body.name = trimmedName
      const newSubtitle = subtitle.value.trim() || null
      if (newSubtitle !== (props.scene.subtitle ?? null)) body.subtitle = newSubtitle
      const newSetting = setting.value.trim() || null
      if (newSetting !== (props.scene.setting ?? null)) body.setting = newSetting
      if (Object.keys(body).length > 0) {
        await store.patchSceneFields(props.scene.id, body)
      }
    }
    // Both store actions swallow failures into store.error rather than
    // throwing — surface it here and keep the dialog open.
    if (store.error) {
      saveError.value = store.error
      store.error = null
      return
    }
    emit('close')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>{{ isCreate ? 'New scene' : 'Edit scene' }}</h3>

      <div class="field">
        <label for="se-title">Title</label>
        <input id="se-title" v-model="name" type="text" placeholder="Scene title" />
      </div>

      <div class="field">
        <label for="se-subtitle">Subtitle</label>
        <input
          id="se-subtitle"
          v-model="subtitle"
          type="text"
          placeholder="Short tagline shown under the title"
        />
      </div>

      <div class="field">
        <label for="se-setting">Setting</label>
        <textarea
          id="se-setting"
          v-model="setting"
          rows="7"
          placeholder="Common setting and background for every panel in this scene — woven into each synthesized prompt"
        />
      </div>

      <p v-if="saveError" class="error">{{ saveError }}</p>

      <div class="dialog-actions">
        <button class="btn-primary" :disabled="!canSave" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
        <button class="btn-secondary" :disabled="saving" @click="emit('close')">Cancel</button>
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

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 560px;
  max-width: 92vw;
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 14px;
  font-size: 18px;
  color: var(--text-color);
}

.field {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

input[type='text'],
textarea {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

input:focus,
textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

textarea {
  resize: vertical;
  min-height: 120px;
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 4px 0 0;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
}
</style>
