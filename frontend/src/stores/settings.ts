import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchConfig, updateConfig } from '../api/config'
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
    config.value = await fetchConfig()
    const t1 = now()
    const t = config.value.theme as string | undefined
    if (t) theme.value = t.replace('.xml', '')
    const ts = config.value.thumbnail_size as [number, number] | undefined
    if (ts) {
      thumbnailSize.value = ts
      // Find closest label
      if (ts[0] <= 150) thumbnailSizeLabel.value = 'small'
      else if (ts[0] <= 250) thumbnailSizeLabel.value = 'medium'
      else thumbnailSizeLabel.value = 'large'
    }
    // eslint-disable-next-line no-console
    console.info(
      `[perf] loadConfig: fetch+parse=${(t1 - t0).toFixed(0)}ms `
        + `assign=${since(t1)}`,
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
