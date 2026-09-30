import { getCurrentScope, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

export interface FormAutosaveOptions<F extends object, I extends string | number = number> {
  /**
   * The form's current values, as one plain object. Read inside the
   * getter (or computed) so the autosave sees every field change, nested
   * arrays and objects included.
   */
  form: MaybeRefOrGetter<F>
  /** The item whose form_state edits are saved into; null = a scratch form, nothing is saved. */
  selectedId: MaybeRefOrGetter<I | null>
  /** Persists `fields` into item `id`. A rejection marks the save as failed. */
  save: (id: I, fields: Partial<F>) => Promise<unknown>
  delayMs?: number
  /**
   * Trims the form to what may be sent, e.g. leaves out a number field that
   * is mid-edit and empty, so its last good value stays saved and everything
   * else still goes through. Default: the whole form.
   */
  savable?: (form: F) => Partial<F>
}

export interface FormAutosave {
  /** The newest save for the selected item failed; the next edit (or flush) retries. */
  saveFailed: Ref<boolean>
  /** Save now if the form differs from what was last loaded or saved. */
  flush: () => void
  /** Drop a scheduled save without sending it (the item is about to be deleted). */
  cancelPending: () => void
  /**
   * Call right after loading an item's values into the form: the form now
   * equals what the server holds, so loading must not echo back as a save.
   */
  markLoaded: () => void
}

/**
 * Debounced, serialised autosave of a form into the selected item's
 * form_state, generalised from I2VDialog.vue (per-clip form state).
 *
 * - Saves run one at a time, in order, so a slow early request can never
 *   land after, and overwrite, a later one.
 * - The item id and a deep copy of the fields are captured when a save is
 *   queued, not when it runs: by then the user may have selected another item
 *   or edited a nested value in place.
 * - A snapshot of the form as last loaded or saved decides whether a flush
 *   has anything to send. A failed save leaves it alone, so the next edit
 *   (or the close-time flush) retries.
 *
 * Switching items: call flush() BEFORE changing selectedId or the form (it
 * saves the pending edits of the item being left), then load the new values,
 * set selectedId, and call markLoaded().
 *
 * Also flushes when the owning component (or effect scope) is disposed.
 */
export function useFormAutosave<F extends object, I extends string | number = number>(
  options: FormAutosaveOptions<F, I>,
): FormAutosave {
  const delayMs = options.delayMs ?? 600
  const saveFailed = ref(false)
  // JSON of the form as last loaded or saved.
  let savedSnapshot = ''
  let timer: ReturnType<typeof setTimeout> | null = null
  let chain: Promise<void> = Promise.resolve()

  const snapshotOf = (): string => JSON.stringify(toValue(options.form))

  function cancelPending(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function flush(): void {
    cancelPending()
    const id = toValue(options.selectedId)
    if (id == null) return
    const snapshot = JSON.stringify(toValue(options.form))
    if (snapshot === savedSnapshot) return
    const plain = JSON.parse(snapshot) as F
    const fields: Partial<F> = options.savable ? options.savable(plain) : plain
    chain = chain.then(async () => {
      try {
        await options.save(id, fields)
        // Only the item still on screen gets to update the bookkeeping.
        if (toValue(options.selectedId) === id) {
          savedSnapshot = snapshot
          saveFailed.value = false
        }
      } catch {
        // Snapshot left alone, so the next edit (or close) retries.
        if (toValue(options.selectedId) === id) saveFailed.value = true
      }
    })
  }

  function markLoaded(): void {
    cancelPending()
    savedSnapshot = snapshotOf()
    saveFailed.value = false
  }

  watch(snapshotOf, () => {
    if (toValue(options.selectedId) == null) return
    cancelPending()
    timer = setTimeout(flush, delayMs)
  })

  if (getCurrentScope()) onScopeDispose(flush)

  return { saveFailed, flush, cancelPending, markLoaded }
}
