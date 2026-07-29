import { useMediaQuery } from '@vueuse/core'

// Module-level singleton: evaluated once so the whole app shares a single
// MediaQueryList listener. `isMobile` flips live on resize / rotation.
const isMobile = useMediaQuery('(max-width: 767px)')

export function useViewport() {
  return { isMobile }
}
