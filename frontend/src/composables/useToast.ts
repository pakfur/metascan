import { ref } from 'vue'

interface ToastState {
  message: string
  kind: 'success' | 'warn' | 'info'
}

// Single-slot toast — a second call replaces the first rather than queueing.
// Matches the prototype: brief, non-blocking confirmation.
const state = ref<ToastState | null>(null)
let hideTimer: ReturnType<typeof setTimeout> | null = null

export function useToast() {
  function show(message: string, kind: ToastState['kind'] = 'success', duration = 2200) {
    state.value = { message, kind }
    if (hideTimer) clearTimeout(hideTimer)
    hideTimer = setTimeout(() => {
      state.value = null
      hideTimer = null
    }, duration)
  }

  function dismiss() {
    if (hideTimer) clearTimeout(hideTimer)
    hideTimer = null
    state.value = null
  }

  return { state, show, dismiss }
}
