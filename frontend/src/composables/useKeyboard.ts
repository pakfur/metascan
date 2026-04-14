import { onMounted, onUnmounted } from 'vue'

interface ShortcutDef {
  key: string
  ctrl?: boolean
  shift?: boolean
  alt?: boolean
  handler: () => void
}

export function useKeyboard(shortcuts: ShortcutDef[]) {
  function onKeyDown(e: KeyboardEvent) {
    // Don't fire shortcuts when typing in inputs
    const tag = (e.target as HTMLElement)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

    for (const s of shortcuts) {
      const ctrlMatch = !!s.ctrl === (e.ctrlKey || e.metaKey)
      const shiftMatch = !!s.shift === e.shiftKey
      const altMatch = !!s.alt === e.altKey
      if (e.key.toLowerCase() === s.key.toLowerCase() && ctrlMatch && shiftMatch && altMatch) {
        e.preventDefault()
        s.handler()
        return
      }
    }
  }

  onMounted(() => window.addEventListener('keydown', onKeyDown))
  onUnmounted(() => window.removeEventListener('keydown', onKeyDown))
}
