// Small helper for console-level perf diagnostics. Logs are prefixed so
// they're easy to filter ("[perf]") in DevTools. Use sparingly — reserved
// for user-visible operations where a regression would matter (startup
// loads, per-selection detail fetch).
//
// The goal is to surface *where* time is spent so the user can tell
// whether a slow page load is in the network, server work, or
// client-side assignment/rendering.

export async function timed<T>(label: string, fn: () => Promise<T>): Promise<T> {
  const t0 = performance.now()
  try {
    return await fn()
  } finally {
    // eslint-disable-next-line no-console
    console.info(`[perf] ${label}: ${(performance.now() - t0).toFixed(0)}ms`)
  }
}

export function now(): number {
  return performance.now()
}

export function since(t0: number): string {
  return `${(performance.now() - t0).toFixed(0)}ms`
}
