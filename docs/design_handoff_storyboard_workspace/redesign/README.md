# Storyboard redesign — three directions

Built on the same components and fixtures as `ui_kits/storyboard/`, so the only variable is organisation. Each answers the same brief: the current editor is dense, related controls are separated, and space is spent on the wrong things.

## What all three fix

| Current fault | Fix in all three |
| --- | --- |
| A beat's action is shown in the bottom pane, edited on the right, and repeated in the Preview tab | One editable home per field; no duplicate surfaces |
| Image candidates on the right, rendered takes at the bottom | Stills and takes reviewed in the same place |
| Beat duration on the right, 15s cap total on the left | Duration sits next to a live cap meter |
| Camera = three unlabelled selects in a row | One "Camera / Move" group: motion + amplitude + speed as one control |
| Header = 8 flat buttons across 5 pipeline stages | Compose + Generate promoted; the rest behind an overflow (A, B) or turned into the stage bar (C) |
| Panel grid squeezed to ~90px at 820px tall | No fixed bottom pane; the board or the shot owns the vertical space |

## A · Timeline — `A-timeline.html`

Duration becomes spatial. Scenes stack as rails; each shot is a card whose width tracks its runtime, with beats as proportional segments inside it and a cap bar under each. The whole film is visible at once without scrolling a strip. One 400px inspector holds everything about the selected beat, grouped: Action & cast · Camera · Still (prompt + candidates together) · Sound & dialog · Shot (LoRAs, video prompt, takes).

Best if the main job is pacing and coverage — seeing whether the cut works.

## B · Shot workspace — `B-workspace.html` — **chosen direction**

One object, one place. A 270px outline rail lists scenes and shots; the centre is the selected shot as a document. The shot header carries its action, cap meter, both LoRA stacks, the compiled video prompt and its takes. Below it each beat is a full-width card holding *everything* that beat owns — framing, timing, cut, camera, cast on the left; prompt, candidates, sound, dialog on the right. No tabs, no bottom pane, no right panel.

Best if the main job is authoring one shot properly, with zero hunting.

### Changes applied after review

- **A's pacing strip grafted in.** The shot header now carries proportional beat segments — width tracks each beat's share, the trailing dashed block is unused clip budget, a warn-coloured left edge marks a hard cut, and the whole strip *is* the 15s cap at 1:1 scale. Clicking a segment selects that beat and brings its card up.
- **The compiled video prompt moved behind a `Video prompt` button.** It is reference output, not a field, so it no longer eats header space: the dialog gives it a 640px column, 13px/1.6 mono, up to 52vh of height, a Copy button, a word count, its lint warnings in a warn panel, and Compile / Render inline. Lint-warning count still surfaces on the header as a warn chip.
- **The reclaimed space went to LoRAs.** Image and video stacks now sit side by side at 236px minimum each and grow downward, so 2–4 entries per shot are readable without truncation.
- **Takes got bigger** (176×99 rather than 128×72) and sit beside the LoRA stacks, since comparing takes is a primary job.
- Selected beat now reads as selected: primary border, 1px ring, primary "BEAT n" label.
- **Every beat prompt has an optional expand-to-dialog.** The inline 6-row box stays for quick edits; a maximize icon button on the Prompt label opens the same dialog shell as the video prompt — 576px wide, 16 rows of 13px/1.6 mono, the beat's framing and action as context above, plus Copy, Re-synth and a word count. It edits a local draft, so Cancel discards; \`Save prompt\` commits and locks the prompt against the next Synthesize pass.

## C · Pipeline stages — `C-pipeline.html`

Grouped by stage instead of by object. A stage bar (Outline · Scenes · Shots · Beats · Prompts · Stills · Video) shows real progress per step and is also the action surface — each stage owns its own run button. The body switches to the right review surface: an editable table for beats, a per-beat list for prompts, a keeper-picking review for stills, a per-shot compile-and-render list for video.

Best if the main job is batch work — fixing timing across 40 beats, or picking keepers in one pass.

## Fidelity notes

All three reuse the design system's own components, so nothing here introduces new visual vocabulary: same tokens, same 8px cards with no shadow, same 1px selection ring, same eyebrow labels, same PrimeIcons. Backend behaviour is faked the same way as the UI kit — job badges are pre-seeded on three beats, run buttons no-op.
