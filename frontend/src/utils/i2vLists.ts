import type { I2vVideo } from '../types/i2v'

/**
 * Fold a fresh newest-first page of the all-videos list into the rows already
 * held (also newest first): the page, then every held row older than the
 * page's oldest id. A held row inside the page's id range that the page lacks
 * is gone (deleted); rows beyond the range were never asked about and stay,
 * so an event refresh does not throw away the pages "load older" fetched.
 */
export function mergeNewestPage(page: I2vVideo[], held: I2vVideo[]): I2vVideo[] {
  if (!page.length) return []
  const oldest = page[page.length - 1].id
  return [...page, ...held.filter((v) => v.id < oldest)]
}
