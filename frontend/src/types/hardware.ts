export type Tier =
  | 'cpu_only'
  | 'apple_silicon'
  | 'cuda_entry'
  | 'cuda_mainstream'
  | 'cuda_workstation'

export interface CudaInfo {
  name: string
  vram_gb: number
  capability: string
}

export interface VulkanInfo {
  available: boolean
  devices: string[]
  has_real_device: boolean
}

export interface HardwareReport {
  os: string
  machine: string
  python: string
  is_wsl: boolean
  cpu_count: number | null
  ram_gb: number | null
  glibc: string | null
  cuda: CudaInfo | null
  mps: boolean
  vulkan: VulkanInfo | null
  nltk_version: string | null
  torch_version: string | null
  warnings: string[]
}

export interface Gate {
  available: boolean
  recommended: boolean
  reason: string
}

export interface HardwarePayload {
  tier: Tier
  report: HardwareReport
  // Legacy fields kept for older callers — prefer `report.*`.
  platform: string
  cpu_count: number | null
  cuda_available: boolean
  gpu_name: string | null
  vram_gb: number | null
}

export const TIER_LABEL: Record<Tier, string> = {
  cpu_only: 'CPU only',
  apple_silicon: 'Apple Silicon',
  cuda_entry: 'CUDA — entry',
  cuda_mainstream: 'CUDA — mainstream',
  cuda_workstation: 'CUDA — workstation',
}

export const TIER_COLOR: Record<Tier, string> = {
  cpu_only: '#94a3b8',           // slate
  apple_silicon: '#a855f7',      // purple
  cuda_entry: '#3b82f6',         // blue
  cuda_mainstream: '#22c55e',    // green
  cuda_workstation: '#f59e0b',   // amber
}
