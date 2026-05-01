import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchUpscaleQueue,
  removeUpscaleTask,
  pauseAllUpscale,
  resumeAllUpscale,
  clearCompletedUpscale,
} from '../api/upscale'
import { useWebSocket } from '../composables/useWebSocket'

export interface QueueTask {
  task_id: string
  file_path: string
  file_name: string
  status: string // pending | processing | complete | error
  progress: number // 0-1
  error?: string
  [key: string]: unknown
}

export const useUpscaleStore = defineStore('upscale', () => {
  const tasks = ref<QueueTask[]>([])
  const loading = ref(false)
  const paused = ref(false)

  // Subscribe to upscale WebSocket channel
  useWebSocket('upscale', (event, data) => {
    switch (event) {
      case 'task_added': {
        const task = data as unknown as QueueTask
        const idx = tasks.value.findIndex((t) => t.task_id === task.task_id)
        if (idx < 0) tasks.value.push(task)
        break
      }
      case 'task_updated': {
        const updated = data as unknown as QueueTask
        const idx = tasks.value.findIndex((t) => t.task_id === updated.task_id)
        if (idx >= 0) tasks.value[idx] = { ...tasks.value[idx], ...updated }
        break
      }
      case 'task_removed': {
        const taskId = data.task_id as string
        tasks.value = tasks.value.filter((t) => t.task_id !== taskId)
        break
      }
    }
  })

  async function loadQueue() {
    loading.value = true
    try {
      const result = await fetchUpscaleQueue()
      tasks.value = (result.tasks ?? []) as unknown as QueueTask[]
    } finally {
      loading.value = false
    }
  }

  async function removeTask(taskId: string) {
    await removeUpscaleTask(taskId)
    tasks.value = tasks.value.filter((t) => t.task_id !== taskId)
  }

  async function pauseAll() {
    await pauseAllUpscale()
    paused.value = true
  }

  async function resumeAll() {
    await resumeAllUpscale()
    paused.value = false
  }

  async function clearCompleted() {
    await clearCompletedUpscale()
    tasks.value = tasks.value.filter((t) => t.status !== 'complete')
  }

  return {
    tasks,
    loading,
    paused,
    loadQueue,
    removeTask,
    pauseAll,
    resumeAll,
    clearCompleted,
  }
})
