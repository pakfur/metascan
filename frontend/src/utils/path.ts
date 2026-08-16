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
