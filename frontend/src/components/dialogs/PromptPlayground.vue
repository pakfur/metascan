<script setup lang="ts">
// Prompt Playground — a chat window with the active Qwen VLM.
//
// Opened from an image's context menu. Supports plain multi-turn chat and,
// optionally, sending the image as context (attached to the first user
// message; see backend/api/chat.py). The system prompt, temperature and
// max tokens are editable so prompting strategies can be tried quickly;
// system prompts can be saved as named presets.
//
// The old single-shot generate / transform / clean UI was replaced by this
// window. Its backend (/api/prompt/*) is untouched and still serves the
// T2I / I2V pipelines and the Saved Prompts metadata section.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, streamUrl, thumbnailUrl } from '../../api/client'
import { streamChat, type ChatMessageIn } from '../../api/chat'
import { fetchMediaDetails } from '../../api/media'
import { useChatStore, DEFAULT_SYSTEM_PROMPT, type ChatTurn } from '../../stores/chat'
import { useModelsStore } from '../../stores/models'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import type { Media } from '../../types/media'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const chatStore = useChatStore()
const modelsStore = useModelsStore()
const toast = useToast()

const key = props.media.file_path
chatStore.conversation(key) // ensure the slot exists before the computed reads it
const turns = computed<ChatTurn[]>(() => chatStore.conversations[key] ?? [])

const cardRef = ref<HTMLElement | null>(null)
const scrollRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)

const draft = ref('')
const showSettings = ref(false)
const streaming = ref(false)
const presetName = ref('')
let abortCtrl: AbortController | null = null

const embeddedPrompt = ref<string | null>(props.media.prompt ?? null)
const embeddedNegative = ref<string | null>(props.media.negative_prompt ?? null)

const isImage = computed(() => !props.media.is_video)
const imageUrl = computed(() =>
  isImage.value ? streamUrl(props.media.file_path) : thumbnailUrl(props.media.file_path),
)
const fileName = computed(
  () => props.media.file_name ?? props.media.file_path.split(/[\\/]/).pop() ?? '',
)
const includeImage = computed({
  get: () => isImage.value && chatStore.settings.include_image,
  set: (v: boolean) => {
    chatStore.settings.include_image = v
  },
})
const firstUserId = computed(() => turns.value.find((t) => t.role === 'user')?.id ?? null)
const lastAssistant = computed(() => {
  const t = turns.value[turns.value.length - 1]
  return t && t.role === 'assistant' ? t : null
})
const canSend = computed(
  () => !streaming.value && draft.value.trim().length > 0 && modelsStore.isVlmReady,
)
const systemIsDefault = computed(
  () => chatStore.settings.system_prompt.trim() === DEFAULT_SYSTEM_PROMPT,
)

watch(
  () => chatStore.settings,
  () => chatStore.persistSettings(),
  { deep: true },
)

onMounted(async () => {
  inputRef.value?.focus()
  scrollToBottom(true)
  // The grid hands us a summary row without the prompt; fetch the detail
  // so the embedded prompt can be inserted into the chat.
  try {
    const full = await fetchMediaDetails(props.media.file_path)
    embeddedPrompt.value = full.prompt ?? null
    embeddedNegative.value = full.negative_prompt ?? null
  } catch {
    /* non-fatal */
  }
})

onBeforeUnmount(() => {
  if (abortCtrl) abortCtrl.abort()
})

// ---- scrolling -----------------------------------------------------------

function nearBottom(): boolean {
  const el = scrollRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function scrollToBottom(force = false) {
  const stick = force || nearBottom()
  void nextTick(() => {
    const el = scrollRef.value
    if (el && stick) el.scrollTop = el.scrollHeight
  })
}

// ---- sending -------------------------------------------------------------

function historyForRequest(): ChatMessageIn[] {
  // Drop failed / empty assistant turns so a retry doesn't feed the model
  // its own error placeholder.
  return turns.value
    .filter((t) => !t.streaming)
    .filter((t) => t.role === 'user' || (!t.error && t.content.trim()))
    .map((t) => ({ role: t.role, content: t.content }))
}

async function runAssistant() {
  const conv = chatStore.conversation(key)
  const messages = historyForRequest()
  if (!messages.length || messages[messages.length - 1].role !== 'user') return

  conv.push(chatStore.newTurn('assistant', ''))
  const reply = conv[conv.length - 1] // reactive proxy
  reply.streaming = true
  streaming.value = true
  abortCtrl = new AbortController()
  scrollToBottom(true)

  try {
    await streamChat(
      {
        messages,
        system_prompt: chatStore.settings.system_prompt,
        file_path: props.media.file_path,
        include_image: includeImage.value,
        temperature: chatStore.settings.temperature,
        max_tokens: chatStore.settings.max_tokens,
      },
      (ev) => {
        switch (ev.type) {
          case 'delta':
            reply.content += ev.text
            scrollToBottom()
            break
          case 'reasoning':
            reply.reasoning = (reply.reasoning ?? '') + ev.text
            scrollToBottom()
            break
          case 'done':
            reply.elapsedMs = ev.elapsed_ms
            reply.modelId = ev.vlm_model_id
            break
          case 'error':
            reply.error = ev.message
            break
        }
      },
      abortCtrl.signal,
    )
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      reply.stopped = true
    } else {
      reply.error = e instanceof ApiError ? e.message : String(e)
    }
  } finally {
    reply.streaming = false
    streaming.value = false
    abortCtrl = null
    scrollToBottom()
    void nextTick(() => inputRef.value?.focus())
  }
}

async function send() {
  if (!canSend.value) return
  const text = draft.value
  draft.value = ''
  chatStore.conversation(key).push(chatStore.newTurn('user', text))
  await runAssistant()
}

function stop() {
  if (abortCtrl) abortCtrl.abort()
}

async function regenerate() {
  if (streaming.value || !lastAssistant.value) return
  chatStore.conversation(key).pop()
  await runAssistant()
}

function editTurn(turn: ChatTurn) {
  if (streaming.value) return
  if (draft.value.trim() && !window.confirm('Replace the text in the message box?')) return
  const conv = chatStore.conversation(key)
  const idx = conv.findIndex((t) => t.id === turn.id)
  if (idx < 0) return
  draft.value = turn.content
  conv.splice(idx)
  void nextTick(() => inputRef.value?.focus())
}

function newChat() {
  if (streaming.value) stop()
  if (turns.value.length && !window.confirm('Clear this conversation?')) return
  chatStore.clearConversation(key)
  inputRef.value?.focus()
}

function onComposerKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
  }
}

function insertIntoDraft(text: string) {
  draft.value = draft.value.trim() ? `${draft.value.trimEnd()}\n\n${text}` : text
  void nextTick(() => inputRef.value?.focus())
}

async function copy(text: string) {
  if (!text) return
  await copyToClipboard(text)
  toast.show('Copied to clipboard', 'success')
}

// ---- system prompt presets ------------------------------------------------

function loadPreset(name: string) {
  const p = chatStore.presets.find((x) => x.name === name)
  if (p) chatStore.settings.system_prompt = p.prompt
}

function savePreset() {
  const name = window.prompt('Save system prompt as:', presetName.value || '')
  if (!name?.trim()) return
  chatStore.savePreset(name.trim(), chatStore.settings.system_prompt)
  presetName.value = name.trim()
  toast.show(`Saved "${name.trim()}"`, 'success')
}

function deletePreset() {
  if (!presetName.value) return
  if (!window.confirm(`Delete preset "${presetName.value}"?`)) return
  chatStore.deletePreset(presetName.value)
  presetName.value = ''
}

function resetSystemPrompt() {
  chatStore.settings.system_prompt = DEFAULT_SYSTEM_PROMPT
  presetName.value = ''
}

function tryClose() {
  if (streaming.value) stop()
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="tryClose">
    <div
      ref="cardRef"
      class="chat-card"
      tabindex="-1"
      role="dialog"
      aria-label="Prompt Playground"
      @keydown.esc.stop="tryClose"
    >
      <header class="chat-header">
        <h3>Prompt Playground</h3>
        <span
          class="model-chip"
          :class="{ ready: modelsStore.isVlmReady }"
          :title="modelsStore.vlmError ?? ''"
        >
          <span class="dot" />
          {{ modelsStore.vlmModelId ?? 'no model' }} · {{ modelsStore.vlmState }}
        </span>
        <div class="header-actions">
          <button
            class="hdr-btn"
            :class="{ active: showSettings }"
            title="System prompt and sampling settings"
            @click="showSettings = !showSettings"
          >
            <i class="pi pi-sliders-h" /> Settings
          </button>
          <button class="hdr-btn" title="Start a new conversation" @click="newChat">
            <i class="pi pi-plus" /> New chat
          </button>
          <button class="close-btn" title="Close" aria-label="Close" @click="tryClose">×</button>
        </div>
      </header>

      <div class="chat-body">
        <!-- Image context -->
        <aside class="image-pane">
          <a :href="imageUrl" target="_blank" rel="noopener" class="image-link" title="Open full size">
            <img :src="imageUrl" :alt="fileName" class="preview-img" />
          </a>
          <div class="file-name" :title="media.file_path">{{ fileName }}</div>

          <label class="toggle" :class="{ disabled: !isImage }">
            <input v-model="includeImage" type="checkbox" :disabled="!isImage" />
            <span>
              Send image to the model
              <small v-if="isImage">Attached to the first message of the conversation.</small>
              <small v-else>Videos can't be sent to the VLM.</small>
            </span>
          </label>

          <div v-if="embeddedPrompt" class="embedded">
            <div class="embedded-head">
              <span class="label">Embedded prompt</span>
              <button class="link-btn" title="Copy" @click="copy(embeddedPrompt)">Copy</button>
              <button class="link-btn" title="Insert into the message box" @click="insertIntoDraft(embeddedPrompt)">Insert</button>
            </div>
            <div class="embedded-text">{{ embeddedPrompt }}</div>
            <template v-if="embeddedNegative">
              <div class="embedded-head">
                <span class="label">Negative</span>
                <button class="link-btn" @click="copy(embeddedNegative)">Copy</button>
                <button class="link-btn" @click="insertIntoDraft(embeddedNegative)">Insert</button>
              </div>
              <div class="embedded-text">{{ embeddedNegative }}</div>
            </template>
          </div>
        </aside>

        <!-- Conversation -->
        <section class="chat-pane">
          <div v-if="showSettings" class="settings">
            <div class="settings-row">
              <span class="label">System prompt</span>
              <select
                v-model="presetName"
                class="preset-select"
                @change="loadPreset(presetName)"
              >
                <option value="">Presets…</option>
                <option v-for="p in chatStore.presets" :key="p.name" :value="p.name">{{ p.name }}</option>
              </select>
              <button class="small-btn" @click="savePreset">Save as…</button>
              <button class="small-btn" :disabled="!presetName" @click="deletePreset">Delete</button>
              <button class="small-btn" :disabled="systemIsDefault" @click="resetSystemPrompt">Reset</button>
            </div>
            <textarea
              v-model="chatStore.settings.system_prompt"
              class="system-input"
              rows="5"
              spellcheck="false"
              placeholder="(no system prompt)"
            />
            <div class="settings-row sliders">
              <label>
                <span class="label">Temperature</span>
                <input v-model.number="chatStore.settings.temperature" type="range" min="0" max="1.5" step="0.05" />
                <span class="value">{{ chatStore.settings.temperature.toFixed(2) }}</span>
              </label>
              <label>
                <span class="label">Max tokens</span>
                <input v-model.number="chatStore.settings.max_tokens" type="range" min="64" max="4096" step="64" />
                <span class="value">{{ chatStore.settings.max_tokens }}</span>
              </label>
            </div>
          </div>

          <div ref="scrollRef" class="messages">
            <div v-if="!turns.length" class="empty">
              <p>Chat about anything, or ask about the image.</p>
              <p class="hint">
                Paste a long prompt to try a prompting strategy, or open
                <b>Settings</b> to change the system prompt.
                Enter sends · Shift+Enter adds a new line.
              </p>
            </div>

            <div
              v-for="t in turns"
              :key="t.id"
              class="msg"
              :class="[t.role, { errored: !!t.error }]"
            >
              <div class="bubble">
                <span
                  v-if="t.role === 'user' && t.id === firstUserId && includeImage"
                  class="img-chip"
                  title="The image is sent with this message"
                ><i class="pi pi-image" /> image</span>
                <details v-if="t.reasoning" class="reasoning">
                  <summary>Thinking</summary>
                  <div class="text">{{ t.reasoning }}</div>
                </details>
                <div v-if="t.content" class="text">{{ t.content }}</div>
                <div v-else-if="t.streaming" class="typing"><span /><span /><span /></div>
                <div v-if="t.error" class="error-text">{{ t.error }}</div>
              </div>
              <div class="msg-meta">
                <template v-if="t.role === 'assistant'">
                  <span v-if="t.stopped">stopped</span>
                  <span v-if="t.elapsedMs != null">{{ (t.elapsedMs / 1000).toFixed(1) }}s</span>
                  <button v-if="t.content" class="link-btn" @click="copy(t.content)">Copy</button>
                  <button
                    v-if="t.id === lastAssistant?.id && !streaming"
                    class="link-btn"
                    @click="regenerate"
                  >Regenerate</button>
                </template>
                <template v-else>
                  <button class="link-btn" @click="copy(t.content)">Copy</button>
                  <button class="link-btn" :disabled="streaming" title="Edit and resend from here" @click="editTurn(t)">Edit</button>
                </template>
              </div>
            </div>
          </div>

          <div v-if="!modelsStore.isVlmReady" class="not-ready">
            The VLM isn't loaded{{ modelsStore.isVlmLoading ? ' yet — it is starting up' : '' }}.
            Load a Qwen model from Settings → Models to chat.
          </div>

          <div class="composer">
            <textarea
              ref="inputRef"
              v-model="draft"
              class="composer-input"
              rows="3"
              placeholder="Message the model…"
              @keydown="onComposerKey"
            />
            <div class="composer-actions">
              <button v-if="streaming" class="stop-btn" @click="stop">
                <i class="pi pi-stop" /> Stop
              </button>
              <button v-else class="send-btn" :disabled="!canSend" @click="send">
                <i class="pi pi-send" /> Send
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
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

.chat-card {
  width: min(1200px, 96vw);
  height: min(860px, 92vh);
  display: flex;
  flex-direction: column;
  background: var(--surface-section);
  color: var(--text-color);
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  outline: none;
}

/* header */
.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--surface-border);
}
.chat-header h3 { margin: 0; font-size: 16px; }
.model-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--surface-border);
  color: var(--text-color-secondary);
}
.model-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-color-secondary); }
.model-chip.ready .dot { background: #22c55e; }
.header-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.hdr-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  font-size: 12px;
  background: transparent;
  color: inherit;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  cursor: pointer;
}
.hdr-btn:hover, .hdr-btn.active { background: var(--surface-hover); }
.close-btn { background: none; border: none; font-size: 22px; line-height: 1; cursor: pointer; color: inherit; padding: 0 4px; }

/* body */
.chat-body { flex: 1; min-height: 0; display: flex; }

.image-pane {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border-right: 1px solid var(--surface-border);
  overflow-y: auto;
  scrollbar-gutter: stable;
}
.image-link { display: block; }
.preview-img {
  width: 100%;
  max-height: 340px;
  object-fit: contain;
  background: #000;
  border-radius: 8px;
  display: block;
}
.file-name { font-size: 12px; color: var(--text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.toggle { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; cursor: pointer; }
.toggle input { margin-top: 3px; }
.toggle small { display: block; font-size: 11px; color: var(--text-color-secondary); }
.toggle.disabled { opacity: 0.55; cursor: not-allowed; }

.embedded { display: flex; flex-direction: column; gap: 4px; }
.embedded-head { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.embedded-head .label { margin-right: auto; }
.embedded-text {
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
  padding: 6px 8px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
}
.label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-color-secondary); }

.chat-pane { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }

/* settings */
.settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-ground);
}
.settings-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.settings-row .label { margin-right: auto; }
.settings-row.sliders { gap: 24px; }
.settings-row.sliders label { display: flex; align-items: center; gap: 8px; }
.settings-row.sliders .label { margin-right: 0; }
.value { font-size: 12px; font-variant-numeric: tabular-nums; min-width: 36px; }
.preset-select, .small-btn {
  font-size: 12px;
  padding: 3px 8px;
  background: var(--surface-card);
  color: inherit;
  border: 1px solid var(--surface-border);
  border-radius: 4px;
}
.small-btn { cursor: pointer; }
.small-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.system-input {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 60px;
  max-height: 40vh;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  padding: 8px;
  background: var(--surface-card);
  color: inherit;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
}

/* messages */
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.empty { margin: auto; text-align: center; color: var(--text-color-secondary); max-width: 420px; }
.empty p { margin: 4px 0; }
.empty .hint { font-size: 12px; }

.msg { display: flex; flex-direction: column; max-width: 85%; }
.msg.user { align-self: flex-end; align-items: flex-end; }
.msg.assistant { align-self: flex-start; align-items: flex-start; }
.bubble {
  padding: 8px 12px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.5;
  max-width: 100%;
}
.msg.user .bubble { background: var(--primary-color); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: var(--surface-hover); border-bottom-left-radius: 4px; }
.msg.errored .bubble { border: 1px solid var(--danger-color); }
.text { white-space: pre-wrap; word-break: break-word; }
.error-text { color: var(--danger-color); font-size: 12px; margin-top: 4px; }
.img-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 1px 6px;
  margin-bottom: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.2);
}
.reasoning { font-size: 12px; color: var(--text-color-secondary); margin-bottom: 6px; }
.reasoning summary { cursor: pointer; }
.reasoning .text { padding: 4px 0 0 10px; border-left: 2px solid var(--surface-border); }

.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-color-secondary);
  margin-top: 3px;
  min-height: 16px;
}

.typing { display: inline-flex; gap: 4px; padding: 4px 0; }
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-color-secondary);
  animation: blink 1.2s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }

.not-ready {
  margin: 0 14px 8px;
  padding: 8px 10px;
  font-size: 12px;
  border-radius: 6px;
  border: 1px solid var(--danger-color);
  color: var(--danger-color);
}

/* composer */
.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 10px 14px 14px;
  border-top: 1px solid var(--surface-border);
}
.composer-input {
  flex: 1;
  resize: vertical;
  min-height: 44px;
  max-height: 45vh;
  box-sizing: border-box;
  font-family: inherit;
  font-size: 14px;
  padding: 8px 10px;
  background: var(--surface-card);
  color: inherit;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
}
.composer-input:focus { outline: none; border-color: var(--primary-color); }
.send-btn, .stop-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
}
.send-btn { background: var(--primary-color); color: #fff; border: none; }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.stop-btn { background: transparent; color: inherit; border: 1px solid var(--surface-border); }

.link-btn { background: none; border: none; padding: 0; color: var(--primary-color); cursor: pointer; font-size: 11px; }
.link-btn:disabled { opacity: 0.4; cursor: not-allowed; }

@media (max-width: 767px) {
  .chat-card { width: 100vw; height: 100vh; border-radius: 0; }
  .chat-body { flex-direction: column; }
  .image-pane {
    width: auto;
    max-height: 30vh;
    border-right: none;
    border-bottom: 1px solid var(--surface-border);
  }
  .preview-img { max-height: 120px; }
  .chat-header { flex-wrap: wrap; }
  .settings { max-height: 40vh; overflow-y: auto; }
  .msg { max-width: 95%; }
  .hdr-btn { padding: 5px 7px; }
}
</style>
