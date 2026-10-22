import type { Category, CategoryRef } from '../types/judgments'
import { get, post, patch, del } from './client'

export function fetchCategories(): Promise<Category[]> {
  return get<Category[]>('/categories')
}

export function createCategory(name: string): Promise<CategoryRef> {
  return post<CategoryRef>('/categories', { name })
}

export function renameCategory(id: number, name: string): Promise<CategoryRef> {
  return patch<CategoryRef>(`/categories/${id}`, { name })
}

export function deleteCategory(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/categories/${id}`)
}

export function addItems(
  id: number,
  filePaths: string[],
  source: 'viewer' | 'bulk',
): Promise<{ added: number }> {
  return post<{ added: number }>(`/categories/${id}/items`, {
    file_paths: filePaths,
    source,
  })
}

export function removeItems(
  id: number,
  filePaths: string[],
): Promise<{ status: string; removed: number }> {
  return del<{ status: string; removed: number }>(`/categories/${id}/items`, {
    file_paths: filePaths,
  })
}
