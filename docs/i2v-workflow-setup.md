[← Back to README](../README.md)

# Image-to-Video Workflow Setup (MiniMax H3)

The Image to Video feature drives your own ComfyUI install. Before it can
generate anything you register **two** ComfyUI workflows with metascan and
assign them to the two quality slots the dialog offers:

| Slot | What it's for | Typical build |
|------|---------------|---------------|
| **Fast (turbo)** | Quick previews, iterating on a prompt | Base H3 model + a 4-step turbo LoRA, ~4 sampler steps |
| **High quality** | The keeper render | Same graph, no turbo LoRA, ~20 sampler steps |

Both are the same graph shape. Build one, get it working, then clone it and
change the two things that differ. This guide walks through the full build.

---

## 1. Prerequisites

**Models.** MiniMax H3 image-to-video needs four files in your ComfyUI
model directories. The exact filenames depend on which quantisation you
downloaded; these are the reference set:

| Kind | Directory | File |
|------|-----------|------|
| Diffusion model | `models/diffusion_models/` | `minimax_h3_fl2va_pruned_bf16.safetensors` |
| Text encoder | `models/text_encoders/` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video VAE | `models/vae/` | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | `models/vae/` | `minimax_h3_audio_vae_fp32.safetensors` |

The `fl2va` model is the right one for image-to-video: it accepts a first
frame (and optionally a last frame, which this flow does not use). Do not
use `ref2va` — that is the reference-image model the storyboard feature
drives, and it takes no first frame.

For the **Fast** preset you also want a 4-step turbo LoRA in
`models/loras/`, e.g. `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors`.

**Custom nodes.** Two beyond ComfyUI core:

- **[rgthree-comfy](https://github.com/rgthree/rgthree-comfy)** — for
  *Power Lora Loader*, which backs the dialog's LoRA list.
- **[comfyui-various](https://github.com/jamesWalker55/comfyui-various)** —
  for `JWStringMultiline`. See the warning in step 3 about why ComfyUI's
  built-in string node will not work here.

---

## 2. The `MS_*` title contract

Metascan drives ComfyUI by **node title**, not node id — ComfyUI renumbers
ids whenever a graph is re-saved, but titles survive. You rename nodes in
ComfyUI (right-click a node → *Title*, or double-click its titlebar) to the
`MS_*` names below, and metascan overwrites those nodes' widgets at
submit time.

For an image-to-video preset (kind `ref2v`, tagged `minimax` / `i2va`):

| Title | Required? | Put it on | Metascan writes |
|-------|-----------|-----------|-----------------|
| `MS_POSITIVE` | **yes** | `JWStringMultiline` | `text` ← the compiled I2VA prompt |
| `MS_SEED` | **yes** | `RandomNoise` | `noise_seed` ← the dialog's seed |
| `MS_SAVE` | **yes** | `SaveVideo` | nothing — it just needs to find the output |
| `MS_FIRST_FRAME` | **yes** | `LoadImage` | `image` ← the selected library image |
| `MS_RESOLUTION` | warns if absent | `MiniMaxH3ImageToVideo` | `width` / `height` ← computed dimensions |
| `MS_DURATION` | warns if absent | `PrimitiveFloat` | `value` ← the dialog's duration in seconds |
| `MS_LORA_STACK` | warns if absent | `Power Lora Loader (rgthree)` | the dialog's LoRA list |

Missing one of the three "warns if absent" titles is not fatal — registration
succeeds with a warning and the graph runs with whatever is baked into it.
You just lose that control from the dialog.

Leave `MS_LAST_FRAME`, `MS_REF_IMAGE*` and `MS_AUDIO*` off entirely. The
i2va validator flags them as `slot_unused` because this flow never fills
them.

---

## 3. Build the graph

Start from ComfyUI's MiniMax H3 image-to-video template, or wire it by hand.
The shape is:

```
LoadImage [MS_FIRST_FRAME] ─────────────────┐
                                            ↓
JWStringMultiline [MS_POSITIVE] ──prompt→ MiniMaxH3ImageToVideo [MS_RESOLUTION]
UNETLoader → Power Lora Loader [MS_LORA_STACK] ─┐   │ (width, height, length)
CLIPLoader ─────────────────────────clip────────┼───┤
VAELoader (video) ──────────────────vae─────────┘   │
                                                    ├─ cond ─→ BasicGuider ─┐
PrimitiveFloat [MS_DURATION] → Math Expression ─────┘                       │
                                                                            ↓
RandomNoise [MS_SEED] ──────────────────────────────→ SamplerCustomAdvanced
KSamplerSelect ─────────────────────────────────────→        │
BasicScheduler ─────────────────────────────────────→        ↓
                                        VAEDecode → CreateVideo → SaveVideo [MS_SAVE]
                                   VAEDecodeAudio ────┘
```

Three parts of this are easy to get wrong.

### `MS_RESOLUTION` goes on the H3 node, not on `ResolutionSelector`

This is the one most people get backwards. A typical H3 template has a
**`ResolutionSelector`** node feeding width and height into the sampler
node, so that looks like the obvious place for the title. It is not.

`ResolutionSelector` has no `width` or `height` widgets at all — its inputs
are `aspect_ratio`, `megapixels` and `multiple`, and it *outputs* a width
and a height. Titling it `MS_RESOLUTION` fails registration with a missing-
widget error.

Put `MS_RESOLUTION` on the **`MiniMaxH3ImageToVideo`** node, which has real
`width` and `height` inputs. Metascan replaces the incoming links with
literal values, which cleanly overrides whatever `ResolutionSelector` was
feeding it. You can leave that node connected and unused, or delete it.

### `MS_POSITIVE` needs `JWStringMultiline` specifically

`MiniMaxH3ImageToVideo` has its own multiline `prompt` widget, so it is
tempting to skip the separate text node. Two reasons you can't:

1. A node carries **one** title, and this one has to be `MS_RESOLUTION`.
2. `MS_POSITIVE` requires a widget literally named `text`.

That second point rules out ComfyUI's built-in string node too:
`PrimitiveStringMultiline` exposes its text as `value`, not `text`, so
registration rejects it. Use `JWStringMultiline` from comfyui-various,
which does expose `text`, and wire its output into the H3 node's `prompt`
input.

### Duration is seconds → frames, through a math node

`MS_DURATION` carries **seconds** (the dialog offers 6 / 10 / 15 / 20), but
`MiniMaxH3ImageToVideo.length` wants a **frame count** on H3's 17k+5 grid.
Bridge them with a `ComfyMathExpression` node:

```
max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
```

with `values.a` linked to the `PrimitiveFloat` titled `MS_DURATION`, and its
output linked to `length`. At 24 fps a 6-second request becomes 158 frames.

H3's trained range is roughly 124–362 frames (~5–15 s); the 20 s option runs
past that and results get less predictable.

---

## 4. Export the workflow

Metascan needs **API format**, not the regular workflow save.

In ComfyUI: gear icon → enable **Dev Mode**, then
*Workflow → Export (API)*. That writes a JSON keyed by node id, with a
`class_type` and `_meta.title` per node. If your file has a `nodes` array
and a `links` array instead, it's the UI format.

Metascan rejects the UI format, but the symptom is misleading: you get a
list of *"Missing required node title MS_POSITIVE / MS_SEED / MS_SAVE"*
errors, because it can't find any node titles in a structure shaped that
way. If you know your graph is titled correctly and validation claims every
title is missing, you exported the wrong format.

---

## 5. Register it in metascan

1. Open **Configuration → Video**.
2. Click **Register workflow…**.
3. Fill in:
   - **Name** — e.g. `H3 i2v turbo` or `H3 i2v quality`
   - **Video model** — `MiniMax H3`
   - **Generation mode** — `i2va` (pre-selected from this tab)
   - **Workflow JSON** — paste the exported file
4. Click **Validate**. Fix anything reported as an error; warnings are fine
   to accept. If the only problem is a misspelled `MS_*` title, the dialog
   offers **Apply fixes** to rename it for you.
5. Save.

You can run the same check from the command line before you ever open the
dialog, which is faster to iterate against:

```bash
python scripts/validate_i2v_workflow.py path/to/exported_api.json
```

It exits non-zero on anything that would block registration, prints warnings
without failing, and lists which node each `MS_*` title resolved to.

Then, still in Configuration → Video, set the **Fast (turbo)** and **High
quality** dropdowns to the two presets, and hit **Save**. Until both slots
point at something, the dialog's Generate button returns an error naming the
empty slot.

The same tab also sets the duration choices, the megapixel ladder, and which
defaults the dialog opens with.

---

## 6. Building the two variants

Get one graph validating, then make the second from it.

**High quality:** base `minimax_h3_fl2va_pruned_bf16` model, `BasicScheduler`
steps at **20**, no turbo LoRA in the Power Lora Loader.

**Fast (turbo):** same graph, `BasicScheduler` steps at **4**, and
`minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors` loaded at
strength 1.0. Two ways to load it, and the difference matters:

- **Bake it into the graph** — add a separate `LoraLoaderModelOnly` in front
  of the Power Lora Loader. Metascan never touches it, so it is always on.
  This is what you want for the turbo LoRA.
- **Leave it to the dialog** — anything in the node titled `MS_LORA_STACK` is
  *replaced* on every submit with whatever the dialog's LoRA list holds.
  Metascan owns that node completely, so a turbo LoRA placed there is wiped
  the moment a user picks any style LoRA.

Register the turbo graph as its own preset and assign it to the Fast slot.

---

## 7. Output size

You never set an output resolution in the workflow — metascan computes it
per generation and writes it into `MS_RESOLUTION`.

The user picks only a **megapixel budget** in the dialog (0.25 / 0.5 / 0.75 /
1.0 by default). Orientation and aspect ratio are **not** adjustable, by
design: the selected image is the first frame, so any output ratio other
than the source's would letterbox or crop it. A portrait source gives a
portrait clip automatically.

Both edges land on a multiple of 32, which is what H3's `width` / `height`
widgets require. A 1920×1080 source at 0.75 MP becomes 1152×640.

If you use the 768p-tuned turbo LoRA, note that **1.0 MP** is the budget
closest to its training resolution (16:9 at 1.0 MP is 1312×736; 768p 16:9 is
1366×768). The shipped default is 0.75 MP, which is slightly under — consider
raising `default_megapixels` in Configuration → Video if turbo output looks
soft.

If a preset has no `MS_RESOLUTION` node, nothing breaks — the graph keeps
whatever resolution is baked into it, and the dialog's size selector simply
has no effect on that preset.

---

## 8. Validation messages

| Code | Level | Meaning |
|------|-------|---------|
| `no_first_frame` | **error** | No `MS_FIRST_FRAME`. The flow drives the selected image in as the first frame; without it the preset cannot run. |
| `no_resolution` | warning | No `MS_RESOLUTION`. Output size won't follow the dialog, and the clip may not match the source's aspect ratio. |
| `no_duration` | warning | No `MS_DURATION`. Clip length won't follow the dialog. |
| `no_lora_stack` | warning | No `MS_LORA_STACK`. The dialog's LoRA list can't be applied. |
| `slot_unused` | warning | A title this flow never fills (`MS_LAST_FRAME`, `MS_REF_IMAGE*`, `MS_AUDIO*`). Harmless. |
| `unknown_title` | warning | A node titled `MS_something` that isn't in the contract — usually a typo. |

---

## 9. Troubleshooting

**"is kind 'ref' / 't2i', not a video (ref2v) workflow"** — the preset was
registered under the wrong kind. Re-register it from Configuration → Video,
which registers `ref2v`.

**"is tagged minimax/ref2va; the i2v flow needs minimax/i2va"** — you picked
the wrong Generation mode. Re-register with `i2va`.

**Registration rejects a missing `width` widget** — you titled
`ResolutionSelector` as `MS_RESOLUTION`. Move the title to the
`MiniMaxH3ImageToVideo` node (see step 3).

**Registration rejects a missing `text` widget** — your `MS_POSITIVE` node is
`PrimitiveStringMultiline` or similar. Swap it for `JWStringMultiline`.

**Video generates but ignores the duration** — no `MS_DURATION`, or it's on
the math node instead of the `PrimitiveFloat` feeding it.

**Video generates at the wrong size** — no `MS_RESOLUTION`, or it's on
`ResolutionSelector`.

**Clips don't appear in the strip** — check that `MS_SAVE` is on the
`SaveVideo` node. Metascan fetches outputs over HTTP from the node it
resolves from that title.

---

## See also

- [Configuration](configuration.md) — the `i2v` section of `config.json`
- [API Reference](api-reference.md#image-to-video-apii2v) — `/api/i2v/*` endpoints
- [Features](features.md#image-to-video) — what the dialog does
