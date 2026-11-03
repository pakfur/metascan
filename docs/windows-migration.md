[← Back to README](../README.md)

# Moving from WSL2 to native Windows

Moving keeps your whole library: grades, categories, embeddings and lineage. The main
gain is detection. On WSL2, Linux cannot be notified about changes on a Windows drive,
so Metascan has to poll, and a new file takes up to about 3 minutes to appear. On
Windows, a new file reaches the library in about 3 seconds.

**Status: the app has not yet been run end to end on Windows.** The file watcher,
path conversion and file timestamps were tested on real Windows against `D:`. The rest
of the app, including the GPU workers, is untested there. That is why you run the
Windows install alongside the WSL one, on a copy of your data, until you trust it.

## What was checked on this machine

| Check | Result |
|---|---|
| Stored paths | All 13,305 `/mnt/d/...` rows match paths derived on Windows; nothing needs rewriting |
| File sizes and timestamps | Identical from WSL and Windows on all 13,601 files, so nothing will re-embed |
| File watcher on `D:` (exFAT) | First event in under 40 ms; new, renamed, deleted and moved files and folders all correct |
| Python packages | 35 of 36 have Windows wheels for Python 3.11. `basicsr` builds from source and installed cleanly (tested on Python 3.12) |
| Already installed | `ffmpeg`, `ffprobe`, Node, Git, NVIDIA driver 610.88 |
| Missing | Python 3.11, `exiftool` |

## Before you start

1. Merge the watcher branch into `main`. The clone below takes what `main` holds.
2. Finish or clear any upscale, T2I, I2V or embedding work, then stop the server.
3. Check that `data/metascan.db-wal` is gone. If it is still there, the server did not
   shut down cleanly: start it, stop it with Ctrl+C, and check again.

Run everything below in PowerShell unless it says otherwise.

## 1. Install what's missing

```powershell
py install 3.11
scoop install exiftool
```

Check with `py -3.11 --version` and `exiftool -ver`.

## 2. Get the code

Put the repo on `C:`, not on the exFAT `D:` drive. SQLite's write-ahead log has not been
tested on exFAT. Your media stays on `D:`.

Clone from WSL, because your local commits are not on GitHub:

```powershell
git clone \\wsl.localhost\Ubuntu\home\jk\gws\metascan C:\metascan
cd C:\metascan
```

If git refuses with a "dubious ownership" error, run the `safe.directory` command it
prints, then clone again.

## 3. Create the Python environment

Create it as `venv`, the folder `run.bat` uses. Install CUDA torch **first**, or `pip`
installs a CPU-only build. See [step 5 of the setup guide](first_time_setup.md#5-install-backend-dependencies)
to pick the index for your driver. An RTX 5090 on a current driver takes `cu132`.

```powershell
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu132
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last line must print `True` and your GPU's name. For LoRA training, also install
`requirements-train.txt`.

## 4. Copy your data

`data/models` is about 60 GB, so this takes a while. The excluded folders are Linux
binaries, thumbnails that would be rebuilt anyway, and old database backups.

```powershell
$wsl = "\\wsl.localhost\Ubuntu\home\jk\gws\metascan"
robocopy "$wsl\data" C:\metascan\data /E /XD bin thumbnails rife /XF *.bak metascan.db-wal metascan.db-shm
Copy-Item "$wsl\config.json" C:\metascan\config.json
```

`robocopy` exit codes below 8 mean success.

## 5. Edit `config.json`

Change the scan folder. This value is used exactly as written, so a `/mnt/d` path here
is treated as missing and nothing gets scanned:

```json
"directories": [{ "filepath": "D:/Media/Staging", "search_subfolders": true }]
```

`t2i.output_root` and `i2v.output_root` are converted for you and can stay as they are.
Don't add a `/mnt/d` value for `comfy.output_root`: that one is not converted.

## 6. Install the llama-server

This fetches the Windows `llama-server`, along with the VLM model and the NLTK data. Name
the model you use:

```powershell
venv\Scripts\python.exe setup_models.py --vlm qwen3vl-8b
```

`run.bat` builds the UI on first start.

## 7. First start

Keep the WSL server stopped: running both would poll and ingest at the same time, each
into its own database. Start ComfyUI as usual; Metascan reaches it over `127.0.0.1:8188`.

```powershell
.\run.bat
```

Open <http://localhost:8700> and check, in this order:

1. **The Import panel says `Watching 1 folder live`.** Without "live" the watcher did not
   arm, and it has fallen back to polling. Check `logs\server.log` for a line starting
   `no native file watcher` or `a folder could not be watched`.
2. **The grid shows your library.** Thumbnails regenerate as you scroll, because their
   cache names include the file path. Expect a slow first browse.
3. **Open an image and a video.** Windows paths in thumbnail and stream URLs are the
   part of the UI that has never run on Windows.
4. **Drop an image into the scan folder.** It should appear in about 3 seconds.
5. **Run a similarity search, a T2I batch and an upscale.** These exercise CLIP, ComfyUI
   and the worker subprocesses.

If Library hygiene goes red with "N files missing from ...", **do not press play**.
Pressing play confirms the removal. Stop and check the folder in `config.json`.

## Known gaps on Windows

- **Deleted files bypass the Recycle Bin on `D:`** if Windows has no Recycle Bin for that
  drive, so a delete from Metascan can be permanent. Try it on a throwaway file.
- **`make` and the other Linux scripts don't apply.** Use `run.bat`, and run `pytest`,
  `black` and `mypy` directly.

## Going back

Your WSL install is untouched. Stop the Windows server and start the WSL one. Files added
meanwhile are picked up on its next walk, but grades, categories and other edits made on
Windows stay in the Windows database. Nothing merges the two, so once you have settled on
Windows, stop using the WSL install.
