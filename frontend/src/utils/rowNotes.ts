// What a list of rows learnt from the server while a request for rows was
// out: a ★, a grade, an autosaved form.
//
// A reply carries the server's fields as they were when the server answered,
// and a request that goes out before a change can be answered before it yet
// arrive after it: dropping the reply's rows in would undo the change (a
// tile's ✓ / ✗ and ★, a T2I scorecard blind with them, or the form a tile
// load fills, which the next autosave would then write back over the newer
// one). So every field the client gets back from the server for a row is
// noted with a tick, every request that brings rows back takes a tick as it
// goes out, and each row its reply brings gets back the notes made after
// that tick. A note made before a request went out never is: the server had
// already answered it, so the request saw it (or something newer). That
// holds because the server commits a change before it answers it, and every
// read goes through the one database lock after the writes before it.
//
// Only this tab's answers are noted. A field changed in another tab reaches
// a row through a reply; until a request asked after this tab's last note
// for that field comes back, the note can cover it.

export interface RowNotes<R extends object> {
  /** Fields the server answered for the row at `key`. */
  note(key: string, fields: Partial<R>): void
  /** A request that brings rows back is going out: its tick. */
  ask(): number
  /** Puts back on `row`, the row at `key`, each field noted after `askedAt`. */
  settle(key: string, row: R, askedAt: number): void
  /** The request asked at `askedAt` is over, answered or not. */
  done(askedAt: number): void
}

interface Noted {
  value: unknown
  at: number
}

export function makeRowNotes<R extends object>(): RowNotes<R> {
  let tick = 0
  // The ticks of the requests still out.
  const out = new Set<number>()
  // key -> field -> the value last answered and when.
  const notes = new Map<string, Map<keyof R, Noted>>()

  return {
    note(key, fields) {
      const at = ++tick
      let row = notes.get(key)
      if (!row) notes.set(key, (row = new Map()))
      for (const field of Object.keys(fields) as (keyof R)[]) {
        const value = fields[field]
        if (value !== undefined) row.set(field, { value, at })
      }
    },
    ask() {
      const at = ++tick
      out.add(at)
      return at
    },
    settle(key, row, askedAt) {
      for (const [field, n] of notes.get(key) ?? []) {
        if (n.at > askedAt) row[field] = n.value as R[keyof R]
      }
    },
    done(askedAt) {
      out.delete(askedAt)
      // A note older than every request still out can never be needed again.
      let oldest = Infinity
      for (const at of out) oldest = Math.min(oldest, at)
      for (const [key, row] of notes) {
        for (const [field, n] of row) if (n.at < oldest) row.delete(field)
        if (row.size === 0) notes.delete(key)
      }
    },
  }
}
