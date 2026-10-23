import { computed, nextTick, ref, watch, type ComputedRef, type Ref } from 'vue'
import { useToast } from './useToast'
import { isAdapterWriterId, writerName, type WriterAdapter, type WriterOption } from '../types/writers'

// The Writer pick of the T2I / I2V dialogs, per browser (never part of a
// form_state). Two values:
//
// - `picked`: what the user chose, persisted. A transient fallback never
//   touches it: an adapter that leaves the menu (out of the selected
//   categories, too few examples for the selected model, briefly not
//   servable, refused by the writer) comes back by itself when its option
//   returns. Only an adapter that no longer exists at all (deleted: gone
//   from GET /api/writers/adapters) drops the pick to Base.
// - `writer`: the effective writer every request sends — the pick while its
//   option is offered and enabled, Base otherwise. The menu shows it.
//
// A fallback says so with a toast; so does the return.

export interface WriterPickOptions {
  storageKey: string
  /** The built-in writer ids ('base', 'base+taste'). */
  builtinIds: readonly string[]
  adapters: () => WriterAdapter[]
  adaptersLoaded: () => boolean
  /** The adapter entries the menu offers now. */
  options: () => WriterOption[]
}

export interface WriterPick<W extends string> {
  picked: Ref<string>
  writer: ComputedRef<W>
  /** A menu choice: a built-in id or an enabled adapter option. */
  setWriter: (v: string) => void
  /** The writer refused `id` (409 adapter_unavailable): use Base until the adapter list changes. */
  refuse: (id: string) => void
  /** `adapters_changed`: a refused adapter may be servable again. */
  clearRefusal: () => void
  /** The display name of a writer id, also for an adapter that has just left the list. */
  nameOf: (id: string) => string
}

export function useWriterPick<W extends string>(o: WriterPickOptions): WriterPick<W> {
  const toast = useToast()

  function read(): string {
    try {
      const v = localStorage.getItem(o.storageKey)
      return v !== null && (o.builtinIds.includes(v) || isAdapterWriterId(v)) ? v : 'base'
    } catch {
      return 'base'
    }
  }
  function persist(v: string): void {
    try {
      localStorage.setItem(o.storageKey, v)
    } catch {
      /* private mode: the choice lasts for this session */
    }
  }

  // Names of every adapter seen, so a toast can name one that has just left.
  const knownNames = new Map<string, string>()
  watch(
    o.adapters,
    (rows) => rows.forEach((a) => knownNames.set(a.writer_id, a.name)),
    { immediate: true },
  )
  function nameOf(id: string): string {
    return knownNames.get(id) ?? writerName(o.adapters(), id)
  }

  const picked = ref<string>(read())
  const refused = ref<string | null>(null)

  const writer = computed<W>(() => {
    const p = picked.value
    if (!isAdapterWriterId(p)) return p as W
    if (refused.value === p) return 'base' as W
    // Until the list first arrives the pick stands (the server refuses a gone one).
    if (!o.adaptersLoaded()) return p as W
    const option = o.options().find((x) => x.id === p)
    return (option && !option.disabled ? p : 'base') as W
  })

  // Deleted (absent from the list, which never carries deleted rows): the
  // pick itself goes back to Base. Declared before the fallback watcher so
  // it runs first in a flush and that watcher sees the pick change.
  watch(
    [o.adapters, o.adaptersLoaded],
    () => {
      const p = picked.value
      if (!isAdapterWriterId(p) || !o.adaptersLoaded()) return
      if (o.adapters().some((a) => a.writer_id === p)) return
      picked.value = 'base'
      persist('base')
      if (refused.value === p) refused.value = null
      toast.show(`Writer ${nameOf(p)} was deleted; using Base.`, 'warn', 6000)
    },
    { immediate: true },
  )

  // Same pick, different effective writer: a fallback or a return (not when
  // the user's own menu choice caused it).
  let userChoice = false
  watch([writer, picked], ([now, pickNow], [before, pickBefore]) => {
    const own = userChoice
    userChoice = false
    if (own || pickNow !== pickBefore || !isAdapterWriterId(pickNow) || now === before) return
    const name = nameOf(pickNow)
    if (before === pickNow) {
      const reason = refused.value === pickNow ? 'is no longer available' : 'is not offered here right now'
      toast.show(`Writer ${name} ${reason}; using Base until it is back.`, 'warn', 6000)
    } else if (now === pickNow) {
      toast.show(`Writer ${name} is back.`, 'info', 4000)
    }
  })

  function setWriter(v: string): void {
    const option = o.options().find((x) => x.id === v)
    if (!o.builtinIds.includes(v) && !(option && !option.disabled)) return
    // Cleared after this flush even when nothing changed.
    userChoice = true
    void nextTick(() => {
      userChoice = false
    })
    picked.value = v
    persist(v)
    if (refused.value !== null && refused.value === v) refused.value = null
  }

  function refuse(id: string): void {
    if (isAdapterWriterId(id) && id === picked.value) refused.value = id
  }

  function clearRefusal(): void {
    refused.value = null
  }

  return { picked, writer, setWriter, refuse, clearRefusal, nameOf }
}
