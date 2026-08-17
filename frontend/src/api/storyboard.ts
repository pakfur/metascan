import { get, post, patch, del } from './client'
import type {
  Beat,
  BeatWithoutImages,
  ComposeStage,
  Panel,
  StoryboardSummary,
  StoryboardTree,
} from '../types/storyboard'

export function listStoryboards(): Promise<StoryboardSummary[]> {
  return get<StoryboardSummary[]>('/storyboard')
}

export function createStoryboard(body: {
  name: string
  target_model: string
  architecture?: string
  aspect_ratio?: string
  style_block?: string | null
  negative?: string | null
  preset_id?: number | null
  base_seed?: number | null
  batch_size?: number
}): Promise<{ id: number }> {
  return post<{ id: number }>('/storyboard', body)
}

export function fetchStoryboard(id: number): Promise<StoryboardTree> {
  return get<StoryboardTree>(`/storyboard/${id}`)
}

export function patchStoryboard(
  id: number,
  body: Partial<{
    name: string
    // Nullable columns (see storyboards table DDL) -- `| null` lets callers
    // send an explicit clear, which the backend now honors via
    // exclude_unset=True rather than silently dropping it (exclude_none).
    source_text: string | null
    outline: string | null
    aspect_ratio: string
    style_block: string | null
    negative: string | null
    target_model: string
    architecture: string
    preset_id: number | null
    base_seed: number
    batch_size: number
    video_target: string | null
    video_mode: string | null
    video_preset_id: number | null
    notes: string | null
  }>,
): Promise<{ status: string }> {
  return patch<{ status: string }>(`/storyboard/${id}`, body)
}

// purgeImages=true also deletes the generated images (media rows + files
// moved to OS trash); default releases them into the library (unhidden).
// Deleting a storyboard always removes its "Storyboard: <name>" folder.
export function deleteStoryboard(
  id: number,
  purgeImages = false,
): Promise<{ status: string }> {
  return del<{ status: string }>(`/storyboard/${id}?purge_images=${purgeImages}`)
}

// Runner-backed. 409 with detail {code: 'confirm_required', message} when
// the storyboard already has scenes and confirm=false -- callers should
// catch ApiError and re-call with confirm=true after user confirmation.
export function parseStoryboard(
  id: number,
  text: string,
  confirm = false,
): Promise<StoryboardTree> {
  return post<StoryboardTree>(`/storyboard/${id}/parse`, { text, confirm })
}

// Runner-backed, same confirm_required 409 shape as parseStoryboard --
// callers should catch ApiError and re-call with confirm=true.
export function composeStoryboard(
  id: number,
  body: {
    stages?: ComposeStage[]
    scene_ids?: number[]
    panel_ids?: number[]
    confirm?: boolean
  },
): Promise<{ status: string }> {
  return post<{ status: string }>(`/storyboard/${id}/compose`, body)
}

export function synthesizeStoryboard(
  id: number,
  body: { beat_ids?: number[]; force?: boolean } = {},
): Promise<{ status: string; total: number }> {
  return post<{ status: string; total: number }>(`/storyboard/${id}/synthesize`, body)
}

// Runner-backed. 400 when the storyboard's video_target isn't 'minimax'.
export function compileStoryboard(
  id: number,
  body: { panel_ids?: number[]; force?: boolean } = {},
): Promise<{ status: string; total: number }> {
  return post<{ status: string; total: number }>(`/storyboard/${id}/compile`, body)
}

export function generateStoryboard(
  id: number,
  body: { beat_ids?: number[]; only_failed?: boolean } = {},
): Promise<{ jobs: number[] }> {
  return post<{ jobs: number[] }>(`/storyboard/${id}/generate`, body)
}

// Runner-backed. 400 (multi-line validation detail) when video_target/mode
// or the storyboard's video_preset_id aren't set, or a named panel has no
// compiled video_prompt / keeper image. `skipped` names per-panel reasons
// (e.g. missing anchor source) that don't fail the whole request.
export function generateVideoStoryboard(
  id: number,
  body: { panel_ids?: number[]; only_failed?: boolean } = {},
): Promise<{ jobs: number[]; skipped: { panel_id: number; error: string }[] }> {
  return post<{ jobs: number[]; skipped: { panel_id: number; error: string }[] }>(
    `/storyboard/${id}/generate-video`,
    body,
  )
}

export function cancelStoryboard(id: number): Promise<{ cancelled: number }> {
  return post<{ cancelled: number }>(`/storyboard/${id}/cancel`)
}

export function createSubject(
  storyboardId: number,
  body: {
    name: string
    description: string
    lora_name?: string | null
    lora_strength?: number
    reference_path?: string | null
    sort_order?: number
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/${storyboardId}/subjects`, body)
}

// `body` also accepts `voice_ref_path?: string | null` (audio reference for
// the H3 video pipeline's per-subject voice cloning).
export function patchSubject(
  subjectId: number,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return patch<{ status: string }>(`/storyboard/subjects/${subjectId}`, body)
}

export function deleteSubject(subjectId: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/storyboard/subjects/${subjectId}`)
}

// Runner-backed VLM call: reads the subject's persisted reference(s) from
// the DB. First call can take ~30-60s while the VLM model loads.
export function describeSubject(
  subjectId: number,
): Promise<{ description: string; voice: string | null }> {
  return post(`/storyboard/subjects/${subjectId}/describe`, {})
}

export function createScene(
  storyboardId: number,
  body: {
    name: string
    sort_order?: number
    subtitle?: string | null
    setting?: string | null
    location?: string | null
    time_of_day?: string | null
    mood?: string | null
    lighting?: string | null
    notes?: string | null
    reference_path?: string | null
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/${storyboardId}/scenes`, body)
}

export function patchScene(
  sceneId: number,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return patch<{ status: string }>(`/storyboard/scenes/${sceneId}`, body)
}

// Runner-backed VLM call: reads the scene's persisted `reference_path` from
// the DB (not the request body) -- callers in edit mode must patchScene the
// reference first. First call can take ~30-60s while the VLM model loads.
export function describeScene(
  sceneId: number,
): Promise<{ setting: string; lighting: string | null; mood: string | null }> {
  return post(`/storyboard/scenes/${sceneId}/describe`, {})
}

export function deleteScene(
  sceneId: number,
  purgeImages = false,
): Promise<{ status: string }> {
  return del<{ status: string }>(
    `/storyboard/scenes/${sceneId}?purge_images=${purgeImages}`,
  )
}

export function createPanel(
  sceneId: number,
  body: {
    action: string
    sort_order?: number
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/scenes/${sceneId}/panels`, body)
}

// Backend returns `db.get_panel` -- Panel no longer carries per-shot
// generation fields (those live on Beat now), only sort_order/action/
// duration_s/video_prompt*/video_anchor*/beats. `body` accepts
// `sort_order?`, `action?`, `duration_s?`, `video_prompt?: string | null`
// (non-null forces video_prompt_locked=1/video_prompt_source='user'
// server-side; null clears all three video-prompt fields),
// `video_prompt_locked?: 0 | 1`, and `video_anchor?: 'keeper' | 'prev_last'
// | null`.
export function patchPanel(panelId: number, body: Record<string, unknown>): Promise<Panel> {
  return patch<Panel>(`/storyboard/panels/${panelId}`, body)
}

export function deletePanel(
  panelId: number,
  purgeImages = false,
): Promise<{ status: string }> {
  return del<{ status: string }>(
    `/storyboard/panels/${panelId}?purge_images=${purgeImages}`,
  )
}

export function createBeat(
  panelId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>> & {
    action: string
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/panels/${panelId}/beats`, body)
}

// Backend returns `db.get_beat`, which never carries `images` (only
// `get_storyboard_tree` assembles that array) -- callers must merge the
// images they already have for this beat back onto the result.
export function patchBeat(
  beatId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>>,
): Promise<BeatWithoutImages> {
  return patch<BeatWithoutImages>(`/storyboard/beats/${beatId}`, body)
}

export function deleteBeat(beatId: number, purgeImages = false): Promise<{ status: string }> {
  return del<{ status: string }>(
    `/storyboard/beats/${beatId}?purge_images=${purgeImages}`,
  )
}

// Backend returns `db.get_beat`, same shape/caveat as patchBeat.
export function selectBeatImage(
  beatId: number,
  imageId: number | null,
): Promise<BeatWithoutImages> {
  return post<BeatWithoutImages>(`/storyboard/beats/${beatId}/select`, {
    image_id: imageId,
  })
}
