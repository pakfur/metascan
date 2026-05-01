<script setup lang="ts">
import { computed, ref } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import { useFoldersUi } from '../../composables/useFoldersUi'
import { useToast } from '../../composables/useToast'
import type { AnyFolder, FolderScope } from '../../types/folders'
import FolderRow from './FolderRow.vue'

const props = defineProps<{
  kind: 'manual' | 'smart'
  label: string
}>()

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()
const ui = useFoldersUi()
const toast = useToast()
const open = ref(true)

const list = computed<AnyFolder[]>(() =>
  props.kind === 'manual'
    ? foldersStore.manualFolders
    : foldersStore.smartFolders,
)

function onSelect(next: FolderScope) {
  foldersStore.setScope(next)
}

function onAdd() {
  if (props.kind === 'manual') ui.openNewFolder()
  else ui.openSmartEditor('new')
}

function onKebab(el: HTMLElement, id: string) {
  const folder = list.value.find((f) => f.id === id)
  if (!folder) return
  const rect = el.getBoundingClientRect()
  ui.openKebab(rect.left, rect.bottom + 4, folder)
}

function onDrop(id: string, paths: string[]) {
  const f = list.value.find((x) => x.id === id) as
    | Extract<AnyFolder, { kind: 'manual' }>
    | undefined
  if (!f || f.kind !== 'manual') return
  const added = foldersStore.addToManualFolder(id, paths)
  if (added > 0) {
    toast.show(
      `${added} item${added === 1 ? '' : 's'} added to ${f.name}`,
    )
  } else {
    toast.show(`Already in ${f.name}`, 'warn')
  }
}

function onRejectDrop() {
  toast.show(
    "Smart folders are rule-based — items can't be dropped directly.",
    'warn',
  )
}
</script>

<template>
  <div class="fsec">
    <button class="fhead" @click="open = !open">
      <span class="chev" :class="{ closed: !open }">▼</span>
      <span class="label">{{ label }}</span>
      <span class="count">{{ list.length }}</span>
      <span
        class="add"
        :title="kind === 'manual' ? 'New folder' : 'New smart folder'"
        @click.stop="onAdd"
      >
        <i class="pi pi-plus" />
      </span>
    </button>

    <div v-if="open" class="fitems">
      <FolderRow
        v-if="kind === 'manual'"
        kind="library"
        id="library"
        name="Library"
        icon="pi-images"
        :count="mediaStore.allMedia.length"
        :is-library="true"
        :active="foldersStore.scope.kind === 'library'"
        @select="onSelect"
      />
      <FolderRow
        v-for="f in list"
        :key="f.id"
        :kind="kind"
        :id="f.id"
        :name="f.name"
        :icon="f.icon"
        :count="foldersStore.scopeCount(kind, f.id, mediaStore.allMedia)"
        :active="
          foldersStore.scope.kind === kind && foldersStore.scope.id === f.id
        "
        :folder="f"
        @select="onSelect"
        @kebab="onKebab"
        @drop="onDrop"
        @reject-drop="onRejectDrop"
      />
      <div v-if="kind === 'manual' && list.length === 0" class="empty-hint">
        No folders yet. Drop images into a new folder or use the + button.
      </div>
      <button
        v-if="kind === 'smart' && list.length === 0"
        class="new-folder-btn"
        @click="onAdd"
      >
        <i class="pi pi-bolt" />New smart folder
      </button>
    </div>
  </div>
</template>

<style scoped>
.fsec {
  border-bottom: 1px solid var(--surface-border);
  padding: 2px 0 4px;
}

.fhead {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 4px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color);
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  font-family: inherit;
}

.fhead:hover {
  background: var(--surface-hover);
  border-radius: 4px;
}

.fhead .chev {
  font-size: 10px;
  width: 12px;
  color: var(--text-color-secondary);
  transition: transform 0.15s;
}

.fhead .chev.closed {
  transform: rotate(-90deg);
}

.fhead .count {
  color: var(--text-color-secondary);
  font-weight: 400;
}

.fhead .label {
  letter-spacing: 0.02em;
}

.fhead .add {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.fhead .add:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.fhead .add .pi {
  font-size: 11px;
}

.fitems {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 2px;
}

.empty-hint {
  font-size: 11.5px;
  color: var(--text-color-secondary);
  padding: 6px 10px;
  line-height: 1.5;
}

.new-folder-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  font-size: 12.5px;
  background: transparent;
  border: 1px dashed var(--surface-border);
  border-radius: 4px;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-family: inherit;
  width: 100%;
  margin-top: 4px;
}

.new-folder-btn:hover {
  color: var(--primary-color);
  border-color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.new-folder-btn .pi {
  font-size: 11px;
}
</style>
