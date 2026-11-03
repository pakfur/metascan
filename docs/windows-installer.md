[← Back to README](../README.md)

# Windows installer

`Setup.exe` is a normal Windows setup wizard. It needs no
terminal, no administrator account and no copy of the source code. If you want to
work on Metascan itself, use the [first-time setup guide](first_time_setup.md).

## Installing

Run the Setup file. If Windows SmartScreen warns about it, choose **More info**, then
**Run anyway**: the Setup file is not code-signed yet.

| Page | What you choose |
|---|---|
| Install location | Default: `%LOCALAPPDATA%\Programs\Metascan`. Your database, thumbnails and models live here too, so pick a local NTFS drive with room (about 12 GB to start). Your media can stay on any drive |
| Prerequisites | Python 3.11, FFmpeg and ExifTool. Anything already on this PC is greyed out. Setup installs the rest through winget, and ExifTool shows a Windows permission prompt. Also whether to download the search model now |
| Your library | The folders that hold your media. Tick a folder to include its subfolders |
| ComfyUI | Host and port, with a **Test connection** button. Keep the defaults if ComfyUI runs on this PC |
| Original folder | Where the originals of upscaled files go. It must be outside your library folders |
| Shortcuts | Desktop shortcut, and starting Metascan in the tray when you sign in |

Setup then copies the files and installs Python packages and models, which takes 10
to 30 minutes, mostly downloading. It finds your NVIDIA card and driver and installs
the matching GPU build of PyTorch, and it picks the CLIP search model your GPU can
handle. **Stop** ends this step; run Setup again to continue.

## Using Metascan

**Metascan** in the Start menu opens your library in your browser, at
<http://127.0.0.1:8700>. Metascan stays in the notification area near the clock:
right-click its icon to **Open Metascan**, **Open log folder** or **Stop Metascan**.
Clicking the shortcut again opens the page.

The first time Metascan indexes your library, the Embedding panel can stay at "0 of N" for
several minutes (about 5 on an RTX 5090 with the large model) while it prepares its tag
vocabulary. This happens once.

If port 8700 is taken, for example by Metascan running in WSL, it uses the next free
port and opens that one.

Metascan is reachable from this PC only. To reach it from other devices, set the user
environment variable `METASCAN_HOST` to `0.0.0.0`, restart Metascan and allow it
through Windows Firewall when asked. `METASCAN_PORT` sets the port.

To change your folders or ComfyUI later, use **Config** in the app. Change the Original
folder only from **Config → Directories**, which also moves the originals already there.

## Updating

Stop Metascan from its tray icon, then run the newer Setup. It finds the old install,
skips the library questions and keeps your settings, ratings and thumbnails.

Running Setup again also repairs an install, and it switches to the GPU build of
PyTorch if a driver update makes that possible.

## Uninstalling

Use **Settings → Apps** or **Uninstall Metascan** in the Start menu. Your media files are
never touched. Uninstall asks whether to delete Metascan's own data as well, which is your
grades, categories and folders, thumbnails, models, settings and the originals of upscaled
files, if you kept them in the default place. An Original folder you chose elsewhere is left
alone. **No** is the default and keeps all this in the install folder for a later install.

These stay either way: Python, FFmpeg and ExifTool, which other programs may use, and the
downloads in `%USERPROFILE%\.cache\huggingface` and `%USERPROFILE%\.metascan`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Setup could not finish | Choose **Retry**. The reason is in `logs\install.log` in the install folder. To run the step by hand: `powershell -ExecutionPolicy Bypass -File installer\provision.ps1` from that folder |
| Setup says Metascan is running | Stop it from its tray icon |
| The GPU isn't used | Setup's last page says so. Update your NVIDIA driver, then run Setup again |
| Prerequisites can't be installed | winget is missing. Install **App Installer** from the Microsoft Store and run Setup again |
| Metascan stopped unexpectedly | The message shows the end of the log. The full logs are `launcher.log` and `server.log` in `logs` |

## Building the installer

You need this repo's `venv`, Node.js and
[Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
winget install --id JRSoftware.InnoSetup -e
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

`just installer` runs the same script. The result is
`installer\build\output\Setup.exe`, and the version shown in the wizard comes from `setup.py`.
`-SkipFrontend` reuses `frontend\dist`.

| File | Job |
|---|---|
| `installer/metascan.iss` | The wizard, the copy, the shortcuts and Uninstall |
| `installer/provision.ps1` | Everything after the copy. It runs alone from a terminal and is safe to repeat |
| `installer/apply_settings.py` | Merges the wizard's answers into `config.json` |
| `installer/launcher.py`, `serve.py`, `serve_protocol.py` | The tray icon, the server it runs, and how it stops the server |
| `installer/build.ps1`, `make_assets.py` | Stage the files, make the pictures, compile |

`build.ps1` ships an allow-list of tracked and new files, and never ignored ones. A new
top-level folder the app needs must be added to it.

Keep `RedirectionGuard=no` in `metascan.iss`. With Inno Setup's default, every program Setup
starts refuses to follow symlinks a non-admin made, which is every entry in the Hugging Face
cache, so CLIP cannot load during Setup or in a Metascan started from its last page. Keep the
stop signal off stdin too: a thread blocked reading it hangs the server's child processes.

For an unattended install:

```powershell
Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="D:\Metascan" /LIBRARY="D:\Media;E:\Renders" /COMFYHOST=127.0.0.1 /COMFYPORT=8188 /ORIGINAL="D:\Originals"
```

`/NOPREREQS`, `/NOCLIP` and `/CPUTORCH=1` skip the prerequisites, skip the search model and
force the CPU build. The exit code is 0 when Metascan is ready.

## What has been tested

Run on 2026-11-02 on Windows 11 Pro with an RTX 5090, driver 610.88, on the machine used to
build it, with the real wizard and real downloads.

| Worked | |
|---|---|
| Wizard | Every page, the folder picker, **Test connection** against a live ComfyUI, the refusal of an Original folder inside the library |
| Provisioning | New virtualenv, `cu132` torch verified on the GPU, the large CLIP model chosen by tier and fetched, config written. A CPU torch replaced by the CUDA build on a re-run |
| Running | Start menu shortcut, tray icon, a second click, the move to port 8701 beside a server on 8700, import, live watching, a dropped file arriving, CLIP text search on the GPU, ComfyUI connected, **Stop Metascan** with a clean database |
| Lifecycle | Update keeping config and database, Setup refused while Metascan runs, the server stopping when the launcher is killed, Uninstall with both answers, a fresh install and removal at the default folder |

Not tested yet:

- **A clean machine.** Python, FFmpeg and ExifTool were already here, so the winget installs
  of those three, and the fallback to the classic Python package, have never run. The first
  run on a clean PC, or in Windows Sandbox, is the important one left.
- **No NVIDIA GPU**, an old driver, an RTX 50-series card on a CUDA 12.8 or 12.9 driver, and Windows 10.
- **Stop** during provisioning, **Retry** after a failure, and a network failure mid-download.
- The new `llama-server` CUDA DLL download. Its target list is unit-tested, but it has not
  been downloaded through `setup_models.py`.
- Install folders with a very long path: Uninstall left 9 files of `venv` behind in one of
  about 130 characters, past the Windows path limit. The default folder is fine.
