[← Back to README](../README.md)

# Background activity

Metascan does a lot in the background: importing and embedding your library, tagging images with the vision model, generating images and videos, upscaling, and training. The activity pill and grid show all of it in one place and let you pause or stop any of it.

## The pill

The pill sits next to the search bar (and in the mobile header). It spins with a count of the processes that are running, and shows a red dot when one has failed. Click it to open the grid; click elsewhere or press Escape to close it. The grid follows the pill when you scroll.

## The grid

The grid has one panel per process, in a fixed order: Import, Embedding, Tagging, Text to image, Image to video, Upscaling, Storyboard, Library hygiene, Taste training and Writer training. Each panel shows its state, a progress bar when there is one, a line saying what it is doing, and a play / pause button (play on a paused, stopped, failed or idle panel; pause on a running, waiting or held one). Panels that can be stopped have a stop button. A button that does not apply to that process is disabled.

## Running, held, paused, stopped

- **Running**: working now. **Waiting**: has work but cannot do it yet (for example the vision model is not ready). **Idle**: nothing to do.
- **Held**: the app paused it for you because something heavier is running. Upscaling, generation and training hold Tagging and Embedding so the GPU is theirs, and Embedding holds Tagging. The panel names what is holding it. It resumes by itself the moment the holder ends.
- **Paused**: you paused it. It stays paused until you resume it, and it stays paused across a server restart.
- **Stopped**: you stopped it. Nothing starts it again behind your back (a scan finishing does not restart Embedding, say); press play to start it. Queued work is kept.
- **Failed**: the last run failed; the panel shows why. Play tries again.

A process told to pause or stop may need a moment to finish what it is saving. It then shows as running with a line like "Saving, then pausing" until it has really stopped.

## What pause and stop mean, per panel

- **Import**: pause and stop both switch the folder watching off (a file already being imported finishes first); play switches it back on and catches up on everything that changed meanwhile. Pause is not offered during a Full clean & rescan, and stopping one is the same as cancelling it (it asks first): your ratings stay safe and pending, and nothing is deleted.
- **Embedding**: pause saves what is done and exits; the rest stays queued. Resuming reloads the CLIP model, which takes a little while.
- **Tagging**: pause and stop both take no new images and let the ones in flight finish (the panel says "Stopping after the current images" until they have). Stop keeps the queue on disk, and nothing is tagged until you press play, not even after a restart or a finished index build; new images keep joining the queue. Starting a retag yourself also clears a stop. A retag while paused stays paused.
- **Text to image, Image to video, Storyboard**: pause lets the job already in ComfyUI finish ("Pausing after the current job") and holds the rest. Stop cancels the batch.
- **Upscaling**: pause stops the file being worked on, and it starts again from the beginning on resume.
- **Taste training**: pause lets the run in progress finish, then stops automatic retraining. Training from the Config window still works.
- **Writer training**: pause saves a checkpoint at the next step. Stop cancels the run and deletes its checkpoints, so it asks first.
- **Library hygiene**: pause and stop both end the check at the next step. Play runs a check as soon as nothing else is running (see below).

## Drop a file and what happens

Import is always on. It looks at your scan folders (the ones in Config -> Directories) about every 5 seconds; a very slow or network drive is looked at less often, so it is never hammered. There is no Import button. Metascan checks folders by listing them, not by waiting for the operating system to announce changes, because those announcements are not reliable on network drives, WSL2's `/mnt/d` or exFAT disks.

1. **Wait until the file is done.** A file is picked up only after its size and modification time have stayed the same for two checks, it is not empty, and it can be opened. A download or copy still in progress (`.part`, `.crdownload`, `.tmp`, `.download`) and system junk (`Thumbs.db`, `desktop.ini`, `.DS_Store`, `._*` files) are ignored. Expect a new file to appear on a local drive within about 15 seconds.
2. **Import.** Its metadata, thumbnail and perceptual hash are read, one file at a time, and the grid shows it without any click.
3. **Embed.** A few seconds after the last file, Embedding builds the similarity data for the new files.
4. **Tag.** The vision model tags new images when it is free.

The Import panel shows what it is doing (a count and a file name, never a full path). If a file cannot be read, Import tries it once more the next time it changes and then lists it as a warning; one bad file never stops the rest. Output of Text to image and Image to video is imported by those features, with its prompts, not by the folder watching.

If a scan folder cannot be seen (an unplugged drive, a renamed or unmounted folder), Import shows **Waiting** and touches nothing in it. Nothing is deleted because a folder is out of sight: when it comes back, everything is as it was. Set `watch_directories` to false in `config.json` to turn the watching off.

## Moved and renamed files

If you move or rename a file or a whole folder from your file manager or the shell, Metascan recognises the file by its size, modification time and picture content, and keeps everything with it: ★, grades, reasons, categories, folders, tags, prompts, similarity data and generated-image links. The same item shows at the new path with its ✓. A match must be unambiguous: if two files could each be the moved one, Metascan treats the new file as new rather than guess. The old row is only given up after a minute without a match.

## What Library hygiene does

Library hygiene is the housekeeping panel. It runs by itself when the server is quiet: nothing else is running, you have not changed anything for 2 minutes (looking around does not count), and the last pass was 30 minutes ago. It stops when something else starts. Its checks, in order:

1. **Missing files.** Rows whose file is gone are matched against new files first (so a move is a move, not a deletion), then removed from the library. A removed image's grade, reasons, categories and prompt history stay recorded as a tombstone, as when you delete from inside Metascan. It only removes a row once the file has been gone for a minute, and never while a Full clean is unfinished. It never removes anything under a folder it cannot see, nor under a Text to image or Image to video output folder that is missing or unreadable.
2. **New files.** Files in your folders the library does not know are handed to Import.
3. **Changed files.** A file edited in place is read again.
4. **Missing derived data.** Missing thumbnails and perceptual hashes are made, and unembedded or untagged images are queued for Embedding and Tagging. Generated images are not tagged after the fact, and an image the vision model gave up on is left alone until you retag it.

The panel says "In sync" with the time of the last pass and how many files it cleaned.

### The red "press play to confirm" panel

If more than a quarter of a folder's files, and more than 100, are missing in one pass, Hygiene assumes something is wrong (a drive, a mistaken delete) rather than that you meant it. It cleans nothing in that folder, cleans the others as usual, and the panel turns red: "N files missing from <folder>: press play to confirm". The line is cut short in the panel when the folder path is long; hover it to read it all. If the files are really gone, press **Play**: that confirms exactly the number the panel shows and cleans them. If they are not, reconnect the drive or restore the folder; the next pass (or Play) finds nothing missing and clears the warning. If more files go missing than the panel showed, it asks again.

Play on Hygiene means "as soon as nothing else is running": it skips the quiet-time waits but never runs beside another process, and the panel shows Waiting until then.

## Pausing and stopping Import

Pause or stop Import and nothing is picked up, moved or imported: drop a file and nothing happens. Press play and Import catches up on every file added, moved or removed meanwhile, so nothing is missed. Hygiene does not import new files while Import is paused or stopped either, but it still keeps moved files' ratings and removes the rows of deleted files.

## Full clean & rescan

Config -> Maintenance -> "Full clean & rescan..." is the repair tool: it empties the database and imports every file again, keeping your ★, grades, reasons, categories and briefs. It is refused while a scan folder cannot be seen. Its progress shows in the Import panel, and its Done step lists folders it could not see and generated files it could not bring back. It is the only explicit import left.

## Platforms

Metascan was developed and tested on Linux (WSL2). The Windows and macOS handling (case-insensitive file names, Unicode name forms, files locked while being copied, system junk files) was tested only with simulated inputs in the automated tests, never on Windows or macOS themselves. Expect rough edges there and report them.
