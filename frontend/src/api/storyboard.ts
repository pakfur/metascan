import { get, post, patch, del } from './client'
import type {
  Beat,
  ComposeStage,
  PanelWithoutImages,
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
  body: { panel_ids?: number[]; force?: boolean } = {},
): Promise<{ status: string; total: number }> {
  return post<{ status: string; total: number }>(`/storyboard/${id}/synthesize`, body)
}

export function generateStoryboard(
  id: number,
  body: { panel_ids?: number[]; only_failed?: boolean } = {},
): Promise<{ jobs: number[] }> {
  return post<{ jobs: number[] }>(`/storyboard/${id}/generate`, body)
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

export function patchSubject(
  subjectId: number,
  body: Record<string, unknown>,
): Promise<{ status: string }> {
  return patch<{ status: string }>(`/storyboard/subjects/${subjectId}`, body)
}

export function deleteSubject(subjectId: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/storyboard/subjects/${subjectId}`)
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
    shot_size?: string | null
    angle?: string | null
    lens?: string | null
    subject_ids?: number[]
    notes?: string | null
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/scenes/${sceneId}/panels`, body)
}

// Backend returns `db.get_panel`, which never carries `images` (only
// `get_storyboard_tree` assembles that array) -- callers must merge the
// images they already have for this panel back onto the result.
export function patchPanel(
  panelId: number,
  body: Record<string, unknown>,
): Promise<PanelWithoutImages> {
  return patch<PanelWithoutImages>(`/storyboard/panels/${panelId}`, body)
}

export function deletePanel(
  panelId: number,
  purgeImages = false,
): Promise<{ status: string }> {
  return del<{ status: string }>(
    `/storyboard/panels/${panelId}?purge_images=${purgeImages}`,
  )
}

export function selectPanelImage(
  panelId: number,
  imageId: number | null,
): Promise<PanelWithoutImages> {
  return post<PanelWithoutImages>(`/storyboard/panels/${panelId}/select`, {
    image_id: imageId,
  })
}

export function createBeat(
  panelId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>> & {
    action: string
  },
): Promise<{ id: number }> {
  return post<{ id: number }>(`/storyboard/panels/${panelId}/beats`, body)
}

export function patchBeat(
  beatId: number,
  body: Partial<Omit<Beat, 'id' | 'panel_id' | 'created_at' | 'updated_at'>>,
): Promise<Beat> {
  return patch<Beat>(`/storyboard/beats/${beatId}`, body)
}

export function deleteBeat(beatId: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/storyboard/beats/${beatId}`)
}
