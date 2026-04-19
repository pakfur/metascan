<script setup lang="ts">
import { ref } from 'vue'
import type { AnyFolder, FolderScope } from '../../types/folders'

const props = defineProps<{
  kind: 'library' | 'manual' | 'smart'
  id: string
  name: string
  icon: string
  count: number
  isLibrary?: boolean
  active: boolean
  // Only set for real folders; library has no kebab.
  folder?: AnyFolder | null
}>()

const emit = defineEmits<{
  select: [scope: FolderScope]
  kebab: [el: HTMLElement, id: string]
  drop: [id: string, paths: string[]]
  rejectDrop: []
}>()

const dragOver = ref(false)

function onClick() {
  if (props.kind === 'library') emit('select', { kind: 'library' })
  else if (props.kind === 'manual') emit('select', { kind: 'manual', id: props.id })
  else if (props.kind === 'smart') emit('select', { kind: 'smart', id: props.id })
}

function onKebabClick(e: MouseEvent) {
  e.stopPropagation()
  emit('kebab', e.currentTarget as HTMLElement, props.id)
}

function onDragOver(e: DragEvent) {
  if (props.kind === 'smart') {
    // Hint to the browser: refuse this drop.
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'none'
    e.preventDefault()
    return
  }
  if (props.kind === 'manual') {
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    dragOver.value = true
  }
}

function onDragLeave() {
  dragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  if (props.kind !== 'manual' && props.kind !== 'smart') return
  const raw = e.dataTransfer?.getData('application/x-metascan-paths') ?? ''
  let paths: string[] = []
  try {
    paths = JSON.parse(raw)
  } catch {
    paths = []
  }
  if (props.kind === 'smart') {
    emit('rejectDrop')
    return
  }
  if (paths.length) emit('drop', props.id, paths)
}
</script>

<template>
  <div
    class="folder-row"
    :class="{ active, 'drag-over': dragOver }"
    :data-kind="kind"
    @click="onClick"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <i class="pi" :class="icon" />
    <span class="name" :title="name">{{ name }}</span>
    <span class="cnt">{{ count }}</span>
    <button
      v-if="!isLibrary"
      class="kebab"
      :title="`Actions for ${name}`"
      @click="onKebabClick"
    >
      <i class="pi pi-ellipsis-h" />
    </button>
  </div>
</template>

<style scoped>
.folder-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text-color);
  user-select: none;
  position: relative;
}

.folder-row:hover {
  background: var(--surface-hover);
}

.folder-row.active {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.folder-row.drag-over {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  outline: 2px dashed var(--primary-color);
  outline-offset: -2px;
}

.folder-row .pi {
  font-size: 13px;
  color: var(--text-color-secondary);
  flex-shrink: 0;
}

.folder-row.active .pi {
  color: var(--primary-color);
}

.folder-row .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.folder-row .cnt {
  color: var(--text-color-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.kebab {
  opacity: 0;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color-secondary);
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
}

.folder-row:hover .kebab {
  opacity: 1;
}

.kebab:hover {
  background: var(--surface-card);
  color: var(--text-color);
}

.kebab .pi {
  font-size: 11px;
}
</style>
