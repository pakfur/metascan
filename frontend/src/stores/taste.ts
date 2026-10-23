import { defineStore } from 'pinia'
import { ref, shallowRef, computed } from 'vue'
import type { TasteStatus } from '../types/taste'
import {
  fetchTasteStatus,
  startTasteTraining,
  fetchTasteScores,
  fetchReviewQueue,
} from '../api/taste'

// Learned taste scores (0..1) per image path. The map holds one entry per
// scored image (~13k on a large library), so it lives behind a shallowRef:
// Vue tracks only the map's identity, never its entries.
export const useTasteStore = defineStore('taste', () => {
  const scores = shallowRef<Map<string, number>>(new Map())
  const modelId = ref<number | null>(null)
  const status = ref<TasteStatus | null>(null)
  const hasScores = computed(() => scores.value.size > 0)

  // WS bursts can overlap loads; only the newest may write.
  let scoresToken = 0
  async function load(): Promise<void> {
    const token = ++scoresToken
    const data = await fetchTasteScores()
    if (token !== scoresToken) return
    scores.value = new Map(Object.entries(data.scores))
    modelId.value = data.model_id
  }

  let statusToken = 0
  async function loadStatus(): Promise<void> {
    const token = ++statusToken
    const data = await fetchTasteStatus()
    if (token !== statusToken) return
    status.value = data
  }

  // Throws ApiError on 409 (detail.code already_training / not_enough_data).
  async function train(): Promise<void> {
    await startTasteTraining()
    // Invalidate any in-flight loadStatus so it can't overwrite the flag below.
    statusToken++
    // Reflect "training" at once; training_started / loadStatus confirm it.
    if (status.value) status.value = { ...status.value, training: true }
  }

  function scoreOf(path: string): number | null {
    return scores.value.get(path) ?? null
  }

  function reviewQueue(limit?: number): Promise<string[]> {
    return fetchReviewQueue(limit)
  }

  return { scores, modelId, status, hasScores, load, loadStatus, train, scoreOf, reviewQueue }
})
