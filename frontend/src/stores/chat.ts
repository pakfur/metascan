import { defineStore } from 'pinia'
import { ref } from 'vue'

// Settings + saved system prompts persist in localStorage; conversations
// live in memory only (one per image, kept until the page reloads or the
// user hits "New chat").

const SETTINGS_KEY = 'metascan_chat_settings'
const PRESETS_KEY = 'metascan_chat_system_presets'

export const DEFAULT_SYSTEM_PROMPT = 'You are a helpful, knowledgeable assistant.'

export interface ChatSettings {
  system_prompt: string
  temperature: number
  max_tokens: number
  include_image: boolean
}

export interface SystemPreset {
  name: string
  prompt: string
}

export interface ChatTurn {
  id: number
  role: 'user' | 'assistant'
  content: string
  reasoning?: string
  elapsedMs?: number
  modelId?: string
  error?: string
  stopped?: boolean
  streaming?: boolean
}

const DEFAULTS: ChatSettings = {
  system_prompt: DEFAULT_SYSTEM_PROMPT,
  temperature: 0.7,
  max_tokens: 1024,
  include_image: true,
}

function loadSettings(): ChatSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      const p = JSON.parse(raw) as Partial<ChatSettings>
      return {
        system_prompt: typeof p.system_prompt === 'string' ? p.system_prompt : DEFAULTS.system_prompt,
        temperature: typeof p.temperature === 'number' ? p.temperature : DEFAULTS.temperature,
        max_tokens: typeof p.max_tokens === 'number' ? p.max_tokens : DEFAULTS.max_tokens,
        include_image: typeof p.include_image === 'boolean' ? p.include_image : DEFAULTS.include_image,
      }
    }
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS }
}

function loadPresets(): SystemPreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY)
    if (raw) {
      const p = JSON.parse(raw) as unknown
      if (Array.isArray(p)) {
        return p.filter(
          (x): x is SystemPreset =>
            !!x && typeof x.name === 'string' && typeof x.prompt === 'string',
        )
      }
    }
  } catch {
    /* ignore */
  }
  return []
}

let nextId = 1

export const useChatStore = defineStore('chat', () => {
  const settings = ref<ChatSettings>(loadSettings())
  const presets = ref<SystemPreset[]>(loadPresets())
  const conversations = ref<Record<string, ChatTurn[]>>({})

  function persistSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings.value))
    } catch {
      /* ignore */
    }
  }

  function persistPresets() {
    try {
      localStorage.setItem(PRESETS_KEY, JSON.stringify(presets.value))
    } catch {
      /* ignore */
    }
  }

  function savePreset(name: string, prompt: string) {
    const idx = presets.value.findIndex((p) => p.name === name)
    if (idx >= 0) presets.value[idx] = { name, prompt }
    else presets.value.push({ name, prompt })
    presets.value.sort((a, b) => a.name.localeCompare(b.name))
    persistPresets()
  }

  function deletePreset(name: string) {
    presets.value = presets.value.filter((p) => p.name !== name)
    persistPresets()
  }

  function conversation(key: string): ChatTurn[] {
    if (!conversations.value[key]) conversations.value[key] = []
    return conversations.value[key]
  }

  function clearConversation(key: string) {
    conversations.value[key] = []
  }

  function newTurn(role: ChatTurn['role'], content: string): ChatTurn {
    return { id: nextId++, role, content }
  }

  return {
    settings,
    presets,
    conversations,
    persistSettings,
    savePreset,
    deletePreset,
    conversation,
    clearConversation,
    newTurn,
  }
})
