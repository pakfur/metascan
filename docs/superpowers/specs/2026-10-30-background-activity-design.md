# Background activity: shared protocol, activity grid, always-on import

Status: draft for review. "Owner" is the person using Metascan. Items marked
**(builder's call)** are open; items marked **(call)** were decided while writing
this document, not by the owner, and can be overturned.

## 1. Why

Today the owner copies files into a library folder, runs Import, and stops using
the app while it runs. They also run a full reimport from time to time just to be
sure the database and disk agree. Meanwhile, background work is invisible: VLM
tagging of 23,756 entries ran for hours with nothing in the UI, no pause, and no
safe stop.

What the owner wants, in their words: drop a file in a folder; a few seconds later
it appears in the library; a short time after that it is scanned, embedded and
tagged. All of it visible behind a spinner, all of it pausable and stoppable, and
none of it interrupting use of the system.

## 2. Goals

1. A title-bar widget that shows background work is happening and opens a grid of
   panels, one per process.
2. One shared protocol for start, pause, resume, stop, status and errors. A new
   background process plugs in by implementing it, with no new UI code.
3. Processes can pause each other, or keep each other from starting, and release
   them when finished.
4. Import becomes a watcher plus background work. No explicit import.
5. Idle-time hygiene keeps the database and file system in sync, so the periodic
   full reimport is never needed.
6. Moved and renamed files keep their ratings, tags and history.
7. Everything OS-specific has Windows, macOS and Linux implementations.

## 3. Non-goals

- Not a job scheduler: no priorities, cron, or user-defined ordering.
- Not a log viewer. A panel shows one line; detail stays in the server log.
- T2I and I2V output keep their existing ingest. The auto-index path stays
  CLIP-tagged (`allow_vlm_tagging=False`); this project does not VLM-tag generated
  output.
- No per-item control (no reordering or cancelling one file in a queue).
- No move detection for files that leave the library and return later.
- Not replacing `gpu_guard` or `GPU_FREE`. They stay as the backstop.

## 4. Anti-goals: this fails even if it works, if...

- A drive that is missing, unreadable or an empty mount point causes anything to
  be treated as deleted.
- A rating, tag or category is lost by a move, a rename, a pause or a restart.
- A moved file is matched to the wrong item. When unsure, it is treated as new.
- Anything a user stopped comes back by itself.
- Background work slows down what the owner is doing on the GPU.
- Queued work is thrown away by a pause, a hold, a render or a restart.

## 5. The protocol

Lives in `backend/services/background/`. The registry is installed from the
`lifespan` and is the only thing the routes and the UI talk to.

### 5.1 States

| State | Meaning | Who leaves it |
|---|---|---|
| `idle` | Nothing to do | The process, when work arrives |
| `running` | Working | The process, or any command |
| `waiting` | Has work, blocked by something outside the protocol (ComfyUI down, VLM loading, drive missing) | The process |
| `held` | Paused by another process; shows who | Automatic, when the last holder releases |
| `paused` | Paused by the owner | Only the owner |
| `stopped` | Stopped by the owner | Only the owner |
| `failed` | Last run failed; keeps the error | Owner's play, or the next successful run |

### 5.2 Owner's rules (decided by the owner)

- Any process can pause others that should not run alongside it, or keep them from
  starting, and un-pause them when it finishes.
- An un-paused process resumes if it has work, otherwise it stays idle.
- A stopped process can only be stopped by the owner and is never resumed by a
  background process.

### 5.3 Refinements (call)

- `hold` is reference counted by holder. A process resumes only when its last
  holder has released.
- A process's declared `holds` apply only while it is `running`. A held, paused or
  waiting process holds nothing. The registry rejects a cycle in `holds` when a
  process registers, so two processes can never wait on each other.
- A hold with no process behind it (Chat) has a label only, shown in `held_by`. The
  registry owns its grace timer, which runs however the request ended, including a
  cancel, and always releases.
- Owner intent outranks holds. Pausing a held process makes it `paused`; when
  holders release it stays `paused`. Pressing play while held clears the owner's
  pause and it stays `held` until released.
- A stopped process ignores holds and releases entirely.
- `paused` and `stopped` persist across restarts in `data/background_state.json`
  (atomic write). Holds never persist; the holder re-establishes them after a
  restart if it is still working.
- A holder that ends in any way (finish, failure, crash, cancel) releases its
  holds. Holds are context-managed, and the registry drops holds from a process
  that is no longer active.
- What Stop means:
  - **Queue processes** (Import, Embedding, Tagging, Hygiene): the current run ends
    at the next safe point, queued work stays durable, and nothing is processed
    until the owner presses play.
  - **Run processes** (T2I, I2V, Storyboard, Upscaling, Writer training, Taste):
    today's cancel. A process in `stopped` does not auto-start, and the owner
    starting a new run clears it, since that is the owner's own action.
- Writer training Stop deletes checkpoints, so it alone asks for confirmation. Every
  other Stop is immediate.

### 5.4 The interface

```python
class ProcessCapabilities:    # what the panel's buttons may do
    can_start: bool           # play from idle ("run now")
    can_pause: bool
    can_stop: bool

class ProcessStatus:          # the panel, in full
    id: str
    label: str
    order: int
    state: State
    progress: Progress | None # done, total, unit; None = no bar
    activity: str             # the one line; never prompt text or a full path
    held_by: list[str]        # labels of holders
    error: str | None
    warnings_count: int
    capabilities: ProcessCapabilities
    updated_at: float

class BackgroundProcess(Protocol):
    id: str
    label: str
    order: int
    capabilities: ProcessCapabilities
    holds: tuple[str, ...]            # process ids held while this one is active

    def status(self) -> ProcessStatusFields: ...
    async def start(self) -> None: ...      # owner's play from idle / stopped / failed
    async def pause(self) -> None: ...      # returns when the pause is requested
    async def resume(self) -> None: ...     # also the release of a hold
    async def stop(self) -> None: ...
```

`pause` covers both owner pause and hold; the registry owns the distinction, and
the process only implements "stop taking new work" and "take work again". A process
reports changes by calling `registry.notify(id)`.

`holds` is the declared conflict list. For a hold with no process behind it (chat),
`registry.hold(targets, by="Chat")` is a context manager.

### 5.5 Transport

- `GET /api/background` returns every status plus server time.
- `POST /api/background/{id}/{start|pause|resume|stop}` returns the new status.
  `start`/`resume` on a process that cannot run returns 409 with a `code`.
- WS channel `background`, event `process_updated` (one status), coalesced to at
  most 2 per second per process.
- The browser refetches `GET /api/background` from `onWsConnect`. `process_updated`
  is never the only source of truth.
- Existing channels and routes stay. Processes wrap them; dialogs that read them
  keep working.

## 6. The widget

### 6.1 Pill

A small rounded pill at the far left of `ContentSearchBar`. It spins with a count
while anything is `running`, sits still when nothing is, and shows a red dot when
anything is `failed`. (call) Held, waiting and paused do not spin, since nothing is
happening, but the pill stays visible at all times.

(call) It also appears in the mobile top bar (`MobileShell.vue`) and the storyboard
header, since moving Import away would otherwise leave mobile and storyboards with
no view of this.

### 6.2 Grid (owner approved mockup)

Clicking the pill opens a dropdown grid, three columns, one panel per process.
Ten panels, so a fourth row (the owner said a 3 × 3 grid would suit; storyboard
makes ten). One column on mobile.

Each panel: status dot, name, play/pause button, stop button, short progress bar,
one line of current activity. Idle panels are dimmed. Buttons a process's
capabilities exclude are disabled. Dot colours: running green, waiting blue, held
and paused amber, failed red, idle or stopped grey. The line says why: "Paused for
Text to image". Clicking outside closes it.

The frontend is generic: one Pinia store and one panel component, rendering
whatever `GET /api/background` returns. Nothing in it knows a process by name,
except a confirm flag on the status for Writer training's Stop.

The panels, in order:

| Panel | Includes | Pause means | Held by |
|---|---|---|---|
| Import | Watcher, ingest, Full clean as a run inside it | Watching and ingest stop. Resuming catches up. Not available during a Full clean | none |
| Embedding | Index builds, `AutoIndexScheduler` | Worker exits after its save (see 6.3) | T2I, I2V, Storyboard, Upscaling, Writer training, Chat, Full clean |
| Tagging | Tag queue, retag | No new images; in-flight finish | T2I, I2V, Storyboard, Upscaling, Writer training, Chat, Embedding, Full clean |
| Text to image | T2I batches | No new submissions; a running job finishes | none |
| Image to video | I2V batches | Same | none |
| Upscaling | Upscale queue | Existing pause (see 6.3) | Writer training refuses instead |
| Storyboard | Synthesis, compile, compose, story engine | No new submissions | none |
| Library hygiene | Idle checks | Between checks | Any `running` process |
| Taste training | Taste model, tombstone re-embedder | Auto-retrain off; a run finishes | none |
| Writer training | Training runs, briefs, scorecard | Existing pause at a step boundary | none |

(call) A panel may aggregate more than one mechanism, so the retag job, the
tombstone re-embedder, hindsight briefs and scorecard do not each get a panel.

### 6.3 Limits the panel must not hide

- **ComfyUI cannot pause a running job.** Pause for T2I, I2V and Storyboard holds
  back submission through a per-source gate in `ComfyClient._pump_loop`, which
  already holds queued jobs while `gpu_reserved()`. The running job finishes.
  The panel says "Pausing after the current job" until it does.
- **Embedding has no pause.** Pause uses the existing cancel path: the worker saves
  and exits, and `has_embedding` keeps the remainder durable. Resume starts an
  incremental build, which costs a CLIP reload. Never suspend a process with a
  signal; there is no Windows equivalent.
- **Upscale pause restarts the current file.** It kills the worker and resets the
  task to 0. It stays that way; the panel says so.
- **Storyboard synthesis, compile and compose have no cancel today.** Stage 1 adds a
  cancellation point between beats and panels. They run as untracked background
  tasks with no status GET, so the process needs its own run tracking.
- Scan, embedding progress, VLM drain, storyboard and retag have no status GET. Each
  process keeps its current state in memory, so the protocol's GET is the first way
  to read them.

## 7. Hold rules

- Declared on the holder. T2I, I2V, Storyboard and Upscaling hold Tagging and
  Embedding while active. Embedding holds Tagging, because they run one after the
  other today and no one has checked both fit alongside `qwen38-27b`.
- The owner's actions always win. Background work never delays them. A render
  that holds Tagging submits at once; it does not wait for the hold to land.
- **Chat, Playground and writer prompting** hold Tagging for each request plus
  `INTERACTIVE_GRACE_S = 60` (call), since a chat has no clear end. Requests already
  in flight finish. The existing eviction and retry logic in `VlmRoles` stays.
- **Writer training** holds Tagging and Embedding, and keeps refusing to start
  (`gpu_busy`) for foreground work. `index_build` stops being a refusal reason once
  the hold has landed. **(builder's call)**: how long start waits for that.
- Existing blocked checks (`reembed_blocked`, hindsight `blocked()`) stay. They are
  fine-grained safeguards that panels report as `waiting`.
- A render's VLM unload no longer drops the tagging queue: see 8.
- An owner's **Unload** of the general VLM in Config sets Tagging to `paused`, with
  the line "Paused: model unloaded". (call) That keeps the rule that an explicit
  unload is never undone by background work.

## 8. Durable tag queue

The queue moves from `vlm_pending.jsonl` to a table. This is what makes tagging
pausable, and it fixes four problems:

- The file is emptied when a batch starts, so a restart loses the rest. The current
  run's 21,000 remaining entries exist only in server memory.
- After an unload, every remaining image fails at once and is dropped. `Unload` and
  any render do this today.
- Entries pile up with no de-duplication: 23,756 entries for a library of about
  8,285 images.
- "Re-tag library" targets CLIP-tagged files only, and this library has none.

New table `vlm_tag_queue(file_path PK REFERENCES media ON DELETE CASCADE, enqueued_at,
attempts, last_error)`. Migration v8 creates it and imports any leftover
`vlm_pending.jsonl` lines that have a media row, then removes the file.

- A row is removed only after its tags are written, or after `MAX_TAG_ATTEMPTS = 3`.
  A file that no longer exists is removed without a failure count.
- "VLM not ready" holds the queue; it is never an item failure.
- The embedding worker enqueues through the database.
- Retag becomes an enqueue, so its progress is the Tagging panel. `POST /api/vlm/retag`
  keeps its shape.
- Scope `all_clip` also includes images with no VLM tags. **(call)** That also gives
  the current run's lost items a route back, as does hygiene (10).
- `should_tag_with_vlm` stays the gate. On tiers without VLM tagging, the panel is
  idle and says CLIP tagging is in use.

## 9. Always-on import

### 9.1 Why polling

`metascan/core/watcher.py` is not connected to anything, and `watch_directories`
does nothing. Native file notifications are not reliable for this library: it is on
`/mnt/d`, a 9p mount in WSL2, where changes made from Windows are not expected to
raise inotify events (not tested on this machine). Network and exFAT drives have
similar problems elsewhere.

(call) One polling implementation on every OS, built on `os.scandir`, so there is a
single code path to get right. Listing 13,583 files on `/mnt/d` took about 0.2 s,
probably helped by caching. Remove `watcher.py`.

- `watch_directories` turns it on. Default stays true.
- The interval is `max(WATCH_INTERVAL_S = 5, 20 × last walk's duration)`, so a slow
  or network drive is never hammered.
- On start, and on resume from pause, the first pass compares disk against the
  database, so changes made while it was off are caught up.

### 9.2 Pipeline

1. **Detect.** A new path enters a settling list.
2. **Settle.** It counts once size and mtime are unchanged across two polls and it
   can be opened for reading. On Windows, a sharing violation means "still copying".
3. **Identify.** Check whether it is a moved file (11).
4. **Ingest.** `Scanner.ingest_file` on one background thread, one file at a time:
   metadata, phash and thumbnail, no GPU. A `library` WS event, coalesced to one per
   2 s, tells the grid to show the new items.
5. **Embed.** When settled files have ingested, Embedding runs an incremental build
   with `allow_vlm_tagging=True`, debounced.
6. **Tag.** Embedding enqueues images, and Tagging takes them when free.

Target: a file on a local drive appears within about 15 s of being dropped. A
thousand-file drop ingests steadily and never blocks the UI.

- Each failed file is retried once on the next scan of it, then counted in
  `warnings_count`. Import does not fail for one bad file.
- A path under a generated output root (`_generated_output_roots`) is watched like
  any other. ComfyClient ingests its own output with lineage, and Import skips a path
  that already has a row. (Amended 2026-10-31: the roots were skipped at first, which
  hid every file under them from Import and hygiene alike when a root sits inside a
  scan folder.)
- A configured folder that is unavailable (`_probe_folder`) sets Import to `waiting`
  and nothing in it is touched.

### 9.3 What goes away

- The Import button in `ContentSearchBar`, its `@scan` wiring and Ctrl+S.
- `ScanDialog` as an everyday flow. Its Full clean & rescan stays as a repair tool,
  reached from Config. (call) The Done view stays with it, since it is where
  `missing_folders`, `unreachable_output` and `unreadable_output` are shown.
- `scan.ts` stays for Full clean and `SimilaritySettings`.
- Hygiene shows the same findings in its panel line.

### 9.4 Full clean

Full clean is a run inside Import, not a process of its own. While it runs, Import
suspends its own watching (no self-hold) and holds Embedding, Tagging and Hygiene,
releasing Embedding for the build that follows the walk. `capabilities` are dynamic:
`can_pause` is false during a Full clean. Stop on it is the existing cancel, with all
the cancel rules in `CLAUDE.md`: marker and pending file kept, no tombstones. All of
its ordering and rules in `CLAUDE.md` are unchanged. A full clean in progress is not
merged with any new work.

## 10. Library hygiene

A list of named checks run one at a time, with a shared `HygieneCheck` interface so
a new one is one class. Stoppable between checks and between rows.

**Idle** (call): no process `running` (Import counts as `running` only while it
ingests, never for polling alone), no non-GET API request for `IDLE_AFTER_S = 120`,
and at least `HYGIENE_INTERVAL_S = 1800` since the last complete pass. GETs do not
count: an open tab polls, and browsing does not compete with a light file check. Play on the panel means run now, ignoring both timers. It runs
once at startup when idle, which catches anything that changed with the server off.

Checks:

1. **Missing files.** Rows whose file is gone are cleaned automatically, keeping
   ratings through `delete_media_batch`, so tombstones are written (owner's
   decision). It reuses `_stale_paths_to_clean`: folders checked after the pass, a
   final stat, nothing under an unavailable folder.
2. **New files.** Anything the watcher missed. Runs the `_reingest_generated_files`
   pass for generated output.
3. **Changed files.** Size or mtime differs from the row: re-ingest and reset the
   embedding. Ratings stay, since the path is the same.
4. **Missing derived data.** No phash or thumbnail: regenerate. Not embedded: Embedding
   picks it up. An image with no VLM tags on a VLM tier: enqueue for tagging.
5. **Finish.** `apply_pending_taste_snapshot`, and the marker rule below.

**Mass-removal guard (owner agreed).** When a pass would clean more than 25% of a
folder's rows and more than 100 rows, it cleans nothing there and sets `failed` with
"1,240 files missing from D:\Media: press play to confirm". Play applies the cleanup
to that folder only. Other folders in the pass clean as normal. This is the backstop
for a partially mounted or half-synced drive, which the folder probe cannot see.

**Full-clean interplay.** While the marker or pending snapshot exists, hygiene may add
rows but cleans nothing. A hygiene pass counts as an ordinary scan for the marker: it
removes the marker under the same conditions (`completed` and `walked_folders > 0`),
through the same code, not a copy. `_run_scan` logic it needs is extracted, not
duplicated.

Moved-file grace: a row is not cleaned until `MOVE_GRACE_S = 60` after its file
vanished, so check 1 cannot race move detection.

## 11. Moved and renamed files

Nothing in the app changes a media path today. Every child table keys on `file_path`
with `ON DELETE CASCADE` only, and FAISS is keyed by the same string. A move today
is a delete plus a new item.

### 11.1 Identifying a move

When a path vanishes, its row goes on a candidates list for `MOVE_GRACE_S`. Each
settled new file is compared against the candidates:

1. Same size and mtime, one candidate: a move, confirmed by phash when both exist.
2. Same size, different mtime (a copy tool that does not preserve it): a move only if
   the phash matches and there is exactly one candidate.
3. Anything else, including several candidates or a phash mismatch: not a move.

Same-looking duplicates are the failure this guards against, so ambiguity is always
"new file". A renamed folder matches many files in a single pass and is handled as one
batch.

The stored values are `file_size` and `modified_at` in the media JSON blob, so no new
column is needed. `modified_at` is compared as a number after reading either
historical shape, within 2 s (FAT and exFAT round mtimes). Videos have a first-frame
phash. A file with no phash can only match by rule 1.

**During an incomplete Full clean.** Moves still apply to rows that exist, and the
batch also re-keys any entry for the old path in the pending snapshot, through
`taste_snapshot.py`. A path with no row (still pending) cannot be recognised as moved
and follows the existing rules. This is an accepted limit.

### 11.2 Applying a move

`db.move_media_batch(pairs)`: one transaction, foreign keys deferred, re-keying
`media` (and the `file_path` in its JSON blob) and every table that holds a media
path: hashes, indices, judgments, categories, folder items, briefs, taste scores,
tag queue, T2I / I2V lineage, storyboard references. `judgment_events` is re-keyed so
score lookups still work; tombstones are history and are not.

A test walks the schema and fails on any `*_path` column not on the move's list or an
explicit exclusion list, as `test_grades_guard.py` does for grades.

Moves and the target row: if the target path already has a row, it is not a move.

### 11.3 FAISS

The embedding worker is the index's one writer, and the server's copy can lag. A
pending-renames table, applied by the embedding queue only when no worker runs, then a
save, then the usual reload. Search results pass through the pending map until then.
Each rename stamps `mark_tombstone_paths_rewritten` for the old and new paths. Never
re-embed to get a rename; a renamed folder would cost a lot of GPU time.
**(builder's call)**: the exact shape, as long as `CLAUDE.md`'s tombstone-vector rules
hold.

## 12. Cross-platform

All OS-specific behaviour goes in `metascan/utils/fs_compat.py`, with one tested
implementation per OS. The rest of the code calls it.

| Concern | Windows | macOS | Linux |
|---|---|---|---|
| Path comparison | Case-insensitive; `_path_key` | Case-insensitive by default; Unicode NFC vs NFD | Case-sensitive |
| "Still copying" | Sharing violation on open | Size and mtime | Size and mtime |
| Ignore list | `Thumbs.db`, `desktop.ini`, `~$*` | `.DS_Store`, `._*`, `.Spotlight-V100` | Dotfiles |
| Partial downloads | `.crdownload`, `.part`, `.tmp` (all OSes) | Same, plus `.download` | Same |
| Mount detection | Drive letter present | `/Volumes/<name>` | `_probe_folder` empty mount rule |
| Pause / stop a subprocess | Files and exit codes, no signals | Same | Same |

No OS-specific API for file watching, idle detection or GPU checks. Existing `HF`,
`fsync` and subprocess handling stays.

**Testing limit:** this machine runs Linux on WSL2. Windows and macOS behaviour is
covered by unit tests with simulated inputs, and has not run on those systems. The
hand-over must say so.

## 13. Build order

Each stage ships on its own, on a feature branch, behind `make quality test` and
`npm run build`.

**Stage 1: protocol and grid.** Registry, routes, WS channel, pill, grid. All ten
panels, wrapping what exists. Durable tag queue (8), hold rules (7), and pause for
everything in 6.2. Import panel wraps the existing scan: `can_pause` is false until
Stage 2, and Stop is the existing scan cancel. Hygiene panel is a stub. This alone
gives pause and stop for tagging.

**Stage 2: always-on import.** Polling watcher, pipeline, moved files (11), `fs_compat`.
Remove Import button, move Full clean to Config.

**Stage 3: library hygiene.** Checks, idle rule, mass-removal guard, full-clean
interplay.

## 14. Documents to update

- New `.claude/rules/background.md`: how to add a process, the hold rules.
- `CLAUDE.md`: the `background` and `library` WS channels, migration v8, the changed
  import flow.
- New `docs/background-activity.md`, linked from `README.md`.
- `docs/` pages describing Import.

## 15. Open to the builder

- Module layout inside `backend/services/background/`.
- How `library` events update the grid (`mediaStore`).
- Exact debounce for the embedding step.
- Pill and panel styling, following the owner-approved mockup and the app's theme.
