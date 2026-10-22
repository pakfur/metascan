<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import Galleria from 'primevue/galleria'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { updateMedia, deleteMedia } from '../../api/media'
import { thumbnailUrl } from '../../api/client'
import { fileName } from '../../utils/path'
import ImageViewer from './ImageViewer.vue'
import VideoPlayer from './VideoPlayer.vue'
import LazyThumb from './LazyThumb.vue'
import RatingRail from './RatingRail.vue'
import CategoryPicker from './CategoryPicker.vue'
import { useCategoriesStore } from '../../stores/categories'
import { ApiError } from '../../api/client'
import { fetchJudgment, markSeen, setVerdict, setReason } from '../../api/judgments'
import { addItems, removeItems } from '../../api/categories'
import { REASONS, SEEN_DWELL_MS } from '../../types/judgments'
import type { CategoryRef, JudgmentState, Verdict } from '../../types/judgments'

// allowDestructive defaults to true so every existing (desktop library)
// mount stays byte-identical. The storyboard candidate picker
// (PanelDetail.vue) passes false: its mediaList is synthesized from
// BeatImage rows (stubbed is_favorite, no real Media row for
// mediaStore.selectMedia to resolve), so real-file delete, the favorite
// toggle, and the library-metadata-panel selectMedia leak all need to be
// off there.
const props = withDefaults(
  defineProps<{
    mediaList: Media[]
    initialIndex: number
    allowDestructive?: boolean
  }>(),
  { allowDestructive: true },
)

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
  if (media && props.allowDestructive) mediaStore.selectMedia(media)
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

// ---------------------------------------------------------------------
// Taste rating (only when allowDestructive). The viewer owns every
// judgment request; RatingRail just displays `judgment`.
//
// Ordering: every request for one item (the initial GET, seen, verdicts,
// reasons, category toggles) runs on that item's promise chain, so the
// server sees them in key-press order and responses come back in that
// order. Each op reads the state the previous op returned (`lastKnown`),
// which is what makes a fast `F` then `2` work before the first response
// has painted. Every response is tagged with the path it was for and only
// paints the rail when that path is still the current item.
// ---------------------------------------------------------------------
const categoriesStore = useCategoriesStore()
const judgment = ref<JudgmentState | null>(null)
const hint = ref<string | null>(null)
const pickerOpen = ref(false)
const currentPath = computed(() => current.value?.file_path ?? null)

const lastKnown = new Map<string, JudgmentState>()
const chains = new Map<string, Promise<void>>()
let seenTimer: ReturnType<typeof setTimeout> | null = null
let hintTimer: ReturnType<typeof setTimeout> | null = null
// Video seen fires once per visit; reset on every item change.
let seenSentForVisit = false
let unmounted = false

type JudgmentOp = (state: JudgmentState | null) => Promise<JudgmentState | null>

function enqueue(path: string, op: JudgmentOp): void {
  const prev = chains.get(path) ?? Promise.resolve()
  const next = prev.then(async () => {
    try {
      const state = await op(lastKnown.get(path) ?? null)
      if (state) receive(path, state)
    } catch (e) {
      onJudgmentError(path, e)
    }
  })
  chains.set(path, next)
  void next.then(() => {
    if (chains.get(path) === next) chains.delete(path)
  })
}

function receive(path: string, state: JudgmentState): void {
  lastKnown.set(path, state)
  // The server's flag is the truth whether or not this item is still on
  // screen, so the grid star follows even for a stale response.
  mediaStore.applyFavoriteFlag(path, state.is_favorite)
  if (!unmounted && path === currentPath.value) judgment.value = state
}

function showHint(path: string, message: string): void {
  if (unmounted || path !== currentPath.value) return
  hint.value = message
  if (hintTimer) clearTimeout(hintTimer)
  hintTimer = setTimeout(() => {
    hint.value = null
    hintTimer = null
  }, 1500)
}

function clearHint(): void {
  if (hintTimer) clearTimeout(hintTimer)
  hintTimer = null
  hint.value = null
}

function errorCode(e: unknown): string | null {
  if (e instanceof ApiError && e.detail && typeof e.detail === 'object') {
    const code = (e.detail as { code?: unknown }).code
    if (typeof code === 'string') return code
  }
  return null
}

function onJudgmentError(path: string, e: unknown): void {
  const code = errorCode(e)
  if (code === 'no_verdict') {
    showHint(path, 'Press F or X first')
    return
  }
  if (e instanceof ApiError && e.status === 404 && code === null) {
    // Not a library item (GET/seen on unknown media): nothing to rate.
    return
  }
  console.error('Judgment request failed', e)
  if (code === 'duplicate_name') showHint(path, 'A category with that name exists')
  else if (code === 'invalid_name') showHint(path, 'Category names are 1–40 characters')
  else showHint(path, 'Save failed — see console')
}

watch(
  currentPath,
  (path) => {
    if (seenTimer) clearTimeout(seenTimer)
    seenTimer = null
    seenSentForVisit = false
    clearHint()
    judgment.value = path ? (lastKnown.get(path) ?? null) : null
    // Forget settled items so a long rating session doesn't accumulate.
    for (const key of lastKnown.keys()) {
      if (key !== path && !chains.has(key)) lastKnown.delete(key)
    }
    if (!props.allowDestructive || !path) return
    enqueue(path, () => fetchJudgment(path))
    if (!current.value?.is_video) {
      seenTimer = setTimeout(() => {
        seenTimer = null
        if (path !== currentPath.value) return
        seenSentForVisit = true
        enqueue(path, () => markSeen(path))
      }, SEEN_DWELL_MS)
    }
  },
  { immediate: true },
)

function onVideoPlay(path: string): void {
  if (!props.allowDestructive || path !== currentPath.value || seenSentForVisit) return
  seenSentForVisit = true
  enqueue(path, () => markSeen(path))
}

function verdictOpts(): { stickyCategoryId?: number } {
  const id = categoriesStore.stickyId
  return id === null ? {} : { stickyCategoryId: id }
}

// F, X and the header star. The toggle reads the state the previous op on
// this item left behind, so F F F alternates correctly even when typed
// faster than the server answers.
function toggleVerdict(target: Verdict): void {
  const media = current.value
  if (!props.allowDestructive || !media) return
  const path = media.file_path
  const opts = verdictOpts()
  enqueue(path, (state) => {
    const isTarget = state
      ? state.verdict === target
      : target === 'favorite' && media.is_favorite
    return setVerdict(path, isTarget ? null : target, isTarget ? {} : opts)
  })
}

function toggleFavorite(): void {
  toggleVerdict('favorite')
}

function toggleReason(index: number): void {
  const reason = REASONS[index]
  if (!props.allowDestructive || !currentPath.value || !reason) return
  const path = currentPath.value
  enqueue(path, async (state) => {
    if (!state?.verdict) {
      showHint(path, 'Press F or X first')
      return null
    }
    return setReason(path, reason, !state.reasons.includes(reason))
  })
}

// Category picker
const assignedIds = computed(() => judgment.value?.categories.map((c) => c.id) ?? [])

const stickyCategory = computed<CategoryRef | null>(() => {
  const id = categoriesStore.stickyId
  if (id === null) return null
  return categoriesStore.categories.find((c) => c.id === id) ?? null
})

// Chip names follow renames made in the picker or another tab without a
// refetch of the judgment.
const railJudgment = computed<JudgmentState | null>(() => {
  const j = judgment.value
  if (!j || !categoriesStore.categories.length) return j
  const names = new Map(categoriesStore.categories.map((c) => [c.id, c.name]))
  return {
    ...j,
    categories: j.categories.map((c) => ({ id: c.id, name: names.get(c.id) ?? c.name })),
  }
})

function openPicker(): void {
  if (!props.allowDestructive || !current.value) return
  pickerOpen.value = true
  categoriesStore.load().catch((e) => console.error('Failed to load categories', e))
}

function closePicker(): void {
  pickerOpen.value = false
}

function toggleCategory(id: number): void {
  const path = currentPath.value
  if (!path) return
  enqueue(path, async (state) => {
    const on = state?.categories.some((c) => c.id === id) ?? false
    if (on) await removeItems(id, [path])
    else await addItems(id, [path], 'viewer')
    return fetchJudgment(path)
  })
}

async function createAndAddCategory(name: string): Promise<void> {
  const path = currentPath.value
  if (!path) return
  try {
    const created = await categoriesStore.create(name)
    enqueue(path, async () => {
      await addItems(created.id, [path], 'viewer')
      return fetchJudgment(path)
    })
  } catch (e) {
    onJudgmentError(path, e)
  }
}

function toggleSticky(id: number): void {
  categoriesStore.setSticky(categoriesStore.stickyId === id ? null : id)
}

async function renameCategory(id: number, name: string): Promise<void> {
  try {
    await categoriesStore.rename(id, name)
  } catch (e) {
    if (currentPath.value) onJudgmentError(currentPath.value, e)
  }
}

async function deleteCategory(id: number): Promise<void> {
  const path = currentPath.value
  try {
    await categoriesStore.remove(id)
    if (path) enqueue(path, () => fetchJudgment(path))
  } catch (e) {
    if (path) onJudgmentError(path, e)
  }
}

// Delete with undo
async function deleteCurrent() {
  if (!props.allowDestructive || !current.value) return
  const confirmed = window.confirm(
    `Delete "${current.value.file_name ?? fileName(current.value.file_path)}"?`,
  )
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

  // The picker owns the keyboard while open. Its input normally has focus
  // (and is skipped above); if focus wandered off, Esc still closes it and
  // nothing else reaches the viewer.
  if (pickerOpen.value) {
    if (e.key === 'Escape') closePicker()
    return
  }

  // Rating keys: only with the rail mounted, never with a modifier held.
  if (props.allowDestructive && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (e.key >= '1' && e.key <= '6' && e.key.length === 1) {
      toggleReason(Number(e.key) - 1)
      return
    }
    switch (e.key) {
      case 'f':
      case 'F':
        toggleFavorite()
        return
      case 'x':
      case 'X':
        toggleVerdict('pass')
        return
      case 'c':
      case 'C':
        // Keep the "c" from landing in the picker input focused next.
        e.preventDefault()
        openPicker()
        return
    }
  }

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
      if ((e.ctrlKey || e.metaKey) && props.allowDestructive) {
        e.preventDefault()
        deleteCurrent()
      }
      break
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
  if (props.allowDestructive) {
    categoriesStore.load().catch((e) => console.error('Failed to load categories', e))
  }
})
onUnmounted(() => {
  unmounted = true
  window.removeEventListener('keydown', onKeyDown)
  if (seenTimer) clearTimeout(seenTimer)
  if (hintTimer) clearTimeout(hintTimer)
})

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
            v-if="allowDestructive"
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
          <button
            v-if="allowDestructive"
            class="icon-btn delete-btn"
            @click="deleteCurrent"
            title="Delete (Ctrl+D)"
          >
            🗑
          </button>
          <button class="icon-btn close-btn" @click="emit('close')" title="Close (Esc)">✕</button>
        </div>
      </div>

      <!-- Main display area -->
      <div class="viewer-body">
        <!-- Wrapper div carries the scoped class because Galleria has
             inheritAttrs: false and drops class/style from its root. -->
        <div class="media-galleria">
          <Galleria
            v-model:activeIndex="currentIndex"
            :value="mediaList"
            :show-item-navigators="true"
            :show-thumbnails="true"
            :num-visible="7"
          >
            <template #item="{ item }">
              <div class="galleria-item">
                <VideoPlayer
                  v-if="(item as Media).is_video"
                  ref="videoPlayerRef"
                  :file-path="(item as Media).file_path"
                  :playback-speed="(item as Media).playback_speed"
                  @speed-change="onSpeedChange"
                  @play="onVideoPlay((item as Media).file_path)"
                />
                <ImageViewer
                  v-else
                  :file-path="(item as Media).file_path"
                />
              </div>
            </template>
            <template #thumbnail="{ item }">
              <LazyThumb
                :src="thumbnailUrl((item as Media).file_path)"
                :alt="(item as Media).file_name ?? fileName((item as Media).file_path)"
              />
            </template>
          </Galleria>
        </div>
        <RatingRail
          v-if="allowDestructive"
          :judgment="railJudgment"
          :hint="hint"
          :sticky="stickyCategory"
        >
          <CategoryPicker
            v-if="pickerOpen"
            :assigned="assignedIds"
            @toggle="toggleCategory"
            @create="createAndAddCategory"
            @sticky="toggleSticky"
            @rename="renameCategory"
            @delete="deleteCategory"
            @close="closePicker"
          />
        </RatingRail>
      </div>

      <!-- Info bar -->
      <div v-if="current" class="viewer-info">
        <span>{{ current.file_name ?? fileName(current.file_path) }}</span>
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
              <tr v-if="allowDestructive"><td>F</td><td>Toggle favorite</td></tr>
              <tr v-if="allowDestructive"><td>X</td><td>Pass</td></tr>
              <tr v-if="allowDestructive">
                <td>1–6</td>
                <td>Toggle reason (realism, composition, emotion, light, concept, motion)</td>
              </tr>
              <tr v-if="allowDestructive"><td>C</td><td>Categories (Shift+Enter: sticky)</td></tr>
              <tr v-if="allowDestructive"><td>Ctrl+D</td><td>Delete file</td></tr>
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

.media-galleria {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: stretch;
  color: #fff;
}

.media-galleria :deep(.p-galleria) {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.media-galleria :deep(.p-galleria-content) {
  flex: 1;
  min-height: 0;
}

.media-galleria :deep(.p-galleria-items-container) {
  flex: 1;
  min-height: 0;
}

.media-galleria :deep(.p-galleria-item) {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
}

.galleria-item {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
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
