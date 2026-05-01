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
    "huggingface_token": ""
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
- **`clip_threshold`** — similarity search threshold (0–1)
- **`search_results_count`** — max similarity search results
- **`compute_phash_during_scan`** — compute perceptual hashes during scan

## `ui`

- **`map_tile_url`** — MapLibre GL style URL for the location metadata panel. Defaults to OpenFreeMap liberty if absent. Override to point at any compatible style URL, including a self-hosted tile server.

## `models`

Managed by the Models tab in the config dialog. Both fields are surfaced via the `/api/models/status` endpoint.

- **`preload_at_startup`** — model ids to preload on server start. The lifespan loop reads this; supplying `clip-<key>` triggers a CLIP weights load before the first request.
- **`huggingface_token`** — masked in the UI; injected as `HF_TOKEN` into subprocess env so embedding/inference workers can pull gated weights.

Model ids surfaced by `GET /api/models/status`: `clip-small|medium|large`, `resr-x2|x4|x4-anime`, `gfpgan-v1.4`, `rife`, `nltk-punkt|punkt-tab|stopwords`. The same ids are keys in the `gates` map; `nltk-punkt` vs `nltk-punkt-tab` are mutually exclusive — feature_gates marks exactly one available based on the installed NLTK version.
