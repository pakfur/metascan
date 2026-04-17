// Extract the final path component ("basename") from a file path. Used by
// components that receive lightweight Media summaries — the list endpoint
// no longer ships `file_name` to keep the payload small.
export function fileName(filePath: string): string {
  const match = filePath.match(/[^/\\]+$/)
  return match ? match[0] : filePath
}
