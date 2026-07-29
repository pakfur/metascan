export interface Point {
  x: number
  y: number
}

export type SwipeAction = 'next' | 'prev' | 'close' | 'none'

/**
 * Classify a single-finger release (from zoom==1) into a navigation action.
 * Horizontal wins ties. A leftward swipe goes to the next item; a rightward
 * swipe to the previous; a downward swipe closes. Movement below both
 * thresholds is a tap (`none`).
 */
export function classifySwipe(
  dx: number,
  dy: number,
  opts: { hMin: number; vMin: number },
): SwipeAction {
  const adx = Math.abs(dx)
  const ady = Math.abs(dy)
  if (adx < opts.hMin && ady < opts.vMin) return 'none'
  if (adx >= ady) return dx < 0 ? 'next' : 'prev'
  return dy > 0 ? 'close' : 'none'
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

export function midpoint(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}

export function clampZoom(z: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, z))
}
