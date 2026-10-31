// Mirrors ProcessStatus.to_dict() in backend/services/background/protocol.py.
// Nothing here branches on a process id: the panels render whatever the
// registry returns.

export type ProcessState =
  | 'idle'
  | 'running'
  | 'waiting'
  | 'held'
  | 'paused'
  | 'stopped'
  | 'failed'

export type ProcessAction = 'start' | 'pause' | 'resume' | 'stop'

export interface ProcessCapabilities {
  can_start: boolean
  can_pause: boolean
  can_stop: boolean
  confirm_stop: boolean
}

export interface ProcessProgress {
  done: number
  total: number
  unit: string
}

export interface BackgroundProcess {
  id: string
  label: string
  order: number
  state: ProcessState
  progress: ProcessProgress | null
  activity: string
  held_by: string[]
  error: string | null
  warnings_count: number
  capabilities: ProcessCapabilities
  updated_at: number
}

export interface PillSummary {
  spinning: boolean
  count: number
  failed: boolean
}

/** The pill spins with a count only while something is running; held, waiting
 *  and paused processes are not doing anything. Any failure shows red. */
export function pillSummary(list: readonly BackgroundProcess[]): PillSummary {
  const count = list.filter((p) => p.state === 'running').length
  return {
    spinning: count > 0,
    count,
    failed: list.some((p) => p.state === 'failed'),
  }
}

export interface PlayPause {
  kind: 'play' | 'pause'
  action: 'start' | 'pause' | 'resume'
  disabled: boolean
}

/** One button, two faces. Paused resumes; stopped, failed and idle start (when
 *  the capability says); running, waiting and held pause (when it says). */
export function playPauseButton(p: BackgroundProcess): PlayPause {
  const caps = p.capabilities
  switch (p.state) {
    case 'paused':
      return { kind: 'play', action: 'resume', disabled: false }
    case 'stopped':
    case 'failed':
    case 'idle':
      return { kind: 'play', action: 'start', disabled: !caps.can_start }
    case 'running':
    case 'waiting':
    case 'held':
      return { kind: 'pause', action: 'pause', disabled: !caps.can_pause }
  }
  return { kind: 'play', action: 'start', disabled: true }
}

/** "Paused for X" for a held panel, unless its activity already says so. */
export function heldLine(p: BackgroundProcess, all: readonly BackgroundProcess[]): string {
  if (p.state !== 'held' || p.held_by.length === 0) return ''
  const names = p.held_by.map((id) => all.find((q) => q.id === id)?.label ?? id)
  if (names.some((n) => p.activity.includes(n))) return ''
  return `Paused for ${names.join(', ')}`
}

/** Dot colour class: running green, waiting blue, held and paused amber,
 *  failed red, idle and stopped grey. */
export function dotClass(state: ProcessState): string {
  switch (state) {
    case 'running':
      return 'run'
    case 'waiting':
      return 'wait'
    case 'held':
    case 'paused':
      return 'paused'
    case 'failed':
      return 'err'
    default:
      return ''
  }
}
