[← Back to README](../README.md)

# Moving a library to another PC

Metascan keeps your ratings and settings in its database, not in your media files. To keep them when you move a library to a new PC, a new drive or another operating system, export them from the old install and import them into the new one. Both steps are in **Configuration → Import / Export**.

The media files are not part of this. Copy them yourself.

## What moves

| Moves | Rebuilt by the media import | Stays behind |
|---|---|---|
| ★ favorites and video playback speeds | Metadata and prompts read from files | Storyboards |
| ✓ / ✗ grades, reasons and views | Tags | Trained writer adapters and scorecards |
| Rating history and hindsight briefs | Embeddings and the similarity index | Application settings (`config.json`) |
| Categories, folders and saved prompts | Thumbnails and perceptual hashes | |
| Text to Image and Video settings for each generated file, with their lineage | Taste scores (the model retrains from your grades) | |
| Workflow presets | | |
| Training data of deleted images: their grades, briefs, CLIP vectors and archived thumbnails | | |

An adapter's weights do not move, but its name does, so older generations still show which writer made them. It appears as deleted in the new install.

## Files must keep their relative paths

Metascan stores no content hash, so a file is identified by its path. The folders **below** your library folder must stay the same. The library folder itself can move: `D:\Media\Library\2026\a.png` can become `/data/pictures/2026/a.png`.

A file that was renamed, or moved to another subfolder, is not found, and its data is skipped.

## Moving a library

1. On the old PC, open **Configuration → Import / Export** and choose **Export library data…**. Keep the `.zip` it saves.
2. Copy your media files to the new location, keeping their folders. Include your Text to Image and Video output folders.
3. Install Metascan on the new PC. Add your library folders in **Configuration → Directories**, and wait for the media import to finish.
4. In **Configuration → Import / Export**, choose **Choose export file…** and pick the `.zip`.
5. Check the folder list. Each old folder is paired with one here, and shows how many files were found. Correct a pair if needed; the count updates when you leave the box.
6. Choose **Import**. A table shows what was added.

Writing the data takes seconds. If many generated files have to be added first, it takes longer, and you can close the window while it runs.

## Importing more than once

Importing is safe to repeat:

- **Nothing you already have is overwritten.** A grade, ★, category, brief or setting already here stays as it is.
- **Nothing is added twice.**
- **Data for missing files is skipped, not lost.** It stays in the `.zip`. If the media import was still running, let it finish and import the same file again.

Text to Image and Video files often sit outside your scan folders, so the media import would never find them. If the import finds such a file on disk, it adds it to the library first.

## When an import is refused

Export and import both wait while a scan runs or while a **Full clean & rescan** is unfinished, because some ratings are not in the library at that point. Finish it, then try again.

If an import fails partway, nothing is changed.

The `.zip` holds your prompts and ratings, so treat it as private.

For rules engineers must follow, see the "Library data export / import" entry in [CLAUDE.md](../CLAUDE.md).
