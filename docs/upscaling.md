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
| `MS_NEGATIVE` | A text node (`text` input) | Optional: adds a Negative prompt box |
| `MS_SCALE` | A number node with a `value` input, wired into the scale | Optional: adds a Scale menu, with the choices from **Config → Upscale** |
| `MS_DENOISE` | The sampler (`denoise` input), or a number node (`value`) wired into it | Optional: adds a Denoise menu |
| `MS_MODEL` | The model loader (`unet_name` of Load Diffusion Model, or `ckpt_name` of Load Checkpoint) | Optional: adds a Model box |
| `MS_LORA_STACK` | A Power Lora Loader (rgthree) | Optional: adds a LoRA list |

A node carries one title. When the sampler is already `MS_SEED`, as Ultimate SD Upscale is, give `MS_DENOISE` to a number node wired into its `denoise` input.

### Filled in from the image

For a generated image, the window starts with what the image was made with: its prompt, negative prompt, seed, model and LoRAs. Change any of them, or press **Reset** to put them back. For a T2I image the LoRAs are the exact stack it was made with; for others they come from the file's metadata.

The model and LoRAs are filled in only when the workflow's model is in the same model folder as the image's (`moss\dirtyMoss_bf16.safetensors` and `moss\moss_image_sft_bf16.safetensors` are both `moss`). A Krea LoRA in a Moss workflow fails or draws nonsense, so for an image from another model the window says so and leaves them out; **Use them anyway** puts them in. An empty Model box keeps the workflow's own model, and an empty Negative prompt keeps the workflow's own.

A scorecard image that is not graded yet keeps its prompt hidden here too, as it does everywhere else.

### Detail prompts

At a low denoise a refine works better with a short prompt about the detail to add than with the image's own scene prompt (see [Refining generated images](#refining-generated-images)). For a workflow with a prompt, the **Prompt** menu offers the image's own prompt, a prompt written from the image once you have written one, and four detail prompts:

| Detail prompt | For |
|---|---|
| Photo, people | Portraits and any photo where skin fills much of the image |
| Photo, scene | Landscapes, nature, architecture, interiors, products |
| Illustration | Digital art, paintings, anime, 3D renders: stops photo texture being added |
| Gentle | Anything; when the others add too much |

At a denoise of 0.3 or lower the window starts on the detail prompt for the image: Illustration when its VLM or CLIP tags name an illustration medium, Photo, people when any tag names a person, else Photo, scene. Above 0.3 it starts on the image's own prompt, and on a detail prompt when the image has none. Changing the denoise or the workflow switches between them while you have not edited the text; an edited prompt, marked **(edited)**, stays until you pick another entry or press **Reset**. Picking an entry replaces both the prompt and the negative.

The prompts live in `data/upscale_refine_prompts.yml`. Edit them, add your own or remove some: the window reads the file again the next time it opens. Each entry has an `id`, a `label`, a `prompt`, an optional `negative` and an optional `for` (`people`, `scene` or `illustration`), which the guess uses. Leave out parentheses: ComfyUI reads them as weights. A missing or broken file gives the built-in prompts, and the server log says why. The denoise limit is `upscale.refine_prompt_max_denoise` in `config.json` (0.3).

### A prompt from the image

An image with no prompt, or one written for another model, makes a poor start: SDXL and Pony prompts are tag lists, and Moss reads them badly. For a workflow whose model has a T2I prompt style (the Moss workflows), the window offers **Write a MOSS-Image prompt from the image**. It says when the image's own prompt is missing or was written for another model, and highlights the button then.

It works in two steps. The prompt writer (the VLM you use for T2I) looks at the image and describes it in a few plain sentences. That description then goes through the same caption-to-prompt step as a caption typed in the T2I window: the MOSS-Image guideline, its stock negative and your content mode from **Config → T2I**. The prompt and negative boxes are filled in; open **Written from this caption** to read the description it used. Edit the prompt as you like, or press **Reset** to go back to the image's own.

An upscaled item is described from its original. Nothing is saved until you run the upscale, and the description is not logged. It needs a prompt-writer model downloaded in **Config → Models**; without one the window says so.

**Keep the soundtrack.** A video workflow must wire the audio through to its save node, or the upscaled video is silent. Metascan warns when you register a workflow that doesn't, and marks any result that lost its audio. The frame rate is also up to the workflow.

## Ready-made workflows

`data/workflows/` holds workflows you can register as they are. Three use [SeedVR2](https://github.com/ByteDance-Seed/SeedVR), the strongest open upscaler in ComfyUI at the time of writing: it restores detail rather than sharpening what is there, and it works on video frames in groups, so it doesn't flicker.

| File | For | Needs |
|---|---|---|
| `upscale_image_seedvr2_7b_api.json` | Images, best quality | SeedVR2 pack and 7B model |
| `upscale_image_fast_api.json` | Images, a result in seconds | `4x_foolhardy_Remacri.safetensors` in `models/upscale_models` |
| `upscale_video_seedvr2_7b_api.json` | Videos, best quality | SeedVR2 pack and 7B model |
| `upscale_image_moss_refine_api.json` | Moss images: redraws detail and fixes generation flaws | Moss models, Ultimate SD Upscale pack, Remacri |
| `upscale_image_moss_refine_seedvr2_api.json` | Moss images at large sizes: the refine, then SeedVR2 | All of the above, and the comfyui-unload-model pack |

### Refining generated images

SeedVR2 restores real-world damage: blur, noise, compression, low resolution. It takes no prompt and stays faithful to what it is given, so on a generated image it keeps a malformed hand or a smeared texture and only makes it bigger.

The two Moss workflows redraw instead. [Ultimate SD Upscale](https://github.com/ssitu/ComfyUI_UltimateSDUpscale) enlarges the image, then Moss redraws it in 1024-pixel tiles at a low denoise, with the image's own prompt, model and LoRAs, so it adds the detail the model would have drawn at that size. **Denoise** sets how much it may change:

| Denoise | Use it to |
|---|---|
| 0.2 | Clean up: sharper texture, nothing moves |
| 0.3 | Add detail (the default) |
| 0.4 | Redraw small flaws: fingers, eyes, smeared fabric |
| 0.5 | Redraw more; the composition can start to change |

Run two strengths, or two seeds, and compare the results before accepting one. The refine works best at ×2 or less.

**The prompt.** Every tile is redrawn with the same prompt, so a prompt that describes the whole scene pushes a tile showing only a cheek or a wall toward that scene. At 0.2–0.3 a short prompt about the detail you want works better, which is why the window starts on a [detail prompt](#detail-prompts) there. The image's own prompt matters again at 0.4 and above, where Moss redraws content.

**No tile grid.** Each tile is redrawn on its own and drifts a little in colour, which showed as a faint grid on smooth skin. Both workflows end the refine with `easy imageDetailTransfer` (from [ComfyUI-Easy-Use](https://github.com/yolain/ComfyUI-Easy-Use)): it keeps the original's colour and tone and takes only the fine detail from the refine. Its `blur_sigma` (4) is where the two meet: lower takes more from the original, higher keeps more of the refine's tone and with it more of the grid. On two refined images the seams measured no stronger than the rest of the image at 4, with 99 % of the fine detail kept. It also undoes any broad colour change the refine made, which at these strengths is the drift itself.

**Refine + SeedVR2** refines at up to ×2, where Moss still draws well, then frees the GPU and runs one SeedVR2 pass to the chosen scale of the original, at most 16 megapixels. Use it for a large final size.

**Setup.** Install `comfyui_ultimatesdupscale`, `comfyui-easy-use` and, for the second workflow, `comfyui-unload-model` with the ComfyUI Manager. The workflows load `moss\moss_image_sft_bf16.safetensors`, `qwen3.5_9b_bf16.safetensors` and `flux2-vae.safetensors`, as the Moss T2I workflows do; the window replaces the model with the image's own. Refining another model family needs its own copy of the workflow with that family's loaders.

**Setup.** Install [ComfyUI-SeedVR2_VideoUpscaler](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler) with the ComfyUI Manager. Its models go in `ComfyUI/models/SEEDVR2/` (`seedvr2_ema_7b_fp16.safetensors`, `ema_vae_fp16.safetensors`) and download on first use if they are missing. They were tested on a 32 GB card. On a smaller card, change the model and lower the size limits.

**Size limits.** Every workflow offers the Scale menu, but SeedVR2 results stop growing at 16 megapixels for images (about 4000 × 4000) and 3.7 megapixels for video (about 2560 × 1440). A large source at a high scale comes out smaller than the scale says.

These limits matter. Past what fits in VRAM, a run slows from seconds to many minutes, and it can't be stopped: the SeedVR2 node only checks for Stop or ComfyUI's interrupt between batches. A 6144 × 6144 image was still in its only batch after 18 minutes. If you raise a limit and this happens, restarting ComfyUI is the only way out.

**What to expect on a 32 GB card:**

- A 1024 × 1024 image at 4× takes about 35 seconds.
- A 5-second 720p video at 2× takes about 7 minutes. VRAM use reached 31.4 GB at the size limit, so close other GPU apps first.
- The video workflow keeps the source's sound and frame rate.
- Images are saved as PNG, and the SeedVR2 workflow drops transparency.
- Very noisy sources keep some of their colour noise.
- Video length is limited by the system memory holding the decoded frames, not by VRAM.

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
