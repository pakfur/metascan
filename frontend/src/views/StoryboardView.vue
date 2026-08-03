<template>
  <div class="storyboard-view">
    <div v-if="isMobile" class="mobile-note">
      <p>The storyboard editor is desktop-only.</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
    <div v-else-if="id === undefined" class="storyboard-shell">
      <StoryboardLanding />
    </div>
    <div v-else class="storyboard-shell">
      <p>Storyboard view — id: {{ id ?? 'none' }}</p>
      <RouterLink to="/">Back to library</RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useViewport } from '../composables/useViewport'
import StoryboardLanding from '../components/storyboard/StoryboardLanding.vue'
defineProps<{ id?: number }>()
const { isMobile } = useViewport()
</script>
