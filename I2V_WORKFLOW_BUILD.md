# Build Brief: MiniMax H3 image-to-video workflows

**For a Claude Code CLI session with the ComfyUI MCP attached.**

Your job is to produce **two working ComfyUI workflows** in API format, save
them into this repo, and prove both actually render a video. When you're
done, a human registers them in metascan's Configuration → Video tab.

Read this whole file before starting. Background on why the contract looks
the way it does is in [docs/i2v-workflow-setup.md](docs/i2v-workflow-setup.md);
this file is the executable version.

---

## 0. Inputs you need from the human

| Thing | Value |
|-------|-------|
| ComfyUI base URL | `http://127.0.0.1:8188` (confirm it responds before building) |
| Validation image | **← the human pastes an absolute path here before you start** |

If the validation image path is still blank, stop and ask for it. Do not
substitute your own test image: the point is to check a real library file
with a real aspect ratio end to end.

---

## 1. Deliverables

```
data/workflows/minimax_i2va_turbo_api.json     # Fast preset
data/workflows/minimax_i2va_quality_api.json   # High quality preset
```

Both must:

- be **API format** (a flat object keyed by node id, each value having
  `class_type`, `inputs`, `_meta.title`) — not the UI format with `nodes`
  and `links` arrays;
- pass `python scripts/validate_i2v_workflow.py <file>` with exit code 0 and
  **no unbound optional slots**;
- actually produce a video file when submitted to ComfyUI.

---

## 2. Models

Verified present on this machine. Use these exact strings.

| Role | Node | Value |
|------|------|-------|
| Diffusion model | `UNETLoader.unet_name` | `minmax\minimax_h3_fl2va_pruned_bf16.safetensors` |
| Text encoder | `CLIPLoader.clip_name` | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` (type `minimax`) |
| Video VAE | `VAELoader.vae_name` | `minimax_h3_video_vae_fp16.safetensors` |
| Audio VAE | `VAELoader.vae_name` | `minimax_h3_audio_vae_fp32.safetensors` |
| Turbo LoRA (Fast only) | `LoraLoaderModelOnly.lora_name` | `minimax\minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors` |

Note the `minmax` folder spelling (not `minimax`) for the diffusion model, and
the backslashes — these are Windows paths and ComfyUI wants them verbatim.

The bf16 diffusion model is 37.5 GB against ~34 GB of VRAM. If it OOMs,
switch to `minmax\minimax_h3_fl2va_pruned_fp8_scaled.safetensors` and say so
in your report rather than silently changing models.

**Do not** use `ref2va` models — those are for a different feature and take
no first frame.

---

## 3. The `MS_*` title contract

metascan binds to node **titles**, not ids (ComfyUI renumbers ids on save).
Set `_meta.title` on exactly these nodes:

| Title | Node class | Why |
|-------|-----------|-----|
| `MS_POSITIVE` | `JWStringMultiline` | receives the compiled prompt |
| `MS_SEED` | `RandomNoise` | receives the seed |
| `MS_SAVE` | `SaveVideo` | where outputs are collected from |
| `MS_FIRST_FRAME` | `LoadImage` | receives the source image |
| `MS_RESOLUTION` | `MiniMaxH3ImageToVideo` | receives computed width/height |
| `MS_DURATION` | `PrimitiveFloat` | receives duration in seconds |
| `MS_LORA_STACK` | `Power Lora Loader (rgthree)` | receives the user's LoRA list |
| `MS_STEPS` | `BasicScheduler` — **quality build only** | receives the dialog's Steps choice (20–40) |

Leave every other node untitled or with its default title. Do **not** add
`MS_LAST_FRAME`, `MS_REF_IMAGE*` or `MS_AUDIO*` — this flow never fills them
and they produce `slot_unused` warnings.

### Three traps — read these, they are the whole difficulty

**1. `MS_RESOLUTION` goes on `MiniMaxH3ImageToVideo`, not `ResolutionSelector`.**
`ResolutionSelector` looks like the size node but has no `width`/`height`
widgets at all — only `aspect_ratio`, `megapixels`, `multiple`. Titling it
`MS_RESOLUTION` fails validation. `MiniMaxH3ImageToVideo` has real `width`
and `height` inputs; metascan overwrites the incoming links with literals.
You can delete `ResolutionSelector` entirely.

**2. `MS_POSITIVE` must be `JWStringMultiline`.** The contract requires a
widget literally named `text`. ComfyUI's built-in `PrimitiveStringMultiline`
exposes its text as `value` and will be rejected. `MiniMaxH3ImageToVideo` has
its own `prompt` widget, but it can't be used — a node carries one title and
this one has to be `MS_RESOLUTION`. Wire `JWStringMultiline` → the H3 node's
`prompt` input.

**3. Duration is seconds, `length` is frames.** `MS_DURATION` carries seconds.
`MiniMaxH3ImageToVideo.length` wants a frame count on H3's 17k+5 grid. Bridge
with `ComfyMathExpression`:

```
max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
```

`values.a` ← the `PrimitiveFloat` titled `MS_DURATION`; output → `length`.

---

## 4. Graph shape

```
LoadImage [MS_FIRST_FRAME] ──────first_frame──┐
JWStringMultiline [MS_POSITIVE] ──prompt──────┤
CLIPLoader ───────────────────────clip────────┤
VAELoader (video) ────────────────vae─────────┤
PrimitiveFloat [MS_DURATION]                  ├─→ MiniMaxH3ImageToVideo [MS_RESOLUTION]
        └→ ComfyMathExpression ──length───────┘        │ out 0: conditioning
                                                       │ out 1: latent
UNETLoader → [LoraLoaderModelOnly]* → Power Lora Loader [MS_LORA_STACK] ─┐
                                                                         ├→ BasicGuider ←conditioning
                                                                         └→ BasicScheduler
RandomNoise [MS_SEED] ┐
KSamplerSelect        ├──→ SamplerCustomAdvanced ←── latent
BasicScheduler        ┘              │
                                     ↓
                    VAEDecode ────────→ CreateVideo → SaveVideo [MS_SAVE]
                    VAEDecodeAudio ────┘
```

`*` `LoraLoaderModelOnly` exists only in the Fast build — see §5.

Reference values from a known-good graph: `KSamplerSelect.sampler_name =
res_multistep`, `BasicScheduler.scheduler = simple`, `BasicScheduler.denoise
= 1`, `CreateVideo.fps = 24`, `SaveVideo.format = auto`, `SaveVideo.codec =
auto`.

`ComfyUI-KJNodes`' `PathchSageAttentionKJ` between the loaders and the guider
is optional; include it if present, it speeds things up.

---

## 5. What differs between the two builds

Build one, validate it, then copy and change only these:

| | `minimax_i2va_quality_api.json` | `minimax_i2va_turbo_api.json` |
|---|---|---|
| `BasicScheduler.steps` | `25` | `4` |
| `BasicScheduler` title | `MS_STEPS` | untitled — never `MS_STEPS` |
| Turbo LoRA | none | `LoraLoaderModelOnly` at `strength_model` 1.0, between `UNETLoader` and `MS_LORA_STACK` |

**The turbo LoRA must be its own `LoraLoaderModelOnly` node — never an entry
inside `MS_LORA_STACK`.** metascan owns that node and replaces its contents on
every single submit, so a turbo LoRA parked there is wiped the moment a user
picks any style LoRA, and the Fast preset would silently start rendering
4-step garbage. Keep `MS_LORA_STACK` present but empty in both files.

Strength 1.0 exactly. These are step distillations, not style LoRAs.

---

## 6. Validate

Run the repo's own checker against each file. It uses metascan's real
validator, so a pass here means registration will succeed:

```bash
python scripts/validate_i2v_workflow.py data/workflows/minimax_i2va_turbo_api.json
python scripts/validate_i2v_workflow.py data/workflows/minimax_i2va_quality_api.json
```

Both must exit 0 and print `ok` against all seven shared `MS_*` titles. The
quality file must also print `ok` for `MS_STEPS`; the turbo file must print
`n/a` for it (a 4-step distillation has no business receiving 20–40 steps). "Optional
slots unbound" in the output means you missed a title — fix it, don't accept
it.

---

## 7. Prove they render

Static validation does not prove the graph runs. For **each** workflow:

1. Upload the human's validation image to ComfyUI.
2. Patch a copy of the workflow the way metascan would at submit time:
   - `MS_FIRST_FRAME.image` ← the uploaded filename
   - `MS_POSITIVE.text` ← any plausible I2VA prompt, e.g.
     *"For the target video, at 0.00 seconds into the target video, `<Picture 1>` (from [Shot 1]) is fully referenced. The camera holds a static shot as the subject turns slightly toward the lens."*
   - `MS_SEED.noise_seed` ← `12345`
   - `MS_DURATION.value` ← `6.0`
   - `MS_RESOLUTION.width` / `.height` ← computed, see below
3. Submit, wait for completion, confirm a video file came back.

Get the width and height from the repo rather than inventing them, so the
test exercises the real sizing path:

```bash
python -c "
from PIL import Image
from metascan.core.i2v_compiler import i2v_dims
im = Image.open('<VALIDATION_IMAGE>')
print(i2v_dims(im.width, im.height, 0.75))
"
```

**Acceptance:** both jobs complete without error, each produces a video whose
dimensions match what `i2v_dims` returned, and the turbo build is
substantially faster than the quality build (it's doing 4 steps against 20).
Record both wall-clock times.

If a render fails, fix the graph and re-run both checks. Do not hand back a
workflow that only passes static validation.

---

## 8. Report back

- Both file paths, and validator output for each.
- Wall-clock render time for each, and the output dimensions.
- The exact models you used, if you had to deviate from §2 (e.g. fp8 for VRAM).
- Anything you changed about the graph shape and why.
- Any node class in §4 that wasn't installed.

Do not register the presets in metascan yourself, and do not edit
`config.json` — the human does that step in the UI.
