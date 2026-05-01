# Developer Guidelines

[← Back to README](../README.md)

## Build Commands

```bash
# Backend
make serve            # Run the FastAPI backend server
python run_server.py  # Same, without make

# Frontend
cd frontend
npm run dev           # Development server with HMR
npm run build         # Production build (vue-tsc + vite)
npx vue-tsc --noEmit  # Type check only

# Quality
make test             # Run Python tests
make quality          # flake8 + black --check + mypy
make quality test     # The full local equivalent of CI's backend job
```

## Code Style

- **Python:** `black` (v25.11.0 — must match in both `requirements.txt` and `requirements-dev.txt`), `flake8`, and `mypy` with `python_version = 3.11` (strict on `metascan/core/*`).
- **TypeScript / Vue:** `vue-tsc` for type checking, Vite for building. Vue 3 Composition API with `<script setup>`. Strict TypeScript.

```bash
# Python
black metascan/ tests/ backend/
flake8 metascan/ backend/ tests/
mypy metascan/

# Frontend
cd frontend && npx vue-tsc --noEmit
```

## Project Rules

- **No UI framework in Python code.** Backend Python must never import `PyQt6`, `qt_material`, `tkinter`, or any other UI toolkit. The Vue 3 + FastAPI stack is the only UI.
- **DB access is synchronous.** Wrap calls in `asyncio.to_thread()` from the service layer; the `threading.Lock` in `DatabaseManager` handles concurrency.
- **Heavy AI work runs in subprocesses**, not threads, to avoid GIL contention and isolate crashes.

See [`CLAUDE.md`](../CLAUDE.md) at the repo root for the canonical, exhaustive rule set — including the historical gotchas (HEIC encoder segfaults, MapLibre `display:none` wedge, `INSERT OR REPLACE` vs upsert, covering-index requirements, etc.).

## Testing

```bash
# Python tests
pytest
pytest --cov=metascan
pytest tests/test_prompt_tokenizer.py

# Frontend type checking
cd frontend && npx vue-tsc --noEmit

# Frontend production build verification
cd frontend && npm run build
```

The pytest suite covers core modules, async service wrappers, hardware probes/gates, the inference subprocess wiring (with a fake NDJSON worker — no CLIP required), and the folders DB + REST layer (against an isolated temp DB via `fastapi.testclient.TestClient`). All tests must pass before merging.

## CI

`.github/workflows/python-package.yml` runs two parallel jobs:

1. **backend** — install deps, `flake8`, `black --check`, `mypy`, `pytest`.
2. **frontend** — `npm ci`, `vue-tsc --noEmit`, `npm run build`.

`make quality test` locally matches the CI backend job exactly.

## Common Tasks

The patterns for adding API endpoints, dialogs, smart-folder fields, hardware probes, and tag axes are documented inline in [`CLAUDE.md`](../CLAUDE.md) under "Common Tasks". That document is the source of truth — keep it updated when you change the relevant pattern.
