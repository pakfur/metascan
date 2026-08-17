import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type {
  Beat,
  BeatImage,
  ComposeStage,
  Panel,
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
  const selectedBeatId = ref<number | null>(null)
  // job_id -> panel_id, for video-render jobs (generate-video is still
  // per-panel) and the panelJobState progress-chip overlay below.
  const jobToPanel = ref<Map<number, number>>(new Map())
  // job_id -> beat_id, for image-generation jobs (generate is now per-beat).
  const jobToBeat = ref<Map<number, number>>(new Map())
  const panelJobState = ref<
    Map<number, { state: JobState; error: string | null; value?: number; max?: number }>
  >(new Map())
  const synthesis = ref<{ running: boolean; done: number; total: number; error: string | null }>({
    running: false,
    done: 0,
    total: 0,
    error: null,
  })
  const compile = ref<{ running: boolean; done: number; total: number; error: string | null }>({
    running: false,
    done: 0,
    total: 0,
    error: null,
  })
  const story = ref<{
    running: boolean
    stage: ComposeStage | null
    done: number
    total: number
    error: string | null
  }>({ running: false, stage: null, done: 0, total: 0, error: null })

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

  const selectedBeat = computed<Beat | null>(() => {
    const panel = selectedPanel.value
    if (!panel || selectedBeatId.value == null) return null
    return panel.beats.find((b) => b.id === selectedBeatId.value) ?? null
  })

  // Any panel switch invalidates the previously-selected beat -- it belonged
  // to a different panel's beats array and would otherwise resolve to null
  // silently (harmless) or, worse, to a same-id beat on the new panel in some
  // hypothetical future schema. Clearing explicitly keeps the invariant
  // "selectedBeatId is always either null or a beat of selectedPanel" true
  // without relying on selectedBeat's own null-fallback.
  watch(selectedPanelId, () => {
    selectedBeatId.value = null
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

  function keeperImage(beat: Beat): BeatImage | null {
    return beat.images.find((i) => i.id === beat.selected_image_id) ?? null
  }

  // Panel thumbnails (grid tiles, scene strip) no longer have their own
  // image -- the generated candidates live on each beat now. Until Task 11
  // redesigns those tiles around beats directly, this surfaces the first
  // beat's keeper as the panel's representative image.
  function firstBeatKeeper(panel: Panel): BeatImage | null {
    const beat = panel.beats[0]
    return beat ? keeperImage(beat) : null
  }

  function findBeat(beatId: number): Beat | null {
    if (!tree.value) return null
    for (const scene of tree.value.scenes) {
      for (const panel of scene.panels) {
        const found = panel.beats.find((b) => b.id === beatId)
        if (found) return found
      }
    }
    return null
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
    // A board switch always supersedes any synthesis/compile banner left
    // over from whatever board was previously loaded (or mid-flight).
    synthesis.value = { running: false, done: 0, total: 0, error: null }
    compile.value = { running: false, done: 0, total: 0, error: null }
    // Navigating to a DIFFERENT board (or there's no tree at all yet) must
    // clear the previously-rendered board's state before the fetch, not
    // after: a failed fetch otherwise leaves the OLD board's tree fully
    // rendered under the NEW url (notFound never trips because tree stays
    // non-null), and even a successful fetch would flash the old board's
    // scenes/panels/jobs for a frame before the new tree lands. Guarded on
    // id so a same-board reload (e.g. a manual refresh of the current
    // board) keeps the existing tree visible while the new fetch is in
    // flight, matching refresh()'s no-flash behavior.
    if (tree.value === null || tree.value.id !== id) {
      tree.value = null
      selectedSceneId.value = null
      selectedPanelId.value = null
      jobToPanel.value = new Map()
      jobToBeat.value = new Map()
      panelJobState.value = new Map()
    }
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
  async function remove(id: number, purgeImages = false): Promise<void> {
    try {
      await api.deleteStoryboard(id, purgeImages)
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
      // patchPanel's response (db.get_panel) never carries `beats` -- only
      // get_storyboard_tree assembles that array -- so even though the
      // return type is Panel, Object.assign only touches keys actually
      // present on the response and the local `beats` array is left
      // untouched.
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

  async function addScene(fields: Parameters<typeof api.createScene>[1]): Promise<void> {
    if (!tree.value) return
    try {
      await api.createScene(tree.value.id, fields)
      await refresh()
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  async function addPanel(sceneId: number, action: string): Promise<void> {
    try {
      await api.createPanel(sceneId, { action })
      await refresh()
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  async function removeScene(id: number, purgeImages = false): Promise<void> {
    try {
      await api.deleteScene(id, purgeImages)
      await refresh()
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  async function removePanel(id: number, purgeImages = false): Promise<void> {
    try {
      await api.deletePanel(id, purgeImages)
      await refresh()
    } catch (e) {
      error.value = errMessage(e)
    }
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

  async function synthesize(beatIds?: number[], force?: boolean): Promise<void> {
    if (!tree.value) return
    const body: { beat_ids?: number[]; force?: boolean } = {}
    if (beatIds !== undefined) body.beat_ids = beatIds
    if (force !== undefined) body.force = force
    // Set the banner optimistically BEFORE the await: on a fast no-VLM
    // path the runner can broadcast synthesis_complete/synthesis_error
    // over the `storyboard` WS channel before this HTTP 202 response even
    // resolves. If `running: true` were only set after the await, it would
    // stomp that already-arrived terminal state -- a permanently stuck
    // "synthesizing 0/N" chip with Re-synth disabled forever.
    synthesis.value = { running: true, done: 0, total: 0, error: null }
    try {
      const res = await api.synthesizeStoryboard(tree.value.id, body)
      // Only fill in the real total if nothing has already reported
      // completion/failure via WS while this request was in flight.
      if (synthesis.value.running) synthesis.value.total = res.total
    } catch (e) {
      synthesis.value = { running: false, done: 0, total: 0, error: errMessage(e) }
      error.value = errMessage(e)
    }
  }

  async function compileVideo(panelIds?: number[], force?: boolean): Promise<void> {
    if (!tree.value) return
    const body: { panel_ids?: number[]; force?: boolean } = {}
    if (panelIds !== undefined) body.panel_ids = panelIds
    if (force !== undefined) body.force = force
    // Set the banner optimistically BEFORE the await -- same race as
    // synthesize() (compile_progress/compile_complete can beat the HTTP 202
    // response back over the WS channel).
    compile.value = { running: true, done: 0, total: 0, error: null }
    try {
      const res = await api.compileStoryboard(tree.value.id, body)
      // Only fill in the real total if nothing has already reported
      // completion/failure via WS while this request was in flight.
      if (compile.value.running) compile.value.total = res.total
    } catch (e) {
      compile.value = { running: false, done: 0, total: 0, error: errMessage(e) }
      error.value = errMessage(e)
    }
  }

  async function composeStory(body: {
    stages?: ComposeStage[]
    scene_ids?: number[]
    panel_ids?: number[]
    confirm?: boolean
  }): Promise<void> {
    // Optimistic before await: the WS story_progress can beat the HTTP
    // response (same race as synthesize(), storyboard.ts:365-386).
    story.value = { running: true, stage: null, done: 0, total: 0, error: null }
    try {
      if (!tree.value) throw new Error('no board loaded')
      await api.composeStoryboard(tree.value.id, body)
    } catch (e) {
      story.value.running = false
      throw e
    }
  }

  async function generate(beatIds?: number[], onlyFailed?: boolean): Promise<void> {
    if (!tree.value) return
    const body: { beat_ids?: number[]; only_failed?: boolean } = {}
    if (beatIds !== undefined) body.beat_ids = beatIds
    if (onlyFailed !== undefined) body.only_failed = onlyFailed
    try {
      await api.generateStoryboard(tree.value.id, body)
      await refreshActiveJobs()
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  // Mirrors generate(): calls the API then refreshActiveJobs() (video jobs
  // are ordinary generation_jobs -- the existing `comfy` channel jobToPanel
  // overlay and `beat_images_changed` refresh already cover them, no new
  // WS handling needed). Unlike generate(), returns the response so callers
  // can surface `skipped` entries (per-panel reasons that didn't fail the
  // whole request) themselves -- returns undefined on a thrown error (e.g.
  // the 400 multi-line validation detail), which is set on error.value the
  // same way every other action here does.
  async function generateVideo(
    panelIds?: number[],
    onlyFailed?: boolean,
  ): Promise<{ jobs: number[]; skipped: { panel_id: number; error: string }[] } | undefined> {
    if (!tree.value) return undefined
    const body: { panel_ids?: number[]; only_failed?: boolean } = {}
    if (panelIds !== undefined) body.panel_ids = panelIds
    if (onlyFailed !== undefined) body.only_failed = onlyFailed
    try {
      const res = await api.generateVideoStoryboard(tree.value.id, body)
      await refreshActiveJobs()
      return res
    } catch (e) {
      error.value = errMessage(e)
      return undefined
    }
  }

  async function cancelAll(): Promise<void> {
    if (!tree.value) return
    try {
      await api.cancelStoryboard(tree.value.id)
      await refreshActiveJobs()
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  async function selectImage(beatId: number, imageId: number | null): Promise<void> {
    try {
      const updated = await api.selectBeatImage(beatId, imageId)
      const beat = findBeat(beatId)
      // images survives: not present on the patch response, so
      // Object.assign leaves the locally-loaded array untouched.
      if (beat) Object.assign(beat, updated)
    } catch (e) {
      error.value = errMessage(e)
    }
  }

  // Rebuilds jobToPanel/jobToBeat/panelJobState from the server's
  // queued+running job lists, filtered to panels/beats belonging to the
  // current tree. This is what survives a page reload mid-generation --
  // there's no other durable client-side record of in-flight jobs.
  async function refreshActiveJobs(): Promise<void> {
    if (!tree.value) {
      jobToPanel.value = new Map()
      jobToBeat.value = new Map()
      panelJobState.value = new Map()
      return
    }
    const panelIds = new Set<number>()
    const beatIds = new Set<number>()
    for (const scene of tree.value.scenes) {
      for (const p of scene.panels) {
        panelIds.add(p.id)
        for (const b of p.beats) beatIds.add(b.id)
      }
    }
    const [queued, running] = await Promise.all([
      comfyApi.listJobs('queued', 1000),
      comfyApi.listJobs('running', 1000),
    ])
    const nextJobToPanel = new Map<number, number>()
    const nextJobToBeat = new Map<number, number>()
    const nextPanelJobState = new Map<
      number,
      { state: JobState; error: string | null; value?: number; max?: number }
    >()
    for (const job of [...queued, ...running]) {
      if (job.panel_id != null && panelIds.has(job.panel_id)) {
        nextJobToPanel.set(job.id, job.panel_id)
        nextPanelJobState.set(job.panel_id, { state: job.state, error: null })
      }
      if (job.beat_id != null && beatIds.has(job.beat_id)) {
        nextJobToBeat.set(job.id, job.beat_id)
      }
    }
    jobToPanel.value = nextJobToPanel
    jobToBeat.value = nextJobToBeat
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

  // ---- actions: beat CRUD ------------------------------------------

  async function addBeat(panelId: number, action: string): Promise<void> {
    const panel = panelById(panelId)
    const sort_order = panel ? panel.beats.length : 0
    await api.createBeat(panelId, { action, sort_order })
    await refresh()
  }

  async function patchBeatFields(
    beatId: number,
    body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>>,
  ): Promise<void> {
    const updated = await api.patchBeat(beatId, body)
    const panel = panelById(updated.panel_id)
    if (!panel) return
    const idx = panel.beats.findIndex((b) => b.id === beatId)
    // patchBeat returns BeatWithoutImages -- merge onto the existing beat
    // (rather than replacing it) so the locally-loaded `images` array
    // survives, mirroring patchPanelFields' treatment of patchPanel's
    // beats-less response above.
    if (idx >= 0) Object.assign(panel.beats[idx], updated)
    panel.beats.sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
  }

  async function removeBeat(beatId: number, purgeImages = false): Promise<void> {
    await api.deleteBeat(beatId, purgeImages)
    for (const scene of tree.value?.scenes ?? [])
      for (const panel of scene.panels)
        panel.beats = panel.beats.filter((b) => b.id !== beatId)
    if (selectedBeatId.value === beatId) selectedBeatId.value = null
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
      } else if (event === 'beat_images_changed') {
        // No per-beat GET endpoint exists; a full tree refetch (which
        // preserves selection) is the simplest correct way to pick up the
        // new image set.
        void refresh()
      } else if (event === 'story_progress') {
        story.value = {
          running: true,
          stage: (d.stage as ComposeStage) ?? null,
          done: Number(d.done ?? 0),
          total: Number(d.total ?? 0),
          error: null,
        }
      } else if (event === 'story_stage_complete') {
        void refresh() // each stage lands reviewable state immediately
      } else if (event === 'story_complete') {
        story.value.running = false
        void refresh()
      } else if (event === 'story_error') {
        story.value.running = false
        story.value.error = String(d.error ?? 'compose failed')
      } else if (event === 'compile_progress') {
        compile.value = {
          running: true,
          done: d.done as number,
          total: d.total as number,
          error: null,
        }
      } else if (event === 'compile_complete') {
        compile.value = {
          running: false,
          done: compile.value.total,
          total: compile.value.total,
          error: null,
        }
        void refresh()
      } else if (event === 'compile_error') {
        compile.value = {
          running: false,
          done: compile.value.done,
          total: compile.value.total,
          error: String(d.error ?? 'compile failed'),
        }
        // Panels compiled before the run-level failure already wrote their
        // video_prompt/warnings server-side -- refetch so they surface now
        // instead of waiting for an unrelated reload.
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
      // job_outputs: ignored -- the storyboard channel's beat_images_changed
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
    selectedBeatId,
    jobToPanel,
    jobToBeat,
    panelJobState,
    synthesis,
    compile,
    story,
    // getters
    selectedScene,
    selectedPanel,
    selectedBeat,
    subjectsById,
    panelById,
    keeperImage,
    firstBeatKeeper,
    findBeat,
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
    compileVideo,
    generate,
    generateVideo,
    cancelAll,
    selectImage,
    refreshActiveJobs,
    addSubject,
    patchSubjectFields,
    removeSubject,
    composeStory,
    addBeat,
    patchBeatFields,
    removeBeat,
    attachWs,
  }
})
