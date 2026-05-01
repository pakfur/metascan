<script setup lang="ts">
import { nextTick, ref, onMounted } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useFoldersUi } from '../../composables/useFoldersUi'
import { useToast } from '../../composables/useToast'

// Shared between "new folder" (creates a folder, optionally seeded with
// items) and "rename folder" (when foldersUi.renameFolderId is set).
const foldersStore = useFoldersStore()
const ui = useFoldersUi()
const toast = useToast()

const input = ref<HTMLInputElement | null>(null)
const name = ref('')

// Rename path: prefill with current name; New path: blank.
const renameTarget = (() => {
  if (!ui.renameFolderId.value) return null
  const m = foldersStore.manualFolders.find(
    (f) => f.id === ui.renameFolderId.value,
  )
  if (m) return m
  const s = foldersStore.smartFolders.find(
    (f) => f.id === ui.renameFolderId.value,
  )
  return s ?? null
})()

if (renameTarget) name.value = renameTarget.name

const title = renameTarget ? 'Rename folder' : 'New folder'
const ok = renameTarget ? 'Rename' : 'Create'

onMounted(async () => {
  await nextTick()
  input.value?.focus()
  input.value?.select()
})

function close() {
  if (renameTarget) ui.closeRename()
  else ui.closeNewFolder()
}

function submit() {
  const value = name.value.trim()
  if (!value) return
  if (renameTarget) {
    foldersStore.renameFolder(renameTarget.id, value)
    toast.show('Renamed')
  } else {
    const f = foldersStore.createManualFolder(value, ui.newFolderInitialItems.value)
    foldersStore.setScope({ kind: 'manual', id: f.id })
    const seeded = ui.newFolderInitialItems.value.length
    toast.show(
      seeded > 0
        ? `Added ${seeded} to "${value}"`
        : `Folder "${value}" created`,
    )
  }
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') submit()
  if (e.key === 'Escape') close()
}

function onBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) close()
}
</script>

<template>
  <div class="dialog-overlay" @click="onBackdrop">
    <div class="dialog" style="width: 440px">
      <div class="dhead">
        <div class="dt">
          <i class="pi pi-folder" />
          {{ title }}
        </div>
        <button class="dclose" @click="close" aria-label="Close">
          <i class="pi pi-times" />
        </button>
      </div>
      <div class="dbody">
        <div class="dfield">
          <label class="dlabel">Folder name</label>
          <input
            ref="input"
            class="dinput"
            v-model="name"
            placeholder="e.g. Client work"
            @keydown="onKeydown"
          />
        </div>
      </div>
      <div class="dfoot">
        <span class="preview-count" />
        <div class="actions">
          <button class="btn btn-secondary" @click="close">Cancel</button>
          <button
            class="btn btn-primary"
            :disabled="!name.trim()"
            @click="submit"
          >
            {{ ok }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog {
  max-height: 86vh;
  overflow: hidden;
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}

.dhead {
  padding: 12px 18px;
  border-bottom: 1px solid var(--surface-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--surface-section);
}

.dt {
  font-size: 15px;
  font-weight: 700;
  display: flex;
  gap: 8px;
  align-items: center;
}

.dt .pi {
  color: var(--primary-color);
}

.dclose {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color-secondary);
  padding: 4px;
  border-radius: 4px;
}

.dclose:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.dbody {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dfoot {
  padding: 10px 16px;
  border-top: 1px solid var(--surface-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  background: var(--surface-section);
}

.actions {
  display: flex;
  gap: 8px;
}

.dfield {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dlabel {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.dinput {
  padding: 8px 10px;
  font-size: 13px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-family: inherit;
  width: 100%;
  outline: none;
}

.dinput:focus {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  font-family: inherit;
}

.btn-primary {
  background: var(--primary-color);
  color: #fff;
}

.btn-primary:hover:not(:disabled) {
  filter: brightness(0.95);
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  background: var(--surface-card);
  color: var(--text-color);
  border-color: var(--surface-border);
}

.btn-secondary:hover {
  background: var(--surface-hover);
}
</style>
