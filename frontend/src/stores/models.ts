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
import { fetchVlmStatus, setActiveVlm, type VlmState, type VlmStatus } from '../api/vlm'
import type { Gate, Tier } from '../types/hardware'
import { useWebSocket } from '../composables/useWebSocket'

export const useModelsStore = defineStore('models', () => {
  const models = ref<ModelRow[]>([])
  const hardware = ref<HardwareInfo | null>(null)
  const hfTokenSet = ref(false)
  const currentClipModel = ref<string>('small')
  const currentClipDim = ref<number>(0)
  const tier = ref<Tier>('cpu_only')
  const gates = ref<Record<string, Gate>>({})

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

  // VLM (Qwen3-VL tagging) state — bootstrapped from /api/vlm/status, kept
  // live by the 'models' WS channel's vlm_status events.
  const vlmState = ref<VlmState>('idle')
  const vlmModelId = ref<string | null>(null)
  const vlmBaseUrl = ref<string | null>(null)
  const vlmError = ref<string | null>(null)
  const vlmProgress = ref<Record<string, unknown>>({})

  const isVlmReady = computed(() => vlmState.value === 'ready')
  const isVlmLoading = computed(
    () => vlmState.value === 'loading' || vlmState.value === 'spawning',
  )

  function applyVlmPayload(p: Partial<VlmStatus>) {
    if (p.state) vlmState.value = p.state
    if ('model_id' in p) vlmModelId.value = p.model_id ?? null
    if ('base_url' in p) vlmBaseUrl.value = p.base_url ?? null
    if ('error' in p) vlmError.value = p.error ?? null
    if (p.progress) vlmProgress.value = p.progress
  }

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
      case 'vlm_status':
        applyVlmPayload(data as unknown as VlmStatus)
        break
      case 'vlm_progress':
        // Generic VLM progress events from retag jobs etc.
        vlmProgress.value = data as Record<string, unknown>
        break
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
    const [statusResp, hw, infer, vlm] = await Promise.all([
      fetchModelsStatus(),
      fetchHardware(),
      fetchInferenceStatus(),
      fetchVlmStatus(),
    ])
    models.value = statusResp.models
    hfTokenSet.value = statusResp.hf_token_set
    currentClipModel.value = statusResp.current_clip_model
    currentClipDim.value = statusResp.current_clip_dim
    tier.value = statusResp.tier
    gates.value = statusResp.gates
    hardware.value = hw
    applyInferencePayload(infer)
    applyVlmPayload(vlm)
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

  function gateFor(id: string): Gate | null {
    return gates.value[id] ?? null
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

  async function setActiveVlmModel(modelId: string): Promise<void> {
    // Optimistic UI: flip the loading state immediately; the real state will
    // arrive on the WS channel once the swap completes.
    vlmState.value = 'spawning'
    vlmModelId.value = modelId
    try {
      const snap = await setActiveVlm(modelId)
      applyVlmPayload(snap)
    } catch (e) {
      vlmError.value = e instanceof Error ? e.message : String(e)
      vlmState.value = 'error'
    }
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
    tier,
    gates,
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
    vlmState,
    vlmModelId,
    vlmBaseUrl,
    vlmError,
    vlmProgress,
    isVlmReady,
    isVlmLoading,
    setActiveVlmModel,
    loadStatus,
    refreshHfToken,
    togglePreload,
    saveHfToken,
    removeHfToken,
    verifyHfToken,
    gateFor,
    startDownload,
    startDownloadAllMissing,
    startInferenceWorker,
    removeCache,
    rebuildIndex,
  }
})
