import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  clearHfToken,
  deleteModel,
  downloadModel,
  fetchHardware,
  fetchHfTokenStatus,
  fetchInferenceStatus,
  fetchModelsStatus,
  rebuildEmbeddingIndex,
  setHfToken,
  setPreload,
  startInference,
  testHfToken,
  type HardwareInfo,
  type InferenceState,
  type InferenceStatusPayload,
  type ModelRow,
} from '../api/models'
import { useWebSocket } from '../composables/useWebSocket'

export const useModelsStore = defineStore('models', () => {
  const models = ref<ModelRow[]>([])
  const hardware = ref<HardwareInfo | null>(null)
  const hfTokenSet = ref(false)
  const currentClipModel = ref<string>('small')
  const currentClipDim = ref<number>(0)

  // Per-model transient state for rows with an in-flight download.
  // Keyed by model id; cleared on download_complete or download_error.
  const downloading = ref<Record<string, { stage: string; percent: number }>>({})
  const downloadErrors = ref<Record<string, string>>({})

  // Live inference worker state — drives the content-search status chip and
  // submit gating. Bootstrapped from GET /models/inference-status and then
  // kept live by the 'models' WS channel.
  const inferenceState = ref<InferenceState>('idle')
  const inferenceModelKey = ref<string | null>(null)
  const inferenceDevice = ref<string | null>(null)
  const inferenceDim = ref<number | null>(null)
  const inferenceProgress = ref<{ stage?: string; percent?: number | null }>({})
  const inferenceError = ref<string | null>(null)

  const isInferenceReady = computed(() => inferenceState.value === 'ready')
  const isInferenceLoading = computed(
    () =>
      inferenceState.value === 'loading' || inferenceState.value === 'spawning',
  )

  useWebSocket('models', (event, data) => {
    switch (event) {
      case 'inference_status':
        applyInferencePayload(data as unknown as InferenceStatusPayload)
        break
      case 'inference_progress': {
        const p = data as { stage?: string; percent?: number | null }
        inferenceProgress.value = p
        break
      }
      case 'download_progress': {
        const p = data as { id: string; stage?: string; percent?: number }
        if (!p.id) break
        downloading.value[p.id] = {
          stage: p.stage ?? 'downloading',
          percent: typeof p.percent === 'number' ? p.percent : 0,
        }
        delete downloadErrors.value[p.id]
        break
      }
      case 'download_complete': {
        const p = data as { id: string }
        if (!p.id) break
        delete downloading.value[p.id]
        delete downloadErrors.value[p.id]
        // Refresh status so the row flips from 'missing' to 'available'.
        void loadStatus()
        break
      }
      case 'download_error': {
        const p = data as { id: string; error?: string }
        if (!p.id) break
        delete downloading.value[p.id]
        downloadErrors.value[p.id] = p.error || 'download failed'
        break
      }
    }
  })

  function applyInferencePayload(p: Partial<InferenceStatusPayload>) {
    if (p.state) inferenceState.value = p.state as InferenceState
    if ('model_key' in p) inferenceModelKey.value = p.model_key ?? null
    if ('device' in p) inferenceDevice.value = p.device ?? null
    if ('dim' in p) inferenceDim.value = p.dim ?? null
    if (p.progress) inferenceProgress.value = p.progress
    if ('error' in p) inferenceError.value = p.error ?? null
  }

  async function loadStatus() {
    const [statusResp, hw, infer] = await Promise.all([
      fetchModelsStatus(),
      fetchHardware(),
      fetchInferenceStatus(),
    ])
    models.value = statusResp.models
    hfTokenSet.value = statusResp.hf_token_set
    currentClipModel.value = statusResp.current_clip_model
    currentClipDim.value = statusResp.current_clip_dim
    hardware.value = hw
    applyInferencePayload(infer)
  }

  async function refreshHfToken() {
    const r = await fetchHfTokenStatus()
    hfTokenSet.value = r.set
  }

  async function togglePreload(id: string, enabled: boolean) {
    await setPreload(id, enabled)
    const row = models.value.find((m) => m.id === id)
    if (row) row.preload_at_startup = enabled
  }

  async function saveHfToken(token: string) {
    const r = await setHfToken(token)
    hfTokenSet.value = r.set
  }

  async function removeHfToken() {
    const r = await clearHfToken()
    hfTokenSet.value = r.set
  }

  async function verifyHfToken(): Promise<{ ok: boolean; name?: string; error?: string }> {
    return await testHfToken()
  }

  async function startDownload(id: string) {
    downloading.value[id] = { stage: 'starting', percent: 0 }
    delete downloadErrors.value[id]
    try {
      await downloadModel(id)
    } catch (e) {
      delete downloading.value[id]
      downloadErrors.value[id] = e instanceof Error ? e.message : String(e)
    }
  }

  async function startDownloadAllMissing() {
    const missing = models.value.filter((m) => m.status === 'missing')
    for (const m of missing) {
      // Fire in parallel; the backend dispatches each on its own asyncio task.
      void startDownload(m.id)
    }
  }

  async function removeCache(id: string) {
    await deleteModel(id)
    await loadStatus()
  }

  async function rebuildIndex() {
    await rebuildEmbeddingIndex()
  }

  async function startInferenceWorker(): Promise<void> {
    // Optimistically flip to spawning so the UI chip updates immediately;
    // the real state will arrive on the 'models' WS channel once the
    // backend starts the subprocess.
    inferenceState.value = 'spawning'
    try {
      const snap = await startInference()
      applyInferencePayload(snap)
    } catch (e) {
      inferenceError.value = e instanceof Error ? e.message : String(e)
      inferenceState.value = 'error'
    }
  }

  return {
    models,
    hardware,
    hfTokenSet,
    currentClipModel,
    currentClipDim,
    downloading,
    downloadErrors,
    inferenceState,
    inferenceModelKey,
    inferenceDevice,
    inferenceDim,
    inferenceProgress,
    inferenceError,
    isInferenceReady,
    isInferenceLoading,
    loadStatus,
    refreshHfToken,
    togglePreload,
    saveHfToken,
    removeHfToken,
    verifyHfToken,
    startDownload,
    startDownloadAllMissing,
    startInferenceWorker,
    removeCache,
    rebuildIndex,
  }
})
