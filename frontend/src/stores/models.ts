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
  stopInference,
  testHfToken,
  type HardwareInfo,
  type InferenceState,
  type InferenceStatusPayload,
  type ModelRow,
} from '../api/models'
import {
  fetchVlmStatus,
  fetchVlmRoles,
  setVlmRole,
  type VlmRole,
  type VlmRolesSnapshot,
  setActiveVlm,
  unloadVlm,
  type VlmState,
  type VlmStatus,
} from '../api/vlm'
import type { Gate, Tier } from '../types/hardware'
import { ApiError } from '../api/client'
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

  // Per-role pickers. `vlmState`/`vlmModelId` above stay the *general* role's
  // client; a WS payload tagged with roles that exclude 'general' belongs to
  // the (separate) writer client and only updates `vlmRoles`.
  const vlmRoles = ref<VlmRolesSnapshot | null>(null)
  // A failed role change and a failed roles fetch are separate: a later
  // successful refetch must not wipe the change error the user is reading.
  const vlmRolesChangeError = ref<string | null>(null)
  const vlmRolesFetchError = ref<string | null>(null)
  const vlmRolesError = computed(() => vlmRolesChangeError.value ?? vlmRolesFetchError.value)
  // Monotonic token: only the newest request may write vlmRoles.
  let vlmRolesSeq = 0

  async function loadVlmRoles(): Promise<void> {
    const seq = ++vlmRolesSeq
    try {
      const snap = await fetchVlmRoles()
      if (seq !== vlmRolesSeq) return
      vlmRoles.value = snap
      vlmRolesFetchError.value = null
    } catch (e) {
      if (seq !== vlmRolesSeq) return
      vlmRolesFetchError.value = vlmRoleErrorText(e)
    }
  }

  function vlmRoleErrorText(e: unknown): string {
    if (e instanceof ApiError) {
      const d = e.detail as { code?: string; message?: string } | string | null
      const code = d && typeof d === 'object' ? d.code : undefined
      const msg = d && typeof d === 'object' ? d.message : undefined
      if (code === 'writer_model_not_allowed') {
        return msg || 'That model cannot be the prompt writer.'
      }
      if (code === 'model_not_installed') {
        return msg ? `${msg} — download it first.` : 'That model is not installed — download it first.'
      }
      if (code === 'unknown_model') return msg || 'Unknown model.'
      return msg || e.message
    }
    return e instanceof Error ? e.message : String(e)
  }

  async function setVlmRoleModel(role: VlmRole, modelId: string): Promise<boolean> {
    vlmRolesChangeError.value = null
    const seq = ++vlmRolesSeq
    try {
      const snap = await setVlmRole(role, modelId)
      // Invalidate fetches started before this response; keep it if a newer
      // fetch already started after it was sent.
      if (seq === vlmRolesSeq) vlmRoles.value = snap
      loadStatus().catch((e) => {
        console.error('loadStatus after role change failed', e)
        vlmRolesFetchError.value = vlmRoleErrorText(e)
      })
      return true
    } catch (e) {
      vlmRolesChangeError.value = vlmRoleErrorText(e)
      return false
    }
  }

  function applyVlmPayload(p: Partial<VlmStatus>) {
    // Any client's state change can move a role row (state / loaded model).
    if (p.state) void loadVlmRoles()
    if (p.roles && !p.roles.includes('general')) return
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
        loadStatus().catch((e) => console.error('loadStatus after download failed', e))
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
        // Generic VLM progress events from retag jobs etc. Writer-only (or
        // retired-client) progress must not overwrite the general role's.
        {
          const roles = (data as { roles?: string[] }).roles
          if (roles && !roles.includes('general')) break
        }
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

  async function unloadVlmModel(modelId: string): Promise<void> {
    try {
      const snap = await unloadVlm(modelId)
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

  async function stopInferenceWorker(): Promise<void> {
    try {
      const snap = await stopInference()
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
    vlmRoles,
    vlmRolesError,
    loadVlmRoles,
    setVlmRoleModel,
    setActiveVlmModel,
    unloadVlmModel,
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
    stopInferenceWorker,
    removeCache,
    rebuildIndex,
  }
})
