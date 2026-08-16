import type { Beat, Panel, Scene, Subject } from '../types/storyboard'

// Natural-language phrasing for a beat's camera motion triple. Mirrors the
// enum values in types/storyboard.ts (CAMERA_MOTIONS/AMPLITUDES/SPEEDS) —
// falls back to the raw enum string for a motion this map doesn't know
// about, so a future vocabulary addition degrades gracefully instead of
// throwing.
const MOTION_PHRASE: Record<string, string> = {
  zoom_in: 'zoom in',
  zoom_out: 'zoom out',
  push_in: 'push in',
  pull_out: 'pull out',
  pan_left: 'pan left',
  pan_right: 'pan right',
  truck_left: 'truck left',
  truck_right: 'truck right',
  tilt_up: 'tilt up',
  tilt_down: 'tilt down',
  pedestal_up: 'pedestal up',
  pedestal_down: 'pedestal down',
  arc: 'arc around',
  tracking: 'tracking shot',
  static: 'static',
  shake_slight: 'slight shake',
  shake_strong: 'strong shake',
  pov: 'POV',
  roll_cw: 'roll clockwise',
  roll_ccw: 'roll counter-clockwise',
}

function cameraLine(beat: Beat): string | null {
  if (!beat.camera_motion) return null
  const parts = [MOTION_PHRASE[beat.camera_motion] ?? beat.camera_motion]
  if (beat.camera_amplitude) parts.push(`${beat.camera_amplitude} amplitude`)
  if (beat.camera_speed) parts.push(beat.camera_speed)
  return parts.join(', ')
}

function formatTimecode(seconds: number): string {
  return `${seconds.toFixed(1)}s`
}

function speakerLabel(subjectId: number | null, voice: string | null, subjects: Subject[]): string {
  if (subjectId !== null) {
    const subject = subjects.find((s) => s.id === subjectId)
    if (subject) return subject.name
  }
  return voice ?? 'Voice'
}

/**
 * Renders a human-readable beat sheet for a panel: a header describing the
 * shot, then one block per beat with cumulative timecodes, camera phrasing,
 * sound, and dialog. Pure function — no store/API access, safe to call from
 * any component or a future export/print path.
 */
export function buildShotScript(panel: Panel, scene: Scene, subjects: Subject[]): string {
  const lines: string[] = []

  const shotBits = [panel.shot_size, panel.angle, panel.lens].filter(
    (v): v is string => v !== null && v !== '',
  )
  const shotLabel = shotBits.length ? ` (${shotBits.join(', ')})` : ''
  lines.push(`${scene.name} — ${panel.action}${shotLabel}`)
  lines.push(`Duration: ${formatTimecode(panel.duration_s)}`)
  lines.push('')

  let cursor = 0
  const beats = [...panel.beats].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
  if (beats.length === 0) {
    lines.push('(no beats)')
    return lines.join('\n')
  }

  for (const beat of beats) {
    const start = cursor
    const end = cursor + beat.duration_s
    cursor = end
    const cutMarker = beat.is_cut ? ' (CUT)' : ''
    lines.push(`[${formatTimecode(start)} – ${formatTimecode(end)}]${cutMarker} ${beat.action}`)

    const camera = cameraLine(beat)
    if (camera) lines.push(`  Camera: ${camera}`)
    if (beat.sound) lines.push(`  Sound: ${beat.sound}`)

    for (const line of beat.dialog) {
      const speaker = speakerLabel(line.subject_id, line.voice, subjects)
      const delivery = line.delivery ? `${line.delivery}, ${line.language}` : line.language
      lines.push(`  ${speaker} (${delivery}): "${line.text}"`)
    }

    lines.push('')
  }

  return lines.join('\n').trimEnd()
}
