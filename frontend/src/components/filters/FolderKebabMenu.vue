<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useFoldersUi } from '../../composables/useFoldersUi'
import { useToast } from '../../composables/useToast'

const foldersStore = useFoldersStore()
const ui = useFoldersUi()
const toast = useToast()

// A single, anchored menu — coordinates come from useFoldersUi.kebabMenu.
// We close on any outside click or Escape, matching the existing
// ThumbnailGrid context menu behavior.
function onDocClick() {
  ui.closeKebab()
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') ui.closeKebab()
}

onMounted(() => {
  // Defer attaching so the click that opened the menu doesn't close it.
  setTimeout(() => document.addEventListener('click', onDocClick, { once: true }), 0)
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('keydown', onKey)
})

function onOpen() {
  const info = ui.kebabMenu.value
  if (!info) return
  foldersStore.setScope({ kind: info.folder.kind, id: info.folder.id })
  ui.closeKebab()
}

function onRename() {
  const info = ui.kebabMenu.value
  if (!info) return
  ui.openRename(info.folder.id)
  ui.closeKebab()
}

function onEditRules() {
  const info = ui.kebabMenu.value
  if (!info || info.folder.kind !== 'smart') return
  ui.openSmartEditor(info.folder.id)
  ui.closeKebab()
}

function onDuplicate() {
  const info = ui.kebabMenu.value
  if (!info || info.folder.kind !== 'smart') return
  const copy = foldersStore.duplicateSmartFolder(info.folder.id)
  ui.closeKebab()
  if (copy) toast.show('Duplicated')
}

function onConvertToSmart() {
  const info = ui.kebabMenu.value
  if (!info || info.folder.kind !== 'manual') return
  ui.closeKebab()
  // Seed a minimal rule so the editor is usable immediately. The user
  // edits the real rules in the dialog.
  ui.openSmartEditor('new', {
    match: 'all',
    conditions: [{ field: 'favorite', op: 'is', value: true }],
  })
}

function onDelete() {
  const info = ui.kebabMenu.value
  if (!info) return
  const name = info.folder.name
  ui.closeKebab()
  if (!confirm(`Delete "${name}"? The images themselves are not deleted.`)) return
  foldersStore.deleteFolder(info.folder.id)
  toast.show('Deleted')
}
</script>

<template>
  <div
    v-if="ui.kebabMenu.value"
    class="ctx"
    :style="{
      left: ui.kebabMenu.value.x + 'px',
      top: ui.kebabMenu.value.y + 'px',
    }"
    @click.stop
  >
    <button class="ctx-item" @click="onOpen">
      <i class="pi pi-folder-open" />
      <span>Open</span>
    </button>
    <button class="ctx-item" @click="onRename">
      <i class="pi pi-pencil" />
      <span>Rename</span>
    </button>
    <template v-if="ui.kebabMenu.value.folder.kind === 'smart'">
      <button class="ctx-item" @click="onEditRules">
        <i class="pi pi-sliders-h" />
        <span>Edit rules…</span>
      </button>
      <button class="ctx-item" @click="onDuplicate">
        <i class="pi pi-copy" />
        <span>Duplicate</span>
      </button>
    </template>
    <template v-else>
      <button class="ctx-item" @click="onConvertToSmart">
        <i class="pi pi-bolt" />
        <span>Convert to smart folder…</span>
      </button>
    </template>
    <hr />
    <button class="ctx-item danger" @click="onDelete">
      <i class="pi pi-trash" />
      <span>
        {{
          ui.kebabMenu.value.folder.kind === 'smart'
            ? 'Delete smart folder'
            : 'Delete folder'
        }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.ctx {
  position: fixed;
  z-index: 1500;
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  padding: 4px;
  min-width: 240px;
  font-size: 13px;
}

.ctx-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
  font-family: inherit;
}

.ctx-item:hover {
  background: var(--surface-hover);
}

.ctx-item .pi {
  font-size: 13px;
  color: var(--text-color-secondary);
  width: 16px;
}

.ctx-item.danger {
  color: var(--danger-color);
}

.ctx-item.danger .pi {
  color: var(--danger-color);
}

hr {
  border: none;
  border-top: 1px solid var(--surface-border);
  margin: 4px 2px;
}
</style>
