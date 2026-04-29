const LABELS: Record<number, string> = {
  1: 'Landscape',
  2: 'Landscape (mirrored)',
  3: 'Landscape (rotated 180°)',
  4: 'Landscape (mirrored, rotated 180°)',
  5: 'Portrait (mirrored, rotated 270°)',
  6: 'Portrait',
  7: 'Portrait (mirrored, rotated 90°)',
  8: 'Portrait (rotated 270°)',
}

export function orientationLabel(value: number | null | undefined): string | null {
  if (value == null) return null
  return LABELS[value] ?? `Unknown orientation (${value})`
}
