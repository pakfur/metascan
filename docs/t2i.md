[← Back to README](../README.md)

# Text to Image

The **Text to Image** dialog turns a caption into a prompt and renders it through a ComfyUI workflow. Open it with the sparkles icon at the top right of the main screen, next to **Storyboards** (desktop only).

A caption comes from one of two places:

- **Manual** — you type it.
- **Random Caption** — the server draws it from `data/t2i_captions/t2i_captions.csv`, optionally narrowed by column filters.

The pipeline is always the same:

1. **Resolve** — character-name tokens (`__ALICE__`, …) and body-part tokens (`__HAIR__`, …) become seeded, deterministic character descriptions.
2. **Rewrite** — the local VLM turns the resolved caption into a prompt in the selected model's style (Krea 2, Qwen-Image, SDXL, Z-Image).
3. **Render** — a registered ComfyUI workflow renders it. Every image is its own job with its own seed.

Generated images are ordinary library media. They appear in the strip at the bottom of the dialog, and each one remembers the dialog contents that produced it.

## The form

| Control | What it does |
|---|---|
| **Caption** | The description to expand. Plain text; tokens are optional. A collapsible *Resolved caption* shows exactly what the VLM will be given. |
| **Generate Prompt** | Resolves the caption with the current seed and writes the prompt into **Prompt**. Review-only: nothing is queued. In Random Caption mode the next **Generate** renders that prompt as its first step (see Batches). |
| **Manual / Random Caption** | Where captions come from. In Random mode the 🎲 button loads one caption so you can preview it; **Generate** renders that caption only if you have also pressed **Generate Prompt** for it. |
| **Filter** | Random mode only. Nudity, the three scores, Males / Females, Aspect Ratio and Clothing, with a live "N captions match". |
| **Model** | Krea 2, Qwen-Image, SDXL or Z-Image. Chooses the prompt style and the default workflow. |
| **Workflow** | Every registered `t2i` workflow. |
| **Size** | Pixel budget in megapixels. |
| **Aspect Ratio** | Manual: your choice. Random: taken from each caption's row (read-only while a batch runs). |
| **Seed** and policy | The seed, plus how it advances: **Fixed**, **Increment**, **Decrement** or **Randomize**. |
| **LoRAs** | Same editor as Image to Video. Needs an `MS_LORA_STACK` node in the workflow. |
| **Prompt** / **Negative** | The text that will be rendered. **Negative** appears only for models that write one (Qwen-Image, SDXL) and is sent only if the workflow has an `MS_NEGATIVE` node. |
| **Batch Size** | How many captions a run uses; the first is your prompt when one is ready. Locked at 1 in Manual mode. |
| **Count per Batch** | How many images each caption renders, one seed apiece. Above 1 needs a non-Fixed policy. |

## Batches

**Generate** starts a batch on the server, so it keeps running if you close the dialog or reload the page; reopening the dialog reattaches to it. **Cancel** stops every running batch and cancels its unfinished jobs.

- **Manual:** one step — your caption and prompt — rendered `Count per Batch` times.
- **Random Caption:** for each of `Batch Size` steps the server picks an unused caption, takes its aspect ratio, writes a prompt, then renders `Count per Batch` images. The one exception is the **first step when the Prompt box holds a prompt you generated with *Generate Prompt* or wrote yourself, and no batch has rendered it yet**: that prompt, with the Caption, Aspect Ratio and Negative in the boxes, is step 1 — nothing is drawn and no prompt is written for it, so what you reviewed is what renders — and the remaining steps are drawn as above. A prompt a batch has already used, including the last step a finished run hands back in the boxes, is never reused: the next **Generate** draws every caption itself. Closing and reopening the dialog keeps a prompt ready; reloading the page forgets it, so press **Generate Prompt** again first. While it runs, the Caption, Prompt, Aspect and Seed boxes show the current step and cannot be edited. Only one Random batch runs at a time.

Seeds advance across the whole run according to the policy and stop the run early, with a message, rather than leave `0` to `2147483647` (the batch then plans fewer images and the seed box is left alone, because no unused seed remains). Under **Randomize** the first image uses the seed shown in the dialog, so it reproduces that prompt, and the rest are drawn at random. The character description for a step is drawn from that step's **first** image seed, so the same caption and seed always give the same cast. With **Fixed**, every step uses the same seed and therefore the same cast. When a run ends, the seed box shows the next unused seed.

**GPU order** follows the existing `comfy.unload_vlm_during_generation` setting:

- **On (default):** all prompts are written first, then the VLM is unloaded, then the images render. The first image appears only after the last prompt is written. A Manual **Generate** also unloads the VLM, so the next **Generate Prompt** reloads it.
- **Off:** each step renders as soon as its prompt is ready, overlapping the next prompt.

Jobs are submitted through a small window (`t2i.window`, default 4 unfinished jobs per batch) so a large batch cannot bury Image to Video and storyboard jobs in ComfyUI's shared queue.

Batch state lives in memory. If the server restarts mid-run, steps that were not yet submitted are dropped; jobs already queued still finish and their images are ingested, but without a saved form.

## Caption files (`data/t2i_captions/`)

`t2i_captions.csv` needs the columns `Caption` and `Aspect Ratio`. `Nudity`, `Artistic Quality`, `Erotic Score`, `Pornographic Score`, `Males`, `Females` and `Clothing` are optional; each one present enables its filter. The file is git-ignored — it is your data.

### Tokens

A token is a name in capitals between double underscores.

- **Characters** — `__ALICE__ __BELLA__ __CLARA__ __DIANNA__ __EMMA__` (female) and `__ADAM__ __BOB__` (male). The first time a character appears it is replaced by a generated description; later mentions become a short handle tied to the same character.
- **Body-part tokens** — `__HAIR__ __BREASTS__ __VAGINA__ __PENIS__` stand in for the word itself and belong to a character (`her __HAIR__`). They resolve from a list of the same name.
- **Anything else** — `__TOKEN__` resolves from `token.txt` if that list exists, as one value used everywhere in the caption; otherwise it becomes the plain lowercase word.

Malformed underscores (`____ALICE____`) are tolerated. A caption with no tokens passes through unchanged.

### Lists

Each characteristic is a text file with one value per line; blank lines and `#` comments are ignored and duplicates are dropped.

| File | Used for |
|---|---|
| `age.txt`, `ethnicity.txt`, `skin.txt`, `eyes.txt`, `face.txt`, `hair.txt`, `body.txt` | The character description. |
| `<name>.female.txt` / `<name>.male.txt` | Override `<name>.txt` for that gender. The shipped body lists are `body.female.txt` and `body.male.txt`. |
| `breasts.txt`, `vagina.txt`, `penis.txt` | Body-part tokens. **Not shipped** — supply your own. A missing list makes the token its plain word. |

Write values so they read after `with`: `an oval face`, `green eyes`, `olive skin`, `an athletic build`. Hair values must end in ` hair` (`auburn hair`) — the short handle for a later mention is built from it (`the auburn-haired woman`).

Lists reload automatically when a file changes. **Editing or reordering a list changes which value a given caption and seed picks.**

**Adult only.** Any line in an `age` list containing a number under 18, in digits or spelled out (`twelve-year-old`), is rejected. Any line in any list containing a minor-indicating word, phrase or word family (`teen…`, `child…`, `loli…`, `school girl`, plurals included) or an age under 18 (`14-year-old`, `aged 12`) is rejected. The `nouns` and `names` in `characters.yml` are screened the same way. It is a word screen for careless edits, not a promise about what a list can express: keep your lists adult. A line containing a parenthesis is rejected too, because parentheses never belong in a generated prompt. Rejections are logged and listed in the `wildcards.warnings` of `GET /api/t2i/config`.

### `characters.yml`

Optional; defaults are built in. It sets the names, the noun for each gender, which slots are drawn and in what order, which slots read as `<age> <ethnicity> woman` versus `with …`, which tokens belong to characters, and which words before `hair` mean body hair (`pubic`, `body`, …), where the plain word is used.

### How a description is placed

- If the next word is a verb, the description goes inline: *A 31-year-old West African woman with olive skin, brown eyes and an athletic build sits on a bench.*
- If the next word is a possessive, `with`, `and`, punctuation, or the name is part of a compound subject (`X and Y`), only the head noun goes inline and the details move to one trailing sentence. Parentheses are never used — ComfyUI parses them as weighting.
- A slot the caption already writes out with a token (`__HAIR__`) is left out of the description.
- The owner of a body-part token is the character a pronoun points at (`his` / `her`), otherwise the character named nearest before it. This is a heuristic: in a caption with several people the worst case swaps who gets which hair colour.

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

- Click a thumbnail to load the dialog contents that produced it (mode, filter, caption, model, workflow, size, aspect ratio, seed, prompt, negative, LoRAs). Edits save automatically into that image. **Stop editing** returns to a scratch form. Seed policy, Batch Size and Count per Batch are never restored, so selecting an image cannot trigger a large re-run.
- **Generate** with an image selected first saves your pending edits into it, then leaves editing mode before the seed advances (the form keeps its values), so the automatic seed advance never rewrites the saved form of the image it came from. Thumbnails cannot be selected while a Random batch runs.
- New images reach the library grid while the dialog is open *and* after you close it, without the dimming "Loading media…" overlay.
- Double-click to view; **★** stars it (the library's favorite flag); **✕** deletes the file and its library entry.
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
