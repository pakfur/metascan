import { ref, watch } from 'vue'
import { useSearchStore } from '../stores/search'

export function useContentSearch() {
  const searchStore = useSearchStore()

  const query = ref(searchStore.textQuery)

  // Keep the local box in sync if the query is set elsewhere.
  watch(
    () => searchStore.textQuery,
    (q) => {
      query.value = q
    },
  )

  function submit() {
    searchStore.submitText(query.value)
  }

  function clear() {
    query.value = ''
    searchStore.clearAll()
  }

  return { query, submit, clear }
}
