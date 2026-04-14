<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { streamUrl } from '../../api/client'

const props = defineProps<{
  filePath: string
}>()

const container = ref<HTMLElement | null>(null)
const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)
const isPanning = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const panStartX = ref(0)
const panStartY = ref(0)

const MIN_ZOOM = 0.1
const MAX_ZOOM = 10
const ZOOM_STEP = 0.1

function resetView() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
}

watch(() => props.filePath, resetView)

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP
  const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom.value + delta))

  if (container.value) {
    const rect = container.value.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top
    const centerX = rect.width / 2
    const centerY = rect.height / 2

    // Zoom toward cursor position
    const scale = newZoom / zoom.value
    panX.value = mouseX - scale * (mouseX - panX.value - centerX) - centerX
    panY.value = mouseY - scale * (mouseY - panY.value - centerY) - centerY
  }

  zoom.value = newZoom
}

function onMouseDown(e: MouseEvent) {
  if (zoom.value <= 1) return
  isPanning.value = true
  dragStartX.value = e.clientX
  dragStartY.value = e.clientY
  panStartX.value = panX.value
  panStartY.value = panY.value
}

function onMouseMove(e: MouseEvent) {
  if (!isPanning.value) return
  panX.value = panStartX.value + (e.clientX - dragStartX.value)
  panY.value = panStartY.value + (e.clientY - dragStartY.value)
}

function onMouseUp() {
  isPanning.value = false
}

onMounted(() => {
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
})

onUnmounted(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
})
</script>

<template>
  <div
    ref="container"
    class="image-viewer"
    :class="{ panning: isPanning, zoomable: zoom > 1 }"
    @wheel.prevent="onWheel"
    @mousedown="onMouseDown"
    @dblclick="resetView"
  >
    <img
      :src="streamUrl(filePath)"
      :style="{
        transform: `translate(${panX}px, ${panY}px) scale(${zoom})`,
      }"
      draggable="false"
    />
  </div>
</template>

<style scoped>
.image-viewer {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #000;
}

.image-viewer.zoomable {
  cursor: grab;
}

.image-viewer.panning {
  cursor: grabbing;
}

.image-viewer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transform-origin: center center;
  user-select: none;
  transition: none;
}
</style>
