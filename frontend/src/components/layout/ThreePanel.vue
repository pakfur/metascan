<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const container = ref<HTMLElement | null>(null)
const leftWidth = ref(250)
const rightWidth = ref(350)
const dragging = ref<'left' | 'right' | null>(null)
const startX = ref(0)
const startWidth = ref(0)

function onMouseDown(side: 'left' | 'right', e: MouseEvent) {
  dragging.value = side
  startX.value = e.clientX
  startWidth.value = side === 'left' ? leftWidth.value : rightWidth.value
  e.preventDefault()
}

function onMouseMove(e: MouseEvent) {
  if (!dragging.value) return
  const dx = e.clientX - startX.value
  if (dragging.value === 'left') {
    leftWidth.value = Math.max(180, Math.min(500, startWidth.value + dx))
  } else {
    rightWidth.value = Math.max(250, Math.min(600, startWidth.value - dx))
  }
}

function onMouseUp() {
  dragging.value = null
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
  <div ref="container" class="three-panel" :class="{ dragging: !!dragging }">
    <aside class="panel panel-left" :style="{ width: leftWidth + 'px' }">
      <slot name="left" />
    </aside>

    <div class="gutter" @mousedown="onMouseDown('left', $event)" />

    <main class="panel panel-center">
      <slot name="center" />
    </main>

    <div class="gutter" @mousedown="onMouseDown('right', $event)" />

    <aside class="panel panel-right" :style="{ width: rightWidth + 'px' }">
      <slot name="right" />
    </aside>
  </div>
</template>

<style scoped>
.three-panel {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.three-panel.dragging {
  cursor: col-resize;
  user-select: none;
}

.panel {
  overflow-y: auto;
  overflow-x: hidden;
}

.panel-left {
  flex-shrink: 0;
  border-right: 1px solid var(--surface-border);
}

.panel-center {
  flex: 1;
  min-width: 200px;
}

.panel-right {
  flex-shrink: 0;
  border-left: 1px solid var(--surface-border);
}

.gutter {
  width: 5px;
  cursor: col-resize;
  background: transparent;
  flex-shrink: 0;
  transition: background 0.15s;
}

.gutter:hover {
  background: var(--primary-color);
  opacity: 0.4;
}
</style>
