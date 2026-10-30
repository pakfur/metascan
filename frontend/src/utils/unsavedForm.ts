// The unsaved-changes check shared by the I2V and T2I dialogs. Pure: the
// dialogs keep the baseline as a plain string and pass it in; nothing here
// touches a form.

export type LeaveDecision = 'go' | 'ask-unsaved' | 'ask-save-failed'
export type LeaveKind = 'clip' | 'image' | 'close'

export interface LeaveState {
  // A saved item is open: its edits autosave into it, so they are not "unsaved".
  selected: boolean
  saveFailed: boolean
  // The form as it was when it last matched a render, and as it is now.
  baseline: string
  current: string
}

export function leaveDecision(s: LeaveState): LeaveDecision {
  if (s.selected) return s.saveFailed ? 'ask-save-failed' : 'go'
  return s.baseline === s.current ? 'go' : 'ask-unsaved'
}

export function leaveText(d: LeaveDecision, kind: LeaveKind): string {
  const tail =
    kind === 'close' ? 'Close and lose them?' : `Load this ${kind} and lose them?`
  if (d === 'ask-save-failed') {
    return kind === 'close'
      ? `Your changes to this clip couldn't be saved. ${tail}`
      : `Your changes to this ${kind} couldn't be saved. ${tail}`
  }
  return `You have changes that haven't been generated. ${tail}`
}

// True when the user may leave: nothing to lose, or they accepted the prompt.
export function confirmLeave(s: LeaveState, kind: LeaveKind): boolean {
  const d = leaveDecision(s)
  return d === 'go' || confirm(leaveText(d, kind))
}
