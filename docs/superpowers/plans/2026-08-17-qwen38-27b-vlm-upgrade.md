# Qwen3.8-27B VLM Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Qwen3.8-27B (OrcaRouter abliterated GGUF) as a new top-tier VLM registry entry, bumping the pinned llama.cpp release to b10456 and generalizing the registry/gates/download/UI plumbing that currently hardcodes the `qwen3vl-` family.

**Architecture:** The `VlmModelSpec` registry stays the single source of truth; it grows three data fields (`ctx_size`, `extra_args`, `cuda_gate_vram_gb`) so per-model server flags and gate floors become data instead of id-string special cases. Every `mid.startswith("qwen3vl-")` filter becomes a `mid in REGISTRY` membership check. The llama.cpp pin jumps b7400 → b10456 (required: Qwen3.8's Gated DeltaNet CUDA kernels were broken before ≈b10450 — models load but emit corrupted tokens), which forces a `.tar.gz` extraction path since upstream moved Linux/macOS assets off `.zip`.

**Tech Stack:** Python 3.11 / FastAPI backend, llama.cpp `llama-server` subprocess (GGUF + mmproj), HuggingFace Hub downloads, Vue 3 + TypeScript frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-qwen38-27b-vlm-upgrade-design.md`

## Global Constraints

- Python 3.11; `make quality test` (flake8 + black 25.11.0 + mypy strict on `metascan/core/*` + pytest) must pass after every task. In a worktree run `make VENV_DIR=/home/jk/gws/metascan/venv quality test`.
- Frontend gate: `cd frontend && npm run build` (vue-tsc + Vite) must pass after any frontend task.
- New model id is exactly `qwen38-27b`. HF repo: `chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF`. GGUF file: `Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf`. mmproj repo path: `AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf`, stored locally as `mmproj-qwen38-27b-F16.gguf`.
- llama.cpp pin is exactly `b10456`. Linux/macOS release assets are `.tar.gz`; Windows assets remain `.zip`. There is still no Linux CUDA prebuilt — Linux+NVIDIA uses `scripts/build_llama_server.sh` (local override at `data/bin/local/llama-server`).
- Reasoning/thinking MUST be disabled for `qwen38-27b`: llama.cpp grammar enforcement is inactive while thinking is enabled (ggml-org/llama.cpp#20345) and every metascan VLM call is GBNF-constrained. The disable flags live in the spec's `extra_args`, confirmed against `llama-server --help` in Task 2.
- Known WSL2 flake: `tests/test_prompt_store.py::test_file_watcher_triggers_reload` fails in full-suite runs and passes in isolation — not a regression signal.
- Tasks 2, 3, and 10 run on the host (RTX 5090, WSL2) and hit network/GPU; they are verification tasks, not TDD tasks.

---

### Task 1: llama.cpp pin bump, asset picker, tar.gz extractor

**Files:**
- Modify: `metascan/utils/llama_server.py:23-29` (pin + comment), `:61-86` (`pick_release_asset`)
- Modify: `setup_models.py:89-162` (`_ensure_target` URL branch)
- Test: `tests/test_llama_server_paths.py`, Create: `tests/test_setup_models_archive.py`

**Interfaces:**
- Consumes: existing `HardwareReport`, `binary_filename()`, `DownloadTarget`.
- Produces: `LLAMA_CPP_RELEASE = "b10456"`; `pick_release_asset()` returning `.tar.gz` names on Linux/macOS; `_ensure_target()` able to extract both `.zip` and `.tar.gz` archives (flat `bin/` payload with symlink preservation). Task 7 imports nothing new from here.

- [ ] **Step 1: Update asset-name tests to the new release layout**

In `tests/test_llama_server_paths.py`, replace the bodies of the five `pick_release_asset` tests and the release-url test so expectations interpolate `LLAMA_CPP_RELEASE` and use the new suffixes (keep each test's existing `_report(...)` call):

```python
def test_pick_release_asset_linux_cuda_falls_back_to_vulkan():
    asset = pick_release_asset(_report(cuda=True, has_real_vk=True))
    assert asset == f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-vulkan-x64.tar.gz"


def test_pick_release_asset_linux_cuda_no_vulkan_falls_back_to_cpu():
    asset = pick_release_asset(_report(cuda=True, has_real_vk=False))
    assert asset == f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz"


def test_pick_release_asset_macos_arm64():
    asset = pick_release_asset(_report(os_="Darwin", machine="arm64"))
    assert asset == f"llama-{LLAMA_CPP_RELEASE}-bin-macos-arm64.tar.gz"


def test_pick_release_asset_linux_vulkan_no_cuda():
    asset = pick_release_asset(_report(has_real_vk=True))
    assert asset == f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-vulkan-x64.tar.gz"


def test_pick_release_asset_linux_cpu_fallback():
    asset = pick_release_asset(_report())
    assert asset == f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-x64.tar.gz"
```

For the Windows test(s), keep `.zip` expectations (`win-cuda-12.4-x64.zip`, `win-vulkan-x64.zip`, `win-cpu-x64.zip`). If `test_release_url_format` hardcodes `b7400`, change it to interpolate `LLAMA_CPP_RELEASE`. Also add:

```python
def test_pinned_release_is_b10456():
    assert LLAMA_CPP_RELEASE == "b10456"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llama_server_paths.py -v`
Expected: FAIL — asset names still end in `.zip`, pin is `b7400`.

- [ ] **Step 3: Update `llama_server.py`**

Replace the pin block (lines 23-29):

```python
# Pinned upstream release. Bump only deliberately — model compatibility and
# command-line flags evolve between releases. b10456 (2026-08-17) is pinned
# because Qwen3.8's Gated DeltaNet CUDA kernels were broken before ≈b10450:
# older builds load the model, use normal VRAM, and silently emit corrupted
# tokens (ggml-org/llama.cpp discussion #27164). b10456 retains Qwen3-VL
# support. From this release line Linux/macOS assets are ``.tar.gz`` and
# Windows stays ``.zip`` — ``setup_models._ensure_target`` handles both.
LLAMA_CPP_RELEASE = "b10456"
```

In `pick_release_asset` change the three non-Windows return values: `llama-{rel}-bin-macos-arm64.tar.gz`, `llama-{rel}-bin-ubuntu-vulkan-x64.tar.gz`, `llama-{rel}-bin-ubuntu-x64.tar.gz`. Windows branches unchanged. Update the module docstring line "Later releases switched Linux/macOS to .tar.gz…" — that caveat is now handled, not avoided.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llama_server_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing tar.gz extraction test**

Create `tests/test_setup_models_archive.py`:

```python
"""Archive extraction for the llama-server release assets.

b10456 ships Linux/macOS assets as .tar.gz and Windows as .zip; both carry
the binary plus sister shared libraries (with SONAME symlink chains) under
a bin/ folder that must be flattened next to the binary (RUNPATH=$ORIGIN).
Uses file:// URLs so no network is involved.
"""

import io
import tarfile
from pathlib import Path

from setup_models import DownloadTarget, _ensure_target


def _make_targz(path: Path, prefix: str = "llama-b10456/bin/") -> None:
    with tarfile.open(path, "w:gz") as tf:
        def add_file(name: str, data: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            info.mode = mode
            tf.addfile(info, io.BytesIO(data))

        add_file("llama-server", b"#!/bin/sh\necho ok\n", mode=0o755)
        add_file("libllama.so.0.0.1", b"ELFDATA")
        link = tarfile.TarInfo(prefix + "libllama.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libllama.so.0.0.1"
        tf.addfile(link)
        # Nested payloads (e.g. a docs/ subfolder) must be skipped.
        add_file("extras/README.txt", b"skip me")


def test_targz_asset_extracts_flat_with_symlinks(tmp_path):
    archive = tmp_path / "llama-b10456-bin-ubuntu-x64.tar.gz"
    _make_targz(archive)
    dest = tmp_path / "out" / "llama-server"

    ok = _ensure_target(DownloadTarget(url=archive.as_uri(), dest=dest))

    assert ok is True
    assert dest.exists()
    assert dest.stat().st_mode & 0o111  # executable
    assert (tmp_path / "out" / "libllama.so.0.0.1").exists()
    link = tmp_path / "out" / "libllama.so"
    assert link.is_symlink()
    assert link.readlink() == Path("libllama.so.0.0.1")
    assert not (tmp_path / "out" / "README.txt").exists()
    assert not (tmp_path / "out" / "extras").exists()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/test_setup_models_archive.py -v`
Expected: FAIL — `_ensure_target` treats the download as a `.zip` (`zipfile.BadZipFile`).

- [ ] **Step 7: Implement the tar.gz branch in `_ensure_target`**

In `setup_models.py`, add `import tarfile` to the imports, then restructure the `if t.url:` branch: pick the temp suffix from the URL and dispatch to per-format extractors. Replace the body from `tmp = t.dest.with_suffix(".zip")` through the end of the zip handling with:

```python
        print(f"  ⇣ Downloading {t.url}…")
        is_targz = t.url.endswith(".tar.gz")
        tmp = t.dest.with_suffix(".tar.gz" if is_targz else ".zip")
        try:
            urllib.request.urlretrieve(t.url, tmp)
            target_name = binary_filename()
            if is_targz:
                _extract_flat_bin_targz(tmp, t.dest, target_name)
            else:
                _extract_flat_bin_zip(tmp, t.dest, target_name)
            # Ensure the server binary is executable even if the archive's
            # mode bits were stripped (e.g. some Windows zip producers).
            t.dest.chmod(0o755)
            print(f"    → {t.dest}")
            return True
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
```

Move the existing zip logic verbatim into a module-level `_extract_flat_bin_zip(tmp: Path, dest: Path, target_name: str) -> None` (it already covers prefix detection, flat-only filtering, mode bits, and deferred symlinks). Add the tar twin:

```python
def _extract_flat_bin_targz(tmp: Path, dest: Path, target_name: str) -> None:
    """Extract the flat ``bin/`` payload of a llama.cpp .tar.gz release asset.

    Mirrors ``_extract_flat_bin_zip``: locate the binary to learn the
    archive's bin-folder prefix, extract only that folder's direct children
    next to ``dest`` (RUNPATH=$ORIGIN needs the .so files beside the
    binary), and materialize SONAME symlink chains after their targets.
    """
    with tarfile.open(tmp, "r:gz") as tf:
        members = tf.getmembers()
        member = next(
            (
                m
                for m in members
                if m.name.endswith(f"/{target_name}") or m.name == target_name
            ),
            None,
        )
        if member is None:
            raise RuntimeError(f"{target_name} not found inside {tmp.name}")
        bin_prefix = member.name[: -len(target_name)]
        deferred_links: list[tuple[Path, str]] = []
        for m in members:
            if m.isdir():
                continue
            if not m.name.startswith(bin_prefix):
                continue
            rel = m.name[len(bin_prefix) :]
            if not rel or "/" in rel:
                continue  # flat bin/* contents only
            out_path = dest.parent / rel
            if out_path.is_symlink() or out_path.exists():
                out_path.unlink()
            if m.issym():
                deferred_links.append((out_path, m.linkname))
                continue
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(out_path, "wb") as dst_f:
                shutil.copyfileobj(src, dst_f)
            perm = m.mode & 0o777
            if perm:
                out_path.chmod(perm)
        for link_path, link_target in deferred_links:
            os.symlink(link_target, link_path)
```

Also ensure `dest.parent.mkdir(parents=True, exist_ok=True)` still runs before extraction (it already happens at the top of `_ensure_target`).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_setup_models_archive.py tests/test_llama_server_paths.py tests/test_setup_models_qwen3vl.py -v`
Expected: PASS (zip refactor must not break the existing resolver tests).

- [ ] **Step 9: Full gate + commit**

Run: `make quality test`
Expected: PASS (modulo the known watcher flake).

```bash
git add metascan/utils/llama_server.py setup_models.py tests/test_llama_server_paths.py tests/test_setup_models_archive.py
git commit -m "feat(vlm): bump llama.cpp pin to b10456 with tar.gz asset support"
```

---

### Task 2: Rebuild the local llama-server binary at b10456 (host verification)

**Files:**
- No repo changes. Produces `data/bin/local/llama-server` (git-ignored) and a recorded flag decision for Task 4.

**Interfaces:**
- Consumes: Task 1's pin (the build script reads `LLAMA_CPP_RELEASE` from `metascan/utils/llama_server.py`).
- Produces: a b10456 CUDA binary at `data/bin/local/llama-server`; the **recorded reasoning-disable flags** (see Step 3) that Task 4 places in the registry entry's `extra_args`.

- [ ] **Step 1: Rebuild the local CUDA binary**

Run from the repo root (takes 10–25 minutes):

```bash
bash scripts/build_llama_server.sh
```

Expected: script clones tag `b10456`, builds with CUDA, installs to `data/bin/local/`, and its verify step prints a version containing `b10456`.

- [ ] **Step 2: Confirm the version and that the stale-binary trap is closed**

```bash
data/bin/local/llama-server --version 2>&1 | head -2
```

Expected: output contains `b10456`. If the old binary is still reported, the build failed silently — stop and investigate before proceeding (a b7400 local override would make Qwen3.8 emit garbage in Task 3 and mislead the whole spike).

- [ ] **Step 3: Record the reasoning-disable flag syntax**

```bash
data/bin/local/llama-server --help 2>&1 | grep -iA2 -E "reasoning|jinja|chat-template-kwargs"
```

Decision rule (record the outcome in the execution ledger for Task 4):
- If `--reasoning` with an on/off value exists → Task 4 uses `extra_args=("--jinja", "--reasoning", "off")`.
- Else → Task 4 uses `extra_args=("--jinja", "--chat-template-kwargs", '{"enable_thinking": false}')`.
- If `--jinja` is documented as default/deprecated-no-op, drop it from the tuple.

No commit (nothing in the repo changed).

---

### Task 3: Spike — download weights and verify Qwen3.8-27B end-to-end (ABORT GATE)

**Files:**
- No repo changes. Produces `data/models/vlm/Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf` (~16.8 GB) and `data/models/vlm/mmproj-qwen38-27b-F16.gguf` (these are the final install locations Task 4's registry entry expects).

**Interfaces:**
- Consumes: Task 2's binary and recorded flags.
- Produces: go/no-go verdict. **Abort criteria:** corrupted/garbage text output, grammar not enforced with reasoning disabled, vision requests failing, or a Qwen3-VL regression on the new binary — on any of these, stop the plan and report; do not proceed to Task 4.

- [ ] **Step 1: Download the weights to their final location**

```bash
source venv/bin/activate && python - <<'EOF'
from pathlib import Path
import shutil
from huggingface_hub import hf_hub_download

REPO = "chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF"
dst = Path("data/models/vlm")
dst.mkdir(parents=True, exist_ok=True)
for repo_file, local in [
    ("Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf",
     "Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf"),
    ("AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf",
     "mmproj-qwen38-27b-F16.gguf"),
]:
    cached = hf_hub_download(REPO, repo_file)
    shutil.copy(cached, dst / local)
    print("→", dst / local)
EOF
```

Expected: both files land under `data/models/vlm/` (~17.7 GB total; the GGUF takes a while).

- [ ] **Step 2: Launch the server manually**

Use the flags recorded in Task 2 (shown here with the `--reasoning` variant):

```bash
data/bin/local/llama-server \
  --model data/models/vlm/Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf \
  --mmproj data/models/vlm/mmproj-qwen38-27b-F16.gguf \
  --port 45899 --host 127.0.0.1 --parallel 4 --ctx-size 65536 \
  --n-gpu-layers 99 --jinja --reasoning off > /tmp/qwen38-spike.log 2>&1 &
until curl -sf http://127.0.0.1:45899/health; do sleep 2; done
```

Expected: health returns 200 within ~60 s. Check `nvidia-smi` — VRAM ≤ ~22 GB.

- [ ] **Step 3: DeltaNet coherence check (plain text)**

```bash
curl -s http://127.0.0.1:45899/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages": [{"role": "user", "content": "Describe a sunrise over mountains in two sentences."}],
  "max_tokens": 120
}' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

Expected: two coherent English sentences. Garbage like `/Q i` or `ance iurnesNSE){` = the DeltaNet CUDA bug → ABORT (binary predates the fix).

- [ ] **Step 4: Grammar enforcement check (reasoning must be off)**

```bash
curl -s http://127.0.0.1:45899/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "messages": [{"role": "user", "content": "List three colors as a JSON array of strings."}],
  "max_tokens": 60,
  "grammar": "root ::= \"[\" item (\",\" item){0,4} \"]\"\nitem ::= \"\\\"\" [a-z]{1,15} \"\\\"\""
}' | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

Expected: output is exactly a JSON array like `["red","green","blue"]` — no `<think>` block, no prose. A `<think>` prefix or free prose = grammar bypassed → ABORT (reasoning-disable flags wrong; re-check Task 2's decision).

- [ ] **Step 5: Vision check**

Pick any JPEG from the library and send it:

```bash
IMG=$(find /home/jk -name "*.jpg" -path "*metascan*" 2>/dev/null | head -1)
python3 - "$IMG" <<'EOF'
import base64, json, sys, urllib.request
b64 = base64.b64encode(open(sys.argv[1], "rb").read()).decode()
body = json.dumps({
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image in one sentence."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}],
    "max_tokens": 80,
}).encode()
req = urllib.request.Request("http://127.0.0.1:45899/v1/chat/completions",
                             body, {"Content-Type": "application/json"})
print(json.load(urllib.request.urlopen(req))["choices"][0]["message"]["content"])
EOF
```

Expected: a sentence that actually describes the image. (If no jpg is found, use any image file on disk and adjust the MIME type.)

- [ ] **Step 6: Qwen3-VL regression check on the same binary**

```bash
kill %1
ls data/models/vlm/   # confirm which qwen3vl weights exist locally
data/bin/local/llama-server \
  --model data/models/vlm/Huihui-Qwen3-VL-30B-A3B-Instruct-abliterated-Q4_K_M.gguf \
  --mmproj data/models/vlm/mmproj-qwen3vl-30b-a3b-F16.gguf \
  --port 45899 --host 127.0.0.1 --parallel 4 --ctx-size 32768 \
  --n-gpu-layers 99 --cache-type-k q8_0 --cache-type-v q8_0 > /tmp/qwen3vl-regress.log 2>&1 &
until curl -sf http://127.0.0.1:45899/health; do sleep 2; done
```

Repeat Step 3's text request and Step 5's image request. Expected: coherent output. (Substitute whichever `qwen3vl-*` weights are actually on disk.) Then `kill %1`.

- [ ] **Step 7: Record the verdict**

Record in the execution ledger: PASS/FAIL per check, VRAM observed, tokens/s if visible in the log, and the final `extra_args` tuple. On any ABORT, stop the plan here.

---

### Task 4: `VlmModelSpec` new fields + `qwen38-27b` registry entry + spec-driven `_build_command`

**Files:**
- Modify: `metascan/core/vlm_models.py`
- Modify: `metascan/core/vlm_client.py:218-251`
- Test: `tests/test_vlm_models.py`, `tests/test_vlm_client_lifecycle.py`

**Interfaces:**
- Consumes: Task 3's confirmed `extra_args` tuple (default shown below: `("--jinja", "--reasoning", "off")` — substitute the ledger's value).
- Produces: `VlmModelSpec` fields `ctx_size: int = 32768`, `extra_args: tuple[str, ...] = ()`, `cuda_gate_vram_gb: Optional[float] = None`; `REGISTRY["qwen38-27b"]`. Task 5 reads `cuda_gate_vram_gb`; Task 7 reads the mmproj subdir convention.

- [ ] **Step 1: Write the failing registry tests**

In `tests/test_vlm_models.py`, replace `test_all_four_sizes_present` and `test_min_vram_is_monotonic_by_size`, and add two tests:

```python
def test_all_five_entries_present():
    expected = {
        "qwen3vl-2b",
        "qwen3vl-4b",
        "qwen3vl-8b",
        "qwen3vl-30b-a3b",
        "qwen38-27b",
    }
    assert set(REGISTRY.keys()) == expected


def test_min_vram_is_monotonic_within_qwen3vl_family():
    sizes = ["qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b"]
    vrams = [REGISTRY[s].min_vram_gb for s in sizes]
    assert vrams == sorted(vrams)


def test_qwen38_entry_shape():
    spec = REGISTRY["qwen38-27b"]
    assert spec.hf_repo == "chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF"
    assert spec.gguf_filename == "Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf"
    # Repo path may carry a subdir; the local filename must be flat.
    assert spec.mmproj_repo_filename.startswith("AUX/")
    assert "/" not in spec.mmproj_filename
    assert spec.ctx_size == 65536
    # Reasoning must be disabled — grammar enforcement is inactive while
    # thinking is enabled (ggml-org/llama.cpp#20345).
    assert any("reasoning" in a or "enable_thinking" in a for a in spec.extra_args)


def test_moe_kv_cache_quant_moved_to_extra_args():
    assert "--cache-type-k" in REGISTRY["qwen3vl-30b-a3b"].extra_args
    assert REGISTRY["qwen3vl-4b"].extra_args == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vlm_models.py -v`
Expected: FAIL — no `qwen38-27b` key, no `extra_args` field.

- [ ] **Step 3: Extend the dataclass and registry**

In `metascan/core/vlm_models.py` add to `VlmModelSpec` (after `parallel_slots`, before `mmproj_repo_filename` so existing positional-free entries stay valid — all new fields have defaults):

```python
    ctx_size: int = 32768
    extra_args: tuple[str, ...] = ()
    cuda_gate_vram_gb: Optional[float] = None
```

(`from typing import Optional` at the top.) Document them in the class docstring:

```
      - ``ctx_size``: TOTAL --ctx-size budget, split across
        ``parallel_slots`` (each slot sees ctx_size / parallel_slots).
      - ``extra_args``: extra llama-server argv appended verbatim
        (KV-cache quant, reasoning control, …).
      - ``cuda_gate_vram_gb``: CUDA availability floor for feature_gates;
        None means "use min_vram_gb".
```

Update the module docstring first line to "Registry of VLM GGUF variants supported by metascan (Qwen3-VL Abliterated + Qwen3.8 Abliterated)." and note the override key is `config.models.vlm_repos.<model_id>` (legacy `qwen3vl_repos` still honored — Task 7 wires it).

Set on existing entries: `qwen3vl-8b` gets `cuda_gate_vram_gb=10.0`; `qwen3vl-30b-a3b` gets `cuda_gate_vram_gb=24.0` and `extra_args=("--cache-type-k", "q8_0", "--cache-type-v", "q8_0")`. Add the new entry:

```python
    "qwen38-27b": VlmModelSpec(
        model_id="qwen38-27b",
        display_name="Qwen3.8 27B (Abliterated)",
        hf_repo="chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF",
        gguf_filename="Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf",
        mmproj_filename="mmproj-qwen38-27b-F16.gguf",
        quant="Q4_K_M",
        approx_vram_gb=20.0,
        min_vram_gb=18.0,
        parallel_slots=4,
        ctx_size=65536,
        # Hybrid Gated DeltaNet model: KV cache is ~64 KB/token (only 16 of
        # 64 layers are full attention), so 65536 ctx costs ~4 GB. Reasoning
        # MUST stay disabled: llama.cpp grammar enforcement is inactive
        # while thinking is enabled (ggml-org/llama.cpp#20345) and every
        # metascan call is grammar-constrained. Requires llama.cpp >= b10450
        # (DeltaNet CUDA fix) — see utils/llama_server.LLAMA_CPP_RELEASE.
        extra_args=("--jinja", "--reasoning", "off"),
        cuda_gate_vram_gb=20.0,
        mmproj_repo_filename="AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf",
    ),
```

(Substitute `extra_args` with Task 3's recorded tuple if it differs.) Also update the registry's leading comment: the noctrex remark applies to the qwen3vl entries; the 27B ships from chimingw/OrcaRouter with its mmproj under `AUX/`.

- [ ] **Step 4: Run registry tests**

Run: `pytest tests/test_vlm_models.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing `_build_command` test**

Append to `tests/test_vlm_client_lifecycle.py`:

```python
def test_build_command_is_spec_driven():
    from metascan.core.vlm_client import VlmClient
    from metascan.core.vlm_models import REGISTRY

    client = VlmClient()

    cmd27 = client._build_command(REGISTRY["qwen38-27b"], 12345)
    assert cmd27[cmd27.index("--ctx-size") + 1] == "65536"
    for arg in REGISTRY["qwen38-27b"].extra_args:
        assert arg in cmd27
    assert "--cache-type-k" not in cmd27

    cmd30 = client._build_command(REGISTRY["qwen3vl-30b-a3b"], 12345)
    assert "--cache-type-k" in cmd30
    assert cmd30[cmd30.index("--ctx-size") + 1] == "32768"

    cmd4 = client._build_command(REGISTRY["qwen3vl-4b"], 12345)
    assert "--cache-type-k" not in cmd4
```

- [ ] **Step 6: Run it to verify it fails**

Run: `pytest tests/test_vlm_client_lifecycle.py::test_build_command_is_spec_driven -v`
Expected: FAIL — ctx-size is hardcoded `"32768"` for the 27B and `--jinja`/`--reasoning` are missing.

- [ ] **Step 7: Make `_build_command` spec-driven**

In `metascan/core/vlm_client.py` change the docstring to `"""Build the llama-server argv from the model spec."""`, replace the literal `"32768"` with `str(spec.ctx_size)`, and replace the trailing special case:

```python
        cmd += list(spec.extra_args)
        return cmd
```

Adjust the ctx-size comment: keep the existing per-slot explanation, and note the budget now comes from `spec.ctx_size` (32768 default; 65536 for `qwen38-27b`, whose hybrid attention makes KV cheap).

- [ ] **Step 8: Run tests + full gate**

Run: `pytest tests/test_vlm_client_lifecycle.py tests/test_vlm_models.py -v && make quality test`
Expected: PASS (mypy is strict on `metascan/core/*` — the new `Optional[float]` import matters).

- [ ] **Step 9: Commit**

```bash
git add metascan/core/vlm_models.py metascan/core/vlm_client.py tests/test_vlm_models.py tests/test_vlm_client_lifecycle.py
git commit -m "feat(vlm): add qwen38-27b registry entry with spec-driven server args"
```

---

### Task 5: Spec-driven hardware gates + new recommendation ladder

**Files:**
- Modify: `metascan/core/hardware.py:255-262` (docstring), `:385-438` (VLM gate block)
- Test: `tests/test_hardware_vlm_gates.py`, `tests/test_scanner_vlm_routing.py`

**Interfaces:**
- Consumes: `VlmModelSpec.cuda_gate_vram_gb` / `min_vram_gb` from Task 4.
- Produces: gates keyed by all five registry ids; on `cuda_workstation` with ≥20 GB VRAM, `qwen38-27b` is the recommended model (8B below that; 30B-A3B is never recommended anymore but stays available at ≥24 GB).

- [ ] **Step 1: Write the failing gate tests**

In `tests/test_hardware_vlm_gates.py`, rename/replace `test_cuda_workstation_high_recommends_30b` and add coverage for the new entry:

```python
def test_cuda_workstation_high_recommends_qwen38():
    g = feature_gates(_report(cuda_gb=24.0))
    assert g["qwen38-27b"].available is True
    assert g["qwen38-27b"].recommended is True
    assert g["qwen3vl-30b-a3b"].available is True
    assert g["qwen3vl-30b-a3b"].recommended is False
    assert g["qwen3vl-8b"].recommended is False


def test_cuda_workstation_20gb_band_recommends_qwen38():
    g = feature_gates(_report(cuda_gb=20.0))
    assert g["qwen38-27b"].available is True
    assert g["qwen38-27b"].recommended is True
    assert g["qwen3vl-30b-a3b"].available is False  # below its 24 GB floor


def test_cuda_workstation_16gb_recommends_8b_qwen38_unavailable():
    g = feature_gates(_report(cuda_gb=16.0))
    assert g["qwen3vl-8b"].recommended is True
    assert g["qwen38-27b"].available is False
    assert "20 GB" in g["qwen38-27b"].reason
```

Also update `test_cuda_workstation_recommends_8b` (16 GB fixture) to additionally assert `g["qwen38-27b"].available is False`, and `test_cpu_only_recommends_clip_offers_2b_4b` / `test_apple_silicon_recommends_4b_offers_all` to assert `g["qwen38-27b"]` exists (`available is False` with reason `"Requires GPU acceleration."` on CPU; `available is True, recommended is False` on Apple Silicon).

In `tests/test_scanner_vlm_routing.py` add:

```python
def test_recommended_model_id_for_workstation_high_is_qwen38():
    from backend.services.scan_dispatch import recommended_vlm_model_id

    assert recommended_vlm_model_id(_report(cuda_gb=32.0)) == "qwen38-27b"
```

(The existing 16 GB workstation test keeps expecting `qwen3vl-8b` — unchanged. This new test fails until Task 6 removes the prefix filter; that is expected — mark it `@pytest.mark.xfail(reason="prefix filter removed in next task", strict=True)` and remove the marker in Task 6.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hardware_vlm_gates.py -v`
Expected: the new/updated tests FAIL — gates still recommend `qwen3vl-30b-a3b` at 24 GB and there is no generalized floor message for `qwen38-27b`.

- [ ] **Step 3: Generalize the gate block**

In `metascan/core/hardware.py` replace the CUDA branch of the VLM loop (the `if key == "qwen3vl-30b-a3b":` / `elif key == "qwen3vl-8b":` / `else:` chain) with:

```python
        if report.cuda is not None:
            floor = _spec.cuda_gate_vram_gb or min_vram
            available = cuda_vram >= floor
            reason = (
                ""
                if available
                else f"Requires {floor:.0f} GB VRAM; detected {cuda_vram} GB."
            )
```

Replace the CPU-only branch's model list check so any GPU-class entry stays gated off generically (the existing `else: available = False; reason = "Requires GPU acceleration."` already covers `qwen38-27b` — just confirm the 2b/4b special cases remain first). Replace the workstation recommendation branch:

```python
        elif tier is Tier.CUDA_WORKSTATION:
            recommended = (
                key == "qwen38-27b" if cuda_vram >= 20.0 else key == "qwen3vl-8b"
            )
```

Update the comment above the loop from "Qwen3-VL Abliterated tagging" to "VLM tagging (Qwen3-VL + Qwen3.8 Abliterated)" and the `feature_gates` docstring id list (line ~261) to `qwen3vl-{2b,4b,8b,30b-a3b}`, `qwen38-27b`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hardware_vlm_gates.py tests/test_scanner_vlm_routing.py -v`
Expected: PASS (the xfail-marked routing test xfails).

- [ ] **Step 5: Full gate + commit**

Run: `make quality test`

```bash
git add metascan/core/hardware.py tests/test_hardware_vlm_gates.py tests/test_scanner_vlm_routing.py
git commit -m "feat(vlm): spec-driven gate floors; workstation >=20GB recommends qwen38-27b"
```

---

### Task 6: Remove every `qwen3vl-` prefix filter (registry membership instead)

**Files:**
- Modify: `metascan/core/vlm_select.py:12-19`
- Modify: `backend/services/scan_dispatch.py:26,46`
- Modify: `backend/api/vlm.py:76-90,153`
- Modify: `backend/api/models.py:507-513,542-549`
- Modify: `backend/main.py:252-263`
- Test: `tests/test_scanner_vlm_routing.py` (un-xfail), `tests/test_vlm_api.py` (existing suite as regression net)

**Interfaces:**
- Consumes: `REGISTRY` from `metascan.core.vlm_models`; `recommended_vlm_model_id` from `backend.services.scan_dispatch`.
- Produces: `qwen38-27b` (whose id does not match the old prefix) flows through selection, preload, download, delete, and retag-fallback paths.

- [ ] **Step 1: Un-xfail the routing test and verify it fails**

Remove the `@pytest.mark.xfail` from `test_recommended_model_id_for_workstation_high_is_qwen38` (Task 5).

Run: `pytest tests/test_scanner_vlm_routing.py::test_recommended_model_id_for_workstation_high_is_qwen38 -v`
Expected: FAIL — `recommended_vlm_model_id` still filters on `startswith("qwen3vl-")`, so the recommended `qwen38-27b` gate is skipped and `None` is returned.

- [ ] **Step 2: Replace the prefix filters**

Each change swaps the string test for registry membership:

`metascan/core/vlm_select.py` — rename `_recommended_qwen_gate` to `_recommended_vlm_gate` (update its one caller in `pick_vlm_model` and the docstring's "``qwen3vl-*``" phrasing to "registered VLM"):

```python
def _recommended_vlm_gate() -> Optional[str]:
    from metascan.core.hardware import detect_hardware, feature_gates
    from metascan.core.vlm_models import REGISTRY

    gates = feature_gates(detect_hardware())
    for mid, gate in gates.items():
        if mid in REGISTRY and gate.recommended:
            return mid
    return None
```

`backend/services/scan_dispatch.py` — in `should_tag_with_vlm`: `if mid in REGISTRY and g.recommended`; in `recommended_vlm_model_id`: `if mid in REGISTRY and g.recommended` (REGISTRY is already imported; update both docstrings' "``qwen3vl-*``" phrasing).

`backend/api/vlm.py:76-90` — the auto-pick candidates list:

```python
        from metascan.core.hardware import detect_hardware, feature_gates
        from metascan.core.vlm_models import REGISTRY

        gates = feature_gates(detect_hardware())
        candidates = [
            mid for mid, g in gates.items() if mid in REGISTRY and g.recommended
        ]
```

`backend/api/vlm.py:153` — hardware-aware fallback:

```python
    from metascan.core.hardware import detect_hardware
    from backend.services.scan_dispatch import recommended_vlm_model_id

    model_id = (
        client.model_id
        or recommended_vlm_model_id(detect_hardware())
        or "qwen3vl-4b"
    )
```

`backend/api/models.py` — download route (replace lines 507-513):

```python
    from metascan.core.vlm_models import REGISTRY as _VLM_REGISTRY

    if mid in _VLM_REGISTRY:
        asyncio.create_task(_download_vlm(mid))
        return {"status": "started", "id": mid}
```

and the delete route (replace the `model_id.startswith("qwen3vl-")` block's guard):

```python
    from metascan.core.vlm_models import REGISTRY as _VLM_REGISTRY
    from metascan.utils.app_paths import get_data_dir

    if model_id in _VLM_REGISTRY:
        spec = _VLM_REGISTRY[model_id]
        ...  # existing body unchanged
```

(Unknown `qwen3vl-bogus`-style ids now fall through to the routes' final `404 unknown model id` — the separate "unknown VLM model" 404s disappear.)

`backend/main.py:253` — preload:

```python
    from metascan.core.vlm_models import REGISTRY as _VLM_REGISTRY

    for preload_id in preload_list:
        if preload_id in _VLM_REGISTRY:
```

(place the import with the function's other local imports, matching the file's style).

- [ ] **Step 3: Run the affected suites**

Run: `pytest tests/test_scanner_vlm_routing.py tests/test_vlm_api.py tests/test_models_vlm_rows.py tests/test_storyboard_runner.py tests/test_lifespan_vlm.py -v`
Expected: PASS, including the previously-xfailed test.

- [ ] **Step 4: Full gate + commit**

Run: `make quality test`

```bash
git add metascan/core/vlm_select.py backend/services/scan_dispatch.py backend/api/vlm.py backend/api/models.py backend/main.py tests/test_scanner_vlm_routing.py
git commit -m "refactor(vlm): registry membership replaces qwen3vl- prefix filters"
```

---

### Task 7: Download path generalization + `vlm_repos` config override + status-row flag

**Files:**
- Modify: `setup_models.py:43-68,167-180,250-260` (resolver, download fn, CLI)
- Modify: `backend/config.py:77-93` (`get_models_config`)
- Modify: `backend/api/models.py:222-256` (`_vlm_status_rows`), `:641-671` (`_download_vlm` import)
- Modify: `metascan/core/vlm_models.py:5-6,22-23,51-52,106-110` (override-key docstrings)
- Test: `tests/test_setup_models_qwen3vl.py`, `tests/test_models_vlm_rows.py`

**Interfaces:**
- Consumes: `resolve_repo(model_id, override)` from `vlm_models` (finally gets a real caller), `get_config_path` from `metascan.utils.app_paths`.
- Produces: `resolve_vlm_targets(model_id)` (alias `resolve_qwen3vl_targets` kept); `config.models.vlm_repos.<id>` honored by both CLI and Models-tab downloads (legacy key `qwen3vl_repos` still read); status rows carry `"is_vlm": True` (Task 8 consumes it).

- [ ] **Step 1: Write the failing resolver tests**

In `tests/test_setup_models_qwen3vl.py` add (keep the existing tests; they exercise the alias):

```python
from setup_models import resolve_vlm_targets


def test_resolve_targets_qwen38_flattens_mmproj_subdir():
    targets = resolve_vlm_targets("qwen38-27b")
    assert len(targets) == 3
    mm = next(t for t in targets if t.filename and "mmproj" in t.filename.lower())
    assert mm.filename == "AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf"
    assert mm.dest.name == "mmproj-qwen38-27b-F16.gguf"


def test_resolve_targets_applies_vlm_repos_override(tmp_path, monkeypatch):
    import setup_models

    cfg = tmp_path / "config.json"
    cfg.write_text('{"models": {"vlm_repos": {"qwen38-27b": "me/my-remix"}}}')
    monkeypatch.setattr(setup_models, "_config_json_path", lambda: cfg)

    targets = resolve_vlm_targets("qwen38-27b")
    gguf = next(t for t in targets if t.filename and t.filename.endswith("Q4_K_M.gguf"))
    assert gguf.repo == "me/my-remix"


def test_resolve_targets_honors_legacy_qwen3vl_repos_key(tmp_path, monkeypatch):
    import setup_models

    cfg = tmp_path / "config.json"
    cfg.write_text('{"models": {"qwen3vl_repos": {"qwen3vl-4b": "me/legacy-remix"}}}')
    monkeypatch.setattr(setup_models, "_config_json_path", lambda: cfg)

    targets = resolve_vlm_targets("qwen3vl-4b")
    gguf = next(t for t in targets if t.filename and t.filename.endswith(".gguf") and "mmproj" not in t.filename)
    assert gguf.repo == "me/legacy-remix"
```

And in `tests/test_models_vlm_rows.py`, extend `test_models_status_includes_vlm_rows` to assert five rows and the flag:

```python
    vlm_rows = [r for r in rows if r.get("is_vlm")]
    assert {r["id"] for r in vlm_rows} == {
        "qwen3vl-2b", "qwen3vl-4b", "qwen3vl-8b", "qwen3vl-30b-a3b", "qwen38-27b",
    }
```

(adapt to the file's existing row-fetch shape).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_setup_models_qwen3vl.py tests/test_models_vlm_rows.py -v`
Expected: FAIL — no `resolve_vlm_targets`, no `_config_json_path`, no `is_vlm`.

- [ ] **Step 3: Implement the resolver rename + override loader**

In `setup_models.py` add `import json` if missing, then:

```python
def _config_json_path() -> Path:
    """Path to the app config.json (patchable in tests)."""
    from metascan.utils.app_paths import get_config_path

    return get_config_path()


def _load_vlm_repo_overrides() -> dict[str, str]:
    """Read ``models.vlm_repos`` (legacy ``models.qwen3vl_repos``) from
    config.json. Returns {} when the file or key is absent/malformed."""
    try:
        raw = json.loads(_config_json_path().read_text())
    except (OSError, ValueError):
        return {}
    models = raw.get("models") or {}
    ov = models.get("vlm_repos") or models.get("qwen3vl_repos") or {}
    if not isinstance(ov, dict):
        return {}
    return {str(k): str(v) for k, v in ov.items()}
```

Rename `resolve_qwen3vl_targets` → `resolve_vlm_targets`, and inside it resolve the repo through the override (this makes `resolve_repo` live code at last):

```python
    from metascan.core.vlm_models import REGISTRY, resolve_repo

    spec = REGISTRY[model_id]
    repo = resolve_repo(model_id, _load_vlm_repo_overrides())
```

and use `repo=repo` for both HF targets. After the function add the compat alias:

```python
# Backwards-compat alias (pre-Qwen3.8 name).
resolve_qwen3vl_targets = resolve_vlm_targets
```

Update `download_qwen3vl`'s banner text to `"Setting up VLM tagger ({model_id})…"`, the argparse `--qwen3vl` help to list `qwen38-27b` among the examples, and add an alias flag:

```python
    parser.add_argument(
        "--vlm",
        dest="qwen3vl",
        metavar="MODEL_ID",
        help="Alias for --qwen3vl.",
    )
```

- [ ] **Step 4: Wire the backend side**

`backend/api/models.py`:
- `_download_vlm`: `from setup_models import _ensure_target, resolve_vlm_targets` and call `resolve_vlm_targets(mid)`. Update its docstring ("a VLM GGUF + mmproj…").
- `_vlm_status_rows`: change `"group": "Tagging (Qwen3-VL)"` to `"group": "Tagging (VLM)"`, add `"is_vlm": True` to the row dict, and update the function docstring's "four Qwen3-VL Abliterated variants" to "registered VLM variants".

`backend/config.py` `get_models_config` — expose the override for API/config-UI consumers:

```python
    vlm_repos = raw.get("vlm_repos") or raw.get("qwen3vl_repos") or {}
    if not isinstance(vlm_repos, dict):
        vlm_repos = {}
    return {
        "preload_at_startup": [str(x) for x in preload],
        "huggingface_token": str(raw.get("huggingface_token") or ""),
        "vlm_repos": {str(k): str(v) for k, v in vlm_repos.items()},
    }
```

(update the docstring's Shape block accordingly).

`metascan/core/vlm_models.py` — update the module and `resolve_repo` docstrings: the override key is `config.models.vlm_repos.<model_id>` (legacy `qwen3vl_repos` honored by the loader in `setup_models.py`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_setup_models_qwen3vl.py tests/test_models_vlm_rows.py tests/test_vlm_models.py -v`
Expected: PASS.

- [ ] **Step 6: Full gate + commit**

Run: `make quality test`

```bash
git add setup_models.py backend/config.py backend/api/models.py metascan/core/vlm_models.py tests/test_setup_models_qwen3vl.py tests/test_models_vlm_rows.py
git commit -m "feat(vlm): generalized download resolver with vlm_repos override; is_vlm row flag"
```

---

### Task 8: Frontend — VLM rows gated by `is_vlm`, not id prefix

**Files:**
- Modify: `frontend/src/api/models.ts:12` (`ModelRow` interface)
- Modify: `frontend/src/components/dialogs/ConfigModelsTab.vue:404,417`

**Interfaces:**
- Consumes: `is_vlm: true` on VLM status rows (Task 7).
- Produces: Load/Unload buttons render for any VLM row, including `qwen38-27b`.

- [ ] **Step 1: Extend the row type**

In `frontend/src/api/models.ts` add to `ModelRow`:

```typescript
  /** True for VLM (llama-server) rows — enables Load/Unload controls. */
  is_vlm?: boolean
```

- [ ] **Step 2: Swap the two template gates**

In `ConfigModelsTab.vue`, change both `v-if="row.id.startsWith('qwen3vl-')"` occurrences (the Load button at ~line 404 and the Unload button at ~line 417) to `v-if="row.is_vlm"`.

- [ ] **Step 3: Type-check + build**

Run: `cd frontend && npm run build`
Expected: PASS (vue-tsc + Vite).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/models.ts frontend/src/components/dialogs/ConfigModelsTab.vue
git commit -m "feat(models-ui): gate VLM Load/Unload on is_vlm row flag"
```

---

### Task 9: Documentation sweep

**Files:**
- Modify: `CLAUDE.md`, `docs/architecture.md`, `docs/build-llama-server.md`, `docs/hardware-detection.md`, `docs/configuration.md`, `docs/future_ideas.md`

**Interfaces:** none (prose only), but wording must match the code landed in Tasks 1–8.

- [ ] **Step 1: CLAUDE.md**

- Update the "llama.cpp release zip extraction must flatten `bin/`" bullet: title becomes archive-format-neutral; note Linux/macOS assets are `.tar.gz` from b10456 (`_extract_flat_bin_targz` mirrors the zip path; symlinks preserved via `TarInfo.issym()`/`linkname`).
- Update the "Qwen3-VL VLM tagging" bullet to mention the registry now also carries `qwen38-27b` (Qwen3.8, dense hybrid Gated DeltaNet) as the `cuda_workstation` ≥20 GB recommendation.
- Add a new gotcha bullet:

```markdown
- **Qwen3.8 reasoning must stay disabled.** `qwen38-27b`'s `extra_args`
  disable thinking at server startup because llama.cpp grammar enforcement
  is inactive while thinking is enabled (ggml-org/llama.cpp#20345) — and
  every metascan VLM call is GBNF-constrained. Never remove those flags
  without moving all call sites off grammars. The model also requires
  llama.cpp >= ~b10450: older CUDA builds load it fine and silently emit
  corrupted tokens (Gated DeltaNet kernel bug). `LLAMA_CPP_RELEASE` is
  pinned accordingly; a stale `data/bin/local/llama-server` built from an
  older tag reproduces the garbage-output failure even with a correct pin.
- **Per-model llama-server flags live in `VlmModelSpec.extra_args`**, and
  the context budget in `VlmModelSpec.ctx_size` — never re-introduce
  model-id string matching in `vlm_client._build_command` or
  `startswith("qwen3vl-")` filters in selection code; use `mid in REGISTRY`.
```

- [ ] **Step 2: docs/build-llama-server.md**

Update the pinned-release mention to b10456 and note the still-true "no Linux CUDA prebuilt" state; add a warning that after a pin bump the local override must be rebuilt (stale local builds mask the bundled binary and, for Qwen3.8, produce corrupted output).

- [ ] **Step 3: docs/architecture.md, docs/hardware-detection.md, docs/configuration.md**

- architecture.md VLM section: registry has five entries across two families; mention `extra_args`/`ctx_size` spec fields.
- hardware-detection.md: gate table gains `qwen38-27b` (CUDA floor 20 GB; recommended on `cuda_workstation` ≥20 GB; 30B-A3B available ≥24 GB but no longer recommended).
- configuration.md: document `models.vlm_repos` (`{"<model_id>": "<hf-repo>"}`; legacy `qwen3vl_repos` still read).

- [ ] **Step 4: docs/future_ideas.md**

Add two entries: MTP speculative decoding for `qwen38-27b` (repo ships `MTP/Qwen3.8-27B-Uncensored-OrcaRouter-MTP-Q8_0.gguf`; llama-server `--model-draft … --spec-type draft-mtp`; would need a `draft_gguf_filename` spec field), and VLM video tagging via sampled frames (Qwen3.8 understands video natively; llama.cpp's video path is still frame-based — the `_SUPPORTED_IMAGE_EXTS` guard stays for now).

- [ ] **Step 5: Verify + commit**

Run: `make quality test` (docs don't affect it, but confirm nothing else drifted).

```bash
git add CLAUDE.md docs/architecture.md docs/build-llama-server.md docs/hardware-detection.md docs/configuration.md docs/future_ideas.md
git commit -m "docs: Qwen3.8-27B VLM entry, b10456 pin, vlm_repos override"
```

---

### Task 10: Live quality validation on the host (manual)

**Files:**
- Possibly modify: `data/meta_prompt.yml` (hot-reloaded prompts) — only if validation shows drift.
- Append findings to: `docs/superpowers/specs/2026-08-17-qwen38-27b-vlm-upgrade-design.md` (a short "Validation results" section).

**Interfaces:**
- Consumes: everything landed in Tasks 1–9; weights already on disk from Task 3.

- [ ] **Step 1: Start the app and activate the model**

```bash
source venv/bin/activate && python run_server.py &
sleep 5
curl -s -X POST http://localhost:8700/api/vlm/active -H 'Content-Type: application/json' -d '{"model_id": "qwen38-27b"}'
```

Expected: model loads (watch `vlm_status` in logs / Models tab shows Loaded). Confirm the Models tab row renders with Load/Unload buttons and the gate chip shows "recommended".

- [ ] **Step 2: Tagging comparison**

Pick ~10 known library images spanning content types. Tag via `POST /api/vlm/tag` with `qwen38-27b`, then swap to `qwen3vl-30b-a3b` (or `-8b`) and tag the same files. Compare: tag validity against the grammar, coverage, hallucination rate.

- [ ] **Step 3: Storyboard paths**

On a test storyboard: run a subject Describe, a scene Describe, one story compose stage (beats reroll is cheap and non-destructive), and one H3 compile (`prompt_source` sound stage). Expected: no `StoryError` validation failures, sound sections coherent, compile completes for all panels.

- [ ] **Step 4: Iterate prompts only if needed**

If a systematic style drift appears (e.g. over-verbose soundscapes, tag phrasing changes), adjust the relevant prompt in `data/meta_prompt.yml` (hot-reloads; no restart). Keep changes minimal and note each in the findings.

- [ ] **Step 5: Record findings + commit**

Append a "## Validation results (2026-08-…)" section to the spec doc: tagging comparison verdict, storyboard results, tokens/s, VRAM, any prompt tweaks. Decide and record whether `preload_at_startup` should include the VLM (leave config.json itself to the user).

```bash
git add docs/superpowers/specs/2026-08-17-qwen38-27b-vlm-upgrade-design.md data/meta_prompt.yml
git commit -m "docs(spec): Qwen3.8-27B validation results"
```

(Omit `data/meta_prompt.yml` from the add if unchanged.)
