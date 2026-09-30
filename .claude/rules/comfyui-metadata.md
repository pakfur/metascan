---
paths:
  - "metascan/extractors/**"
  - "tests/test_comfyui_extractor.py"
  - "tests/test_comfyui_video_extractor.py"
  - "test_extractor_harness.py"
  - "test_comfyui_video_extraction.py"
---

# Reading generation metadata out of ComfyUI files

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

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
