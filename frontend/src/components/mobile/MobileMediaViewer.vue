<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { streamUrl } from '../../api/client'
import { fileName } from '../../utils/path'
import {
  classifySwipe,
  distance,
  midpoint,
  clampZoom,
  type Point,
} from '../../utils/gestures'

const props = defineProps<{
  mediaList: Media[]
  initialIndex: number
}>()

const emit = defineEmits<{
  close: []
}>()

const mediaStore = useMediaStore()
const currentIndex = ref(props.initialIndex)
const current = computed(() => props.mediaList[currentIndex.value] ?? null)
const positionLabel = computed(
  () => `${currentIndex.value + 1} / ${props.mediaList.length}`,
)

// Keep the store selection in sync so favorite state stays reactive.
watch(current, (m) => {
  if (m) mediaStore.selectMedia(m)
})

// --- zoom / pan state -------------------------------------------------
const MIN_ZOOM = 1
const MAX_ZOOM = 6
const SWIPE_H_MIN = 60 // px horizontal to trigger prev/next
const SWIPE_V_MIN = 100 // px vertical to trigger close

const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)

function resetView() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
}

watch(currentIndex, resetView)

function navigate(direction: number) {
  const next = currentIndex.value + direction
  if (next >= 0 && next < props.mediaList.length) {
    currentIndex.value = next
  }
}

async function toggleFavorite() {
  if (current.value) await mediaStore.toggleFavorite(current.value)
}

// --- pointer gesture handling ----------------------------------------
const pointers = new Map<number, Point>()

// One-finger swipe tracking (only meaningful at zoom == 1).
let swipeStart: Point | null = null
// One-finger pan tracking (only when zoomed in).
let panStart: { pan: Point; pointer: Point } | null = null
// Two-finger pinch tracking.
let pinchStart: { dist: number; zoom: number; mid: Point; pan: Point } | null =
  null

function pointsArray(): Point[] {
  return Array.from(pointers.values())
}

function onPointerDown(e: PointerEvent) {
  ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (pointers.size === 2) {
    const [a, b] = pointsArray()
    pinchStart = {
      dist: distance(a, b),
      zoom: zoom.value,
      mid: midpoint(a, b),
      pan: { x: panX.value, y: panY.value },
    }
    swipeStart = null
    panStart = null
  } else if (pointers.size === 1) {
    if (zoom.value > 1) {
      panStart = {
        pan: { x: panX.value, y: panY.value },
        pointer: { x: e.clientX, y: e.clientY },
      }
      swipeStart = null
    } else {
      swipeStart = { x: e.clientX, y: e.clientY }
      panStart = null
    }
  }
}

function onPointerMove(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) return
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })

  if (pinchStart && pointers.size >= 2) {
    const [a, b] = pointsArray()
    const ratio = distance(a, b) / (pinchStart.dist || 1)
    zoom.value = clampZoom(pinchStart.zoom * ratio, MIN_ZOOM, MAX_ZOOM)
    const mid = midpoint(a, b)
    panX.value = pinchStart.pan.x + (mid.x - pinchStart.mid.x)
    panY.value = pinchStart.pan.y + (mid.y - pinchStart.mid.y)
    return
  }

  if (panStart && zoom.value > 1) {
    panX.value = panStart.pan.x + (e.clientX - panStart.pointer.x)
    panY.value = panStart.pan.y + (e.clientY - panStart.pointer.y)
  }
  // At zoom == 1 we only classify on release (no live translate), keeping the
  // interaction simple and avoiding jitter.
}

function endPointer(e: PointerEvent) {
  const start = swipeStart
  pointers.delete(e.pointerId)

  if (pointers.size < 2) pinchStart = null

  if (pointers.size === 1) {
    // Collapsed from a two-finger gesture (or a stray second touch) down to one
    // finger — re-arm single-pointer tracking for the survivor so pan/swipe
    // continues without requiring a full release.
    const [survivor] = pointsArray()
    if (zoom.value > 1) {
      panStart = {
        pan: { x: panX.value, y: panY.value },
        pointer: { x: survivor.x, y: survivor.y },
      }
      swipeStart = null
    } else {
      swipeStart = { x: survivor.x, y: survivor.y }
      panStart = null
    }
    return
  }

  if (pointers.size === 0) panStart = null

  // Snap an almost-reset zoom back to exactly 1 so swipe re-enables cleanly.
  if (zoom.value <= 1.02) resetView()

  if (start && pointers.size === 0 && zoom.value <= 1) {
    const action = classifySwipe(e.clientX - start.x, e.clientY - start.y, {
      hMin: SWIPE_H_MIN,
      vMin: SWIPE_V_MIN,
    })
    if (action === 'next') navigate(1)
    else if (action === 'prev') navigate(-1)
    else if (action === 'close') emit('close')
  }
  swipeStart = null
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
  else if (e.key === 'ArrowLeft') navigate(-1)
  else if (e.key === 'ArrowRight') navigate(1)
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <div class="mv-overlay">
    <header class="mv-topbar">
      <button
        class="mv-icon"
        :class="{ fav: current?.is_favorite }"
        type="button"
        aria-label="Favorite"
        @click="toggleFavorite"
      >
        {{ current?.is_favorite ? '★' : '☆' }}
      </button>
      <span class="mv-position">{{ positionLabel }}</span>
      <button class="mv-icon" type="button" aria-label="Close" @click="emit('close')">
        ✕
      </button>
    </header>

    <div class="mv-body">
      <video
        v-if="current?.is_video"
        :key="current.file_path"
        class="mv-video"
        :src="streamUrl(current.file_path)"
        controls
        playsinline
      />
      <div
        v-else-if="current"
        class="mv-image-stage"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="endPointer"
        @pointercancel="endPointer"
      >
        <img
          class="mv-image"
          :src="streamUrl(current.file_path)"
          :alt="current.file_name ?? fileName(current.file_path)"
          :style="{ transform: `translate(${panX}px, ${panY}px) scale(${zoom})` }"
          draggable="false"
        />
      </div>
    </div>

    <footer class="mv-nav">
      <button
        class="mv-nav-btn"
        type="button"
        aria-label="Previous"
        :disabled="currentIndex === 0"
        @click="navigate(-1)"
      >
        ‹
      </button>
      <button
        class="mv-nav-btn"
        type="button"
        aria-label="Next"
        :disabled="currentIndex >= mediaList.length - 1"
        @click="navigate(1)"
      >
        ›
      </button>
    </footer>
  </div>
</template>

<style scoped>
.mv-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: #000;
  display: flex;
  flex-direction: column;
}

.mv-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  padding-top: calc(8px + env(safe-area-inset-top));
  background: rgba(0, 0, 0, 0.6);
  flex-shrink: 0;
}

.mv-icon {
  background: none;
  border: none;
  color: #ccc;
  font-size: 22px;
  line-height: 1;
  padding: 6px 10px;
}

.mv-icon.fav {
  color: #fbbf24;
}

.mv-position {
  color: #ccc;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.mv-body {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.mv-video {
  max-width: 100%;
  max-height: 100%;
}

.mv-image-stage {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  touch-action: none; /* we handle all gestures ourselves */
}

.mv-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transform-origin: center center;
  user-select: none;
  -webkit-user-select: none;
}

.mv-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 24px;
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
  background: rgba(0, 0, 0, 0.6);
  flex-shrink: 0;
}

.mv-nav-btn {
  background: none;
  border: none;
  color: #fff;
  font-size: 32px;
  line-height: 1;
  padding: 4px 24px;
}

.mv-nav-btn:disabled {
  color: #555;
}
</style>
