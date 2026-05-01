<script setup lang="ts">
import { computed } from 'vue'
import type { Media } from '../../types/media'
import MetadataField from './MetadataField.vue'
import { orientationLabel } from '../../utils/orientation'

const props = defineProps<{ media: Media }>()

const cameraLabel = computed(() => {
  const make = props.media.camera_make ?? ''
  const model = props.media.camera_model ?? ''
  const joined = [make, model].filter(Boolean).join(' ')
  return joined || null
})

const focalLengthLabel = computed(() => {
  const fl = props.media.photo_exposure?.focal_length
  const fl35 = props.media.photo_exposure?.focal_length_35mm
  if (fl == null) return null
  if (fl35 != null) return `${fl} mm (35mm equiv. ${fl35} mm)`
  return `${fl} mm`
})

const dateTaken = computed(() => {
  const v = props.media.datetime_original
  if (!v) return null
  try { return new Date(v).toLocaleString() } catch { return v }
})

const visible = computed(() =>
  Boolean(
    props.media.camera_make ||
    props.media.camera_model ||
    props.media.lens_model ||
    props.media.datetime_original ||
    props.media.photo_exposure
  )
)
</script>

<template>
  <details v-if="visible" class="meta-section" open>
    <summary class="section-title">Camera</summary>
    <div class="section-body">
      <MetadataField v-if="cameraLabel" label="Camera" :value="cameraLabel" />
      <MetadataField v-if="media.lens_model" label="Lens" :value="media.lens_model" />
      <MetadataField v-if="dateTaken" label="Date taken" :value="dateTaken" />
      <MetadataField
        v-if="media.photo_exposure?.shutter_speed"
        label="Shutter"
        :value="`${media.photo_exposure.shutter_speed} s`"
      />
      <MetadataField
        v-if="media.photo_exposure?.aperture != null"
        label="Aperture"
        :value="`f/${media.photo_exposure.aperture}`"
      />
      <MetadataField
        v-if="media.photo_exposure?.iso != null"
        label="ISO"
        :value="`ISO ${media.photo_exposure.iso}`"
      />
      <MetadataField
        v-if="focalLengthLabel"
        label="Focal length"
        :value="focalLengthLabel"
      />
      <MetadataField
        v-if="media.photo_exposure?.flash"
        label="Flash"
        :value="media.photo_exposure.flash"
      />
      <MetadataField
        v-if="orientationLabel(media.orientation)"
        label="Orientation"
        :value="orientationLabel(media.orientation) ?? ''"
      />
    </div>
  </details>
</template>
