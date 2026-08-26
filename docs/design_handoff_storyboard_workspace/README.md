# Handoff: Storyboard workspace redesign (Metascan)

## Overview

This is a reorganisation of Metascan's Storyboard authoring surface (`/storyboard/:id?`). It is **not a new feature and not a visual rebrand** — every colour, radius, font size and control style is the one the app already ships. What changes is *where things live*.

The problem being solved, in the user's words: the UI is dense and poorly organised, logically related elements are not together, and space is used poorly. Concretely, in the current build:

| Fault in the shipping UI | Where it lives today |
| --- | --- |
| A beat's action appears in three places | `BeatsEditor` list, `BeatForm` editor, `PanelSidePanel` Preview script |
| Image candidates and rendered clips are reviewed in different panes | `BeatImages` (right panel) vs `PanelVideos` (bottom pane) |
| A beat's duration is edited far from the 15s cap total it feeds | `BeatForm` (right) vs `BeatsEditor` label (bottom) |
| Camera is one idea split across three unlabelled selects | `BeatForm` |
| Five pipeline stages are flat sibling buttons | `StoryboardView` header |
| The artifact gets the least space | at 820px viewport height, `PanelDetail` (44vh) + `SceneStrip` leave `PanelGrid` ~90px |

The redesign replaces the four-region layout (scene strip / panel grid / bottom detail pane / right tabbed panel) with **two regions**: an outline rail and one shot rendered as a document. Rule applied throughout: *one object, one place.* Nothing about a beat appears anywhere except that beat's card.

## About the design files

The files in this bundle are **design references written in HTML/JSX** — a click-through prototype showing intended layout and behaviour. They are **not production code to copy**.

The target is the existing app: **Vue 3 + TypeScript, Pinia, PrimeVue 4.5 (Aura preset), PrimeIcons 7, Vite**, at `frontend/src/`. Recreate these designs as Vue SFCs following the codebase's established patterns — `<script setup lang="ts">`, scoped styles, the CSS custom properties in `frontend/src/style.css`, and the existing store actions. The React/JSX in this bundle exists only because that is what the design tool renders; **do not port React into the app.**

The prototype's own components (`components/**/*.jsx`) are the styling source of truth — read them for exact values, then write Vue. Each has a sibling `.prompt.md` describing intent and a `.d.ts` describing its props.

## Fidelity

**High-fidelity.** Colours, spacing, type and radii are final and are taken verbatim from the shipping app. Recreate pixel-for-pixel. Every number in this document is measured from the prototype, not approximated. Where a value looks off-grid (5px, 6px, 10px, 14px, 22px, 236px, 374px) it is deliberate — the app is not on a 4px grid. Do not round.

**Backend impact: none.** This is presentational. No API route, DB column, WebSocket event or store action changes. Every mutation the redesign performs already exists in `stores/storyboard.ts`.

---

## Screens / Views

There is one screen: the board editor. The landing list (`StoryboardLanding.vue`) is unchanged and out of scope.

### Shell

**Purpose:** hold the two regions and the board-level actions.

```
html, body, #app { height: 100%; overflow: hidden }
```

- Root: `display:flex; flex-direction:column; height:100%; min-height:0`
- Header: `display:flex; align-items:center; gap:12px; padding:10px 20px; border-bottom:1px solid var(--surface-border); flex-shrink:0`
- Body: `display:flex; flex:1; min-height:0`
  - Outline rail: `flex:0 0 270px; min-height:0; overflow-y:auto; border-right:1px solid var(--surface-border)`
  - Shot document: `flex:1; min-width:0; min-height:0; overflow-y:auto; padding:18px 24px 40px`; inner column `max-width:1040px; display:flex; flex-direction:column; gap:16px`

**Header contents, left to right:**

| Element | Spec | Copy |
| --- | --- | --- |
| Back link | `font-size:13px; color:var(--text-color-secondary); text-decoration:none`; hover → `var(--text-color)` | `← Library` |
| Board title | `h2`, `margin:0; font-size:16px; font-weight:600; color:var(--text-color)`, ellipsised | board name |
| Video chip | `.ms-chip.ms-chip--primary` — `font-size:12px; padding:4px 10px; border-radius:999px; color:var(--primary-color); background:color-mix(in srgb, var(--primary-color) 12%, transparent)` | `MiniMax H3 · ref2va` |
| Progress chip | `.ms-chip` neutral, only while a job runs | `synthesizing 4/12` |
| Error chip | `.ms-chip.ms-chip--danger`, `max-width:360px`, ellipsised, with `×` dismiss | store error text |
| Action group | `margin-left:auto; display:flex; align-items:center; gap:6px` | see below |

Action group — **this is the header's main change**. Today there are eight flat buttons. Now:

1. `Compose` — quiet button, icon `pi pi-sparkles`
2. `Generate all` — **primary** button, icon `pi pi-play`
3. `⋯` — 24px outline icon button, opens a menu holding: `Import text`, `Synthesize`, `Compile video prompts`, `Generate video`, `Cancel` (Cancel rendered in `var(--danger-color)`)
4. `pi pi-cog` — 24px outline icon button, storyboard settings

Rationale: only the two things a user does every session stay visible; the stage-specific runs move behind the overflow. Keep the existing keyboard shortcuts if any are bound.

### Region 1 — Outline rail (270px)

**Purpose:** navigate the whole board; replaces `SceneStrip.vue` entirely.

Container `padding:10px 8px 24px`. Per scene, a block with `margin-bottom:10px`:

**Scene header row** — `display:flex; align-items:center; gap:6px; padding:5px 6px`
- Disclosure caret: literal `▼` / `▶`, `font-size:10px; color:var(--text-color-secondary)`
- Scene name: `font-size:12px; font-weight:600; flex:1; min-width:0`, ellipsised
- Shot count: `font-size:11px; color:var(--text-color-secondary); font-variant-numeric:tabular-nums`
- Edit affordance: 18px circular corner button, glyph `✎` at `font-size:10px`, `background:var(--surface-ground); color:var(--text-color-secondary)`; hover `background:color-mix(in srgb, var(--primary-color) 18%, transparent); color:var(--primary-color)`

**Shot row** (a `<button>`) — `display:flex; align-items:center; gap:8px; width:100%; padding:5px 6px; margin-bottom:1px; text-align:left; border:1px solid transparent; border-radius:6px; background:transparent`
- Selected: `border-color:var(--primary-color); background:color-mix(in srgb, var(--primary-color) 10%, transparent)`
- Thumb: `34×34; border-radius:4px; overflow:hidden; background:var(--surface-ground)`; keeper image `object-fit:cover`; no keeper → `1px dashed var(--surface-border)` box. A running/queued/failed job renders the fill-layout job badge over it (see Job states).
- Line 1: `font-size:12px`, ellipsised — `"1. Mara waits for the kettle and does not look at the door."` (index + `panel.action`)
- Line 2: `font-size:10px; color:var(--text-color-secondary); font-variant-numeric:tabular-nums` — `"3 beats · 6.0s"`, plus `" · 🎬2"` when `panel.videos.length > 0`

**`+ Shot`** — dashed button, `padding:3px 10px; font-size:11px; border:1px dashed var(--surface-border); border-radius:5px; background:none; color:var(--text-color-secondary)`, `margin:4px 0 0 6px`. **`+ Scene`** — same, `margin-left:6px`, after the last scene.

Selecting a shot sets the selected beat to that shot's first beat and resets the document scroll to 0.

### Region 2 — Shot document

`display:flex; flex-direction:column; gap:16px`, max width 1040px.

#### 2a. Shot header

`display:flex; flex-direction:column; gap:12px; padding-bottom:16px; border-bottom:1px solid var(--surface-border)`

**Row 1** — `display:flex; align-items:baseline; gap:10px`
- Breadcrumb: `font-size:11px; color:var(--text-color-secondary)` — `"Kitchen, dawn ›"`
- Title: `h3`, `font-size:18px; font-weight:600` — `"Shot 1"` (1-based `sort_order`)
- Meta: `font-size:11px; color:var(--text-color-secondary)` — `"3 beats"`
- Right group `margin-left:auto; display:flex; gap:8px`: three secondary buttons at `padding:5px 12px; font-size:12px` — `Reroll shot`, `Re-synth shot`, `Re-beat shot`

**Row 2 — pacing strip.** Grafted from the rejected "timeline" direction; this is the piece that makes duration legible.

- Track: `display:flex; gap:2px; height:48px`
- One segment per beat, a `<button>`:
  - `width: (beat.duration_s / scale) * 100%`, `min-width:30px`, `flex-shrink:0`, `padding:0`, `overflow:hidden`
  - `scale = max(totalShotSeconds, 15)` — so the strip *is* the 15s H3 clip cap at 1:1 until the shot exceeds it
  - `border:2px solid transparent`, selected → `var(--primary-color)`; `border-radius:5px`
  - `is_cut === 1` → `border-left:3px solid var(--warn)`
  - `background:var(--surface-ground)`; keeper image fills it `object-fit:cover` at `opacity:1` when a keeper is picked, `opacity:0.45` when not; no images → inset 2px box with `1px dashed var(--surface-border); border-radius:3px`
  - Label overlay: `position:absolute; inset:0; display:flex; align-items:flex-end; justify-content:space-between; gap:6px; padding:2px 4px; background:linear-gradient(transparent, rgba(0,0,0,0.7)); color:#fff; font-size:10px; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden`
  - **Label rule:** the beat index always renders. The duration (`"2.5s"`) renders **only when the segment's computed pixel width ≥ 44px**; below that it would collide with the index and a 0.5s beat reads as "30.5s". When suppressed, the duration stays in the button's `title` alongside the action text. Measure the track width (`clientWidth`, re-measured on resize) to decide.
  - Job badge (fill layout) when that beat has an active job
  - Click → select that beat and bring its card into view
- Unused-budget block: when `total < 15`, a trailing `div` of `width:((15 - total)/scale)*100%`, `border:1px dashed var(--surface-border); border-radius:5px`, `title="4.5s of clip budget unused"`
- Cap bar beneath: `display:flex; align-items:center; gap:8px; margin-top:4px`
  - Track `flex:1; height:3px; border-radius:2px; background:var(--surface-hover); overflow:hidden`
  - Fill `width:min(100, total/15*100)%`, `background:var(--primary-color)`, or `var(--warn)` when over
  - Label `font-size:11px; font-variant-numeric:tabular-nums` — `"6.0s / 15s"`, and when over: `"18.5s / 15s — exceeds H3 clip cap"` in `var(--warn)`

**Row 3** — `display:flex; gap:16px; align-items:flex-end`
- Field `Shot action`, `flex:1; min-width:220px` — uppercase eyebrow label + text input, commit on change → `patchPanelFields(panel.id, { action })`
- Video-prompt group, `flex-shrink:0; display:flex; align-items:center; gap:10px`:
  - `Video prompt` — secondary button `padding:5px 12px; font-size:12px`, icon `pi pi-file`. **Opens the dialog; the prompt text is no longer inline.**
  - Status: `font-size:11px; color:var(--text-color-secondary)` — `compiled` / `user edited` / `not compiled`
  - `🔒` at `font-size:11px` when `video_prompt_locked === 1`, `title="Locked against the next compile"`
  - Warn chip when `video_prompt_warnings.length` — `"1 lint warning"` / `"3 lint warnings"`, `.ms-chip--warn`
  - `Compile` — secondary sm
  - `Render video` — **primary** sm, icon `pi pi-play`, disabled when `!video_prompt`

**Row 4** — `display:flex; gap:16px; align-items:flex-start`
- `Image LoRAs` editor in a wrapper `flex:1; min-width:236px`
- `Video LoRAs` editor in a wrapper `flex:1; min-width:236px`
- `Takes (N)` field, `flex:0 0 374px`, with aside hint `"newest last · double-click to play"`

LoRA editor (unchanged behaviour from `LoraListEditor.vue`, new sizing): rows of `display:flex; gap:6px; align-items:center` — name input `flex:1; min-width:0` backed by a `<datalist>` of the ComfyUI LoRA folder, strength `<input type="number" step="0.05">` at `width:72px`, bare `✕` remove at `font-size:12px; padding:4px; color:var(--text-color-secondary)`. Then `+ Add LoRA` dashed. **The whole reason the video prompt moved into a dialog is to give these two stacks room: shots carry 2–4 LoRAs each and the names must not truncate.** They grow downward.

Takes: `.ms-candidates-row` (`display:flex; gap:10px; overflow-x:auto; padding-bottom:4px`) of tiles at **176×99** (up from 128×72 — comparing takes is a primary user job). Tile: `border-radius:6px; overflow:hidden; background:var(--surface-ground); position:relative`; image `object-fit:cover`; hover shows a centred `▶` at `font-size:20px; color:#fff` over `rgba(0,0,0,0.25)` and a 20px scrim `×` delete at `top:4px; right:4px`, both `opacity 0 → 1` over `0.15s`. Empty state: a `height:99px` box, `1px dashed var(--surface-border); border-radius:6px`, centred `font-size:12px; color:var(--text-color-secondary)` — `"No takes rendered yet."`

#### 2b. Beat card — one per beat

This card absorbs `BeatRow.vue` + `BeatForm.vue` + `BeatImages.vue`. Everything a beat owns is here and nowhere else.

`<article>`: `border:1px solid var(--surface-border); border-radius:8px; background:var(--surface-card); padding:12px 14px 14px; display:flex; flex-direction:column; gap:12px`. Selected: `border-color:var(--primary-color); box-shadow:0 0 0 1px var(--primary-color)`. Clicking anywhere in the card selects that beat. **No shadow when unselected** — cards in this app never carry shadow.

**Head row** — `display:flex; align-items:center; gap:8px`
- `BEAT 1` — `font-size:12px; font-weight:600`, `var(--text-color-secondary)`, or `var(--primary-color)` when selected
- `hard cut` — `font-size:11px; font-weight:600; color:var(--warn)`, only when `is_cut`
- Duration — `font-size:11px; color:var(--text-color-secondary); font-variant-numeric:tabular-nums`
- Job chip (neutral) showing `queued` / `running` / `failed`
- Right group `margin-left:auto; display:flex; gap:4px`: `↑`, `↓`, `✕` — 22×22 outline icon buttons, `border:1px solid var(--surface-border); border-radius:5px; background:var(--surface-card); color:var(--text-color-secondary); font-size:11px`; hover fills `--surface-hover`; the `✕` hover goes `var(--danger-color)`. Disabled at the ends: `opacity:.5; cursor:not-allowed`. All three `stopPropagation` so they don't also select.

**Body** — `display:flex; gap:14px; align-items:flex-start`

*Left column* `flex:1; min-width:0; display:flex; flex-direction:column; gap:10px`
1. `Action` — textarea, 2 rows
2. Field row (`display:flex; align-items:flex-end; gap:10px`): `Shot size` · `Angle` · `Lens` selects, each `flex:1`; `Dur (s)` number input at `flex:0 0 84px` (`step="0.5" min="0.5"`); `Cut` toggle button `padding:6px 14px; font-size:12px` — off: `background:var(--surface-ground); color:var(--text-color-secondary)`; on: `border-color:var(--primary-color); color:var(--primary-color); background:color-mix(in srgb, var(--primary-color) 12%, transparent)`
3. `Camera move` — **one field, three controls**: `display:flex; gap:6px` with motion select `flex:2`, amplitude select `flex:1`, speed select `flex:1`. This replaces three separately-labelled selects; the labels were redundant once grouped.
4. `Cast` — promoted subject chips (`display:flex; flex-wrap:wrap; gap:6px; margin-bottom:2px`; clicking a non-primary chip moves it to index 0; primary carries a `★` at `font-size:10px` and switches to primary border + primary text) over the membership checklist (`.ms-checklist`: `display:flex; flex-wrap:wrap; gap:4px 12px; padding:4px 0`, each `label` `display:flex; align-items:center; gap:5px; font-size:12px`)

*Right column* `flex:0 0 340px; display:flex; flex-direction:column; gap:10px`
1. `Prompt` — label row carries: status hint (`flex:1`) reading `synthesized` / `brief fallback` / `🔒 edited` / `—`; an `Unlock` link button (`background:none; border:none; padding:0; color:var(--primary-color); text-decoration:underline; font-size:11px`) when `prompt_locked === 1`; and a **22px outline icon button, `pi pi-window-maximize`, `title="Open prompt in a larger editor"`** which opens the beat prompt dialog. Below: textarea, 6 rows. Typing here commits and locks (`prompt_source:'user'`, `prompt_locked:1`).
2. `Candidates` — label row with `Re-synth` and `Reroll` xs buttons (`padding:3px 10px; font-size:11px`); then `.ms-candidates-row` of 96×96 tiles. Tile: `border:2px solid transparent; border-radius:6px; overflow:hidden`; keeper → `border-color:var(--primary-color)` plus an 18px primary disc top-left with `✓` at `font-size:11px; line-height:18px; color:#fff`; hover reveals a 22px scrim circle with `pi pi-search-plus`. Click toggles keeper (clicking the current keeper clears it → `selected_image_id: null`); double-click opens the media viewer. `title` = `"seed 481314106 · variant 2"`. Empty: `"No candidates yet."` at `font-size:12px; color:var(--text-color-secondary); padding:8px 0`.
3. `Sound` — textarea, 2 rows, placeholder `"ambient, effects, music"`
4. `Dialog` — `display:flex; flex-direction:column; gap:6px; padding-left:8px; border-left:2px solid var(--surface-border)`. Per line: a row (`display:flex; gap:6px`) of speaker select `flex:1` (first option `other voice` → `subject_id: null`, then one per subject), `delivery` text input `flex:0 0 84px`, and a 24px `✕`; beneath it a 2-row textarea, placeholder `"spoken line"`. Then `+ line` dashed xs.

**`+ Beat`** — dashed button `padding:6px 14px; font-size:13px`, `align-self:flex-start`, after the last card. Adding selects the new beat.

---

## Dialogs

Both use the app's existing modal shell: scrim `position:fixed; inset:0; background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:900`; card `background:var(--surface-section); border-radius:12px; padding:22px 28px 24px; width:640px; max-width:92vw; max-height:85vh; overflow-y:auto; box-shadow:0 20px 60px rgba(0,0,0,0.3)`. Scrim click cancels. Footer `display:flex; flex-wrap:wrap; gap:10px; margin-top:18px`, buttons at `padding:8px 20px; font-size:14px`.

### Video prompt dialog — read-only

Replaces the video-prompt block in `PanelSidePanel.vue`'s Preview tab. **It is reference output, not a field** — render it read-only.

- Head row `display:flex; align-items:center; gap:10px; margin-bottom:10px`: `h3` `font-size:18px` — `"Video prompt — shot 1"`; primary chip `"MiniMax H3 · ref2va"`; right-aligned hint `margin-left:auto; font-variant-numeric:tabular-nums` — `"compiled · 41 words"`; `🔒 Unlock` link when locked
- Warnings, when present: `<ul>` `list-style:disc; padding:8px 10px 8px 26px; margin:0 0 10px; color:var(--warn); font-size:12px; line-height:1.5; background:color-mix(in srgb, var(--warn) 14%, transparent); border-radius:6px`
- Body: the app's mono script block — `padding:14px; border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-ground); font-family:var(--font-mono); font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; overflow-y:auto; max-height:52vh`
- Not compiled yet: centred hint, `font-size:13px; padding:24px 0` — `"No video prompt compiled yet — Compile builds it from this shot's beats."`
- Footer note `font-size:11px; color:var(--text-color-secondary); margin-top:10px` — `"Compiled from the beats below. Edit a beat's framing, cast or dialog and recompile — hand-editing this text locks it against the next compile pass."`
- Actions: **`Compile`** (primary) · `Copy` (flips to `Copied` for 1500ms) · `Render video` · `Close`

### Beat prompt dialog — editable

Same shell, but this one **is** a field.

- Head: `h3` `"Prompt — beat 1"`; status hint; right-aligned `"37 words"` tabular; `Unlock` link when locked
- Context line `font-size:12px; color:var(--text-color-secondary); margin-bottom:8px` — `"MCU · eye · normal · push in — She sets the cup down without looking up."`
- Body: textarea, **16 rows**, `font-family:var(--font-mono); font-size:13px; line-height:1.6; padding:14px; resize:vertical`
- Footer note — `"Saving marks the prompt user-edited and locks it, so the next Synthesize pass leaves it alone."`
- Actions: **`Save prompt`** (primary; label becomes `Done` when nothing changed) · `Copy` · `Re-synth` · `Cancel`
- **Draft semantics:** the dialog holds a local copy. `Cancel` and scrim-click discard. `Save prompt` commits `{ prompt, prompt_source:'user', prompt_locked:1 }`. This differs deliberately from the inline box, which commits on change — the dialog is for composing, so it needs an escape hatch.

---

## Job states

Three visual slots, all over a thumbnail, all driven by the existing WebSocket job state. Image jobs are beat-scoped; video jobs are panel-scoped — unchanged.

- **Fill layout** (outline-rail thumb, pacing segment): `position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:11px; background:rgba(0,0,0,0.55); color:#fff`. `queued` → `⏳`; `running` → `<span class="pi pi-spin pi-spinner">`; `failed` → `⚠` with `background:color-mix(in srgb, var(--danger-color) 70%, black); cursor:help` and the error in `title`.
- **Chip** (beat card head): neutral `.ms-chip` with the state word.
- No badge for `done` — a finished job just shows its image.

## Interactions & behaviour

| Interaction | Behaviour |
| --- | --- |
| Click shot in outline rail | Select shot, select its first beat, scroll document to top |
| Click pacing segment | Select that beat and scroll its card into view. **Do not use `scrollIntoView`** — set `container.scrollTop = card.offsetTop - container.offsetTop - 12` |
| Click beat card | Select that beat (highlights the matching pacing segment) |
| Click subject chip | Promote to primary (move to index 0 of `subject_ids`) |
| Toggle subject checkbox | Add/remove from `subject_ids` |
| Click candidate | Toggle keeper; clicking the current keeper clears it |
| Double-click candidate / take | Open the media viewer over that row |
| `↑` / `↓` on a beat | Swap `sort_order` with the neighbour, computed from array positions (stored values can collide) |
| `✕` on a beat | If the beat has images, open the existing three-way `DeleteImagesDialog` (purge / keep in library / cancel); otherwise a plain confirm |
| Copy buttons | Flip own label to `Copied` for 1500ms. **Never fire a toast** |
| Text fields | Commit on `change` (blur/Enter), not per keystroke. Keep the existing local-copy-plus-snapshot pattern so a WebSocket refresh never stomps an in-progress edit (see the `syncField` comments in `PanelDetail.vue`) |
| Hover reveals | `opacity 0 → 1` over `0.15s`: corner buttons, tile delete, candidate zoom, take play |
| Surface hover | `background → var(--surface-hover)` over `0.15s` |
| Press | **Nothing.** No transform, no scale, no darkening |
| Disabled | `opacity:.5; cursor:not-allowed` |
| Mobile | Unchanged — the storyboard stays desktop-only with the existing note and a link back to the library |

No entrance animations, no skeletons, no layout transitions. Loading is the word `Loading…` or the PrimeIcons spinner.

## State management

All server state stays in the existing Pinia store; the redesign only changes local selection state.

**Component-local:**
- `selectedPanelId: number | null` — the shot in the document (store already has this)
- `selectedBeatId: number | null` — drives both the pacing strip highlight and the card ring (store already has this)
- `videoPromptOpen: boolean` — one per shot document
- `promptOpen: boolean` — one per beat card
- A ref to the scrolling document element and a map of beat-id → card element, for the scroll-to-beat maths

**Store actions used, all existing and unchanged:** `load`, `refresh`, `attachWs`, `patchPanelFields`, `patchBeatFields`, `addBeat`, `removeBeat`, `addPanel`, `removePanel`, `removeScene`, `selectImage`, `synthesize`, `generate`, `compileVideo`, `generateVideo`, `composeStory`, `removePanelVideo`, `cancelAll`.

**Removed local state:** the side panel's `activeTab`, `PanelDetail`'s dragged `detailHeight` / module-scope `savedDetailHeight`, and the `videoPromptVal`/`videoPromptSnap` pair (the field is read-only now, so there is nothing to snapshot).

## Design tokens

All already declared in `frontend/src/style.css`; `styles.css` + `tokens/` in this bundle document the full set with the base scale spelled out.

**Colours (semantic — use the variables, not the hex):**

| Token | Light | Dark |
| --- | --- | --- |
| `--primary-color` | `#3b82f6` | `#60a5fa` |
| `--surface-ground` | `#f8fafc` | `#0f172a` |
| `--surface-section` | `#ffffff` | `#1e293b` |
| `--surface-card` | `#ffffff` | `#1e293b` |
| `--surface-border` | `#e2e8f0` | `#334155` |
| `--surface-hover` | `#f1f5f9` | `#334155` |
| `--text-color` | `#1e293b` | `#f1f5f9` |
| `--text-color-secondary` | `#64748b` | `#94a3b8` |
| `--danger-color` | `#ef4444` | `#f87171` |

`--warn: #e0a030` — referenced with that fallback throughout the current code; **declare it properly in `style.css`** as part of this work. Same for `--font-mono` (currently `var(--font-mono, ui-monospace, monospace)` in two files and `var(--font-family-mono, …)` in a third — unify on one name).

Both themes ship and follow `prefers-color-scheme`. **Dark is canonical** — design and review in dark first.

**State tints** — always `color-mix`, never a new hex: primary at 10% (selected row/card fill), 12% (toggle-on fill, video chip), 18% (corner-button hover); danger at 12% (outline-danger hover), 40% (confirm border), 70%-with-black (failed badge); warn at 14% (warn chip, warning panel).

**Media scrims** (fixed black alphas, not theme-aware): `0.25` take hover · `0.5` dialog · `0.55` corner badge / job fill · `0.6` zoom & delete buttons · `0.65` job overlay. One gradient exists in the whole product: `linear-gradient(transparent, rgba(0,0,0,0.7))` for label protection over a thumbnail — used by the pacing segment labels.

**Spacing** — `2 4 5 6 8 10 12 14 16 18 20 22 24 28 32`. Not a 4px grid.

**Fixed measurements in this design:** outline rail `270`, document max width `1040`, LoRA column min `236`, takes column `374`, take tile `176×99`, beat right column `340`, candidate tile `96`, pacing track height `48`, cap bar height `3`, duration-label threshold `44`, beat-card `min-width:0` on the flexible left column.

**Type** — platform UI stack, no webfont: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif`. Sizes `11 12 13 14 16 18 20`, nothing larger. Weights 400 and 600 only. The signature object is the **eyebrow label**: `font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.4px; color:var(--text-color-secondary)` on every field in the working panes. Dialog forms use a plain `12px` sentence-case label instead. Numbers use `font-variant-numeric:tabular-nums`.

**Radii** — `4` thumbnails/overlays · `5` 22–24px icon buttons · `6` buttons/inputs/selects/tiles · `8` cards/rows · `10` dashed empty state · `12` dialogs · `999px` status chips · `50%` circular corner buttons.

**Elevation** — border-first. Cards have **no shadow**. Only two exist: dialog `0 20px 60px rgba(0,0,0,0.3)`, toast `0 8px 24px rgba(0,0,0,0.15)`. No inner shadows, no glow, no blur/backdrop-filter anywhere. Selection = primary border + `box-shadow:0 0 0 1px var(--primary-color)`.

**Motion** — `0.15s` hover/reveal, `0.2s` toast, `0.8s` linear spinner, default `ease`. Nothing else.

## Assets

- **Icons: PrimeIcons 7.0.0**, already a dependency. Used by class name (`pi pi-cog`). New usages in this design: `pi-file` (video prompt button), `pi-window-maximize` (expand prompt), `pi-play`, `pi-sparkles`, `pi-search-plus`, `pi-spin pi-spinner`. The font binaries are in `assets/icons/primeicons/` for reference only — **use the npm package in the app.** Do not hand-roll SVGs.
- **Unicode glyphs are typed literally**, not icons: `× ✕ ✓ ★ ✎ ▶ ↑ ↓ ← ▼ ▶ — ⏳ ⚠ +`.
- **Emoji are load-bearing state markers** and must survive: `🔒` locked prompt, `🎬` shot has takes, `🖼` image count, `💬` dialog lines. They are compact and deliberate — do not swap them for icons.
- `assets/placeholders/*.png` are sample frames cropped from the repo's own library screenshot, used to populate the prototype. **Not product assets** — the app renders real thumbnails via `thumbnailUrl(file_path)`.
- There is no logo. The wordmark is the word "Metascan" in the platform font at `16px/700` in `--primary-color`.

## Files

Open `redesign/B-workspace.html` in a browser — it runs standalone from this folder.

| Path | What it is |
| --- | --- |
| `redesign/B-workspace.html` | **The design to build.** Entry point |
| `redesign/RedesignWorkspace.jsx` | Its source — read for exact values and behaviour |
| `redesign/README.md` | Why this direction won, and the two rejected alternatives |
| `ui_kits/storyboard/index.html` + `*.jsx` | Recreation of the **current** UI, for before/after comparison |
| `ui_kits/storyboard/fixtures.js` | The sample board (3 scenes, 7 shots, 8 beats, 2 takes) and the real enum vocabularies |
| `styles.css`, `tokens/*.css` | The token layer, documented |
| `components/**` | Prototype components: `.jsx` source, `.d.ts` props, `.prompt.md` intent, one `@dsCard` HTML per folder |
| `_ds_bundle.js` | Compiled prototype components (build artifact; the HTML needs it) |

**Vue files this work touches, in `frontend/src/`:**

| File | Action |
| --- | --- |
| `views/StoryboardView.vue` | Rewrite layout: two regions, collapsed header action group |
| `components/storyboard/SceneStrip.vue` | **Delete** — replaced by the outline rail |
| `components/storyboard/PanelGrid.vue` | **Delete** — replaced by the outline rail + pacing strip |
| `components/storyboard/PanelSidePanel.vue` | **Delete** — Edit tab folds into the beat card, Preview splits into the two dialogs |
| `components/storyboard/BeatRow.vue` | **Delete** — merged into the beat card |
| `components/storyboard/PanelDetail.vue` | Becomes the shot header |
| `components/storyboard/BeatsEditor.vue` | Becomes the beat-card list + the pacing strip's data |
| `components/storyboard/BeatForm.vue` | Becomes the beat card |
| `components/storyboard/BeatImages.vue` | Folds into the beat card's Candidates block |
| `components/storyboard/PanelVideos.vue` | Moves into the shot header's Takes column, tiles resized |
| `components/storyboard/LoraListEditor.vue` | Keep as-is; only its container width changes |
| New: `OutlineRail.vue`, `PacingStrip.vue`, `VideoPromptDialog.vue`, `BeatPromptDialog.vue` | |
| Unchanged | `StoryboardLanding.vue`, `CreateStoryboardDialog.vue`, `OutlineDialog.vue`, `ImportTextDialog.vue`, `SceneEditDialog.vue`, `StoryboardSettingsDialog.vue`, `PresetRegistrationDialog.vue`, `DeleteImagesDialog.vue`, `ReferenceImagePicker.vue`, `stores/storyboard.ts`, `types/storyboard.ts`, `api/*` |

## Open decisions for the implementer

1. **The shot script has no home.** `PanelSidePanel.vue`'s Preview tab renders `buildShotScript` / `buildShotScriptBlocks` (from `utils/shotScript.ts`) as selectable per-beat blocks with a Copy action, and clicking a block selects that beat. Direction B has no Preview tab, and this was not re-placed. Recommendation: add a `Shot script` button beside `Video prompt` opening the same dialog shell with the existing block-per-beat rendering — it is the cheapest option and keeps the copy-to-clipboard workflow. Confirm with the designer before dropping it.
2. **Video anchor select.** `PanelSidePanel.vue` shows an `Anchor` select (`keeper` / `prev_last`) only when `video_mode` is `i2va` or `fl2va`, plus a `recompile suggested` warn chip when `video_anchor !== video_compiled_anchor`. The prototype board uses `ref2va`, so neither renders. Put the select in the video prompt dialog's head area and keep the chip on the shot header next to the lint chip.
3. **`⋯` menu component.** The prototype draws the trigger only. Use PrimeVue `Menu` in overlay mode for consistency with the library view's existing menus.
4. **Scene-level actions.** The old scene card carried render / edit / delete. The outline rail's scene header currently shows only edit. Add render and delete to a kebab, or keep them in the scene edit dialog — designer's call.
5. **Empty board.** A board with no scenes shows nothing in either region. Reuse the existing dashed empty-state box (`border:1px dashed var(--surface-border); border-radius:10px; padding:48px 16px; text-align:center; font-size:14px; color:var(--text-color-secondary)`) in the document area with copy pointing at Compose or Import text.
