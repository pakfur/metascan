import { ref } from 'vue'
import type { AnyFolder, SmartRules } from '../types/folders'

// Shared UI state for folder-related overlays that get mounted at the App
// level. Components like FoldersSection, FolderContextMenu, and the grid's
// context menu all trigger these dialogs without needing to emit events up
// through three or four parents.
const newFolderOpen = ref(false)
const newFolderInitialItems = ref<string[]>([])
const renameFolderId = ref<string | null>(null)

type SmartEditorTarget = null | 'new' | string

const smartEditorOpen = ref<SmartEditorTarget>(null)
const smartEditorSeedRules = ref<SmartRules | null>(null)

const kebabMenu = ref<{
  x: number
  y: number
  folder: AnyFolder
} | null>(null)

export function useFoldersUi() {
  return {
    newFolderOpen,
    newFolderInitialItems,
    renameFolderId,
    smartEditorOpen,
    smartEditorSeedRules,
    kebabMenu,

    openNewFolder(initialItems: string[] = []) {
      newFolderInitialItems.value = initialItems
      newFolderOpen.value = true
    },
    closeNewFolder() {
      newFolderOpen.value = false
      newFolderInitialItems.value = []
    },
    openRename(id: string) {
      renameFolderId.value = id
    },
    closeRename() {
      renameFolderId.value = null
    },
    openSmartEditor(target: SmartEditorTarget, seed: SmartRules | null = null) {
      smartEditorOpen.value = target
      smartEditorSeedRules.value = seed
    },
    closeSmartEditor() {
      smartEditorOpen.value = null
      smartEditorSeedRules.value = null
    },
    openKebab(x: number, y: number, folder: AnyFolder) {
      kebabMenu.value = { x, y, folder }
    },
    closeKebab() {
      kebabMenu.value = null
    },
  }
}
