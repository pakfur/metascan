import { onScopeDispose } from 'vue'

// A single click that waits to see whether it becomes a double-click. The
// timer lives in the closure, so each caller (each tile) has its own.
export function makeDelayedClick<E>(
  setTimer: (fn: () => void, ms: number) => unknown,
  clearTimer: (t: unknown) => void,
  onSingle: (e: E) => void,
  onDouble: () => void,
  delayMs = 250,
) {
  let pending: unknown = null
  const cancel = () => {
    if (pending !== null) clearTimer(pending)
    pending = null
  }
  return {
    onClick(e: E) {
      cancel()
      pending = setTimer(() => {
        pending = null
        onSingle(e)
      }, delayMs)
    },
    onDblclick() {
      cancel()
      onDouble()
    },
    cancel,
  }
}

export function useDelayedClick(
  onSingle: (e: MouseEvent) => void,
  onDouble: () => void,
  delayMs = 250,
) {
  const c = makeDelayedClick<MouseEvent>(
    (fn, ms) => setTimeout(fn, ms),
    (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
    onSingle,
    onDouble,
    delayMs,
  )
  onScopeDispose(c.cancel)
  return { onClick: c.onClick, onDblclick: c.onDblclick }
}
