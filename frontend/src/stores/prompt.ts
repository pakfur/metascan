import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/prompt'

const STORAGE_KEY = 'metascan_prompt_playground_settings'

export interface PlaygroundSettings {
  target_model: api.TargetModel
  architecture: api.Architecture
  extras: api.ExtraOption[]
  temperature: number
  max_tokens: number
  prefix: string
  suffix: string
}

const DEFAULT_TARGET: api.TargetModel = 'sd'

const DEFAULTS: PlaygroundSettings = {
  target_model: DEFAULT_TARGET,
  architecture: 't2i',
  extras: [],
  temperature: 0.6,
  max_tokens: 250,
  prefix: api.TARGET_PRESETS[DEFAULT_TARGET].prefix,
  suffix: api.TARGET_PRESETS[DEFAULT_TARGET].suffix,
}

const _validTargets = new Set<string>(api.TARGET_MODEL_ORDER)
const _validExtras = new Set<string>(api.EXTRA_OPTIONS.map((e) => e.key))

function _coerce(parsed: Record<string, unknown>): PlaygroundSettings {
  const out: PlaygroundSettings = { ...DEFAULTS }
  if (typeof parsed.target_model === 'string' && _validTargets.has(parsed.target_model)) {
    out.target_model = parsed.target_model as api.TargetModel
  }
  if (Array.isArray(parsed.extras)) {
    // Stale extras from the previous (16-option) playground are silently
    // dropped — _validExtras now contains only the two surviving keys.
    out.extras = (parsed.extras as unknown[]).filter(
      (x): x is api.ExtraOption => typeof x === 'string' && _validExtras.has(x),
    )
  }
  if (typeof parsed.temperature === 'number') out.temperature = parsed.temperature
  if (typeof parsed.max_tokens === 'number') out.max_tokens = parsed.max_tokens
  if (typeof parsed.prefix === 'string') out.prefix = parsed.prefix
  if (typeof parsed.suffix === 'string') out.suffix = parsed.suffix
  return out
}

function loadSettings(): PlaygroundSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      return _coerce(JSON.parse(raw) as Record<string, unknown>)
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS }
}

export const usePromptStore = defineStore('prompt', () => {
  const settings = ref<PlaygroundSettings>(loadSettings())
  const savedByPath = ref<Record<string, api.SavedPrompt[]>>({})

  function persistSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings.value))
    } catch {
      /* ignore */
    }
  }

  async function loadSavedPrompts(filePath: string): Promise<api.SavedPrompt[]> {
    const rows = await api.listByImage(filePath)
    savedByPath.value = { ...savedByPath.value, [filePath]: rows }
    return rows
  }

  async function savePrompt(body: api.SaveBody): Promise<number> {
    const { id } = await api.savePrompt(body)
    await loadSavedPrompts(body.file_path)
    return id
  }

  async function deleteSavedPrompt(id: number, filePath: string): Promise<void> {
    await api.deleteSavedPrompt(id)
    await loadSavedPrompts(filePath)
  }

  return {
    settings,
    savedByPath,
    persistSettings,
    loadSavedPrompts,
    savePrompt,
    deleteSavedPrompt,
  }
})
