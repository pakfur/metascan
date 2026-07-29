import { ref, watch } from 'vue'
import { useSimilarityStore } from '../stores/similarity'
import { useModelsStore } from '../stores/models'

export function useContentSearch() {
  const simStore = useSimilarityStore()
  const modelsStore = useModelsStore()

  const query = ref(simStore.contentQuery)
  const pendingQuery = ref<string | null>(null)

  // Keep the local box in sync if the query is set elsewhere.
  watch(
    () => simStore.contentQuery,
    (q) => {
      query.value = q
    },
  )

  function run(q: string) {
    if (q) void simStore.searchByText(q)
  }

  // When the inference worker becomes ready, drain any queued query.
  watch(
    () => modelsStore.inferenceState,
    (state) => {
      if (state === 'ready' && pendingQuery.value) {
        const q = pendingQuery.value
        pendingQuery.value = null
        run(q)
      }
    },
  )

  function submit() {
    const q = query.value.trim()
    if (!q) return
    if (modelsStore.isInferenceReady) {
      pendingQuery.value = null
      run(q)
      return
    }
    // Coalesce: remember the query and fire once the worker is ready.
    pendingQuery.value = q
    const s = modelsStore.inferenceState
    if (s === 'idle' || s === 'stopped' || s === 'error') {
      void modelsStore.startInferenceWorker()
    }
  }

  function clear() {
    query.value = ''
    pendingQuery.value = null
    if (simStore.active && simStore.isContentSearch) {
      simStore.exit()
    }
  }

  return { query, submit, clear }
}
