# API Reference

[← Back to README](../README.md)

The backend exposes a REST API at `http://localhost:8700`. Full interactive documentation is available at `/docs` (Swagger UI) or `/redoc`.

## Authentication

If `METASCAN_API_KEY` is set, every request must carry it as a bearer token:

```
Authorization: Bearer <METASCAN_API_KEY>
```

Without the env var the API is unauthenticated — fine for localhost, but set a key before exposing the server to a network.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/media` | List media summaries (with sort/filter params) |
| GET | `/api/media/{path}` | Get single media record |
| DELETE | `/api/media/{path}` | Delete media (move to trash) |
| PATCH | `/api/media/{path}` | Update favorite/playback speed |
| GET | `/api/stream/{path}` | Stream file with HTTP Range support |
| GET | `/api/thumbnails/{path}` | Serve cached thumbnail |
| GET | `/api/filters` | Get filter groups with counts |
| POST | `/api/filters/apply` | Apply filters, return matching paths |
| POST | `/api/filters/tag_paths` | Resolve a list of tag keys to file path sets (used by smart folders) |
| GET | `/api/folders` | List static + smart folders |
| POST | `/api/folders` | Create folder |
| PATCH | `/api/folders/{id}` | Update folder name / icon / rules / sort order |
| DELETE | `/api/folders/{id}` | Delete folder |
| POST | `/api/folders/{id}/items` | Add items to a static folder |
| DELETE | `/api/folders/{id}/items` | Remove items from a static folder |
| POST | `/api/scan/prepare` | Count files for scan confirmation |
| POST | `/api/scan/start` | Begin scan (progress via WebSocket) |
| POST | `/api/similarity/search` | Find similar media (image → image) |
| POST | `/api/similarity/content-search` | CLIP text-to-image search |
| POST | `/api/duplicates/find` | Find duplicate groups |
| POST | `/api/upscale` | Submit upscale tasks |
| GET | `/api/upscale/queue` | List queue tasks |
| GET | `/api/models/status` | Per-model availability rows + tier + gates |
| GET | `/api/models/hardware` | Full hardware probe report |
| WS | `/ws` | Multiplexed WebSocket — channels: `scan`, `upscale`, `embedding`, `watcher`, `models`, `folders` |

## WebSocket Envelope

Every message on `/ws` carries a JSON envelope:

```json
{ "channel": "scan", "event": "progress", "data": { "completed": 42, "total": 100 } }
```

Subscribe per channel on the frontend with `useWebSocket('<channel>', handler)`.

## Error Shapes

Most errors are FastAPI's default `{ "detail": "..." }`. Two endpoints return a structured error the UI matches against:

- **Dim mismatch (HTTP 409)** from similarity endpoints when the loaded CLIP model's embedding dim differs from the on-disk FAISS index:
  ```json
  { "detail": { "code": "dim_mismatch", "index_dim": 768, "model_dim": 1024 } }
  ```
  The frontend renders an actionable "Rebuild index" banner.

## VLM tagging (`/api/vlm/*`)

### `GET /api/vlm/status`
Returns the current `VlmClient` snapshot:
`{state, model_id, base_url, progress, error}`. State is one of
`idle | spawning | loading | ready | error | stopped`.

### `POST /api/vlm/tag`
Body: `{path: string}`. Re-tags one image with the active VLM. If no model
is active, picks the recommended one for the host tier and starts it.
Returns `{tags: string[]}`. 404 if the file doesn't exist; 503 if no
recommended VLM model is available on this hardware.

### `POST /api/vlm/retag` (status: 202)
Body: `{scope: 'paths' | 'all_clip', paths?: string[], force?: boolean}`.
Enqueues a background re-tag job. Returns `{job_id, total}`. Progress is
broadcast on the `models` WS channel as `vlm_progress` events:
`{job_id, current, total}`.

### `DELETE /api/vlm/retag/{job_id}`
Cancels a running re-tag job. Returns `{status: 'cancelled'}`. Returns 404
if the job id is unknown.

### `POST /api/vlm/active`
Body: `{model_id: string}`. Switches the loaded VLM to a different model
in the `vlm_models.REGISTRY`. Cancels any in-flight retag jobs first.
Returns the new VlmClient snapshot. 400 if `model_id` isn't recognised.
