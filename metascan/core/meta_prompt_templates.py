"""Qwen3-VL meta-prompts for image-to-prompt generation, per target model.

A successor to ``prompt_templates.py``. The legacy module composed short,
hand-tuned instructions; this one embeds the full meta-prompts from
``docs/meta-prompts.md`` verbatim — those have been field-tested against
each target image-generation model and produce notably better adherence
than the older terse builders.

Surface area (kept intentionally small):

* ``compose_generate_prompts(target, architecture, extras) -> (system, user)``
  builds the system / user pair that drives ``VlmClient.generate_text``.

* ``parse_output(target, raw)`` splits the model's response into
  ``(positive, negative)`` for targets that emit a ``Negative:`` block
  (sd / pony / chroma / qwen). For other targets ``negative`` is ``None``.

* ``EXTRA_OPTION_LABELS`` / ``MUTEX_PAIRS`` / ``TARGET_PRESETS`` mirror
  the same names exported by ``prompt_templates`` so the API and the
  frontend can swap import sources without restructuring callers.

Caption-length is intentionally absent: each meta-prompt embeds its own
sweet-spot range, and the user has chosen to defer per-target length
control until the meta-prompts prove insufficient.

Pure functions: no I/O, no model calls.
"""

from __future__ import annotations

import re
from typing import Final, Literal, NamedTuple


TargetModel = Literal["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
Architecture = Literal["t2i"]  # t2v / i2v / i2i deferred to v2

# Two-option vocabulary: the user can ask for explicit, anatomically
# correct content (``includeUncensored``); ask Qwen3 to keep output SFW
# (``includeSafety``); or set neither and let Qwen3 decide.
ExtraOption = Literal["includeUncensored", "includeSafety"]


# Targets whose meta-prompt instructs Qwen3 to emit a "Negative: …" block
# alongside the positive prompt. The rest produce a single block.
MODELS_WITH_NEGATIVE: Final[frozenset[TargetModel]] = frozenset(
    {"sd", "pony", "chroma", "qwen"}
)


_USER_INSTRUCTION: Final[str] = "Now analyze the attached image and produce the prompt."


# Mutually-exclusive option pairs. UI prevents both from being checked
# at once; the backend resolver also drops the second one if both arrive.
MUTEX_PAIRS: Final[tuple[tuple[ExtraOption, ExtraOption], ...]] = (
    ("includeSafety", "includeUncensored"),
)


# (short_label, full_instruction) per option. Short label is for the
# checkbox; full instruction is the tooltip text.
EXTRA_OPTION_LABELS: Final[dict[ExtraOption, tuple[str, str]]] = {
    "includeSafety": (
        "Keep SFW",
        (
            "Tell Qwen3 to keep the description SFW: no nudity, no sexual "
            "acts, no exposed genitalia. Mutually exclusive with "
            "Uncensored."
        ),
    ),
    "includeUncensored": (
        "Uncensored / Adult Detail",
        (
            "Tell Qwen3 to describe nudity, anatomy, and sexual acts using "
            "explicit, anatomically-correct vocabulary. Mutually exclusive "
            "with Keep SFW."
        ),
    ),
}


# --- Per-target meta-prompts ----------------------------------------------
#
# Each string is the meta-prompt body, lifted verbatim from
# ``docs/meta-prompts.md`` *minus* the trailing
# "Now analyze the attached image and produce the prompt." line — that
# is sent as the user message instead, keeping system/user roles clean.
#
# The "defensive add-ons" the doc author flagged as workarounds for
# Qwen3 8B drift are appended to the relevant prompts so the runtime
# benefits from the doc author's prior tuning.


_META_FLUX1: Final[
    str
] = """\
You are an expert Flux.1 prompt engineer. Analyze the provided image and output a single, cohesive natural-language prompt that would recreate it (or its style) using Flux.1.

# How Flux.1 prompts differ from SD/SDXL
- Flux understands natural prose, not comma-separated tags. Write flowing sentences, not keyword lists.
- It rewards specific, concrete language ("warm tungsten light spilling through venetian blinds" beats "cinematic lighting").
- Weight syntax like (word:1.3), BREAK, and negative prompts are NOT used — describe only what should be in the image.
- Sweet spot: 60-130 words. Specific enough to constrain the model, short enough to stay coherent.

# Cover these elements, woven into prose (roughly this order)
1. Subject — who/what, with distinguishing details (age, expression, clothing, materials, pose, action)
2. Composition & framing — shot type (close-up, medium, wide, overhead), angle (eye-level, low, Dutch), focal point, placement in frame
3. Setting / environment — location, background elements, depth, foreground-to-background separation
4. Lighting — direction, quality (hard/soft), color temperature, time of day, key/fill/rim, character of shadows and highlights
5. Color palette — dominant hues, accents, saturation, contrast
6. Camera & medium — photography (with lens / film stock if so)
7. Style - illustration, oil painting, 3D render, etc.
8. Mood & atmosphere — emotional register, weather, particles in air (dust, fog, mist, bokeh)
9. Technical finish — depth of field, grain, texture, sharpness, post-processing feel

# Output rules
- Output ONLY the Flux.1 prompt. No preamble, no labels, no markdown, no quotation marks, no explanations.
- Single paragraph. No bullets, no line breaks.
- Present tense, declarative, descriptive.
- Avoid prompt-engineering filler: "masterpiece," "best quality," "8k," "trending on artstation," "highly detailed," "award-winning."
- If a person resembles a real public figure, describe them generically ("a woman in her 30s with…") rather than by name.
- If the medium is ambiguous (photo vs. illustration vs. render), pick the most likely option and commit — do not hedge.
- Sentences only. If you would otherwise write a comma-separated list of three or more adjectives, rewrite as a sentence.
- Begin the prompt with the subject. Do not start with "An image of" or "A photo of" unless the medium is the most important feature.
"""

_META_FLUX2: Final[
    str
] = """\
You are an expert Flux.2 prompt engineer. Analyze the provided image and output a single, cohesive prompt that would recreate it (or its style) using Flux.2.

# How Flux.2 prompts differ from Flux.1 and SD/SDXL
- The subject and most critical attributes go FIRST. Style, mood, and technical finish go last.
- Preferred form is comma-separated descriptive phrases, not pure flowing prose and not keyword tags. Example structure: "Luxury leather handbag, draped over marble countertop, soft directional window light from left, warm amber tones, shallow depth of field, 85mm lens." Each phrase is concrete and compositional, not a single adjective.
- HEX color codes are honored. When a color is brand-specific, signature, or visually dominant, include the HEX (e.g., "deep forest green #1B4332"). Identify the 1-3 most visually dominant or signature colors and provide HEX codes for those specifically. Do not HEX every color.
- Text rendering is reliable. If the image contains legible text (signage, packaging, labels), reproduce it in double quotes — e.g., the sign reads "OPEN".
- Weight syntax (word:1.3), BREAK, and negative prompts are NOT used.
- Sweet spot: 50-110 words. Dense and specific beats long and meandering.

# Order of elements (this order matters for Flux.2)
1. Subject — the primary noun and its defining traits, first and concrete (age, build, expression, clothing fabric/cut, pose, action, or for objects: material, condition, orientation)
2. Secondary subjects / interaction — what else is in the scene and how it relates to the subject
3. Setting — location, surfaces, background depth, foreground elements
4. Composition & framing — shot type (close-up, medium, wide, overhead, three-quarter), angle, focal point, subject placement
5. Lighting — direction, quality (hard/soft/diffuse), color temperature, key/fill/rim, shadow character
6. Color palette — dominant hues with HEX where useful, accents, saturation level, contrast level
7. Camera & medium — photography (lens, aperture, film stock if relevant)
8. Style - illustration style, 3D render, oil paint, etc.
9. Mood & atmosphere — emotional register, weather, particulates (haze, dust, bokeh)
10. Technical finish — depth of field, grain, texture, sharpness

# Output rules
- Output ONLY the Flux.2 prompt. No preamble, no labels, no markdown, no quotation marks around the whole thing, no explanations.
- Single paragraph composed of comma-separated phrases. No bullets, no line breaks, no JSON.
- Phrases separated by commas. Do NOT write full grammatical sentences with subjects and verbs except where naming an action.
- Start with the subject noun phrase. Do NOT start with "An image of," "A photo of," "This picture shows," or any framing preamble.
- Present tense, declarative.
- Avoid filler: "masterpiece," "best quality," "8k," "highly detailed," "award-winning," "trending on artstation," "ultra-realistic." Replace with concrete specifics.
- If a person resembles a real public figure, describe them generically ("a man in his 50s with silver hair and a trimmed beard") — never by name.
- Commit to one medium. If photo vs. illustration vs. render is ambiguous, pick the most likely and state it confidently. Do not hedge with "either/or."
- Include HEX codes only for colors that are visually dominant or clearly brand-specific. Limit to 3 HEX codes.
- If legible text appears in the image, include it in double quotes within the prompt.
"""

_META_ZIMAGE: Final[
    str
] = """\
You are an expert Z-Image Turbo prompt engineer. Analyze the provided image and output a single, cohesive natural-language prompt that would recreate it (or its style) using Z-Image Turbo.

# How Z-Image Turbo prompts differ from Flux/SD/SDXL
- Z-Image Turbo is a distilled few-step model with NO classifier-free guidance, which means negative prompts are ignored. All constraints must be expressed as positive descriptions ("clean uncluttered background" not "no clutter"; "sharp focus, fine skin texture" not "no blur").
- The model's default human prior is glossy stock photography. To break out of that and produce realistic-looking people, you MUST anchor in concrete photographic equipment: a specific camera body, a specific lens, a film stock or sensor characteristic, and at least one non-idealized facial feature (asymmetry, freckles, weathered skin, three-day stubble, crooked tooth, slight bags under eyes, etc.).
- Attention fades after ~75 tokens (≈50-60 words). Put the subject and any required text in the FIRST sentence. Detail follows.
- Prefer natural-language sentences over comma-tag soup. Sentence-shaped descriptions outperform keyword lists.
- If text appears in the image, write the EXACT text inside double quotes (e.g., a sign reads "OPEN LATE"). Z-Image renders text well, including Chinese, when it is quoted.
- Add explicit texture words to escape plastic look: skin texture, pores, fabric weave, woodgrain, film grain, surface imperfections.

# Cover these elements, in this order, woven into 2-4 sentences
1. Subject + action — who/what, doing what, with at least one non-idealized concrete detail
2. Any in-image text — quoted exactly, with placement
3. Setting — location, surfaces, foreground/background depth
4. Lighting — direction, quality, time of day, color temperature
5. Camera & medium — body, lens (focal length + aperture), film stock or "shot on phone," composition keyword (medium shot, wide, overhead)
6. Mood + texture cues — atmosphere plus the specific texture words that fight the plastic default
7. In-prompt constraints (only if needed) — phrased positively, e.g., "clean studio background, no extra people in frame, no visible logos"

# Output rules
- Output ONLY the Z-Image Turbo prompt. No preamble, no labels, no markdown, no explanations.
- 50-90 words. Hard ceiling at 110.
- Start with the subject. Do NOT start with "An image of," "A photo of," "This shows."
- Present tense, declarative.
- No weight syntax (word:1.3), no BREAK, no negative-prompt block.
- Banned filler that does nothing here: "masterpiece," "best quality," "8k" (alone), "highly detailed" (alone), "ultra-realistic," "award-winning." Replace with concrete equipment and texture words.
- For any human subject, you MUST specify (a) a real camera body, (b) a focal length and aperture, (c) a film stock OR sensor descriptor, and (d) at least one non-idealized facial feature.
- If a person resembles a real public figure, describe them generically.
- Commit to one medium. No "either photo or painting" hedging.

# Reference example (match this density and structure — do not copy phrasing)
A middle-aged carpenter with weathered hands and a faint scar above his left eyebrow planes a length of oak in a small workshop, sawdust drifting in the air around him. Late afternoon sunlight cuts through a single dirty window, casting long warm shadows across the workbench and lighting the curl of shavings rising from his plane. Shot on a Pentax K1000 with a 50mm f/1.7 lens on Kodak Portra 400, medium shot at slightly low angle, fine film grain, visible skin texture and stubble, real wood grain on the workbench, quiet focused mood with a soft haze of dust in the light."""


_META_CHROMA: Final[
    str
] = """\
You are an expert Chroma prompt engineer. Analyze the provided image and output a Chroma prompt plus a negative prompt.

# How Chroma prompts differ from Flux.1/Flux.2
- Flowing prose works well. Write 2-4 natural sentences.
- Output negative prompts as a separate short list, comma-separated, on a new line.
- Be specific about color palette, hue, contrast, and saturation.

# Order of elements (in prose form)
1. Art direction — medium + style, FIRST when the image is stylized (e.g., "dark fantasy illustration," "inked comic panel," "oil painting in classical European style," "painterly digital concept art"). Skip or move down if the image is straight photography.
2. Subject — concrete, with distinguishing features (age, build, expression, costume, materials, pose, action)
3. Setting / environment — location, props, depth, background detail
4. Composition & framing — shot type (close-up, medium, wide, full-body, three-quarter), angle, focal point
5. Lighting — direction, quality, color temperature, dramatic effects (rim light, volumetric, chiaroscuro)
6. Color palette — dominant hues, accents, contrast level, saturation level
7. Mood & atmosphere — emotional tone, weather, particulates
8. Technical finish — brush stroke quality, line work, render style, grain, fidelity cues

# Output format (TWO blocks, separated by exactly one blank line)
Block 1: The positive prompt as 2-4 prose sentences.
Block 2: A line beginning with "Negative: " followed by 6-12 comma-separated terms.

# Output rules
- Output ONLY the two blocks. No preamble, no labels other than "Negative: ", no markdown, no explanations.
- Positive prompt: 60-120 words, prose sentences (not tag soup, not JSON).
- Negative prompt: short comma list. Standard quality negatives are fine ("low quality, blurry, smudged, deformed, bad anatomy, flat colors, restricted palette, jpeg artifacts, watermark, text"), plus 1-3 image-specific terms when the source clearly avoids something (e.g., add "photorealistic" to a negative for a stylized illustration to push away from photo bleed-through).
- Present tense, declarative.
- No weight syntax (word:1.3), no BREAK.
- Avoid filler in the positive: "masterpiece," "best quality," "8k," "trending on artstation."
- Commit to one medium per generation. Don't mix "photorealistic anime."
- For real public figures, describe generically.
- If the image is non-photographic, do NOT include camera/lens/film-stock language. Use brush, line, ink, render, or paint vocabulary instead.
- Cap the negative list at 12 terms. Overstuffed negatives muddy Chroma output.

Negative: low quality, blurry, smudged, deformed hands, bad anatomy, flat colors, restricted palette, jpeg artifacts, watermark, text, photorealistic, 3d render"""


_META_QWEN: Final[
    str
] = """\
You are an expert Qwen-Image-2512 prompt engineer. Analyze the provided image and output a structured prompt plus a negative prompt that would recreate it (or its style) using Qwen-Image-2512.

# How Qwen-Image prompts differ from Flux/SD/SDXL/Chroma
- Use short labeled phrases joined by commas, not flowing sentences.
- BREVITY WINS. The sweet spot is 1-3 sentences total / roughly 30-70 words. Long prompts hurt this model rather than helping it.
- Position-weighted attention. The PRIMARY SUBJECT goes first, before any setting or style information.
- Best-in-class text rendering, including Chinese. ALWAYS put in-image text inside double quotes. Specify font style (bold sans-serif, elegant serif, handwritten, calligraphy) and placement (upper left, centered, along the bottom) when text appears.
- Negative prompts are supported and improve satisfaction. Output them.
- Standard sampling is CFG 4.0-4.5 at 50 steps; the prompt should be written assuming full-quality render.

# Recommended structured format
Use short comma-joined phrases, optionally grouped by category. Categories that matter, in order:
1. Subject — primary noun + 2-4 defining traits (age, ethnicity if relevant, clothing, expression, action)
2. Pose / action — what the subject is doing
3. Environment — location and 1-3 anchoring details
4. Lighting — direction + quality + temperature, kept short
5. Camera / framing — shot type, angle, lens if photographic
6. Style — medium and artistic style (photorealistic, oil painting, anime cel-shaded, 3D render, etc.)
7. Mood — 1-2 atmosphere words
8. Detail anchors — 1-3 micro-detail cues (skin texture, fabric weave, sharp focus on eyes)
9. Any text — quoted, with font and placement

# Output format (TWO blocks, separated by exactly one blank line)
Block 1: The positive prompt as comma-joined phrases. May span 1-3 sentences if natural breaks help, but keep it tight.
Block 2: A line beginning with "Negative: " followed by 4-8 comma-separated terms.

# Output rules
- Output ONLY the two blocks. No preamble, no labels except "Negative: ", no markdown, no explanations.
- Positive: 30-70 words, 1-3 sentences, comma-phrase style.
- Start with the subject noun. Do NOT begin with "An image of," "A photo of," "This shows."
- If in-image text exists, quote it exactly and specify font + placement. Reproduce non-Latin scripts faithfully. If text is visible but illegible or partial, omit it rather than guessing.
- Negative: short, focused. Standard quality terms ("low quality, blurry, deformed, bad anatomy, extra fingers, watermark") plus 1-2 image-specific exclusions when relevant.
- No weight syntax (word:1.3), no BREAK.
- Avoid filler: "masterpiece," "8k" (alone), "highly detailed" (alone), "trending on artstation."
- Commit to one medium. No hybrid styles.
- For real public figures, describe generically.

# Reference negative example 
Negative: low quality, blurry, deformed, plastic skin, oversaturated, watermark, text artifacts
"""


_META_SDXL: Final[
    str
] = """\
You are an expert SDXL prompt engineer. Analyze the provided image and output an SDXL positive prompt plus a negative prompt that would recreate it (or its style).

# Positive prompt structure (in this order)
1. Quality opener — 2-4 quality tags ("masterpiece, best quality, highly detailed, sharp focus")
2. Medium / style declaration — "photograph," "oil painting," "digital illustration," "3D render," etc.
3. Subject sentence — natural-language description of the primary subject and action (1 short sentence)
4. Subject attributes — comma tags for age, build, hair, eyes, clothing, expression, pose
5. Setting tags — comma tags for location, props, background depth
6. Lighting tags — direction, quality, time of day, color temperature
7. Composition tags — shot type (close-up, medium shot, wide shot, full body), angle, framing
8. Color/palette tags — dominant hues, saturation, contrast cues
9. Technical/style tags — lens info if photo, brushwork if painting, render style, fidelity cues
10. Optional weighted emphasis on 1-3 critical attributes

# Negative prompt structure
Standard quality block first, then anatomy/artifacts, then image-specific exclusions:
- Quality: "low quality, worst quality, blurry, jpeg artifacts, lowres, watermark, signature, text"
- Anatomy: "deformed, disfigured, bad anatomy, extra fingers, extra limbs, fused fingers, missing fingers"
- Image-specific: 1-4 terms that push away from things this image clearly is NOT (e.g., "monochrome" if the image is colorful, "cartoon" if the image is photoreal)

# Output format (TWO blocks, separated by exactly one blank line)
Block 1: Positive prompt — natural-language opener followed by comma tags, optionally with BREAK separators.
Block 2: Line beginning with "Negative: " followed by comma-separated terms.

# Output rules
- Output ONLY the two blocks. No preamble, no labels other than "Negative: ", no markdown, no explanations.
- Positive: roughly 60-130 words. Quality tags first, subject by token 30, full attribute set by token 75.
- BREAK is allowed but optional. Use it only when subject and style would otherwise contaminate each other.
- Token weights: max 3 per prompt, weights between 0.7 and 1.4.
- Negative: 12-25 comma-separated terms. The negative prompt is a precision tool, not a wishlist. Cap at 25 terms. Prefer fewer, more specific negatives over a kitchen sink.
- Commit to one medium. Don't mix.
- For real public figures, describe generically.

# Reference negative example
Negative: low quality, worst quality, blurry, jpeg artifacts, lowres, watermark, signature, text, deformed, disfigured, bad anatomy, extra fingers, extra limbs, fused fingers, missing fingers, oversaturated, plastic skin, cartoon, anime, 3d render, cgi, harsh lighting, flat lighting"""


_META_PONY: Final[
    str
] = """\
You are an expert Pony Diffusion v6 XL (and Pony-derivative checkpoint) prompt engineer. Analyze the provided image and output a Pony-format positive prompt plus a negative prompt.

# How Pony prompts differ from base SDXL and everything else
- Pony uses a custom tag vocabulary based on booru conventions (Danbooru/e621-style). NOT natural language. NOT standard SDXL quality tags. The whole prompt is comma-separated tags from start to finish, with at most a single short descriptive phrase if absolutely necessary.
- Tags often use underscores between words (looking_at_viewer, from_above, depth_of_field). Both space and underscore forms work in most builds, but underscore is the convention and is more reliable.
- The MOST IMPORTANT distinction: Pony uses score tags, source tags, and rating tags as a mandatory front-of-prompt block. Skipping these produces dramatically worse output.

# Mandatory leading block (in this order, always present)
1. Score tags — ALWAYS start with: score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up
   (This stack pulls the model toward its highest-quality training distribution. It is not redundant — each tag does work. Always include all six.)
2. Source tag — exactly one of: source_anime, source_cartoon, source_pony, source_furry, source_3d
   Pick based on the image's medium: anime/manga style → source_anime; western 2D illustration → source_cartoon; 3D render or CGI → source_3d; anthropomorphic animal characters → source_furry; if none clearly apply, omit rather than guess.
3. Rating tag — one of: rating_safe, rating_questionable, rating_explicit
   Default to rating_safe unless the image is clearly non-safe content.

# Tag body structure (comma-separated tags after the leading block)
4. Subject count — 1girl, 1boy, 2girls, multiple_girls, solo, etc.
5. Subject attributes — using booru tag conventions: hair (long_hair, blonde_hair, twintails), eyes (blue_eyes, closed_eyes), face (smile, blush, serious), build, age cues
6. Clothing — specific garment tags (school_uniform, leather_jacket, white_dress, hoodie, off_shoulder)
7. Pose / action — sitting, standing, walking, holding_book, looking_at_viewer, looking_away, from_side
8. Setting — location tags (forest, indoors, cafe, cityscape, night), background elements
9. Lighting — sunlight, moonlight, backlighting, rim_lighting, dramatic_lighting, soft_lighting
10. Composition — shot type tags: portrait, upper_body, cowboy_shot (mid-thigh up), full_body, close-up; angle tags: from_above, from_below, dutch_angle
11. Style modifiers — detailed_background, intricate_details, cinematic, film_grain, depth_of_field
12. Optional booru artist/style tags ONLY if you can identify a recognized style (rare; skip if unsure)

# Negative prompt
The standard Pony negative leads with the inverse score tags plus quality terms, then anatomy:
- "score_6, score_5, score_4, score_3, score_2, score_1, worst quality, low quality, lowres, bad anatomy, bad hands, deformed, extra fingers, missing fingers, extra limbs, jpeg artifacts, watermark, signature, text"
- Add 1-3 source exclusions to push away from unwanted style bleed (e.g., "source_furry" in the negative if the image is human and you want clean human output; "source_3d" in the negative if the image is 2D illustration)
- If rating_safe is in positive, also add "nsfw, nudity" to negative as a belt-and-suspenders measure

# Output format (TWO blocks, separated by exactly one blank line)
Block 1: Positive prompt — pure comma-separated tag list, leading score/source/rating block first.
Block 2: Line beginning with "Negative: " followed by comma-separated tags.

# Output rules
- Output ONLY the two blocks. No preamble, no labels other than "Negative: ", no markdown, no explanations.
- Positive: 30-80 tags total. Pure tag form. No prose sentences. No "a woman who is."
- You must use Danbooru/e621 booru tag conventions. Multi-word descriptions become underscore-joined tags (e.g., "long blonde hair" → long_hair, blonde_hair). If you find yourself writing a sentence with verbs like "is," "has," or "wearing," stop and rewrite as tags.
- Use underscore form for multi-word tags (looking_at_viewer, blonde_hair, depth_of_field).
- The score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up block goes at the very front, always, in that exact order.
- Pick exactly one source tag, or omit if genuinely ambiguous.
- Default to rating_safe unless the image is clearly otherwise.
- Token weighting (tag:1.2) is supported, max 3 per prompt, weights 0.7-1.4.
- Negative: 15-25 tags including the inverse-score block.
- For real public figures, describe generically with attribute tags only.

# Reference negative example
Negative: score_6, score_5, score_4, score_3, score_2, score_1, worst quality, low quality, lowres, bad anatomy, bad hands, deformed, extra fingers, missing fingers, extra limbs, jpeg artifacts, watermark, signature, text, source_furry, source_3d, nsfw, nudity, oversaturated, blurry"""


_META_BY_TARGET: Final[dict[TargetModel, str]] = {
    "flux1": _META_FLUX1,
    "flux2": _META_FLUX2,
    "zimage": _META_ZIMAGE,
    "chroma": _META_CHROMA,
    "qwen": _META_QWEN,
    "sd": _META_SDXL,
    "pony": _META_PONY,
}


# --- Safety / uncensored directives ---------------------------------------
#
# Appended near the end of the system prompt — after all the formatting
# rules so they take precedence over earlier defaults like Pony's
# rating_safe convention.


_SAFETY_DIRECTIVE: Final[str] = (
    "\n\n# Content constraint (overrides anything above)\n"
    "Keep the description fully SFW. Do NOT mention nudity, sexual acts, "
    "exposed genitalia, or any explicit content. If the image contains "
    "such content, describe only the non-explicit visible elements "
    '(setting, framing, lighting, etc.). Where a "Negative:" block is '
    'produced, ensure it includes "nsfw, nudity, explicit". For Pony '
    "targets keep rating_safe and append nsfw, nudity to the negative."
)


_UNCENSORED_DIRECTIVE: Final[str] = (
    "\n\n# Content constraint (overrides anything above)\n"
    "Describe any visible nudity, anatomy, or sexual acts using explicit, "
    "anatomically-correct vocabulary. Do not euphemize, omit, or soften "
    "such details. Mention positions, exposed body parts, and acts plainly. "
    'Where a "Negative:" block is produced, do NOT add "nsfw," "nudity," '
    'or "explicit" to it. For Pony targets switch the rating tag to '
    "rating_explicit and remove nsfw / nudity from the negative."
)


# --- Presets --------------------------------------------------------------


class TargetPreset(NamedTuple):
    label: str
    prefix: str
    suffix: str
    has_negative: bool


TARGET_PRESETS: Final[dict[TargetModel, TargetPreset]] = {
    "sd": TargetPreset(
        label="Stable Diffusion (SDXL)",
        prefix="",
        suffix="",
        has_negative=True,
    ),
    "pony": TargetPreset(
        label="Pony (SDXL)",
        prefix="",
        # Pony's rating tag is emitted INSIDE the meta-prompt's positive
        # block, so the user-side suffix is empty — no double-rating.
        suffix="",
        has_negative=True,
    ),
    "flux1": TargetPreset(
        label="Flux 1",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "flux2": TargetPreset(
        label="Flux 2",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "zimage": TargetPreset(
        label="Z-Image Turbo",
        prefix="",
        suffix="",
        has_negative=False,
    ),
    "chroma": TargetPreset(
        label="Chroma",
        prefix="",
        suffix="",
        has_negative=True,
    ),
    "qwen": TargetPreset(
        label="Qwen-Image",
        prefix="",
        suffix="",
        has_negative=True,
    ),
}


# --- Composer -------------------------------------------------------------


def _enabled(extras: list[ExtraOption]) -> dict[ExtraOption, bool]:
    """Resolve duplicates + the safety/uncensored mutex.

    If both ``includeSafety`` and ``includeUncensored`` arrive, prefer
    ``includeSafety`` (the more conservative default — easier to flip
    later than to undo a leak the other way).
    """
    seen: dict[ExtraOption, bool] = {}
    for x in extras:
        if x not in seen:
            seen[x] = True
    for a, b in MUTEX_PAIRS:
        if a in seen and b in seen:
            del seen[b]
    return seen


def compose_generate_prompts(
    target_model: TargetModel,
    architecture: Architecture,  # noqa: ARG001 — t2i is the only supported value today
    extras: list[ExtraOption],
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for image-to-prompt generation.

    The system prompt is the target's full meta-prompt body plus a
    safety / uncensored directive when one of those extras is active.
    The user prompt is a short fixed instruction that, with the image
    attached by ``VlmClient.generate_text``, asks the model to produce
    the prompt.
    """
    meta = _META_BY_TARGET[target_model]
    enabled = _enabled(extras)
    if enabled.get("includeSafety"):
        meta = meta + _SAFETY_DIRECTIVE
    elif enabled.get("includeUncensored"):
        meta = meta + _UNCENSORED_DIRECTIVE
    return meta, _USER_INSTRUCTION


# --- Output parsing -------------------------------------------------------


# Matches a line that introduces the negative block. Tolerates leading
# whitespace, Markdown emphasis on either side of the label
# (``**Negative:**``), and the "Negative prompt:" variant. The first
# inline run (everything after the colon on the same line) is captured;
# the trailing ``\**`` chunk lets us absorb a closing ``**`` from
# Markdown without leaking it into the negative body.
_NEGATIVE_LINE_RX: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*\**\s*[Nn]egative(?:\s+prompt)?\s*:\s*\**\s*(.*?)\s*\**\s*$",
    re.MULTILINE,
)

# Strip Markdown / labelled-block junk the model sometimes emits
# despite the "no labels" instruction in the meta-prompt.
_BLOCK_LABEL_RX: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\**\s*)?Block\s*[12]\s*[:\-]\s*",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_markdown_fence(text: str) -> str:
    """Remove a leading/trailing triple-backtick fence if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence and any language tag on the first line.
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def parse_output(target_model: TargetModel, raw: str) -> tuple[str, str | None]:
    """Split a Qwen3 response into ``(positive, negative_or_none)``.

    For targets in :data:`MODELS_WITH_NEGATIVE` we look for a line that
    begins with ``Negative:`` (Markdown emphasis and "Negative prompt:"
    variants are accepted) and split there. For other targets the whole
    response is the positive prompt and ``negative`` is ``None``.

    Robust to:
    * leading/trailing whitespace
    * a wrapping ``⁠```⁠``⁠``⁠`` code fence
    * stray ``Block 1:`` / ``Block 2:`` labels
    * the model failing to emit a Negative block (returns ``None``)
    """
    text = _strip_markdown_fence(raw)
    text = _BLOCK_LABEL_RX.sub("", text).strip()

    if target_model not in MODELS_WITH_NEGATIVE:
        return text, None

    match = _NEGATIVE_LINE_RX.search(text)
    if not match:
        return text, None

    positive = text[: match.start()].rstrip()
    inline_tail = match.group(1).strip()
    after_line = text[match.end() :].strip()

    if inline_tail and after_line:
        negative = f"{inline_tail}\n{after_line}".strip()
    else:
        negative = (inline_tail or after_line).strip()

    return positive.strip(), (negative or None)


__all__ = [
    "TargetModel",
    "Architecture",
    "ExtraOption",
    "MODELS_WITH_NEGATIVE",
    "MUTEX_PAIRS",
    "EXTRA_OPTION_LABELS",
    "TargetPreset",
    "TARGET_PRESETS",
    "compose_generate_prompts",
    "parse_output",
]
