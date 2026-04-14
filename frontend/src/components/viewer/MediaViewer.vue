<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { updateMedia, deleteMedia } from '../../api/media'
import ImageViewer from './ImageViewer.vue'
import VideoPlayer from './VideoPlayer.vue'

const props = defineProps<{
  mediaList: Media[]
  initialIndex: number
}>()

const emit = defineEmits<{
  close: []
}>()

const mediaStore = useMediaStore()
const currentIndex = ref(props.initialIndex)
const videoPlayerRef = ref<InstanceType<typeof VideoPlayer> | null>(null)
const showHelp = ref(false)
const undoData = ref<{ media: Media; index: number } | null>(null)

const current = computed(() => props.mediaList[currentIndex.value] ?? null)

const positionLabel = computed(() =>
  `${currentIndex.value + 1} / ${props.mediaList.length}`
)

watch(current, (media) => {
  if (media) mediaStore.selectMedia(media)
})

// Navigation
function navigate(direction: number) {
  const newIdx = currentIndex.value + direction
  if (newIdx >= 0 && newIdx < props.mediaList.length) {
    currentIndex.value = newIdx
    undoData.value = null
  }
}

function goNext() { navigate(1) }
function goPrev() { navigate(-1) }

// Favorite
async function toggleFavorite() {
  if (!current.value) return
  await mediaStore.toggleFavorite(current.value)
}

// Delete with undo
async function deleteCurrent() {
  if (!current.value) return
  const confirmed = window.confirm(`Delete "${current.value.file_name}"?`)
  if (!confirmed) return

  const deletedMedia = current.value
  const deletedIndex = currentIndex.value

  await mediaStore.removeMedia(deletedMedia)

  undoData.value = { media: deletedMedia, index: deletedIndex }

  // Navigate after delete
  if (props.mediaList.length === 0) {
    emit('close')
    return
  }
  if (currentIndex.value >= props.mediaList.length) {
    currentIndex.value = props.mediaList.length - 1
  }
}

// Speed change persistence
async function onSpeedChange(speed: number) {
  if (!current.value) return
  await updateMedia(current.value.file_path, { playback_speed: speed })
}

// Keyboard shortcuts
function onKeyDown(e: KeyboardEvent) {
  // Don't handle if typing in input
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

  switch (e.key) {
    case 'Escape':
      emit('close')
      break
    case 'ArrowLeft':
      goPrev()
      break
    case 'ArrowRight':
      goNext()
      break
    case ' ':
      e.preventDefault()
      if (current.value?.is_video) {
        videoPlayerRef.value?.togglePlay()
      }
      break
    case 'f':
    case 'F':
      toggleFavorite()
      break
    case ',':
      videoPlayerRef.value?.stepFrame(-1)
      break
    case '.':
      videoPlayerRef.value?.stepFrame(1)
      break
    case 'm':
    case 'M':
      videoPlayerRef.value?.toggleMute()
      break
    case 'ArrowUp':
      e.preventDefault()
      videoPlayerRef.value?.adjustVolume(0.05)
      break
    case 'ArrowDown':
      e.preventDefault()
      videoPlayerRef.value?.adjustVolume(-0.05)
      break
    case 'h':
    case 'H':
    case '?':
      showHelp.value = !showHelp.value
      break
    case 'd':
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        deleteCurrent()
      }
      break
  }
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div class="media-viewer-overlay" @click.self="emit('close')">
    <div class="viewer-container">
      <!-- Header -->
      <div class="viewer-header">
        <div class="header-left">
          <button
            class="fav-btn"
            :class="{ active: current?.is_favorite }"
            @click="toggleFavorite"
            :title="current?.is_favorite ? 'Remove favorite' : 'Add favorite'"
          >
            {{ current?.is_favorite ? '★' : '☆' }}
          </button>
          <span class="position-label">{{ positionLabel }}</span>
          <span v-if="undoData" class="undo-link" @click="undoData = null">
            File deleted.
          </span>
        </div>

        <div class="header-right">
          <button class="icon-btn" @click="showHelp = !showHelp" title="Shortcuts (H)">?</button>
          <button class="icon-btn delete-btn" @click="deleteCurrent" title="Delete (Ctrl+D)">🗑</button>
          <button class="icon-btn close-btn" @click="emit('close')" title="Close (Esc)">✕</button>
        </div>
      </div>

      <!-- Main display area -->
      <div class="viewer-body">
        <!-- Prev button -->
        <button
          class="nav-btn nav-prev"
          @click="goPrev"
          :disabled="currentIndex <= 0"
        >
          ‹
        </button>

        <!-- Content -->
        <div class="viewer-content">
          <template v-if="current">
            <VideoPlayer
              v-if="current.is_video"
              ref="videoPlayerRef"
              :file-path="current.file_path"
              :playback-speed="current.playback_speed"
              @speed-change="onSpeedChange"
            />
            <ImageViewer
              v-else
              :file-path="current.file_path"
            />
          </template>
        </div>

        <!-- Next button -->
        <button
          class="nav-btn nav-next"
          @click="goNext"
          :disabled="currentIndex >= mediaList.length - 1"
        >
          ›
        </button>
      </div>

      <!-- Info bar -->
      <div v-if="current" class="viewer-info">
        <span>{{ current.file_name }}</span>
        <span>{{ current.width }} x {{ current.height }}</span>
        <span>{{ formatSize(current.file_size) }}</span>
        <span v-if="current.frame_rate">{{ current.frame_rate.toFixed(1) }} fps</span>
      </div>

      <!-- Help overlay -->
      <div v-if="showHelp" class="help-overlay" @click="showHelp = false">
        <div class="help-card" @click.stop>
          <h3>Keyboard Shortcuts</h3>
          <table>
            <tbody>
              <tr><td>Esc</td><td>Close viewer</td></tr>
              <tr><td>← →</td><td>Previous / Next</td></tr>
              <tr><td>Space</td><td>Play / Pause (video)</td></tr>
              <tr><td>F</td><td>Toggle favorite</td></tr>
              <tr><td>Ctrl+D</td><td>Delete file</td></tr>
              <tr><td>, .</td><td>Prev / Next frame (video)</td></tr>
              <tr><td>M</td><td>Mute / Unmute (video)</td></tr>
              <tr><td>↑ ↓</td><td>Volume up / down (video)</td></tr>
              <tr><td>H / ?</td><td>Toggle this help</td></tr>
              <tr><td>Dbl-click</td><td>Reset zoom (image)</td></tr>
            </tbody>
          </table>
          <button class="close-help" @click="showHelp = false">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.media-viewer-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.92);
  display: flex;
  align-items: stretch;
  justify-content: stretch;
}

.viewer-container {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
}

/* Header */
.viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: rgba(0, 0, 0, 0.6);
  flex-shrink: 0;
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.fav-btn {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: #999;
  line-height: 1;
}

.fav-btn.active {
  color: #fbbf24;
}

.position-label {
  color: #ccc;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.undo-link {
  color: #60a5fa;
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
}

.icon-btn {
  background: none;
  border: none;
  color: #ccc;
  font-size: 18px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.delete-btn:hover {
  color: #f87171;
}

.close-btn {
  font-size: 20px;
}

/* Body */
.viewer-body {
  flex: 1;
  display: flex;
  align-items: stretch;
  min-height: 0;
}

.viewer-content {
  flex: 1;
  min-width: 0;
  display: flex;
}

.nav-btn {
  width: 48px;
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.5);
  font-size: 48px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: color 0.15s, background 0.15s;
}

.nav-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.05);
  color: #fff;
}

.nav-btn:disabled {
  opacity: 0.2;
  cursor: default;
}

/* Info bar */
.viewer-info {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 6px 16px;
  background: rgba(0, 0, 0, 0.6);
  color: #aaa;
  font-size: 13px;
  flex-shrink: 0;
}

.viewer-info span:first-child {
  color: #ddd;
  font-weight: 500;
}

/* Help overlay */
.help-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}

.help-card {
  background: #1e293b;
  border-radius: 12px;
  padding: 24px 32px;
  color: #e2e8f0;
  min-width: 320px;
}

.help-card h3 {
  margin: 0 0 16px;
  font-size: 16px;
  color: #fff;
}

.help-card table {
  width: 100%;
  border-collapse: collapse;
}

.help-card td {
  padding: 4px 0;
  font-size: 13px;
}

.help-card td:first-child {
  font-family: monospace;
  color: #60a5fa;
  padding-right: 24px;
  white-space: nowrap;
}

.close-help {
  margin-top: 16px;
  padding: 6px 16px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  border-radius: 6px;
  color: #ccc;
  cursor: pointer;
  font-size: 13px;
}

.close-help:hover {
  background: rgba(255, 255, 255, 0.2);
}
</style>
