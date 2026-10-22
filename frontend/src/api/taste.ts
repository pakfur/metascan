import type { TasteScoresResponse, TasteStatus } from '../types/taste'
import { get, post } from './client'

export function fetchTasteStatus(): Promise<TasteStatus> {
  return get<TasteStatus>('/taste/status')
}

// 202 on success; 409 detail.code is 'already_training' | 'not_enough_data'.
export function startTasteTraining(): Promise<{ status: string }> {
  return post<{ status: string }>('/taste/train')
}

export function fetchTasteScores(): Promise<TasteScoresResponse> {
  return get<TasteScoresResponse>('/taste/scores')
}

export function fetchTasteScoresFor(filePaths: string[]): Promise<TasteScoresResponse> {
  return post<TasteScoresResponse>('/taste/scores', { file_paths: filePaths })
}

export async function fetchReviewQueue(limit = 200): Promise<string[]> {
  const res = await get<{ paths: string[] }>(`/taste/review-queue?limit=${limit}`)
  return res.paths
}
