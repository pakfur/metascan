<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import type { Media } from '../../types/media'
import { findDuplicates, deleteDuplicates } from '../../api/similarity'
import { thumbnailUrl } from '../../api/client'
import { fileName } from '../../utils/path'

const emit = defineEmits<{
  close: []
}>()

const groups = ref<Media[][]>([])
const selectedGroup = ref(0)
const selectedForDeletion = ref<Set<string>>(new Set())
const loading = ref(true)
const deleting = ref(false)

const currentGroup = computed(() => groups.value[selectedGroup.value] ?? [])

onMounted(async () => {
  loading.value = true
  try {
    const result = await findDuplicates()
    groups.value = result.groups as unknown as Media[][]
  } finally {
    loading.value = false
  }
})

function selectGroup(idx: number) {
  selectedGroup.value = idx
  selectedForDeletion.value = new Set()
}

function toggleSelection(filePath: string) {
  const s = new Set(selectedForDeletion.value)
  if (s.has(filePath)) {
    s.delete(filePath)
  } else {
    s.add(filePath)
  }
  selectedForDeletion.value = s
}

async function deleteSelected() {
  if (selectedForDeletion.value.size === 0) return
  const paths = Array.from(selectedForDeletion.value)
  const confirmed = confirm(`Delete ${paths.length} file(s)? They will be moved to trash.`)
  if (!confirmed) return

  deleting.value = true
  try {
    await deleteDuplicates(paths)
    // Remove deleted items from group
    groups.value[selectedGroup.value] = currentGroup.value.filter(
      (m) => !selectedForDeletion.value.has(m.file_path)
    )
    // Remove groups with < 2 items
    groups.value = groups.value.filter((g) => g.length >= 2)
    selectedForDeletion.value = new Set()
    if (selectedGroup.value >= groups.value.length) {
      selectedGroup.value = Math.max(0, groups.value.length - 1)
    }
  } finally {
    deleting.value = false
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card dup-dialog">
      <div class="dup-header">
        <h3>Duplicate Finder</h3>
        <span class="group-count">{{ groups.length }} groups found</span>
      </div>

      <div v-if="loading" class="muted">Scanning for duplicates...</div>

      <div v-else-if="groups.length === 0" class="muted">No duplicates found.</div>

      <div v-else class="dup-body">
        <!-- Group list (left) -->
        <div class="group-list">
          <button
            v-for="(group, idx) in groups"
            :key="idx"
            class="group-item"
            :class="{ active: selectedGroup === idx }"
            @click="selectGroup(idx)"
          >
            <span class="group-label">Group {{ idx + 1 }}</span>
            <span class="group-size">{{ group.length }} files</span>
          </button>
        </div>

        <!-- Group detail (right) -->
        <div class="group-detail">
          <div class="detail-files">
            <div
              v-for="media in currentGroup"
              :key="media.file_path"
              class="dup-file"
              :class="{ 'marked-delete': selectedForDeletion.has(media.file_path) }"
            >
              <label class="dup-checkbox">
                <input
                  type="checkbox"
                  :checked="selectedForDeletion.has(media.file_path)"
                  @change="toggleSelection(media.file_path)"
                />
              </label>
              <img
                :src="thumbnailUrl(media.file_path)"
                class="dup-thumb"
                loading="lazy"
              />
              <div class="dup-info">
                <span class="dup-name" :title="media.file_path">{{ media.file_name ?? fileName(media.file_path) }}</span>
                <span class="dup-meta">
                  {{ media.width }}x{{ media.height }} &middot;
                  {{ formatSize(media.file_size) }}
                </span>
              </div>
            </div>
          </div>

          <div class="detail-actions">
            <button
              class="btn-danger"
              :disabled="selectedForDeletion.size === 0 || deleting"
              @click="deleteSelected"
            >
              Delete {{ selectedForDeletion.size }} Selected
            </button>
          </div>
        </div>
      </div>

      <div class="dialog-actions">
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

.dialog-card.dup-dialog {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 24px 28px;
  min-width: 680px;
  max-width: 800px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.dup-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

h3 {
  margin: 0;
  font-size: 18px;
  color: var(--text-color);
}

.group-count {
  font-size: 13px;
  color: var(--text-color-secondary);
}

.muted {
  color: var(--text-color-secondary);
  font-size: 14px;
  padding: 20px 0;
}

.dup-body {
  display: flex;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* Group list */
.group-list {
  width: 160px;
  flex-shrink: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.group-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  border-radius: 6px;
  text-align: left;
}

.group-item:hover {
  background: var(--surface-hover);
}

.group-item.active {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
  font-weight: 600;
}

.group-size {
  font-size: 11px;
  color: var(--text-color-secondary);
}

/* Detail */
.group-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.detail-files {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dup-file {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: var(--surface-ground);
  border-radius: 8px;
  border: 2px solid transparent;
}

.dup-file.marked-delete {
  border-color: var(--danger-color);
  opacity: 0.7;
}

.dup-checkbox input {
  accent-color: var(--danger-color);
}

.dup-thumb {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 4px;
  flex-shrink: 0;
}

.dup-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dup-name {
  font-size: 13px;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dup-meta {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.detail-actions {
  padding-top: 10px;
  flex-shrink: 0;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-shrink: 0;
}

.btn-danger {
  padding: 8px 20px;
  background: var(--danger-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.btn-danger:hover {
  opacity: 0.9;
}

.btn-danger:disabled {
  opacity: 0.4;
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

.btn-secondary:hover {
  background: var(--surface-hover);
}
</style>
