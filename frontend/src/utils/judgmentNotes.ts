// The ★ and grades a list of rows learnt while a request for rows was out.
//
// A reply carries the server's ★ and grade as they were when the server
// answered, and a request that goes out before a judgment can be answered
// before it yet arrive after it: dropping the reply's rows in would undo the
// judgment (a tile's ✓ / ✗ and ★, and a T2I scorecard blind with them). So
// every ★ and grade the client gets back from the server is noted with a
// tick, every request that brings rows back takes a tick as it goes out, and
// each row its reply brings gets back the notes made after that tick. A note
// made before a request went out never is: the server had already answered
// it, so the request saw it (or something newer). That holds because the
// server commits a judgment before it answers it, and every read goes
// through the one database lock after the writes before it.
//
// Only this tab's answers are noted. A ★ or grade changed in another tab
// reaches a row through a reply; until a request asked after this tab's last
// note for that file comes back, the note can cover it.

import type { Grade } from '../types/judgments'

/** The judgment fields a row carries. */
export interface JudgedRow {
  is_favorite: boolean
  grade?: Grade | null
}

export interface JudgmentNotes {
  /** A ★ and/or grade the server answered for the file at `key`. */
  note(key: string, fields: { favorite?: boolean; grade?: Grade | null }): void
  /** A request that brings rows back is going out: its tick. */
  ask(): number
  /** Puts back on `row`, the file at `key`, each note made after `askedAt`. */
  settle(key: string, row: JudgedRow, askedAt: number): void
  /** The request asked at `askedAt` is over, answered or not. */
  done(askedAt: number): void
}

interface Noted<T> {
  value: T
  at: number
}

// Notes older than every request still out can never be needed again.
function prune<T>(notes: Map<string, Noted<T>>, oldest: number): void {
  for (const [key, n] of notes) if (n.at < oldest) notes.delete(key)
}

export function makeJudgmentNotes(): JudgmentNotes {
  let tick = 0
  // The ticks of the requests still out.
  const out = new Set<number>()
  const favorites = new Map<string, Noted<boolean>>()
  const grades = new Map<string, Noted<Grade | null>>()

  return {
    note(key, fields) {
      const at = ++tick
      if (fields.favorite !== undefined) favorites.set(key, { value: fields.favorite, at })
      if (fields.grade !== undefined) grades.set(key, { value: fields.grade, at })
    },
    ask() {
      const at = ++tick
      out.add(at)
      return at
    },
    settle(key, row, askedAt) {
      const favorite = favorites.get(key)
      if (favorite && favorite.at > askedAt && row.is_favorite !== favorite.value) {
        row.is_favorite = favorite.value
      }
      const grade = grades.get(key)
      if (grade && grade.at > askedAt && (row.grade ?? null) !== grade.value) row.grade = grade.value
    },
    done(askedAt) {
      out.delete(askedAt)
      let oldest = Infinity
      for (const at of out) oldest = Math.min(oldest, at)
      prune(favorites, oldest)
      prune(grades, oldest)
    },
  }
}
