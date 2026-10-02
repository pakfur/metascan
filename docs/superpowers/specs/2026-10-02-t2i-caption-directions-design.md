# T2I caption directions — design

Date: 2026-10-02
Status: design only. It builds on `feature/caption-classifier`, which is not merged, and
it waits for the full classifier sweep and more manual testing.
Depends on: `docs/superpowers/specs/2026-10-01-caption-classifier-design.md`

## 1. Purpose

Many captions in `data/t2i_captions/t2i_captions.csv` never name an emotion,
or imply a sexual act without saying what it is, so the image model is left to
guess. The caption classifier scores every caption for these gaps. This
feature uses those scores **each time a prompt is written**: when a score
passes its threshold, a short prose snippet is picked from a wildcard list and
handed to the local prompt-writing model as a **direction** next to the
caption. The model weaves it into the prompt it writes.

The caption text itself is never changed. That matters: a caption's
characters are drawn from a hash of its exact text (`t2i_characters`), so any
edit would re-roll the cast.

### Success criteria

- Captions without emotional context get a fitting expression in the generated
  prompt. Which expression it is varies with the seed.
- Captions with a confidently implied act get a prompt that states the act
  explicitly and plausibly.
- Captions without classification data behave exactly as they do today, as do
  captions below every threshold and runs with directions switched off. The
  system and user prompts are byte-identical to the current ones.
- Thresholds and snippet text can be changed without touching the code or the
  captions.

### Non-goals

- Fixing the captions that the plausibility check flags. That stays a manual
  review.
- Editing caption text in the CSV.
- New Random-mode filters on the classification columns. They would be easy
  to add later and are left out here.

## 2. Pipeline today, and where directions fit

```
caption ──resolve_caption(caption, seed, style, library)──▶ resolved text
        ──compose_t2i_prompts(profile, resolved, content_mode)──▶ (system, user)
        ──VLM──▶ prompt, negative          (no VLM: fallback_prompt(resolved))
```

With directions:

```
caption ──▶ CaptionStore.find(caption) ──▶ CaptionRow (+ classification fields) or None
                     │
                     ▼
        build_direction(row, seed, content_mode, settings, snippets) ──▶ Direction | None
                     │
caption ──resolve──▶ resolved ──compose(…, direction.text)──▶ VLM ──▶ prompt
                                     (no VLM: resolved + " " + direction.text)
```

## 3. Data: classification columns in the captions CSV

A one-off **merge tool**, `scripts/caption_classifier/merge.py`, appends
classification columns to the captions CSV. It runs once after the full sweep,
and again after any re-sweep.

| Column | Values | Source |
|---|---|---|
| `Caption SHA1` | 40 hex characters | the hash recorded at classification time |
| `Emotion Explicit` | 0–1 | `emotion.C` |
| `Emotion` | `none` / `implicit` / `explicit` | the most likely emotion label |
| `Kiss` | 0–1 | `kiss.Y` |
| `Partner` | `none` / `male` / `female` / `unknown` | the most likely partner |
| `Act` | an act name (rubric) | top gated act |
| `Act P` | 0–1 | its gated probability |
| `Act Conflict` | `true` / `false` | `act_gate_conflict` |
| `Issues` | issue types joined with `;`, or empty | `issues[].type` |

How the merge tool works:

- **Safe matching.** Rows are matched by row id, and a row's columns are
  filled only when the caption's current SHA1 equals the recorded one. A row
  edited since classification, an error row or an unclassified row gets blank
  cells, and blank means "no direction".
- **The original file is never overwritten in place.** The merged file is
  written beside the CSV (`t2i_captions.merged.csv`) along with a summary of
  rows filled, rows skipped and why. `--replace` then keeps the original as
  `t2i_captions.csv.bak-<timestamp>` and swaps the merged file in with an
  atomic rename. `CaptionStore` reloads on its own when the file's mtime and
  size change.
- **It can run again.** Existing classification columns are replaced, never
  duplicated.

`CaptionStore` changes (`metascan/core/t2i_captions.py`):

- **Optional columns.** The new columns join the existing optional ones,
  matched by name with case and spacing ignored, and stored in compact numpy
  arrays: float32 for the probabilities, small integer codes for the labels.
  A CSV without them works exactly as it does today.
- **`CaptionRow` gains one field:** `classification: Optional[Classification]`,
  a frozen dataclass with the fields above, or `None` when the row's cells are
  blank.
- **`find(caption: str) -> Optional[CaptionRow]`** looks a row up by its exact
  text. The index from SHA1 to row id is built the first time `find` is
  called, by one pass over the file (about a second for ~83k rows), and is
  rebuilt when the file changes. If the CSV has a `Caption SHA1` column, that
  column is used and no text needs reading. With `find`, a caption that is
  typed, loaded with the dice or drawn in Random mode all get the same
  direction.

## 4. Snippets: wildcard lists per act, kissing and emotion

The snippet lists live in a new directory, `data/t2i_captions/directions/`,
kept out of the top level so they never become `__TOKEN__` wildcards. The
directory is hot-reloaded the same way `LibraryCache` reloads the lists.

| File | Used when |
|---|---|
| `emotion.txt` | the emotion threshold passes and the caption's erotic score is below `emotion_sensual_from` |
| `emotion.sensual.txt` | the emotion threshold passes and the erotic score is at or above `emotion_sensual_from` (falls back to `emotion.txt`) |
| `kissing.txt` | the kiss threshold passes |
| `act.<act-name>.txt` | the act threshold passes, for each of the 17 real acts (`breast-fondling` … `ff-tribbing`) |
| `act.<act-name>.pov.txt` | optional; preferred over the plain file when `Partner` is `male`, `female` or `unknown`, meaning the partner is out of frame |

`none-artistic` and `unclear` never get a direction.

**File format.** It matches the existing lists: one snippet per line, `#`
comments allowed, blank lines skipped. A snippet is a plain prose instruction
to the prompt writer, for example:

- `act.doggy.txt`: "she is on her hands and knees and he takes her from behind, his hips pressed against her buttocks"
- `emotion.txt`: "give each person a facial expression that suits the moment, such as a soft, unguarded smile"

Snippets contain no `__TOKEN__`s and no parentheses.

**Adult-only screen.** Every snippet line goes through the same check as every
other caption list, `t2i_wildcards._reject_reason`, and a rejected line is
skipped with a warning. This keeps the load-time adult-only invariant intact.

**Picking a snippet.** The line is chosen deterministically from the seed:
`sha256(f"{seed}|direction|{file_key}|0")` modulo the list length, the same
scheme plain wildcards use. The same seed always gives the same snippet, and a
new seed gives fresh variety. A missing or empty file means that part of the
direction is left out, with a warning.

## 5. Thresholds and settings

The settings live under `config.json` → `t2i.directions`:

```jsonc
{
  "t2i": {
    "directions": {
      "enabled": true,              // default for the dialog toggle
      "emotion_missing_min": 0.70,  // 1 − Emotion Explicit ≥ this → emotion snippet
      "emotion_sensual_from": 0.60, // Erotic Score ≥ this → emotion.sensual.txt
      "kiss_min": 0.80,             // Kiss ≥ this → kissing snippet
      "act_min": 0.80,              // Act P ≥ this → act snippet
      "skip_act_on_conflict": true  // no act snippet when Act Conflict is true
    }
  }
}
```

**Implicit emotion also triggers the snippet.** The emotion check uses the
probability that the caption has *no explicit* emotion (none plus implicit),
so a caption that only describes gaze or posture still gets an expression.

**Kissing is added alongside an act.** The kissing snippet is independent of
the act snippet, so a caption can get both.

Missing keys fall back to these defaults. A value outside 0–1 is clamped,
with one warning in the server log.

**Content mode.** When `t2i.content_mode` is `sfw`, act and kissing snippets
are suppressed and emotion snippets always come from `emotion.txt`. A
direction must never work against the safety directive.

## 6. Building the direction

A new pure module, `metascan/core/t2i_directions.py`, does no I/O beyond the
cached snippet loader:

```python
@dataclass(frozen=True)
class Direction:
    text: str                 # sentences handed to the prompt writer
    parts: Tuple[str, ...]    # e.g. ("emotion", "act:doggy:pov", "kissing")
    warnings: Tuple[str, ...]

def build_direction(
    row: Optional[CaptionRow], seed: int, content_mode: str,
    settings: DirectionSettings, snippets: SnippetLibrary,
) -> Optional[Direction]
```

It returns `None` when the row is missing, the row has no classification,
directions are disabled, or no part passes its threshold. The parts are
built in a fixed order: act, then kissing, then emotion. Each part becomes
one sentence, ending with a full stop and capitalised. The result is reported
in `parts` so it can be seen and tested.

## 7. Prompt composition

`compose_t2i_prompts(profile, resolved_caption, content_mode, direction=None)`:

- Without a direction, it returns exactly what it returns today.
- With one, the user turn becomes:

```
DESCRIPTION:
<resolved caption>

DIRECTION:
<direction text>

Write the prompt now.
```

`T2I_CAPTION_PREAMBLE` in `data/meta_prompt.yml` gains one rule:

> If a DIRECTION block follows the description, carry it out: work each
> instruction into the prompt naturally, in the guideline's style. A
> direction adds detail but never overrides the description. Where they
> disagree, the description wins.

**Fallback without a VLM.** `fallback_prompt` takes the same optional
direction and appends its text to the resolved caption before the SDXL prefix
logic, so directions still have some effect without a prompt writer.

## 8. Runner, API and dialog

`T2iRunner` (`metascan/core/t2i_runner.py`):

- **Random steps.** `_random_step` has the drawn `CaptionRow` in hand. For a
  named first caption it calls `CaptionStore.find`. It then builds the
  direction with the step's prompt seed (`seeds[0]`) and passes it to
  `_write_prompt`, which passes it on to `compose_t2i_prompts` or
  `fallback_prompt`.
- **Manual prompts.** The prompt-writing path (`prompt(...)`) does the same
  with `find(caption)`.
- **Toggle.** A request field `directions: bool` controls all of this; it
  defaults to `t2i.directions.enabled`.
- **Visibility.** `PromptResult` and `_Step` gain `direction: Optional[str]`
  and `direction_parts: List[str]`. Both are returned by the prompt endpoint
  and carried on the `batch_step` frame, and every applied direction is
  logged at INFO with the row id.
- **Nothing new is stored per image** (decided). The direction already shows
  in the prompt it produced, and that prompt is stored as it is today.

Frontend (`T2IDialog`):

- **Checkbox.** A "Caption directions" checkbox, autosaved like the other form
  fields, sends `directions`.
- **Read-only line.** Under the Prompt box, a line shows `Direction: …` with
  the parts, for example `act: doggy (pov) · emotion`, whenever the current
  prompt was written with one.
- **Display only.** The line follows the existing rule that nothing is written
  into the form from a watcher.

## 9. Error handling

- **Missing or broken input.** A missing snippet directory, a missing file, an
  unreadable file or a rejected line each give a warning on the step. The
  generation still goes ahead with whatever parts are available, possibly
  none.
- **No classification.** A CSV with no classification columns is a silent
  no-op: no warning, because that is the normal state before the merge.
- **Slow index build.** A `find` index build that fails or takes too long
  falls back to no direction, logs a warning, and never blocks a batch.

## 10. Testing

Unit tests, following existing patterns:

- **`t2i_directions`:**
  - each threshold at its boundary, including an `implicit`-only caption triggering the emotion snippet
  - kissing and an act together
  - the order of the parts
  - `sfw` suppression
  - choosing `emotion.sensual.txt` and falling back from it
  - preferring the `.pov` file and falling back from it
  - seed-stable snippet choice, and that different seeds vary it
  - missing and empty files
  - a row with no classification
  - disabled directions
- **Snippet loader:**
  - the adult-only screen rejects minor-indicating lines, with tests written
    before any new terms are added
  - parentheses are stripped
  - hot reload
- **`CaptionStore`:**
  - the new columns are parsed and blank cells give `None`
  - `find` uses the `Caption SHA1` column when present and the text hash
    otherwise
  - `find` rebuilds after the file changes
  - an old CSV without the new columns behaves as before
- **`compose_t2i_prompts` / `fallback_prompt`:**
  - with no direction, output is byte-identical to today, pinned by a test
  - with a direction, the user turn has the expected layout
- **Runner:**
  - a Random step and a Manual prompt each carry the direction to the
    (fake) VLM user turn, and report it on `batch_step` and in the response
  - with `directions: false`, nothing changes
- **`merge.py`:**
  - columns are filled only where the SHA1 matches
  - running it again replaces rather than duplicates the columns
  - `--replace` keeps a timestamped backup and the swap is atomic
  - error and unclassified rows stay blank

**Manual evaluation.** Use hand-written captions in the CSV's style, never
captions taken from the library, as the project's evaluation rule requires.
A small set covers:

- no emotion
- an implied act, in-frame and POV
- kissing
- a caption that already names its emotion, which should get no emotion snippet

Each is rendered with the same seed with directions on and off, comparing
both the prompt and the image. `scripts/t2i_prompt_probe.py` can be extended
with a `--directions` flag that prints the direction next to each prompt.

## 11. Build order

1. `merge.py` and the `CaptionStore` columns plus `find`. These can be tested
   on the 519-row trial.
2. The snippet loader and `t2i_directions`.
3. The `compose_t2i_prompts` / `fallback_prompt` / preamble rule.
4. Runner wiring and the API fields.
5. The dialog checkbox and the Direction line.
6. Starter snippet files, written by the user, and the manual A/B runs.

Steps 1–5 can be built before the full sweep. Thresholds are tuned after it,
using `classifications.csv`.

## 12. Decisions settled on 2026-10-02

- **Implicit emotion triggers the emotion snippet:** the check is
  1 − `Emotion Explicit` ≥ `emotion_missing_min` (§5).
- **Kissing is added even when an act snippet fires** (§5).
- **No per-image storage** of the applied direction (§8).
