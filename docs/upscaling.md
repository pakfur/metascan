[← Back to README](../README.md)

# Upscaling

Metascan upscales images and videos with ComfyUI workflows you register yourself. It has no upscaling models of its own, so you need a running ComfyUI (set its address in **Config → Directories**).

## How it works

1. Register a workflow in **Config → Upscale**.
2. Right-click an image or video and choose **Upscale**, or select it and press **Ctrl+U**.
3. Pick a workflow and press **Upscale**. Set **Count** above 1 to get several results to choose from.
4. Results appear on the left. Click one and move the mouse across the picture to compare: the original is left of the line, the result is right of it.
5. Press **Accept** on the one you like.

Results are not in your library until you accept one. The others stay in the window until you discard them, or until you start a new run after accepting.

## What happens to your files

- **The upscaled file replaces the original in the library**, at the same place with the same name. If the workflow saves a different format, the extension changes (`photo.jpg` becomes `photo.png`). If that name is taken, it becomes `photo_2.png`.
- **The original moves to the Original folder.** It is not part of the library: it does not appear in the grid, in search or in any folder. It is kept only so you can compare and revert.
- **There is only ever one upscaled file and one original** for each item.
- Ratings, ★, categories, folders, the prompt and all other generation details stay with the item. Thumbnails, duplicates data and similarity are refreshed for the new pixels.

### Upscale again

**Upscale again** always starts from the original, never from the current upscaled file, so quality does not stack up or degrade. Accepting a new result sends the previous upscaled file to the trash.

### Revert

**Revert to original** puts the original back in the library, with its original name, and sends the upscaled file to the trash. Revert works with ComfyUI turned off.

### Deleting an upscaled item

Deleting it from the library sends its original to the trash too. Restore both from the trash if you change your mind, though Metascan will treat them as two unrelated files.

### Compare for videos

Videos compare the same way, with one play button and position bar for both. If the two cannot be kept in step, the window falls back to side by side.

## Registering a workflow

Export the workflow from ComfyUI in API format, then in **Config → Upscale** choose **Manage image workflows…** or **Manage video workflows…**. Any registered image workflow can be used on any image, and any video workflow on any video. The default you pick is only what the window starts on.

Metascan finds nodes by title, so give these titles to the nodes in ComfyUI:

| Title | Node | Needed |
|---|---|---|
| `MS_SOURCE_IMAGE` | Your image loader (`image` input) | Image workflows |
| `MS_SOURCE_VIDEO` | Your video loader (`video` input of VHS Load Video, or `file` of core Load Video) | Video workflows |
| `MS_SAVE` | The save node whose output is the result | Always |
| `MS_SEED` | The node holding the seed | Optional: without it, every run gives the same result, so Count is fixed at 1 |
| `MS_POSITIVE` | A text node (`text` input) | Optional: adds a Prompt box |
| `MS_NEGATIVE` | A text node (`text` input) | Optional |
| `MS_SCALE` | A number node with a `value` input, wired into the scale | Optional: adds a Scale menu, with the choices from **Config → Upscale** |

**Keep the soundtrack.** A video workflow must wire the audio through to its save node, or the upscaled video is silent. Metascan warns when you register a workflow that doesn't, and marks any result that lost its audio. The frame rate is also up to the workflow.

## The Original folder

By default originals are kept in `data/_original`. Choose another folder in **Config → Directories**. It must not overlap a scan folder or a generated-output folder, or the originals would be imported as library files.

Changing the folder with originals already there moves them: the **Moving originals** panel shows the progress, and you can pause or stop it. The setting changes only once every file has arrived. Accept and revert wait until the move ends. Stopping keeps every original usable, wherever it got to.

The Original folder is yours to back up. Metascan needs it for compare and revert, and for the prompt and other generation details of upscaled files after a rescan.

## Background activity

Runs show in the **Upscaling** panel, and Tagging and Embedding wait while one is working. Pause lets the job already in ComfyUI finish and holds the rest; Stop cancels the run and keeps results you already have. Closing the window does not stop a run.

A restart drops a run's jobs that have not been submitted yet. Jobs already submitted carry on, and their results are waiting when you open the window again.

## Moving to another PC

A library export does not include originals or upscale records. On the new PC an upscaled file is an ordinary file with no original to revert to.

## Upgrading from the old upscaler

Real-ESRGAN, GFPGAN and RIFE are gone, along with the Upscale Queue. Frame interpolation and face restoration are now up to your workflows.

- Files you upscaled before count as originals: the old upscaler sent their true originals to the trash.
- Downloaded weights are not removed. They take about 1 GB in `data/models/` (the `RealESRGAN_*.pth` and `GFPGANv1.4.pth` files and the `rife` folder) and can be deleted by hand.
