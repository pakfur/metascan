// A lazily loaded set of file paths behind a synchronous `has()`.
//
// Built for smart-folder rules whose membership lives on the server (the
// "Generated with T2I" rule): the synchronous rule evaluator reads `has()`,
// while `ensure()` / `refresh()` fetch the set. It loads only when something
// needs it -- `isReferenced()` says a saved folder uses the rule, or the
// caller passes `always` (the open editor) -- and a failed fetch leaves
// whatever was loaded before untouched.
//
// `ensure()` and `refresh()` resolve `true` when the set changed, so the
// caller knows to bump whatever version counter its computeds watch.

export interface PathSetCache {
  /** Is `path` in the loaded set? Always false until a set has loaded. */
  has(path: string): boolean
  /** Load once, if anything uses the set (or `always`); a no-op afterwards. */
  ensure(always?: boolean): Promise<boolean>
  /** Refetch after the underlying data changed. A no-op while nothing has
   * loaded the set and no folder references it. */
  refresh(): Promise<boolean>
}

export function makePathSetCache(
  fetchPaths: () => Promise<string[]>,
  isReferenced: () => boolean,
): PathSetCache {
  let paths: Set<string> | null = null
  // Requests are numbered in the order they start; a reply is applied unless
  // a newer request's reply has already been applied, so a stale reply that
  // lands last can never overwrite fresher data.
  let started = 0
  let applied = 0

  async function load(): Promise<boolean> {
    const mine = ++started
    try {
      const rows = await fetchPaths()
      if (mine < applied) return false
      applied = mine
      paths = new Set(rows)
      return true
    } catch {
      // Leave the cache as it was; a later ensure()/refresh() retries.
      return false
    }
  }

  return {
    has: (path) => paths?.has(path) ?? false,
    async ensure(always = false) {
      if (paths !== null) return false
      if (!always && !isReferenced()) return false
      return load()
    },
    async refresh() {
      if (paths === null && !isReferenced()) return false
      return load()
    },
  }
}
