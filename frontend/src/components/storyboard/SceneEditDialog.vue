<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { describeScene } from '../../api/storyboard'
import { ApiError, thumbnailUrl } from '../../api/client'
import ReferenceImagePicker from './ReferenceImagePicker.vue'
import TextEditPopup from './TextEditPopup.vue'
import { SCENE_FUNCTIONS, type Scene, type ShotTemplateSummary } from '../../types/storyboard'

// `scene` null means create mode: Save POSTs a new scene. Otherwise Save
// PATCHes only the changed fields of the given scene.
const props = defineProps<{ scene: Scene | null }>()
const emit = defineEmits<{ close: [] }>()

const store = useStoryboardStore()

const name = ref(props.scene?.name ?? '')
const subtitle = ref(props.scene?.subtitle ?? '')
const setting = ref(props.scene?.setting ?? '')
const lighting = ref(props.scene?.lighting ?? '')
const mood = ref(props.scene?.mood ?? '')
const sceneFunction = ref(props.scene?.function ?? '')
const referencePath = ref(props.scene?.reference_path ?? '')
// Last value successfully persisted to the DB via a pick/clear (below) --
// distinct from `referencePath`, which updates optimistically the instant
// the user picks/clears. Only used to gate Describe: it must never run
// against a reference the server doesn't actually have on file.
const persistedRef = ref<string | null>(props.scene?.reference_path ?? null)

const saving = ref(false)
const saveError = ref<string | null>(null)

const isCreate = computed(() => props.scene === null)
const canSave = computed(() => name.value.trim().length > 0 && !saving.value)

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
}

// ---- reference image ------------------------------------------------------

// Path whose thumbnail failed to load, so a subsequent path change retries.
const refThumbFailedPath = ref<string | null>(null)

function onRefThumbError(path: string): void {
  refThumbFailedPath.value = path
}

const pickerOpen = ref(false)
const refError = ref<string | null>(null)
// True while a pick/clear persist is in flight -- disables Browse/Clear so a
// second pick can't race the first's PATCH, and blocks Describe (below)
// until the in-flight persist resolves one way or the other.
const refBusy = ref(false)

async function onPickReference(path: string): Promise<void> {
  referencePath.value = path
  pickerOpen.value = false
  // The describe endpoint reads the persisted reference from the DB, so in
  // edit mode it must be saved immediately rather than waiting for Save.
  // Create mode has no scene id yet -- the picked path just rides along in
  // the create payload when the user hits Save.
  if (props.scene) {
    refBusy.value = true
    refError.value = null
    store.error = null
    await store.patchSceneFields(props.scene.id, { reference_path: path })
    if (store.error) {
      // referencePath now shows the attempted (unsaved) pick and refError
      // explains why it isn't persisted; persistedRef deliberately stays at
      // its old value so Describe (gated below) can't run against it.
      refError.value = store.error
      store.error = null
    } else {
      persistedRef.value = path
    }
    refBusy.value = false
  }
}

// The path input itself is read-only (see template) -- the reference only
// ever changes via a picked file or this clear, both of which persist
// immediately in edit mode so `persistedRef` can never drift from what a
// manually-typed, never-saved path would have caused.
async function onClearReference(): Promise<void> {
  referencePath.value = ''
  if (props.scene) {
    refBusy.value = true
    refError.value = null
    store.error = null
    await store.patchSceneFields(props.scene.id, { reference_path: null })
    if (store.error) {
      refError.value = store.error
      store.error = null
    } else {
      persistedRef.value = null
    }
    refBusy.value = false
  }
}

// ---- describe from ref -----------------------------------------------------

const describing = ref(false)
const describeErr = ref<string | null>(null)

// Requires the local reference to exactly match what's persisted -- after a
// failed pick/clear persist (refError set, persistedRef unchanged) this is
// false until the user re-picks (or re-clears) successfully, so Describe
// can never read a stale/mismatched DB reference silently.
const canDescribe = computed(
  () =>
    !isCreate.value &&
    !!persistedRef.value &&
    referencePath.value === persistedRef.value &&
    !refBusy.value &&
    !describing.value,
)

async function onDescribeScene(): Promise<void> {
  if (!props.scene) return
  describing.value = true
  describeErr.value = null
  try {
    const res = await describeScene(props.scene.id)
    setting.value = res.setting
    if (!lighting.value.trim() && res.lighting) lighting.value = res.lighting
    if (!mood.value.trim() && res.mood) mood.value = res.mood
  } catch (e) {
    describeErr.value = errMsg(e)
  } finally {
    describing.value = false
  }
}

// ---- shot template (user-selected; the shots stage honours it) --------
const templateId = ref(props.scene?.template_id ?? '')
const brief = ref(props.scene?.brief ?? '')
onMounted(() => { void store.loadTemplates() })

function templateLabel(t: ShotTemplateSummary): string {
  const match = t.function === sceneFunction.value ? '✓ ' : ''
  return `${match}${t.id} · ${t.function} · ${t.slot_count} slots / ${t.duration_s}s`
}
const sortedTemplates = computed(() =>
  [...store.templates].sort((a, b) =>
    Number(b.function === sceneFunction.value) - Number(a.function === sceneFunction.value)),
)

// ---- save -------------------------------------------------------------

async function save(): Promise<void> {
  const trimmedName = name.value.trim()
  if (!trimmedName) return
  saving.value = true
  saveError.value = null
  store.error = null
  try {
    if (props.scene === null) {
      await store.addScene({
        name: trimmedName,
        subtitle: subtitle.value.trim() || null,
        setting: setting.value.trim() || null,
        lighting: lighting.value.trim() || null,
        mood: mood.value.trim() || null,
        reference_path: referencePath.value.trim() || null,
        function: sceneFunction.value || null,
        template_id: templateId.value || null,
        brief: brief.value.trim() || null,
      })
    } else {
      // Changed fields only; empty text clears the nullable columns via an
      // explicit null (the PATCH route honors it — exclude_unset semantics).
      const body: Record<string, unknown> = {}
      if (trimmedName !== props.scene.name) body.name = trimmedName
      const newSubtitle = subtitle.value.trim() || null
      if (newSubtitle !== (props.scene.subtitle ?? null)) body.subtitle = newSubtitle
      const newSetting = setting.value.trim() || null
      if (newSetting !== (props.scene.setting ?? null)) body.setting = newSetting
      const newLighting = lighting.value.trim() || null
      if (newLighting !== (props.scene.lighting ?? null)) body.lighting = newLighting
      const newMood = mood.value.trim() || null
      if (newMood !== (props.scene.mood ?? null)) body.mood = newMood
      const newFunction = sceneFunction.value || null
      if (newFunction !== (props.scene.function ?? null)) body.function = newFunction
      const newRef = referencePath.value.trim() || null
      if (newRef !== (props.scene.reference_path ?? null)) body.reference_path = newRef
      const newTemplate = templateId.value || null
      if (newTemplate !== (props.scene.template_id ?? null)) body.template_id = newTemplate
      const newBrief = brief.value.trim() || null
      if (newBrief !== (props.scene.brief ?? null)) body.brief = newBrief
      if (Object.keys(body).length > 0) {
        await store.patchSceneFields(props.scene.id, body)
      }
    }
    // Both store actions swallow failures into store.error rather than
    // throwing — surface it here and keep the dialog open.
    if (store.error) {
      saveError.value = store.error
      store.error = null
      return
    }
    // A saved change to a field the shots stage reads (utils/storyboardDeps)
    // leaves a pending prompt in the store; keep the dialog open to ask.
    if (props.scene && store.downstreamPromptsFor('shots', props.scene.id).length > 0) {
      return
    }
    emit('close')
  } finally {
    saving.value = false
  }
}

// ---- downstream prompt: rebuild shots after a scene edit ----------------

const shotsPrompt = computed(() =>
  props.scene ? (store.downstreamPromptsFor('shots', props.scene.id)[0] ?? null) : null,
)
const rebuildBusy = ref(false)
const rebuildError = ref<string | null>(null)

function dismissShotsPrompt(): void {
  if (shotsPrompt.value) store.dismissDownstream(shotsPrompt.value.key)
  emit('close')
}

// Same 409 confirm_required shape composeStory uses elsewhere: rebuilding
// shots destroys the scene's existing shots/beats, so the backend gates it.
async function rebuildShots(confirm = false): Promise<void> {
  if (!props.scene) return
  rebuildBusy.value = true
  rebuildError.value = null
  try {
    await store.composeStory({
      stages: ['shots', 'beats'],
      scene_ids: [props.scene.id],
      confirm,
    })
    emit('close')
  } catch (e: unknown) {
    if (
      e instanceof ApiError &&
      e.status === 409 &&
      (e.detail as { code?: string } | undefined)?.code === 'confirm_required'
    ) {
      const message =
        (e.detail as { message?: string } | undefined)?.message ??
        'This replaces this scene’s existing shots and beats — continue?'
      if (window.confirm(message)) {
        await rebuildShots(true)
        return
      }
    } else {
      rebuildError.value = errMsg(e)
    }
  } finally {
    rebuildBusy.value = false
  }
}
</script>

<template>
  <div class="dialog-overlay" @click.self="emit('close')">
    <div class="dialog-card">
      <h3>{{ isCreate ? 'New scene' : 'Edit scene' }}</h3>

      <div class="field">
        <label for="se-title">Title</label>
        <TextEditPopup title="Title" :value="name" @save="name = $event">
          <input id="se-title" v-model="name" type="text" placeholder="Scene title" />
        </TextEditPopup>
      </div>

      <div class="field">
        <label for="se-subtitle">Subtitle</label>
        <TextEditPopup title="Subtitle" :value="subtitle" @save="subtitle = $event">
          <input
            id="se-subtitle"
            v-model="subtitle"
            type="text"
            placeholder="Short tagline shown under the title"
          />
        </TextEditPopup>
      </div>

      <div class="field">
        <label for="se-brief">Brief <span class="hint-inline">what this scene must accomplish</span></label>
        <textarea id="se-brief" v-model="brief" rows="3" placeholder="Derived from the arc entries this scene covers" />
        <p v-if="props.scene?.arc_beats?.length" class="arc-line">
          Covers: <span v-for="b in props.scene.arc_beats" :key="b" class="arc-chip">{{ b }}</span>
          <template v-if="props.scene.charge_in !== null"> · charge {{ props.scene.charge_in }} → {{ props.scene.charge_out }}</template>
        </p>
      </div>

      <div class="field">
        <label for="se-setting">Setting</label>
        <textarea
          id="se-setting"
          v-model="setting"
          rows="7"
          placeholder="Common setting and background for every panel in this scene"
        />
      </div>

      <div class="field-row">
        <div class="field">
          <label for="se-lighting">Lighting</label>
          <TextEditPopup title="Lighting" :value="lighting" @save="lighting = $event">
            <input
              id="se-lighting"
              v-model="lighting"
              type="text"
              placeholder="e.g. golden hour, harsh fluorescent"
            />
          </TextEditPopup>
        </div>
        <div class="field">
          <label for="se-mood">Mood</label>
          <TextEditPopup title="Mood" :value="mood" @save="mood = $event">
            <input id="se-mood" v-model="mood" type="text" placeholder="e.g. tense, melancholic" />
          </TextEditPopup>
        </div>
      </div>

      <div class="field">
        <label for="se-function">Function</label>
        <select id="se-function" v-model="sceneFunction">
          <option value="">—</option>
          <option v-for="fn in SCENE_FUNCTIONS" :key="fn" :value="fn">{{ fn }}</option>
        </select>
      </div>

      <div class="field template-section">
        <label for="se-template">Shot template</label>
        <select id="se-template" v-model="templateId">
          <option value="">Free-form (VLM decides the shots)</option>
          <option v-for="t in sortedTemplates" :key="t.id" :value="t.id">{{ templateLabel(t) }}</option>
        </select>
        <p v-for="m in props.scene?.template_problems ?? []" :key="m" class="error inline">{{ m }}</p>
        <p v-for="m in props.scene?.template_warnings ?? []" :key="m" class="warn inline">{{ m }}</p>
      </div>

      <div class="field">
        <label for="se-ref">Setting reference image</label>
        <div class="ref-row">
          <img
            v-if="referencePath && refThumbFailedPath !== referencePath"
            :src="thumbnailUrl(referencePath)"
            alt=""
            class="ref-thumb"
            @error="onRefThumbError(referencePath)"
          />
          <div v-else class="ref-thumb ref-thumb-empty" title="No reference image">
            <span>—</span>
          </div>
          <input
            id="se-ref"
            :value="referencePath"
            type="text"
            class="ref-path"
            placeholder="Reference image path"
            readonly
          />
          <button
            type="button"
            class="browse-btn"
            :disabled="refBusy"
            @click="pickerOpen = true"
          >
            Browse…
          </button>
          <button
            v-if="referencePath"
            type="button"
            class="remove-btn"
            title="Clear reference image"
            :disabled="refBusy"
            @click="onClearReference"
          >
            &times;
          </button>
        </div>
        <p v-if="refError" class="error inline">{{ refError }}</p>
      </div>

      <div class="describe-row">
        <button type="button" class="describe-btn" :disabled="!canDescribe" @click="onDescribeScene">
          {{ describing ? 'Describing…' : 'Describe from ref' }}
        </button>
        <span v-if="isCreate" class="hint">Save the scene first to enable Describe.</span>
        <span v-else class="hint">First call may take up to a minute while the model loads.</span>
      </div>
      <p v-if="describeErr" class="error inline">{{ describeErr }}</p>

      <p v-if="saveError" class="error">{{ saveError }}</p>

      <div v-if="shotsPrompt" class="downstream">
        <p>
          Scene {{ shotsPrompt.fields.map((f) => f.replace(/_/g, ' ')).join(', ') }} changed —
          rebuild the shots and beats for this scene? Existing shots are replaced.
        </p>
        <p v-if="rebuildError" class="error inline">{{ rebuildError }}</p>
        <div class="dialog-actions">
          <button class="btn-primary" :disabled="rebuildBusy || store.story.running" @click="rebuildShots()">
            {{ rebuildBusy ? 'Rebuilding…' : 'Rebuild shots' }}
          </button>
          <button class="btn-secondary" :disabled="rebuildBusy" @click="dismissShotsPrompt">
            Not now
          </button>
        </div>
      </div>

      <div v-else class="dialog-actions">
        <button class="btn-primary" :disabled="!canSave" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
        </button>
        <button class="btn-secondary" :disabled="saving" @click="emit('close')">Cancel</button>
      </div>
    </div>

    <ReferenceImagePicker
      v-if="pickerOpen"
      @select="onPickReference"
      @close="pickerOpen = false"
    />
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 560px;
  max-width: 92vw;
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 14px;
  font-size: 18px;
  color: var(--text-color);
}

.field {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-row {
  display: flex;
  gap: 12px;
}

.field-row .field {
  flex: 1;
}

label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

input[type='text'],
textarea,
select {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.hint-inline {
  font-weight: 400;
  color: var(--text-color-secondary);
}

.warn.inline {
  color: var(--yellow-500, #d4a017);
  font-size: 12px;
  margin: 4px 0 0;
}

.arc-chip {
  display: inline-block;
  padding: 0 6px;
  margin-right: 4px;
  border-radius: 8px;
  background: var(--surface-border);
  font-size: 11px;
}

.arc-line {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin: 6px 0 0;
}

textarea {
  resize: vertical;
  min-height: 120px;
}

.ref-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ref-path {
  flex: 1;
  min-width: 0;
}

.ref-path[readonly] {
  cursor: default;
  background: var(--surface-ground);
}

.ref-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  flex-shrink: 0;
  display: block;
}

.ref-thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 14px;
}

.browse-btn {
  padding: 6px 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
}

.browse-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.browse-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.remove-btn {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.remove-btn:hover:not(:disabled) {
  color: var(--danger-color, #e53e3e);
}

.remove-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.describe-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}

.describe-btn {
  padding: 6px 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
}

.describe-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.describe-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 4px 0 0;
}

.error.inline {
  margin-top: 6px;
}

.downstream {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-color);
  background: color-mix(in srgb, var(--primary-color) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--primary-color) 40%, transparent);
}
.downstream p {
  margin: 0 0 4px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
}
</style>
