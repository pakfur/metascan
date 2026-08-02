import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  Panel,
  PanelImage,
  Scene,
  StoryboardSummary,
  StoryboardTree,
  Subject,
  JobState,
} from '../types/storyboard'
import * as api from '../api/storyboard'
import * as comfyApi from '../api/comfy'
import { useWebSocket } from '../composables/useWebSocket'

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export const useStoryboardStore = defineStore('storyboard', () => {
  // ---- state ----------------------------------------------------------
  const list = ref<StoryboardSummary[]>([])
  const tree = ref<StoryboardTree | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const selectedSceneId = ref<number | null>(null)
  const selectedPanelId = ref<number | null>(null)
  const jobToPanel = ref<Map<number, number>>(new Map())
  const panelJobState = ref<
    Map<number, { state: JobState; error: string | null; value?: number; max?: number }>
  >(new Map())
  const synthesis = ref<{ running: boolean; done: number; total: number; error: string | null }>({
    running: false,
    done: 0,
    total: 0,
    error: null,
  })

  // ---- getters ----------------------------------------------------------
  const selectedScene = computed<Scene | null>(() => {
    if (!tree.value || selectedSceneId.value == null) return null
    return tree.value.scenes.find((s) => s.id === selectedSceneId.value) ?? null
  })

  const selectedPanel = computed<Panel | null>(() => {
    const scene = selectedScene.value
    if (!scene || selectedPanelId.value == null) return null
    return scene.panels.find((p) => p.id === selectedPanelId.value) ?? null
  })

  const subjectsById = computed<Map<number, Subject>>(() => {
    const m = new Map<number, Subject>()
    if (tree.value) {
      for (const s of tree.value.subjects) m.set(s.id, s)
    }
    return m
  })

  function panelById(id: number): Panel | null {
    if (!tree.value) return null
    for (const scene of tree.value.scenes) {
      const found = scene.panels.find((p) => p.id === id)
      if (found) return found
    }
    return null
  }

  function keeperImage(panel: Panel): PanelImage | null {
    return panel.images.find((i) => i.id === panel.selected_image_id) ?? null
  }

  // Pick a default scene/panel selection for a freshly (re)loaded tree —
  // used by load() on first entry. refresh() has its own, selection-
  // preserving version of this logic (see below).
  function selectDefaults(t: StoryboardTree): void {
    const firstScene = t.scenes[0] ?? null
    selectedSceneId.value = firstScene ? firstScene.id : null
    selectedPanelId.value = firstScene && firstScene.panels[0] ? firstScene.panels[0].id : null
  }

  // ---- actions: load / refresh ------------------------------------------

  async function loadList(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      list.value = await api.listStoryboards()
    } catch (e) {
      error.value = errMessage(e)
    } finally {
      loading.value = false
    }
  }

  // Fetch the tree, default-select the first scene/panel, then pull any
  // in-flight comfy jobs so a mid-generation reload still shows progress.
  async function load(id: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const t = await api.fetchStoryboard(id)
      tree.value = t
      selectDefaults(t)
      await refreshActiveJobs()
    } catch (e) {
      error.value = errMessage(e)
    } finally {
      loading.value = false
    }
  }

  // Re-fetch the current tree in place, preserving selection where the
  // selected scene/panel still exists; falls back to selectDefaults()
  // otherwise (e.g. the selected panel was deleted server-side).
  async function refresh(): Promise<void> {
    if (!tree.value) return
    const id = tree.value.id
    try {
      const t = await api.fetchStoryboard(id)
      tree.value = t
      const scene = selectedSceneId.value != null
        ? t.scenes.find((s) => s.id === selectedSceneId.value)
        : undefined
      if (!scene) {
        selectDefaults(t)
        return
      }
      const panel = selectedPanelId.value != null
        ? scene.panels.find((p) => p.id === selectedPanelId.value)
        : undefined
      if (!panel) {
        selectedPanelId.value = scene.panels[0] ? scene.panels[0].id : null
      }
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  // ---- actions: storyboard CRUD ------------------------------------------

  async function create(body: Parameters<typeof api.createStoryboard>[0]): Promise<number> {
    const res = await api.createStoryboard(body)
    await loadList()
    return res.id
  }

  async function remove(id: number): Promise<void> {
    await api.deleteStoryboard(id)
    list.value = list.value.filter((s) => s.id !== id)
    if (tree.value?.id === id) {
      tree.value = null
      selectedSceneId.value = null
      selectedPanelId.value = null
    }
  }

  async function patchStoryboardFields(
    body: Parameters<typeof api.patchStoryboard>[1],
  ): Promise<void> {
    if (!tree.value) return
    const id = tree.value.id
    const snapshot = { ...tree.value }
    Object.assign(tree.value, body)
    try {
      await api.patchStoryboard(id, body)
    } catch (e) {
      tree.value = snapshot
      error.value = errMessage(e)
    }
  }

  async function patchPanelFields(panelId: number, body: Record<string, unknown>): Promise<void> {
    const panel = panelById(panelId)
    if (!panel) return
    const snapshot = { ...panel }
    Object.assign(panel, body)
    try {
      // patchPanel returns PanelWithoutImages -- Object.assign only touches
      // keys present on the response, so the local `images` array (absent
      // from the response) is left untouched.
      const res = await api.patchPanel(panelId, body)
      Object.assign(panel, res)
    } catch (e) {
      Object.assign(panel, snapshot)
      error.value = errMessage(e)
    }
  }

  async function patchSceneFields(sceneId: number, body: Record<string, unknown>): Promise<void> {
    if (!tree.value) return
    const scene = tree.value.scenes.find((s) => s.id === sceneId)
    if (!scene) return
    const snapshot = { ...scene }
    Object.assign(scene, body)
    try {
      await api.patchScene(sceneId, body)
    } catch (e) {
      Object.assign(scene, snapshot)
      error.value = errMessage(e)
    }
  }

  async function addScene(name: string): Promise<void> {
    if (!tree.value) return
    await api.createScene(tree.value.id, { name })
    await refresh()
  }

  async function addPanel(sceneId: number, action: string): Promise<void> {
    await api.createPanel(sceneId, { action })
    await refresh()
  }

  async function removeScene(id: number): Promise<void> {
    await api.deleteScene(id)
    await refresh()
  }

  async function removePanel(id: number): Promise<void> {
    await api.deletePanel(id)
    await refresh()
  }

  // Replaces the tree wholesale and resets selection. Deliberately rethrows
  // (ApiError in particular) so the import dialog can catch the 409
  // confirm_required response and re-call with confirm=true.
  async function importText(text: string, confirm: boolean): Promise<void> {
    if (!tree.value) return
    const id = tree.value.id
    try {
      const t = await api.parseStoryboard(id, text, confirm)
      tree.value = t
      selectDefaults(t)
    } catch (e) {
      error.value = errMessage(e)
      throw e
    }
  }

  // ---- actions: synthesis / generation ------------------------------------------

  async function synthesize(panelIds?: number[], force?: boolean): Promise<void> {
    if (!tree.value) return
    const body: { panel_ids?: number[]; force?: boolean } = {}
    if (panelIds !== undefined) body.panel_ids = panelIds
    if (force !== undefined) body.force = force
    const res = await api.synthesizeStoryboard(tree.value.id, body)
    synthesis.value = { running: true, done: 0, total: res.total, error: null }
  }

  async function generate(panelIds?: number[], onlyFailed?: boolean): Promise<void> {
    if (!tree.value) return
    const body: { panel_ids?: number[]; only_failed?: boolean } = {}
    if (panelIds !== undefined) body.panel_ids = panelIds
    if (onlyFailed !== undefined) body.only_failed = onlyFailed
    await api.generateStoryboard(tree.value.id, body)
    await refreshActiveJobs()
  }

  async function cancelAll(): Promise<void> {
    if (!tree.value) return
    await api.cancelStoryboard(tree.value.id)
    await refreshActiveJobs()
  }

  async function selectImage(panelId: number, imageId: number | null): Promise<void> {
    const res = await api.selectPanelImage(panelId, imageId)
    const panel = panelById(panelId)
    if (panel) Object.assign(panel, res)
  }

  // Rebuilds jobToPanel/panelJobState from the server's queued+running job
  // lists, filtered to panels belonging to the current tree. This is what
  // survives a page reload mid-generation -- there's no other durable
  // client-side record of in-flight jobs.
  async function refreshActiveJobs(): Promise<void> {
    if (!tree.value) {
      jobToPanel.value = new Map()
      panelJobState.value = new Map()
      return
    }
    const panelIds = new Set<number>()
    for (const scene of tree.value.scenes) {
      for (const p of scene.panels) panelIds.add(p.id)
    }
    const [queued, running] = await Promise.all([
      comfyApi.listJobs('queued', 1000),
      comfyApi.listJobs('running', 1000),
    ])
    const nextJobToPanel = new Map<number, number>()
    const nextPanelJobState = new Map<
      number,
      { state: JobState; error: string | null; value?: number; max?: number }
    >()
    for (const job of [...queued, ...running]) {
      if (job.panel_id == null || !panelIds.has(job.panel_id)) continue
      nextJobToPanel.set(job.id, job.panel_id)
      nextPanelJobState.set(job.panel_id, { state: job.state, error: null })
    }
    jobToPanel.value = nextJobToPanel
    panelJobState.value = nextPanelJobState
  }

  // ---- actions: subject CRUD ------------------------------------------

  async function addSubject(body: Parameters<typeof api.createSubject>[1]): Promise<void> {
    if (!tree.value) return
    await api.createSubject(tree.value.id, body)
    await refresh()
  }

  async function patchSubjectFields(subjectId: number, body: Record<string, unknown>): Promise<void> {
    await api.patchSubject(subjectId, body)
    await refresh()
  }

  async function removeSubject(id: number): Promise<void> {
    await api.deleteSubject(id)
    await refresh()
  }

  // ---- WS wiring ------------------------------------------------------
  //
  // useWebSocket() registers its cleanup via Vue's onUnmounted, which only
  // works while a component is actively being set up. The store instance
  // outlives any single view, so attachWs() must NOT be called once from
  // inside the store (there'd be no component context to unmount from,
  // and the handler would leak forever). Instead it is called directly
  // from StoryboardView's <script setup> body on every mount; there is
  // deliberately no "already attached" module-level guard here -- each
  // mount's call to useWebSocket registers a handler and gets its own
  // onUnmounted cleanup, so mount/unmount/remount cycles stay balanced.
  // A guard would do the opposite of what's needed: it would block the
  // second mount's subscription after the first mount's handler was torn
  // down.
  function attachWs(): void {
    useWebSocket('storyboard', (event, data) => {
      const d = data as Record<string, unknown>
      if (tree.value && d.storyboard_id !== tree.value.id) return
      if (event === 'synthesis_progress') {
        synthesis.value = {
          running: true,
          done: d.done as number,
          total: d.total as number,
          error: null,
        }
      } else if (event === 'synthesis_complete') {
        synthesis.value = {
          running: false,
          done: synthesis.value.total,
          total: synthesis.value.total,
          error: null,
        }
        void refresh()
      } else if (event === 'synthesis_error') {
        synthesis.value = {
          running: false,
          done: synthesis.value.done,
          total: synthesis.value.total,
          error: String(d.error ?? 'synthesis failed'),
        }
      } else if (event === 'panel_images_changed') {
        // No per-panel GET endpoint exists; a full tree refetch (which
        // preserves selection) is the simplest correct way to pick up the
        // new image set.
        void refresh()
      }
    })

    useWebSocket('comfy', (event, data) => {
      const d = data as Record<string, unknown>
      const jobId = d.job_id as number
      const panelId = jobToPanel.value.get(jobId)
      if (panelId === undefined) return
      if (event === 'job_update') {
        const state = d.state as JobState
        if (state === 'done' || state === 'cancelled') {
          panelJobState.value.delete(panelId)
        } else {
          panelJobState.value.set(panelId, { state, error: (d.error as string) ?? null })
        }
      } else if (event === 'job_progress') {
        panelJobState.value.set(panelId, {
          state: 'running',
          error: null,
          value: d.value as number,
          max: d.max as number,
        })
      }
      // job_outputs: ignored -- the storyboard channel's panel_images_changed
      // event is what carries the panel-scoped refresh.
    })
  }

  return {
    // state
    list,
    tree,
    loading,
    error,
    selectedSceneId,
    selectedPanelId,
    jobToPanel,
    panelJobState,
    synthesis,
    // getters
    selectedScene,
    selectedPanel,
    subjectsById,
    panelById,
    keeperImage,
    // actions
    loadList,
    load,
    refresh,
    create,
    remove,
    patchStoryboardFields,
    patchPanelFields,
    patchSceneFields,
    addScene,
    addPanel,
    removeScene,
    removePanel,
    importText,
    synthesize,
    generate,
    cancelAll,
    selectImage,
    refreshActiveJobs,
    addSubject,
    patchSubjectFields,
    removeSubject,
    attachWs,
  }
})
