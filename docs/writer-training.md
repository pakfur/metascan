[← Back to README](../README.md)

# Prompt-writer training (taste learning)

Metascan can fine-tune a **LoRA adapter** for the prompt-writing model (the 8B Qwen3-VL "writer" that turns a T2I caption or an I2V image into a prompt) from your own ratings and edits. The adapter is served by the same `llama-server` as the base writer, appears by its name in the **Writer** menu of the T2I dialog (training makes T2I adapters only; the I2V menu offers Base), and is judged against the base writer by a blind **scorecard** before you trust it.

This is optional. Nothing here runs unless you open the training window and press **Start**, and Metascan runs without the training dependencies. Training needs an NVIDIA GPU, so the feature stays gated (with the reasons listed) on a Mac or a small card.

Everything is built and trained locally. Your captions, prompts and edits are never logged and never leave the machine; the only place example text is shown is the training window's **Preview** panel.

## What it learns from

Three kinds of example, all from images only (I2V clips are never read):

| Kind | Where it comes from | Used for |
|---|---|---|
| **Preference pair** | A T2I caption written as several variants: a variant with at least one favorite image beats a variant whose images were all seen or passed with none favorited | DPO |
| **Edit** | A prompt you edited before rendering: your text is preferred to the writer's draft | DPO pair and SFT target |
| **Hindsight** | A favorited image with a known prompt, plus a short "brief" the general VLM writes about the image | SFT |

Scorecard images, `manual` prompts and the stock `fallback` text are never training targets. Deleted images still count (from their tombstone). About 10% of the preference groups are held out and never trained on (a group is held by a stable hash, so it stays held as your data grows; with two or more groups at least one is held and never all); they give the **holdout agreement** (the share of held-out pairs where the adapter prefers your chosen prompt), shown next to the base model's number.

**Readiness** is per scope (`All`, or one category): trainable at 100 or more preference and edit pairs, "enough new data" at 200 or more pairs since the newest adapter of that scope. The window shows both, and **Write briefs** fills in the hindsight briefs (a resumable background job: starting a training run cancels it, and it restarts by itself when the GPU is free).

## What you need

| Piece | Requirement |
|---|---|
| GPU | NVIDIA CUDA, **32 GB VRAM or more** (LoRA in bf16 on the 8B, up to 2048 tokens per example; no 4-bit) |
| Python | 3.11 only, like the rest of Metascan |
| Training packages | `requirements-train.txt` (torch with CUDA, transformers, peft, trl, accelerate, datasets, safetensors, sentencepiece, gguf) |
| Base weights | The full-precision Hugging Face weights of the writer, about 17.5 GB (16.3 GiB) |
| Converter | llama.cpp's `convert_lora_to_gguf.py` (turns the trained adapter into a GGUF `llama-server` can load) |
| TRL version | `trl>=0.25.0,<0.26`, pinned by `requirements-train.txt` (only the TRL 0.25 line is confirmed to work). The DPO step uses the SFT adapter as its reference model through `DPOConfig(model_adapter_name, ref_adapter_name)`; the gate probes the installed TRL for exactly those two `DPOConfig` fields, not for the version number |

The **Writer training** rows in the Models tab and the training window's gate block list whatever is missing, one reason per piece.

## Setup on Windows (native)

An RTX 50-series card (Blackwell, sm_120) needs a **CUDA 12.8 or newer** torch build; the default PyPI wheel on Windows is CPU-only, so install torch from the CUDA index first. In the virtualenv the Metascan server runs from (training uses the server's own Python by default):

```powershell
.\venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-train.txt
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last line must print `True` and your GPU. Restart the server (or press **Re-check** in the training window) so the gate re-probes.

To keep the heavy packages out of the server's environment, create a second 3.11 virtualenv, install the same two commands there, and point Metascan at it in `config.json`:

```jsonc
{ "models": { "writer_train_python": "C:/path/to/train-venv/Scripts/python.exe" } }
```

### WSL2 alternative

If native Windows installs give you trouble, run the **whole** Metascan backend inside WSL2 with the NVIDIA Windows driver (CUDA passes through to WSL2; do not install a Linux display driver inside it). Create the 3.11 virtualenv in the WSL filesystem, install torch from the same `cu128` index, then `pip install -r requirements-train.txt`. Keep `data/` on the WSL filesystem, not under `/mnt/c`, for speed. Pointing a Windows server at a WSL interpreter is not supported: the worker is started as a plain Python command with local paths.

## Download the base weights and the converter

Open **Config → Models → Writer training**:

- **`writer-train-base`**: the Hugging Face weights, `huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated` by default (the full-precision model the served GGUF was made from), about 17.5 GB. They download into `data/models/vlm/train/<org>--<name>/` through a `.partial` folder, so an interrupted download resumes where it stopped and a half-finished download never opens the gate. Use `models.writer_train_repo` to name another repo, and `models.huggingface_token` if the repo is gated. Deleting the base is refused while any running, paused or failed run was started from it.
- **`llama-cpp-converter`**: Metascan downloads only the converter's files (`convert_lora_to_gguf.py`, `gguf-py`, `conversion`, `requirements`, `models/templates`) from the llama.cpp release tag it was tested with (`LLAMA_CPP_RELEASE`) into `data/llama.cpp/`, with a `.metascan-release` marker. The row warns when that tag differs from the `llama-server` you run. To use your own checkout, set `models.llama_cpp_dir` to it; Metascan then never downloads into or deletes it. (`scripts/build_llama_server.sh` builds in a temporary directory and keeps no checkout, so the build alone does not provide the converter.)

If the converter fails on a missing package, install llama.cpp's own list into the training interpreter: `pip install -r <llama.cpp>/requirements/requirements-convert_lora_to_gguf.txt`.

Both downloads work on any machine; they are skipped by **Download all missing** because of their size.

## Training

**Config → Models → Train prompt writer** (or the header chip while a run is active) opens the **Writer training** window. It is a floating panel: closing it never stops a run.

1. Pick the **scope** (`All`, or one category) and check the **Dataset** panel and readiness.
2. Pick the **effort**:

   | Preset | LoRA rank | Learning rate (SFT) | Epochs |
   |---|---|---|---|
   | Quick | 8 | 1e-4 | 1 |
   | Standard | 16 | 1e-4 | 2 |
   | Thorough | 32 | 5e-5 | 3 |

   **Advanced** lets you override rank (4 to 64), learning rate (1e-6 to 5e-4), epochs (1 to 5) and the DPO learning rate (default 1e-5, same range). Values are clamped to those ranges and a warning shows when you differ from the preset.
3. Choose **Fresh**, or **Continue** an existing ready or untested adapter (it trains on top of that adapter; its own rank wins). The run keeps its own copy of that adapter, so deleting the source later does not break Resume.
4. Leave **Render scorecard briefs (blind)** on to start the scorecard when the run finishes.
5. **Start**.

A run is **SFT first, then DPO**. DPO's reference model is the SFT adapter (not the bare base), so DPO nudges preferences without undoing what SFT taught. Examples longer than 2048 tokens (system prompt, request and answer together) are dropped, never truncated, so the system prompt always matches what the writer sees at inference; the worker reports the count. After training, the adapter is converted to a GGUF and checked (its LoRA tensors are counted and no vision tensors may be present), then evaluated on the held-out pairs. Only a finished, verified run registers an adapter, with the status **untested**.

- **Checkpoints** are saved every 50 steps. **Pause** stops at the next step boundary and keeps a checkpoint; **Resume** continues from it (the same data and hyperparameters); **Cancel** deletes the run's checkpoints and files.
- A **server restart** or crash turns a running run into **paused**; resume it from the window. If a worker survived a hard kill, Metascan verifies it is its own worker (PID, start time, command line), stops it and pauses the run. If it cannot verify a worker it refuses to resume (`worker_unverifiable`) and tells you the PID to stop yourself.
- A **failed** run keeps its checkpoints and can be resumed; deleting the adapter of a paused or failed run discards the run.
- Adapter names are `taste-<scope>-v<N>` (renameable). A number is never reused, even after a delete or a rename.

### The GPU is locked while training

Training owns the GPU, so Metascan stops everything else that would use it and refuses new GPU work with `409 gpu_busy_training` ("GPU busy: training"):

- Refused: T2I and I2V generation and prompt writing, storyboard generation and describing, VLM tagging and retagging, chat and the prompt playground, similarity search by text or image, embedding and index builds, CLIP start/reload, ComfyUI submissions, upscaling, and a scorecard start.
- Unloaded when training starts: every VLM, and the CLIP worker. A running hindsight-briefs job is cancelled and restarts by itself when the GPU is free.
- Training itself will not start while ComfyUI jobs are queued or running, a T2I batch is active, a storyboard job is running, an index is building, an upscale is active or a scorecard is rendering; it lists what is in the way (`gpu_busy`).
- Still works: browsing, rating, the taste scorer (CPU), folders and categories, media and metadata.

### Expected run times

Real training has not yet been timed on the target hardware, so treat this as a rough guess to be corrected by your first runs: **Standard on an RTX 5090 is on the order of an hour for a few thousand examples**, Quick well under that, Thorough a few times longer. The window shows steps, an ETA (after the first steps), the loss curve and "checkpoint N min ago". Model loading and tokenizing happen before the first step.

## Using an adapter

A finished adapter shows up in the **Writer** menu of the T2I dialog as `name · date · NN% fav`, where the rate is its favorite rate on its blind scorecard (left out until a scorecard image of it is judged), plus `· untested · n/30` (judged pairs) until the scorecard promotes it. **Base** shows `baseline · NN% fav`, the base writer's rate on the same scorecards. Training creates T2I adapters only, so the I2V dialog's Writer menu offers Base. Details:

- A scope-`all` adapter is always offered; a one-category adapter is offered when that category is among the ones you selected. An adapter with fewer than 50 training examples for the chosen target model is shown disabled.
- Your pick is remembered. When the picked adapter is not offered for the moment (its category deselected, a target model it has too few examples for, not servable, refused by the writer), the dialog uses **Base** and says so, and switches back by itself when the adapter is offered again. Only deleting the adapter resets the pick to Base.
- Adapters are served with `llama-server --lora` per adapter, loaded unapplied and switched on per request, so one writer process serves the base and every adapter. Adding, deleting or withdrawing an adapter restarts the writer once it is idle (the next request starts it again); a request that needs a just-added adapter while the writer is busy gets `409 adapter_loading` ("try again"), and one for an adapter that is gone, failed, of the other surface or built on another base model gets `409 adapter_unavailable` and the dialog uses **Base** with a note (until the adapter list changes).
- Adapter writers get no variant "angle" hint and no taste examples (they were trained without them); their variants differ by sampling. For a scope-`all` adapter the request's category names are added to the request, exactly as in training.
- Every image records which writer made its prompt (`adapter:<id>`), so the **Writers** dashboard (Models tab) can compare favorite rates per writer.

## The scorecard

The scorecard answers "is this adapter actually better than the base writer for me?" without letting you prefer it just because you know which is which.

- **40 fixed briefs** (`data/taste/scorecard_briefs.txt`, written for this and committed). For each brief, the base writer and the adapter each write a prompt with the **same seed**, and both are rendered with the same ComfyUI workflow (default target model `krea2`, using its `t2i.model_workflows` preset; without one you get `no_workflow`). That is 80 images.
- Render it from the training window (**render scorecard**; **resume** continues a partial one at the same seeds; **re-render** adds a fresh set). It needs the GPU free of training.
- **Blind**: the scorecard images look like any other image in the grid and viewer, with no prompt, tags, writer or variant shown (the server strips them, not just the UI), until you give an explicit **favorite or pass**. Looking at an image without deciding does not reveal it. Clearing a verdict hides it again.
- **Judged** means both images of a pair have an explicit verdict, favorite or pass; an image you only looked at (seen, no verdict) does not count, even though it is a negative label for the taste scorer. At **30 judged pairs** the adapter moves from **untested** to **ready** and stays ready. The window shows `n/30 judged`, and the Models tab Writers table shows the favorite rate of the base and of each adapter, the mean taste score, and judged pairs.
- Scorecard images are real judgments for the taste scorer, but are never writer training data or part of the writer favorite-rate stats.
- Each brief counts as one pair (its latest complete render). **Resume** skips briefs still rendering and says so if only those are left; a scorecard cannot start while the server is shutting down.

A "ready" adapter that scores no better than the base is your call: delete it, or train again with more data.

## First run on a new machine

1. **Gate all green**: Models → Writer training shows the base and converter present and no reasons in the training window (use **Re-check** after installing packages).
2. **Quick run on small data** first: scope `All` or a small category with at least 100 pairs, effort **Quick**, scorecard off. Watch VRAM (it must stay under your card's 32 GB at the peak during DPO) and that the loss moves and a checkpoint appears. The first time through also confirms the pieces that could not be tested without a GPU: the model loads as `Qwen3VLForConditionalGeneration`, the converter accepts the adapter, and the run reports a GGUF with the expected LoRA tensor count.
3. **Check the GGUF loads**: the finished adapter appears in the T2I Writer menu; pick it and press **Generate Prompt**. A prompt comes back and the dialog does not fall back to Base.
4. **Run the scorecard**, judge pairs with F and X, and watch `n/30` climb to **ready**.
5. Then train the real Standard run.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Gate says dependencies are missing after installing | The check is cached per server; press **Re-check** in the training window |
| "Requires 32 GB VRAM" | The feature needs a 32 GB card; there is no override |
| TRL reason in the gate | Install the pinned version: `pip install -r requirements-train.txt` |
| Start says `gpu_busy` | The reasons name what is running (ComfyUI jobs, a T2I batch, an index build, and so on); wait or cancel it |
| Resume says `worker_alive` or `worker_unverifiable` | A training process from before a crash is still running; the message gives the PID |
| Base weights delete refused (`training_uses_base`) | A running, paused or failed run uses them; cancel those runs first |
| `adapter_loading` on a prompt | The writer is busy loading a new adapter; retry once the batch ends |
| Converter row shows a release mismatch | The adapter GGUF may not match your `llama-server`; rebuild or re-download so both come from a similar llama.cpp version |

Developer rules for this subsystem are in [`.claude/rules/writer-training.md`](../.claude/rules/writer-training.md) and [`.claude/rules/vlm-llama.md`](../.claude/rules/vlm-llama.md).
