import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Category, CategoryRef } from '../types/judgments'
import {
  fetchCategories,
  createCategory,
  renameCategory,
  deleteCategory,
} from '../api/categories'

// User categories for taste rating. Holds no per-item state: an item's
// categories come with its judgment (GET /api/judgments/{path}). The sticky
// category lives only here, so a page reload clears it.
export const useCategoriesStore = defineStore('categories', () => {
  const categories = ref<Category[]>([])
  const stickyId = ref<number | null>(null)

  // A WS burst can start several loads; only the newest may write.
  let loadToken = 0
  async function load(): Promise<void> {
    const token = ++loadToken
    const data = await fetchCategories()
    if (token !== loadToken) return
    categories.value = data
    // Another tab may have deleted the sticky category.
    if (stickyId.value !== null && !data.some((c) => c.id === stickyId.value)) {
      stickyId.value = null
    }
  }

  async function create(name: string): Promise<CategoryRef> {
    const created = await createCategory(name)
    await load()
    return created
  }

  async function rename(id: number, name: string): Promise<CategoryRef> {
    const renamed = await renameCategory(id, name)
    await load()
    return renamed
  }

  async function remove(id: number): Promise<void> {
    await deleteCategory(id)
    if (stickyId.value === id) stickyId.value = null
    await load()
  }

  function setSticky(id: number | null): void {
    stickyId.value = id
  }

  return { categories, stickyId, load, create, rename, remove, setSticky }
})
