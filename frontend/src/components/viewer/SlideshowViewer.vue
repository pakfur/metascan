<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { streamUrl } from '../../api/client'
import VideoPlayer from './VideoPlayer.vue'
import { fileName } from '../../utils/path'

const props = withDefaults(
  defineProps<{
    mediaList: Media[]
    autoStart?: boolean
  }>(),
  { autoStart: false },
)

const emit = defineEmits<{
  close: []
}>()

const mediaStore = useMediaStore()
const videoPlayerRef = ref<InstanceType<typeof VideoPlayer> | null>(null)
const overlayRef = ref<HTMLElement | null>(null)

// Setup state
const started = ref(false)
const orderMode = ref<'ordered' | 'random'>('ordered')
const imageDuration = ref(5)
const transition = ref<'none' | 'fade'>('fade')
const transitionDuration = ref(1)

// Playback state
const currentIndex = ref(0)
const paused = ref(false)
const shuffledIndices = ref<number[]>([])
const shufflePos = ref(0)
const transitioning = ref(false)
const controlsVisible = ref(true)
const expanded = ref(false)
let autoAdvanceTimer: ReturnType<typeof setTimeout> | null = null
let hideControlsTimer: ReturnType<typeof setTimeout> | null = null

const current = computed(() => {
  if (!started.value) return null
  const idx = orderMode.value === 'random'
    ? shuffledIndices.value[shufflePos.value]
    : currentIndex.value
  return props.mediaList[idx] ?? null
})

const durationOptions = [
  { label: '3 seconds', value: 3 },
  { label: '5 seconds', value: 5 },
  { label: '10 seconds', value: 10 },
  { label: '15 seconds', value: 15 },
  { label: '30 seconds', value: 30 },
]

const transitionDurationOptions = [
  { label: '0.25s', value: 0.25 },
  { label: '0.5s', value: 0.5 },
  { label: '0.75s', value: 0.75 },
  { label: '1s', value: 1 },
  { label: '1.5s', value: 1.5 },
]

function shuffleArray(n: number): number[] {
  const arr = Array.from({ length: n }, (_, i) => i)
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[arr[i], arr[j]] = [arr[j], arr[i]]
  }
  return arr
}

function startSlideshow() {
  if (props.mediaList.length === 0) return
  started.value = true
  paused.value = false
  currentIndex.value = 0

  if (orderMode.value === 'random') {
    shuffledIndices.value = shuffleArray(props.mediaList.length)
    shufflePos.value = 0
  }

  scheduleAdvance()
  resetHideControls()
}

function scheduleAdvance() {
  clearAdvanceTimer()
  if (paused.value) return
  if (current.value?.is_video) return // Videos don't auto-advance

  autoAdvanceTimer = setTimeout(() => {
    navigateNext()
  }, imageDuration.value * 1000)
}

function clearAdvanceTimer() {
  if (autoAdvanceTimer) {
    clearTimeout(autoAdvanceTimer)
    autoAdvanceTimer = null
  }
}

function navigateNext() {
  if (orderMode.value === 'random') {
    shufflePos.value++
    if (shufflePos.value >= shuffledIndices.value.length) {
      shuffledIndices.value = shuffleArray(props.mediaList.length)
      shufflePos.value = 0
    }
  } else {
    currentIndex.value = (currentIndex.value + 1) % props.mediaList.length
  }
  applyTransition()
}

function navigatePrev() {
  if (orderMode.value === 'random') {
    shufflePos.value = Math.max(0, shufflePos.value - 1)
  } else {
    currentIndex.value =
      (currentIndex.value - 1 + props.mediaList.length) % props.mediaList.length
  }
  applyTransition()
}

function applyTransition() {
  if (transition.value === 'none') {
    scheduleAdvance()
    return
  }
  transitioning.value = true
  setTimeout(() => {
    transitioning.value = false
    scheduleAdvance()
  }, transitionDuration.value * 1000)
}

function togglePause() {
  paused.value = !paused.value
  if (paused.value) {
    clearAdvanceTimer()
  } else {
    scheduleAdvance()
  }
}

function toggleFavorite() {
  if (!current.value) return
  mediaStore.toggleFavorite(current.value)
}

// Auto-hide controls
function resetHideControls() {
  controlsVisible.value = true
  if (hideControlsTimer) clearTimeout(hideControlsTimer)
  hideControlsTimer = setTimeout(() => {
    controlsVisible.value = false
  }, 3000)
}

function onMouseMove() {
  if (started.value) resetHideControls()
}

function onPointerReveal() {
  if (started.value) resetHideControls()
}

function reopenSetup() {
  clearAdvanceTimer()
  started.value = false
}

// Expand (browser fullscreen — drops window chrome). Safari still ships the
// webkit-prefixed API only, so both spellings are probed.
type WebkitFullscreenElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void
}
type WebkitFullscreenDocument = Document & {
  webkitFullscreenElement?: Element | null
  webkitExitFullscreen?: () => Promise<void> | void
}

const fsDoc = document as WebkitFullscreenDocument

const expandSupported =
  document.fullscreenEnabled ||
  typeof (document.documentElement as WebkitFullscreenElement).webkitRequestFullscreen ===
    'function'

function fullscreenElement(): Element | null {
  return document.fullscreenElement ?? fsDoc.webkitFullscreenElement ?? null
}

async function enterExpand() {
  const el = overlayRef.value as WebkitFullscreenElement | null
  if (!el) return
  try {
    if (el.requestFullscreen) await el.requestFullscreen()
    else if (el.webkitRequestFullscreen) await el.webkitRequestFullscreen()
  } catch {
    // Denied (e.g. iOS Safari, or no user gesture) — stay windowed.
  }
  syncExpanded()
}

async function exitExpand() {
  if (!fullscreenElement()) return
  try {
    if (document.exitFullscreen) await document.exitFullscreen()
    else if (fsDoc.webkitExitFullscreen) await fsDoc.webkitExitFullscreen()
  } catch {
    // Ignore — syncExpanded below reconciles with the real state.
  }
  syncExpanded()
}

function toggleExpand() {
  if (expanded.value) exitExpand()
  else enterExpand()
  resetHideControls()
}

function syncExpanded() {
  const el = fullscreenElement()
  expanded.value = !!el && el === overlayRef.value
}

// Keyboard shortcuts
function onKeyDown(e: KeyboardEvent) {
  if (!started.value) return

  switch (e.key) {
    case 'Escape':
      // Browsers normally swallow the Escape that leaves fullscreen, but when
      // one does deliver it, collapse instead of tearing down the slideshow.
      if (expanded.value) exitExpand()
      else emit('close')
      break
    case ' ':
      e.preventDefault()
      togglePause()
      break
    case 'ArrowLeft':
      navigatePrev()
      break
    case 'ArrowRight':
      navigateNext()
      break
    case 'f':
    case 'F':
      toggleFavorite()
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
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('mousemove', onMouseMove)
  document.addEventListener('fullscreenchange', syncExpanded)
  document.addEventListener('webkitfullscreenchange', syncExpanded)
  if (props.autoStart && props.mediaList.length > 0) {
    startSlideshow()
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('fullscreenchange', syncExpanded)
  document.removeEventListener('webkitfullscreenchange', syncExpanded)
  clearAdvanceTimer()
  if (hideControlsTimer) clearTimeout(hideControlsTimer)
  // Don't leave the page in fullscreen after the overlay unmounts.
  if (expanded.value) exitExpand()
})

// Re-schedule timer when media changes
watch(current, () => {
  if (started.value) {
    scheduleAdvance()
  }
})
</script>

<template>
  <div
    ref="overlayRef"
    class="slideshow-overlay"
    :class="{ 'hide-cursor': !controlsVisible && started, expanded }"
    @pointerdown="onPointerReveal"
  >
    <!-- Setup panel (before start) -->
    <div v-if="!started" class="setup-panel">
      <h2>Slideshow</h2>

      <div class="setup-group">
        <label class="setup-label">Order</label>
        <div class="radio-group">
          <label>
            <input type="radio" v-model="orderMode" value="ordered" />
            Ordered
          </label>
          <label>
            <input type="radio" v-model="orderMode" value="random" />
            Random
          </label>
        </div>
      </div>

      <div class="setup-group">
        <label class="setup-label">Image Duration</label>
        <select v-model="imageDuration" class="setup-select">
          <option v-for="opt in durationOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>

      <div class="setup-group">
        <label class="setup-label">Transition</label>
        <div class="radio-group">
          <label>
            <input type="radio" v-model="transition" value="none" />
            None
          </label>
          <label>
            <input type="radio" v-model="transition" value="fade" />
            Fade
          </label>
        </div>
      </div>

      <div v-if="transition !== 'none'" class="setup-group">
        <label class="setup-label">Transition Duration</label>
        <select v-model="transitionDuration" class="setup-select">
          <option v-for="opt in transitionDurationOptions" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>

      <div class="setup-actions">
        <button class="start-btn" @click="startSlideshow">
          Start Slideshow ({{ mediaList.length }} items)
        </button>
        <button class="cancel-btn" @click="emit('close')">Cancel</button>
      </div>
    </div>

    <!-- Slideshow display -->
    <template v-if="started && current">
      <div
        class="slide"
        :class="{
          'fade-in': transitioning && transition === 'fade',
        }"
        :style="{
          animationDuration: transitionDuration + 's',
        }"
      >
        <VideoPlayer
          v-if="current.is_video"
          ref="videoPlayerRef"
          :file-path="current.file_path"
          :playback-speed="current.playback_speed"
          :overlay-controls="expanded"
          :controls-visible="controlsVisible"
          autoplay
        />
        <img
          v-else
          :src="streamUrl(current.file_path)"
          :alt="current.file_name ?? fileName(current.file_path)"
          class="slide-image"
        />
      </div>

      <!-- Hover controls -->
      <div
        class="slideshow-controls"
        :class="{
          visible: controlsVisible,
          'above-video-bar': expanded && current.is_video,
        }"
      >
        <button class="ss-btn" @click="navigatePrev">‹</button>
        <button class="ss-btn" @click="togglePause">
          {{ paused ? '▶' : '❚❚' }}
        </button>
        <button class="ss-btn" @click="navigateNext">›</button>
        <button
          class="ss-btn fav"
          :class="{ active: current.is_favorite }"
          @click="toggleFavorite"
        >
          {{ current.is_favorite ? '★' : '☆' }}
        </button>
        <button class="ss-btn" @click="reopenSetup" title="Slideshow settings">⚙</button>
        <button
          v-if="expandSupported"
          class="ss-btn expand"
          :title="expanded ? 'Collapse (Esc)' : 'Expand to full screen'"
          :aria-label="expanded ? 'Collapse' : 'Expand to full screen'"
          @click="toggleExpand"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path
              v-if="expanded"
              d="M10 4v6H4M14 4v6h6M10 20v-6H4M14 20v-6h6"
            />
            <path v-else d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
          </svg>
        </button>
        <button class="ss-btn exit" @click="emit('close')">✕</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.slideshow-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.slideshow-overlay.hide-cursor {
  cursor: none;
}

/* Setup panel */
.setup-panel {
  background: #1e293b;
  border-radius: 12px;
  padding: 32px 40px;
  color: #e2e8f0;
  min-width: 360px;
}

.setup-panel h2 {
  margin: 0 0 24px;
  font-size: 20px;
  color: #fff;
}

.setup-group {
  margin-bottom: 16px;
}

.setup-label {
  display: block;
  font-size: 13px;
  color: #94a3b8;
  margin-bottom: 6px;
  font-weight: 600;
}

.radio-group {
  display: flex;
  gap: 16px;
}

.radio-group label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #e2e8f0;
  cursor: pointer;
}

.setup-select {
  width: 100%;
  padding: 6px 10px;
  background: #334155;
  border: 1px solid #475569;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 14px;
}

.setup-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
}

.start-btn {
  flex: 1;
  padding: 10px 16px;
  background: var(--primary-color, #3b82f6);
  border: none;
  border-radius: 8px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.start-btn:hover {
  opacity: 0.9;
}

.cancel-btn {
  padding: 10px 16px;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  border-radius: 8px;
  color: #ccc;
  font-size: 14px;
  cursor: pointer;
}

.cancel-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}

/* Slide display */
.slide {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* width/height (not max-*) so undersized media scales UP to the viewport;
   object-fit: contain keeps its own aspect ratio, so it meets the edges
   top-to-bottom or side-to-side depending on which way it's proportioned. */
.slide-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
}

.fade-in {
  animation: fadeIn ease-in forwards;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* Slideshow controls */
.slideshow-controls {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 8px;
  background: rgba(0, 0, 0, 0.7);
  border-radius: 12px;
  padding: 8px 12px;
  opacity: 0;
  transition: opacity 0.3s;
  pointer-events: none;
}

.slideshow-controls.visible {
  opacity: 1;
  pointer-events: auto;
}

/* Clear the video player's own overlaid control bar. */
.slideshow-controls.above-video-bar {
  bottom: 68px;
}

.ss-btn {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.8);
  font-size: 24px;
  cursor: pointer;
  padding: 4px 12px;
  border-radius: 6px;
  line-height: 1;
}

.ss-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
}

.ss-btn.fav {
  font-size: 20px;
}

.ss-btn.fav.active {
  color: #fbbf24;
}

.ss-btn.expand {
  display: flex;
  align-items: center;
  padding: 4px 10px;
}

.ss-btn.expand svg {
  width: 20px;
  height: 20px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.ss-btn.exit {
  font-size: 18px;
  margin-left: 4px;
}
</style>
