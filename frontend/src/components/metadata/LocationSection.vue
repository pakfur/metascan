<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Media } from '../../types/media'
import MetadataField from './MetadataField.vue'
import { useSettingsStore } from '../../stores/settings'
import { copyToClipboard } from '../../utils/clipboard'

// Lazy MapLibre import — keeps ~200 KB out of the main bundle.
type MaplibreModule = typeof import('maplibre-gl')

const props = defineProps<{ media: Media }>()
const settings = useSettingsStore()

const mapEl = ref<HTMLDivElement | null>(null)
const mapLoadFailed = ref(false)
let map: import('maplibre-gl').Map | null = null
let marker: import('maplibre-gl').Marker | null = null
let maplibre: MaplibreModule | null = null
let initPromise: Promise<void> | null = null

function destroyMap() {
  marker?.remove()
  map?.remove()
  marker = null
  map = null
}

const hasGps = computed(() =>
  props.media.gps_latitude != null && props.media.gps_longitude != null
)

const lat = computed(() => props.media.gps_latitude as number)
const lng = computed(() => props.media.gps_longitude as number)

const coordsLabel = computed(() => {
  const ns = lat.value >= 0 ? 'N' : 'S'
  const ew = lng.value >= 0 ? 'E' : 'W'
  return `${Math.abs(lat.value).toFixed(4)}° ${ns}, ${Math.abs(lng.value).toFixed(4)}° ${ew}`
})

const altitudeLabel = computed(() => {
  const a = props.media.gps_altitude
  if (a == null) return null
  if (a < 0) return `${Math.abs(a).toFixed(0)} m below sea level`
  return `${a.toFixed(0)} m above sea level`
})

const osmUrl = computed(() => {
  const lat4 = lat.value.toFixed(5)
  const lng4 = lng.value.toFixed(5)
  return `https://www.openstreetmap.org/?mlat=${lat4}&mlon=${lng4}#map=15/${lat4}/${lng4}`
})

async function ensureMap() {
  if (map) return
  if (initPromise) return initPromise
  if (!mapEl.value || !hasGps.value) return
  initPromise = (async () => {
    try {
      if (!maplibre) {
        maplibre = await import('maplibre-gl')
        await import('maplibre-gl/dist/maplibre-gl.css')
      }
      // Container or GPS may have disappeared while we awaited the import.
      const el = mapEl.value
      if (!el || !hasGps.value) return
      const m = new maplibre.Map({
        container: el,
        style: settings.mapTileUrl,
        center: [lng.value, lat.value],
        zoom: 13,
        attributionControl: { compact: true },
      })
      m.scrollZoom.disable()
      el.addEventListener('mouseenter', () => m.scrollZoom.enable())
      el.addEventListener('mouseleave', () => m.scrollZoom.disable())
      // Surface fatal style/tile errors so the user gets the fallback
      // instead of a permanently blank gray canvas.
      m.on('error', (ev: { error?: { status?: number; message?: string } }) => {
        const err = ev?.error
        const msg = err?.message ?? String(err)
        if (err?.status === 404) return // missing tile, not fatal
        console.warn('MapLibre error:', msg)
      })
      map = m
      marker = new maplibre.Marker().setLngLat([lng.value, lat.value]).addTo(m)
    } catch (e) {
      console.warn('MapLibre failed to load:', e)
      mapLoadFailed.value = true
    } finally {
      initPromise = null
    }
  })()
  return initPromise
}

// Tear the map down when its container disappears (e.g. user clicks a
// media without GPS, which unmounts the <details> wrapper). Without this,
// the Map keeps a detached canvas + WebGL context — the context leak that
// makes the panel go permanently blank after ~16 toggles.
watch(
  () => mapEl.value,
  (el) => {
    if (!el) destroyMap()
  },
)

watch(
  () => [hasGps.value, lat.value, lng.value, mapEl.value] as const,
  async ([gpsOk, la, lo, el]) => {
    if (!gpsOk || !el) return
    if (!map) {
      await ensureMap()
      return
    }
    map.flyTo({ center: [lo as number, la as number], zoom: 13, duration: 600 })
    if (marker) marker.setLngLat([lo as number, la as number])
  },
  { immediate: true },
)

onBeforeUnmount(destroyMap)

async function copyCoords() {
  await copyToClipboard(`${lat.value},${lng.value}`)
}
</script>

<template>
  <details v-if="hasGps" class="meta-section" open>
    <summary class="section-title">Location</summary>
    <div class="section-body location-body">
      <div v-if="!mapLoadFailed" ref="mapEl" class="map-canvas" />
      <div v-else class="map-fallback">
        Map unavailable — showing coordinates only.
      </div>
      <MetadataField label="Coordinates" :value="coordsLabel" />
      <MetadataField v-if="altitudeLabel" label="Altitude" :value="altitudeLabel" />
      <div class="map-actions">
        <a class="map-link" :href="osmUrl" target="_blank" rel="noopener">
          Open in OpenStreetMap ↗
        </a>
        <button class="copy-coords-btn" @click="copyCoords">Copy coords</button>
      </div>
    </div>
  </details>
</template>

<style scoped>
.location-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.map-canvas {
  width: 100%;
  height: 220px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-200, #1a1a1a);
}
.map-fallback {
  padding: 24px 12px;
  text-align: center;
  font-size: 0.9em;
  color: var(--text-color-secondary, #888);
  background: var(--surface-200, #1a1a1a);
  border-radius: 6px;
}
.map-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.map-link {
  font-size: 0.9em;
}
.copy-coords-btn {
  font-size: 0.85em;
  padding: 2px 8px;
  cursor: pointer;
}
</style>
