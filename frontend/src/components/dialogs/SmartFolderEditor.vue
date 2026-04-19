<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  FIELD_DEFS,
  OP_LABELS,
  matches,
  useFoldersStore,
} from '../../stores/folders'
import { useFoldersUi } from '../../composables/useFoldersUi'
import { useMediaStore } from '../../stores/media'
import { useToast } from '../../composables/useToast'
import type {
  RuleField,
  RuleOp,
  SmartCondition,
  SmartRules,
} from '../../types/folders'

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()
const ui = useFoldersUi()
const toast = useToast()

// Determine whether we're editing an existing smart folder or creating new.
const target = ui.smartEditorOpen.value
const existing =
  target && target !== 'new'
    ? foldersStore.smartFolders.find((f) => f.id === target) ?? null
    : null

// Deep-clone rules so the editor stays local until the user saves.
function cloneRules(r: SmartRules): SmartRules {
  return {
    match: r.match,
    conditions: r.conditions.map((c) => ({
      field: c.field,
      op: c.op,
      // Clone arrays for the `tags` value.
      value: Array.isArray(c.value) ? [...c.value] : c.value,
    })),
  }
}

const name = ref(existing?.name ?? 'New smart folder')
const rules = ref<SmartRules>(
  existing
    ? cloneRules(existing.rules)
    : ui.smartEditorSeedRules.value
      ? cloneRules(ui.smartEditorSeedRules.value)
      : {
          match: 'all',
          conditions: [{ field: 'favorite', op: 'is', value: true }],
        },
)

const matchCount = computed(() => {
  const source = mediaStore.allMedia
  let n = 0
  for (const m of source) {
    if (matches(m, rules.value)) n++
  }
  return n
})

function onFieldChange(idx: number, field: RuleField) {
  const def = FIELD_DEFS[field]
  const c = rules.value.conditions[idx]
  c.field = field
  c.op = def.ops[0]
  const dv = def.defaultValue()
  c.value = Array.isArray(dv) ? [...dv] : dv
}

function onOpChange(idx: number, op: RuleOp) {
  rules.value.conditions[idx].op = op
}

function onValueChange(idx: number, value: SmartCondition['value']) {
  rules.value.conditions[idx].value = value
}

function addCondition() {
  rules.value.conditions.push({ field: 'tags', op: 'contains', value: [] })
}

function removeCondition(idx: number) {
  rules.value.conditions.splice(idx, 1)
  if (rules.value.conditions.length === 0) {
    rules.value.conditions.push({ field: 'favorite', op: 'is', value: true })
  }
}

function addTag(idx: number, raw: string) {
  const v = raw.trim()
  if (!v) return
  const c = rules.value.conditions[idx]
  const arr = Array.isArray(c.value) ? c.value : []
  if (!arr.includes(v)) arr.push(v)
  c.value = arr
}

function removeTag(idx: number, tag: string) {
  const c = rules.value.conditions[idx]
  if (Array.isArray(c.value)) {
    c.value = c.value.filter((t) => t !== tag)
  }
}

// Per-row tag input buffer — one string per condition index.
const tagBuffers = ref<Record<number, string>>({})

function onTagInputKey(idx: number, e: KeyboardEvent) {
  const buf = tagBuffers.value[idx] ?? ''
  if (e.key === 'Enter' && buf.trim()) {
    e.preventDefault()
    addTag(idx, buf)
    tagBuffers.value[idx] = ''
  } else if (e.key === 'Backspace' && !buf) {
    const c = rules.value.conditions[idx]
    if (Array.isArray(c.value) && c.value.length) {
      const next = [...c.value]
      next.pop()
      c.value = next
    }
  }
}

function close() {
  ui.closeSmartEditor()
}

function save() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    toast.show('Name is required', 'warn')
    return
  }
  if (existing) {
    foldersStore.updateSmartFolder(existing.id, {
      name: trimmed,
      rules: cloneRules(rules.value),
    })
    toast.show('Smart folder updated')
  } else {
    const f = foldersStore.createSmartFolder(trimmed, cloneRules(rules.value))
    foldersStore.setScope({ kind: 'smart', id: f.id })
    toast.show('Smart folder created')
  }
  close()
}

function del() {
  if (!existing) return
  if (!confirm(`Delete smart folder "${existing.name}"?`)) return
  foldersStore.deleteFolder(existing.id)
  toast.show('Deleted')
  close()
}

function onBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

const nameInput = ref<HTMLInputElement | null>(null)
onMounted(() => {
  nameInput.value?.focus()
  nameInput.value?.select()
})

// Friendly per-field helpers for the template.
function opsFor(field: RuleField): RuleOp[] {
  return FIELD_DEFS[field].ops
}

function defFor(field: RuleField) {
  return FIELD_DEFS[field]
}

const allFields = computed(() =>
  (Object.keys(FIELD_DEFS) as RuleField[]).map((k) => ({
    value: k,
    label: FIELD_DEFS[k].label,
  })),
)
</script>

<template>
  <div class="dialog-overlay" @click="onBackdrop" @keydown="onKeydown">
    <div class="dialog">
      <div class="dhead">
        <div class="dt">
          <i class="pi pi-bolt" />
          {{ existing ? 'Edit smart folder' : 'New smart folder' }}
        </div>
        <button class="dclose" @click="close"><i class="pi pi-times" /></button>
      </div>

      <div class="dbody">
        <div class="smart-note">
          <i class="pi pi-info-circle" />
          <div>
            Smart folders auto-update as your library changes. Set rules here —
            membership is live.
          </div>
        </div>

        <div class="dfield">
          <label class="dlabel">Name</label>
          <input ref="nameInput" class="dinput" v-model="name" />
        </div>

        <div class="dfield">
          <label class="dlabel">Rules</label>
          <div class="rule-group">
            <div class="rg-head">
              <span>Match</span>
              <select class="match-select" v-model="rules.match">
                <option value="all">all</option>
                <option value="any">any</option>
              </select>
              <span>of the following conditions:</span>
            </div>

            <div class="rules-body">
              <div
                v-for="(c, idx) in rules.conditions"
                :key="idx"
                class="rule-row"
              >
                <select
                  :value="c.field"
                  @change="
                    onFieldChange(
                      idx,
                      ($event.target as HTMLSelectElement).value as RuleField,
                    )
                  "
                >
                  <option
                    v-for="f in allFields"
                    :key="f.value"
                    :value="f.value"
                  >
                    {{ f.label }}
                  </option>
                </select>

                <select
                  :value="c.op"
                  @change="
                    onOpChange(
                      idx,
                      ($event.target as HTMLSelectElement).value as RuleOp,
                    )
                  "
                >
                  <option v-for="op in opsFor(c.field)" :key="op" :value="op">
                    {{ OP_LABELS[op] }}
                  </option>
                </select>

                <div class="value-cell">
                  <!-- bool -->
                  <select
                    v-if="defFor(c.field).value === 'bool'"
                    :value="String(c.value)"
                    @change="
                      onValueChange(
                        idx,
                        ($event.target as HTMLSelectElement).value === 'true',
                      )
                    "
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>

                  <!-- select -->
                  <select
                    v-else-if="defFor(c.field).value === 'select'"
                    :value="String(c.value ?? '')"
                    @change="
                      onValueChange(
                        idx,
                        ($event.target as HTMLSelectElement).value,
                      )
                    "
                  >
                    <option
                      v-for="opt in defFor(c.field).options"
                      :key="opt[0]"
                      :value="opt[0]"
                    >
                      {{ opt[1] }}
                    </option>
                  </select>

                  <!-- text -->
                  <input
                    v-else-if="defFor(c.field).value === 'text'"
                    type="text"
                    :value="String(c.value ?? '')"
                    placeholder="…"
                    @input="
                      onValueChange(
                        idx,
                        ($event.target as HTMLInputElement).value,
                      )
                    "
                  />

                  <!-- days -->
                  <input
                    v-else-if="defFor(c.field).value === 'days'"
                    type="number"
                    min="1"
                    step="1"
                    :value="Number(c.value) || 30"
                    @input="
                      onValueChange(
                        idx,
                        Number(($event.target as HTMLInputElement).value) || 0,
                      )
                    "
                  />

                  <!-- tags -->
                  <div v-else class="tags-input">
                    <span
                      v-for="t in Array.isArray(c.value) ? c.value : []"
                      :key="t"
                      class="chip"
                    >
                      {{ t }}
                      <span class="x" @click="removeTag(idx, t)">×</span>
                    </span>
                    <input
                      type="text"
                      placeholder="add tag + Enter"
                      :value="tagBuffers[idx] ?? ''"
                      @input="
                        tagBuffers[idx] = (
                          $event.target as HTMLInputElement
                        ).value
                      "
                      @keydown="onTagInputKey(idx, $event)"
                    />
                  </div>
                </div>

                <button
                  class="rm"
                  title="Remove"
                  @click="removeCondition(idx)"
                >
                  <i class="pi pi-times" />
                </button>
              </div>
            </div>

            <button class="add-rule-btn" @click="addCondition">
              <i class="pi pi-plus" />Add condition
            </button>
          </div>
        </div>
      </div>

      <div class="dfoot">
        <div class="preview-count">
          <i class="pi pi-check-circle" />
          <b>{{ matchCount }}</b>
          item{{ matchCount === 1 ? '' : 's' }} match these rules
        </div>
        <div class="actions">
          <button v-if="existing" class="btn btn-danger-text" @click="del">
            Delete
          </button>
          <button class="btn btn-secondary" @click="close">Cancel</button>
          <button class="btn btn-primary" @click="save">
            {{ existing ? 'Save' : 'Create' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog {
  width: min(620px, 92vw);
  max-height: 86vh;
  overflow: hidden;
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}

.dhead {
  padding: 12px 18px;
  border-bottom: 1px solid var(--surface-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--surface-section);
}

.dt {
  font-size: 15px;
  font-weight: 700;
  display: flex;
  gap: 8px;
  align-items: center;
}

.dt .pi {
  color: var(--primary-color);
}

.dclose {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color-secondary);
  padding: 4px;
  border-radius: 4px;
}

.dclose:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.dbody {
  padding: 18px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dfoot {
  padding: 10px 16px;
  border-top: 1px solid var(--surface-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  background: var(--surface-section);
}

.preview-count {
  font-size: 12px;
  color: var(--text-color-secondary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.preview-count b {
  color: var(--text-color);
}

.preview-count .pi {
  color: #22c55e;
  font-size: 12px;
}

.actions {
  display: flex;
  gap: 8px;
}

.dfield {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dlabel {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.dinput {
  padding: 8px 10px;
  font-size: 13px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-family: inherit;
  width: 100%;
  outline: none;
}

.dinput:focus {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 15%, transparent);
}

.smart-note {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color);
  line-height: 1.5;
}

.smart-note .pi {
  color: var(--primary-color);
  font-size: 14px;
  flex-shrink: 0;
  padding-top: 1px;
}

.rule-group {
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--surface-section);
}

.rg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.match-select {
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  background: var(--surface-card);
  color: var(--text-color);
  font-family: inherit;
}

.rules-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rule-row {
  display: grid;
  grid-template-columns: 110px 140px 1fr auto;
  gap: 6px;
  align-items: center;
}

.rule-row select,
.rule-row input {
  padding: 6px 8px;
  font-size: 12.5px;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  background: var(--surface-card);
  color: var(--text-color);
  font-family: inherit;
  outline: none;
  width: 100%;
}

.rule-row select:focus,
.rule-row input:focus {
  border-color: var(--primary-color);
}

.rm {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-color-secondary);
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.rm:hover {
  background: var(--surface-hover);
  color: var(--danger-color);
}

.value-cell {
  display: flex;
  align-items: center;
}

.tags-input {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
  background: var(--surface-card);
  min-height: 30px;
  align-items: center;
  width: 100%;
}

.tags-input:focus-within {
  border-color: var(--primary-color);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
  font-size: 11.5px;
}

.chip .x {
  cursor: pointer;
  opacity: 0.7;
}

.chip .x:hover {
  opacity: 1;
}

.tags-input input {
  flex: 1;
  min-width: 80px;
  border: none !important;
  outline: none;
  background: transparent;
  color: var(--text-color);
  font-size: 12.5px;
  padding: 2px 4px !important;
  font-family: inherit;
}

.add-rule-btn {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 12px;
  background: transparent;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-family: inherit;
}

.add-rule-btn:hover {
  color: var(--primary-color);
  border-color: var(--primary-color);
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  font-family: inherit;
}

.btn-primary {
  background: var(--primary-color);
  color: #fff;
}

.btn-primary:hover {
  filter: brightness(0.95);
}

.btn-secondary {
  background: var(--surface-card);
  color: var(--text-color);
  border-color: var(--surface-border);
}

.btn-secondary:hover {
  background: var(--surface-hover);
}

.btn-danger-text {
  background: none;
  color: var(--danger-color);
}

.btn-danger-text:hover {
  text-decoration: underline;
}
</style>
