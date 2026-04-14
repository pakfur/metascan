<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { streamUrl } from '../../api/client'

const props = defineProps<{
  filePath: string
  playbackSpeed?: number | null
}>()

const emit = defineEmits<{
  speedChange: [speed: number]
}>()

const videoEl = ref<HTMLVideoElement | null>(null)
const playing = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const volume = ref(0.75)
const muted = ref(false)
const speed = ref(props.playbackSpeed ?? 1.0)
const seeking = ref(false)

const SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

const timeDisplay = computed(() => {
  return `${formatTime(currentTime.value)} / ${formatTime(duration.value)}`
})

const volumeIcon = computed(() => {
  if (muted.value || volume.value === 0) return '\u{1F507}'
  if (volume.value < 0.5) return '\u{1F509}'
  return '\u{1F50A}'
})

const progress = computed(() =>
  duration.value > 0 ? (currentTime.value / duration.value) * 100 : 0
)

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function togglePlay() {
  if (!videoEl.value) return
  if (playing.value) {
    videoEl.value.pause()
  } else {
    videoEl.value.play()
  }
}

function seek(e: MouseEvent) {
  if (!videoEl.value) return
  const bar = e.currentTarget as HTMLElement
  const rect = bar.getBoundingClientRect()
  const ratio = (e.clientX - rect.left) / rect.width
  videoEl.value.currentTime = ratio * duration.value
}

function setVolume(val: number) {
  volume.value = Math.max(0, Math.min(1, val))
  if (videoEl.value) videoEl.value.volume = volume.value
  if (volume.value > 0) muted.value = false
}

function toggleMute() {
  muted.value = !muted.value
  if (videoEl.value) videoEl.value.muted = muted.value
}

function stepFrame(direction: number) {
  if (!videoEl.value) return
  videoEl.value.pause()
  videoEl.value.currentTime += direction * (1 / 30)
}

function setSpeed(s: number) {
  speed.value = s
  if (videoEl.value) videoEl.value.playbackRate = s
  emit('speedChange', s)
}

function adjustVolume(delta: number) {
  setVolume(volume.value + delta)
}

// Expose methods for parent keyboard handling
defineExpose({ togglePlay, stepFrame, adjustVolume, toggleMute })

function onTimeUpdate() {
  if (!seeking.value && videoEl.value) {
    currentTime.value = videoEl.value.currentTime
  }
}

function onLoadedMetadata() {
  if (!videoEl.value) return
  duration.value = videoEl.value.duration
  videoEl.value.volume = volume.value
  videoEl.value.playbackRate = speed.value
}

function onPlay() { playing.value = true }
function onPause() { playing.value = false }

watch(() => props.filePath, async () => {
  playing.value = false
  currentTime.value = 0
  duration.value = 0
  if (props.playbackSpeed != null) speed.value = props.playbackSpeed
  await nextTick()
  if (videoEl.value) {
    videoEl.value.load()
  }
})

watch(() => props.playbackSpeed, (s) => {
  if (s != null) {
    speed.value = s
    if (videoEl.value) videoEl.value.playbackRate = s
  }
})
</script>

<template>
  <div class="video-player">
    <video
      ref="videoEl"
      :src="streamUrl(filePath)"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
      @play="onPlay"
      @pause="onPause"
      @click="togglePlay"
    />

    <div class="controls">
      <button class="ctrl-btn play-btn" @click="togglePlay">
        {{ playing ? '❚❚' : '▶' }}
      </button>

      <button class="ctrl-btn" @click="stepFrame(-1)" title="Previous frame (,)">
        ⏮
      </button>

      <button class="ctrl-btn" @click="stepFrame(1)" title="Next frame (.)">
        ⏭
      </button>

      <div class="seek-bar" @click="seek">
        <div class="seek-fill" :style="{ width: progress + '%' }" />
      </div>

      <span class="time-display">{{ timeDisplay }}</span>

      <select
        class="speed-select"
        :value="speed"
        @change="setSpeed(parseFloat(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="s in SPEEDS" :key="s" :value="s">{{ s }}x</option>
      </select>

      <button class="ctrl-btn volume-btn" @click="toggleMute" :title="muted ? 'Unmute (M)' : 'Mute (M)'">
        {{ volumeIcon }}
      </button>

      <input
        type="range"
        class="volume-slider"
        min="0"
        max="1"
        step="0.01"
        :value="volume"
        @input="setVolume(parseFloat(($event.target as HTMLInputElement).value))"
      />
    </div>
  </div>
</template>

<style scoped>
.video-player {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background: #000;
}

video {
  max-width: 100%;
  max-height: calc(100% - 48px);
  object-fit: contain;
  cursor: pointer;
}

.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 16px;
  background: rgba(0, 0, 0, 0.85);
  flex-shrink: 0;
}

.ctrl-btn {
  background: none;
  border: none;
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
  line-height: 1;
}

.ctrl-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}

.play-btn {
  font-size: 18px;
  min-width: 32px;
}

.seek-bar {
  flex: 1;
  height: 6px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 3px;
  cursor: pointer;
  position: relative;
}

.seek-fill {
  height: 100%;
  background: var(--primary-color, #3b82f6);
  border-radius: 3px;
  pointer-events: none;
}

.time-display {
  color: #ccc;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  min-width: 80px;
  text-align: center;
}

.speed-select {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: #fff;
  font-size: 12px;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
}

.speed-select option {
  background: #1e293b;
}

.volume-btn {
  font-size: 16px;
}

.volume-slider {
  width: 80px;
  accent-color: var(--primary-color, #3b82f6);
  cursor: pointer;
}
</style>
