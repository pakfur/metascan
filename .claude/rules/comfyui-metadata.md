---
paths:
  - "metascan/extractors/**"
  - "metascan/utils/png_chunks.py"
  - "metascan/core/scanner.py"
  - "tests/test_png_chunks.py"
  - "tests/test_comfyui_extractor.py"
  - "tests/test_comfyui_video_extractor.py"
  - "test_extractor_harness.py"
  - "test_comfyui_video_extraction.py"
---

# Reading generation metadata out of ComfyUI files

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **The metadata extractors decode a PNG only when it has text or EXIF after
  its image data: read its text and EXIF through
  `metascan/utils/png_chunks.py`, never `img.text` / `img.getexif()`.**
  Pillow decodes the whole image for either (`PngImageFile.text` /
  `getexif()` call `load()` to look for chunks after the image data);
  `_get_exif_metadata` did that three times per import (the Fooocus check,
  the ComfyUI check and the ComfyUI extract), about 65 ms per 1 MP image and
  260 ms per 4 MP. `png_text(img)` returns the text Pillow parsed at open
  (`PngStream.im_text`, which `img.text` is built from: `img.info` loses a
  tEXt "exif" and a text key a later chunk of another kind reuses) and
  `png_exif(img)` the EXIF before the image data (Pillow's
  format-independent `Image.Image.getexif`). Both fall back to the decoding
  reads when `metadata_after_data(img.filename)` finds a tEXt / zTXt / iTXt
  / eXIf after the first IDAT (a walk over the chunk headers, remembered per
  file version: path, size, mtime, ctime and inode; a failed read is not
  remembered), for an image not opened from a path, and if this Pillow keeps
  the parsed text elsewhere. **The scanner's one decode stays**:
  `Scanner._read_image_info_and_exif`'s `getexif()` decodes a PNG with no
  eXIf before its image data, and that decode alone decides whether such a
  PNG imports (one with an early eXIf is not decoded, and imports when its
  pixels are damaged, as before). Don't replace it with a cheaper check:
  none agrees with the decoder (Pillow never checks an IDAT chunk's CRC and
  stops once every row is out, while a stream can keep every CRC and still
  not decode), and `test_a_png_imports_exactly_when_pillow_can_decode_it`
  pins it. `tests/test_png_chunks.py` counts Pillow's decodes.
- **Video metadata is read from the API-format `prompt` container tag, and
  the graph shape varies by model.** `ComfyUIVideoExtractor`
  (`metascan/extractors/comfyui_video.py`) dispatches handlers on
  `class_type`, so a new topology silently yields nothing. MiniMax H3 uses
  the split-sampler layout — seed on `RandomNoise.noise_seed`,
  scheduler/steps on `BasicScheduler`, sampler name on `KSamplerSelect`,
  fps on `CreateVideo` — and feeds its prompt from a plain string node into
  `MiniMaxH3*.prompt` (no `CLIPTextEncode`). Three graph-aware passes run
  after the handlers: `_apply_ms_titles` reads back `MS_POSITIVE` /
  `MS_NEGATIVE` / `MS_SEED` / `MS_STEPS` and is authoritative for
  metascan-generated clips; `_resolve_linked_prompt` follows a video node's
  `prompt` link for hand-run graphs; `_fill_from_container` ffprobes frame
  rate / frame count / duration only when the graph left one unset (H3's
  `length` is a link into a math node, and no node carries duration). A
  list-valued input is a link, never a number — guard with `isinstance`
  before `int()`. The `"RES4LYF"` sampler relabel excludes ComfyUI's core
  `res_multistep*` family. The extractor→scanner key for frame count is
  **`video_length`** (`base.py`'s documented contract); the scanner once
  read `"length"`, which left `media.video_length` NULL for every video.
  The SaveVideo tag carries only `prompt` (no `workflow`) for API-submitted
  jobs. Rows scanned before a fix keep their old values until rescanned.
- **In an API-format graph any widget can be a `[node_id, output_index]`
  link instead of a literal — never call a str/number method on an input
  without resolving it first.** `ComfyUIExtractor._resolve_input`
  (`metascan/extractors/comfyui.py`, still images) follows a link back to
  its literal; a wired `CLIPTextEncode.text` once raised `'list' object has
  no attribute 'lower'`, and because `extract()`'s blanket `except` returns
  `None`, the file lost ALL its metadata, not just the prompt. A literal on
  the immediate source wins over walking upstream: `ShowText|pysssss`'s
  `text_0` is the expanded prompt, while the `DPRandomGenerator` feeding it
  still holds the unexpanded wildcard template. Positive/negative polarity
  comes from the node title when it says so; the keyword heuristic is only
  the untitled fallback — a bare type guard that merely skipped the linked
  positive would store a keyword-free negative as the prompt.
- **Image models and LoRAs are traced, not listed.**
  `ComfyUIExtractor._models_and_loras` walks each `KSampler`'s `model`
  wire upstream and reports only what it reaches: a multi-mode workflow
  keeps several loaders behind a switch (a real Qwen graph has a
  generation and an edit `UNETLoader` behind one `ImpactSwitch`), and the
  loaders on unselected branches never ran — never go back to "every
  `UNETLoader` in the graph". `_upstream_model_links` resolves a switch's
  `select` through `_resolve_input` and follows only `input<N>`
  (ImpactSwitch) / `input_<N>` (Dream), both 1-based; an unresolvable
  `select` reports every branch rather than none. `_node_loras` reads a
  `lora_name` widget or the enabled `lora_N` rows of `Power Lora Loader
  (rgthree)`; LoRAs come back in application order (loader → sampler). A
  shared `seen` set dedupes across samplers and ends cycles. When no wire
  reaches a loader (a sampler class the extractor doesn't know) it falls
  back to listing everything, which is what the old flat scan did. The
  extractor now emits `models` (list), not `model` (str); the scanner
  accepts either. Model names stay verbatim — extension and subfolder
  included — because that is how this extractor always reported
  checkpoints, so existing model filter values stay valid (the *video*
  extractor strips the extension; the two are inconsistent on purpose
  until someone migrates stored values).
