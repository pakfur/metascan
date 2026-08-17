# Configuration

[← Back to README](../README.md)

Configuration is stored in `config.json` in the application directory.

```json
{
  "directories": [
    {
      "filepath": "/path/to/your/ai/images",
      "search_subfolders": true
    }
  ],
  "watch_directories": true,
  "thumbnail_size": [200, 200],
  "cache_size_mb": 500,
  "sort_order": "date_added",
  "theme": "light_blue_500.xml",
  "similarity": {
    "clip_model": "small",
    "device": "auto",
    "phash_threshold": 10,
    "clip_threshold": 0.7,
    "search_results_count": 100,
    "video_keyframes": 4,
    "compute_phash_during_scan": true
  },
  "ui": {
    "map_tile_url": "https://tiles.openfreemap.org/styles/liberty"
  },
  "models": {
    "preload_at_startup": ["clip-large"],
    "huggingface_token": "",
    "vlm_repos": {}
  },
  "comfy": {
    "base_url": "http://127.0.0.1:8188",
    "in_flight": 2,
    "unload_vlm_during_generation": true,
    "output_root": "data/storyboards",
    "request_timeout_s": 30.0
  }
}
```

## Top-Level Options

- **`directories`** — list of scan directories with subfolder toggle
- **`watch_directories`** — enable real-time directory monitoring
- **`thumbnail_size`** — thumbnail dimensions `[width, height]` in pixels
- **`cache_size_mb`** — maximum thumbnail cache size in megabytes
- **`sort_order`** — default sorting (`"date_added"`, `"file_name"`, `"date_modified"`)
- **`theme`** — selected UI theme

## `similarity`

- **`clip_model`** — CLIP model size (`"small"`, `"medium"`, `"large"`)
- **`device`** — compute device (`"auto"`, `"cpu"`, `"cuda"`, `"mps"`)
- **`clip_threshold`**, **`phash_threshold`**, **`search_results_count`** — legacy keys, not consumed by search: the search thresholds are per-session sliders in the filter panel's SEARCH section (text 0–0.45, Find Similar 0–1), results are unbounded, and the duplicate finder uses a fixed Hamming distance
- **`compute_phash_during_scan`** — compute perceptual hashes during scan

## `ui`

- **`map_tile_url`** — MapLibre GL style URL for the location metadata panel. Defaults to OpenFreeMap liberty if absent. Override to point at any compatible style URL, including a self-hosted tile server.

## `models`

Managed by the Models tab in the config dialog. Both fields are surfaced via the `/api/models/status` endpoint.

- **`preload_at_startup`** — model ids to preload on server start. The lifespan loop reads this; supplying `clip-<key>` triggers a CLIP weights load before the first request.
- **`huggingface_token`** — masked in the UI; injected as `HF_TOKEN` into subprocess env so embedding/inference workers can pull gated weights.
- **`vlm_repos`** — `{"<model_id>": "<hf-repo>"}`, one entry per VLM registry id (`qwen3vl-2b|4b|8b|30b-a3b`, `qwen38-27b`) to override the default HuggingFace repo `setup_models.py` downloads from — e.g. to point at a different quant or remix. The GGUF/mmproj filenames still have to match what the registry expects. Defaults to `{}` (use the registry's built-in repo for every model). The legacy key `qwen3vl_repos` is still read for backward compatibility if `vlm_repos` is absent.

Model ids surfaced by `GET /api/models/status`: `clip-small|medium|large`, `resr-x2|x4|x4-anime`, `gfpgan-v1.4`, `rife`, `nltk-punkt|punkt-tab|stopwords`, `qwen3vl-2b|4b|8b|30b-a3b`, `qwen38-27b`. The same ids are keys in the `gates` map; `nltk-punkt` vs `nltk-punkt-tab` are mutually exclusive — feature_gates marks exactly one available based on the installed NLTK version. The VLM ids are also the valid keys for `vlm_repos` above.

## `comfy`

Read by `backend.config.get_comfy_config` and consumed by the FastAPI lifespan to construct the `ComfyClient` singleton. `GET /api/comfy/status` does **not** echo this whole section back — see `docs/api-reference.md` for exactly what it returns.

- **`base_url`** — ComfyUI server URL. Default `"http://127.0.0.1:8188"`.
- **`in_flight`** — max jobs `ComfyClient` holds inside ComfyUI at once; metascan queues the rest itself so a reroll can jump the line and cancellation stays responsive. Default `2`, floored at `1`.
- **`unload_vlm_during_generation`** — reserved for the storyboard feature (Phase B/C), which will pause VLM tagging while ComfyUI is busy to free VRAM. Default `true`.
- **`output_root`** — directory generated images are written to, relative to the repo root. Default `"data/storyboards"`.
- **`request_timeout_s`** — HTTP timeout (seconds) for calls to ComfyUI (`/prompt`, `/upload/image`, `/history`, `/view`). Default `30.0`.
