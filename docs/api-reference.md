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
| GET | `/api/comfy/status` | ComfyUI driver connection snapshot |
| GET | `/api/comfy/presets` | List registered workflow presets |
| POST | `/api/comfy/presets` | Register a workflow preset |
| DELETE | `/api/comfy/presets/{id}` | Delete a workflow preset |
| POST | `/api/comfy/submit` | Submit a generation job |
| GET | `/api/comfy/jobs` | List generation jobs |
| GET | `/api/comfy/jobs/{id}` | Get one generation job |
| POST | `/api/comfy/jobs/{id}/cancel` | Cancel a queued or running job |
| WS | `/ws` | Multiplexed WebSocket — channels: `scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy` |

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

## ComfyUI driver (`/api/comfy/*`)

The `ComfyClient` singleton is constructed in the FastAPI lifespan from the
`comfy` section of `config.json` (see `docs/configuration.md`) and connects
to an existing ComfyUI server — metascan never spawns one. A ComfyUI that
isn't reachable yet is not an error: `ComfyClient.start()` never blocks, and
the driver reconnects with backoff once ComfyUI comes up. Endpoints that
call into the client (`POST /submit`, `POST /jobs/{id}/cancel`,
`POST /presets`) return 503 only in the (non-standard) case where no
`ComfyClient` singleton was installed at all. The remaining endpoints —
`GET /status`, `GET /presets`, `DELETE /presets/{id}`, `GET /jobs`,
`GET /jobs/{id}` — read only from the database service layer and never
require the client, so they work even before the driver comes up.

### `GET /api/comfy/status`
Returns the driver's connection snapshot, not the full `comfy` config
section: `{base_url, client_id, in_flight}`, where `client_id` is a
per-process UUID generated by `ComfyClient` (not a config key) that
identifies this metascan instance to ComfyUI's websocket. `output_root`,
`unload_vlm_during_generation`, and `request_timeout_s` are not included.
When no client is installed, returns `{base_url: null, client_id: null,
in_flight: 0}` with a 200 — this endpoint never errors.

### `GET /api/comfy/presets`
Returns registered workflow presets, summary shape (omits the workflow
graph): `[{id, name, kind, bindings, created_at, updated_at}, ...]`.

### `POST /api/comfy/presets`
Body: `{name: string, kind: "t2i" | "ref", workflow: object}`, where
`workflow` is a ComfyUI API-format export with `MS_*` node titles. Returns
`{id: int}`. 400 with the missing/invalid title list if the workflow
doesn't satisfy the `MS_*` binding contract (`BindingError`).

### `DELETE /api/comfy/presets/{id}`
Returns `{status: "deleted"}`. 404 if the preset doesn't exist.

### `POST /api/comfy/submit`
Body:
```json
{
  "preset_id": 1,
  "positive": "a cat",
  "seed": 42,
  "width": 1024,
  "height": 576,
  "batch_size": 1,
  "negative": null,
  "lora_name": null,
  "lora_strength": null,
  "ref_image": null,
  "priority": false
}
```
Returns `{job_id: int}` immediately — the job is enqueued and reaches
ComfyUI once an `in_flight` slot frees up (or right away if `priority` is
`true`, which jumps the queue). Validation happens synchronously before
the job is created, so a bad request never reaches the queue:
- **404** if `preset_id` doesn't name a registered preset.
- **400** if the preset doesn't support a supplied parameter (e.g.
  `negative` with no `MS_NEGATIVE` node) — the detail names the missing
  node title.
- **503** if the driver isn't installed (no `ComfyClient` singleton).

Once a validated job is actually dispatched to ComfyUI, a rejection there
(as opposed to a local validation failure) surfaces asynchronously: the
job's `state` flips to `failed` and `error` holds ComfyUI's own message —
poll `GET /api/comfy/jobs/{id}` or subscribe to the `comfy` WS channel's
`job_update` event to see it.

### `GET /api/comfy/jobs`
Query params: `state` (optional, filters to one state), `limit` (default
100). Returns generation job rows, newest-id-last.

### `GET /api/comfy/jobs/{id}`
Returns one generation job row: `{id, preset_id, panel_id, state, params,
comfy_prompt_id, error, created_at, started_at, finished_at}`. `state` is
one of `queued | running | done | failed | cancelled`. 404 if unknown.

### `POST /api/comfy/jobs/{id}/cancel`
Cancels a queued or running job — a queued job never reaches ComfyUI; a
running job is interrupted there. Returns `{status: "cancelled"}`. 404 if
the job doesn't exist. `ComfyClient.cancel()` is a no-op on a job that has
already reached a terminal state (`done` / `failed` / `cancelled`) — the
response is still `{status: "cancelled"}` in that case, but the job's own
`state` (`GET /api/comfy/jobs/{id}`) is left exactly as it was; the
response describes the cancel request being accepted, not a guarantee that
it changed anything.

### `comfy` WebSocket channel
Bridged from `ComfyClient.on_job_event` via
`ws_manager.broadcast_sync("comfy", event, payload)`:

- **`job_update`** — `{job_id, state, error}`, sent on every state
  transition (queued → running → done/failed/cancelled).
- **`job_progress`** — `{job_id, value, max}`, ComfyUI's step progress for
  the running job.
- **`job_outputs`** — `{job_id, files}`, sent once a job's images have been
  downloaded and ingested; `files` is a list of local paths.
