<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { browseDirectories, type DirectoryListing } from '../../api/config'

// Server-side directory picker. A browser file input cannot produce a
// path the backend can use, so this walks the SERVER's tree through
// GET /api/config/browse (directories only).
const props = defineProps<{ initialPath?: string; title?: string }>()
const emit = defineEmits<{ select: [path: string]; close: [] }>()

const listing = ref<DirectoryListing | null>(null)
const pathText = ref('')
const loading = ref(false)
const error = ref('')

async function go(path?: string) {
  loading.value = true
  error.value = ''
  try {
    const res = await browseDirectories(path)
    listing.value = res
    pathText.value = res.path
  } catch (e) {
    // Keep the last good listing on screen; only the message changes.
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await go(props.initialPath || undefined)
  // A stale/mistyped starting path should still open somewhere useful.
  if (!listing.value) await go()
})
</script>

<template>
  <div class="picker-overlay">
    <div class="picker-card">
      <header class="picker-header">
        <h3>{{ title ?? 'Select a directory' }}</h3>
        <button class="icon-btn" title="Close" @click="emit('close')">✕</button>
      </header>

      <form class="path-row" @submit.prevent="go(pathText)">
        <button
          type="button"
          class="btn"
          title="Parent directory"
          :disabled="!listing?.parent || loading"
          @click="go(listing?.parent ?? undefined)"
        >↑ Up</button>
        <input v-model="pathText" spellcheck="false" placeholder="/absolute/path" />
        <button type="submit" class="btn" :disabled="loading">Go</button>
      </form>
      <p v-if="error" class="picker-error">⚠ {{ error }}</p>

      <ul class="dir-list">
        <li v-if="loading" class="muted">Loading…</li>
        <li v-else-if="listing && !listing.dirs.length" class="muted">
          No subdirectories.
        </li>
        <template v-else-if="listing">
          <li v-for="d in listing.dirs" :key="d.path">
            <button class="dir-btn" :title="d.path" @click="go(d.path)">📁 {{ d.name }}</button>
          </li>
        </template>
      </ul>

      <footer class="picker-footer">
        <span class="chosen" :title="listing?.path">{{ listing?.path }}</span>
        <button class="btn" @click="emit('close')">Cancel</button>
        <button
          class="btn primary"
          :disabled="!listing"
          @click="listing && emit('select', listing.path)"
        >Select this directory</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.picker-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000; /* above ConfigDialog (900) and preset registration (950) */
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.picker-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 18px 22px 20px;
  width: min(620px, 94vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}
.picker-header { display: flex; align-items: center; justify-content: space-between; }
.picker-header h3 { margin: 0; font-size: 16px; color: var(--text-color); }
.icon-btn {
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
}
.icon-btn:hover { color: var(--text-color); }
.path-row { display: flex; gap: 8px; }
.path-row input {
  flex: 1;
  min-width: 0;
  font-family: monospace;
  font-size: 12px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
}
.picker-error { margin: 0; font-size: 12px; color: #c33; }
.dir-list {
  list-style: none;
  margin: 0;
  padding: 4px;
  min-height: 220px;
  overflow-y: auto;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
}
.dir-btn {
  width: 100%;
  text-align: left;
  border: none;
  background: none;
  color: var(--text-color);
  font-size: 13px;
  padding: 5px 8px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dir-btn:hover { background: var(--surface-hover, rgba(127, 127, 127, 0.15)); }
.muted { color: var(--text-color-secondary); font-size: 13px; padding: 6px 8px; }
.picker-footer { display: flex; align-items: center; gap: 8px; }
.chosen {
  flex: 1;
  min-width: 0;
  font-family: monospace;
  font-size: 12px;
  color: var(--text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  direction: rtl; /* keep the END of a long path visible */
  text-align: left;
}
.btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
  white-space: nowrap;
}
.btn:disabled { opacity: 0.6; cursor: default; }
.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}
</style>
