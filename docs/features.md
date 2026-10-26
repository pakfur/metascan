# Features

[← Back to README](../README.md)

## Media Browsing
- Virtual-scrolling thumbnail grid for large collections (12,000+ items)
- Three-panel resizable layout: filters, thumbnails, metadata
- Thumbnail size presets (S/M/L)
- Sorting by file name, date added, or date modified — plus relevance while a search is active
- ★ favorites (star toggle) and ✓ pass / ✗ fail grades, with a small grade badge on every tile (see [Rating](#rating-favorites-and-grades))
- Dark/light mode following system preference

## Media Viewer
- Full-screen overlay with image zoom (mouse wheel) and pan (click-drag)
- Video player with play/pause, seek bar, volume, playback speed (0.25x-2x)
- Frame-by-frame stepping (comma/period keys)
- Previous/next navigation with keyboard arrows
- Per-file playback speed persistence
- Rating rail: ★ favorite, ✓ pass / ✗ fail grade, reasons and categories, all from the keyboard (see [Rating](#rating-favorites-and-grades))
- Keyboard shortcuts help overlay

## Rating: favorites and grades

Two independent marks, set in the media viewer (and in the Text to Image and Image to Video result viewers):

- **★ Favorite**: "I want to look at this again". It drives the Favorites filter, the smart-folder favorite rule and the star on tiles, and nothing ever learns from it. The tile star, the slideshow's F and the mobile viewer's star set it too.
- **Grade**: ✓ **Pass** ("the prompt did what I wanted"), ✗ **Fail** ("it didn't"), or none. Grades are the only thing the taste scorer, prompt-writer training, the writer statistics and the writer scorecard learn from. Looking at an image teaches nothing.

Neither mark changes the other: an image can be ★ and ✗, or ✓ without ★.

In the viewer:

- **F** toggles ★, **P** toggles ✓ and **X** toggles ✗. Pressing the key of the grade already set clears it. Switching between ✓ and ✗, or clearing, also clears the reasons.
- **1–6** toggle a reason (realism, composition, emotion, light, concept, motion): what worked for a ✓, what missed for a ✗. A reason needs a grade first ("Press P or X first").
- **C** opens the category picker. Shift+Enter makes a category sticky: it is added to every image you grade until you turn it off or reload the page.
- **R** starts review mode: ungraded images the taste scorer is least sure about, the ones closest to 0.5 first.
- The rating rail beside the image has the same buttons (★ Favorite, ✓ Pass, ✗ Fail, or "No grade"), the reasons ("worked on…" for ✓, "missed on…" for ✗), the taste score (the chance you'd pass this), the image's place in its T2I batch, and "● seen". Clicking a button never takes the keyboard away from the viewer.

Library, Text to Image and Image to Video tiles show the grade as a small read-only ✓ / ✗ badge; grading happens in the viewer. The library grid shows a grade as soon as you give it. While the viewer is open it keeps the images it opened with, in the same order, whatever changes behind it (new images arriving, F5, an image leaving a Grade or ★ folder because you just graded or starred it), so every key acts on the image on screen. Smart folders have a **Grade** rule (✓ Pass, ✗ Fail or No grade), so "every ✗" is one folder away.

**Deleting a graded image keeps what it taught.** Before the file goes to the trash, Metascan saves a JPEG thumbnail of it (at most 512 px, about 50 KB) in `data/archive/thumbs/`, and the deleted image's grade, reasons, categories and hindsight brief stay on record. Its prompt stays too: a generated image keeps it in its generation record, and a ✓ library image keeps the prompt from its metadata (a ✗ or ungraded library image does not). The taste scorer, prompt-writer training and the scorecard keep counting it, even after you switch to another CLIP model: the deleted images are then re-embedded from their thumbnails in the background, only while the GPU is otherwise idle (Config → Training shows "re-embedding deleted images, n / N"). Keep `data/archive/`: without a deleted image's thumbnail it cannot be re-embedded after a CLIP switch, so it stops counting for the taste scorer, and a deleted ✓ image with no hindsight brief yet can no longer get one.

### Upgrading an existing library (one time)

The first start of this version converts your ratings, once:

- every ★ favorite stays ★ and also becomes ✓ pass;
- every old reject (the old X key, stored as "pass" in the old data) becomes ✗ fail;
- an image you had only looked at stays ungraded;
- reasons, categories and the dates the taste scorer weighs by are kept, and nothing is added to the rating history.

The taste scorer's model from before keeps its scores until it has retrained on the grades. With auto-retrain on (the default) that happens by itself once there are 20 ✓ and 20 ✗; until then the Training tab says how many are missing.

Before it changes anything, Metascan backs up the database next to it as `metascan.db.pre-v6-<date>-<time>.bak` (in UTC, for example `metascan.db.pre-v6-20261025-091500.bak`), in `data/`, or in `~/.metascan/data/` for the packaged app. The drive needs free space of about 1.2 × the database's size. Only a database with nothing to convert is not backed up: no ★ favorite, no image you have rated or even just viewed in the viewer, and no record of a deleted image. On a large library the first start takes a while (the backup is a full copy of the database): let it finish. Delete the backup once you are happy with the upgrade.

If the backup or the upgrade fails, **the server does not start**. It prints one message that says why (for example how much space to free) and that your ratings are unchanged. Fix the cause and start Metascan again: the upgrade is tried again. A failed upgrade normally deletes the backup it took (the database is unchanged, so it still matches it; the message names the backup when it keeps one), and the next start takes a new one, so retries never fill the drive with copies. Under `python run_server.py`, which runs with auto-reload, a refused start leaves the reloader running and holding the port (8700 by default), so the terminal looks busy: press **Ctrl-C** before you start again. Left running, it would retry the upgrade after every change to a backend file.

To go back to the backup:

1. Stop the server.
2. In the data folder, move `metascan.db` to another folder, together with `metascan.db-wal` and `metascan.db-shm` if they are there. Keep them until you are sure.
3. Copy the `.bak` file into the data folder as `metascan.db`. If there are several, take the newest (the date and time are in its name).

The backup is the database from before the upgrade, so this version upgrades it again (with a new backup) on its next start. To keep the old ratings as they were, run the previous version of Metascan.

## Slideshow
- Ordered or random (shuffled) playback
- Configurable image duration (3-30 seconds)
- Fade transition with adjustable duration
- Auto-hide controls after 3 seconds
- Video support (no auto-advance, manual navigation)
- Expand to true full screen (no browser chrome); media scales to the screen at its own aspect ratio, Escape returns to normal display

## Search (Content, Similar, Tags)
Search lives in the **SEARCH section** at the top of the left filter panel and behaves like a filter: results intersect with the Video/Images/Favorites presets, the standard filters, and the selected folder rather than replacing the grid.
- CLIP-powered text-to-image content search (type a description, find matching media)
- FAISS-based visual similarity search (right-click any thumbnail, "Find Similar" — shows a "Similar to …" chip in the panel)
- Tag search — autocomplete chips ANDed together (tag AND tag AND tag), combinable with the text search
- Unbounded results defined by an adjustable score threshold (text search 0–0.45, default 0.2; Find Similar 0–1, default 0.7); moving the slider re-runs the search
- Relevance sort option, auto-selected when a search runs and restored to your previous sort on clear
- Inline model-status chip; a search submitted before the CLIP worker is ready is queued and fires automatically once the model loads
- Multiple CLIP model sizes (Small/Medium/Large)
- Device selection (Auto/CPU/CUDA)
- Embedding index build/rebuild with live progress

## Hardware Detection & Feature Gating
- Auto-detects CPU / RAM / CUDA / MPS / Vulkan / glibc / NLTK at startup
- Classifies the host into one of five tiers (CPU only, Apple Silicon, CUDA entry/mainstream/workstation)
- Per-model availability + recommendation chips in the Models config tab — green "recommended" picks the right CLIP/upscaler for your tier; red "unsupported" explains *why* on hover (insufficient VRAM, no real Vulkan device, glibc too old, etc.)
- Surfaces actionable warnings: WSL2 without GPU Vulkan blocks RIFE, Linux glibc < 2.29 breaks `rife-ncnn-vulkan`, NLTK ≥ 3.8.2 needs `punkt_tab` instead of legacy `punkt`
- Shared device picker (CUDA → MPS → CPU) so Apple Silicon Macs now use MPS for CLIP inference automatically

See [Hardware Detection](hardware-detection.md) for the full probe / tier / gate reference.

## Duplicate Detection
- pHash-based perceptual duplicate grouping
- Side-by-side comparison with thumbnails
- Checkbox selection for batch deletion
- Files moved to trash (recoverable)

## Media Upscaling
- Real-ESRGAN upscaling (2x/4x) with General and Anime models
- GFPGAN face enhancement
- RIFE video frame interpolation (2x/4x/8x FPS multiplier)
- 1-4 concurrent workers
- Queue management with pause/resume, clear completed
- Real-time progress tracking via WebSocket

## Scanning & Indexing
- Multi-phase scan with real-time progress (file-by-file WebSocket updates)
- Stale entry cleanup for deleted files. It never removes the entries of a scan folder whose files can't be seen: a folder that is missing (an unplugged drive, a renamed folder) or can't be read, or one that is empty while the library still has images from it (the mount point of a drive that is not mounted). Those entries stay in the library, ratings and all, until the folder is back. Only the scan folders themselves are checked: a drive mounted at a subfolder *inside* a scan folder is not, so while it is unmounted its files look deleted. Add such a mount point to the scan list as a folder of its own, or run the cleanup only while the drive is mounted
- Full clean & rescan wipes the database and re-imports every file, keeping ★ favorites, ✓ / ✗ grades, reasons and categories. It is refused while a scan folder is missing, can't be read, or is empty while the library has images from it. Reconnect (or mount) the drive and try again. Remove the folder from the scan list (Configuration → Directories) only if it is gone for good: the ratings of its images are then dropped from the library. If a folder goes missing during the rescan, the other folders are still imported, the Done step names the folder, and the ratings of its images wait: they come back when a later scan finds the files again
- A full clean also re-imports Text-to-Image and Image-to-Video output, which usually lives outside the scan folders (under ComfyUI's output folder). An image or clip the rescan does not find holds the full clean back while the output folder it was in (Text-to-Image's or Image-to-Video's: the one set in its configuration, or else the `t2i` or `i2v` folder in ComfyUI's output folder) is missing or can't be read, for example on a drive that is not mounted: the full clean stays unfinished, the ratings of those images wait, the Text-to-Image and Image-to-Video lists keep their records, and the Done step names the folder. A folder missing inside one of them (an image's or a day's) holds nothing back: its images count as deleted. An output folder that is itself a drive's mount point is the exception to watch: when the drive is unmounted, the empty mount point left behind counts as present, so a full clean records the drive's images as deleted. Set the output folder to a folder inside the drive rather than to the drive's mount point itself, or run Full clean & rescan only while the drive is mounted. If an output folder is gone for good, recreate it (empty is fine) and run Full clean & rescan again: its missing images then count as deleted, and each one you had graded, viewed, starred, put in a category or that has a hindsight brief is recorded as deleted with its history. Nothing is lost that way. An image or clip that is on disk but can't be read or imported holds the full clean back too, however many scans fail on it, and its ratings wait: the Done step lists such files (the first 50, and how many there are in all), and any later scan lists them again. Delete each one or move it somewhere else (or repair it), then run Full clean & rescan; one that only failed this time (a busy disk) comes back by itself at the next scan. Deleting `full_clean_incomplete` from the data folder alone is not enough while the pending ratings file (`taste_snapshot_before_full_clean.json`) is still there, and deleting that file too drops those ratings
- Automatic thumbnail generation
- pHash computation during scan
- File system watcher with auto-refresh

## Metadata Extraction
- ComfyUI workflow extraction with enhanced parsing
- SwarmUI parameter parsing
- Fooocus metadata support
- Extracts: prompt, negative prompt, model, sampler, scheduler, steps, CFG scale, seed, LoRAs
- Custom prompt tokenization with NLTK for searchable keywords

## Filtering
- Filter by directory path (with subfolder support)
- Filter by AI source, model, LoRA, file extension, tags, prompt keywords
- Collapsible filter sections with item counts
- AND logic between filter types, OR within same type
- Favorites-only toggle
- Active searches (content, Find Similar, tag AND) compose with all of the above as additional filter layers

## Folders (Static & Smart)
- **Static folders** — manually curated collections; right-click items to add/remove
- **Smart folders** — saved rules over fields like tags, model, source, modified/added date, favorite status, grade (✓ pass, ✗ fail or none), and whether an image has one or more I2V (image-to-video) clips. Evaluated client-side and re-resolved as the library changes
- Cross-tab sync over WebSocket: creating, renaming, or moving items broadcasts to every connected browser

## Storyboards
Desktop-only authoring canvas at `/storyboard/:id?` (nav button in the header action row), for scripting and generating multi-panel AI image sequences via a driven ComfyUI backend.
- Create a storyboard with a name, aspect ratio, target model, and shared style block / negative prompt applied to every shot
- Import a plain-text script and have it parsed into scenes and panels (shots), or build the scene/panel tree by hand
- **Story engine** — from a premise, AI-build the outline, scenes, shots, and beats in sequence; re-roll any single level, and every generated field is editable afterward
- Define subjects (characters) with a LoRA and reference image, reused across beats
- Attach up to two subject reference images plus a per-scene setting reference from the library, and have the VLM draft a description/voice or setting/lighting/mood suggestion for review before accepting it
- Per-beat controls for shot size, camera angle, lens, and which subjects are on screen; prompts can be locked to protect hand edits from re-synthesis (a shot is the H3 generation unit — a video clip and its compiled prompt — while a beat is its internal timeline unit, carrying the framing, cast, and still-image keyframe)
- **Synthesize** — generates per-beat still-image prompts (VLM-composed or a deterministic template fallback), respecting locked beats
- **Generate** — submits beats to ComfyUI with deterministic per-beat/variant seeds; supports generating only failed beats and rerolling a single beat for a fresh variant
- Live synthesis and per-beat job progress over WebSocket, with cancel-all support
- Candidate picker per beat — every rendered variant is kept as a candidate thumbnail; click one to make it the keeper. Non-keeper variants are hidden from the main library grid until picked
- Hidden-media toggle ("Show hidden" in the view menubar) reveals those in-progress/non-keeper variants in the grid, each marked with an eye-slash badge
- **Compile** — compile shots into MiniMax H3 video prompts (six-section format), rendering each beat as its own `[Shot n]` section with its framing and cast, with lint warnings, editable in the side panel
- **Generate video** — render shots to video via a local ComfyUI H3 workflow; clips land as hidden panel candidates for review/keeper selection/reroll

## Image to Video
Right-click any image → "Image to Video…" opens a dialog to turn it into a short MiniMax H3 clip via a driven ComfyUI backend.
- Type an idea and expand it into a full I2VA video prompt (VLM-composed, editable before generating)
- The prompt is linted as you type; **Apply fixes** rewrites natural camera wording ("slowly dollies in") into MiniMax's motion vocabulary ("pushes in at slow speed") and quoted speech into its `(S1) says: <d>[English] …</d>` dialogue form, showing each before/after first — the prompt is never changed without pressing it
- Pick a clip duration and a fast/quality generation preset; High quality also offers a sampler Steps choice (20–40, default 25)
- Pick a **Cadence**: Single take, or a découpage template that fixes the shot plan — timing, framing, camera and cuts — while the VLM writes only the action and lines. A template sets the clip duration. See [Image-to-Video cadence templates](i2v-templates.md).
- Pick an output size as a megapixel budget; the clip's orientation and aspect ratio always follow the source image, since that image is the first frame
- Optional LoRAs applied to the generation
- Generated clips appear as visible library media (not hidden, unlike storyboard candidates) in a strip within the dialog, with view, favorite (star), and delete actions
- Clips are saved under a configurable root directory with a date-expanding file prefix (Configuration → Video), e.g. `<root>/2026-09-20/minimax_<number>.mp4`
- Re-submitting with nothing changed (same seed, quality, steps, prompt…) asks for confirmation first, since it would render an identical clip
- The prompt is shown as its four regions — a read-only **Header** (fixed by the MiniMax format), then editable **Prompt**, **Soundscape** and **Non diegetic music** — each with the same popup editor the storyboard screens use. This is only a way of seeing and editing the one prompt; the text sent is unchanged
- **Seed after generate** (Fixed, Increment, Decrement, Random) advances the seed once per queued job, and **Generate count** (1–20) queues that many in one press. A Fixed seed would render the same clip every time, so the count is ignored and exactly one is queued
- The dialog closes only through its ✕ — clicking the backdrop does nothing, so a written prompt or a running job is never lost to a stray click
- Each clip is labelled with the quality (and steps) used, clip length and render time, and when it was generated
- Every finished clip remembers the idea, settings and prompt that made it: click a clip to load them back into the form (double-click still plays it). While a clip is selected, any change you make is saved to that clip automatically — **Stop editing** detaches the form. The clip's label and recorded provenance always describe the actual render; your edits are kept separately. Nothing is saved for a render that is cancelled or fails
- Live generation progress over WebSocket, with Cancel for the running workflow (or ✕ on a single queued/running job)

Requires two registered ComfyUI workflows (turbo and high quality) — see [Image-to-Video Workflow Setup](i2v-workflow-setup.md).

## Context Menu
- Open (full-screen viewer)
- Find Similar (image-to-image search, applied as a filter with a "Similar to …" chip)
- Upscale (open upscale dialog)
- Image to Video (images only — opens the Image to Video dialog)
- Delete (move to trash with confirmation)

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| F5 | Refresh media list |
| Ctrl+S | Open scan dialog |
| Ctrl+Shift+S | Open slideshow |
| Ctrl+Shift+D | Open duplicate finder |
| Ctrl+U | Upscale selected media |
| Esc | Close viewer / clear active search |
| Space | Play/Pause (video) |
| Left/Right | Previous/Next media (in viewer) |
| Up/Down | Volume up/down (video) |
| , / . | Previous/Next frame (video) |
| F | Toggle ★ favorite (viewer, slideshow) |
| P | Toggle ✓ pass (viewer) |
| X | Toggle ✗ fail (viewer) |
| 1–6 | Toggle a reason: realism, composition, emotion, light, concept, motion (viewer; press P or X first) |
| C | Categories; Shift+Enter makes one sticky (viewer) |
| R | Review mode: ungraded images, least certain first (viewer) |
| I | Show / hide the Details panel (Text to Image viewer) |
| M | Mute/Unmute (video) |
| Ctrl+D | Delete file |
| H or ? | Show shortcuts help (in viewer) |
