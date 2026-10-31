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

- **Import**: stop only. Stopping a Full clean & rescan is the same as cancelling it: your ratings stay safe and pending, and nothing is deleted.
- **Embedding**: pause saves what is done and exits; the rest stays queued. Resuming reloads the CLIP model, which takes a little while.
- **Tagging**: pause and stop both take no new images and let the ones in flight finish (the panel says "Stopping after the current images" until they have). Stop keeps the queue on disk, and nothing is tagged until you press play, not even after a restart or a finished index build; new images keep joining the queue. Starting a retag yourself also clears a stop. A retag while paused stays paused.
- **Text to image, Image to video, Storyboard**: pause lets the job already in ComfyUI finish ("Pausing after the current job") and holds the rest. Stop cancels the batch.
- **Upscaling**: pause stops the file being worked on, and it starts again from the beginning on resume.
- **Taste training**: pause lets the run in progress finish, then stops automatic retraining. Training from the Config window still works.
- **Writer training**: pause saves a checkpoint at the next step. Stop cancels the run and deletes its checkpoints, so it asks first.
- **Library hygiene**: not active yet.

## Current limits

Import is always on: new and moved files in your library folders are picked up without any button, and the grid refreshes when they land (a `library` / `media_added` event; hygiene's removal of rows whose files are gone sends `media_removed {count}`). The only explicit import left is Full clean & rescan, under Config → Maintenance. The hygiene work is still to come.
