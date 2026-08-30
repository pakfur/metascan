// Downstream-dependency map for the storyboard's editable data elements.
//
// Each compose/compile stage reads specific fields of the element above it;
// editing one of those fields makes the stage's output stale. This table is
// the single place that knowledge lives -- the store consults it after every
// successful PATCH (`noteDownstream`) and the UI offers to recompute.
// Extend here when a stage starts reading a new field.

export type DownstreamKind = 'beats' | 'shots' | 'compile'
export type DepEntity = 'panel' | 'scene' | 'beat'

interface DepRule {
  entity: DepEntity
  fields: readonly string[]
  downstream: DownstreamKind
}

const RULES: readonly DepRule[] = [
  // Shot action/subtext feed the beats-compose prompt (build_beats_user_prompt).
  { entity: 'panel', fields: ['action', 'subtext', 'is_turn'], downstream: 'beats' },
  // Scene descriptors feed the shots-compose prompt (build_shots_user_prompt);
  // rebuilding shots re-runs beats for the scene as well.
  {
    entity: 'scene',
    fields: [
      'name',
      'subtitle',
      'setting',
      'location',
      'mood',
      'lighting',
      'time_of_day',
      'function',
      'template_id',
      'brief',
    ],
    downstream: 'shots',
  },
  // Every beat field the H3 compiler renders into the shot's video prompt.
  {
    entity: 'beat',
    fields: [
      'action',
      'duration_s',
      'is_cut',
      'shot_size',
      'angle',
      'lens',
      'composition',
      'light_quality',
      'camera_motion',
      'camera_amplitude',
      'camera_speed',
      'subject_ids',
      'dialog',
      'sound',
      'emotional_intent',
      'reveals',
      'movement_motivation',
    ],
    downstream: 'compile',
  },
]

/** Which downstream computations the given changed fields invalidate. */
export function downstreamFor(entity: DepEntity, changedFields: readonly string[]): DownstreamKind[] {
  const out: DownstreamKind[] = []
  for (const rule of RULES) {
    if (rule.entity !== entity) continue
    if (changedFields.some((f) => rule.fields.includes(f)) && !out.includes(rule.downstream)) {
      out.push(rule.downstream)
    }
  }
  return out
}

export const DOWNSTREAM_LABEL: Record<DownstreamKind, string> = {
  beats: 'recalculate the beats for this shot',
  shots: 'rebuild the shots (and beats) for this scene',
  compile: 'recompile the video prompt for this shot',
}
