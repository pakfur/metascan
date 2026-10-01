[← Back to README](../README.md)

# Text to Image

The **Text to Image** dialog turns a caption into a prompt and renders it through a ComfyUI workflow. Open it with the sparkles icon at the top right of the main screen, next to **Storyboards** (desktop only).

A caption comes from one of two places:

- **Manual** — you type it.
- **Random Caption** — the server draws it from `data/t2i_captions/t2i_captions.csv`, optionally narrowed by column filters.

The pipeline is always the same:

1. **Resolve** — character-name tokens (`__ALICE__`, …) and body-part tokens (`__HAIR__`, …) become deterministic character descriptions: the same caption always gets the same characters, whatever the seed.
2. **Rewrite** — the local VLM turns the resolved caption into a prompt in the selected model's style (Krea 2, Qwen-Image, SDXL, Z-Image).
3. **Render** — a registered ComfyUI workflow renders it. Every image is its own job with its own seed.

Generated images are ordinary library media. They appear in the grid on the right of the dialog, and each one remembers the dialog contents that produced it.

## The form

The form is on the left, top to bottom: the caption and where it comes from, the prompt, then the model, image and seed settings and the LoRAs. **Batch Size**, **Count per Batch**, **Cancel** and **Generate** stay pinned at its bottom, so they are in reach however far the form is scrolled; while an image is selected, a bar pinned at its top says so.

| Control | What it does |
|---|---|
| **Caption** | The description to expand. Plain text; tokens are optional. A collapsible *Resolved caption* shows exactly what the VLM will be given. |
| **Manual / Random Caption** | Where captions come from. In Random mode the 🎲 button loads one caption and **empties the Prompt**, so the next **Generate** writes a prompt for that caption and renders it. |
| **Filter** | Random mode only. Nudity, the three scores, Males / Females, Aspect Ratio and Clothing, with a live "N captions match". |
| **Generate Prompt** | Resolves the caption and writes the prompt into **Prompt**. Review-only: nothing is queued. In Random Caption mode the next **Generate** renders that prompt as its first batch (see Batches). |
| **Prompt** / **Negative** | The text that will be rendered. **Negative** appears only for models that write one (Qwen-Image, SDXL) and is sent only if the workflow has an `MS_NEGATIVE` node. |
| **Model** | Krea 2, Qwen-Image, SDXL or Z-Image. Chooses the prompt style and the default workflow. |
| **Workflow** | Every registered `t2i` workflow. |
| **Size** | Pixel budget in megapixels. |
| **Aspect Ratio** | Manual: your choice. Random: taken from each caption's row (read-only while a batch runs). |
| **Seed** and policy | The seed, plus how it advances: **Fixed**, **Increment**, **Decrement** or **Randomize**. |
| **LoRAs** | Same editor as Image to Video. Needs an `MS_LORA_STACK` node in the workflow. |
| **Batch Size** | How many batches a run renders (Random mode). Batch 1 is the prompt in the box when there is one. Locked at 1 in Manual mode. |
| **Count per Batch** | How many images each batch renders, differing only in their seed. Above 1 needs a non-Fixed policy. |

## Batches

**Generate** starts a batch on the server, so it keeps running if you close the dialog or reload the page; reopening the dialog reattaches to it. **Cancel** stops every running batch and cancels its unfinished jobs.

- **Manual:** one step — your caption and prompt — rendered `Count per Batch` times.
- **Random Caption:** a run is `Batch Size` batches. A **batch** is one caption, one prompt and `Count per Batch` images that differ only in their seed. The boxes decide what **batch 1** is, and nothing else does (nothing is remembered about how the text got there):
  - the **Prompt** box holds text → that prompt is rendered as it stands (with the Caption, Aspect Ratio and Negative in the boxes); no caption is rolled and no prompt is written;
  - the Prompt is empty but the **Caption** box is not → a prompt is written for that caption, then rendered; no caption is rolled;
  - both are empty → a caption is rolled from the Filter and a prompt written for it.

  Every later batch rolls a new caption from the Filter, takes its aspect ratio, writes its prompt, then renders its images. Within a batch the prompt and every setting are kept; only the seed changes from image to image. When a run ends the boxes keep the last batch's caption and prompt, so the next **Generate** starts with that prompt and the next seeds; to start on something new, empty the Prompt (or press 🎲, which loads a caption and empties the Prompt). While a run is going, the Caption, Prompt, Aspect and Seed boxes show the batch being rendered and cannot be edited. Only one Random batch runs at a time. If you close the dialog during a run it carries on: reopening shows it live, and if it has already finished the boxes are as you left them (the last batch's values are handed back only to an open dialog), though the Seed box is always current.

**Seed.** The box always holds the seed the next image will use, and it moves on after every image unless the policy is **Fixed**: **Increment** and **Decrement** step by one, **Randomize** draws a random one. While a Random run renders, the box shows the seed of the image being rendered; when **Generate** is pressed the dialog's own seed also jumps to the next unused one, so it is right when the boxes unlock, after a reload, and if you closed the dialog mid-run. Under **Randomize** the first image of a Generate uses the seed shown in the box, the rest are random, and the run ends on a fresh random seed. A run stops early, with a message, rather than leave `0` to `2147483647` (the batch then plans fewer images and the seed box is left alone, because no unused seed remains). **The seed does not choose the characters.** A caption's cast is drawn from the text of the caption itself, so the same caption always describes the same people whatever the seed, and a new seed changes the image (and the value of any plain `__TOKEN__` wildcard) but not who is in it. The flip side: editing the caption, even by a comma, draws a new cast.

**GPU order** follows the existing `comfy.unload_vlm_during_generation` setting:

- **On (default):** all prompts are written first, then the VLM is unloaded, then the images render. The first image appears only after the last prompt is written. A Manual **Generate** also unloads the VLM, so the next **Generate Prompt** reloads it.
- **Off:** each batch renders as soon as its prompt is ready, overlapping the next prompt.

Either way the boxes stay on the batch being rendered: a prompt written ahead of the render waits, and is shown when that batch's images start.

Jobs are submitted through a small window (`t2i.window`, default 4 unfinished jobs per batch) so a large batch cannot bury Image to Video and storyboard jobs in ComfyUI's shared queue.

Batch state lives in memory. If the server restarts mid-run, steps that were not yet submitted are dropped; jobs already queued still finish and their images are ingested, but without a saved form.

## Caption files (`data/t2i_captions/`)

`t2i_captions.csv` needs the columns `Caption` and `Aspect Ratio`. `Nudity`, `Artistic Quality`, `Erotic Score`, `Pornographic Score`, `Males`, `Females` and `Clothing` are optional; each one present enables its filter. The file is git-ignored — it is your data.

### Tokens

A token is a name in capitals between double underscores.

- **Characters** — `__ALICE__ __BELLA__ __CLARA__ __DIANNA__ __EMMA__` (female) and `__ADAM__ __BOB__` (male). The first time a character appears it is replaced by a generated description; later mentions become a short handle tied to the same character. The description depends on the caption text alone.
- **Body-part tokens** — `__HAIR__ __BREASTS__ __VAGINA__ __PENIS__` stand in for the word itself and belong to a character (`her __HAIR__`). They resolve from a list of the same name.
- **Anything else** — `__TOKEN__` resolves from `token.txt` if that list exists, as one value used everywhere in the caption, picked by the seed; otherwise it becomes the plain lowercase word.

Malformed underscores (`____ALICE____`) are tolerated. A caption with no tokens passes through unchanged.

### Lists

Each characteristic is a text file with one value per line; blank lines and `#` comments are ignored and duplicates are dropped.

| File | Used for |
|---|---|
| `age.txt`, `ethnicity.txt`, `skin.txt`, `eyes.txt`, `face.txt`, `hair.txt`, `body.txt` | The character description. |
| `<name>.female.txt` / `<name>.male.txt` | Override `<name>.txt` for that gender. The shipped body lists are `body.female.txt` and `body.male.txt`. |
| `breasts.txt`, `vagina.txt`, `penis.txt` | Body-part tokens. **Not shipped** — supply your own. A missing list makes the token its plain word. |

Write values so they read after `with`: `an oval face`, `green eyes`, `olive skin`, `an athletic build`. Write every `age` value with its number (`45-year-old`): the age wording above reads it. Hair values must end in ` hair` (`auburn hair`) — the short handle for a later mention is built from it (`the auburn-haired woman`).

Lists reload automatically when a file changes. **Editing or reordering a list changes which value a given caption picks.**

**Adult only.** Any line in an `age` list containing a number under 18, in digits or spelled out (`twelve-year-old`), is rejected. Any line in any list containing a minor-indicating word, phrase or word family (`teen…`, `child…`, `loli…`, `school girl`, plurals included) or an age under 18 (`14-year-old`, `aged 12`) is rejected. The `nouns` and `names` in `characters.yml` are screened the same way. It is a word screen for careless edits, not a promise about what a list can express: keep your lists adult. A line containing a parenthesis is rejected too, because parentheses never belong in a generated prompt. Rejections are logged and listed in the `wildcards.warnings` of `GET /api/t2i/config`.

### `characters.yml`

Optional; defaults are built in. It sets the names, the noun for each gender, which slots are drawn and in what order, which slots read as `<age> <ethnicity> woman` versus `with …`, which tokens belong to characters, and which words before `hair` mean body hair (`pubic`, `body`, …), where the plain word is used.

### How a description is placed

- If the next word is a verb, the description goes inline: *A 31-year-old West African woman with olive skin, brown eyes and an athletic build sits on a bench.*
- If the next word is a possessive, `with`, `and`, punctuation, or the name is part of a compound subject (`X and Y`), only the head noun goes inline and the details move to one trailing sentence. Parentheses are never used — ComfyUI parses them as weighting.
- A slot the caption already writes out with a token (`__HAIR__`) is left out of the description.
- The owner of a body-part token is the character a pronoun points at (`his` / `her`), otherwise the character named nearest before it. This is a heuristic: in a caption with several people the worst case swaps who gets which hair colour.

### Age wording

A bare *woman* or *man* makes image models draw the subject younger than the number, so a first mention also names the age bracket in words: from **40** the noun becomes *middle aged woman* / *middle aged man* (*A 52-year-old Nordic middle aged man with …*), and **over 70** it becomes *old woman* / *old man* (a 70-year-old is still middle aged). The age is the first number in the drawn `age` value, in digits or spelled out (`45-year-old`, `forty-five-year-old`, `in her 50s`); a value with no number (`elderly`) is left alone, and so is a character with no `age` list. Later mentions keep the plain noun. The two cut-offs are the constants `MIDDLE_AGED_FROM` and `OLD_FROM` in `metascan/core/t2i_characters.py`.

### Later mentions

| Style | Example | Default for |
|---|---|---|
| `ref` | *the copper-red-haired woman* | Krea 2, Qwen-Image, Z-Image |
| `noun` | *the woman* | SDXL |
| `name` | *Alice* (introduced as "…woman named Alice") | — |

Override per model with `t2i.identity` in `config.json`.

## Models and prompt style

Each model's guideline is an entry in `data/meta_prompt.yml`, edited live: `META_KREA2`, `META_QWEN`, `META_SDXL`, `META_ZIMAGE`. `T2I_CAPTION_PREAMBLE` tells the VLM that the caption stands in for the image. `META_KREA2` is a starting point — tune it. The **content mode** (`t2i.content_mode`) appends the existing *Uncensored* or *Keep SFW* directive, or nothing.

If no VLM is installed (its weights or `llama-server` are missing), or it fails inside a batch, the prompt falls back to the resolved caption (SDXL and Qwen-Image also get a stock negative, SDXL a quality prefix) and the step carries a warning.

## Workflows

Register workflows in **Configuration → Text to Image**. A `t2i` workflow is a ComfyUI API-format graph whose nodes are titled with the `MS_*` convention:

| Title | Required | Purpose |
|---|---|---|
| `MS_POSITIVE` | yes | Prompt text |
| `MS_SEED` | yes | Seed |
| `MS_LATENT` | yes | Width, height and batch size (metascan sets batch size 1) |
| `MS_SAVE` | yes | The save node metascan collects images from |
| `MS_NEGATIVE` | no | Enables the Negative box |
| `MS_LORA_STACK` | no | Enables the LoRAs editor (a stackable loader such as rgthree Power Lora Loader) |

The **Validate** button lists everything at once and warns when `MS_LORA_STACK` or `MS_NEGATIVE` is missing. The tab also sets the default workflow per model, the output folder, the file-name prefix and the size choices.

## Results

- The grid on the right shows your images newest first, three to a row, 60 at a time: **Show more** at the end of the grid adds the next 60. It scrolls on its own, beside the form. A running job shows as a placeholder in front of the images (its progress, ✕ cancels it). While a run adds images the grid stays at the pages you have open, so new images push the oldest shown ones behind **Show more** instead of making the grid longer; reopening the dialog starts again at 60.
- Click a thumbnail to load the dialog contents that produced it (mode, filter, caption, model, workflow, size, aspect ratio, seed, prompt, negative, LoRAs). Edits save automatically into that image. **Stop editing** returns to a scratch form. Seed policy, Batch Size and Count per Batch are never restored, so selecting an image cannot trigger a large re-run.
- **Generate** with an image selected first saves your pending edits into it, then leaves editing mode before the seed advances (the form keeps its values), so the automatic seed advance never rewrites the saved form of the image it came from. Thumbnails cannot be selected while a Random batch runs.
- New images reach the library grid while the dialog is open *and* after you close it, without the dimming "Loading media…" overlay.
- Double-click to view (the viewer steps through the images the grid shows); **★** stars it (the library's favorite flag); **✕** deletes the file and its library entry.
- With no image selected, the scratch form is remembered between openings in your browser.

## Smart folders

**Generated with T2I** is a rule in the smart-folder editor: it matches every library image produced by this dialog.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Random Caption is disabled | The CSV is missing or unreadable; the reason is in `csv.error` from `GET /api/t2i/config`. |
| "…has no MS_LORA_STACK node" | You added LoRAs but the workflow cannot take them; remove them or add an `MS_LORA_STACK` node. |
| A negative was ignored | The workflow has no `MS_NEGATIVE` node; the start response carries a warning. |
| Prompts are just the caption | No VLM is installed or it failed; see the step's warnings. Install one under Configuration → Models. |
| Images are slow to start | ComfyUI's queue is shared with Image to Video and the storyboard. |

The rules engineers must follow when changing this feature are in [`.claude/rules/t2i.md`](../.claude/rules/t2i.md).
