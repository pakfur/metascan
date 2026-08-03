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

  // Tracks which storyboard load() most recently targeted -- a NAVIGATION,
  // not just a re-fetch. refresh() reads this to detect it's stale (its
  // target board is one the user has since navigated away from) and bails
  // out *before* consuming a loadSeq token, so a refresh of the old board
  // can never supersede a newer load() of a different board.
  let currentBoardId: number | null = null

  // Monotonic counter shared by load()/refresh() that arbitrates which
  // call's fetched tree gets applied to tree.value: whichever call holds
  // the highest seq value when its fetch resolves wins (issue-order, not
  // resolve-order). load() always bumps it. refresh() only bumps it after
  // confirming (via currentBoardId, above) that it isn't stale.
  let loadSeq = 0

  // Monotonic counter incremented ONLY by load() (never by refresh()).
  // Used solely to decide, in load()'s `finally`, whether THIS call is
  // still the most recently issued load() and therefore responsible for
  // clearing `loading`. This has to be tracked separately from loadSeq:
  // a same-board refresh() racing a reload legitimately consumes a
  // loadSeq token (so the fresher of the two trees wins), but must never
  // be mistaken for "a newer load is in flight" and wedge `loading` at
  // true forever -- refresh() never touches loading itself, so if load()
  // deferred resetting it on the assumption that "something newer will
  // handle it," and that "something newer" was actually just a refresh(),
  // nothing ever would.
  let loadCallSeq = 0

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
  // A load() call is a navigation: it always wins over any in-flight
  // refresh() of whatever board was previously current (see currentBoardId
  // / loadSeq / loadCallSeq comments above).
  async function load(id: number): Promise<void> {
    currentBoardId = id
    const callSeq = ++loadCallSeq
    const seq = ++loadSeq
    loading.value = true
    error.value = null
    // A board switch always supersedes any synthesis banner left over from
    // whatever board was previously loaded (or mid-flight).
    synthesis.value = { running: false, done: 0, total: 0, error: null }
    try {
      const t = await api.fetchStoryboard(id)
      if (seq !== loadSeq) return
      tree.value = t
      selectDefaults(t)
      await refreshActiveJobs()
    } catch (e) {
      if (seq !== loadSeq) return
      error.value = errMessage(e)
    } finally {
      // Reset `loading` iff no newer load() call has been issued since
      // this one. Deliberately independent of the seq/loadSeq check above:
      // a racing same-board refresh() can legitimately bump loadSeq past
      // `seq` (making the tree-application branch above a no-op) while
      // this is still the only/latest *load* call -- in that case loading
      // must still clear, since nothing else ever will.
      if (callSeq === loadCallSeq) loading.value = false
    }
  }

  // Re-fetch the current tree in place, preserving selection where the
  // selected scene/panel still exists; falls back to selectDefaults()
  // otherwise (e.g. the selected panel was deleted server-side).
  async function refresh(): Promise<void> {
    if (!tree.value) return
    const id = tree.value.id
    // Stale by definition if the user has navigated to a different board
    // since this refresh's tree.value snapshot was taken (load() already
    // updated currentBoardId synchronously, even though tree.value itself
    // hasn't been overwritten yet because that load's fetch is still in
    // flight). Bail without consuming a loadSeq token -- letting a
    // dropped, off-target refresh grab a token would incorrectly make a
    // still-in-flight load() for the NEW board look stale against it.
    if (id !== currentBoardId) return
    const seq = ++loadSeq
    try {
      const t = await api.fetchStoryboard(id)
      if (seq !== loadSeq) return
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
      if (seq !== loadSeq) return
      error.value = errMessage(e)
    }
  }

  // ---- actions: storyboard CRUD ------------------------------------------

  async function create(body: Parameters<typeof api.createStoryboard>[0]): Promise<number> {
    const res = await api.createStoryboard(body)
    await loadList()
    return res.id
  }

  // Mirrors importText's catch-set-error-and-rethrow shape (rather than the
  // swallow-and-set-error shape most other actions use) so a caller that
  // needs to react per-row -- StoryboardLanding shows a local message next
  // to the row being deleted -- can, while error.value still carries a
  // sensible fallback for anything that doesn't.
  async function remove(id: number): Promise<void> {
    try {
      await api.deleteStoryboard(id)
    } catch (e) {
      error.value = errMessage(e)
      throw e
    }
    list.value = list.value.filter((s) => s.id !== id)
    if (tree.value?.id === id) {
      tree.value = null
      selectedSceneId.value = null
      selectedPanelId.value = null
    }
  }

  // Rollback restores only the FIELDS that were optimistically patched (not
  // the whole tree ref) onto whatever tree.value currently is, and only if
  // it's still the same storyboard -- reassigning tree.value = snapshot
  // outright would clobber a newer tree a concurrent refresh() installed
  // while this patch was in flight.
  async function patchStoryboardFields(
    body: Parameters<typeof api.patchStoryboard>[1],
  ): Promise<void> {
    if (!tree.value) return
    const id = tree.value.id
    const current = tree.value as unknown as Record<string, unknown>
    const snapshot: Record<string, unknown> = {}
    for (const key of Object.keys(body)) {
      snapshot[key] = current[key]
    }
    Object.assign(tree.value, body)
    try {
      await api.patchStoryboard(id, body)
    } catch (e) {
      if (tree.value && tree.value.id === id) {
        Object.assign(tree.value, snapshot)
      }
      error.value = errMessage(e)
    }
  }

  // Both the success-merge and the failure-rollback re-resolve the panel by
  // id against the CURRENT tree.value after the await, rather than reusing
  // the pre-await `panel` reference -- if a refresh() swapped tree.value
  // while the PATCH was in flight, that reference is a detached object the
  // UI no longer renders, and writing to it would be a silent no-op. If the
  // panel is gone entirely (deleted, or its scene was), both paths no-op.
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
      const current = panelById(panelId)
      if (current) Object.assign(current, res)
    } catch (e) {
      const current = panelById(panelId)
      if (current) Object.assign(current, snapshot)
      error.value = errMessage(e)
    }
  }

  // See patchPanelFields: rollback re-resolves the scene by id against the
  // current tree.value rather than mutating the pre-await reference.
  async function patchSceneFields(sceneId: number, body: Record<string, unknown>): Promise<void> {
    if (!tree.value) return
    const scene = tree.value.scenes.find((s) => s.id === sceneId)
    if (!scene) return
    const snapshot = { ...scene }
    Object.assign(scene, body)
    try {
      await api.patchScene(sceneId, body)
    } catch (e) {
      const current = tree.value?.scenes.find((s) => s.id === sceneId)
      if (current) Object.assign(current, snapshot)
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
      // While tree.value is null (the attachWs()→load() window, or after
      // remove()/switching boards), there is no "current" storyboard to
      // compare against -- every event must be dropped, not passed through,
      // or another storyboard's synthesis events would pollute state.
      if (!tree.value || d.storyboard_id !== tree.value.id) return
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
