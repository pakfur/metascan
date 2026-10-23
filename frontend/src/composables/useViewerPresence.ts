import { computed, onMounted, onUnmounted, ref } from 'vue'

// How many full-screen media viewers are mounted right now (the library
// viewer, the viewers nested in the T2I / I2V dialogs, the slideshow, the
// mobile viewer). Module-level, so every caller shares one count.
//
// The writer training window (teleported to body, z-index 950) reads it to
// get out of the way: a viewer nested in a dialog overlay (z-index 900) sits
// below the window's stacking level, and both listen for Escape on window.
const openViewers = ref(0)

/** True while any media viewer is mounted. */
export const anyViewerOpen = computed(() => openViewers.value > 0)

/** Count the calling viewer component as open from mount to unmount. */
export function useRegisterViewer(): void {
  let counted = false
  onMounted(() => {
    counted = true
    openViewers.value++
  })
  onUnmounted(() => {
    if (!counted) return
    counted = false
    openViewers.value = Math.max(0, openViewers.value - 1)
  })
}
