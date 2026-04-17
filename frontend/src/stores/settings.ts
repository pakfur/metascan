import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchConfigTimed, updateConfig } from '../api/config'
import { now, since } from '../utils/timing'

export type ThumbnailSize = 'small' | 'medium' | 'large'

const THUMBNAIL_SIZES: Record<ThumbnailSize, [number, number]> = {
  small: [150, 150],
  medium: [200, 200],
  large: [300, 300],
}

export const useSettingsStore = defineStore('settings', () => {
  const theme = ref('light_blue_500')
  const thumbnailSizeLabel = ref<ThumbnailSize>('medium')
  const thumbnailSize = ref<[number, number]>([200, 200])
  const config = ref<Record<string, unknown>>({})

  async function loadConfig() {
    const t0 = now()
    const { data, phases } = await fetchConfigTimed()
    const t1 = now()
    config.value = data
    const t = config.value.theme as string | undefined
    if (t) theme.value = t.replace('.xml', '')
    const ts = config.value.thumbnail_size as [number, number] | undefined
    if (ts) {
      thumbnailSize.value = ts
      if (ts[0] <= 150) thumbnailSizeLabel.value = 'small'
      else if (ts[0] <= 250) thumbnailSizeLabel.value = 'medium'
      else thumbnailSizeLabel.value = 'large'
    }
    // eslint-disable-next-line no-console
    console.info(
      `[perf] loadConfig: ttfb=${phases.ttfb.toFixed(0)}ms `
        + `body=${phases.body.toFixed(0)}ms parse=${phases.parse.toFixed(0)}ms `
        + `assign=${since(t1)} total=${since(t0)} `
        + `bytes=${(phases.bytes / 1024).toFixed(0)}KB`,
    )
  }

  function setThumbnailSize(label: ThumbnailSize) {
    thumbnailSizeLabel.value = label
    thumbnailSize.value = THUMBNAIL_SIZES[label]
    updateConfig({ thumbnail_size: THUMBNAIL_SIZES[label] })
  }

  function setTheme(t: string) {
    theme.value = t
    updateConfig({ theme: `${t}.xml` })
  }

  return {
    theme,
    thumbnailSizeLabel,
    thumbnailSize,
    config,
    loadConfig,
    setThumbnailSize,
    setTheme,
  }
})
