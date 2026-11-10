// Extract the final path component ("basename") from a file path. Used by
// components that receive lightweight Media summaries — the list endpoint
// no longer ships `file_name` to keep the payload small.
export function fileName(filePath: string): string {
  const match = filePath.match(/[^/\\]+$/)
  return match ? match[0] : filePath
}

// Extension-based video sniff for callers that synthesize a Media object
// client-side (e.g. panel image candidates) and have no server-provided
// `is_video` flag to read.
const VIDEO_EXTS = new Set(['.mp4', '.webm', '.mov'])

export function isVideoPath(filePath: string): boolean {
  const match = filePath.match(/\.[^./\\]+$/)
  return match ? VIDEO_EXTS.has(match[0].toLowerCase()) : false
}

// A comparison key for one file whatever spelling an endpoint used: media and
// clip rows reach the browser native (`D:\Media\a.png` on Windows) while raw
// rows such as GET /api/comfy/jobs keep the stored POSIX form
// (`/mnt/d/Media/a.png`, metascan.utils.path_utils.to_posix_path). Slashes are
// made forward and a drive spelt either way becomes `d:/`. For matching only:
// never send a key to the server.
export function pathKey(filePath: string): string {
  const p = filePath.replace(/\\/g, '/')
  const wsl = /^\/mnt\/([a-zA-Z])(\/|$)/.exec(p)
  if (wsl) return `${wsl[1].toLowerCase()}:/${p.slice(wsl[0].length)}`
  const drive = /^([a-zA-Z]):\//.exec(p)
  if (drive) return `${drive[1].toLowerCase()}:/${p.slice(3)}`
  return p
}
