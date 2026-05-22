Question: 
Is there a reliable way to get a prompt from Qwen3, using the meta-prompts above, to change out a subject, setting, composition, or style? In other words, can a meta-prompt (using Qwen-Image as an example) be crafted to override the image inputs, for only target a fairly narrow set of characteristics, but still be structured in a similar way?



Answer:
Yes, and this is actually a much cleaner pattern than trying to do it in post — you're asking Qwen3 to do selective extraction, which it's well-suited for. The trick is that "ignore X from the image" and "extract X from the image and substitute Y" are different operations, and the meta-prompt has to be explicit about which one is happening for each element.

There are three patterns that work reliably, and they compose. Using Qwen-Image as the running example.

**Pattern 1: Extract some, override others (fixed substitution).** You want to keep the composition, lighting, and style of the source image, but swap the subject for something specific you provide. The meta-prompt accepts named overrides as part of the user instruction, not the image:

````
You are an expert Qwen-Image-2512 prompt engineer. You will receive an image AND a set of override instructions. Analyze the image and output a structured Qwen-Image prompt that uses ONLY the elements specified below from the image, substituting the override values for everything else.

# Extraction policy
For each element category, you will be told either EXTRACT (use what's in the image) or OVERRIDE (use the value provided in the user instruction). Default behavior if not specified:
- subject: EXTRACT
- setting: EXTRACT
- composition: EXTRACT
- lighting: EXTRACT
- style: EXTRACT
- mood: EXTRACT
- palette: EXTRACT

# Override format
The user will provide overrides in the form:
  OVERRIDE subject: <description>
  OVERRIDE setting: <description>
  OVERRIDE style: <description>
  (etc.)

Any element not listed as OVERRIDE is extracted from the image as normal.

# Conflict handling
If an OVERRIDE conflicts with what's in the image (e.g., overriding subject to "a man on horseback" when the image shows a kitchen interior), use the OVERRIDE value and adapt surrounding context only as needed for coherence. Do NOT try to reconcile — the override wins.

# [Rest of the standard Qwen-Image meta-prompt: structure, output format, rules, reference example]
````

Then in the user turn you'd say something like: `OVERRIDE subject: a silver tabby cat sitting upright. OVERRIDE style: oil painting in classical European style.` and pass the image. Qwen3 extracts the rest from the image and produces a prompt that reads like the original composition and lighting applied to your overridden subject.

The reason this is more reliable than freeform "change the subject to a cat" instructions is that the explicit EXTRACT/OVERRIDE labeling forces Qwen3 to make a discrete decision per category rather than blending. Without that structure, Qwen3 8B especially will do partial blending — keeping some color cues from the original subject in the new one, mixing styles, etc.

**Pattern 2: Narrow extraction (whitelist mode).** Inverse of pattern 1 — you want Qwen3 to look at the image but only pull a specific small set of attributes, generating everything else from scratch. Useful when the source image is a reference for one thing only (a color palette, a lighting setup, a composition).

````
# Extraction policy
You will be told which elements to EXTRACT from the image. Everything else must be generated from the TARGET DESCRIPTION provided in the user instruction.

The user will provide:
  EXTRACT: <comma-separated list of elements>
  TARGET: <free-text description of the desired image>

Elements not in the EXTRACT list are taken from TARGET. Elements in the EXTRACT list are pulled from the source image and used verbatim in the output prompt.

Valid EXTRACT elements: subject, setting, composition, framing, lighting, palette, style, mood, texture.
````

So a user turn might be: `EXTRACT: lighting, palette, composition. TARGET: A bonsai tree on a wooden table.` and you pass a moody Tokyo neon image. The output prompt is a bonsai tree on a wooden table rendered with that Tokyo image's lighting setup and color palette and framing. The bonsai never appeared in the source.

This is the more powerful pattern for production workflows because you can build a library of "reference images for lighting" and "reference images for composition" and recombine them with arbitrary subjects. It also tends to work better on the 8B than pattern 1, because narrow extraction is mechanically simpler than selective substitution.

**Pattern 3: Pure style transfer (image as style reference only).** Degenerate case of pattern 2 where the only extracted element is style. Worth calling out separately because it's such a common need:

````
EXTRACT: style.
TARGET: <full scene description>.
````

The output is a prompt describing your target scene in the source image's medium/treatment/finish. The Qwen-Image meta-prompt's structured output is well-suited to this because "style" is one of its native categories — you're just asking Qwen3 to fill that category from the image and the other categories from your text.

**Where this gets brittle and how to harden it.**

The 8B will sometimes ignore the override on the first try, especially if the source image is very visually dominant (a busy scene with strong subject). The fix that helps most: have the meta-prompt require Qwen3 to output a short JSON header before the prompt declaring what it extracted and what it overrode. Something like:

````
Before the prompt, output a single JSON line of the form:
{"extracted":["lighting","palette","composition"],"overridden":["subject","style"]}
Then a blank line, then the prompt as normal.
````

This forces Qwen3 to commit to a decision per category before generating, and the act of writing the JSON catches drift. You can either keep the JSON in your output for debugging or strip it programmatically before sending to Qwen-Image. The 30B doesn't need this; the 8B does on maybe 20% of generations.

The other brittleness is coherence. If your override is `OVERRIDE subject: a medieval knight in full plate` and the source image is a beach scene, Qwen3 will produce a coherent-looking prompt — but Qwen-Image will render a knight on a beach, which may or may not be what you wanted. The meta-prompt can't fix this for you; the override system gives you exactly what you asked for, and "knight on a beach" was what you asked for. If you want the setting to adapt to the subject, you have to also OVERRIDE setting, or add a meta-instruction like `When OVERRIDE subject and EXTRACT setting are both active, verify the combination is plausible; if implausible, prefer the subject and adapt the setting minimally for coherence.` That last rule is somewhat shaky on the 8B but reliable on the 30B.

**Practical setup for your pipeline.** I'd build this as one meta-prompt per model with both extract and override capability, and use the user turn to specify the operation. So a Qwen-Image generation call looks like:

```
[meta-prompt as system message]
[user message: EXTRACT or OVERRIDE instructions + image]
[Qwen3 response: structured prompt]
```

That gives you four operating modes from a single meta-prompt: full extraction (no user overrides, just the image — works exactly like the original meta-prompts), partial extraction (some OVERRIDE lines), narrow extraction (EXTRACT whitelist + TARGET), and pure synthesis (no image, just TARGET — yes, you can run the meta-prompt with no image and a TARGET-only user message, and Qwen3 will produce a from-scratch prompt in the same structure). That last mode is genuinely useful and worth keeping in the design.

One thing not to bother with: trying to use the source image as a literal reference and asking Qwen3 to describe deltas ("change the subject from a woman to a man, keep everything else"). That phrasing reads more naturally but produces worse results because it's asking Qwen3 to do extraction + comparison + substitution as one operation instead of as labeled steps. The structured EXTRACT/OVERRIDE pattern is uglier to type but more reliable, and you can wrap it in a friendlier UI at the pipeline layer.