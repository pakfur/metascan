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
import { useTasteStore } from '../../stores/taste'
import { useToast } from '../../composables/useToast'
import { ApiError } from '../../api/client'
import { fetchJudgment, markSeen, setVerdict, setReason } from '../../api/judgments'
import { addItems, removeItems } from '../../api/categories'
import { REASONS, SEEN_DWELL_MS } from '../../types/judgments'
import type { CategoryRef, JudgmentState, Verdict } from '../../types/judgments'

// allowDestructive defaults to true so every existing (desktop library)
// mount stays byte-identical. The storyboard candidate picker
// (ShotHeader.vue) passes false: its mediaList is synthesized from
// BeatImage rows (stubbed is_favorite, no real Media row for
// mediaStore.selectMedia to resolve), so real-file delete, the favorite
// toggle, and the library-metadata-panel selectMedia leak all need to be
// off there.
//
// allowRating turns the taste-rating surface (the rail, F/X, 1–6, C, the
// header star and seen tracking) on for a non-destructive mount. Rating is
// enabled when allowRating OR allowDestructive is true -- i.e. it
// effectively defaults to allowDestructive (Vue casts an absent boolean
// prop to false, so a true "undefined = inherit" default isn't
// expressible), and every existing mount is unchanged. The T2I and I2V
// result viewers pass allowDestructive=false (no delete / library side
// effects) but allowRating=true: their items are real library files
// (the strip lists only rows JOINed to a live media row), and reviewing
// new generations is exactly where ratings matter most.
//
// favoriteMirror lets a mount whose mediaList is a local copy (T2I/I2V
// keep their own is_favorite) follow every verdict the server returns, so
// the dialog's tile star stays correct. It is a plain callback prop, not
// an emit, because responses can land after the viewer has closed.
const props = withDefaults(
  defineProps<{
    mediaList: Media[]
    initialIndex: number
    allowDestructive?: boolean
    allowRating?: boolean
    favoriteMirror?: (filePath: string, isFavorite: boolean) => void
    // Every judgment state the server returns (a T2I mount follows the
    // pass flag too: it un-blinds scorecard tiles).
    judgmentMirror?: (filePath: string, state: JudgmentState) => void
  }>(),
  { allowDestructive: true, allowRating: false, favoriteMirror: undefined, judgmentMirror: undefined },
)

const ratingEnabled = computed(() => props.allowRating || props.allowDestructive)

const emit = defineEmits<{
  close: []
}>()

const mediaStore = useMediaStore()
const currentIndex = ref(props.initialIndex)
const videoPlayerRef = ref<InstanceType<typeof VideoPlayer> | null>(null)
const showHelp = ref(false)
const undoData = ref<{ media: Media; index: number } | null>(null)

// Review mode (R, rating only): navigation walks the scorer's
// least-certain images instead of mediaList. The state is this viewer's
// own; closing the viewer simply drops it. `returnPath` / `returnIndex`
// remember where the original list was so leaving review puts the owner
// back on the item they were rating.
interface ReviewState {
  list: Media[]
  returnPath: string | null
  returnIndex: number
}
const review = ref<ReviewState | null>(null)
let reviewLoading = false

// The list the viewer is navigating right now.
const activeList = computed<Media[]>(() => review.value?.list ?? props.mediaList)

const current = computed(() => activeList.value[currentIndex.value] ?? null)

const positionLabel = computed(() => {
  const pos = `${currentIndex.value + 1} / ${activeList.value.length}`
  return review.value ? `Review · ${pos}` : pos
})

watch(current, (media) => {
  if (media && props.allowDestructive) mediaStore.selectMedia(media)
})

// Navigation
function navigate(direction: number) {
  const newIdx = currentIndex.value + direction
  if (newIdx >= 0 && newIdx < activeList.value.length) {
    currentIndex.value = newIdx
    undoData.value = null
  } else if (review.value && newIdx >= activeList.value.length) {
    // Stepping past the last review item ends review.
    exitReview()
  }
}

function goNext() { navigate(1) }
function goPrev() { navigate(-1) }

// ---------------------------------------------------------------------
// Taste rating (only when ratingEnabled). The viewer owns every
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
const toast = useToast()
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

function receive(path: string, incoming: JudgmentState): void {
  // Only GET carries `batch`; a PATCH / seen response omits the key, so
  // keep the position already known instead of wiping it.
  const prev = lastKnown.get(path)
  let state =
    incoming.batch === undefined && prev?.batch !== undefined
      ? { ...incoming, batch: prev.batch }
      : incoming
  // `scorecard` too is GET-only.
  if (state.scorecard === undefined && prev?.scorecard !== undefined) {
    state = { ...state, scorecard: prev.scorecard }
  }
  lastKnown.set(path, state)
  refreshBlindDetails(path, state)
  // The server's flag is the truth whether or not this item is still on
  // screen, so the grid star follows even for a stale response.
  mediaStore.applyFavoriteFlag(path, state.is_favorite)
  props.favoriteMirror?.(path, state.is_favorite)
  props.judgmentMirror?.(path, state)
  if (!unmounted && path === currentPath.value) judgment.value = state
}

// A scorecard image is judged blind: no writer, prompt or variant until it
// has an explicit verdict (favorite or pass).
function isBlind(state: JudgmentState | null): boolean {
  return !!state?.scorecard && state.verdict === null && !state.is_favorite
}
const scorecardBlind = computed(() => isBlind(judgment.value))

// The library details panel fetched the blind record: once the verdict
// lifts the blind, fetch it again so the prompt shows.
function refreshBlindDetails(path: string, state: JudgmentState): void {
  if (!props.allowDestructive || unmounted) return
  const selected = mediaStore.selectedMedia
  if (!selected?.scorecard_blind || selected.file_path !== path || isBlind(state)) return
  const media = current.value
  if (media && media.file_path === path) void mediaStore.selectMedia(media)
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
  let message = 'Save failed — see console'
  if (code === 'duplicate_name') message = 'A category with that name exists'
  else if (code === 'invalid_name') message = 'Category names are 1–40 characters'
  if (!unmounted && path === currentPath.value) {
    showHint(path, message)
    return
  }
  // The usual rhythm is F then → at once, so a failed write almost always
  // lands after the item has left the screen (or the viewer has closed).
  // The rail can't show it any more; a toast makes sure a lost rating is
  // never silent.
  toast.show(`Rating for ${fileName(path)} failed to save`, 'warn', 5000)
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
    if (!ratingEnabled.value || !path) return
    enqueue(path, () => fetchJudgment(path))
    // Review mode records only explicit F / X / reasons / categories:
    // arrowing past an item there is not a "seen and not favorited" vote.
    if (!current.value?.is_video && !review.value) {
      seenTimer = setTimeout(() => {
        seenTimer = null
        if (path !== currentPath.value || review.value) return
        seenSentForVisit = true
        // The score on screen when the dwell completed.
        const scoreShown = shownScore()
        enqueue(path, () => markSeen(path, scoreShown))
      }, SEEN_DWELL_MS)
    }
  },
  { immediate: true },
)

function onVideoPlay(path: string): void {
  if (!ratingEnabled.value || path !== currentPath.value || seenSentForVisit) return
  if (review.value) return // review mode: explicit verdicts only
  seenSentForVisit = true
  enqueue(path, () => markSeen(path))
}

// The taste score the rail shows for the current item: images only, and
// only while the rail is mounted.
const tasteStore = useTasteStore()
const railScore = computed<number | null>(() => {
  const media = current.value
  if (!ratingEnabled.value || !media || media.is_video) return null
  return tasteStore.scoreOf(media.file_path)
})

// Captured when the key is pressed, not when the request goes out: a write
// queued behind earlier ops must still report what the owner was looking
// at. Sent as the two-decimal value the rail displayed.
function shownScore(): number | undefined {
  const score = railScore.value
  return score === null ? undefined : Number(score.toFixed(2))
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
  if (!ratingEnabled.value || !media) return
  const path = media.file_path
  const opts = verdictOpts()
  const scoreShown = shownScore()
  enqueue(path, (state) => {
    const isTarget = state
      ? state.verdict === target
      : target === 'favorite' && media.is_favorite
    return setVerdict(path, isTarget ? null : target, {
      ...(isTarget ? {} : opts),
      ...(scoreShown === undefined ? {} : { scoreShown }),
    })
  })
}

function toggleFavorite(): void {
  toggleVerdict('favorite')
}

// The header star follows the server's answer for the current item when
// there is one: a T2I/I2V mediaList is a local copy whose is_favorite
// never changes under the viewer.
const starOn = computed(() => judgment.value?.is_favorite ?? current.value?.is_favorite ?? false)

function toggleReason(index: number): void {
  const reason = REASONS[index]
  if (!ratingEnabled.value || !currentPath.value || !reason) return
  const path = currentPath.value
  const scoreShown = shownScore()
  enqueue(path, async (state) => {
    if (!state?.verdict) {
      showHint(path, 'Press F or X first')
      return null
    }
    return setReason(path, reason, !state.reasons.includes(reason), scoreShown)
  })
}

// Review mode
async function toggleReview(): Promise<void> {
  if (!ratingEnabled.value) return
  if (review.value) {
    exitReview()
    return
  }
  if (reviewLoading) return
  const startPath = currentPath.value
  reviewLoading = true
  let paths: string[]
  try {
    paths = await tasteStore.reviewQueue()
  } catch (e) {
    console.error('Failed to load review queue', e)
    if (startPath) showHint(startPath, 'Review queue failed — see console')
    return
  } finally {
    reviewLoading = false
  }
  // The owner may have moved on (or closed the viewer) while it loaded.
  if (unmounted || review.value || currentPath.value !== startPath) return
  const byPath = new Map(mediaStore.allMedia.map((m) => [m.file_path, m]))
  const list = paths
    .map((p) => byPath.get(p))
    .filter((m): m is Media => m !== undefined)
  if (!list.length) {
    if (startPath) showHint(startPath, 'Nothing to review — rate more or retrain')
    return
  }
  review.value = { list, returnPath: startPath, returnIndex: currentIndex.value }
  currentIndex.value = 0
  undoData.value = null
}

function exitReview(): void {
  const state = review.value
  if (!state) return
  review.value = null
  undoData.value = null
  const list = props.mediaList
  const found = state.returnPath
    ? list.findIndex((m) => m.file_path === state.returnPath)
    : -1
  // The original item may have gone (deleted, list reloaded): fall back to
  // the nearest index.
  currentIndex.value =
    found >= 0 ? found : Math.max(0, Math.min(state.returnIndex, list.length - 1))
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
  if (!ratingEnabled.value || !current.value) return
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

  // The review list is the viewer's own copy, so drop the file from it too.
  if (review.value) {
    const list = review.value.list.filter((m) => m.file_path !== deletedMedia.file_path)
    if (!list.length) {
      exitReview()
      return
    }
    review.value = { ...review.value, list }
  }

  // Navigate after delete
  if (activeList.value.length === 0) {
    emit('close')
    return
  }
  if (currentIndex.value >= activeList.value.length) {
    currentIndex.value = activeList.value.length - 1
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
  if (ratingEnabled.value && !e.ctrlKey && !e.metaKey && !e.altKey) {
    // A held F / X / 1–6 / C must not toggle over and over (auto-repeat);
    // held arrows and volume keys still repeat below.
    if (e.repeat && /^[fFxXcCrR1-6]$/.test(e.key)) return
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
      case 'r':
      case 'R':
        void toggleReview()
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
  if (ratingEnabled.value) {
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
            v-if="ratingEnabled"
            class="fav-btn"
            :class="{ active: starOn }"
            @click="toggleFavorite"
            :title="starOn ? 'Remove favorite' : 'Add favorite'"
          >
            {{ starOn ? '★' : '☆' }}
          </button>
          <span class="position-label">{{ positionLabel }}</span>
          <span v-if="scorecardBlind" class="blind-chip" data-testid="scorecard-blind">
            Scorecard image — judge to reveal (F or X)
          </span>
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
            :value="activeList"
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
          v-if="ratingEnabled"
          :judgment="railJudgment"
          :hint="hint"
          :sticky="stickyCategory"
          :score="railScore"
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
              <tr v-if="ratingEnabled"><td>F</td><td>Toggle favorite</td></tr>
              <tr v-if="ratingEnabled"><td>X</td><td>Pass</td></tr>
              <tr v-if="ratingEnabled">
                <td>1–6</td>
                <td>Toggle reason (realism, composition, emotion, light, concept, motion)</td>
              </tr>
              <tr v-if="ratingEnabled"><td>C</td><td>Categories (Shift+Enter: sticky)</td></tr>
              <tr v-if="ratingEnabled"><td>R</td><td>Review least-certain</td></tr>
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

.blind-chip {
  padding: 1px 8px;
  border-radius: 10px;
  background: rgba(201, 162, 39, 0.85);
  color: #111;
  font-size: 12px;
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
