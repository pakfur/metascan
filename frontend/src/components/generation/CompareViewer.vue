<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

// Original | upscaled, the same box for both: moving the mouse moves the split,
// the original to its left. `object-fit: contain` in one box keeps the two
// aligned whatever their pixel sizes, since an upscale keeps the aspect ratio.
//
// Videos use the same wipe over two elements driven by one set of controls.
// Two <video>s can drift, so each tick corrects the follower; if it will not
// stay in step the viewer falls back to side by side.
const props = defineProps<{
  originalUrl: string
  upscaledUrl: string | null
  isVideo: boolean
}>()

const SEEK_TOLERANCE_S = 0.1
const DRIFT_LIMIT_S = 0.5
const DRIFT_STRIKES = 5

const split = ref(50)
const stage = ref<HTMLElement | null>(null)
const originalVideo = ref<HTMLVideoElement | null>(null)
const upscaledVideo = ref<HTMLVideoElement | null>(null)
const playing = ref(false)
const muted = ref(true)
const time = ref(0)
const duration = ref(0)
const sideBySide = ref(false)
let strikes = 0

const comparing = computed(() => props.upscaledUrl !== null)

function onMove(e: PointerEvent) {
  const box = stage.value?.getBoundingClientRect()
  if (!box || box.width === 0) return
  split.value = Math.min(100, Math.max(0, ((e.clientX - box.left) / box.width) * 100))
}

const clip = computed(() => `inset(0 ${100 - split.value}% 0 0)`)

function leader(): HTMLVideoElement | null {
  return upscaledVideo.value ?? originalVideo.value
}

function onTimeUpdate() {
  const lead = leader()
  if (!lead) return
  time.value = lead.currentTime
  const follow = originalVideo.value
  if (!follow || follow === lead) return
  const drift = Math.abs(follow.currentTime - lead.currentTime)
  if (drift > SEEK_TOLERANCE_S) follow.currentTime = lead.currentTime
  strikes = drift > DRIFT_LIMIT_S ? strikes + 1 : 0
  if (strikes >= DRIFT_STRIKES) sideBySide.value = true
}

function onLoaded() {
  const lead = leader()
  duration.value = lead && Number.isFinite(lead.duration) ? lead.duration : 0
}

async function togglePlay() {
  const videos = [originalVideo.value, upscaledVideo.value].filter(
    (v): v is HTMLVideoElement => v !== null,
  )
  if (playing.value) {
    videos.forEach((v) => v.pause())
    playing.value = false
    return
  }
  playing.value = true
  await Promise.all(videos.map((v) => v.play().catch(() => {})))
}

function onScrub(e: Event) {
  const value = Number((e.target as HTMLInputElement).value)
  time.value = value
  for (const v of [originalVideo.value, upscaledVideo.value]) {
    if (v) v.currentTime = value
  }
}

function formatTime(s: number): string {
  const whole = Math.floor(s)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

// Windows refuses to replace a file a <video> still has open.
function release(): void {
  for (const v of [originalVideo.value, upscaledVideo.value]) {
    if (!v) continue
    v.pause()
    v.removeAttribute('src')
    v.load()
  }
  playing.value = false
}

defineExpose({ release })

watch(
  () => [props.originalUrl, props.upscaledUrl],
  () => {
    playing.value = false
    time.value = 0
    strikes = 0
    sideBySide.value = false
  },
)

onBeforeUnmount(release)
</script>

<template>
  <div class="compare">
    <div
      v-if="!isVideo || !sideBySide"
      ref="stage"
      class="stage"
      @pointermove="onMove"
    >
      <template v-if="isVideo">
        <video
          v-if="upscaledUrl"
          ref="upscaledVideo"
          :key="upscaledUrl"
          class="layer"
          :src="upscaledUrl"
          :muted="muted"
          loop
          playsinline
          @loadedmetadata="onLoaded"
          @timeupdate="onTimeUpdate"
        />
        <video
          ref="originalVideo"
          :key="originalUrl"
          class="layer"
          :class="{ top: comparing }"
          :style="comparing ? { clipPath: clip } : undefined"
          :src="originalUrl"
          muted
          loop
          playsinline
          @loadedmetadata="onLoaded"
          @timeupdate="!upscaledUrl && onTimeUpdate()"
        />
      </template>
      <template v-else>
        <img v-if="upscaledUrl" class="layer" :src="upscaledUrl" alt="Upscaled" draggable="false" />
        <img
          class="layer"
          :class="{ top: comparing }"
          :style="comparing ? { clipPath: clip } : undefined"
          :src="originalUrl"
          alt="Original"
          draggable="false"
        />
      </template>

      <template v-if="comparing">
        <div class="divider" :style="{ left: split + '%' }" />
        <span class="tag left">Original</span>
        <span class="tag right">Upscaled</span>
      </template>
    </div>

    <div v-else class="side-by-side">
      <figure>
        <video ref="originalVideo" :src="originalUrl" muted loop playsinline @loadedmetadata="onLoaded" />
        <figcaption>Original</figcaption>
      </figure>
      <figure>
        <video
          ref="upscaledVideo"
          :src="upscaledUrl ?? undefined"
          :muted="muted"
          loop
          playsinline
          @loadedmetadata="onLoaded"
          @timeupdate="onTimeUpdate"
        />
        <figcaption>Upscaled</figcaption>
      </figure>
    </div>

    <div v-if="isVideo" class="controls">
      <button class="ctl" :title="playing ? 'Pause' : 'Play'" @click="togglePlay">
        <i class="pi" :class="playing ? 'pi-pause' : 'pi-play'" />
      </button>
      <input
        class="scrub"
        type="range"
        min="0"
        :max="duration || 0"
        step="0.01"
        :value="time"
        aria-label="Position"
        @input="onScrub"
      />
      <span class="clock">{{ formatTime(time) }} / {{ formatTime(duration) }}</span>
      <button
        v-if="upscaledUrl"
        class="ctl"
        :title="muted ? 'Unmute' : 'Mute'"
        @click="muted = !muted"
      >
        <i class="pi" :class="muted ? 'pi-volume-off' : 'pi-volume-up'" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.compare {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  cursor: ew-resize;
  touch-action: none;
  user-select: none;
}
.layer {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.layer.top { z-index: 1; }
.divider {
  position: absolute;
  z-index: 2;
  top: 0;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: var(--primary-color);
  pointer-events: none;
}
.tag {
  position: absolute;
  z-index: 2;
  top: 8px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.6);
  pointer-events: none;
}
.tag.left { left: 8px; }
.tag.right { right: 8px; }
.side-by-side {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.side-by-side figure {
  margin: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.side-by-side video {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  object-fit: contain;
  background: var(--surface-ground);
  border-radius: 8px;
}
.side-by-side figcaption {
  font-size: 12px;
  color: var(--text-color-secondary);
  text-align: center;
}
.controls {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
}
.ctl {
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
}
.scrub { flex: 1 1 auto; min-width: 0; }
.clock {
  font-size: 12px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}
</style>
