# Future Ideas

A reservoir of feature ideas for leveraging Qwen3-VL (multimodal) and a
potential Qwen3 (text-only) companion model in metascan. Captured from
brainstorming on 2026-05-03. Items have stable IDs (TA-, TB-, CC-, FN-,
CI-, REC-) so future discussions can reference them by label.

This is an idea reservoir, not a roadmap. Inclusion here implies the
idea is worth remembering, not committed to delivery.

---

## Track A — AI-Image Enthusiasts

Features primarily aimed at users who generate images with diffusion
models (SDXL, Flux, Pony, etc.) and curate the outputs.

- **TA-1: Prompt cross-translation (text-only Qwen3).** Take a prompt
  written for one model family (e.g., Flux natural-language) and
  rewrite it for SDXL, Pony/Illustrious (booru tags), NovelAI, or MJ
  syntax. Each platform has different weight notation, negative-prompt
  conventions, and tag dialects. Lets users reuse a winning prompt
  across model families without manual rewrites.
- **TA-2: Reverse-prompt comparison.** For an image that already has
  its prompt embedded, ask Qwen3-VL to write a fresh prompt from
  scratch. Diff the two. Reveals which terms in the original were
  load-bearing vs noise — useful for prompt pruning.
- **TA-3: Negative-prompt mining.** Given a folder of "good" gens and
  a folder of "bad" gens of the same concept, the VLM contrasts them
  and synthesizes a negative prompt that captures the failure modes.
  Beats hand-curated negatives because it's grounded in *the user's*
  failures, not generic boilerplate.
- **TA-4: Prompt-vs-image divergence detector.** For each AI-gen with
  an embedded prompt, the VLM compares what was asked for vs what
  landed. Surfaces "asked for red dress → got blue", "asked for 2
  people → got 3". Drives a smart folder of "model didn't listen"
  rejects and per-model fidelity scoring.
- **TA-5: "Why did this fail?"** User flags a bad output (extra
  fingers, wrong pose). VLM identifies the failure mode in plain
  language and suggests prompt / negative / embedding tweaks.
  One-click "regenerate with fix" if wired to a generation backend.
- **TA-6: Aesthetic scoring rubric.** Score each image 1-10 on
  composition, anatomy, technical quality, and prompt adherence (when
  prompt is known). Drives smart folders ("rating > 8") and one-click
  batch culling of large generation runs down to keepers.
- **TA-7: LoRA suggestion from concept.** VLM identifies "rare /
  specific" concepts in an image (a particular character, a niche
  art style), cross-references the user's installed LoRA folder,
  suggests which to use. Closes the loop between "I saw this great
  image" and "what model stack made it."
- **TA-8: Style-axis extractor.** "I love the lighting / palette of
  this image — give me a prompt that captures that style but with a
  different subject." VLM separates style from content and re-targets.
- **TA-9: Series detection across a generation batch.** pHash can't
  see "same character in different pose." VLM clusters a batch by
  character identity, outfit, scene context. Useful for character-LoRA
  training-set curation.
- **TA-10: Prompt Playground** Select an image and open a window 
  rewrite or generate a prompt, target a specific model or architecture, 
  change the style, use prompts to influence the prompt. Save the prompt
  in the db, each image can have 0..n generated prompts. Display the 
  prompt in the Details panel. With each saved prompt save model and 
  architeture. Can target t2i, or t2v with the addition of structured 
  action prompts.
- **TA-11: Create a LoRA dataset** From a folder create a LoRA 
  training dataset. Caption/tag the files using the selected style
  copy the image files and associated caption files to a target
  directory.

---

## Track B — Photo-Library Users

Features primarily aimed at users with real-world photo libraries
(family, travel, hobby photography).

- **TB-1: "What is this?" identification on demand.** Right-click →
  "what is this butterfly / building / plant / car." Fast on-device
  replacement for opening Google Lens.
- **TB-2: Auto-album by event/scene.** Cluster a day's photos into
  "morning at the cafe / hike / dinner" using scene + subject + EXIF
  time. Beats pure date-bucket albums; uses the existing folders
  system as the persistence layer.
- **TB-3: Family / pet grouping (without face recognition).** Avoids
  the legal/UX baggage of true face recognition. VLM describes
  people/animals generically ("woman with dark hair, blue jacket";
  "small white dog"). User confirms once per identity, the description
  becomes a smart-folder rule that groups across the library.
  Privacy-friendly version of Apple's People album.
- **TB-4: Burst-mode keeper picker.** For any cluster of photos taken
  < 5s apart, VLM ranks by sharpness + composition + eyes-open and
  suggests one keeper. Most photo libraries have hundreds of
  near-duplicates from burst mode that nobody ever culls.
- **TB-5: Smart EXIF inference for scanned/legacy photos.** When EXIF
  is missing or the photo was scanned, VLM guesses approximate
  location ("coastal Pacific Northwest"), era ("late 1990s based on
  hairstyles/cars"), season, time of day. Bulk-applies to digitized
  print collections.
- **TB-6: Travel timeline narrative.** GPS + EXIF + VLM scene
  description → auto-generated travel-blog text, day by day. "Day 3:
  crossed into Spain; afternoon at Sant Sebastián's old town; dinner
  near the harbor." Pairs naturally with the existing map view.
- **TB-7: Document/receipt/screenshot triage.** Photo libraries
  always have hundreds of these mixed in. VLM auto-tags non-photo
  media; optionally OCRs text. One smart folder absorbs the noise out
  of real-photo browsing.
- **TB-8: Auto-redaction candidates.** For photos about to be shared
  publicly, VLM finds license plates, faces of strangers, addresses
  on packages, screen contents. Outputs bounding boxes ready for
  one-click blur.
- **TB-9: Anniversary recap with VLM commentary.** "On this date 5
  years ago" but with a written summary, not just thumbnails. "You
  spent the afternoon at the botanical garden; standout: the orchid
  macro you posted to IG."

---

## Cross-cutting

Features that serve both audiences. Included so the Recommendations
section below can reference them. Drop this section if scope creep is
a concern.

- **CC-1: Natural-language smart folders (text-only Qwen3).** "All my
  dog photos from last summer except the blurry ones" or "all SDXL
  hand-fix experiments with CFG > 5." Qwen3 with structured-output
  JSON → the existing `SmartRules` schema in `stores/folders.ts`.
  Replaces the rule-builder UI for power users; rule-builder stays for
  visual editing.
- **CC-2: Conversational library chat.** Sidebar where the user chats
  with their library. VLM grounds image questions ("show me photos
  similar to this but more colorful"); text Qwen handles metadata
  aggregation queries ("most-used negative prompt in this folder?",
  "summarize Vacation 2024"). Multi-turn search refinement that CLIP
  alone can't do.
- **CC-3: Library audit / "Wrapped" report.** Once a month: VLM
  samples N% of new media, produces a summary — themes, dominant
  styles, quality trends, gaps. AI-gen users see "you've been heavy
  on portraits, light on landscapes; CFG drift over time"; photo
  users see "your color palette shifted toward warm tones this fall."
- **CC-4: Duplicate-cluster keeper picker.** The existing pHash
  pipeline finds clusters; VLM picks the strongest one (sharpness +
  composition for photos; prompt adherence + technical quality for
  AI-gen). Makes the existing duplicate finder a one-click "keep
  best, archive rest."
- **CC-5: "Tell me about this image" panel.** Right-click action that
  produces a single info panel: subject, composition, mood, technical
  notes, suggested related searches. One feature, very high perceived
  value, costs nothing extra to build alongside the existing metadata
  panel.
- **CC-6: Content-aware crop generator.** VLM identifies safe crop
  boxes for 1:1, 16:9, 4:5, 9:16 without lopping off subjects.
  One-click batch export for IG / wallpaper / story aspect ratios.

---

## ComfyUI Integration — Foundation Nodes

Custom ComfyUI nodes for the v1 integration. These assume the
"metascan as API gateway" architecture: nodes are thin clients that
call new `/api/comfy/*` endpoints. Metascan handles DB lookup, prompt
extraction, llama-server routing, and (when needed) prompt rewriting
via the active VLM running text-only.

- **FN-1: `MetascanImage`.** Tag/aspect query → IMAGE tensor + WIDTH
  + HEIGHT + STRING (prompt) + STRING (negative). Aspect bucketing
  snaps to model-recommended dims (SDXL: 1024×1024 / 832×1216 /
  1216×832; Flux: any /16; etc.) via a `model: SDXL|Flux|Pony|SD1.5|
  HiDream` dropdown that drives the bucket list.
- **FN-2: `MetascanPromptPick`.** Same query but no image output —
  just the prompt + negative + sampler/CFG/steps/seed if extracted.
  Used when the goal is to remix a prompt against fresh weights.
- **FN-3: `MetascanPromptRewrite`.** String in, string out. Targets:
  SDXL natural-language, Pony booru tags, Flux natural-language, MJ
  syntax, NovelAI. Strength dial. Optional preserve-list (terms the
  user does not want changed). Runs against the active VLM as a
  text-only chat completion — no model swap.
- **FN-4: `MetascanImageToPrompt`.** Image in, prompt out. Reuses the
  planned image-to-prompt feature. Pairs naturally with i2i workflows:
  pick a reference image, derive the prompt from *it* rather than
  re-typing.

---

## ComfyUI Integration — Clever Ideas

Higher-leverage nodes that build on the foundation. Most depend on
FN-1..FN-4 being in place.

- **CI-1: `MetascanStyleMatch`.** Image in, *style-only* prompt out
  (no subject — just lighting, palette, medium, composition).
  Enables "subject of prompt A in style of image B" without IPAdapter.
  Solves the "I love this look but want a different scene" loop.
- **CI-2: `MetascanNegativeFromCluster`.** Point at a metascan smart
  folder of "bad" outputs of a concept; receive a synthesized negative
  prompt grounded in the user's failure modes. (Node-side surface for
  TA-3.)
- **CI-3: `MetascanRandomFromTag`.** For batch / wildcard workflows:
  pulls a random image from a tag bucket each queue item. Drives
  variation pipelines without manual prompt curation.
- **CI-4: `MetascanLoopback`** (output side). Wired after `SaveImage`.
  Pushes the just-saved file + workflow JSON + final prompt back into
  metascan so it gets tagged, indexed, and discoverable, with a
  "generated by workflow X" backreference. Closes the
  create → review → reuse loop and makes metascan the system-of-record
  for AI-gen workflow.
- **CI-5: `MetascanI2VPromptHelper`.** Image + target video model
  (Wan / HunyuanVideo / CogVideoX / AnimateDiff) → motion prompt
  phrased for that model's training distribution. i2v models care a
  lot about motion-cue phrasing and each was trained on different
  caption styles.
- **CI-6: `MetascanI2VPromptPicker`.** Image + Prompt generated from
  prompt playground (TA-10). 

---

## Recommendations

Combining the general feature-priority picks (REC-1..REC-4) with the
ComfyUI v1 scope picks (REC-5..REC-7). Each recommendation links to
the IDs above.

### General feature priorities (impact-to-effort)

- **REC-1: Natural-language smart folders** — see CC-1. The
  lowest-effort highest-leverage win. The rule schema already exists;
  structured-output JSON from a small Qwen3 is well-trodden territory.
  Serves both audiences; differentiating feature relative to nearly
  everything else in this category.
- **REC-2: Library Wrapped / audit** — see CC-3. Generates "wow"
  moments, drives engagement, and doubles as the test bed for "does
  Qwen-VL add real signal beyond CLIP tags." Both audiences see value
  immediately.
- **REC-3: Cluster keeper-pickers** — TB-4 (burst-mode photo) and
  CC-4 (general duplicate-cluster). Same underlying primitive (rank a
  cluster, pick the best), two distinct value props, and the kind of
  feature that makes someone say "I deleted 4000 photos in 20
  minutes."
- **REC-4: Reverse-prompt comparison** — see TA-2. Small, novel, and
  AI-gen folks will absolutely talk about it. Cheap to ship.

### ComfyUI v1 scope

- **REC-5: Endpoints + FN-1 + FN-2** — the foundation, no LLM
  involvement, immediate value for i2i/i2v workflows.
- **REC-6: FN-3 (`MetascanPromptRewrite`)** — proves the
  "text-only on the active VLM" hypothesis end-to-end and unlocks
  cross-model translation (TA-1) inside ComfyUI naturally.
- **REC-7: CI-4 (`MetascanLoopback`)** — closes the loop and makes
  metascan the system-of-record for AI-gen workflow.

Skip the embedded thumbnail-grid widget for v1 — text-tag-query first,
browse-popup second.
