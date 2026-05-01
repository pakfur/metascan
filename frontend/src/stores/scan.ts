import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { prepareScan, startScan, cancelScan, type ScanPrepareResult } from '../api/scan'
import { buildIndex, cancelIndex, fetchEmbeddingStatus } from '../api/similarity'
import { useWebSocket } from '../composables/useWebSocket'

export type ScanPhase =
  | 'idle'
  | 'preparing'
  | 'confirming'
  | 'scanning'
  | 'stale_cleanup'
  | 'embedding'
  | 'complete'
  | 'error'
  | 'cancelled'

export const useScanStore = defineStore('scan', () => {
  // Scan state
  const phase = ref<ScanPhase>('idle')
  const prepareResult = ref<ScanPrepareResult | null>(null)
  const fullCleanup = ref(false)
  const scanMode = ref<'incremental' | 'full_clean'>('incremental')
  const errorMessage = ref('')

  // Scan progress
  const currentDir = ref('')
  const dirCurrent = ref(0)
  const dirTotal = ref(0)
  const fileCurrent = ref(0)
  const fileTotal = ref(0)
  const currentFile = ref('')

  // Scan results
  const processedCount = ref(0)
  const staleRemoved = ref(0)

  // Embedding state
  const embeddingPhase = ref<'idle' | 'building' | 'complete' | 'error'>('idle')
  const embeddingCurrent = ref(0)
  const embeddingTotal = ref(0)
  const embeddingError = ref('')
  const embeddingStatus = ref<
    | 'idle'
    | 'starting'
    | 'downloading_model'
    | 'loading_model'
    | 'loading_vocab'
    | 'encoding_vocab'
    | 'processing'
  >('idle')
  const embeddingLabel = ref('')
  const embeddingCurrentFile = ref('')
  const embeddingErrorsCount = ref(0)
  const embeddingVocabCurrent = ref(0)
  const embeddingVocabTotal = ref(0)

  const isActive = computed(() =>
    phase.value !== 'idle' && phase.value !== 'complete' && phase.value !== 'error' && phase.value !== 'cancelled'
  )

  // Subscribe to scan WebSocket channel
  useWebSocket('scan', (event, data) => {
    switch (event) {
      case 'started':
        phase.value = 'scanning'
        break
      case 'progress':
        phase.value = 'scanning'
        currentDir.value = (data.directory as string) || ''
        dirCurrent.value = (data.dir_current as number) || 0
        dirTotal.value = (data.dir_total as number) || 0
        break
      case 'file_progress':
        fileCurrent.value = (data.current as number) || 0
        fileTotal.value = (data.total as number) || 0
        currentFile.value = (data.file as string) || ''
        break
      case 'phase_changed':
        if (data.phase === 'stale_cleanup') {
          phase.value = 'stale_cleanup'
        }
        break
      case 'complete':
        // Auto-index fires BEFORE scan/complete is broadcast, so an
        // 'embedding/started' event has likely already arrived and put
        // embeddingPhase into 'building'. In that case stay on the
        // 'embedding' step so the worker's progress is visible — the
        // 'embedding/complete' handler will move us to 'complete'.
        if (embeddingPhase.value === 'building') {
          phase.value = 'embedding'
        } else {
          phase.value = 'complete'
        }
        processedCount.value = (data.processed as number) || 0
        staleRemoved.value = (data.stale_removed as number) || 0
        break
      case 'error':
        phase.value = 'error'
        errorMessage.value = (data.message as string) || 'Unknown error'
        break
      case 'cancelled':
        phase.value = 'cancelled'
        break
    }
  })

  // Subscribe to embedding WebSocket channel
  useWebSocket('embedding', (event, data) => {
    switch (event) {
      case 'started':
        embeddingPhase.value = 'building'
        embeddingStatus.value = 'starting'
        embeddingLabel.value = ''
        embeddingCurrentFile.value = ''
        embeddingErrorsCount.value = 0
        embeddingError.value = ''
        embeddingVocabCurrent.value = 0
        embeddingVocabTotal.value = 0
        if (data.total !== undefined) {
          embeddingTotal.value = (data.total as number) || 0
        }
        if (phase.value === 'complete') phase.value = 'embedding'
        break
      case 'progress':
        embeddingCurrent.value = (data.current as number) || 0
        embeddingTotal.value = (data.total as number) || 0
        if (data.label) embeddingLabel.value = data.label as string
        if (data.status) embeddingStatus.value = data.status as typeof embeddingStatus.value
        if (data.current_file !== undefined) {
          embeddingCurrentFile.value = (data.current_file as string) || ''
        }
        if (data.errors_count !== undefined) {
          embeddingErrorsCount.value = (data.errors_count as number) || 0
        }
        if (data.vocab_current !== undefined) {
          embeddingVocabCurrent.value = (data.vocab_current as number) || 0
        }
        if (data.vocab_total !== undefined) {
          embeddingVocabTotal.value = (data.vocab_total as number) || 0
        }
        break
      case 'complete':
        embeddingPhase.value = 'complete'
        if (phase.value === 'embedding') phase.value = 'complete'
        break
      case 'cancelled':
        embeddingPhase.value = 'idle'
        if (phase.value === 'embedding') phase.value = 'complete'
        break
      case 'error':
        embeddingPhase.value = 'error'
        embeddingError.value = (data.message as string) || 'Unknown error'
        break
    }
  })

  async function prepare() {
    phase.value = 'preparing'
    try {
      prepareResult.value = await prepareScan()
      phase.value = 'confirming'
    } catch (e) {
      phase.value = 'error'
      errorMessage.value = (e as Error).message
    }
  }

  async function start() {
    phase.value = 'scanning'
    fileCurrent.value = 0
    fileTotal.value = 0
    currentFile.value = ''
    processedCount.value = 0
    staleRemoved.value = 0
    await startScan({
      full_cleanup: fullCleanup.value,
      full_clean: scanMode.value === 'full_clean',
    })
  }

  async function cancel() {
    await cancelScan()
    // Phase will be set to 'cancelled' by WebSocket event
  }

  function reset() {
    phase.value = 'idle'
    prepareResult.value = null
    fullCleanup.value = false
    errorMessage.value = ''
    fileCurrent.value = 0
    fileTotal.value = 0
    currentFile.value = ''
    processedCount.value = 0
    staleRemoved.value = 0
  }

  async function startEmbeddingBuild(rebuild = false) {
    embeddingPhase.value = 'building'
    embeddingCurrent.value = 0
    embeddingTotal.value = 0
    embeddingError.value = ''
    await buildIndex(rebuild)
  }

  async function cancelEmbedding() {
    try { await cancelIndex() } catch { /* 409 = already finished */ }
  }

  function resetEmbedding() {
    embeddingPhase.value = 'idle'
    embeddingStatus.value = 'idle'
    embeddingCurrent.value = 0
    embeddingTotal.value = 0
    embeddingLabel.value = ''
    embeddingCurrentFile.value = ''
    embeddingErrorsCount.value = 0
    embeddingVocabCurrent.value = 0
    embeddingVocabTotal.value = 0
    embeddingError.value = ''
  }

  return {
    phase,
    prepareResult,
    fullCleanup,
    scanMode,
    errorMessage,
    currentDir,
    dirCurrent,
    dirTotal,
    fileCurrent,
    fileTotal,
    currentFile,
    processedCount,
    staleRemoved,
    embeddingPhase,
    embeddingCurrent,
    embeddingTotal,
    embeddingError,
    embeddingStatus,
    embeddingLabel,
    embeddingCurrentFile,
    embeddingErrorsCount,
    embeddingVocabCurrent,
    embeddingVocabTotal,
    isActive,
    prepare,
    start,
    cancel,
    reset,
    startEmbeddingBuild,
    cancelEmbedding,
    resetEmbedding,
  }
})
