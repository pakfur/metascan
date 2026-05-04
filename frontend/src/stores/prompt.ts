import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/prompt'

const STORAGE_KEY = 'metascan_prompt_playground_settings'

export interface PlaygroundSettings {
  target_model: api.TargetModel
  architecture: api.Architecture
  styles: api.StyleEnhancement[]
  temperature: number
  max_tokens: number
}

const DEFAULTS: PlaygroundSettings = {
  target_model: 'sdxl',
  architecture: 't2i',
  styles: [],
  temperature: 0.6,
  max_tokens: 250,
}

function loadSettings(): PlaygroundSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PlaygroundSettings>
      return { ...DEFAULTS, ...parsed }
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
