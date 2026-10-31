import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '../api/client'
import { fetchBackground, sendBackgroundCommand } from '../api/background'
import { useToast } from '../composables/useToast'
import { pillSummary } from '../types/background'
import type { BackgroundProcess, ProcessAction } from '../types/background'

const REFUSALS: Record<string, string> = {
  not_pausable: "This process can't be paused.",
  not_stoppable: "This process can't be stopped.",
  not_startable: "This process can't be started now.",
  not_paused: 'This process is not paused.',
  stopped: 'This process is stopped. Start it again first.',
}

export const useBackgroundStore = defineStore('background', () => {
  const byId = ref<Record<string, BackgroundProcess>>({})
  const loaded = ref(false)
  const toast = useToast()

  const processes = computed(() =>
    Object.values(byId.value).sort((a, b) => a.order - b.order),
  )
  const summary = computed(() => pillSummary(processes.value))
  const runningCount = computed(() => summary.value.count)
  const anyFailed = computed(() => summary.value.failed)

  // The registry stamps `updated_at` whenever a status changes (one wall clock,
  // shared by GET, command answers and WS events), so a response that arrives
  // late never overwrites a newer status.
  function put(p: BackgroundProcess) {
    const cur = byId.value[p.id]
    if (cur && cur.updated_at > p.updated_at) return
    byId.value = { ...byId.value, [p.id]: p }
  }

  async function load() {
    const res = await fetchBackground()
    for (const p of res.processes) put(p)
    loaded.value = true
  }

  function handleEvent(event: string, data: unknown) {
    if (event !== 'process_updated') return
    const p = data as BackgroundProcess
    if (!p || typeof p.id !== 'string') return
    put(p)
  }

  async function command(id: string, action: ProcessAction) {
    try {
      const p = await sendBackgroundCommand(id, action)
      put(p)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const code = (e.detail as { code?: string } | undefined)?.code ?? ''
        toast.show(REFUSALS[code] ?? `The command was refused (${code || 'conflict'}).`, 'warn', 4000)
        return
      }
      toast.show(e instanceof Error ? e.message : String(e), 'warn', 4000)
    }
  }

  return { processes, summary, runningCount, anyFailed, loaded, load, handleEvent, command }
})
