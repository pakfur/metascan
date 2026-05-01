<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps<{
  src: string
  alt: string
}>()

// We avoid relying on the native `loading="lazy"` hint — its behavior was
// unstable across Galleria's unmount/remount cycle, causing the second
// open of the MediaViewer to fetch thumbnails for every item at once.
const rootEl = ref<HTMLElement | null>(null)
const shownSrc = ref<string | null>(null)
let observer: IntersectionObserver | null = null

function attach() {
  if (!rootEl.value || observer) return
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          shownSrc.value = props.src
          observer?.disconnect()
          observer = null
          break
        }
      }
    },
    { rootMargin: '200px' },
  )
  observer.observe(rootEl.value)
}

function detach() {
  observer?.disconnect()
  observer = null
}

onMounted(attach)
onBeforeUnmount(detach)

watch(
  () => props.src,
  (next, prev) => {
    if (next === prev) return
    shownSrc.value = null
    detach()
    attach()
  },
)
</script>

<template>
  <div ref="rootEl" class="galleria-thumb" role="img" :aria-label="alt">
    <img v-if="shownSrc" :src="shownSrc" :alt="alt" decoding="async" />
  </div>
</template>

<style scoped>
.galleria-thumb {
  width: 96px;
  height: 72px;
  border-radius: 4px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.06);
  display: block;
}

.galleria-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
