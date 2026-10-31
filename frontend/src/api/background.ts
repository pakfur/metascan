import { get, post } from './client'
import type { BackgroundProcess, ProcessAction } from '../types/background'

export interface BackgroundList {
  server_time: number
  processes: BackgroundProcess[]
}

export const fetchBackground = () => get<BackgroundList>('/background')

export const sendBackgroundCommand = (id: string, action: ProcessAction) =>
  post<BackgroundProcess>(`/background/${encodeURIComponent(id)}/${action}`)
