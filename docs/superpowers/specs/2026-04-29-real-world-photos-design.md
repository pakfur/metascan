# Real-World Photo Support — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorm complete; ready for implementation plan)
**Scope:** HEIC ingestion, camera/exposure/lens/GPS/orientation EXIF persistence and display, MapLibre-based location panel, filter sidebar buckets for camera make/model/GPS.
**Out of scope (deferred):** Smart-folder rule fields for the new EXIF data, GPS scrubbing/privacy toggle, reverse geocoding, Live Photo motion track, MakerNotes-level lens decoding (Sony/Olympus).

---

## 1. Goals

Metascan currently treats every image as either an AI-generated asset or a generic image with no metadata beyond `width`/`height`/`file_size`. Real-world photos (iPhone HEIC, DSLR JPEG, etc.) are either rejected (HEIC) or stored without their camera/exposure/GPS EXIF, which makes them second-class citizens in the browser.

This spec adds:

1. **HEIC/HEIF decoding** via `pillow-heif`, treated as first-class image formats.
2. **EXIF orientation correctness** — display dimensions, thumbnails, embeddings, and upscale outputs are all correctly oriented.
3. **Photo-EXIF persistence** — camera make/model/lens, exposure (shutter/aperture/ISO/flash/focal length), date taken, GPS (lat/lon/altitude), orientation.
4. **Metadata panel sections** — Camera section with hardware/exposure/timing/orientation, Location section with an interactive MapLibre map.
5. **Filter sidebar buckets** — Camera Make, Camera Model, Has Location.

---

## 2. Architecture

```
                                pillow-heif registered at process start
                                (server, embedding worker, inference worker, upscale worker)
                                                │
                                                ▼
[MediaScanner.scan_file]  ──►  Image.open(...)  ──►  ImageOps.exif_transpose()  ──►  display W×H
        │                              │
        │                              ▼
        │                       extract_photo_exif(img.getexif())
        │                              │
        │                              ▼
        │              (PhotoExif{ make, model, lens_model, datetime_original,
        │                          gps_lat, gps_lon, gps_alt,
        │                          exposure: {shutter, aperture, iso, flash,
        │                                      focal_length, focal_length_35} },
        │               orientation_tag)
        ▼
   Media dataclass  ──►  database_sqlite.save_media (upsert)
                                  │
                                  ▼
                         media columns: camera_make, camera_model, lens_model,
                                        datetime_original, gps_latitude, gps_longitude,
                                        gps_altitude, orientation, photo_exposure (JSON)
                                  │
                                  ▼
                         indices: index_type='camera_make' | 'camera_model' | 'has_gps'
                                  │
                                  ▼
                         /api/media (summary), /api/filters (buckets)
                                  │
                                  ▼
                         MetadataPanel.vue: + Camera section, + Location section (MapLibre)
                         FilterPanel.vue:    + Camera Make, Camera Model, Has Location sections
```

### Component boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `metascan/utils/heic.py` | Idempotent `pillow_heif` registration. | `pillow_heif` (optional) |
| `metascan/core/photo_exif.py` | Pure: `Image.Exif` → `PhotoExif`. DMS→decimal, IFDRational coercion, orientation interpretation, flash decode, sanity guards. | Pillow (no I/O, no DB) |
| `metascan/core/scanner.py` | Adds `.heic`/`.heif` extensions, applies `exif_transpose`, calls `extract_photo_exif`, attaches results to `Media`. | photo_exif, heic |
| `metascan/cache/thumbnail.py` | Applies `exif_transpose` before resize. | heic |
| `metascan/workers/embedding_worker.py` | Applies `exif_transpose` before CLIP encode. | heic |
| `metascan/workers/inference_worker.py` | Applies `exif_transpose` before CLIP encode. | heic |
| `metascan/workers/upscale_worker.py` | Applies `exif_transpose` before pipeline. | heic |
| `metascan/core/database_sqlite.py` | Schema migration (9 columns + 3 index types), covering-index rebuild, `_generate_indices` extension. | — |
| `metascan/core/media.py` | `PhotoExposure` dataclass, 9 new optional `Media` fields. | — |
| `backend/api/filters.py` | Adds `camera_make`, `camera_model`, `has_gps` bucket types. | — |
| `frontend/src/components/metadata/CameraSection.vue` | Camera/exposure/timing/orientation rows. | `MetadataField` |
| `frontend/src/components/metadata/LocationSection.vue` | MapLibre dynamic-import map + coords + altitude. | `maplibre-gl` |
| `frontend/src/components/metadata/MetadataPanel.vue` | Composes new sections; section order: Tags → File → Image → Camera → Location → Generation. | new sections |
| `frontend/src/components/filters/FilterPanel.vue` | Three new `<FilterSection>` configs. | existing FilterSection |
| `frontend/src/types/media.ts` | `Media` interface + `PhotoExposure` interface gain new fields. | — |

Each unit can be tested in isolation. `photo_exif.py` is the heaviest logic surface and is fully pure.

---

## 3. Data model

### `metascan/core/media.py`

```python
@dataclass_json
@dataclass
class PhotoExposure:
    shutter_speed: Optional[str] = None        # "1/250" — pretty from ExposureTime rational
    aperture: Optional[float] = None           # f-number, rounded to 1 decimal
    iso: Optional[int] = None
    flash: Optional[str] = None                # decoded bitfield, e.g. "On, Fired"
    focal_length: Optional[float] = None       # mm
    focal_length_35mm: Optional[int] = None    # 35mm equivalent

# new fields on Media (all Optional, all default None):
camera_make: Optional[str] = None              # "Apple"
camera_model: Optional[str] = None             # "iPhone 15 Pro"
lens_model: Optional[str] = None
datetime_original: Optional[datetime] = None   # when shutter fired (≠ modified_at)
gps_latitude: Optional[float] = None           # decimal degrees, signed
gps_longitude: Optional[float] = None
gps_altitude: Optional[float] = None           # metres, signed
orientation: Optional[int] = None              # raw EXIF tag value 1–8
photo_exposure: Optional[PhotoExposure] = None
```

### `metascan/core/photo_exif.py` return type

```python
@dataclass
class PhotoExif:
    """Bundle of photo-EXIF fields returned by extract_photo_exif. Internal to
    the extractor; values are unpacked into Media kwargs by the scanner. Not
    persisted as a unit (each field maps to its own Media field / DB column,
    except `exposure` which serializes to the photo_exposure JSON column)."""
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    datetime_original: Optional[datetime] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_altitude: Optional[float] = None
    exposure: Optional[PhotoExposure] = None
```

`orientation` is **not** part of `PhotoExif` — it's returned separately because the scanner needs it before EXIF parsing completes (to apply `exif_transpose` for display dims).

`width` / `height` semantics change to **display dimensions** (post-`exif_transpose`). Pre-existing rows keep their stored values until the next rescan, at which point they update to display dims.

### SQLite schema

Added in `_init_database` via the existing `_ensure_column` idempotent pattern:

```sql
ALTER TABLE media ADD COLUMN camera_make        TEXT;
ALTER TABLE media ADD COLUMN camera_model       TEXT;
ALTER TABLE media ADD COLUMN lens_model         TEXT;
ALTER TABLE media ADD COLUMN datetime_original  TEXT;
ALTER TABLE media ADD COLUMN gps_latitude       REAL;
ALTER TABLE media ADD COLUMN gps_longitude      REAL;
ALTER TABLE media ADD COLUMN gps_altitude       REAL;
ALTER TABLE media ADD COLUMN orientation        INTEGER;
ALTER TABLE media ADD COLUMN photo_exposure     TEXT;   -- JSON blob
```

### Covering indexes

The `/api/media` summary SELECT gains six new columns: `camera_make`, `camera_model`, `datetime_original`, `gps_latitude`, `gps_longitude`, `orientation`. (Lens model, altitude, and the exposure JSON stay metadata-panel-only and are read from the main table on demand.)

`idx_media_summary_added` and `idx_media_summary_modified` must include every column the summary projects (per CLAUDE.md). The existing `_init_database` index-rebuild pass reads `sqlite_master` DDL and recreates any index whose definition is missing a currently-required column — this handles the rebuild on first launch with the new code without explicit migration code.

### Inverted index (`indices` table)

Three new `index_type` values:

| index_type | key | source |
|---|---|---|
| `camera_make` | `"Apple"`, `"Canon"`, ... | NULL |
| `camera_model` | `"iPhone 15 Pro"`, `"EOS R5"`, ... | NULL |
| `has_gps` | `"yes"` (only emitted when `gps_latitude IS NOT NULL`) | NULL |

`_generate_indices` and `_update_indices` extended to emit these. The `source` column stays NULL for camera/GPS rows (only tag rows use prompt/clip/both).

### Migration mechanics

- **`PRAGMA user_version` 1 → 2.** Gates the one-shot thumbnail-cache directory wipe (see §5).
- Column adds are idempotent — reruns are no-ops.
- No backfill from the existing `data` JSON blob; that blob never held real-world EXIF, so there's nothing to recover. Pre-existing rows surface NULL values until rescan.

---

## 4. Scan pipeline

### `metascan/utils/heic.py` (new)

```python
def register_heif_opener() -> None:
    """Idempotent: registers pillow-heif with Pillow. Safe to call multiple times."""
    try:
        from pillow_heif import register_heif_opener as _register
        _register()
    except ImportError:
        logger.warning("pillow-heif not installed; HEIC files will be skipped.")
```

Called at module-level in:

- `backend/main.py` (server process)
- `metascan/workers/embedding_worker.py`
- `metascan/workers/inference_worker.py`
- `metascan/workers/upscale_worker.py`
- `metascan/cache/thumbnail.py`

Each subprocess has its own Pillow handler registry, so each entry point must register independently.

### `metascan/core/photo_exif.py` (new)

```python
def extract_photo_exif(exif: Image.Exif | None) -> tuple[PhotoExif | None, int | None]:
    """Returns (photo_exif, orientation_tag).

    - photo_exif is None when no photo-relevant tags are present (caller can
      skip the Camera section cleanly).
    - orientation_tag is returned separately so callers can pass it to
      ImageOps.exif_transpose without re-parsing.
    """
```

Field-by-field extraction rules:

- `Make`, `Model`, `LensModel` from main IFD; `.strip()`; `None` if empty.
- `DateTimeOriginal` from `ExifIFD` (tag `0x9003`); fallback to main-IFD `DateTime` (`0x0132`). Format `"YYYY:MM:DD HH:MM:SS"` parsed via `datetime.strptime`. Reject `year < 1900` or `> 2100` → `None`.
- `ExposureTime` (rational) → `"1/N"` if numerator is 1, else decimal seconds rounded to 2 places.
- `FNumber` (rational) → `float`, rounded to 1 decimal.
- `ISOSpeedRatings` / `PhotographicSensitivity` → `int` (handle tuple form by taking first element).
- `Flash` (bitfield) → human string via lookup table:
  - `0x00 → "Off, Did not fire"`
  - `0x01 → "Fired"`
  - `0x09 → "On, Fired"`
  - `0x10 → "Off, Did not fire (compulsory)"`
  - `0x18 → "Auto, Did not fire"`
  - `0x19 → "Auto, Fired"`
  - unknown values → `f"Flash 0x{value:02X}"`
- `FocalLength` (rational) → `float` mm, rounded to 1 decimal.
- `FocalLengthIn35mmFilm` → `int`.
- `Orientation` (tag `0x0112`) → returned as second tuple element. Values 1–8.
- `GPSInfo` IFD via `exif.get_ifd(IFD.GPSInfo)`:
  - `GPSLatitude` + `GPSLatitudeRef` → signed decimal (`'S'` → negative).
  - `GPSLongitude` + `GPSLongitudeRef` → signed decimal (`'W'` → negative).
  - `GPSAltitude` + `GPSAltitudeRef` → signed (`ref==1` → below sea level → negative).
  - DMS rationals come from Pillow as `(IFDRational, IFDRational, IFDRational)`. Helper `_dms_to_decimal((d, m, s)) → float` does the conversion.
  - **Sanity guard:** reject `(0, 0)` and any `|lat| > 90` / `|lon| > 180` → both `None`. Failed-lock GPS values discarded silently.

Each field decode wrapped in narrow `try/except` returning `None` for that field — one bad tag never kills the parse. Whole-function failure (extremely unlikely) returns `(None, None)`; Media still saves.

If all photo-relevant fields come back `None` after extraction, return `PhotoExif=None` so the Camera section hides cleanly.

### `metascan/core/scanner.py`

```python
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif",
                        ".heic", ".heif",
                        ".mp4", ".webm"}
```

In the image-scan branch:

```python
with Image.open(file_path) as img:
    photo_exif, orientation = extract_photo_exif(img.getexif())
    transposed = ImageOps.exif_transpose(img)
    width, height = transposed.size                 # display dims
```

`Media(...)` construction passes the new fields. The existing `_get_exif_metadata` path in `extractors/base.py` is **unchanged** — it stays focused on AI-generation fields (UserComment, prompt). Pillow caches the parsed `Exif` object on the `Image` instance, so the scanner's single `getexif()` call serves both consumers.

The existing `subprocess(exiftool, ...)` dimension-fallback for unreadable files stays as-is for genuinely-broken files.

### Thumbnail cache (`metascan/cache/thumbnail.py`)

```python
with Image.open(source) as img:
    img = ImageOps.exif_transpose(img)   # before resize
    img.thumbnail((max_w, max_h), ...)
```

### Embedding / inference / upscale workers

Same `ImageOps.exif_transpose()` insertion immediately after `Image.open` in each. CLIP embeddings are computed against the user-visible image.

---

## 5. Thumbnail cache invalidation

Existing iPhone thumbnails generated by the pre-orientation code are sideways and would stay cached forever (cache key is `(file_path, mtime, size)`; `mtime` hasn't changed).

**One-shot mitigation:** on first launch with `user_version < 2`, the migration code wipes the thumbnail cache directory contents (not the directory itself). Logged as an INFO line with the file count. After the wipe, `user_version` advances to 2 and the wipe never re-runs.

---

## 6. UI: MetadataPanel

Section order inside `frontend/src/components/metadata/MetadataPanel.vue`:

1. **Tags** (existing — moved to top)
2. File (existing)
3. Image (existing)
4. **Camera** (new — only if `extract_photo_exif` returned non-null)
5. **Location** (new — only if both `gps_latitude` and `gps_longitude` are non-null)
6. Generation (existing — only if `metadata_source` set)

Real-world photos render Tags → File → Image → Camera → Location. AI-generated PNGs render Tags → File → Image → Generation. No empty sections.

### `CameraSection.vue` (new)

Shown when **any** of `camera_make`, `camera_model`, `lens_model`, `datetime_original`, or `photo_exposure` is set. (`orientation` deliberately does **not** trigger the section — many AI tools write `Orientation=1` to all output, which would render an Orientation-only Camera section on every AI image. Orientation still appears as a row *inside* the section when the section is shown for one of the other reasons.)

| Label | Source | Format |
|---|---|---|
| Camera | `camera_make` + `camera_model` | `"Apple iPhone 15 Pro"` (joined; one if other missing) |
| Lens | `lens_model` | as-is |
| Date taken | `datetime_original` | `toLocaleString()` (locale-aware) |
| Shutter | `photo_exposure.shutter_speed` | `"1/250 s"` |
| Aperture | `photo_exposure.aperture` | `"f/1.8"` |
| ISO | `photo_exposure.iso` | `"ISO 400"` |
| Focal length | `photo_exposure.focal_length` (+ `focal_length_35mm`) | `"24 mm (35mm equiv. 27 mm)"` |
| Flash | `photo_exposure.flash` | as-is |
| Orientation | `orientation` (1–8) | mapped via lookup: 1=Landscape, 3=Landscape (rotated 180°), 6=Portrait, 8=Portrait (rotated 270°), 2/4/5/7=Mirrored variants |

Each row uses the existing `<MetadataField>` component.

### `LocationSection.vue` (new)

Shown when both `gps_latitude` and `gps_longitude` are non-null.

```
┌─────────────────────────────────────────────┐
│  Location                                   │
├─────────────────────────────────────────────┤
│  ┌───────────────────────────────────────┐  │
│  │       [MapLibre map, ~220 px tall]    │  │
│  │            📍 marker at lat,lng       │  │
│  └───────────────────────────────────────┘  │
│  Coordinates    37.7749° N, 122.4194° W     │
│  Altitude       12 m above sea level        │
│  [Open in OSM ↗]   [Copy coords]            │
└─────────────────────────────────────────────┘
```

- **Library:** `maplibre-gl` (`npm i maplibre-gl @types/maplibre-gl`). Imported via dynamic `import()` only when `LocationSection` mounts — keeps ~200 KB out of the main bundle.
- **Tile source:** OpenFreeMap default style (`https://tiles.openfreemap.org/styles/liberty`). Backend `config.json` adds `ui.map_tile_url`; frontend reads via `useSettingsStore().mapTileUrl`. One config knob, no API key, swap-able for any MapLibre style URL (self-hosted included).
- **Interactivity:** zoom + pan, double-click zoom, scroll-zoom on hover only (so trackpad scroll through the metadata panel doesn't accidentally zoom the map).
- **Marker:** built-in `Marker` at `[lng, lat]`, default zoom 13. Click marker → opens `https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=15/{lat}/{lng}` in a new tab.
- **Selection change:** watcher updates marker + `map.flyTo` instead of recreating the map. Map destroyed on unmount.
- **Coordinate display:** decimal degrees with hemisphere letters; raw float available via "Copy coords".
- **No reverse geocoding** in this spec.
- **Fallback:** if MapLibre dynamic import fails (CDN/network), render coordinates as text + "Open in OpenStreetMap" link only. Map widget never blocks the rest of the panel.

### `frontend/src/types/media.ts`

`Media` interface + `PhotoExposure` interface gain the new fields. The Python `Media.from_dict_fast` builder is extended to handle them.

---

## 7. Filter sidebar

### Backend (`backend/api/filters.py`)

`/api/filters` returns three new bucket types alongside existing ones:

```jsonc
{
  "camera_make":  [{"key": "Apple",  "count": 312}, {"key": "Canon", "count": 87}, ...],
  "camera_model": [{"key": "iPhone 15 Pro", "count": 142}, ...],
  "has_gps":      [{"key": "yes", "count": 245}]
}
```

Buckets are computed via the existing `SELECT key, COUNT(*) FROM indices WHERE index_type=?` pattern. No new SQL shape.

The existing filter-application code joins `indices` to `media` on `(index_type, key)`; selecting "Apple" under Camera Make filters the grid to rows with `(index_type='camera_make', key='Apple')` in `indices`. AND-narrowing across buckets works automatically.

`has_gps` is a single-bucket section ("yes" only). The "no GPS" view is the unfiltered default minus the "yes" filter — no `has_gps='no'` rows are emitted (would double the index size).

### Frontend (`FilterPanel.vue`)

Three new `<FilterSection>` configs:

```ts
{ title: "Camera Make",   indexType: "camera_make",  collapsed: false }
{ title: "Camera Model",  indexType: "camera_model", collapsed: true  }
{ title: "Has Location",  indexType: "has_gps",      collapsed: true  }
```

Existing `FilterSection` already supports search-within-section, ordering by count desc, and "show top N / expand all". No new component work.

---

## 8. Error handling

| Failure | Behavior |
|---|---|
| HEIC decode failure (corrupt file) | Caught at `Image.open`; logged WARNING; file skipped. Same fall-through as unreadable JPEGs. Scan job continues. |
| `pillow-heif` import failure at startup | Logged WARNING once at process start; one-line WARNING per HEIC file encountered during scan. Server still starts. |
| `extract_photo_exif` field-level decode failure | Narrow `try/except` → that field is `None`, rest survives. |
| `extract_photo_exif` whole-function failure (extremely unlikely) | Returns `(None, None)`; Media saved without photo EXIF. |
| Bad GPS values (zeros, out of range) | Silently treated as "no GPS"; no log spam (this is normal for failed lock). |
| Bad `DateTimeOriginal` (year < 1900 / > 2100, malformed) | Treated as missing. |
| `LocationSection` MapLibre dynamic import failure | Fallback: coordinates + OSM link as text. |
| Tile-fetch errors | MapLibre's responsibility — degrades visually (gray tiles); no throw. |
| Pre-existing media rows with NULL photo fields | Camera and Location sections gated on field presence; simply don't render. |

---

## 9. Edge cases

| Case | Handling |
|---|---|
| AI-generated PNG with no EXIF | `extract_photo_exif` returns `None`; no Camera/Location sections. |
| AI-generated JPEG with fake camera EXIF | Camera section shows whatever's there. EXIF data is treated as authoritative. |
| Photo with `Orientation=1` already pre-rotated by another tool | `exif_transpose` is a no-op; dimensions stored as-is. |
| Photo edited in Lightroom (DateTime updated, DateTimeOriginal preserved) | "Date taken" uses `DateTimeOriginal` — correct. |
| Live Photo `.heic` with motion track | Still image only; motion track ignored. |
| Multi-image HEIC sequence | Pillow opens primary image; rest ignored. |
| GPS with 4 DMS components (some Garmin) | Helper takes first 3, ignores rest. |
| Negative altitude (Death Valley, Dead Sea) | Stored signed; displayed as `"-30 m below sea level"`. |
| Existing thumbnail cache for sideways iPhone photos | One-shot wipe gated on `user_version=2`. |

---

## 10. Testing

All new tests run alongside the existing 175. Target: ~190 total after this work.

### `tests/test_photo_exif.py` (new)

Pure unit tests against `extract_photo_exif`. Synthetic `Image.Exif` objects built in-test.

- JPEG with full Apple iPhone EXIF (make/model/lens/exposure/GPS/orientation=6).
- JPEG with Canon EOS EXIF, no GPS.
- JPEG with bogus GPS (0, 0) → no coordinates returned.
- JPEG with out-of-range GPS (lat=100) → no coordinates.
- HEIC with iPhone EXIF (small synthesized fixture under `tests/fixtures/photos/`, generated via Pillow + pillow-heif lazily in `conftest.py`, not committed binary).
- PNG with no EXIF → returns `(None, None)`.
- JPEG with corrupt `DateTimeOriginal` string → that field is `None`, rest survives.
- DMS-to-decimal helper: north/south/east/west sign, edge values (89.9999, 179.9999, 0).
- Flash bitfield decoder: 8 common values + an unknown bit (graceful "Flash 0xXX" fallback).
- Orientation-to-label mapper: all 8 values.

### `tests/test_scanner_heic.py` (new)

- Scan a directory with one HEIC and one JPEG; assert both ingested with correct display dims (post-orientation) and correct EXIF columns.
- Scan with `pillow_heif` mocked-out at import → HEIC skipped without crash.

### `tests/test_database_photo_columns.py` (new)

- `save_media` with all photo columns populated → round-trips via `get_media`.
- `_generate_indices` emits `camera_make`, `camera_model`, `has_gps` rows correctly.
- Migration on a pre-existing v1 DB adds columns without data loss; `user_version` advances 1 → 2.
- Covering indexes rebuilt to include new summary columns (assert via `sqlite_master` DDL inspection).

### `tests/test_filters_camera.py` (new)

- `/api/filters` returns `camera_make` / `camera_model` / `has_gps` buckets with correct counts on a seeded DB.
- Filtering by `(camera_make='Apple')` returns only Apple rows.
- AND-narrowing across `camera_make='Apple'` and `camera_model='iPhone 15 Pro'` returns the intersection.

### Frontend

No new test infra (project doesn't currently have FE unit tests per CLAUDE.md). Manual verification checklist:

- HEIC file from iPhone scans, displays right-side-up in grid, opens in viewer.
- Camera section shows make/model/lens/exposure/datetime/orientation.
- Location section renders MapLibre map at correct coords with marker.
- Pan/zoom map works; scroll-zoom only on hover.
- "Open in OSM" link opens correct location.
- Filter buckets appear and filter correctly.
- AI-generated PNG hides Camera + Location sections; Generation section still works.
- Cross-platform smoke: scan one HEIC on Windows, WSL2 (Linux), macOS Apple Silicon.

---

## 11. Cross-platform compatibility

All library choices verified for Windows, WSL2, Linux, macOS (Apple Silicon):

| Library | Status |
|---|---|
| `pillow-heif` | manylinux + macOS-arm64 + Windows wheels published; bundled libheif on Win/macOS, system libheif on Linux. |
| `maplibre-gl` | Pure browser JS; no native deps. |
| `@types/maplibre-gl` | Pure TS types. |
| OpenFreeMap tiles | HTTPS endpoint; no client-side dep beyond MapLibre. |

No new Python deps require compilation steps; no new Node deps require native modules.

---

## 12. Configuration additions

### `config.json`

```jsonc
{
  "ui": {
    "map_tile_url": "https://tiles.openfreemap.org/styles/liberty"
  }
}
```

Single new key. Optional — defaults to OpenFreeMap liberty if absent. Enables self-hosting any MapLibre style URL.

### `requirements.txt`

```
pillow-heif>=0.16.0
```

Single new dep. No version-pin churn.

### `frontend/package.json`

```jsonc
{
  "dependencies": {
    "maplibre-gl": "^4.0.0"
  },
  "devDependencies": {
    "@types/maplibre-gl": "^3.0.0"
  }
}
```

---

## 13. Out of scope (deferred to follow-ups)

- **Smart-folder rule fields** for camera/exposure/datetime/GPS. Power-user surface; benefits from a focused round of design once we see how the basic camera filter buckets get used.
- **GPS scrubbing / privacy toggle**. Per-folder or global "hide GPS" option. Worth designing properly with consideration for what happens to the underlying DB rows.
- **Reverse geocoding**. Showing "San Francisco, CA" instead of just coordinates. Requires Nominatim or similar; rate limits + privacy implications need their own design.
- **Live Photo motion track**. Treating Apple `.heic`+`.mov` motion photos as a video pair.
- **MakerNotes lens decoding** (Sony, Olympus). Pillow doesn't decode MakerNotes; would need `exifread` or a manufacturer-specific parser.
- **Per-photo EXIF re-extraction** without a full file rescan (e.g. user added GPS via desktop tool after first scan). Currently new EXIF only flows in when the file mtime changes.
