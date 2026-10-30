# I2V dialog rework — design

Source request: `docs/i2va-ui-rework.md` (layout sketch and change summary).
Date: 2026-10-30.

## Why

The Image to Video dialog grew as one long column, with the clips in a strip
at the bottom, while the Text to Image dialog has a cleaner shape. The owner
wants the two to feel alike, wants to see every video they have made while
working on one, and wants a run to write several prompts on its own. Separately,
edits to a form can vanish without warning in both dialogs when a tile is
clicked; that must stop.

## Goals

1. A three-panel I2V dialog: source image and its clips, the form, all videos.
2. Form organised like the T2I dialog.
3. A Batch and Count run owned by the server, like T2I's.
4. A RAW / STRUCTURED view of the prompt, with full editing in RAW, a copy icon,
   and line breaks that survive the edit popup.
5. A warning before unsaved form changes are lost, in **both** dialogs.

## Non-goals

- No change to prompt format, templates, lint, Apply fixes, grades, ★, clip
  `form_state`, output placement or presets beyond what is written here.
- No T2I layout change. T2I gets only the lost-changes fix (goal 5).
- No T2I-style Variants, Random Caption or Caption directions in I2V.
- No new way to open the dialog: it still opens from an image.

## Anti-goals

- A prompt silently rendered on the wrong source image.
- A click that discards unsaved work with no warning.
- An automatic form change (a batch, a seed advance) written into a clip's saved
  form.
- Any change to the stored prompt from switching RAW / STRUCTURED.
- A second representation of the prompt: `prompt` stays one string everywhere.

## Layout

Card about 1500px wide (`min(1500px, 96vw)`), height behaviour as T2I's: the form
decides the height, up to 94vh, and the side panels never make the row taller.
The close ✕ is still the only way out; no backdrop close.

**Left panel.** The source image at the top, then its own clips, newest first,
in a vertical scrolling list: job tiles first, then clip tiles with today's three
detail lines. Everything the bottom strip does today moves here unchanged: click,
double-click, ▶, ★, ✕, the grade badge, the selected outline.

**Middle panel: top to bottom.**

1. Editing bar ("Editing the clip from…", Stop editing), sticky as in T2I.
2. Cadence (right-aligned per the sketch) with its chips and notes.
3. Idea, then a row under it holding **Writer** and **Generate Prompt**, as under
   T2I's Caption box.
4. Prompt area with [RAW][STRUCTURED] at the top right, then Apply fixes and the
   lint findings, placed under the prompt as T2I places its lint.
5. Duration, Quality, Steps, Size, Seed (T2I's `SeedControls`: seed, dice, policy
   dropdown), then Categories.
6. LoRAs.
7. Run row, sticky at the bottom as in T2I: Batch, Count, Cancel, Generate, with a
   status line beneath.

**Right panel.** "Videos": every I2V video in the library, newest first, two
columns, a page of 60 with Show more. Same tile as the left list. Finished clips
only; job tiles appear only in the left list.

## Generate Prompt

**Stays**, by decision: it is the way to read and fix a prompt before spending
render time. This overrides the line in `docs/i2va-ui-rework.md` that removes it.
It behaves as it does today.

## Batch and Count

Batch is how many prompts a run uses; Count is how many videos each prompt renders.
Total videos = Batch × Count, shown on the button (`Generate ×12`).

- **The Prompt box decides batch 1** (T2I's Random rule). Text in the box: batch 1
  renders it with no writer call, keeping the held draft's lineage. Empty box:
  batch 1 gets a written prompt too. Batches 2..N are always written.
- **Generate works with an empty box**, unlike today. The Idea is optional, as it
  is for Generate Prompt. The writer uses the dialog's Writer, Idea, Cadence and
  Duration.
- **Written prompts do not go into the box.** The form is left as it was; a run
  does not take it over. A written prompt is seen by clicking its clip. Pressing
  Generate again with an empty box writes fresh prompts.
- **Count needs a non-Fixed seed.** With Fixed, Count is disabled and treated as 1,
  and each batch renders one video with the same seed and its own prompt.
- **Limits:** Batch 1..20, Count 1..20 (`i2v.max_batch_size`,
  `i2v.max_count_per_batch`, reported by `GET /api/i2v/config`). Batch and Count
  are session-local like the seed policy, never part of `form_state`.
- **Seed:** the server plans every seed up front by policy, in render order. A run
  whose policy would leave `0..2^31-1` is refused before anything happens. The
  response carries `next_seed`, which the form takes unless the user changed the
  seed meanwhile.
- **"Nothing has changed" confirm:** only when the run would repeat one identical
  render (box text, Batch 1, Count 1 in effect, unchanged signature).

## Server-side run

New `POST /api/i2v/batches`, `GET /api/i2v/batches`,
`POST /api/i2v/batches/{id}/cancel`, in the shape of T2I's, on `I2vRunner`.
Mirror T2I's approach in a compact form inside `I2vRunner`; do not refactor
`T2iRunner` into a shared base.

- The request is a snapshot of the form plus `batch_size`, `count_per_batch`,
  `seed_policy`, `writer`, `category_ids` and the held draft's lineage. Nothing
  typed afterwards reaches the run. The route declares `GPU_FREE`, like `/generate`.
- **Order:** with `comfy.unload_vlm_during_generation` on (the default), all prompts
  are written first, then the VLM is unloaded (only when no other I2V or T2I run is
  still writing), then jobs are submitted. Off: each prompt renders as soon as it is
  ready.
- **Queue:** at most `i2v.window` unfinished jobs per run (default 4), released on
  terminal `job_update` events, so a large run cannot starve other work.
- **A prompt that fails to write** ends the run with what was already queued left in
  place, and the status says how far it got. Same for a job the server refuses.
  `gpu_busy` from training mid-run is treated this way.
- **Cancel** stops the unfinished writing and cancels the run's jobs. The footer
  Cancel covers every run and job for the open source image.
- **Events** on the `i2v` channel: `batch_progress`, `batch_done`, `batch_failed`,
  `batch_cancelled`, each with `{batch_id, source_path, ...}`. The dialog shows
  `Batch k/N · writing prompt` / `rendering` for the open source image. On open it
  rebuilds from `GET /api/i2v/batches`, so closing the dialog loses nothing.
- Lint warnings on written prompts come back on those events and show under the run
  row, as T2I's batch warnings do. They carry no Apply fixes: that is for the box.
- **A server restart loses a run's unwritten prompts**, as with T2I. Jobs already
  submitted continue as they do today.
- The auto-index `generation_busy` probe counts I2V runs that are still writing.
- Lifespan shutdown closes I2V runs after `comfy_client.shutdown()`, the existing
  event-source-before-consumer order.

The dialog no longer calls `POST /api/i2v/generate`. Remove the route if nothing else
does (check the storyboard callers and tests first); otherwise leave it.

## Selecting a clip

**A click on a clip in either list switches the whole dialog to that clip:** its
source image in the left panel, that image's clips and jobs, its form. Clicking the
selected clip does nothing.

- `source` in the store changes in place; no remount. Session-local choices stay
  (Writer, Categories, seed policy, Batch, Count).
- A run keeps going when the dialog moves to another image. Its jobs are back in the
  left list when that image is open again.
- **A clip whose source image was deleted** loads its form. The left panel shows a
  "source image deleted" placeholder, still lists its clips, and Generate and
  Generate Prompt are disabled with a note saying why. `i2v_videos` keeps these rows
  by design.
- The source's `Media` comes from the media store by path; a path not found there is
  the deleted case.
- **Generate lets go of the selected clip first**, as T2I does (flush, then deselect,
  before the seed advances). Today a seed advance autosaves into the selected clip.

**Double-click must not trigger the single-click action.** It would now pop the
unsaved-changes confirm and switch the dialog just to play a video. In both dialogs
a tile's single click waits about 250 ms and a double-click cancels it. ▶ remains
immediate and never loads. T2I's `ThumbTile` needs the same change.

## Unsaved changes (I2V and T2I)

**The rule:** before a clip or image click replaces the form, if the form holds
changes that exist nowhere else, ask with a native `confirm()` (both dialogs already
use it): "You have changes that haven't been generated. Load this {clip|image} and
lose them?" Cancel leaves everything as it was.

**What counts as unsaved.** Compare the form with a baseline, on the fields a load
replaces (session-local choices are not compared).

The baseline moves to the current form when:

- the dialog opens with default values;
- a clip or image is loaded, or Stop editing is pressed (the values are saved);
- a Generate is accepted by the server, taken *after* the seed advance, so the
  automatic seed change never counts. In T2I, a Random run's unlock patch counts too.

A T2I draft restored from browser storage is **not** a baseline: it is unsaved work,
so it compares against the defaults. A written but unrendered prompt counts as a
change, and is the case that matters most.

**A selected clip or image never asks**, because its edits autosave into it, unless
its last save failed (`saveFailed`), in which case the confirm says the changes
could not be saved.

**Closing the I2V dialog asks too**, under the same rule: it has no scratch draft, so
✕ loses the form as a click would. T2I's ✕ does not ask, since it keeps its draft.

The check runs after each dialog's existing refusals (a blind tile, a locked Random
batch), so a refused click never asks. Share the comparison in one small composable
used by both dialogs.

## RAW and STRUCTURED

- A view switch only; it never writes to `prompt`.
- **STRUCTURED:** Header (read-only, no edit control, always), Prompt, Soundscape,
  Non diegetic music. Each editable panel has the edit-popup control.
- **RAW:** one box with the full document, fully editable, with the edit-popup
  control and a **copy icon** that copies the whole text and confirms with a toast.
  The copy icon is in RAW only.
- The choice is remembered per browser (`metascan.i2v.promptView.v1`), default
  STRUCTURED.
- **Prompt that can't be structured.** When `parseI2vPrompt` returns null on a
  non-empty prompt, the dialog stays in RAW, the STRUCTURED button is disabled, and a
  note says the H3 field labels were changed or removed so it can't be shown as
  structured. This also covers a panel edit that breaks the structure (a pasted field
  label). Generate still works: it sends the text as it is.
- **The view never flips while typing.** Fixing the labels re-enables STRUCTURED; the
  user presses it to go back.
- **Empty prompt:** shows RAW with STRUCTURED disabled and no warning note, because a
  prompt with no structure has nothing to split. This differs from the sketch.

## Line breaks

`TextEditPopup` collapses newlines on purpose and has nine other callers, so it gets
an opt-in `multiline` prop, default off. All four I2V uses set it. Update its header
comment and the rule in `.claude/rules/i2v.md` that says each H3 field is one line.

`_DESCRIPTION_RE` in `metascan/core/i2v_compiler.py` stops at the first blank line,
so a blank line in the Prompt panel cuts the description short: the word-count lint
and the `[Shot n]` scan see only part of it and warn falsely. The description must
end at `\n\noverall_soundscape:` (or end of text), not any blank line. Add tests.

Neither MiniMax guide says how H3 treats line breaks. The writer's output stays one
line; only the owner's own edits carry them.

## Data

`GET /api/i2v/videos` gains the all-videos form: `source_path` optional, plus `limit`
(default 60) and `before_id`. It is a read-only inner JOIN on `media`, newest first,
paged by id, and **prunes nothing**. The per-source listing keeps its prune, under
the existing lineage guard. Rows carry `source_path` and go through
`form_state_for_row` as the per-source list does.

No schema change.

## Store

`stores/i2v.ts` holds the two lists. ★ and grades reach a clip in either list:
`applyJudgment`, `applyFavoriteFlag`, `toggleFavorite`, and `rowNotes` for each
refresh. `i2v_videos_changed` refreshes the right list for any source and the left
list only for the open one. Each list opens the viewer on its own snapshot.

## Reused and left to the builder

Reuse `SeedControls`, `JobTile`, `CategorySelect`, `LoraListEditor`. Extract the clip
tile into one component for both lists; its detail lines don't fit `ThumbTile`.
Dialog file split, CSS, exact toast wording, event field names and test layout are
the builder's. `docs/features.md`, `docs/api-reference.md`, `docs/configuration.md`,
`.claude/rules/i2v.md` and `.claude/rules/t2i.md` (which says I2V doesn't use the
shared pieces) are updated with the work.

## Testing

- Backend: run planning (seeds, box rule, refusal at the range end), order with the
  VLM unload on and off, window, cancel, a failed write mid-run, all-videos paging and
  that it never prunes, the `_DESCRIPTION_RE` change.
- Frontend: there is no test runner, so pure logic (the unsaved-changes comparison)
  is unit-tested with Node type stripping in scratch.
- A full-stack smoke run in the isolated rig: switching source from each list, the
  confirm in both dialogs, double-click to play, a dirty ✕, a deleted source, closing
  the dialog during a run and reopening, RAW / STRUCTURED, line breaks through the
  popup.
- `make quality test` and `npm run build`.

## Calls made without the owner

- Selected clip edits don't ask, since they are autosaved.
- ✕ on the I2V dialog asks; T2I's doesn't.
- Written prompts stay out of the box and the form isn't locked during a run.
- A selected clip is released on Generate.
- Empty prompt shows RAW.
- Limits of 20 × 20, `i2v.window` default 4.
- A delayed single click (about 250 ms) on tiles in both dialogs.
- Job tiles in the left list only.
