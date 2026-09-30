# Text-to-Image (t2i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **This is plan 3 of 3** - Tasks 14-23: frontend foundations, preset dialog / config tab / smart-folder rule, the dialog, and the docs with full verification. It needs plans 1 and 2 applied first.

**Goal:** Add a "Text to Image" dialog that turns a typed or randomly drawn caption into a model-styled prompt and renders it through a registered ComfyUI `t2i` workflow in server-side batches, with results ingested into the library, a config tab, and a smart-folder rule.

**Architecture:** A parallel stack to i2v. A pure, seeded caption engine (`t2i_characters`) plus a list loader (`t2i_wildcards`) and an in-memory CSV store (`t2i_captions`) feed prompt generation (`t2i_prompt`, reusing the existing `META_*` model guidelines). A `T2iRunner` owns server-side batches, windowed ComfyUI submission and ingest into a new `t2i_images` table; `backend/api/t2i.py` exposes it, and a `t2i` WebSocket channel reports progress. The frontend adds `T2IDialog`, a Pinia store, a config tab and a smart-folder rule, built on a few new shared components. I2V is not modified.

**Tech Stack:** Python 3.11, FastAPI, SQLite, numpy, PyYAML, pytest (`unittest.TestCase` style), Vue 3 + TypeScript (`<script setup>`), Pinia, PrimeVue, Vite / `vue-tsc`.

**Spec:** `docs/superpowers/specs/2026-09-29-t2i-design.md` — read it before starting; this plan argues from it.

**Working branch:** `feature/t2i` (the spec is already committed there).

## How this plan was produced

Every task's code was written test-first and run in a throwaway copy of the repo taken from the spec commit. The test files, implementations and diffs below are the verified ones, and the expected outputs quoted in each step were observed. Each plan part was then replayed step by step on a fresh checkout. Diffs are against the state left by the previous tasks, so **apply tasks in numeric order** (across the three plan files, too). A diff that does not apply cleanly means the tree drifted; stop and reconcile instead of forcing it.

## Reading and executing this plan

- **Do not load a plan file whole.** Each is thousands of lines of verified code. Find a task with `grep -n '^### Task' <file>` and read one task at a time (`Read` with `offset`/`limit`).
- **Write Python files so backslash escapes survive.** Python files in this plan are pure ASCII by design: a non-ASCII character is spelled as a `\uXXXX` escape in the source text. The file-writing tool decodes such escapes into literal characters (a `\ufeff` became an invisible BOM). After creating any Python file from this plan run `LC_ALL=C grep -nP '[^\x00-\x7F]' <file>`; it must print nothing. If it prints a line, re-create the file with a quoted heredoc (`cat > <file> <<'EOF'`). Frontend files legitimately contain literal glyphs (✕ ★ × ·); do not run the check on them.
- **Commands run from the repo root** with the venv on the path (`venv/bin/pytest`, `venv/bin/black`, `venv/bin/flake8`, `venv/bin/mypy --check-untyped-defs`). In a git worktree, symlink the repo's `venv` in or use its absolute path.
- **Frontend gate:** `cd frontend && npm run build` (type-check + bundle). There is no frontend test runner; each frontend task ends with a manual check.
- The full suite has one known WSL2 flake, `test_file_watcher_triggers_reload`, which fails in most full runs and passes alone. It is not a regression signal.

## Global Constraints

Every task's requirements implicitly include this section.

- Python 3.11 only; `black` 25.11.0; `flake8` fatal errors (E9, F63, F7, F82) zero; `mypy --check-untyped-defs` with every function in `metascan/core/*` fully annotated.
- Draws use `hashlib.sha256`, never `hash()`.
- Generated prompt text never contains parentheses (ComfyUI parses them as weighting syntax); list lines containing a parenthesis are rejected at load.
- Adult-only guard: a line in an `age` list with an integer under 18 is rejected; a line in ANY list containing `teen`, `teenage`, `teenager`, `underage`, `minor`, `child`, `kid`, `preteen`, `juvenile`, `schoolgirl`, `schoolboy`, `loli`, `shota`, `under 18` or `under eighteen` (case-insensitive, word-bounded; plurals included) is rejected.
- DELETE routes return `{"status": "deleted"}`, never 204.
- Stored paths are POSIX, returned paths native (`to_posix_path` / `to_native_path`).
- `generation_jobs.t2i_batch_id` is nullable TEXT with no `REFERENCES`, added with `_idempotent_add_column`.
- DB access in the service layer goes through `asyncio.to_thread`.
- `t2i_images` has no foreign keys; list queries JOIN `media` and prune; `set_t2i_image_form_state` is the only update and no route writes the fact columns.
- Every image is its own ComfyUI job with latent `batch_size=1`; *Batch Size* and *Count per Batch* are job counts.
- `comfy.unload_vlm_during_generation` decides the GPU order; there is no new setting for it.
- Tests use hand-written known captions and model outputs. They never use prompts from the metascan library and never rows of the real `t2i_captions.csv`.
- Frontend: Vue 3 `<script setup lang="ts">`. The globally registered PrimeVue pieces are only Button, InputText, InputGroup, Menubar, AutoComplete and the Tooltip directive; anything else is imported locally. `useWebSocket` is called during component setup. The T2I overlay closes only through its header ✕ (no `@click.self`). Every `localStorage` access is in try/catch.
- **I2V is not modified**: no edits to `metascan/core/i2v_*.py`, `backend/api/i2v.py`, `backend/services/i2v_service.py`, `I2VDialog.vue`, `stores/i2v.ts`, `api/i2v.ts` or `types/i2v.ts` (importing from them is fine where a task says so).
- **Never stage `metascan/core/i2v_compiler.py` or `docs/t2i_screenshot.txt`** — the working tree carries an uncommitted user edit and an untracked mock. Stage explicit paths only (`git add <path>`); never `git add -A`, `git add .` or `git commit -a`. `black --check` already fails on `metascan/core/i2v_compiler.py` at the commit this branch started from (a missing blank line before `build_i2v_user_prompt`), so `make quality` exits non-zero on black regardless of this work. Do not reformat that file (it is I2V code); run black, flake8 and mypy on the files a task touches instead.
- Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Five inputs or failure modes the spec implies but that no acceptance test in it names, most likely first. Each is pinned by a test in the task that owns the code.

1. **A CSV that is not tidy** — a caption with an embedded newline, CRLF line endings, a UTF-8 BOM and a trailing blank line still index and round-trip exactly. *(Task 3)*
2. **Awkward token neighbours and seeds** — a token next to punctuation or a curly apostrophe (`__ALICE__’s`), a token at the very end, an empty or whitespace-only caption, and seeds `0`, `2**31-1` and a negative number never raise and stay deterministic. *(Task 1)*
3. **Extreme aspect ratios** — `21:9`, `9:21` and a 0.5 MP budget on a 64-multiple model still give edges of at least one multiple, on the grid, with bounded aspect error. *(Task 5)*
4. **Cancel or failure mid-run** — cancelling during the prompt phase (an in-flight VLM call), a failed job and a submit error leave no orphan state: counters sum to the total, the terminal event fires exactly once, and the window is released. *(Tasks 10-11)*
5. **Fixed seed in a Random run** — the same seed across steps draws the same cast, `count_per_batch > 1` with Fixed is rejected, and seed-range exhaustion stops the run with reduced totals. *(Tasks 9-10)*

## Task index

| Plan file | Phase | Task | Deliverable |
|---|---|---|---|
| 1 engine and prompts | 1 Caption engine | 1 | `t2i_characters.py` — pure engine |
| | | 2 | `t2i_wildcards.py`, starter lists, `characters.yml`, `.gitignore` |
| | | 3 | `t2i_captions.py` — CSV store, filters, picker |
| | 2 Prompt generation | 4 | `split_negative_block`, model profiles, prompt composition, YAML keys |
| 2 backend | 3 Backend service | 5 | `t2i_form.py` — seeds, dims, form state |
| | | 6 | DB table and column, `ComfyClient.submit` kwarg |
| | | 7 | Kind-level workflow validator |
| | | 8 | `get_t2i_config` |
| | | 9 | Runner I — errors, `generate_prompt`, `start_batch` validation and planning |
| | | 10 | Runner II — batch execution, GPU order, window, cancel |
| | | 11 | Runner III — job events, ingest, accounting, `aclose` |
| | | 12 | Service, routes, lifespan wiring |
| | | 13 | Guideline-adherence probe script |
| 3 frontend and docs | 4 Frontend foundations | 14 | TS types, API client, job type |
| | | 15 | Shared components and composables |
| | | 16 | `stores/t2i.ts` |
| | 5 Preset dialog, config tab, rule | 17 | `PresetRegistrationDialog` `kind` prop |
| | | 18 | `ConfigT2ITab` and `ConfigDialog` |
| | | 19 | Smart-folder rule (adds `refreshT2iPaths`) |
| | 6 The dialog | 20 | Caption filter popover |
| | | 21 | `T2IDialog` form, batch controls, entry icon and mount |
| | | 22 | Strip, persistence, viewer, app-level library refresh (calls `refreshT2iPaths` after a delete) |
| | 7 Docs | 23 | Docs and full verification |

Tasks 1-4 need nothing else. Tasks 5-13 need Tasks 1-4 (9-12 import their modules). Tasks 14-22 type-check against the shapes in Task 14 and need the routes of Task 12 only at runtime. **Apply tasks in numeric order:** Task 22's delete handler calls a store function that Task 19 creates.

---

## Phase 4 — Frontend foundations

Types and API client, shared pieces, and the store. The frontend has no test runner: each task's gate is `npm run build` (type-check + bundle) plus the manual check at its end.

### Task 14: TS types, API client and neutral job types

**Files:**
- Create: `frontend/src/types/jobs.ts` (neutral `JobChip`, `jobChipLabel`, `ThumbItem`)
- Create: `frontend/src/types/t2i.ts` (every T2I type plus the pure helpers the store and dialog use)
- Create: `frontend/src/api/t2i.ts` (one function per route in contract section 9)
- Modify: `frontend/src/types/storyboard.ts` (`GenerationJob` gains `t2i_batch_id`)
- Test: `frontend/t2i-checks.local/harness.mjs` and `frontend/t2i-checks.local/task14.mjs` (throwaway, never committed: the frontend has no test runner, so these load the real TypeScript through Vite and assert on it; the build is the second gate)

**Interfaces:**
- Consumes: `get`, `post`, `patch`, `del` from `frontend/src/api/client.ts` (every response is parsed as JSON, a non-2xx becomes an `ApiError` carrying the server's `detail`); `withCurrentOption(options: number[], current: number): number[]` from `frontend/src/types/i2v.ts` (re-exported, never copied); `Media` from `frontend/src/types/media.ts`; the JSON shapes of contract sections 8.1-8.6 and the `t2i` channel payloads of spec section 9.
- Produces (later tasks rely on these names exactly):
  - `types/jobs.ts`: `JobChip { state: 'queued'|'running'|'failed'; cancelling?; value?; max?; error? }`, `jobChipLabel(chip: JobChip): string`, `ThumbItem { id: number|string; file_path; is_favorite; title; label }`.
  - `types/t2i.ts` types: `T2iMode`, `SeedPolicy`, `IdentityStyle`, `T2iContentMode`, `T2iModelInfo`, `T2iConfig`, `T2iOutputPreview` (pinned by contract section 9); `Range`, `CaptionFilter`, `CaptionMetaOption`, `CaptionMetaChoice`, `CaptionMetaRange`, `CaptionMetaIntRange`, `CaptionMetaColumn`, `CaptionMeta`, `CaptionCount`, `CaptionRow`, `T2iLora`, `T2iFormState`, `T2iImage`, `T2iPromptResult`, `T2iResolveResult`, `T2iBatchRequest`, `T2iBatchStarted`, `T2iStartResult`, `T2iBatchStep`, `T2iBatchInfo`, `T2iActiveBatch`, `T2iBatchOutcome`, `T2iFinishedBatch`, and the WebSocket payloads `T2iBatchStartedEvent`, `T2iBatchStepEvent`, `T2iBatchProgressEvent`, `T2iBatchEndEvent`, `T2iBatchErrorEvent`, `T2iImagesChangedEvent`.
  - `types/t2i.ts` values: `SEED_MAX`, `DEFAULT_MAX_BATCH_SIZE` (500), `DEFAULT_MAX_COUNT_PER_BATCH` (32), `DRAFT_STORAGE_KEY`; `emptyFilter()`, `cleanFilter(f)`, `activeFilterCount(f)`, `isFilterEmpty(f)`; `randomSeed(max?)`, `clampSeed(n, max?)`, `parseSeedInput(raw, max?)`; `effectiveCount(policy, count, max?)`, `isCountEffective(policy, count)`, `effectiveBatchSize(mode, size, max?)`, `plannedImages(mode, policy, batchSize, count, limits?)`; `t2iImageDetails(img)`, `t2iImageTitle(img)`, `toStripItem(img)`, `t2iImageToMedia(img)`; `parseSqliteUtc(raw)`, `formatT2iTimestamp(raw)`; `mergeImagePage(existing, fresh, pageSize, prevHasMore)`, `mergeOlderPage(existing, page)`; `batchStatusLine(b)`; `sanitizeDraft(raw)`; and the re-export `withCurrentOption`.
  - `api/t2i.ts`: `fetchT2iConfig()`, `fetchCaptionMeta()`, `countCaptions(filter?)`, `randomCaption(filter?)`, `resolveCaption({caption, seed, model})`, `generateT2iPrompt({caption, seed, model})`, `startT2iBatch(body: T2iBatchRequest)`, `listT2iBatches()`, `cancelT2iBatch(batchId)`, `listT2iImages(limit = 60, beforeId?)`, `patchT2iImage(id, fields: Partial<T2iFormState>): Promise<T2iImage>`, `deleteT2iImage(id): Promise<{status: 'deleted'}>`, `listT2iPaths()`, `t2iOutputPreview(root, prefix)`.
  - `GenerationJob.t2i_batch_id: string | null`.

**Design notes.**
- `cleanFilter` produces the wire shape of `CaptionFilter`: an empty list or a range with no usable end restricts nothing (the server ignores them too), so they are removed rather than sent; a range keeps whichever end is a finite number, because the server takes "min and/or max".
- `isCountEffective` is false only when Fixed is overriding a typed count above 1 (the moment to grey the field); `effectiveCount` is the number the dialog actually sends.
- `T2iFormState.model` is nullable because a stored form can lack it (an image ingested without a recorded model); a PATCH must still never send a null or empty model.
- Tile labels use `×` (spec 12.3: `seed · W×H`), not an ASCII `x`.
- `mergeImagePage` keeps rows the user already paged in behind a fresh first page, so refreshing the strip during a long batch never collapses "Older".

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/harness.mjs`, `frontend/t2i-checks.local/task14.mjs`)

`frontend/.gitignore` already ignores anything named `*.local`, so this directory can never be staged. The harness loads sources with Vite's SSR module loader (no new dependency, no browser); Tasks 15 and 16 reuse it.

`frontend/t2i-checks.local/harness.mjs`:
```js
// Throwaway logic-check harness for the T2I frontend pieces. NOT part of the
// app: the frontend has no test runner, so this loads the real TypeScript /
// Vue sources through Vite's SSR module loader (no browser, no extra deps)
// and lets each task script assert on them. The directory name ends in
// `.local`, which frontend/.gitignore already ignores.
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

// Make Date formatting checks meaningful: local time must differ from UTC.
process.env.TZ = 'America/New_York'

export { assert }

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
let server = null

export async function load(id) {
  if (!server) {
    server = await createServer({
      root,
      configFile: false,
      // Keep Vite's caches out of the (shared, symlinked) node_modules.
      cacheDir: path.join(root, 't2i-checks.local', '.vite-cache'),
      plugins: [vue()],
      server: { middlewareMode: true, hmr: false, watch: null },
      optimizeDeps: { noDiscovery: true, include: [] },
      appType: 'custom',
      logLevel: 'error',
    })
  }
  return server.ssrLoadModule(id)
}

export async function stop() {
  if (server) await server.close()
  server = null
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Resolves after pending promise callbacks and zero-delay timers have run.
export const settle = () => sleep(0)

let passed = 0
let failed = 0

export async function check(name, fn) {
  try {
    await fn()
    passed++
    console.log(`  ok   ${name}`)
  } catch (e) {
    failed++
    const msg = String(e && e.message ? e.message : e).split('\n').join('\n         ')
    console.log(`  FAIL ${name}\n         ${msg}`)
  }
}

export async function finish() {
  await stop()
  console.log(`\n${passed} passed, ${failed} failed`)
  if (failed) process.exitCode = 1
}

// ---- fakes ---------------------------------------------------------------

/**
 * Replaces globalThis.fetch. `handler({url, method, body})` returns
 * `{ status?, json? }` (or a promise of one). Every call is recorded.
 */
export function installFetch(handler) {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    const call = {
      url: String(url),
      method: init.method ?? 'GET',
      body: init.body === undefined ? undefined : JSON.parse(init.body),
    }
    calls.push(call)
    const res = (await handler(call)) ?? {}
    const status = res.status ?? 200
    return {
      ok: status < 400,
      status,
      statusText: `status ${status}`,
      // A real response is a fresh parse: no object is shared with the server fixture.
      json: async () => (res.json === undefined ? {} : JSON.parse(JSON.stringify(res.json))),
    }
  }
  return calls
}

export function installLocalStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  globalThis.localStorage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => void map.set(k, String(v)),
    removeItem: (k) => void map.delete(k),
    clear: () => map.clear(),
  }
  return map
}

/** A promise you resolve by hand — for ordering races deterministically. */
export function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
```

`frontend/t2i-checks.local/task14.mjs`:
```js
// Task 14 logic checks: types/jobs.ts, types/t2i.ts, api/t2i.ts.
import {
  assert,
  check,
  finish,
  installFetch,
  installLocalStorage,
  load,
} from './harness.mjs'

installLocalStorage()

const jobs = await load('/src/types/jobs.ts')
const t = await load('/src/types/t2i.ts')
const t2i = t
const api = await load('/src/api/t2i.ts')
const { ApiError } = await load('/src/api/client.ts')

// A hand-written image row (all values invented for these checks).
function img(id, over = {}) {
  return {
    id,
    file_path: `/out/img_${id}.png`,
    file_name: `img_${id}.png`,
    batch_id: 'b1',
    model: 'krea2',
    preset_id: 3,
    caption: 'a lighthouse at dusk',
    prompt_used: 'A lighthouse on a rocky coast at dusk.',
    negative_used: null,
    seed: 321424,
    prompt_seed: 321424,
    width: 1216,
    height: 832,
    megapixels: 1.0,
    aspect_ratio: '3:2',
    loras: null,
    render_s: 12.5,
    comfy_prompt_id: 'p1',
    created_at: '2026-09-29 12:00:00',
    is_favorite: false,
    form_state: {},
    ...over,
  }
}

console.log('types/jobs.ts')

await check('jobChipLabel: state words and percentages', () => {
  assert.equal(jobs.jobChipLabel({ state: 'queued' }), 'queued')
  assert.equal(jobs.jobChipLabel({ state: 'running' }), 'running')
  assert.equal(jobs.jobChipLabel({ state: 'failed' }), 'failed')
  assert.equal(jobs.jobChipLabel({ state: 'running', value: 5, max: 10 }), '50%')
  assert.equal(jobs.jobChipLabel({ state: 'running', value: 1, max: 3 }), '33%')
})

await check('jobChipLabel: cancelling wins; bad progress numbers fall back', () => {
  assert.equal(
    jobs.jobChipLabel({ state: 'running', value: 5, max: 10, cancelling: true }),
    'cancelling…',
  )
  assert.equal(jobs.jobChipLabel({ state: 'running', value: 5, max: 0 }), 'running')
  assert.equal(jobs.jobChipLabel({ state: 'running', value: NaN, max: 10 }), 'running')
  assert.equal(jobs.jobChipLabel({ state: 'running', value: 12, max: 10 }), '100%')
  assert.equal(jobs.jobChipLabel({ state: 'running', value: -3, max: 10 }), '0%')
})

console.log('types/t2i.ts')

await check('withCurrentOption is re-exported from types/i2v', () => {
  assert.deepEqual(t.withCurrentOption([0.5, 1, 2], 1.5), [0.5, 1, 1.5, 2])
  assert.deepEqual(t.withCurrentOption([0.5, 1, 2], 1), [0.5, 1, 2])
})

await check('emptyFilter returns a fresh empty object each call', () => {
  const a = t.emptyFilter()
  assert.deepEqual(a, {})
  a.nudity = ['none']
  assert.deepEqual(t.emptyFilter(), {})
})

await check('cleanFilter drops empty arrays and unusable range ends, copies the rest', () => {
  const messy = {
    nudity: [],
    artistic_quality: { min: 0.3, max: 0.9 },
    erotic_score: null,
    males: { min: 0, max: NaN },
    females: { min: NaN, max: NaN },
    pornographic_score: {},
    aspect_ratios: ['3:2'],
    clothing_any: [],
    clothing_none: ['tie'],
    not_a_filter_key: ['x'],
  }
  const cleaned = t.cleanFilter(messy)
  assert.deepEqual(cleaned, {
    artistic_quality: { min: 0.3, max: 0.9 },
    males: { min: 0 },
    aspect_ratios: ['3:2'],
    clothing_none: ['tie'],
  })
  cleaned.aspect_ratios.push('2:3')
  assert.deepEqual(messy.aspect_ratios, ['3:2'], 'result must not alias the input')
  assert.deepEqual(t.cleanFilter(null), {})
  assert.deepEqual(t.cleanFilter(undefined), {})
})

await check('isFilterEmpty / activeFilterCount work on the cleaned filter', () => {
  assert.equal(t.isFilterEmpty({}), true)
  assert.equal(t.isFilterEmpty({ nudity: [] }), true)
  assert.equal(t.isFilterEmpty(null), true)
  assert.equal(t.isFilterEmpty({ nudity: ['none'] }), false)
  assert.equal(t.activeFilterCount({ nudity: ['none'], males: { min: 0, max: 1 }, clothing_any: [] }), 2)
  assert.equal(t.activeFilterCount(null), 0)
})

await check('effectiveCount: Fixed forces 1, others clamp to 1..max', () => {
  assert.equal(t.effectiveCount('fixed', 5, 32), 1)
  assert.equal(t.effectiveCount('increment', 5, 32), 5)
  assert.equal(t.effectiveCount('decrement', 500, 32), 32)
  assert.equal(t.effectiveCount('random', 0, 32), 1)
  assert.equal(t.effectiveCount('random', -4, 32), 1)
  assert.equal(t.effectiveCount('random', NaN, 32), 1)
  assert.equal(t.effectiveCount('increment', 2.6, 32), 3)
  assert.equal(t.effectiveCount('increment', 40), 32, 'default max is 32')
})

await check('isCountEffective: false only when Fixed overrides a typed count above 1', () => {
  assert.equal(t.isCountEffective('fixed', 5), false)
  assert.equal(t.isCountEffective('fixed', 1), true)
  assert.equal(t.isCountEffective('fixed', NaN), true)
  assert.equal(t.isCountEffective('increment', 5), true)
  assert.equal(t.isCountEffective('random', 5), true)
})

await check('effectiveBatchSize: Manual is locked at 1, Random clamps', () => {
  assert.equal(t.effectiveBatchSize('manual', 7, 500), 1)
  assert.equal(t.effectiveBatchSize('random', 7, 500), 7)
  assert.equal(t.effectiveBatchSize('random', 9999, 500), 500)
  assert.equal(t.effectiveBatchSize('random', 0, 500), 1)
  assert.equal(t.effectiveBatchSize('random', NaN, 500), 1)
  assert.equal(t.effectiveBatchSize('random', 900), 500, 'default max is 500')
})

await check('plannedImages multiplies the effective batch size and count', () => {
  const lim = { maxBatchSize: 500, maxCount: 32 }
  assert.equal(t.plannedImages('random', 'increment', 10, 4, lim), 40)
  assert.equal(t.plannedImages('random', 'fixed', 10, 4, lim), 10)
  assert.equal(t.plannedImages('manual', 'increment', 10, 5, lim), 5)
  assert.equal(t.plannedImages('manual', 'fixed', 10, 5, lim), 1)
  assert.equal(t.plannedImages('random', 'random', 1000, 1000, lim), 500 * 32)
})

await check('t2iImageDetails: seed and size, with gaps handled', () => {
  assert.equal(t.t2iImageDetails(img(1)), 'seed 321424 · 1216×832')
  assert.equal(t.t2iImageDetails(img(1, { seed: 0 })), 'seed 0 · 1216×832')
  assert.equal(t.t2iImageDetails(img(1, { seed: null })), 'seed — · 1216×832')
  assert.equal(t.t2iImageDetails(img(1, { width: null, height: null })), 'seed 321424')
  assert.equal(t.t2iImageDetails(img(1, { width: 1216, height: null })), 'seed 321424')
})

await check('t2iImageTitle: model, then the start of the prompt on one line', () => {
  assert.equal(
    t.t2iImageTitle(img(1)),
    'krea2\nA lighthouse on a rocky coast at dusk.',
  )
  assert.equal(
    t.t2iImageTitle(img(1, { prompt_used: 'a  red\nfox\n\nruns' })),
    'krea2\na red fox runs',
  )
  assert.equal(t.t2iImageTitle(img(1, { model: null, prompt_used: null })), 'unknown model')
  const long = 'w'.repeat(400)
  const title = t.t2iImageTitle(img(1, { prompt_used: long }))
  assert.equal(title, `krea2\n${'w'.repeat(160)}…`)
})

await check('toStripItem maps a row onto the ThumbStrip item shape', () => {
  const row = img(9, { is_favorite: true })
  assert.deepEqual(t.toStripItem(row), {
    id: 9,
    file_path: '/out/img_9.png',
    is_favorite: true,
    title: t.t2iImageTitle(row),
    label: 'seed 321424 · 1216×832',
  })
})

await check('t2iImageToMedia gives MediaViewer a complete, neutral Media', () => {
  const media = t2i.t2iImageToMedia(img(4, { is_favorite: true }))
  assert.deepEqual(media, {
    file_path: '/out/img_4.png',
    file_name: 'img_4.png',
    is_favorite: true,
    is_video: false,
    playback_speed: null,
    width: 1216,
    height: 832,
    file_size: 0,
    frame_rate: null,
    duration: null,
    media_type: 'image',
  })
  const bare = t2i.t2iImageToMedia(img(5, { width: null, height: null }))
  assert.deepEqual([bare.width, bare.height], [0, 0])
})

await check('formatT2iTimestamp: SQLite UTC gets a Z before it is formatted', () => {
  const opts = { dateStyle: 'medium', timeStyle: 'short' }
  const asUtc = new Date('2026-09-29T12:00:00Z').toLocaleString(undefined, opts)
  const asLocal = new Date('2026-09-29T12:00:00').toLocaleString(undefined, opts)
  assert.notEqual(asUtc, asLocal, 'harness TZ must make UTC and local differ')
  assert.equal(t.formatT2iTimestamp('2026-09-29 12:00:00'), asUtc)
  assert.equal(t.formatT2iTimestamp('2026-09-29T12:00:00Z'), asUtc)
  assert.equal(t.formatT2iTimestamp('2026-09-29T08:00:00-04:00'), asUtc)
  assert.equal(t.formatT2iTimestamp(null), '')
  assert.equal(t.formatT2iTimestamp(''), '')
  assert.equal(t.formatT2iTimestamp('not a date'), 'not a date')
})

await check('parseSqliteUtc returns a Date or null', () => {
  assert.equal(t.parseSqliteUtc('2026-09-29 12:00:00').toISOString(), '2026-09-29T12:00:00.000Z')
  assert.equal(t.parseSqliteUtc('garbage'), null)
  assert.equal(t.parseSqliteUtc(undefined), null)
})

await check('seed helpers: randomSeed range, parseSeedInput, clampSeed', () => {
  assert.equal(t.SEED_MAX, 2147483647)
  const real = Math.random
  try {
    Math.random = () => 0
    assert.equal(t.randomSeed(), 0)
    Math.random = () => 0.9999999999
    assert.equal(t.randomSeed(), 2147483647)
    assert.ok(t.randomSeed(10) <= 10)
  } finally {
    Math.random = real
  }
  for (let i = 0; i < 500; i++) {
    const s = t.randomSeed()
    assert.ok(Number.isInteger(s) && s >= 0 && s <= t.SEED_MAX)
  }
  assert.equal(t.parseSeedInput('123'), 123)
  assert.equal(t.parseSeedInput(' 42 '), 42)
  assert.equal(t.parseSeedInput('0'), 0)
  assert.equal(t.parseSeedInput('2147483647'), 2147483647)
  assert.equal(t.parseSeedInput('2147483648'), null)
  assert.equal(t.parseSeedInput(''), null)
  assert.equal(t.parseSeedInput('-5'), null)
  assert.equal(t.parseSeedInput('1.5'), null)
  assert.equal(t.parseSeedInput('12abc'), null)
  assert.equal(t.clampSeed(-4), 0)
  assert.equal(t.clampSeed(3e9), 2147483647)
  assert.equal(t.clampSeed(77), 77)
})

await check('mergeImagePage keeps older loaded pages behind a full fresh page', () => {
  const ids = (r) => r.images.map((i) => i.id)
  const mk = (...nums) => nums.map((n) => img(n))
  // first load
  let r = t.mergeImagePage([], mk(3, 2, 1), 3, false)
  assert.deepEqual(ids(r), [3, 2, 1])
  assert.equal(r.hasMore, true)
  // short page = the whole library
  r = t.mergeImagePage(mk(9, 8, 7), mk(2, 1), 3, true)
  assert.deepEqual(ids(r), [2, 1])
  assert.equal(r.hasMore, false)
  // full fresh page in front of already-loaded older ones
  r = t.mergeImagePage(mk(9, 8, 7, 6, 5, 4), mk(10, 9, 8), 3, true)
  assert.deepEqual(ids(r), [10, 9, 8, 7, 6, 5, 4])
  assert.equal(r.hasMore, true)
  // the end had been reached before: it stays reached
  r = t.mergeImagePage(mk(3, 2, 1), mk(4, 3, 2), 3, false)
  assert.deepEqual(ids(r), [4, 3, 2, 1])
  assert.equal(r.hasMore, false)
  // fresh rows replace stale copies of the same ids
  const stale = [img(3, { is_favorite: false })]
  r = t.mergeImagePage(stale, [img(3, { is_favorite: true }), img(2), img(1)], 3, false)
  assert.equal(r.images[0].is_favorite, true)
})

await check('mergeOlderPage appends new rows only, newest first', () => {
  const mk = (...nums) => nums.map((n) => img(n))
  const merged = t.mergeOlderPage(mk(9, 8, 7), mk(7, 6, 5))
  assert.deepEqual(merged.map((i) => i.id), [9, 8, 7, 6, 5])
})

await check('batchStatusLine composes batch, images, failures and phase', () => {
  const base = {
    batch_id: 'b',
    mode: 'random',
    state: 'rendering',
    total_steps: 40,
    step: { step: 12, total_steps: 40, caption: '', aspect_ratio: '3:2', seed: 1, prompt: '', negative: null, warnings: [] },
    images_total: 160,
    images_done: 35,
    images_failed: 2,
    next_seed: null,
    started_at: '',
    last_error: null,
  }
  assert.equal(t.batchStatusLine(base), 'Batch 12/40 · 37/160 images · 2 failed · rendering')
  assert.equal(
    t.batchStatusLine({ ...base, state: 'prompting', images_done: 0, images_failed: 0, step: { ...base.step, step: 3 } }),
    'Batch 3/40 · writing prompts',
  )
  assert.equal(
    t.batchStatusLine({ ...base, mode: 'manual', total_steps: 1, images_total: 4, images_done: 1, images_failed: 0 }),
    '1/4 images · rendering',
  )
  assert.equal(
    t.batchStatusLine({ ...base, step: null, images_done: 0, images_failed: 0 }),
    'rendering',
  )
})

await check('sanitizeDraft keeps valid fields only', () => {
  assert.equal(t.sanitizeDraft(null), null)
  assert.equal(t.sanitizeDraft('x'), null)
  assert.equal(t.sanitizeDraft([]), null)
  assert.equal(t.sanitizeDraft({ mode: 'weird', seed: -1, megapixels: 0, aspect_ratio: 'wide', model: '' }), null)
  const cleaned = t.sanitizeDraft({
    mode: 'random',
    caption: 'x',
    prompt: null,
    seed: 5,
    model: 'sd',
    megapixels: 1,
    aspect_ratio: '3:2',
    preset_id: 7,
    filter: { nudity: ['none'], males: { min: 0, max: NaN } },
    loras: [
      { name: 'a', strength: 0.5 },
      { name: '', strength: 1 },
      { name: 'b', strength: 'x' },
    ],
    junk: 1,
  })
  assert.deepEqual(cleaned, {
    mode: 'random',
    caption: 'x',
    prompt: null,
    seed: 5,
    model: 'sd',
    megapixels: 1,
    aspect_ratio: '3:2',
    preset_id: 7,
    filter: { nudity: ['none'], males: { min: 0 } },
    loras: [{ name: 'a', strength: 0.5 }],
  })
  assert.deepEqual(t.sanitizeDraft({ filter: null, seed: 0 }), { filter: null, seed: 0 })
  assert.deepEqual(t.sanitizeDraft({ filter: [1], seed: 1 }), { seed: 1 })
  assert.equal(t.DRAFT_STORAGE_KEY, 'metascan.t2i.draft.v1')
})

console.log('api/t2i.ts')

let failWith = null
const calls = installFetch((c) => {
  if (failWith) {
    const f = failWith
    failWith = null
    return f
  }
  if (c.url.includes('/t2i/images/') && c.method === 'DELETE') return { json: { status: 'deleted' } }
  return { json: { ok: true } }
})
const last = () => calls[calls.length - 1]

await check('GET helpers hit the pinned paths', async () => {
  await api.fetchT2iConfig()
  assert.deepEqual([last().method, last().url], ['GET', '/api/t2i/config'])
  await api.fetchCaptionMeta()
  assert.equal(last().url, '/api/t2i/captions/meta')
  await api.listT2iBatches()
  assert.equal(last().url, '/api/t2i/batches')
  await api.listT2iPaths()
  assert.equal(last().url, '/api/t2i/paths')
})

await check('t2iOutputPreview encodes root and prefix', async () => {
  await api.t2iOutputPreview('/a b/c', '/%Y-%m-%d/t2i_')
  assert.equal(
    last().url,
    '/api/t2i/output-preview?root=%2Fa%20b%2Fc&prefix=%2F%25Y-%25m-%25d%2Ft2i_',
  )
})

await check('caption endpoints wrap the filter in {filter}', async () => {
  await api.countCaptions({ nudity: ['none'] })
  assert.deepEqual([last().method, last().url, last().body], [
    'POST',
    '/api/t2i/captions/count',
    { filter: { nudity: ['none'] } },
  ])
  await api.randomCaption({})
  assert.deepEqual([last().url, last().body], ['/api/t2i/captions/random', { filter: {} }])
  await api.resolveCaption({ caption: '__ALICE__ waves.', seed: 7, model: 'krea2' })
  assert.deepEqual([last().url, last().body], [
    '/api/t2i/captions/resolve',
    { caption: '__ALICE__ waves.', seed: 7, model: 'krea2' },
  ])
})

await check('generateT2iPrompt posts {caption, seed, model}', async () => {
  await api.generateT2iPrompt({ caption: 'a cat', seed: 1, model: 'sd' })
  assert.deepEqual([last().method, last().url, last().body], [
    'POST',
    '/api/t2i/prompt',
    { caption: 'a cat', seed: 1, model: 'sd' },
  ])
})

await check('startT2iBatch posts the request body verbatim; cancel posts no body', async () => {
  const req = {
    mode: 'manual',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    seed: 5,
    seed_policy: 'fixed',
    prompt: 'p',
    aspect_ratio: '3:2',
    loras: [{ name: 'x.safetensors', strength: 0.8 }],
  }
  await api.startT2iBatch(req)
  assert.deepEqual([last().method, last().url, last().body], ['POST', '/api/t2i/batches', req])
  await api.cancelT2iBatch('abc123')
  assert.deepEqual([last().method, last().url, last().body], [
    'POST',
    '/api/t2i/batches/abc123/cancel',
    undefined,
  ])
})

await check('listT2iImages: limit defaults to 60, before_id is optional', async () => {
  await api.listT2iImages()
  assert.equal(last().url, '/api/t2i/images?limit=60')
  await api.listT2iImages(30, 99)
  assert.equal(last().url, '/api/t2i/images?limit=30&before_id=99')
  await api.listT2iImages(60, 0)
  assert.equal(last().url, '/api/t2i/images?limit=60&before_id=0')
})

await check('patchT2iImage PATCHes the partial form; deleteT2iImage returns {status}', async () => {
  await api.patchT2iImage(7, { seed: 5, prompt: 'x' })
  assert.deepEqual([last().method, last().url, last().body], [
    'PATCH',
    '/api/t2i/images/7',
    { seed: 5, prompt: 'x' },
  ])
  const res = await api.deleteT2iImage(7)
  assert.deepEqual([last().method, last().url], ['DELETE', '/api/t2i/images/7'])
  assert.deepEqual(res, { status: 'deleted' })
})

await check('a 404 surfaces as ApiError carrying the server detail', async () => {
  failWith = { status: 404, json: { detail: 'No captions match the filter' } }
  await assert.rejects(
    () => api.randomCaption({}),
    (e) =>
      e instanceof ApiError &&
      e.status === 404 &&
      e.detail === 'No captions match the filter' &&
      e.message === 'No captions match the filter',
  )
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task14.mjs`
Expected: FAIL (exit code 1) — `Error: Failed to load url /src/types/jobs.ts (resolved id: /src/types/jobs.ts). Does the file exist?`
- [ ] **Step 3: Implement**

`frontend/src/types/jobs.ts`:
```ts
/**
 * Neutral types for the generation UI pieces in components/generation/ and
 * composables/useJobTracker.ts. T2I is the first user; I2V keeps its own
 * I2vJobChip until it is moved over. Nothing here knows about a feature.
 */

/** One ComfyUI job as a strip tile shows it: in flight, or failed and not yet dismissed. */
export interface JobChip {
  state: 'queued' | 'running' | 'failed'
  // A cancel request is in flight; the tile's button is disabled until the
  // job_update -> cancelled event (or the request failing) settles it.
  cancelling?: boolean
  // Sampler progress from the comfy channel's job_progress event.
  value?: number
  max?: number
  // The server's error text, shown verbatim on a failed tile.
  error?: string | null
}

/**
 * The tile's headline: "cancelling…", a percentage once the sampler reports
 * progress, otherwise the bare state word. Progress numbers that do not add
 * up (NaN, max of 0) fall back to the state word rather than showing "NaN%".
 */
export function jobChipLabel(chip: JobChip): string {
  if (chip.cancelling) return 'cancelling…'
  if (
    chip.state === 'running' &&
    typeof chip.value === 'number' &&
    typeof chip.max === 'number' &&
    Number.isFinite(chip.value) &&
    Number.isFinite(chip.max) &&
    chip.max > 0
  ) {
    const pct = Math.round((chip.value / chip.max) * 100)
    return `${Math.min(100, Math.max(0, pct))}%`
  }
  return chip.state
}

/** One image tile in a ThumbStrip; the caller builds `title` (tooltip) and `label`. */
export interface ThumbItem {
  id: number | string
  file_path: string
  is_favorite: boolean
  title: string
  label: string
}
```

`frontend/src/types/t2i.ts`:
```ts
import type { Media } from './media'
import type { ThumbItem } from './jobs'

// Reused, not copied: a select's options plus a stored value that has since
// left the config list (types/i2v.ts owns the one implementation).
export { withCurrentOption } from './i2v'

// ---- config ---------------------------------------------------------------

export type T2iMode = 'manual' | 'random'
export type SeedPolicy = 'fixed' | 'increment' | 'decrement' | 'random'
export type IdentityStyle = 'ref' | 'noun' | 'name'
export type T2iContentMode = 'uncensored' | 'sfw' | 'default'

export interface T2iModelInfo {
  id: string
  label: string
  has_negative: boolean
  identity: IdentityStyle
}

/** GET /api/t2i/config: the sanitised `t2i` config section plus server-derived facts. */
export interface T2iConfig {
  output_root: string
  output_prefix: string
  megapixels: number[]
  default_megapixels: number
  default_model: string
  model_workflows: Record<string, number | null>
  content_mode: T2iContentMode
  identity: Record<string, IdentityStyle>
  window: number
  max_batch_size: number
  max_count_per_batch: number
  models: T2iModelInfo[]
  aspect_ratios: string[]
  seed_policies: SeedPolicy[]
  seed_max: number
  csv: { available: boolean; total: number; error: string | null }
  wildcards: { slots: string[]; warnings: string[] }
}

/** GET /api/t2i/output-preview: always 200, problems come back as `error`. */
export interface T2iOutputPreview {
  path: string | null
  error: string | null
  warnings: string[]
}

// Used when the config has not loaded yet; the same defaults get_t2i_config applies.
export const DEFAULT_MAX_BATCH_SIZE = 500
export const DEFAULT_MAX_COUNT_PER_BATCH = 32

// ---- caption filter and meta ------------------------------------------------

/** A numeric filter range: the server takes "min and/or max", so either end may be left out. */
export interface Range {
  min?: number
  max?: number
}

/**
 * POST /api/t2i/captions/{count,random} and BatchRequest.filter. Every key is
 * optional and an absent key means "no restriction"; cleanFilter removes what
 * restricts nothing (empty lists, ranges with no usable end) so the wire shape
 * stays minimal. The meta column `clothing` (type "tags") maps to the two keys
 * clothing_any / clothing_none.
 */
export interface CaptionFilter {
  nudity?: string[]
  artistic_quality?: Range
  erotic_score?: Range
  pornographic_score?: Range
  males?: Range
  females?: Range
  aspect_ratios?: string[]
  clothing_any?: string[]
  clothing_none?: string[]
}

export interface CaptionMetaOption {
  value: string
  count: number
}

/** A choice column (nudity, aspect_ratios) or the clothing tag list, options by count desc. */
export interface CaptionMetaChoice {
  key: string
  label: string
  type: 'choice' | 'tags'
  options: CaptionMetaOption[]
}

/** A float score column (artistic_quality, erotic_score, pornographic_score), step 0.05. */
export interface CaptionMetaRange {
  key: string
  label: string
  type: 'range'
  min: number
  max: number
  step: number
}

/** An integer count column (males, females). */
export interface CaptionMetaIntRange {
  key: string
  label: string
  type: 'int_range'
  min: number
  max: number
}

export type CaptionMetaColumn = CaptionMetaChoice | CaptionMetaRange | CaptionMetaIntRange

/** GET /api/t2i/captions/meta. Only columns present in the CSV appear. */
export interface CaptionMeta {
  total: number
  columns: CaptionMetaColumn[]
}

export interface CaptionCount {
  count: number
  total: number
}

/** One CSV row, as POST /api/t2i/captions/random returns it. */
export interface CaptionRow {
  id: number
  caption: string
  aspect_ratio: string
  nudity: string | null
  artistic_quality: number | null
  erotic_score: number | null
  pornographic_score: number | null
  males: number | null
  females: number | null
  clothing: string[]
}

const FILTER_LIST_KEYS = ['nudity', 'aspect_ratios', 'clothing_any', 'clothing_none'] as const
const FILTER_RANGE_KEYS = [
  'artistic_quality',
  'erotic_score',
  'pornographic_score',
  'males',
  'females',
] as const

export function emptyFilter(): CaptionFilter {
  return {}
}

/**
 * A copy of the filter with everything that means "no restriction" removed:
 * empty lists, null/missing ranges, and any range end that is not a finite
 * number (a range keeps whichever end is usable). Only the known filter keys
 * survive. This is the shape that goes on the wire.
 */
export function cleanFilter(filter: CaptionFilter | null | undefined): CaptionFilter {
  const out: CaptionFilter = {}
  if (!filter) return out
  for (const key of FILTER_LIST_KEYS) {
    const list = filter[key]
    if (Array.isArray(list) && list.length > 0) out[key] = [...list]
  }
  for (const key of FILTER_RANGE_KEYS) {
    const range = filter[key]
    if (!range) continue
    const cleaned: Range = {}
    if (typeof range.min === 'number' && Number.isFinite(range.min)) cleaned.min = range.min
    if (typeof range.max === 'number' && Number.isFinite(range.max)) cleaned.max = range.max
    if (cleaned.min !== undefined || cleaned.max !== undefined) out[key] = cleaned
  }
  return out
}

export function activeFilterCount(filter: CaptionFilter | null | undefined): number {
  return Object.keys(cleanFilter(filter)).length
}

export function isFilterEmpty(filter: CaptionFilter | null | undefined): boolean {
  return activeFilterCount(filter) === 0
}

// ---- form state, images ----------------------------------------------------

export interface T2iLora {
  name: string
  strength: number
}

/**
 * The EDITABLE copy of the dialog's form kept on each image (spec 7.3).
 * Seeded from the request at ingest, loaded when a tile is selected and
 * autosaved into. Seed policy, Batch Size and Count per Batch are
 * deliberately not part of it (session-local). The server always sends a
 * complete one; individual values can still be null, `model` included (an
 * image ingested without a recorded model), but a PATCH must never send a
 * null or empty `model`.
 */
export interface T2iFormState {
  mode: T2iMode
  filter: CaptionFilter | null
  caption: string | null
  model: string | null
  preset_id: number | null
  megapixels: number | null
  aspect_ratio: string | null
  seed: number | null
  prompt: string | null
  negative: string | null
  loras: T2iLora[]
}

/** One row of GET /api/t2i/images. The as-rendered fields are facts and never change. */
export interface T2iImage {
  id: number
  file_path: string
  file_name: string
  batch_id: string | null
  model: string | null
  preset_id: number | null
  caption: string | null
  prompt_used: string | null
  negative_used: string | null
  seed: number | null
  prompt_seed: number | null
  width: number | null
  height: number | null
  megapixels: number | null
  aspect_ratio: string | null
  loras: T2iLora[] | null
  render_s: number | null
  comfy_prompt_id: string | null
  // SQLite datetime('now'): UTC with no zone marker (see formatT2iTimestamp).
  created_at: string
  is_favorite: boolean
  form_state: T2iFormState
}

/** POST /api/t2i/prompt. Review-only: nothing is written. */
export interface T2iPromptResult {
  prompt: string
  negative: string | null
  resolved_caption: string
  warnings: string[]
}

/** POST /api/t2i/captions/resolve. */
export interface T2iResolveResult {
  resolved_caption: string
  characters: Record<string, Record<string, string>>
  warnings: string[]
}

// ---- batches ---------------------------------------------------------------

/**
 * POST /api/t2i/batches body. caption / prompt / negative / aspect_ratio are
 * Manual fields, `filter` is a Random field.
 */
export interface T2iBatchRequest {
  mode: T2iMode
  model: string
  preset_id: number
  megapixels: number
  seed: number
  seed_policy: SeedPolicy
  batch_size?: number
  count_per_batch?: number
  loras?: T2iLora[]
  caption?: string | null
  prompt?: string | null
  negative?: string | null
  aspect_ratio?: string | null
  filter?: CaptionFilter | null
}

/** POST /api/t2i/batches response. */
export interface T2iBatchStarted {
  batch_id: string
  total_images: number
  warnings: string[]
}

/** What the store's startBatch resolves with: the response plus the next unused seed, when known. */
export interface T2iStartResult extends T2iBatchStarted {
  next_seed: number | null
}

/** The current step of a batch: the caption / prompt / aspect / seed the dialog mirrors. */
export interface T2iBatchStep {
  step: number
  total_steps: number
  caption: string
  aspect_ratio: string
  seed: number
  prompt: string
  negative: string | null
  warnings: string[]
}

/** One entry of GET /api/t2i/batches. */
export interface T2iBatchInfo {
  batch_id: string
  mode: T2iMode
  state: 'prompting' | 'rendering'
  total_steps: number
  step: T2iBatchStep | null
  images_total: number
  images_done: number
  images_failed: number
  next_seed: number | null
  started_at: string
}

/** A batch the store is tracking: the server's snapshot plus the last per-image error. */
export interface T2iActiveBatch extends T2iBatchInfo {
  last_error: string | null
}

/** Why a tracked batch stopped. */
export type T2iBatchOutcome = 'complete' | 'cancelled' | 'error'

/** What the store keeps of the batch that ended most recently (the dialog unlocks from it). */
export interface T2iFinishedBatch {
  batch_id: string
  mode: T2iMode
  outcome: T2iBatchOutcome
  images_done: number
  images_failed: number
  next_seed: number | null
  // The last step's values; for a Random batch these fill Caption / Prompt / Aspect.
  step: T2iBatchStep | null
  error: string | null
}

// ---- WebSocket `t2i` channel payloads (spec section 9) ---------------------

export interface T2iBatchStartedEvent {
  batch_id: string
  mode: T2iMode
  total_steps: number
  total_images: number
}

export interface T2iBatchStepEvent extends T2iBatchStep {
  batch_id: string
}

export interface T2iBatchProgressEvent {
  batch_id: string
  phase: 'prompting' | 'rendering'
  images_done: number
  images_failed: number
  images_total: number
  next_seed: number | null
  last_error?: string | null
}

/** batch_complete and batch_cancelled. */
export interface T2iBatchEndEvent {
  batch_id: string
  images_done: number
  images_failed: number
}

export interface T2iBatchErrorEvent {
  batch_id: string
  error: string
}

export interface T2iImagesChangedEvent {
  batch_id: string
  files: string[]
}

// ---- seeds -----------------------------------------------------------------

export const SEED_MAX = 2 ** 31 - 1

/** A uniformly random seed in 0..max (inclusive). */
export function randomSeed(max: number = SEED_MAX): number {
  return Math.floor(Math.random() * (max + 1))
}

/** Pins a number into 0..max as an integer; NaN becomes 0. */
export function clampSeed(n: number, max: number = SEED_MAX): number {
  if (!Number.isFinite(n)) return 0
  return Math.min(max, Math.max(0, Math.round(n)))
}

/** Strict parse of what a user typed: digits only and within 0..max, else null. */
export function parseSeedInput(raw: string, max: number = SEED_MAX): number | null {
  const text = raw.trim()
  if (!/^\d+$/.test(text)) return null
  const n = Number(text)
  return n <= max ? n : null
}

// ---- counts ----------------------------------------------------------------

function clampInt(value: number, min: number, max: number): number {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

/**
 * The Count per Batch the dialog actually sends. Fixed forces 1 — the same
 * seed would render the same image N times — while the field keeps whatever
 * was typed for when the policy changes back.
 */
export function effectiveCount(
  policy: SeedPolicy,
  count: number,
  max: number = DEFAULT_MAX_COUNT_PER_BATCH,
): number {
  if (policy === 'fixed') return 1
  return clampInt(count, 1, max)
}

/**
 * Whether the typed count is honoured as typed. False only when Fixed is
 * overriding a count above 1 — the moment to grey the field and say
 * "ignored while the seed is Fixed".
 */
export function isCountEffective(policy: SeedPolicy, count: number): boolean {
  return policy !== 'fixed' || clampInt(count, 1, Number.MAX_SAFE_INTEGER) === 1
}

/** The Batch Size the dialog actually sends: Manual is locked at 1. */
export function effectiveBatchSize(
  mode: T2iMode,
  size: number,
  max: number = DEFAULT_MAX_BATCH_SIZE,
): number {
  if (mode === 'manual') return 1
  return clampInt(size, 1, max)
}

/** Images one Generate produces: the "Generate ×N" number. */
export function plannedImages(
  mode: T2iMode,
  policy: SeedPolicy,
  batchSize: number,
  count: number,
  limits: { maxBatchSize?: number; maxCount?: number } = {},
): number {
  return (
    effectiveBatchSize(mode, batchSize, limits.maxBatchSize ?? DEFAULT_MAX_BATCH_SIZE) *
    effectiveCount(policy, count, limits.maxCount ?? DEFAULT_MAX_COUNT_PER_BATCH)
  )
}

// ---- image labels ------------------------------------------------------------

/** The label under a tile: "seed 321424 · 1216×832" (size omitted when unknown). */
export function t2iImageDetails(img: Pick<T2iImage, 'seed' | 'width' | 'height'>): string {
  const parts = [`seed ${img.seed ?? '—'}`]
  if (img.width && img.height) parts.push(`${img.width}×${img.height}`)
  return parts.join(' · ')
}

const TITLE_PROMPT_CHARS = 160

/** A tile's tooltip: the model, then the start of the prompt collapsed onto one line. */
export function t2iImageTitle(img: Pick<T2iImage, 'model' | 'prompt_used'>): string {
  const lines = [img.model ?? 'unknown model']
  const prompt = (img.prompt_used ?? '').replace(/\s+/g, ' ').trim()
  if (prompt) {
    lines.push(
      prompt.length > TITLE_PROMPT_CHARS ? `${prompt.slice(0, TITLE_PROMPT_CHARS)}…` : prompt,
    )
  }
  return lines.join('\n')
}

export function toStripItem(img: T2iImage): ThumbItem {
  return {
    id: img.id,
    file_path: img.file_path,
    is_favorite: img.is_favorite,
    title: t2iImageTitle(img),
    label: t2iImageDetails(img),
  }
}

/**
 * A library-shaped Media for MediaViewer: the viewer reads only the path, the
 * name, the favourite flag and the video flag, so the size and timing fields
 * a T2I row does not carry are neutral.
 */
export function t2iImageToMedia(img: T2iImage): Media {
  return {
    file_path: img.file_path,
    file_name: img.file_name,
    is_favorite: img.is_favorite,
    is_video: false,
    playback_speed: null,
    width: img.width ?? 0,
    height: img.height ?? 0,
    file_size: 0,
    frame_rate: null,
    duration: null,
    media_type: 'image',
  }
}

/**
 * SQLite's datetime('now') is UTC, space-separated, with no zone marker. Read
 * as-is a browser treats it as LOCAL time, so the marker is added before
 * parsing. Strings that already carry a zone are left alone. Null on garbage.
 */
export function parseSqliteUtc(raw: string | null | undefined): Date | null {
  if (!raw) return null
  const iso = /([zZ]|[+-]\d\d:?\d\d)$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

/** created_at formatted into the viewer's locale (mirrors formatI2vTimestamp). */
export function formatT2iTimestamp(raw: string | null | undefined): string {
  const d = parseSqliteUtc(raw)
  if (!d) return raw ?? ''
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// ---- image list paging -------------------------------------------------------

/**
 * Folds a freshly fetched first page into the list already held. A short page
 * is the whole library. A full page replaces everything at or above its last
 * id and keeps the older rows the user already paged in behind it, so a
 * refresh during a long batch never collapses "load older".
 */
export function mergeImagePage(
  existing: T2iImage[],
  fresh: T2iImage[],
  pageSize: number,
  prevHasMore: boolean,
): { images: T2iImage[]; hasMore: boolean } {
  if (fresh.length < pageSize) return { images: fresh, hasMore: false }
  const boundary = fresh[fresh.length - 1].id
  const older = existing.filter((i) => i.id < boundary)
  return { images: [...fresh, ...older], hasMore: older.length > 0 ? prevHasMore : true }
}

/** Appends an older page, skipping ids already present; newest first. */
export function mergeOlderPage(existing: T2iImage[], page: T2iImage[]): T2iImage[] {
  const seen = new Set(existing.map((i) => i.id))
  return [...existing, ...page.filter((i) => !seen.has(i.id))].sort((a, b) => b.id - a.id)
}

// ---- status line -------------------------------------------------------------

/**
 * "Batch 12/40 · 37/160 images · 2 failed · rendering". The batch part is
 * dropped for a single-step (Manual) batch; images appear once one finished.
 * With the VLM-unload GPU order the last step's prompt is written before any
 * image renders, so the batch part reads N/N while rendering.
 */
export function batchStatusLine(b: T2iActiveBatch): string {
  const parts: string[] = []
  if (b.step && b.total_steps > 1) parts.push(`Batch ${b.step.step}/${b.total_steps}`)
  const finished = b.images_done + b.images_failed
  if (finished > 0 && b.images_total > 0) parts.push(`${finished}/${b.images_total} images`)
  if (b.images_failed > 0) parts.push(`${b.images_failed} failed`)
  parts.push(b.state === 'prompting' ? 'writing prompts' : 'rendering')
  return parts.join(' · ')
}

// ---- scratch-form draft --------------------------------------------------------

export const DRAFT_STORAGE_KEY = 'metascan.t2i.draft.v1'

/**
 * What a stored draft is trusted to be: only fields of the right type
 * survive, so a hand-edited or older draft can never put a bad value in the
 * form. Null when nothing usable is left.
 */
export function sanitizeDraft(raw: unknown): Partial<T2iFormState> | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const out: Partial<T2iFormState> = {}

  if (r.mode === 'manual' || r.mode === 'random') out.mode = r.mode
  for (const key of ['caption', 'prompt', 'negative'] as const) {
    const v = r[key]
    if (typeof v === 'string' || v === null) out[key] = v
  }
  if (typeof r.model === 'string' && r.model) out.model = r.model
  if (r.preset_id === null || (typeof r.preset_id === 'number' && Number.isInteger(r.preset_id))) {
    out.preset_id = r.preset_id
  }
  if (
    r.megapixels === null ||
    (typeof r.megapixels === 'number' && Number.isFinite(r.megapixels) && r.megapixels > 0)
  ) {
    out.megapixels = r.megapixels
  }
  if (
    r.aspect_ratio === null ||
    (typeof r.aspect_ratio === 'string' && /^\d+:\d+$/.test(r.aspect_ratio))
  ) {
    out.aspect_ratio = r.aspect_ratio
  }
  if (
    r.seed === null ||
    (typeof r.seed === 'number' && Number.isInteger(r.seed) && r.seed >= 0 && r.seed <= SEED_MAX)
  ) {
    out.seed = r.seed
  }
  if (r.filter === null) {
    out.filter = null
  } else if (r.filter && typeof r.filter === 'object' && !Array.isArray(r.filter)) {
    out.filter = cleanFilter(r.filter as CaptionFilter)
  }
  if (Array.isArray(r.loras)) {
    out.loras = r.loras.flatMap((entry: unknown) => {
      const e = entry as { name?: unknown; strength?: unknown } | null
      return e &&
        typeof e.name === 'string' &&
        e.name &&
        typeof e.strength === 'number' &&
        Number.isFinite(e.strength)
        ? [{ name: e.name, strength: e.strength }]
        : []
    })
  }
  return Object.keys(out).length > 0 ? out : null
}
```

`frontend/src/api/t2i.ts`:
```ts
import { get, post, patch, del } from './client'
import type {
  CaptionCount,
  CaptionFilter,
  CaptionMeta,
  CaptionRow,
  T2iBatchInfo,
  T2iBatchRequest,
  T2iBatchStarted,
  T2iConfig,
  T2iFormState,
  T2iImage,
  T2iOutputPreview,
  T2iPromptResult,
  T2iResolveResult,
} from '../types/t2i'

export function fetchT2iConfig(): Promise<T2iConfig> {
  return get('/t2i/config')
}

export function fetchCaptionMeta(): Promise<CaptionMeta> {
  return get('/t2i/captions/meta')
}

// How many CSV rows match. An empty filter matches every row.
export function countCaptions(filter: CaptionFilter = {}): Promise<CaptionCount> {
  return post('/t2i/captions/count', { filter })
}

// One random matching row. Answers 404 (ApiError) when nothing matches.
export function randomCaption(filter: CaptionFilter = {}): Promise<CaptionRow> {
  return post('/t2i/captions/random', { filter })
}

// The caption with its __TOKEN__s resolved for this seed and model. Review
// only; nothing is written.
export function resolveCaption(body: {
  caption: string
  seed: number
  model: string
}): Promise<T2iResolveResult> {
  return post('/t2i/captions/resolve', body)
}

// Rewrites the caption into the model's prompt style with the local VLM (or
// the fallback prompt, with a warning, when no VLM is available). Review
// only; nothing is written.
export function generateT2iPrompt(body: {
  caption: string
  seed: number
  model: string
}): Promise<T2iPromptResult> {
  return post('/t2i/prompt', body)
}

// Starts a server-side batch that keeps running when the dialog closes.
export function startT2iBatch(body: T2iBatchRequest): Promise<T2iBatchStarted> {
  return post('/t2i/batches', body)
}

// Batches that are still running, with counters and the current step.
export function listT2iBatches(): Promise<T2iBatchInfo[]> {
  return get('/t2i/batches')
}

// Idempotent: cancelling a batch that already ended is not an error.
export function cancelT2iBatch(batchId: string): Promise<{ status: string }> {
  return post(`/t2i/batches/${encodeURIComponent(batchId)}/cancel`)
}

// Newest first. Pass the last id of the page you hold as `beforeId` for the next one.
export function listT2iImages(limit = 60, beforeId?: number): Promise<T2iImage[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (beforeId != null) params.set('before_id', String(beforeId))
  return get(`/t2i/images?${params.toString()}`)
}

// Autosave into one image's editable form state. Partial; the as-rendered
// facts are not reachable through this. Answers the whole updated row.
export function patchT2iImage(id: number, fields: Partial<T2iFormState>): Promise<T2iImage> {
  return patch(`/t2i/images/${id}`, fields)
}

// Deletes the row and its library media; the server moves the file to the OS trash.
export function deleteT2iImage(id: number): Promise<{ status: 'deleted' }> {
  return del(`/t2i/images/${id}`)
}

// Output paths of every T2I image still in the library — the "Generated with
// T2I" smart-folder rule's membership set.
export function listT2iPaths(): Promise<string[]> {
  return get('/t2i/paths')
}

// Where an image generated right now would land for these (possibly unsaved)
// settings. Always resolves: problems come back as `error`.
export function t2iOutputPreview(root: string, prefix: string): Promise<T2iOutputPreview> {
  const q = `root=${encodeURIComponent(root)}&prefix=${encodeURIComponent(prefix)}`
  return get(`/t2i/output-preview?${q}`)
}
```

`frontend/src/types/storyboard.ts` is an existing file: apply the diff with `git apply`.
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/types/storyboard.ts b/frontend/src/types/storyboard.ts
index a991491..fb3441c 100644
--- a/frontend/src/types/storyboard.ts
+++ b/frontend/src/types/storyboard.ts
@@ -303,4 +303,5 @@ export interface GenerationJob {
   finished_at: string | null
   output_dir: string | null
   i2v_source_path: string | null
+  t2i_batch_id: string | null
 }
PATCH
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task14.mjs`
Expected: `29 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 677ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build before Task 14 prints it too). Before this task the same command already exits 0 ("✓ built in 669ms").
- [ ] **Step 6: Commit**
```bash
git add frontend/src/types/jobs.ts \
    frontend/src/types/t2i.ts \
    frontend/src/api/t2i.ts \
    frontend/src/types/storyboard.ts
git commit -m "feat(t2i): frontend types, API module and neutral job types" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (the request layer against the real backend)

Nothing in the UI uses these modules yet, so exercise them from the browser console. Start the backend (`source venv/bin/activate && python run_server.py`) and the frontend (`cd frontend && npm run dev`), open http://localhost:5173, press F12 and paste:
```js
const api = await import('/src/api/t2i.ts')
const t2i = await import('/src/types/t2i.ts')

const cfg = await api.fetchT2iConfig()
console.log('models', cfg.models.map((m) => m.id), 'csv', cfg.csv)
const meta = await api.fetchCaptionMeta()
console.log('filter columns', meta.columns.map((c) => c.key))
console.log('count nudity=none', await api.countCaptions({ nudity: ['none'] }))
const rows = await api.listT2iImages(5)
console.log('newest images', rows.map((r) => [r.id, t2i.t2iImageDetails(r)]))

// Pure helpers
console.log(t2i.t2iImageDetails({ seed: 321424, width: 1216, height: 832 }))
console.log(t2i.effectiveCount('fixed', 5), t2i.effectiveCount('increment', 5))
console.log(t2i.cleanFilter({ nudity: [], males: { min: 0, max: 2 } }))
```
You should see, in this order (Chrome prefixes arrays with their length, for example `(4)`):
- `models` and the array `['krea2', 'qwen', 'sd', 'zimage']`, then `csv` and `{available: true, total: 82880, error: null}` (with the shipped CSV in `data/t2i_captions/`; a different `total` means a different CSV).
- `filter columns` and an array with `nudity`, `artistic_quality`, `erotic_score`, `pornographic_score`, `males`, `females`, `aspect_ratios`, `clothing` (only the columns your CSV has).
- `count nudity=none` and `{count: 2265, total: 82880}` for the shipped CSV.
- `newest images` and `[]` before the first generation, afterwards up to five `[id, 'seed N · W×H']` pairs.
- `seed 321424 · 1216×832`, then `1 5`, then `{males: {min: 0, max: 2}}`.

An `ApiError` (404 or 503) instead of the first four lines means the backend does not have the T2I routes yet (Task 12). Nothing here writes anything.

---

### Task 15: Shared generation components and job/autosave composables

**Files:**
- Create: `frontend/src/components/generation/JobTile.vue`
- Create: `frontend/src/components/generation/ThumbTile.vue`
- Create: `frontend/src/components/generation/ThumbStrip.vue`
- Create: `frontend/src/components/generation/SeedControls.vue`
- Create: `frontend/src/composables/useJobTracker.ts`
- Create: `frontend/src/composables/useFormAutosave.ts`
- Test: `frontend/t2i-checks.local/task15.mjs` (throwaway, git-ignored; needs the harness from Task 14)

I2V is not touched: these are new files that I2V may adopt later.

**Interfaces:**
- Consumes: `JobChip`, `jobChipLabel`, `ThumbItem` (Task 14, `types/jobs.ts`); `SEED_MAX`, `randomSeed(max?)`, `clampSeed(n, max?)`, `parseSeedInput(raw, max?)`, `SeedPolicy` (Task 14, `types/t2i.ts`); `thumbnailUrl(filePath)` (`api/client.ts`); `listJobs(state?, limit)`, `getJob(id)`, `cancelJob(id)` (`api/comfy.ts`); `GenerationJob` including `t2i_batch_id` (Task 14).
- Produces:
  - `useJobTracker(options: { match: (job: GenerationJob) => boolean })` returning `{ jobs: Ref<Map<number, JobChip>>, activeJobIds: ComputedRef<number[]>, track(jobId): void, dismiss(jobId): void, cancel(jobId): Promise<void>, cancelAll(): Promise<void>, refresh(): Promise<void>, handleComfyEvent(event: string, data: Record<string, unknown>): void, reset(): void }`; also `type JobTracker` and `interface JobTrackerOptions`.
  - `useFormAutosave<F extends object, I extends string | number = number>(options: FormAutosaveOptions<F, I>): FormAutosave` with `FormAutosaveOptions { form: MaybeRefOrGetter<F>; selectedId: MaybeRefOrGetter<I | null>; save: (id: I, fields: Partial<F>) => Promise<unknown>; delayMs?: number (600); savable?: (form: F) => Partial<F> }` and `FormAutosave { saveFailed: Ref<boolean>; flush(): void; cancelPending(): void; markLoaded(): void }`.
  - `<SeedControls>`: props `seed: number`, `policy: SeedPolicy`, `disabled?`, `max?` (default `SEED_MAX`), `label?` (default `'Seed'`, empty hides it); emits `update:seed(value: number)`, `update:policy(value: SeedPolicy)`.
  - `<JobTile>`: prop `chip: JobChip`; emits `cancel`, `dismiss`.
  - `<ThumbTile>`: props `item: ThumbItem`, `selected?`; emits `select(evt: MouseEvent)`, `open`, `favorite`, `delete`.
  - `<ThumbStrip>`: props `items: ThumbItem[]`, `selectedId: number | string | null`, `emptyText?`, `loading?`, `hasMore?`, `loadingMore?`, `tileSize?` (default 132); emits `select(item, evt)`, `open(index)`, `favorite(item)`, `delete(item)`, `more`; slot `jobs` renders before the tiles.

**Design notes.**
- A `job_update` for a job a server-side runner just queued carries no batch id, so `useJobTracker` looks an unknown id up once (`GET /api/comfy/jobs/{id}`) and adopts it when `match` accepts the row. A non-matching answer is remembered, a failed lookup is not, and a terminal frame that lands during the lookup wins over the row that was read.
- `refresh()` keeps failed chips (they wait for the user to dismiss them), drops queued/running chips the server no longer lists, and carries progress and a pending cancel over.
- `useFormAutosave`: switching items is `flush()` first, then load the new values, set `selectedId`, then `markLoaded()`. The item id and the fields are captured when a save is queued; only the item still on screen may touch `saveFailed`.
- `ThumbTile` ignores the second click of a double-click (`event.detail > 1`) so a double-click selects once and then opens; its overlay buttons stop `click` and `dblclick`.
- `ThumbStrip` shows `emptyText` only when there are no items and `loading` is false, so the caller passes `loading` until its first list load has landed and while a job tile is showing.

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/task15.mjs`)

The composables run as plain modules; the components are rendered to strings with Vue's server renderer.

`frontend/t2i-checks.local/task15.mjs`:
```js
// Task 15 logic checks: useJobTracker, useFormAutosave, and the shared
// generation components (rendered to strings through Vue's SSR renderer).
import { createSSRApp, h, nextTick, reactive, ref } from 'vue'
import { renderToString } from 'vue/server-renderer'
import {
  assert,
  check,
  deferred,
  finish,
  installFetch,
  installLocalStorage,
  load,
  settle,
  sleep,
} from './harness.mjs'

installLocalStorage()

const { useJobTracker } = await load('/src/composables/useJobTracker.ts')
const { useFormAutosave } = await load('/src/composables/useFormAutosave.ts')
const JobTile = (await load('/src/components/generation/JobTile.vue')).default
const ThumbTile = (await load('/src/components/generation/ThumbTile.vue')).default
const ThumbStrip = (await load('/src/components/generation/ThumbStrip.vue')).default
const SeedControls = (await load('/src/components/generation/SeedControls.vue')).default
const { ApiError } = await load('/src/api/client.ts')

const render = (component, props = {}, slots = {}) =>
  renderToString(createSSRApp({ render: () => h(component, props, slots) }))

// How many elements carry ALL of these classes (SSR may order them either way).
const withClasses = (html, ...tokens) =>
  [...html.matchAll(/class="([^"]*)"/g)]
    .map((m) => m[1].split(/\s+/))
    .filter((classes) => tokens.every((t) => classes.includes(t))).length

// ---- fake ComfyUI job endpoints ---------------------------------------------

const jobRows = new Map()
const cancelFails = new Set()
let getJobGate = null // a deferred: when set, GET /comfy/jobs/{id} waits for it
let getJobStatus = 200
const calls = installFetch(async (c) => {
  const list = c.url.match(/^\/api\/comfy\/jobs\?(.*)$/)
  if (list) {
    const state = new URLSearchParams(list[1]).get('state')
    return { json: [...jobRows.values()].filter((j) => j.state === state) }
  }
  const one = c.url.match(/^\/api\/comfy\/jobs\/(\d+)$/)
  if (one && c.method === 'GET') {
    if (getJobGate) await getJobGate.promise
    if (getJobStatus !== 200) return { status: getJobStatus, json: { detail: 'boom' } }
    const row = jobRows.get(Number(one[1]))
    return row ? { json: row } : { status: 404, json: { detail: 'no such job' } }
  }
  const cancel = c.url.match(/^\/api\/comfy\/jobs\/(\d+)\/cancel$/)
  if (cancel) {
    return cancelFails.has(Number(cancel[1]))
      ? { status: 409, json: { detail: 'cannot cancel' } }
      : { json: { status: 'cancelled' } }
  }
  return {}
})
const job = (id, state, t2iBatch = 'b1', extra = {}) => ({
  id,
  state,
  t2i_batch_id: t2iBatch,
  error: null,
  ...extra,
})
const isT2i = (j) => !!j.t2i_batch_id
const urlsFor = (needle) => calls.filter((c) => c.url.includes(needle))

console.log('composables/useJobTracker.ts')

await check('refresh(): queued/running rows that match become chips, others are ignored', async () => {
  jobRows.clear()
  jobRows.set(1, job(1, 'queued'))
  jobRows.set(2, job(2, 'running'))
  jobRows.set(3, job(3, 'queued', null)) // someone else's job
  jobRows.set(4, job(4, 'done'))
  const t = useJobTracker({ match: isT2i })
  await t.refresh()
  assert.deepEqual([...t.jobs.value.keys()].sort(), [1, 2])
  assert.equal(t.jobs.value.get(1).state, 'queued')
  assert.equal(t.jobs.value.get(2).state, 'running')
  assert.deepEqual(t.activeJobIds.value.sort(), [1, 2])
  const listCalls = calls.filter((c) => /\/comfy\/jobs\?/.test(c.url)).slice(-2)
  assert.ok(listCalls.every((c) => c.url.includes('limit=1000')))
})

await check('refresh(): failed chips survive, stale queued/running chips go, progress is kept', async () => {
  jobRows.clear()
  jobRows.set(2, job(2, 'running'))
  const t = useJobTracker({ match: isT2i })
  t.track(9) // a job the server no longer lists
  t.handleComfyEvent('job_update', { job_id: 9, state: 'failed', error: 'boom' })
  t.track(8) // queued locally, gone from the server
  t.track(2)
  t.handleComfyEvent('job_progress', { job_id: 2, value: 3, max: 4 })
  await t.refresh()
  assert.deepEqual([...t.jobs.value.keys()].sort(), [2, 9])
  assert.equal(t.jobs.value.get(9).state, 'failed')
  assert.equal(t.jobs.value.get(9).error, 'boom')
  assert.equal(t.jobs.value.get(2).value, 3)
  assert.deepEqual(t.activeJobIds.value, [2], 'a failed chip is not cancellable')
})

await check('job_update on a tracked job: running, failed (kept), done and cancelled (removed)', () => {
  const t = useJobTracker({ match: isT2i })
  t.track(5)
  assert.equal(t.jobs.value.get(5).state, 'queued')
  t.handleComfyEvent('job_update', { job_id: 5, state: 'running', error: null })
  assert.equal(t.jobs.value.get(5).state, 'running')
  t.handleComfyEvent('job_update', { job_id: 5, state: 'failed', error: 'KSampler: out of memory' })
  assert.deepEqual(t.jobs.value.get(5), { state: 'failed', error: 'KSampler: out of memory' })
  t.track(6)
  t.handleComfyEvent('job_update', { job_id: 6, state: 'done', error: null })
  assert.equal(t.jobs.value.has(6), false)
  t.track(7)
  t.handleComfyEvent('job_update', { job_id: 7, state: 'cancelled', error: null })
  assert.equal(t.jobs.value.has(7), false)
})

await check('job_progress records value/max; garbage progress keeps the last good one', () => {
  const t = useJobTracker({ match: isT2i })
  t.track(5)
  t.handleComfyEvent('job_progress', { job_id: 5, value: 5, max: 20 })
  assert.deepEqual(t.jobs.value.get(5), { state: 'running', value: 5, max: 20 })
  t.handleComfyEvent('job_progress', { job_id: 5, value: null, max: 'x' })
  assert.deepEqual(t.jobs.value.get(5), { state: 'running', value: 5, max: 20 })
  t.handleComfyEvent('job_progress', { job_id: 77, value: 1, max: 2 })
  assert.equal(t.jobs.value.has(77), false, 'progress for an unknown job is ignored')
})

await check('a pending cancel survives progress and state updates', () => {
  const t = useJobTracker({ match: isT2i })
  t.track(5)
  t.jobs.value = new Map([[5, { state: 'queued', cancelling: true }]])
  t.handleComfyEvent('job_update', { job_id: 5, state: 'running', error: null })
  assert.equal(t.jobs.value.get(5).cancelling, true)
  t.handleComfyEvent('job_progress', { job_id: 5, value: 1, max: 4 })
  assert.deepEqual(t.jobs.value.get(5), { state: 'running', value: 1, max: 4, cancelling: true })
})

await check('an unknown job that matches is adopted from GET /comfy/jobs/{id}', async () => {
  jobRows.clear()
  jobRows.set(20, job(20, 'queued', 'b7'))
  const t = useJobTracker({ match: isT2i })
  t.handleComfyEvent('job_update', { job_id: 20, state: 'queued', error: null })
  assert.equal(t.jobs.value.size, 0, 'nothing is tracked until the lookup answers')
  await settle()
  assert.equal(t.jobs.value.get(20).state, 'queued')
  // a later progress event for it now applies
  t.handleComfyEvent('job_progress', { job_id: 20, value: 1, max: 2 })
  assert.equal(t.jobs.value.get(20).value, 1)
})

await check('an unknown job that does not match is looked up once and then ignored', async () => {
  jobRows.clear()
  jobRows.set(30, job(30, 'queued', null, { i2v_source_path: '/a.png' }))
  const before = urlsFor('/comfy/jobs/30').length
  const t = useJobTracker({ match: isT2i })
  t.handleComfyEvent('job_update', { job_id: 30, state: 'queued', error: null })
  await settle()
  t.handleComfyEvent('job_update', { job_id: 30, state: 'running', error: null })
  t.handleComfyEvent('job_update', { job_id: 30, state: 'failed', error: 'x' })
  await settle()
  assert.equal(t.jobs.value.size, 0)
  assert.equal(urlsFor('/comfy/jobs/30').length - before, 1, 'the negative answer is cached')
})

await check('a failed lookup is not cached: the next event retries it', async () => {
  jobRows.clear()
  jobRows.set(31, job(31, 'running'))
  const t = useJobTracker({ match: isT2i })
  getJobStatus = 500
  t.handleComfyEvent('job_update', { job_id: 31, state: 'running', error: null })
  await settle()
  assert.equal(t.jobs.value.size, 0)
  getJobStatus = 200
  t.handleComfyEvent('job_update', { job_id: 31, state: 'running', error: null })
  await settle()
  assert.equal(t.jobs.value.get(31).state, 'running')
})

await check('an unknown job that already failed is adopted as a failed chip', async () => {
  jobRows.clear()
  jobRows.set(32, job(32, 'failed', 'b1', { error: 'node X exploded' }))
  const t = useJobTracker({ match: isT2i })
  t.handleComfyEvent('job_update', { job_id: 32, state: 'failed', error: 'node X exploded' })
  await settle()
  assert.deepEqual(t.jobs.value.get(32), { state: 'failed', error: 'node X exploded' })
})

await check('a terminal event that lands during the lookup wins over the stale row', async () => {
  jobRows.clear()
  jobRows.set(40, job(40, 'running'))
  const t = useJobTracker({ match: isT2i })
  getJobGate = deferred()
  t.handleComfyEvent('job_update', { job_id: 40, state: 'queued', error: null })
  t.handleComfyEvent('job_update', { job_id: 40, state: 'done', error: null })
  getJobGate.resolve()
  getJobGate = null
  await settle()
  assert.equal(t.jobs.value.has(40), false, 'done during the lookup must not leave a running chip')

  jobRows.set(41, job(41, 'running'))
  getJobGate = deferred()
  t.handleComfyEvent('job_update', { job_id: 41, state: 'running', error: null })
  t.handleComfyEvent('job_update', { job_id: 41, state: 'failed', error: 'late failure' })
  getJobGate.resolve()
  getJobGate = null
  await settle()
  assert.deepEqual(t.jobs.value.get(41), { state: 'failed', error: 'late failure' })
})

await check('cancel(): POSTs, then drops the chip; a failure re-arms the button and rethrows', async () => {
  const t = useJobTracker({ match: isT2i })
  t.track(50)
  await t.cancel(50)
  assert.equal(t.jobs.value.has(50), false)
  assert.ok(calls.some((c) => c.method === 'POST' && c.url === '/api/comfy/jobs/50/cancel'))
  t.track(51)
  cancelFails.add(51)
  await assert.rejects(() => t.cancel(51), (e) => e instanceof ApiError && e.status === 409)
  assert.equal(t.jobs.value.get(51).state, 'queued')
  assert.equal(t.jobs.value.get(51).cancelling, false)
  cancelFails.clear()
})

await check('cancel(): ignores unknown, failed and already-cancelling chips', async () => {
  const t = useJobTracker({ match: isT2i })
  const before = calls.length
  await t.cancel(999)
  t.track(52)
  t.handleComfyEvent('job_update', { job_id: 52, state: 'failed', error: 'x' })
  await t.cancel(52)
  assert.equal(calls.length, before)
})

await check('cancelAll(): tries every active job, then rethrows the first failure', async () => {
  const t = useJobTracker({ match: isT2i })
  t.track(60)
  t.track(61)
  t.track(62)
  t.handleComfyEvent('job_update', { job_id: 62, state: 'failed', error: 'x' })
  cancelFails.add(60)
  const before = calls.length
  await assert.rejects(() => t.cancelAll(), (e) => e instanceof ApiError)
  const posted = calls.slice(before).filter((c) => c.method === 'POST').map((c) => c.url)
  assert.deepEqual(posted.sort(), ['/api/comfy/jobs/60/cancel', '/api/comfy/jobs/61/cancel'])
  assert.equal(t.jobs.value.has(61), false)
  assert.equal(t.jobs.value.has(60), true)
  assert.equal(t.jobs.value.has(62), true, 'the failed chip stays until dismissed')
  cancelFails.clear()
})

await check('dismiss() and reset()', () => {
  const t = useJobTracker({ match: isT2i })
  t.track(70)
  t.track(71)
  t.dismiss(70)
  assert.deepEqual([...t.jobs.value.keys()], [71])
  t.reset()
  assert.equal(t.jobs.value.size, 0)
})

console.log('composables/useFormAutosave.ts')

function makeForm() {
  const prompt = ref('a')
  const seed = ref(1)
  const loras = ref([])
  const selectedId = ref(null)
  const saves = []
  let failNext = 0
  let gate = null
  const saved = []
  const auto = useFormAutosave({
    form: () => ({ prompt: prompt.value, seed: seed.value, loras: loras.value.map((l) => ({ ...l })) }),
    selectedId,
    delayMs: 15,
    save: async (id, fields) => {
      saves.push({ id, fields })
      if (gate) await gate.promise
      if (failNext > 0) {
        failNext--
        throw new Error('save failed')
      }
      saved.push({ id, fields })
    },
  })
  return {
    prompt,
    seed,
    loras,
    selectedId,
    saves,
    saved,
    auto,
    failNext: (n) => (failNext = n),
    hold: () => (gate = deferred()),
    release: () => {
      gate.resolve()
      gate = null
    },
  }
}

await check('nothing is saved while no item is selected', async () => {
  const f = makeForm()
  f.prompt.value = 'edited'
  await nextTick()
  await sleep(50)
  assert.equal(f.saves.length, 0)
})

await check('an edit saves the whole form after the delay, once', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.prompt.value = 'b'
  f.seed.value = 2
  await nextTick()
  assert.equal(f.saves.length, 0, 'not before the delay')
  await sleep(60)
  assert.equal(f.saves.length, 1)
  assert.deepEqual(f.saves[0], { id: 5, fields: { prompt: 'b', seed: 2, loras: [] } })
})

await check('markLoaded(): loading an item into the form does not echo back as a save', async () => {
  const f = makeForm()
  f.selectedId.value = 8
  f.prompt.value = 'loaded from the server'
  f.seed.value = 99
  f.auto.markLoaded()
  await nextTick()
  await sleep(60)
  assert.equal(f.saves.length, 0)
})

await check('nested edits (a LoRA row) trigger a save; a no-op re-assignment does not', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.loras.value = [{ name: 'x', strength: 1 }]
  await nextTick()
  await sleep(60)
  assert.equal(f.saves.length, 1)
  f.loras.value[0].strength = 0.5
  await nextTick()
  await sleep(60)
  assert.equal(f.saves.length, 2)
  assert.equal(f.saves[1].fields.loras[0].strength, 0.5)
  f.prompt.value = 'a' // same text as before: JSON unchanged
  await nextTick()
  await sleep(60)
  assert.equal(f.saves.length, 2)
})

await check('a failed save sets saveFailed and the next edit retries with the latest form', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.failNext(1)
  f.prompt.value = 'first try'
  await nextTick()
  await sleep(60)
  assert.equal(f.auto.saveFailed.value, true)
  assert.equal(f.saved.length, 0)
  f.prompt.value = 'second try'
  await nextTick()
  await sleep(60)
  assert.equal(f.auto.saveFailed.value, false)
  assert.deepEqual(f.saved[0].fields, { prompt: 'second try', seed: 1, loras: [] })
})

await check('flush() retries a failed save without another edit (the close path)', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.failNext(1)
  f.prompt.value = 'unsaved'
  await nextTick()
  await sleep(60)
  assert.equal(f.auto.saveFailed.value, true)
  f.auto.flush()
  await sleep(20)
  assert.equal(f.auto.saveFailed.value, false)
  assert.equal(f.saved.length, 1)
})

await check('saves are serialised: a slow first save finishes before the second starts', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.hold()
  f.prompt.value = 'one'
  await nextTick()
  await sleep(40) // first save is now in flight, held at the gate
  f.prompt.value = 'two'
  await nextTick()
  await sleep(40)
  assert.deepEqual(f.saves.map((s) => s.fields.prompt), ['one'], 'the second waits its turn')
  f.release()
  await sleep(20)
  assert.deepEqual(f.saves.map((s) => s.fields.prompt), ['one', 'two'])
})

await check('the id is captured at flush time, so switching items cannot misdirect a save', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.hold()
  f.prompt.value = 'edit for five'
  await nextTick()
  f.auto.flush() // queued for item 5, held
  await sleep(10)
  f.selectedId.value = 6 // the user clicked another tile
  f.prompt.value = 'server value of six'
  f.auto.markLoaded()
  f.release()
  await sleep(20)
  assert.deepEqual(f.saved.map((s) => [s.id, s.fields.prompt]), [[5, 'edit for five']])
  assert.equal(f.auto.saveFailed.value, false)
  // item 6 is now clean: nothing further is sent for it
  await sleep(50)
  assert.equal(f.saves.length, 1)
})

await check('the fields are a deep copy taken when the save is queued', async () => {
  const state = reactive({ prompt: 'a', loras: [{ name: 'x', strength: 1 }] })
  const selectedId = ref(3)
  const gate = deferred()
  const saved = []
  const auto = useFormAutosave({
    form: () => state, // the live reactive object, not a copy
    selectedId,
    delayMs: 10,
    save: async (id, fields) => {
      await gate.promise
      saved.push(fields)
    },
  })
  auto.markLoaded()
  state.loras[0].strength = 0.5
  await nextTick()
  auto.flush() // queued now, held at the gate
  state.loras[0].strength = 0.9 // edited in place after the flush, before the save ran
  gate.resolve()
  await sleep(40)
  // The first save carries the value at flush time; the later edit is its own save.
  assert.deepEqual(saved.map((f) => f.loras[0].strength), [0.5, 0.9])
})

await check('cancelPending() drops a scheduled save (deleting the selected item)', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.prompt.value = 'about to be deleted'
  await nextTick()
  f.auto.cancelPending()
  await sleep(60)
  assert.equal(f.saves.length, 0)
})

await check('flush() sends immediately, and skips when the form matches what was loaded', async () => {
  const f = makeForm()
  f.selectedId.value = 5
  f.auto.markLoaded()
  f.auto.flush()
  await sleep(10)
  assert.equal(f.saves.length, 0)
  f.prompt.value = 'now'
  f.auto.flush()
  await sleep(10)
  assert.equal(f.saves.length, 1)
})

await check('savable() can drop invalid fields before they are sent', async () => {
  const seed = ref(1)
  const selectedId = ref(3)
  const sent = []
  const auto = useFormAutosave({
    form: () => ({ seed: seed.value, prompt: 'p' }),
    selectedId,
    delayMs: 10,
    savable: (form) => {
      const out = { ...form }
      if (!Number.isInteger(form.seed)) delete out.seed
      return out
    },
    save: async (id, fields) => void sent.push(fields),
  })
  auto.markLoaded()
  seed.value = ''
  await nextTick()
  await sleep(40)
  assert.deepEqual(sent, [{ prompt: 'p' }])
})

console.log('components/generation')

await check('JobTile: state word, percentage, cancelling', async () => {
  const queued = await render(JobTile, { chip: { state: 'queued' } })
  assert.equal(withClasses(queued, 'job-tile'), 1)
  assert.equal(withClasses(queued, 'job-tile', 'failed'), 0)
  assert.match(queued, />queued</)
  assert.match(queued, /title="Cancel this job"/)
  const running = await render(JobTile, { chip: { state: 'running', value: 3, max: 4 } })
  assert.match(running, />75%</)
  const cancelling = await render(JobTile, { chip: { state: 'running', cancelling: true } })
  assert.match(cancelling, /cancelling…/)
  assert.match(cancelling, /disabled/)
})

await check('JobTile: a failed job shows the server error verbatim and a Dismiss button', async () => {
  const html = await render(JobTile, {
    chip: { state: 'failed', error: 'KSampler: CUDA out of memory <b>' },
  })
  assert.equal(withClasses(html, 'job-tile', 'failed'), 1)
  assert.match(html, />failed</)
  assert.match(html, /KSampler: CUDA out of memory &lt;b&gt;/)
  assert.match(html, /title="Dismiss"/)
  assert.doesNotMatch(html, /Cancel this job/)
  const bare = await render(JobTile, { chip: { state: 'failed', error: null } })
  assert.doesNotMatch(bare, /job-error/)
})

const item = (id, over = {}) => ({
  id,
  file_path: `/out/dir one/img_${id}.png`,
  is_favorite: false,
  title: `title ${id}`,
  label: `label ${id}`,
  ...over,
})

await check('ThumbTile: thumbnail URL, tooltip, label, favourite state', async () => {
  const html = await render(ThumbTile, { item: item(1, { is_favorite: true }), selected: true })
  assert.match(html, /src="\/api\/thumbnails\/%2Fout%2Fdir%20one%2Fimg_1\.png"/)
  assert.match(html, /title="title 1"/)
  assert.match(html, />label 1</)
  assert.equal(withClasses(html, 'thumb-tile', 'selected'), 1)
  assert.equal(withClasses(html, 'star', 'active'), 1)
  assert.match(html, />★</)
  const plain = await render(ThumbTile, { item: item(2) })
  assert.equal(withClasses(plain, 'thumb-tile', 'selected'), 0)
  assert.equal(withClasses(plain, 'star', 'active'), 0)
  assert.match(plain, />☆</)
})

await check('ThumbStrip: tiles in order, selection, jobs slot first, empty text rules', async () => {
  const html = await render(
    ThumbStrip,
    { items: [item(1), item(2)], selectedId: 2, emptyText: 'No images yet' },
    { jobs: () => h('div', { class: 'JOBSLOT' }, 'j') },
  )
  assert.ok(html.indexOf('JOBSLOT') < html.indexOf('label 1'), 'jobs render before the tiles')
  assert.ok(html.indexOf('label 1') < html.indexOf('label 2'))
  assert.equal(withClasses(html, 'thumb-tile', 'selected'), 1)
  assert.equal(withClasses(html, 'thumb-tile'), 2)
  assert.doesNotMatch(html, /No images yet/)

  const empty = await render(ThumbStrip, { items: [], selectedId: null, emptyText: 'No images yet' })
  assert.match(empty, /No images yet/)
  const loading = await render(ThumbStrip, {
    items: [],
    selectedId: null,
    emptyText: 'No images yet',
    loading: true,
  })
  assert.doesNotMatch(loading, /No images yet/, 'no empty state flash while loading')
  const silent = await render(ThumbStrip, { items: [], selectedId: null })
  assert.doesNotMatch(silent, /thumb-strip-empty/)
})

await check('ThumbStrip: the Older button appears only when there is more', async () => {
  const none = await render(ThumbStrip, { items: [item(1)], selectedId: null })
  assert.doesNotMatch(none, /Older/)
  const more = await render(ThumbStrip, { items: [item(1)], selectedId: null, hasMore: true })
  assert.match(more, /Older/)
  const busy = await render(ThumbStrip, {
    items: [item(1)],
    selectedId: null,
    hasMore: true,
    loadingMore: true,
  })
  assert.match(busy, /Loading…/)
  assert.match(busy, /disabled/)
})

await check('ThumbStrip: tile size is a CSS variable', async () => {
  const html = await render(ThumbStrip, { items: [], selectedId: null, tileSize: 100 })
  assert.match(html, /--tile-size:100px/)
})

await check('SeedControls: seed value, four policies with the current one selected', async () => {
  const html = await render(SeedControls, { seed: 321424, policy: 'increment' })
  assert.match(html, /value="321424"/)
  assert.match(html, /<option value="fixed"[^>]*>Fixed</)
  assert.match(html, /<option value="increment" selected[^>]*>Increment</)
  assert.match(html, /<option value="decrement"[^>]*>Decrement</)
  assert.match(html, /<option value="random"[^>]*>Randomize</)
  assert.match(html, />Seed</)
  assert.doesNotMatch(html, /disabled/)
})

await check('SeedControls: disabled disables every control; label can be dropped', async () => {
  const html = await render(SeedControls, { seed: 1, policy: 'fixed', disabled: true, label: '' })
  assert.equal((html.match(/disabled/g) || []).length >= 3, true)
  assert.doesNotMatch(html, /sc-label/)
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task15.mjs`
Expected: FAIL (exit code 1) — `Error: Failed to load url /src/composables/useJobTracker.ts (resolved id: /src/composables/useJobTracker.ts). Does the file exist?`
- [ ] **Step 3: Implement**

`frontend/src/composables/useJobTracker.ts`:
```ts
import { computed, ref } from 'vue'
import type { GenerationJob } from '../types/storyboard'
import type { JobChip } from '../types/jobs'
import { cancelJob as apiCancelJob, getJob, listJobs } from '../api/comfy'

export interface JobTrackerOptions {
  /**
   * Which ComfyUI jobs this tracker owns, judged from the whole job row
   * (`t2i_batch_id`, `i2v_source_path`, ...). Only matching jobs ever
   * become chips.
   */
  match: (job: GenerationJob) => boolean
}

// Ids that were looked up and are not ours. Bounded so a long session cannot
// grow it without limit; dropping it only costs a few repeated lookups.
const REJECTED_CAP = 2000

interface Lookup {
  // A terminal job_update that landed while the lookup was in flight.
  terminal: { state: string; error: string | null } | null
}

function progressOf(chip: JobChip | undefined): { value: number; max: number } | undefined {
  return chip && chip.state === 'running' && chip.value !== undefined && chip.max !== undefined
    ? { value: chip.value, max: chip.max }
    : undefined
}

function chipFor(
  state: 'queued' | 'running',
  prev: JobChip | undefined,
  progress?: { value: number; max: number },
): JobChip {
  const chip: JobChip = { state }
  if (progress) {
    chip.value = progress.value
    chip.max = progress.max
  }
  if (prev?.cancelling) chip.cancelling = true
  return chip
}

/**
 * Tracks the ComfyUI jobs a feature cares about as strip-tile chips, from two
 * sources: `refresh()` (the server's queued + running lists, which is what
 * survives a page reload) and the comfy WebSocket channel via
 * `handleComfyEvent`. Generalised from stores/i2v.ts.
 *
 * Channel semantics: job_update failed keeps the chip until it is dismissed;
 * done / cancelled remove it; queued / running update it; job_progress
 * becomes value / max.
 *
 * A job created by a server-side runner (a T2I batch) is announced by a
 * `job_update` for an id this tracker has never seen, and that event carries
 * no batch id. So an unknown id is looked up once (GET /api/comfy/jobs/{id})
 * and adopted when `match` accepts the row. A non-matching answer is
 * remembered so a foreign job's later events do not repeat the request.
 *
 * useWebSocket needs a component setup context, so the tracker does not
 * subscribe: the owner forwards `comfy` channel events to `handleComfyEvent`.
 */
export function useJobTracker(options: JobTrackerOptions) {
  const { match } = options
  const jobs = ref<Map<number, JobChip>>(new Map())
  const lookups = new Map<number, Lookup>()
  let rejected = new Set<number>()
  // Bumped by reset(): lookups and refreshes that started earlier drop their result.
  let epoch = 0

  // Jobs that can still be cancelled (a failed tile is only dismissable).
  const activeJobIds = computed<number[]>(() =>
    [...jobs.value].filter(([, chip]) => chip.state !== 'failed').map(([id]) => id),
  )

  function setChip(jobId: number, chip: JobChip): void {
    const next = new Map(jobs.value)
    next.set(jobId, chip)
    jobs.value = next
  }

  function patchChip(jobId: number, fields: Partial<JobChip>): void {
    const chip = jobs.value.get(jobId)
    if (chip) setChip(jobId, { ...chip, ...fields })
  }

  function track(jobId: number): void {
    setChip(jobId, { state: 'queued' })
  }

  function dismiss(jobId: number): void {
    if (!jobs.value.has(jobId)) return
    const next = new Map(jobs.value)
    next.delete(jobId)
    jobs.value = next
  }

  /** Forget everything: chips, in-flight lookups and the not-ours cache. */
  function reset(): void {
    epoch += 1
    jobs.value = new Map()
    lookups.clear()
    rejected = new Set()
  }

  // Rebuilds the in-flight set from the server. Failed chips stay (they wait
  // for the user to dismiss them); queued / running chips the server no
  // longer lists are dropped; progress and a pending cancel carry over.
  async function refresh(): Promise<void> {
    const startEpoch = epoch
    const [queued, running] = await Promise.all([listJobs('queued', 1000), listJobs('running', 1000)])
    if (startEpoch !== epoch) return
    const prev = jobs.value
    const next = new Map<number, JobChip>()
    for (const [id, chip] of prev) {
      if (chip.state === 'failed') next.set(id, chip)
    }
    for (const row of [...queued, ...running]) {
      if (!match(row) || next.has(row.id)) continue
      const old = prev.get(row.id)
      next.set(
        row.id,
        row.state === 'running'
          ? chipFor('running', old, progressOf(old))
          : chipFor('queued', old),
      )
    }
    jobs.value = next
  }

  function remember(jobId: number): void {
    if (rejected.size >= REJECTED_CAP) rejected = new Set()
    rejected.add(jobId)
  }

  async function adopt(jobId: number): Promise<void> {
    if (rejected.has(jobId)) return
    const lookup: Lookup = { terminal: null }
    lookups.set(jobId, lookup)
    const startEpoch = epoch
    try {
      const row = await getJob(jobId)
      if (startEpoch !== epoch) return
      if (!match(row)) {
        remember(jobId)
        return
      }
      if (jobs.value.has(jobId)) return
      // What the events said while we waited is newer than the row we read.
      const outcome = lookup.terminal ?? { state: row.state as string, error: row.error }
      if (outcome.state === 'failed') {
        setChip(jobId, { state: 'failed', error: outcome.error ?? row.error ?? null })
      } else if (outcome.state === 'queued' || outcome.state === 'running') {
        setChip(jobId, { state: outcome.state })
      }
    } catch {
      // Not cached as a rejection: the next event for this id, or refresh(), retries.
    } finally {
      if (lookups.get(jobId) === lookup) lookups.delete(jobId)
    }
  }

  function onJobUpdate(jobId: number, state: string, error: string | null): void {
    const prev = jobs.value.get(jobId)
    if (prev) {
      if (state === 'failed') setChip(jobId, { state: 'failed', error })
      else if (state === 'done' || state === 'cancelled') dismiss(jobId)
      else setChip(jobId, chipFor(state === 'running' ? 'running' : 'queued', prev))
      return
    }
    const lookup = lookups.get(jobId)
    if (lookup) {
      if (state === 'failed' || state === 'done' || state === 'cancelled') {
        lookup.terminal = { state, error }
      }
      return
    }
    if (state === 'queued' || state === 'running' || state === 'failed') void adopt(jobId)
  }

  function onJobProgress(jobId: number, value: unknown, max: unknown): void {
    const prev = jobs.value.get(jobId)
    if (!prev || prev.state === 'failed') return
    if (typeof value === 'number' && typeof max === 'number' && Number.isFinite(value) && Number.isFinite(max)) {
      setChip(jobId, chipFor('running', prev, { value, max }))
    } else if (prev.state !== 'running') {
      setChip(jobId, chipFor('running', prev))
    }
  }

  /** The comfy channel: job_update and job_progress. Other events are ignored. */
  function handleComfyEvent(event: string, data: Record<string, unknown>): void {
    const jobId = Number(data.job_id)
    if (!Number.isFinite(jobId)) return
    if (event === 'job_update') {
      const error = typeof data.error === 'string' && data.error ? data.error : null
      onJobUpdate(jobId, String(data.state), error)
    } else if (event === 'job_progress') {
      onJobProgress(jobId, data.value, data.max)
    }
  }

  // Cancel one queued / running job. The chip normally leaves via the comfy
  // channel's job_update -> cancelled; it is also dropped here on success so a
  // missed WebSocket frame cannot strand it. On failure the chip stays with
  // its button re-armed, and the error propagates to the caller's toast.
  async function cancel(jobId: number): Promise<void> {
    const chip = jobs.value.get(jobId)
    if (!chip || chip.state === 'failed' || chip.cancelling) return
    patchChip(jobId, { cancelling: true })
    try {
      await apiCancelJob(jobId)
      dismiss(jobId)
    } catch (e) {
      patchChip(jobId, { cancelling: false })
      throw e
    }
  }

  // Cancel every in-flight job. Every job is attempted; the first failure is
  // rethrown after the rest have settled.
  async function cancelAll(): Promise<void> {
    const results = await Promise.allSettled(activeJobIds.value.map((id) => cancel(id)))
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (failed) throw failed.reason
  }

  return {
    jobs,
    activeJobIds,
    track,
    dismiss,
    cancel,
    cancelAll,
    refresh,
    handleComfyEvent,
    reset,
  }
}

export type JobTracker = ReturnType<typeof useJobTracker>
```

`frontend/src/composables/useFormAutosave.ts`:
```ts
import { getCurrentScope, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'

export interface FormAutosaveOptions<F extends object, I extends string | number = number> {
  /**
   * The form's current values, as one plain object. Read inside the
   * getter (or computed) so the autosave sees every field change, nested
   * arrays and objects included.
   */
  form: MaybeRefOrGetter<F>
  /** The item whose form_state edits are saved into; null = a scratch form, nothing is saved. */
  selectedId: MaybeRefOrGetter<I | null>
  /** Persists `fields` into item `id`. A rejection marks the save as failed. */
  save: (id: I, fields: Partial<F>) => Promise<unknown>
  delayMs?: number
  /**
   * Trims the form to what may be sent, e.g. leaves out a number field that
   * is mid-edit and empty, so its last good value stays saved and everything
   * else still goes through. Default: the whole form.
   */
  savable?: (form: F) => Partial<F>
}

export interface FormAutosave {
  /** The newest save for the selected item failed; the next edit (or flush) retries. */
  saveFailed: Ref<boolean>
  /** Save now if the form differs from what was last loaded or saved. */
  flush: () => void
  /** Drop a scheduled save without sending it (the item is about to be deleted). */
  cancelPending: () => void
  /**
   * Call right after loading an item's values into the form: the form now
   * equals what the server holds, so loading must not echo back as a save.
   */
  markLoaded: () => void
}

/**
 * Debounced, serialised autosave of a form into the selected item's
 * form_state, generalised from I2VDialog.vue (per-clip form state).
 *
 * - Saves run one at a time, in order, so a slow early request can never
 *   land after, and overwrite, a later one.
 * - The item id and a deep copy of the fields are captured when a save is
 *   queued, not when it runs: by then the user may have selected another item
 *   or edited a nested value in place.
 * - A snapshot of the form as last loaded or saved decides whether a flush
 *   has anything to send. A failed save leaves it alone, so the next edit
 *   (or the close-time flush) retries.
 *
 * Switching items: call flush() BEFORE changing selectedId or the form (it
 * saves the pending edits of the item being left), then load the new values,
 * set selectedId, and call markLoaded().
 *
 * Also flushes when the owning component (or effect scope) is disposed.
 */
export function useFormAutosave<F extends object, I extends string | number = number>(
  options: FormAutosaveOptions<F, I>,
): FormAutosave {
  const delayMs = options.delayMs ?? 600
  const saveFailed = ref(false)
  // JSON of the form as last loaded or saved.
  let savedSnapshot = ''
  let timer: ReturnType<typeof setTimeout> | null = null
  let chain: Promise<void> = Promise.resolve()

  const snapshotOf = (): string => JSON.stringify(toValue(options.form))

  function cancelPending(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function flush(): void {
    cancelPending()
    const id = toValue(options.selectedId)
    if (id == null) return
    const snapshot = JSON.stringify(toValue(options.form))
    if (snapshot === savedSnapshot) return
    const plain = JSON.parse(snapshot) as F
    const fields: Partial<F> = options.savable ? options.savable(plain) : plain
    chain = chain.then(async () => {
      try {
        await options.save(id, fields)
        // Only the item still on screen gets to update the bookkeeping.
        if (toValue(options.selectedId) === id) {
          savedSnapshot = snapshot
          saveFailed.value = false
        }
      } catch {
        // Snapshot left alone, so the next edit (or close) retries.
        if (toValue(options.selectedId) === id) saveFailed.value = true
      }
    })
  }

  function markLoaded(): void {
    cancelPending()
    savedSnapshot = snapshotOf()
    saveFailed.value = false
  }

  watch(snapshotOf, () => {
    if (toValue(options.selectedId) == null) return
    cancelPending()
    timer = setTimeout(flush, delayMs)
  })

  if (getCurrentScope()) onScopeDispose(flush)

  return { saveFailed, flush, cancelPending, markLoaded }
}
```

`frontend/src/components/generation/JobTile.vue`:
```vue
<script setup lang="ts">
import { computed } from 'vue'
import { jobChipLabel, type JobChip } from '../../types/jobs'

// A dashed placeholder tile for a job that has not produced its file yet:
// queued / running with progress, or failed with the server's error text
// shown verbatim. The ✕ cancels a live job and dismisses a failed one.
const props = defineProps<{ chip: JobChip }>()
const emit = defineEmits<{ cancel: []; dismiss: [] }>()

const failed = computed(() => props.chip.state === 'failed')
const label = computed(() => jobChipLabel(props.chip))

function onClose() {
  if (failed.value) emit('dismiss')
  else emit('cancel')
}
</script>

<template>
  <div
    class="job-tile"
    :class="{ failed }"
    :title="failed ? (chip.error ?? undefined) : undefined"
  >
    <button
      type="button"
      class="job-x"
      :title="failed ? 'Dismiss' : 'Cancel this job'"
      :disabled="!failed && !!chip.cancelling"
      @click="onClose"
    >✕</button>
    <span class="job-label">{{ label }}</span>
    <span v-if="failed && chip.error" class="job-error">{{ chip.error }}</span>
  </div>
</template>

<style scoped>
.job-tile {
  position: relative;
  flex: 0 0 var(--tile-size, 132px);
  width: var(--tile-size, 132px);
  height: var(--tile-size, 132px);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 18px 8px 8px;
  border: 1px dashed #666;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-color-secondary);
}

.job-tile.failed {
  border-color: #c33;
  color: #c33;
}

.job-x {
  position: absolute;
  top: 2px;
  right: 2px;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  font-size: 13px;
  padding: 4px;
}

.job-x:hover:not(:disabled) {
  color: var(--text-color);
}

.job-x:disabled {
  opacity: 0.5;
  cursor: default;
}

.job-error {
  max-width: 100%;
  font-size: 11px;
  line-height: 1.3;
  text-align: center;
  word-break: break-word;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  overflow: hidden;
}
</style>
```

`frontend/src/components/generation/ThumbTile.vue`:
```vue
<script setup lang="ts">
import { thumbnailUrl } from '../../api/client'
import type { ThumbItem } from '../../types/jobs'

// One image tile: thumbnail, hover overlays (favourite, delete) and a label
// underneath. It only reports what the user did; ThumbStrip adds the item.
defineProps<{ item: ThumbItem; selected?: boolean }>()
const emit = defineEmits<{
  select: [evt: MouseEvent]
  open: []
  favorite: []
  delete: []
}>()

// The overlay buttons stop click AND dblclick (in the template): a fast
// double-tap on one must neither select nor open the tile underneath.
//
// detail > 1 is the second click of a double-click, which opens the viewer;
// the first click already selected, so it must not select again.
function onClick(evt: MouseEvent) {
  if (evt.detail > 1) return
  emit('select', evt)
}

function onImgError(e: Event) {
  ;(e.target as HTMLImageElement).style.display = 'none'
}
</script>

<template>
  <div class="thumb-wrap">
    <div
      class="thumb-tile"
      :class="{ selected }"
      :title="item.title"
      @click="onClick"
      @dblclick="emit('open')"
    >
      <img :src="thumbnailUrl(item.file_path)" alt="" loading="lazy" decoding="async" @error="onImgError" />
      <button
        type="button"
        class="overlay star"
        :class="{ active: item.is_favorite }"
        title="Favorite"
        @click.stop="emit('favorite')"
        @dblclick.stop
      >{{ item.is_favorite ? '★' : '☆' }}</button>
      <button
        type="button"
        class="overlay del"
        title="Delete"
        @click.stop="emit('delete')"
        @dblclick.stop
      >✕</button>
    </div>
    <div class="thumb-label" :title="item.label">{{ item.label }}</div>
  </div>
</template>

<style scoped>
.thumb-wrap {
  flex: 0 0 var(--tile-size, 132px);
  width: var(--tile-size, 132px);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.thumb-tile {
  position: relative;
  width: 100%;
  height: var(--tile-size, 132px);
  border-radius: 6px;
  overflow: hidden;
  background: #111;
  cursor: pointer;
}

/* contain, not cover: generated images come in portrait and landscape, and
   the whole frame is what the user is judging. */
.thumb-tile img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* outline, not border: a border would shrink the fixed-size thumbnail. */
.thumb-tile.selected {
  outline: 2px solid var(--primary-color, #6366f1);
  outline-offset: 1px;
}

.overlay {
  position: absolute;
  opacity: 0;
  transition: opacity 0.15s;
  border: none;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  padding: 3px 5px;
}

.thumb-tile:hover .overlay {
  opacity: 1;
}

.star {
  top: 4px;
  left: 4px;
}

.star.active {
  opacity: 1;
  color: gold;
}

.del {
  top: 4px;
  right: 4px;
}

.thumb-label {
  font-size: 11px;
  line-height: 1.35;
  color: var(--text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
```

`frontend/src/components/generation/ThumbStrip.vue`:
```vue
<script setup lang="ts">
import type { ThumbItem } from '../../types/jobs'
import ThumbTile from './ThumbTile.vue'

// A horizontally scrolling strip of image tiles. The `jobs` slot renders
// before the tiles (JobTile placeholders); the strip itself knows nothing
// about jobs or about any feature's row type.
//
// `emptyText` shows only when there are no items and `loading` is false, so
// the caller passes loading=true until its first list load has landed and
// while a job placeholder is on screen, which is what prevents an
// empty-state flash.
const props = withDefaults(
  defineProps<{
    items: ThumbItem[]
    selectedId: number | string | null
    emptyText?: string
    loading?: boolean
    // More rows exist beyond the ones held: shows an "Older" tile at the end.
    hasMore?: boolean
    loadingMore?: boolean
    tileSize?: number
  }>(),
  { emptyText: '', loading: false, hasMore: false, loadingMore: false, tileSize: 132 },
)

const emit = defineEmits<{
  select: [item: ThumbItem, evt: MouseEvent]
  open: [index: number]
  favorite: [item: ThumbItem]
  delete: [item: ThumbItem]
  more: []
}>()
</script>

<template>
  <div class="thumb-strip" :style="{ '--tile-size': `${props.tileSize}px` }">
    <slot name="jobs" />
    <ThumbTile
      v-for="(item, index) in items"
      :key="item.id"
      :item="item"
      :selected="item.id === selectedId"
      @select="emit('select', item, $event)"
      @open="emit('open', index)"
      @favorite="emit('favorite', item)"
      @delete="emit('delete', item)"
    />
    <button
      v-if="hasMore"
      type="button"
      class="thumb-more"
      :disabled="loadingMore"
      @click="emit('more')"
    >{{ loadingMore ? 'Loading…' : 'Older ›' }}</button>
    <div v-if="!items.length && !loading && emptyText" class="thumb-strip-empty">{{ emptyText }}</div>
  </div>
</template>

<style scoped>
.thumb-strip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 0;
  min-height: calc(var(--tile-size, 132px) + 30px);
}

.thumb-more {
  flex: 0 0 auto;
  align-self: flex-start;
  height: var(--tile-size, 132px);
  padding: 0 14px;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 13px;
  cursor: pointer;
}

.thumb-more:hover:not(:disabled) {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.thumb-more:disabled {
  opacity: 0.6;
  cursor: default;
}

.thumb-strip-empty {
  align-self: center;
  color: #888;
  font-size: 13px;
}
</style>
```

`frontend/src/components/generation/SeedControls.vue`:
```vue
<script setup lang="ts">
import { SEED_MAX, clampSeed, parseSeedInput, randomSeed, type SeedPolicy } from '../../types/t2i'

// Seed number, a randomise button and the seed policy. Fully controlled
// (v-model:seed / v-model:policy): the input is bound with :value and events
// rather than v-model so a half-typed value never reaches the parent.
const props = withDefaults(
  defineProps<{
    seed: number
    policy: SeedPolicy
    disabled?: boolean
    max?: number
    label?: string
  }>(),
  { disabled: false, max: SEED_MAX, label: 'Seed' },
)

const emit = defineEmits<{
  'update:seed': [value: number]
  'update:policy': [value: SeedPolicy]
}>()

const POLICY_OPTIONS: { value: SeedPolicy; label: string }[] = [
  { value: 'fixed', label: 'Fixed' },
  { value: 'increment', label: 'Increment' },
  { value: 'decrement', label: 'Decrement' },
  { value: 'random', label: 'Randomize' },
]

// While typing, only a complete, in-range integer is reported.
function onInput(e: Event) {
  const value = parseSeedInput((e.target as HTMLInputElement).value, props.max)
  if (value !== null && value !== props.seed) emit('update:seed', value)
}

// On commit (blur / Enter) the box settles: an integer out of range is
// clamped, anything else (blank, "1.5") goes back to the current seed.
function onChange(e: Event) {
  const input = e.target as HTMLInputElement
  const raw = input.value.trim()
  const n = Number(raw)
  if (raw === '' || !Number.isInteger(n)) {
    input.value = String(props.seed)
    return
  }
  const value = clampSeed(n, props.max)
  input.value = String(value)
  if (value !== props.seed) emit('update:seed', value)
}

function onPolicyChange(e: Event) {
  emit('update:policy', (e.target as HTMLSelectElement).value as SeedPolicy)
}
</script>

<template>
  <div class="seed-controls">
    <span v-if="label" class="sc-label">{{ label }}</span>
    <div class="sc-row">
      <input
        class="sc-input"
        type="number"
        min="0"
        step="1"
        :max="max"
        :value="seed"
        :disabled="disabled"
        @input="onInput"
        @change="onChange"
      />
      <button
        type="button"
        class="sc-dice"
        title="Random seed"
        :disabled="disabled"
        @click="emit('update:seed', randomSeed(max))"
      >🎲</button>
      <select
        class="sc-policy"
        title="What happens to the seed after each generated image"
        :disabled="disabled"
        @change="onPolicyChange"
      >
        <option
          v-for="o in POLICY_OPTIONS"
          :key="o.value"
          :value="o.value"
          :selected="o.value === policy"
        >{{ o.label }}</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.seed-controls {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.sc-row {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.sc-input,
.sc-policy {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.sc-input {
  width: 116px;
}

.sc-dice {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 15px;
  padding: 4px;
}

.sc-dice:disabled,
.sc-input:disabled,
.sc-policy:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task15.mjs`
Expected: `34 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 672ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build before Task 14 prints it too).
- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/generation/JobTile.vue \
    frontend/src/components/generation/ThumbTile.vue \
    frontend/src/components/generation/ThumbStrip.vue \
    frontend/src/components/generation/SeedControls.vue \
    frontend/src/composables/useJobTracker.ts \
    frontend/src/composables/useFormAutosave.ts
git commit -m "feat(t2i): shared generation components and job/autosave composables" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (real clicks on the real components)

Nothing mounts these yet, so mount a throwaway demo from the browser console. With `npm run dev` running, open http://localhost:5173, press F12 and paste:
```js
// Mounts a throwaway demo of the shared pieces in the top-left corner of the page.
const main = await (await fetch('/src/main.ts')).text()
const vueUrl = main.match(/"([^"]*\/deps\/vue\.js\?v=[^"]+)"/)[1]
const { createApp, h, ref } = await import(vueUrl)
const load = async (path) => (await import(path)).default
const SeedControls = await load('/src/components/generation/SeedControls.vue')
const JobTile = await load('/src/components/generation/JobTile.vue')
const ThumbStrip = await load('/src/components/generation/ThumbStrip.vue')

document.getElementById('t2i-demo')?.remove()
const host = Object.assign(document.createElement('div'), { id: 't2i-demo' })
host.style.cssText =
  'position:fixed;top:12px;left:12px;z-index:99999;width:680px;padding:14px;border-radius:10px;' +
  'background:var(--surface-section,#222);color:var(--text-color,#eee);box-shadow:0 8px 30px rgba(0,0,0,.5)'
document.body.appendChild(host)

const seed = ref(321424)
const policy = ref('fixed')
const selected = ref(null)
const items = ref(
  [1, 2, 3].map((id) => ({
    id,
    file_path: `/demo/tile_${id}.png`,
    is_favorite: id === 3,
    title: `Tile ${id}\nhover text`,
    label: `seed ${id * 111} · 1216×832`,
  })),
)
const log = ref([])
const say = (m) => log.value.unshift(m)

createApp({
  render: () =>
    h('div', [
      h(SeedControls, {
        seed: seed.value,
        policy: policy.value,
        'onUpdate:seed': (v) => { seed.value = v; say(`seed -> ${v}`) },
        'onUpdate:policy': (v) => { policy.value = v; say(`policy -> ${v}`) },
      }),
      h(
        ThumbStrip,
        {
          items: items.value,
          selectedId: selected.value,
          emptyText: 'No images yet',
          hasMore: true,
          onSelect: (item, e) => { selected.value = item.id; say(`select ${item.id} (click detail ${e.detail})`) },
          onOpen: (i) => say(`open index ${i}`),
          onFavorite: (item) => { item.is_favorite = !item.is_favorite; say(`favorite ${item.id} -> ${item.is_favorite}`) },
          onDelete: (item) => { items.value = items.value.filter((x) => x.id !== item.id); say(`delete ${item.id}`) },
          onMore: () => say('older'),
        },
        {
          jobs: () => [
            h(JobTile, { chip: { state: 'running', value: 3, max: 4 }, onCancel: () => say('cancel job') }),
            h(JobTile, { chip: { state: 'failed', error: 'KSampler: out of memory' }, onDismiss: () => say('dismiss job') }),
          ],
        },
      ),
      h('pre', { id: 't2i-demo-log', style: 'margin:8px 0 0;max-height:110px;overflow:auto;font-size:11px' }, log.value.join('\n') || 'events appear here'),
    ]),
}).mount(host)
```
A panel appears in the top-left corner: a Seed row (number box `321424`, a 🎲 button, a policy select showing Fixed), then a horizontal strip with a dashed tile reading `75%`, a red dashed tile reading `failed` with `KSampler: out of memory` under it, three image tiles labelled `seed 111 · 1216×832`, `seed 222 · …`, `seed 333 · …` (the third has a gold ★), and an `Older ›` tile at the right end (scroll the strip to reach it). The tiles are plain dark squares because `/demo/…` is not a library file; that is expected. The log under the strip starts as `events appear here`. Then:
1. Click 🎲: the box shows a new number and the log's first line is `seed -> <that number>`.
2. Type `42` in the box: `seed -> 42`. Type `99999999999` and press Tab: the box settles on `2147483647`.
3. Pick Increment in the select: `policy -> increment`.
4. Click the first image tile: an outline in the theme's primary colour appears and the log says `select 1 (click detail 1)`.
5. Double-click the second tile: the top two log lines are `open index 1` and `select 2 (click detail 1)`. There is exactly one `select 2` and no `detail 2` line.
6. Hover the third tile and double-click its star quickly: two `favorite 3 -> …` lines, and no `open index` above them.
7. Click ✕ on the red tile: `dismiss job`. Click ✕ on the `75%` tile: `cancel job`.
8. Hover an image tile and click its ✕: `delete <id>` and the tile disappears. Click `Older ›`: `older`.

Remove the demo with `document.getElementById('t2i-demo').remove()` or reload the page.

---

### Task 16: The T2I store

**Files:**
- Create: `frontend/src/stores/t2i.ts`
- Test: `frontend/t2i-checks.local/task16.mjs` (throwaway, git-ignored; needs the harness from Task 14)

**Interfaces:**
- Consumes: everything Task 14 produces (`api/t2i.ts`, `types/t2i.ts`); `useJobTracker` (Task 15); `useToast()` (`composables/useToast.ts`); `useMediaStore` (`allMedia`, `selectedMedia`, `loadAllMedia()`) and `useFoldersStore` (`purgePath`); `updateMedia(filePath, { is_favorite })` (`api/media.ts`); `useDebounceFn` from `@vueuse/core` (already a dependency).
- Produces `useT2iStore()`, a Pinia setup store. The store never subscribes to the WebSocket (`useWebSocket` needs a component's setup context): the dialog subscribes to the `t2i` and `comfy` channels and forwards every message to `handleT2iEvent` / `handleComfyEvent`, and calls `reattach()` after a reconnect. Returned members:
  - State (read-only from a component): `config: T2iConfig | null`, `configLoading`, `configError: string | null`, `captionMeta: CaptionMeta | null`, `metaLoading`, `metaError`, `images: T2iImage[]` (newest first), `hasMoreImages`, `imagesLoaded` (true after the first successful load of a session), `imagesLoading`, `imagesError`, `loadingOlder`, `selectedId: number | null`, `selectedImage: T2iImage | null`, `activeBatches: T2iActiveBatch[]` (oldest first), `randomBatch: T2iActiveBatch | null`, `currentStep: T2iBatchStepEvent | null` (the running Random batch's latest step), `lastFinished: T2iFinishedBatch | null` (the batch that ended most recently; null once the next one begins), `isBusy` (a batch or a job chip is still running), `statusText`, `jobs: Map<number, JobChip>`, `activeJobIds: number[]`.
  - Loading: `open(): Promise<void>` (never rejects), `close(): void`, `loadConfig(): Promise<T2iConfig | null>`, `loadCaptionMeta(): Promise<CaptionMeta | null>`, `refreshImages(): Promise<void>`, `loadOlder(): Promise<void>`, `reattach(): Promise<void>`, `refreshBatches(): Promise<void>`.
  - Captions and prompts (errors propagate for the first three, which the dialog words): `generatePrompt({caption, seed, model}): Promise<T2iPromptResult>`, `rollCaption(filter): Promise<CaptionRow>`, `startBatch(req: T2iBatchRequest): Promise<T2iStartResult>`, and (null on failure or when superseded) `countCaptions(filter): Promise<CaptionCount | null>`, `resolveCaption({caption, seed, model}): Promise<T2iResolveResult | null>`.
  - Batches and jobs: `cancelAll(): Promise<void>`, `dismissJob(jobId): void`, `cancelJob(jobId): Promise<void>`, `handleT2iEvent(event, data): void`, `handleComfyEvent(event, data): void`.
  - Per image: `selectImage(id): T2iFormState | null` (a deep copy of its form state), `clearSelection(): void`, `saveFormState(id, fields: Partial<T2iFormState>): Promise<void>`, `toggleFavorite(id): Promise<void>`, `deleteImage(id): Promise<void>`.
  - Draft: `loadDraft(): Partial<T2iFormState> | null`, `saveDraft(form: Partial<T2iFormState>): void`, `clearDraft(): void`.

**Design notes.**
- Every loader reports its own failure (toast plus an error field) and never rejects, so `open()` cannot leak an unhandled rejection. Requests that have a "newest wins" meaning (`refreshImages`, `refreshBatches`, `countCaptions`, `resolveCaption`) are numbered, and `open()` invalidates whatever was in flight from the previous session.
- `startBatch` tracks the batch at once and resolves with `next_seed` (from a frame that beat the response, else one `GET /api/t2i/batches`). A batch that already ended before the response arrived is remembered and never added, so it cannot linger as a zombie.
- Frames for a batch the store does not know, or that already ended, are ignored; `batch_started` is the only frame that can create one.
- The strip refreshes on every `t2i_images_changed`; the library grid reloads once, debounced by 1.5 s with a 5 s maximum wait so a long steady batch still updates it.
- `deleteImage` removes the row from the strip, the library grid and manual folders, but deliberately does not touch smart-folder path caches: the dialog's delete handler calls `foldersStore.refreshT2iPaths()` (Task 19) after it.
- The draft is a per-viewer convenience: every `localStorage` access is guarded, and only fields of the right type survive loading.

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/task16.mjs`)

A fake server (in the script) answers every route with the JSON shapes of contract section 8; the store runs on a real Pinia.

`frontend/t2i-checks.local/task16.mjs`:
```js
// Task 16 logic checks: stores/t2i.ts against a fake server, with real Pinia.
import { createPinia, setActivePinia } from 'pinia'
import {
  assert,
  check,
  deferred,
  finish,
  installFetch,
  installLocalStorage,
  load,
  settle,
  sleep,
} from './harness.mjs'

const ls = installLocalStorage()
const { useToast } = await load('/src/composables/useToast.ts')
const { useMediaStore } = await load('/src/stores/media.ts')
const { useFoldersStore } = await load('/src/stores/folders.ts')
const { ApiError } = await load('/src/api/client.ts')
const t2i = await load('/src/types/t2i.ts')
const toast = useToast()

// ---- a fake server ------------------------------------------------------------

const config = () => ({
  output_root: '',
  output_prefix: '/%Y-%m-%d/t2i_',
  megapixels: [0.5, 1, 1.5, 2],
  default_megapixels: 1,
  default_model: 'krea2',
  model_workflows: { krea2: 3, qwen: null, sd: null, zimage: null },
  content_mode: 'uncensored',
  identity: {},
  window: 4,
  max_batch_size: 500,
  max_count_per_batch: 32,
  models: [{ id: 'krea2', label: 'Krea 2', has_negative: false, identity: 'ref' }],
  aspect_ratios: ['1:1', '3:2', '2:3'],
  seed_policies: ['fixed', 'increment', 'decrement', 'random'],
  seed_max: 2147483647,
  csv: { available: true, total: 100, error: null },
  wildcards: { slots: ['age'], warnings: [] },
})

const row = (id, over = {}) => ({
  id,
  file_path: `/out/img_${id}.png`,
  file_name: `img_${id}.png`,
  batch_id: 'b1',
  model: 'krea2',
  preset_id: 3,
  caption: 'cap',
  prompt_used: `prompt ${id}`,
  negative_used: null,
  seed: id,
  prompt_seed: id,
  width: 1216,
  height: 832,
  megapixels: 1,
  aspect_ratio: '3:2',
  loras: null,
  render_s: 1,
  comfy_prompt_id: `p${id}`,
  created_at: '2026-09-29 12:00:00',
  is_favorite: false,
  form_state: {
    mode: 'manual',
    filter: null,
    caption: 'cap',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    aspect_ratio: '3:2',
    seed: id,
    prompt: `prompt ${id}`,
    negative: null,
    loras: [{ name: 'l.safetensors', strength: 0.5 }],
  },
  ...over,
})

const batchRow = (id, over = {}) => ({
  batch_id: id,
  mode: 'manual',
  state: 'rendering',
  total_steps: 1,
  step: null,
  images_total: 4,
  images_done: 0,
  images_failed: 0,
  next_seed: 105,
  started_at: '2026-09-29T12:00:00+00:00',
  ...over,
})

let S
let store
let onPostBatch = null // hook: runs inside POST /batches before it answers
function resetServer() {
  S = {
    config: config(),
    meta: { total: 100, columns: [{ key: 'nudity', label: 'Nudity', type: 'choice', options: [{ value: 'none', count: 3 }] }] },
    images: [], // newest first
    batches: [],
    media: [],
    jobs: new Map(),
    fail: new Map(), // "METHOD path-prefix" -> status
    gates: new Map(), // "METHOD path-prefix" -> deferred
    nextBatch: 1,
  }
  onPostBatch = null
}
resetServer()

const calls = installFetch(async (c) => {
  const path = c.url.replace(/^\/api/, '')
  const tag = `${c.method} ${path}`
  const failKey = [...S.fail.keys()].find((k) => tag.startsWith(k))
  if (failKey) {
    const status = S.fail.get(failKey)
    S.fail.delete(failKey)
    return { status, json: { detail: `forced ${status}` } }
  }
  // The answer is computed first and only then held at a gate, so a held
  // request carries the server state of the moment it was sent.
  const res = await route(c, path)
  const gateKey = [...S.gates.keys()].find((k) => tag.startsWith(k))
  if (gateKey) await S.gates.get(gateKey).promise
  return res
})

async function route(c, path) {
  if (path === '/t2i/config') return { json: S.config }
  if (path === '/t2i/captions/meta') return { json: S.meta }
  if (path === '/t2i/captions/count') return { json: { count: 7, total: 100 } }
  if (path === '/t2i/captions/random') return { json: { id: 5, caption: 'a drawn caption', aspect_ratio: '3:2', nudity: 'none', artistic_quality: 0.5, erotic_score: null, pornographic_score: null, males: 0, females: 1, clothing: ['dress'] } }
  if (path === '/t2i/captions/resolve') return { json: { resolved_caption: 'resolved', characters: {}, warnings: [] } }
  if (path === '/t2i/prompt') return { json: { prompt: 'a prompt', negative: null, resolved_caption: 'resolved', warnings: [] } }
  if (path === '/t2i/batches' && c.method === 'POST') {
    const id = `batch${S.nextBatch++}`
    if (onPostBatch) await onPostBatch(id)
    return { json: { batch_id: id, total_images: 4, warnings: [] } }
  }
  if (path === '/t2i/batches') return { json: S.batches }
  let m = path.match(/^\/t2i\/batches\/([^/]+)\/cancel$/)
  if (m) {
    for (const j of S.jobs.values()) if (j.t2i_batch_id === m[1]) j.state = 'cancelled'
    return { json: { status: 'cancelled' } }
  }
  m = path.match(/^\/t2i\/images\?(.*)$/)
  if (m) {
    const p = new URLSearchParams(m[1])
    const limit = Number(p.get('limit'))
    const before = p.get('before_id')
    const rows = S.images.filter((r) => before === null || r.id < Number(before)).slice(0, limit)
    return { json: rows }
  }
  m = path.match(/^\/t2i\/images\/(\d+)$/)
  if (m && c.method === 'PATCH') {
    const r = S.images.find((x) => x.id === Number(m[1]))
    if (!r) return { status: 404, json: { detail: 'no such image' } }
    r.form_state = { ...r.form_state, ...c.body }
    return { json: r }
  }
  if (m && c.method === 'DELETE') {
    S.images = S.images.filter((x) => x.id !== Number(m[1]))
    return { json: { status: 'deleted' } }
  }
  m = path.match(/^\/comfy\/jobs\?(.*)$/)
  if (m) {
    const state = new URLSearchParams(m[1]).get('state')
    return { json: [...S.jobs.values()].filter((j) => j.state === state) }
  }
  m = path.match(/^\/comfy\/jobs\/(\d+)$/)
  if (m && c.method === 'GET') {
    const j = S.jobs.get(Number(m[1]))
    return j ? { json: j } : { status: 404, json: { detail: 'no job' } }
  }
  m = path.match(/^\/comfy\/jobs\/(\d+)\/cancel$/)
  if (m) {
    const j = S.jobs.get(Number(m[1]))
    if (j) j.state = 'cancelled'
    return { json: { status: 'cancelled' } }
  }
  if (path.startsWith('/media?')) return { json: S.media }
  m = path.match(/^\/media\/(.+)$/)
  if (m && c.method === 'PATCH') {
    const file = decodeURIComponent(m[1])
    const entry = S.media.find((x) => x.file_path === file) ?? { file_path: file }
    entry.is_favorite = c.body.is_favorite
    return { json: { ...entry } }
  }
  return { status: 404, json: { detail: `unhandled ${c.method} ${path}` } }
}
const count = (method, needle) => calls.filter((c) => c.method === method && c.url.includes(needle)).length
const last = (method, needle) => calls.filter((c) => c.method === method && c.url.includes(needle)).at(-1)

async function fresh() {
  resetServer()
  ls.clear()
  toast.dismiss()
  setActivePinia(createPinia())
  const { useT2iStore } = await load('/src/stores/t2i.ts')
  store = useT2iStore()
  return store
}

const started = (id, over = {}) => ({ batch_id: id, mode: 'manual', total_steps: 1, total_images: 4, ...over })
const stepEvt = (id, over = {}) => ({
  batch_id: id,
  step: 2,
  total_steps: 5,
  caption: 'cap',
  aspect_ratio: '3:2',
  seed: 77,
  prompt: 'the prompt',
  negative: null,
  warnings: [],
  ...over,
})

console.log('open / loading')

await check('open() loads config, caption meta, the first image page, batches and jobs', async () => {
  await fresh()
  S.images = [row(3), row(2), row(1)]
  S.batches = [batchRow('b9')]
  S.jobs.set(11, { id: 11, state: 'queued', t2i_batch_id: 'b9', error: null })
  S.jobs.set(12, { id: 12, state: 'running', t2i_batch_id: null, error: null })
  await store.open()
  assert.equal(store.config.models[0].id, 'krea2')
  assert.equal(store.captionMeta.total, 100)
  assert.deepEqual(store.images.map((i) => i.id), [3, 2, 1])
  assert.equal(store.imagesLoaded, true)
  assert.equal(store.hasMoreImages, false)
  assert.deepEqual(store.activeBatches.map((b) => b.batch_id), ['b9'])
  assert.deepEqual([...store.jobs.keys()], [11])
  assert.equal(toast.state.value, null)
})

await check('open() never rejects: a failing config load toasts and sets configError', async () => {
  await fresh()
  S.images = [row(1)]
  S.fail.set('GET /t2i/config', 500)
  await store.open()
  assert.equal(store.config, null)
  assert.equal(store.configError, 'forced 500')
  assert.equal(toast.state.value.kind, 'warn')
  assert.match(toast.state.value.message, /forced 500/)
  assert.equal(store.captionMeta, null, 'meta is skipped without a config')
  assert.equal(store.images.length, 1, 'the strip still loads')
})

await check('open() skips the caption meta when the CSV is unavailable', async () => {
  await fresh()
  S.config.csv = { available: false, total: 0, error: 'missing file' }
  const before = count('GET', '/t2i/captions/meta')
  await store.open()
  assert.equal(count('GET', '/t2i/captions/meta'), before)
  assert.equal(store.captionMeta, null)
})

await check('a failing caption-meta load toasts but does not reject', async () => {
  await fresh()
  S.fail.set('GET /t2i/captions/meta', 500)
  const meta = await store.loadCaptionMeta()
  assert.equal(meta, null)
  assert.equal(store.metaError, 'forced 500')
  assert.equal(toast.state.value.kind, 'warn')
  await store.loadCaptionMeta()
  assert.equal(store.metaError, null)
})

await check('open() starts a fresh session: selection, live state and old rows are cleared', async () => {
  await fresh()
  S.images = [row(2), row(1)]
  await store.open()
  store.selectImage(2)
  store.handleT2iEvent('batch_started', started('bX'))
  assert.equal(store.selectedId, 2)
  S.images = [row(1)]
  S.batches = []
  await store.open()
  assert.equal(store.selectedId, null)
  assert.deepEqual(store.activeBatches, [])
  assert.deepEqual(store.images.map((i) => i.id), [1])
})

await check('a reply still in flight when open() begins a new session is dropped', async () => {
  await fresh()
  S.images = [row(9)]
  S.gates.set('GET /t2i/images', deferred())
  const staleGate = S.gates.get('GET /t2i/images')
  const stale = store.refreshImages() // sent while the old session held image 9, held
  await settle()
  S.gates.clear()
  S.images = [row(1)]
  S.gates.set('GET /t2i/config', deferred())
  const configGate = S.gates.get('GET /t2i/config')
  const opening = store.open() // clears the session, then waits for the config
  await settle()
  staleGate.resolve()
  await stale
  assert.deepEqual(store.images, [], 'the old session must not repopulate the strip')
  assert.equal(store.imagesLoaded, false)
  S.gates.clear()
  configGate.resolve()
  await opening
  assert.deepEqual(store.images.map((i) => i.id), [1])
})

console.log('images')

await check('refreshImages is sequence-guarded: an older reply cannot overwrite a newer one', async () => {
  await fresh()
  S.images = [row(1)]
  S.gates.set('GET /t2i/images', deferred())
  const slow = store.refreshImages() // reads [1], held at the gate
  await settle()
  S.images = [row(2), row(1)]
  const gate1 = S.gates.get('GET /t2i/images')
  S.gates.delete('GET /t2i/images')
  const quick = store.refreshImages() // reads [2, 1] and answers at once
  await quick
  assert.deepEqual(store.images.map((i) => i.id), [2, 1])
  gate1.resolve()
  await slow
  assert.deepEqual(store.images.map((i) => i.id), [2, 1], 'the stale reply is dropped')
})

await check('paging: loadOlder appends until a short page, then stops asking', async () => {
  await fresh()
  S.images = Array.from({ length: 130 }, (_, i) => row(130 - i))
  await store.open()
  assert.equal(store.images.length, 60)
  assert.equal(store.hasMoreImages, true)
  await store.loadOlder()
  assert.equal(store.images.length, 120)
  assert.equal(store.hasMoreImages, true)
  await store.loadOlder()
  assert.equal(store.images.length, 130)
  assert.equal(store.hasMoreImages, false)
  const before = calls.length
  await store.loadOlder()
  assert.equal(calls.length, before, 'nothing left to load')
  const older = calls.filter((c) => c.method === 'GET' && c.url.includes('before_id')).map((c) => c.url)
  assert.deepEqual(older.slice(-2), [
    '/api/t2i/images?limit=60&before_id=71',
    '/api/t2i/images?limit=60&before_id=11',
  ])
})

await check('a refresh keeps the older pages already loaded', async () => {
  await fresh()
  S.images = Array.from({ length: 130 }, (_, i) => row(130 - i))
  await store.open()
  await store.loadOlder()
  S.images.unshift(row(131))
  await store.refreshImages()
  assert.equal(store.images[0].id, 131)
  assert.equal(store.images.length, 121, 'the new row is added, the older 60 are kept')
  assert.equal(store.hasMoreImages, true)
})

await check('a refresh drops the selection when the image is gone', async () => {
  await fresh()
  S.images = [row(2), row(1)]
  await store.open()
  store.selectImage(1)
  S.images = [row(2)]
  await store.refreshImages()
  assert.equal(store.selectedId, null)
})

await check('a failing refresh sets imagesError without throwing', async () => {
  await fresh()
  S.fail.set('GET /t2i/images', 500)
  await store.refreshImages()
  assert.equal(store.imagesError, 'forced 500')
  assert.equal(store.imagesLoaded, false)
  await store.refreshImages()
  assert.equal(store.imagesError, null)
  assert.equal(store.imagesLoaded, true)
})

console.log('selection and saving')

await check('selectImage returns a deep copy of the form state and selects the row', async () => {
  await fresh()
  S.images = [row(2), row(1)]
  await store.open()
  const form = store.selectImage(2)
  assert.equal(store.selectedId, 2)
  assert.equal(form.prompt, 'prompt 2')
  form.loras[0].strength = 9
  form.prompt = 'edited'
  assert.equal(store.images[0].form_state.prompt, 'prompt 2')
  assert.equal(store.images[0].form_state.loras[0].strength, 0.5)
  assert.equal(store.selectImage(999), null)
  assert.equal(store.selectedId, 2, 'an unknown id leaves the selection alone')
  store.clearSelection()
  assert.equal(store.selectedId, null)
})

await check('saveFormState PATCHes and mirrors the server row; an empty patch sends nothing', async () => {
  await fresh()
  S.images = [row(2), row(1)]
  await store.open()
  await store.saveFormState(2, { prompt: 'saved text', seed: 5 })
  assert.deepEqual(last('PATCH', '/t2i/images/2').body, { prompt: 'saved text', seed: 5 })
  assert.equal(store.images[0].form_state.prompt, 'saved text')
  const before = calls.length
  await store.saveFormState(2, {})
  assert.equal(calls.length, before)
  S.fail.set('PATCH /t2i/images/2', 400)
  await assert.rejects(() => store.saveFormState(2, { prompt: 'x' }), (e) => e instanceof ApiError && e.status === 400)
})

console.log('favourite / delete')

await check('toggleFavorite is optimistic, PATCHes /media, and updates the library copy too', async () => {
  await fresh()
  S.images = [row(2), row(1)]
  S.media = [{ file_path: '/out/img_2.png', is_favorite: false }]
  await store.open()
  const media = useMediaStore()
  await media.loadAllMedia()
  assert.equal(media.allMedia[0].is_favorite, false)
  const p = store.toggleFavorite(2)
  assert.equal(store.images[0].is_favorite, true, 'flipped before the request finishes')
  await p
  assert.deepEqual(last('PATCH', '/media/').body, { is_favorite: true })
  assert.equal(last('PATCH', '/media/').url, '/api/media/%2Fout%2Fimg_2.png')
  assert.equal(media.allMedia[0].is_favorite, true, 'the library copy is not stale')
  await store.toggleFavorite(2)
  assert.equal(store.images[0].is_favorite, false)
  assert.equal(media.allMedia[0].is_favorite, false)
})

await check('toggleFavorite reverts and rethrows when the request fails', async () => {
  await fresh()
  S.images = [row(2)]
  await store.open()
  S.fail.set('PATCH /media/', 500)
  await assert.rejects(() => store.toggleFavorite(2), (e) => e instanceof ApiError)
  assert.equal(store.images[0].is_favorite, false)
  await store.toggleFavorite(404) // an unknown id is a no-op
})

await check('deleteImage removes the row everywhere and refreshes the strip', async () => {
  await fresh()
  S.images = [row(3), row(2), row(1)]
  S.media = [{ file_path: '/out/img_2.png', is_favorite: false }, { file_path: '/out/img_9.png', is_favorite: false }]
  await store.open()
  const media = useMediaStore()
  await media.loadAllMedia()
  const folders = useFoldersStore()
  folders.manualFolders.push({ id: 'f1', name: 'F', kind: 'manual', icon: 'x', items: ['/out/img_2.png'], createdAt: 0 })
  store.selectImage(2)
  await store.deleteImage(2)
  assert.equal(last('DELETE', '/t2i/images/2').method, 'DELETE')
  assert.deepEqual(store.images.map((i) => i.id), [3, 1])
  assert.equal(store.selectedId, null)
  assert.deepEqual(media.allMedia.map((m) => m.file_path), ['/out/img_9.png'])
  assert.deepEqual(folders.manualFolders[0].items, [])
  S.fail.set('DELETE /t2i/images/3', 500)
  await assert.rejects(() => store.deleteImage(3), (e) => e instanceof ApiError)
  assert.deepEqual(store.images.map((i) => i.id), [3, 1], 'a failed delete keeps the row')
})

console.log('prompt, captions')

await check('generatePrompt / rollCaption / resolveCaption / countCaptions call the API', async () => {
  await fresh()
  const prompt = await store.generatePrompt({ caption: 'c', seed: 1, model: 'krea2' })
  assert.equal(prompt.prompt, 'a prompt')
  assert.deepEqual(last('POST', '/t2i/prompt').body, { caption: 'c', seed: 1, model: 'krea2' })
  const rolled = await store.rollCaption({ nudity: ['none'], males: { min: NaN, max: NaN }, clothing_any: [] })
  assert.equal(rolled.caption, 'a drawn caption')
  assert.deepEqual(last('POST', '/captions/random').body, { filter: { nudity: ['none'] } })
  const resolved = await store.resolveCaption({ caption: 'c', seed: 1, model: 'krea2' })
  assert.equal(resolved.resolved_caption, 'resolved')
  const counted = await store.countCaptions({ females: { min: 1, max: 2 } })
  assert.deepEqual(counted, { count: 7, total: 100 })
  assert.deepEqual(last('POST', '/captions/count').body, { filter: { females: { min: 1, max: 2 } } })
})

await check('user-triggered calls let errors through (the dialog words them)', async () => {
  await fresh()
  S.fail.set('POST /t2i/prompt', 502)
  await assert.rejects(() => store.generatePrompt({ caption: 'c', seed: 1, model: 'krea2' }), (e) => e instanceof ApiError && e.status === 502)
  S.fail.set('POST /t2i/captions/random', 404)
  await assert.rejects(() => store.rollCaption({}), (e) => e instanceof ApiError && e.status === 404)
})

await check('countCaptions / resolveCaption: errors and superseded replies give null', async () => {
  await fresh()
  S.fail.set('POST /t2i/captions/count', 500)
  assert.equal(await store.countCaptions({}), null)
  S.gates.set('POST /t2i/captions/count', deferred())
  const gate = S.gates.get('POST /t2i/captions/count')
  const slow = store.countCaptions({ nudity: ['none'] })
  await settle()
  S.gates.delete('POST /t2i/captions/count')
  const quick = await store.countCaptions({ nudity: ['full'] })
  assert.deepEqual(quick, { count: 7, total: 100 })
  gate.resolve()
  assert.equal(await slow, null, 'the older reply is dropped')

  S.fail.set('POST /t2i/captions/resolve', 500)
  assert.equal(await store.resolveCaption({ caption: 'c', seed: 1, model: 'krea2' }), null)
})

// Placed ahead of every test that ends a batch: each batch end schedules its
// own (debounced) library reload, and one still pending from an earlier test
// would be counted here.
await check('t2i_images_changed refreshes the strip now and the library once, debounced', async () => {
  await fresh()
  S.images = [row(1)]
  S.media = [{ file_path: '/out/img_1.png', is_favorite: false }]
  const imageGets = () => count('GET', '/t2i/images?')
  const mediaGets = () => count('GET', '/api/media?')
  const i0 = imageGets()
  const m0 = mediaGets()
  store.handleT2iEvent('t2i_images_changed', { batch_id: 'b', files: ['/out/img_1.png'] })
  store.handleT2iEvent('t2i_images_changed', { batch_id: 'b', files: ['/out/img_2.png'] })
  store.handleT2iEvent('t2i_images_changed', { batch_id: 'b', files: ['/out/img_3.png'] })
  await settle()
  assert.equal(imageGets() - i0, 3, 'the strip refetches per event (sequence-guarded)')
  assert.equal(mediaGets() - m0, 0, 'the library waits')
  await sleep(1300)
  assert.equal(mediaGets() - m0, 0, 'still inside the debounce window')
  await sleep(500)
  assert.equal(mediaGets() - m0, 1, 'one library reload after the burst')
  assert.deepEqual(store.images.map((i) => i.id), [1])
})

await check('VueUse maxWait fires during a steady stream (the store relies on it)', async () => {
  const { useDebounceFn } = await import('@vueuse/core')
  let runs = 0
  const fn = useDebounceFn(() => void runs++, 40, { maxWait: 100 })
  const t0 = Date.now()
  while (Date.now() - t0 < 260) {
    void fn()
    await sleep(15)
  }
  await sleep(80)
  assert.ok(runs >= 2, `expected the max wait to fire during the stream, got ${runs} run(s)`)
})

console.log('batches: start, events, cancel')

await check('startBatch posts a cleaned filter and answers with the next unused seed', async () => {
  await fresh()
  S.batches = [batchRow('batch1', { mode: 'random', total_steps: 2, next_seed: 105 })]
  const res = await store.startBatch({
    mode: 'random',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    seed: 101,
    seed_policy: 'increment',
    batch_size: 2,
    count_per_batch: 2,
    filter: { nudity: [], females: { min: 1, max: 2 } },
  })
  assert.deepEqual(last('POST', '/t2i/batches').body.filter, { females: { min: 1, max: 2 } })
  assert.deepEqual(res, { batch_id: 'batch1', total_images: 4, warnings: [], next_seed: 105 })
  const b = store.activeBatches.find((x) => x.batch_id === 'batch1')
  assert.ok(b, 'the batch is tracked at once, without waiting for a WebSocket frame')
  assert.equal(b.mode, 'random')
  assert.equal(b.images_total, 4)
})

await check('startBatch: the next seed comes from an event that beat the response', async () => {
  await fresh()
  onPostBatch = async (id) => {
    store.handleT2iEvent('batch_started', started(id))
    store.handleT2iEvent('batch_progress', { batch_id: id, phase: 'rendering', images_done: 0, images_failed: 0, images_total: 4, next_seed: 222 })
  }
  const before = count('GET', '/t2i/batches')
  const res = await store.startBatch({ mode: 'manual', model: 'krea2', preset_id: 3, megapixels: 1, seed: 1, seed_policy: 'increment', prompt: 'p', aspect_ratio: '3:2' })
  assert.equal(res.next_seed, 222)
  assert.equal(count('GET', '/t2i/batches'), before, 'no extra request needed')
})

await check('startBatch: a batch that already ended cannot linger as a zombie', async () => {
  await fresh()
  onPostBatch = async (id) => {
    store.handleT2iEvent('batch_started', started(id))
    store.handleT2iEvent('batch_error', { batch_id: id, error: 'preset vanished' })
  }
  const res = await store.startBatch({ mode: 'manual', model: 'krea2', preset_id: 3, megapixels: 1, seed: 1, seed_policy: 'fixed', prompt: 'p', aspect_ratio: '3:2' })
  assert.deepEqual(store.activeBatches, [])
  assert.equal(store.lastFinished.batch_id, res.batch_id)
  assert.equal(store.lastFinished.outcome, 'error')
})

await check('startBatch errors propagate and leave nothing tracked', async () => {
  await fresh()
  S.fail.set('POST /t2i/batches', 409)
  await assert.rejects(
    () => store.startBatch({ mode: 'random', model: 'krea2', preset_id: 3, megapixels: 1, seed: 1, seed_policy: 'fixed' }),
    (e) => e instanceof ApiError && e.status === 409,
  )
  assert.deepEqual(store.activeBatches, [])
})

await check('events: started, step, progress, complete for a Random batch', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('r1', { mode: 'random', total_steps: 5, total_images: 20 }))
  let b = store.activeBatches[0]
  assert.deepEqual([b.batch_id, b.mode, b.state, b.total_steps, b.images_total], ['r1', 'random', 'prompting', 5, 20])
  store.handleT2iEvent('batch_step', stepEvt('r1'))
  assert.equal(store.currentStep.batch_id, 'r1')
  assert.equal(store.currentStep.prompt, 'the prompt')
  assert.equal(store.currentStep.seed, 77)
  assert.equal(store.activeBatches[0].step.step, 2)
  store.handleT2iEvent('batch_progress', { batch_id: 'r1', phase: 'rendering', images_done: 3, images_failed: 1, images_total: 20, next_seed: 900, last_error: 'node X failed' })
  b = store.activeBatches[0]
  assert.deepEqual([b.state, b.images_done, b.images_failed, b.next_seed, b.last_error], ['rendering', 3, 1, 900, 'node X failed'])
  assert.equal(store.randomBatch.batch_id, 'r1')
  assert.equal(store.isBusy, true)
  store.handleT2iEvent('batch_complete', { batch_id: 'r1', images_done: 19, images_failed: 1 })
  assert.deepEqual(store.activeBatches, [])
  assert.equal(store.randomBatch, null)
  assert.equal(store.currentStep, null)
  assert.deepEqual(
    {
      id: store.lastFinished.batch_id,
      mode: store.lastFinished.mode,
      outcome: store.lastFinished.outcome,
      done: store.lastFinished.images_done,
      failed: store.lastFinished.images_failed,
      next: store.lastFinished.next_seed,
      step: store.lastFinished.step.prompt,
      error: store.lastFinished.error,
    },
    { id: 'r1', mode: 'random', outcome: 'complete', done: 19, failed: 1, next: 900, step: 'the prompt', error: null },
  )
})

await check('events: a Manual batch does not drive currentStep; cancelled and error outcomes', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('m1'))
  store.handleT2iEvent('batch_step', stepEvt('m1', { step: 1, total_steps: 1 }))
  assert.equal(store.currentStep, null)
  assert.equal(store.activeBatches[0].step.prompt, 'the prompt')
  store.handleT2iEvent('batch_cancelled', { batch_id: 'm1', images_done: 1, images_failed: 0 })
  assert.equal(store.lastFinished.outcome, 'cancelled')
  store.handleT2iEvent('batch_started', started('m2'))
  store.handleT2iEvent('batch_error', { batch_id: 'm2', error: 'ComfyUI is unreachable' })
  assert.equal(store.lastFinished.outcome, 'error')
  assert.equal(store.lastFinished.error, 'ComfyUI is unreachable')
  assert.equal(toast.state.value.kind, 'warn')
  assert.match(toast.state.value.message, /ComfyUI is unreachable/)
})

await check('stale-batch protection: unknown and already-finished ids are ignored', async () => {
  await fresh()
  store.handleT2iEvent('batch_step', stepEvt('ghost'))
  store.handleT2iEvent('batch_progress', { batch_id: 'ghost', phase: 'rendering', images_done: 1, images_failed: 0, images_total: 2, next_seed: 1 })
  store.handleT2iEvent('batch_complete', { batch_id: 'ghost', images_done: 1, images_failed: 0 })
  assert.deepEqual(store.activeBatches, [])
  assert.equal(store.currentStep, null, 'a ghost step must not drive the live boxes')
  assert.equal(store.lastFinished, null)
  store.handleT2iEvent('batch_started', started('a1'))
  store.handleT2iEvent('batch_complete', { batch_id: 'a1', images_done: 4, images_failed: 0 })
  store.handleT2iEvent('batch_started', started('a1')) // a replayed frame
  assert.deepEqual(store.activeBatches, [], 'a finished batch is never revived')
  store.handleT2iEvent('batch_progress', { batch_id: 'a1', phase: 'rendering', images_done: 0, images_failed: 0, images_total: 4, next_seed: 1 })
  assert.deepEqual(store.activeBatches, [])
  store.handleT2iEvent('mystery_event', { batch_id: 'a1' })
  store.handleT2iEvent('batch_started', {})
  assert.deepEqual(store.activeBatches, [])
})

await check('cancelAll cancels every active batch, then reconciles the job chips', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('c1'))
  store.handleT2iEvent('batch_started', started('c2', { mode: 'random' }))
  S.jobs.set(31, { id: 31, state: 'queued', t2i_batch_id: 'c1', error: null })
  store.handleComfyEvent('job_update', { job_id: 31, state: 'queued', error: null })
  await settle()
  assert.equal(store.jobs.size, 1, 'a chip for one of the batch jobs is showing')
  await store.cancelAll()
  const posted = calls.filter((c) => c.method === 'POST' && /\/t2i\/batches\/c\d\/cancel/.test(c.url)).map((c) => c.url)
  assert.deepEqual(posted.slice(-2).sort(), ['/api/t2i/batches/c1/cancel', '/api/t2i/batches/c2/cancel'])
  assert.deepEqual(store.activeBatches, [])
  assert.equal(store.lastFinished.outcome, 'cancelled')
  assert.equal(store.jobs.size, 0)
  assert.equal(count('POST', '/comfy/jobs/31/cancel'), 0, 'the batch cancel already stopped its jobs')
})

await check('cancelAll: a failing batch is reported after the others were tried', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('d1'))
  store.handleT2iEvent('batch_started', started('d2'))
  S.fail.set('POST /t2i/batches/d1/cancel', 500)
  await assert.rejects(() => store.cancelAll(), (e) => e instanceof ApiError)
  assert.deepEqual(store.activeBatches.map((b) => b.batch_id), ['d1'])
})

await check('cancelAll with no batch still cancels orphan job chips (after a server restart)', async () => {
  await fresh()
  S.jobs.set(41, { id: 41, state: 'queued', t2i_batch_id: 'gone', error: null })
  await store.reattach()
  assert.deepEqual([...store.jobs.keys()], [41])
  await store.cancelAll()
  assert.equal(count('POST', '/comfy/jobs/41/cancel'), 1)
  assert.equal(store.jobs.size, 0)
})

await check('an empty cancelAll does nothing', async () => {
  await fresh()
  const before = calls.length
  await store.cancelAll()
  assert.equal(calls.length, before)
})

console.log('reattach / job events')

await check('reattach rebuilds the running batches and, for Random, the live step', async () => {
  await fresh()
  S.batches = [
    batchRow('r7', { mode: 'random', state: 'prompting', total_steps: 5, step: { step: 2, total_steps: 5, caption: 'c', aspect_ratio: '2:3', seed: 12, prompt: 'p', negative: null, warnings: [] }, images_total: 10, images_done: 1, next_seed: 20 }),
  ]
  S.images = [row(4), row(3)]
  await store.reattach()
  assert.deepEqual(store.images.map((i) => i.id), [4, 3], 'the strip is rebuilt too')
  assert.equal(store.randomBatch.batch_id, 'r7')
  assert.equal(store.currentStep.batch_id, 'r7')
  assert.equal(store.currentStep.aspect_ratio, '2:3')
  assert.equal(store.activeBatches[0].last_error, null)
})

await check('refreshBatches: a batch the server no longer lists has ended; new local ones are kept', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('old1'))
  S.batches = []
  S.gates.set('GET /t2i/batches', deferred())
  const gate = S.gates.get('GET /t2i/batches')
  const p = store.refreshBatches()
  await settle()
  store.handleT2iEvent('batch_started', started('new1')) // began while the request was in flight
  gate.resolve()
  S.gates.clear()
  await p
  assert.deepEqual(store.activeBatches.map((b) => b.batch_id), ['new1'])
  assert.equal(store.lastFinished.batch_id, 'old1')
})

await check('refreshBatches never moves a counter backwards', async () => {
  await fresh()
  store.handleT2iEvent('batch_started', started('p1'))
  store.handleT2iEvent('batch_progress', { batch_id: 'p1', phase: 'rendering', images_done: 3, images_failed: 0, images_total: 4, next_seed: 9 })
  S.batches = [batchRow('p1', { images_done: 1 })]
  await store.refreshBatches()
  assert.equal(store.activeBatches[0].images_done, 3)
})

await check('comfy job events reach the tracker: a t2i job is adopted, another feature is not', async () => {
  await fresh()
  S.jobs.set(51, { id: 51, state: 'queued', t2i_batch_id: 'b1', error: null })
  S.jobs.set(52, { id: 52, state: 'queued', t2i_batch_id: null, i2v_source_path: '/x.png', error: null })
  store.handleComfyEvent('job_update', { job_id: 51, state: 'queued', error: null })
  store.handleComfyEvent('job_update', { job_id: 52, state: 'queued', error: null })
  await settle()
  assert.deepEqual([...store.jobs.keys()], [51])
  store.handleComfyEvent('job_progress', { job_id: 51, value: 5, max: 10 })
  assert.equal(store.jobs.get(51).value, 5)
  assert.deepEqual(store.activeJobIds, [51])
  store.handleComfyEvent('job_update', { job_id: 51, state: 'failed', error: 'node exploded' })
  assert.equal(store.jobs.get(51).error, 'node exploded')
  assert.deepEqual(store.activeJobIds, [])
  store.dismissJob(51)
  assert.equal(store.jobs.size, 0)
})

await check('batch end refreshes the chips: a chip the server no longer lists disappears', async () => {
  await fresh()
  S.jobs.set(61, { id: 61, state: 'queued', t2i_batch_id: 'e1', error: null })
  S.batches = [batchRow('e1')]
  store.handleT2iEvent('batch_started', started('e1'))
  await store.reattach()
  assert.deepEqual([...store.jobs.keys()], [61])
  S.jobs.delete(61)
  store.handleT2iEvent('batch_complete', { batch_id: 'e1', images_done: 1, images_failed: 0 })
  await sleep(20)
  assert.equal(store.jobs.size, 0)
})

console.log('status text')

await check('statusText describes the Random batch, else the newest, and counts the rest', async () => {
  await fresh()
  assert.equal(store.statusText, '')
  store.handleT2iEvent('batch_started', started('s1', { total_steps: 1, total_images: 4 }))
  assert.equal(store.statusText, 'rendering')
  store.handleT2iEvent('batch_started', started('s2', { mode: 'random', total_steps: 3, total_images: 6 }))
  store.handleT2iEvent('batch_step', stepEvt('s2', { step: 1, total_steps: 3 }))
  assert.equal(store.statusText, 'Batch 1/3 · writing prompts (+1 more)')
})

console.log('draft')

await check('draft: round trip, corrupt JSON, and unusable storage all degrade quietly', async () => {
  await fresh()
  assert.equal(store.loadDraft(), null)
  store.saveDraft({ mode: 'random', seed: 5, prompt: 'p', model: 'krea2', loras: [], filter: null, caption: null, preset_id: 3, megapixels: 1, aspect_ratio: '3:2', negative: null })
  assert.equal(JSON.parse(ls.get(t2i.DRAFT_STORAGE_KEY)).seed, 5)
  assert.equal(store.loadDraft().prompt, 'p')
  ls.set(t2i.DRAFT_STORAGE_KEY, '{not json')
  assert.equal(store.loadDraft(), null)
  ls.set(t2i.DRAFT_STORAGE_KEY, JSON.stringify({ seed: -4, mode: 'nope' }))
  assert.equal(store.loadDraft(), null)
  store.saveDraft({ seed: 1 })
  store.clearDraft()
  assert.equal(ls.has(t2i.DRAFT_STORAGE_KEY), false)

  const real = globalThis.localStorage
  globalThis.localStorage = {
    getItem() { throw new Error('blocked') },
    setItem() { throw new Error('blocked') },
    removeItem() { throw new Error('blocked') },
  }
  try {
    assert.equal(store.loadDraft(), null)
    store.saveDraft({ seed: 1 })
    store.clearDraft()
  } finally {
    globalThis.localStorage = real
  }
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task16.mjs`
Expected: FAIL (exit code 1) — every check reports `Failed to load url /src/stores/t2i.ts (resolved id: /src/stores/t2i.ts). Does the file exist?`, and the run ends with `1 passed, 38 failed` (the one passing check does not use the store).
- [ ] **Step 3: Implement**

`frontend/src/stores/t2i.ts`:
```ts
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import * as t2iApi from '../api/t2i'
import { updateMedia } from '../api/media'
import { useJobTracker } from '../composables/useJobTracker'
import { useToast } from '../composables/useToast'
import { useFoldersStore } from './folders'
import { useMediaStore } from './media'
import {
  DRAFT_STORAGE_KEY,
  batchStatusLine,
  cleanFilter,
  mergeImagePage,
  mergeOlderPage,
  sanitizeDraft,
  type CaptionCount,
  type CaptionFilter,
  type CaptionMeta,
  type CaptionRow,
  type T2iActiveBatch,
  type T2iBatchInfo,
  type T2iBatchOutcome,
  type T2iBatchRequest,
  type T2iBatchStepEvent,
  type T2iConfig,
  type T2iFinishedBatch,
  type T2iFormState,
  type T2iImage,
  type T2iMode,
  type T2iPromptResult,
  type T2iResolveResult,
  type T2iStartResult,
} from '../types/t2i'

const IMAGE_PAGE_SIZE = 60
// New images reach the library grid through one debounced reload, not one
// per image; the max wait keeps a long, steady batch from postponing it
// until the batch is over.
const MEDIA_RELOAD_DEBOUNCE_MS = 1500
const MEDIA_RELOAD_MAX_WAIT_MS = 5000
// How many ended batch ids are remembered so a late frame cannot revive one.
const FINISHED_IDS_CAP = 200

const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e))
const asStr = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback)
const asNum = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : fallback
const asNumOrNull = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null
// Job and media rows are POSIX or native depending on the endpoint.
const samePath = (a: string, b: string): boolean => a.replace(/\\/g, '/') === b.replace(/\\/g, '/')
const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

/**
 * State and actions behind the Text to Image dialog.
 *
 * The store never subscribes to the WebSocket: useWebSocket needs a component
 * setup context, so the dialog subscribes to `t2i` and `comfy` and forwards
 * every message to handleT2iEvent / handleComfyEvent. After a reconnect the
 * dialog should call reattach() to pick up what it missed.
 */
export const useT2iStore = defineStore('t2i', () => {
  const toast = useToast()
  const media = useMediaStore()
  const folders = useFoldersStore()
  // Job chips: any ComfyUI job that carries a t2i_batch_id.
  const tracker = useJobTracker({ match: (job) => !!job.t2i_batch_id })

  // ---- state ---------------------------------------------------------------

  const config = ref<T2iConfig | null>(null)
  const configLoading = ref(false)
  const configError = ref<string | null>(null)
  const captionMeta = ref<CaptionMeta | null>(null)
  const metaLoading = ref(false)
  const metaError = ref<string | null>(null)

  // The strip, newest first. `imagesLoaded` turns true after the first
  // successful load of a session: the strip waits for it before it may say
  // "No images yet".
  const images = ref<T2iImage[]>([])
  const hasMoreImages = ref(false)
  const imagesLoaded = ref(false)
  const imagesLoading = ref(false)
  const imagesError = ref<string | null>(null)
  const loadingOlder = ref(false)
  const selectedId = ref<number | null>(null)
  const selectedImage = computed(() => images.value.find((i) => i.id === selectedId.value) ?? null)

  // Batches that are running, oldest first.
  const activeBatches = ref<T2iActiveBatch[]>([])
  // The latest batch_step of the running Random batch: what the dialog's
  // read-only Caption / Prompt / Aspect / Seed boxes mirror. Null when no
  // Random batch is running.
  const currentStep = ref<T2iBatchStepEvent | null>(null)
  // The batch that ended most recently. The dialog unlocks from it: for a
  // Random batch its `step` fills Caption / Prompt / Aspect and `next_seed`
  // the Seed box. Reset to null when the next batch begins.
  const lastFinished = ref<T2iFinishedBatch | null>(null)

  const randomBatch = computed(() => activeBatches.value.find((b) => b.mode === 'random') ?? null)
  // Anything still running that Cancel could stop.
  const isBusy = computed(
    () => activeBatches.value.length > 0 || tracker.activeJobIds.value.length > 0,
  )
  // "Batch 12/40 · 37/160 images · rendering" for the Random batch, else the
  // newest one, plus a count of the others.
  const statusText = computed(() => {
    const list = activeBatches.value
    if (list.length === 0) return ''
    const main = list.find((b) => b.mode === 'random') ?? list[list.length - 1]
    const others = list.length - 1
    return batchStatusLine(main) + (others > 0 ? ` (+${others} more)` : '')
  })

  // ---- bookkeeping -------------------------------------------------------------

  // Each request family numbers its calls, so a reply that arrives after a
  // newer request (or after open() started a fresh session) is dropped.
  let imagesSeq = 0
  let batchesSeq = 0
  let countSeq = 0
  let resolveSeq = 0
  // Batches that ended, so a late or replayed frame cannot bring one back.
  const finishedIds = new Set<string>()

  // ---- loading -------------------------------------------------------------

  // Each loader below reports its own failure (toast + error field) and
  // never rejects, so open() cannot leak an unhandled rejection.
  async function loadConfig(): Promise<T2iConfig | null> {
    configLoading.value = true
    try {
      const loaded = await t2iApi.fetchT2iConfig()
      config.value = loaded
      configError.value = null
      return loaded
    } catch (e) {
      configError.value = errorMessage(e)
      toast.show(`Couldn't load Text to Image settings: ${configError.value}`, 'warn', 5000)
      return null
    } finally {
      configLoading.value = false
    }
  }

  async function loadCaptionMeta(): Promise<CaptionMeta | null> {
    metaLoading.value = true
    try {
      const meta = await t2iApi.fetchCaptionMeta()
      captionMeta.value = meta
      metaError.value = null
      return meta
    } catch (e) {
      metaError.value = errorMessage(e)
      toast.show(`Couldn't load the caption filters: ${metaError.value}`, 'warn', 5000)
      return null
    } finally {
      metaLoading.value = false
    }
  }

  // Refetches the first page and folds it into the list. A reply that
  // arrives after a newer request was made is dropped.
  async function refreshImages(): Promise<void> {
    const seq = ++imagesSeq
    imagesLoading.value = true
    try {
      const fresh = await t2iApi.listT2iImages(IMAGE_PAGE_SIZE)
      if (seq !== imagesSeq) return
      const merged = mergeImagePage(images.value, fresh, IMAGE_PAGE_SIZE, hasMoreImages.value)
      images.value = merged.images
      hasMoreImages.value = merged.hasMore
      imagesLoaded.value = true
      imagesError.value = null
      // The selected image can vanish: deleted elsewhere, or pruned by the
      // server because its media row is gone.
      if (selectedId.value !== null && !merged.images.some((i) => i.id === selectedId.value)) {
        selectedId.value = null
      }
    } catch (e) {
      if (seq === imagesSeq) imagesError.value = errorMessage(e)
    } finally {
      if (seq === imagesSeq) imagesLoading.value = false
    }
  }

  async function loadOlder(): Promise<void> {
    if (!hasMoreImages.value || loadingOlder.value || images.value.length === 0) return
    loadingOlder.value = true
    const cursor = images.value[images.value.length - 1].id
    try {
      const page = await t2iApi.listT2iImages(IMAGE_PAGE_SIZE, cursor)
      images.value = mergeOlderPage(images.value, page)
      hasMoreImages.value = page.length >= IMAGE_PAGE_SIZE
    } catch (e) {
      toast.show(`Couldn't load older images: ${errorMessage(e)}`, 'warn')
    } finally {
      loadingOlder.value = false
    }
  }

  /** Opening the dialog: a fresh session over whatever the server is still running. */
  async function open(): Promise<void> {
    imagesSeq += 1
    batchesSeq += 1
    selectedId.value = null
    images.value = []
    hasMoreImages.value = false
    imagesLoaded.value = false
    imagesError.value = null
    activeBatches.value = []
    currentStep.value = null
    lastFinished.value = null
    finishedIds.clear()
    tracker.reset()
    const loaded = await loadConfig()
    await Promise.all([
      loaded?.csv.available ? loadCaptionMeta() : Promise.resolve(null),
      reattach(),
    ])
  }

  /** Closing does not stop batches: they run on the server. */
  function close(): void {
    selectedId.value = null
  }

  // ---- captions and prompts -----------------------------------------------------

  // Review-only. Errors propagate: the dialog words them (a 502 means the
  // VLM failed and the user retries).
  function generatePrompt(body: {
    caption: string
    seed: number
    model: string
  }): Promise<T2iPromptResult> {
    return t2iApi.generateT2iPrompt(body)
  }

  // One random CSV row. A 404 ApiError means nothing matches the filter.
  function rollCaption(filter: CaptionFilter): Promise<CaptionRow> {
    return t2iApi.randomCaption(cleanFilter(filter))
  }

  /**
   * How many CSV rows match. Debouncing is the caller's job. Null when the
   * request failed or a newer one was made meanwhile (leave the label alone).
   */
  async function countCaptions(filter: CaptionFilter): Promise<CaptionCount | null> {
    const seq = ++countSeq
    try {
      const result = await t2iApi.countCaptions(cleanFilter(filter))
      return seq === countSeq ? result : null
    } catch {
      return null
    }
  }

  /** The read-only "Resolved caption" preview. Null on failure or when superseded. */
  async function resolveCaption(body: {
    caption: string
    seed: number
    model: string
  }): Promise<T2iResolveResult | null> {
    const seq = ++resolveSeq
    try {
      const result = await t2iApi.resolveCaption(body)
      return seq === resolveSeq ? result : null
    } catch {
      return null
    }
  }

  // ---- batches -----------------------------------------------------------------

  function rememberFinished(id: string): void {
    finishedIds.add(id)
    if (finishedIds.size > FINISHED_IDS_CAP) {
      const oldest = finishedIds.values().next().value
      if (oldest !== undefined) finishedIds.delete(oldest)
    }
  }

  function findBatch(id: string): T2iActiveBatch | undefined {
    return activeBatches.value.find((b) => b.batch_id === id)
  }

  function beginBatch(batch: T2iActiveBatch): void {
    activeBatches.value.push(batch)
    lastFinished.value = null
    if (batch.mode === 'random') currentStep.value = null
  }

  // Moves a batch from "running" to `lastFinished`. Idempotent: an unknown or
  // already-finished id does nothing and returns null.
  function finishBatch(
    id: string,
    outcome: T2iBatchOutcome,
    over: { images_done?: number; images_failed?: number; error?: string | null } = {},
  ): T2iFinishedBatch | null {
    const batch = findBatch(id)
    if (!batch) return null
    activeBatches.value = activeBatches.value.filter((b) => b.batch_id !== id)
    rememberFinished(id)
    const finished: T2iFinishedBatch = {
      batch_id: id,
      mode: batch.mode,
      outcome,
      images_done: over.images_done ?? batch.images_done,
      images_failed: over.images_failed ?? batch.images_failed,
      next_seed: batch.next_seed,
      step: batch.step ? cloneJson(batch.step) : null,
      error: over.error ?? null,
    }
    lastFinished.value = finished
    if (currentStep.value?.batch_id === id) currentStep.value = null
    return finished
  }

  const scheduleMediaReload = useDebounceFn(
    () => {
      media.loadAllMedia().catch((e) => console.warn('T2I: could not reload the library', e))
    },
    MEDIA_RELOAD_DEBOUNCE_MS,
    { maxWait: MEDIA_RELOAD_MAX_WAIT_MS },
  )

  // What a finished batch leaves to reconcile: the strip, the library grid
  // and the job chips (a missed frame must not strand one).
  function afterBatchEnded(): void {
    void refreshImages()
    void scheduleMediaReload()
    tracker.refresh().catch((e) => console.warn('T2I: could not refresh the job chips', e))
  }

  function applyBatchSnapshot(row: T2iBatchInfo): void {
    if (finishedIds.has(row.batch_id)) return
    const existing = findBatch(row.batch_id)
    if (existing) {
      existing.mode = row.mode
      existing.state = row.state
      existing.total_steps = row.total_steps
      if (row.step) existing.step = row.step
      existing.images_total = row.images_total
      // The snapshot may be older than a frame already handled: counters only grow.
      existing.images_done = Math.max(existing.images_done, row.images_done)
      existing.images_failed = Math.max(existing.images_failed, row.images_failed)
      existing.next_seed = existing.next_seed ?? row.next_seed
      existing.started_at = row.started_at
    } else {
      activeBatches.value.push({ ...row, last_error: null })
    }
    const cur = currentStep.value
    if (
      row.mode === 'random' &&
      row.step &&
      (!cur || cur.batch_id !== row.batch_id || row.step.step > cur.step)
    ) {
      currentStep.value = { ...row.step, batch_id: row.batch_id }
    }
  }

  /**
   * Reloads the running batches from the server. A batch held locally that
   * the server no longer lists has ended (its frames were missed) and moves
   * to `lastFinished`; one that began while the request was in flight is kept.
   */
  async function refreshBatches(): Promise<void> {
    const seq = ++batchesSeq
    const before = activeBatches.value.map((b) => b.batch_id)
    const rows = await t2iApi.listT2iBatches()
    if (seq !== batchesSeq) return
    const listed = new Set(rows.map((r) => r.batch_id))
    for (const id of before) {
      if (!listed.has(id)) finishBatch(id, 'complete')
    }
    for (const row of rows) applyBatchSnapshot(row)
  }

  /**
   * On open, and after a WebSocket reconnect: rebuild from the server
   * everything a missed frame could have changed (running batches, job
   * chips, the strip).
   */
  async function reattach(): Promise<void> {
    await Promise.all([
      refreshBatches().catch((e) => console.warn('T2I: could not reload the running batches', e)),
      tracker.refresh().catch((e) => console.warn('T2I: could not reload the running jobs', e)),
      refreshImages(),
    ])
  }

  // The next unused seed the server reported for a batch, asking once if no
  // frame has carried it yet. Null when there is none (e.g. Randomize).
  async function nextSeedFor(id: string): Promise<number | null> {
    const known = (): number | null =>
      findBatch(id)?.next_seed ??
      (lastFinished.value?.batch_id === id ? lastFinished.value.next_seed : null)
    const now = known()
    if (now !== null) return now
    try {
      await refreshBatches()
    } catch (e) {
      console.warn('T2I: could not read the next seed', e)
    }
    return known()
  }

  /**
   * Starts a server-side batch. Validation problems (400), a Random batch
   * already running (409) and ComfyUI failures (502) throw an ApiError for the
   * dialog to show. On success the batch is tracked at once and the result
   * carries `next_seed`, which the dialog puts in its Seed box.
   */
  async function startBatch(req: T2iBatchRequest): Promise<T2iStartResult> {
    const body: T2iBatchRequest = { ...req }
    if (req.filter) body.filter = cleanFilter(req.filter)
    const started = await t2iApi.startT2iBatch(body)
    // The WebSocket frames may have beaten this response, or the batch may
    // already be over: only a batch that is neither is added here.
    if (!findBatch(started.batch_id) && !finishedIds.has(started.batch_id)) {
      beginBatch({
        batch_id: started.batch_id,
        mode: req.mode,
        state: req.mode === 'random' ? 'prompting' : 'rendering',
        total_steps: req.mode === 'random' ? (req.batch_size ?? 1) : 1,
        step: null,
        images_total: started.total_images,
        images_done: 0,
        images_failed: 0,
        next_seed: null,
        started_at: new Date().toISOString(),
        last_error: null,
      })
    }
    return { ...started, next_seed: await nextSeedFor(started.batch_id) }
  }

  /**
   * Cancels every running batch (the server also cancels their jobs), then
   * any job chip still left, e.g. one whose batch died with a server restart.
   * Everything is attempted; the first failure is rethrown afterwards.
   */
  async function cancelAll(): Promise<void> {
    const ids = activeBatches.value.map((b) => b.batch_id)
    const failures: unknown[] = []
    const results = await Promise.allSettled(
      ids.map(async (id) => {
        await t2iApi.cancelT2iBatch(id)
        // The batch_cancelled frame normally does this; doing it here as
        // well means a missed frame cannot leave the dialog locked.
        finishBatch(id, 'cancelled')
      }),
    )
    for (const r of results) if (r.status === 'rejected') failures.push(r.reason)
    if (ids.length > 0) {
      try {
        await tracker.refresh()
      } catch (e) {
        console.warn('T2I: could not refresh the job chips after cancelling', e)
      }
    }
    try {
      await tracker.cancelAll()
    } catch (e) {
      failures.push(e)
    }
    if (failures.length > 0) throw failures[0]
  }

  // ---- WebSocket handlers ---------------------------------------------------------

  function onBatchStarted(d: Record<string, unknown>): void {
    const id = asStr(d.batch_id)
    if (!id || finishedIds.has(id)) return
    const totalSteps = asNum(d.total_steps, 1)
    const totalImages = asNum(d.total_images, 0)
    const existing = findBatch(id)
    if (existing) {
      existing.total_steps = totalSteps
      existing.images_total = totalImages
      return
    }
    const mode: T2iMode = d.mode === 'random' ? 'random' : 'manual'
    beginBatch({
      batch_id: id,
      mode,
      state: mode === 'random' ? 'prompting' : 'rendering',
      total_steps: totalSteps,
      step: null,
      images_total: totalImages,
      images_done: 0,
      images_failed: 0,
      next_seed: null,
      started_at: new Date().toISOString(),
      last_error: null,
    })
  }

  function onBatchStep(d: Record<string, unknown>): void {
    const batch = findBatch(asStr(d.batch_id))
    if (!batch) return
    const step: T2iBatchStepEvent = {
      batch_id: batch.batch_id,
      step: asNum(d.step, 1),
      total_steps: asNum(d.total_steps, batch.total_steps),
      caption: asStr(d.caption),
      aspect_ratio: asStr(d.aspect_ratio),
      seed: asNum(d.seed, 0),
      prompt: asStr(d.prompt),
      negative: d.negative == null ? null : asStr(d.negative),
      warnings: Array.isArray(d.warnings) ? d.warnings.map(String) : [],
    }
    batch.step = step
    batch.total_steps = step.total_steps
    // Only a Random batch drives the live boxes; a Manual step just echoes the form.
    if (batch.mode === 'random') currentStep.value = step
  }

  function onBatchProgress(d: Record<string, unknown>): void {
    const batch = findBatch(asStr(d.batch_id))
    if (!batch) return
    if (d.phase === 'prompting' || d.phase === 'rendering') batch.state = d.phase
    batch.images_done = asNum(d.images_done, batch.images_done)
    batch.images_failed = asNum(d.images_failed, batch.images_failed)
    batch.images_total = asNum(d.images_total, batch.images_total)
    if ('next_seed' in d) batch.next_seed = asNumOrNull(d.next_seed)
    const lastError = asStr(d.last_error)
    if (lastError) batch.last_error = lastError
  }

  function onBatchEnd(d: Record<string, unknown>, outcome: T2iBatchOutcome): void {
    const finished = finishBatch(asStr(d.batch_id), outcome, {
      images_done: typeof d.images_done === 'number' ? d.images_done : undefined,
      images_failed: typeof d.images_failed === 'number' ? d.images_failed : undefined,
    })
    if (finished) afterBatchEnded()
  }

  function onBatchError(d: Record<string, unknown>): void {
    const error = asStr(d.error, 'the batch failed')
    const finished = finishBatch(asStr(d.batch_id), 'error', { error })
    if (!finished) return
    toast.show(`Text to Image batch failed: ${error}`, 'warn', 6000)
    afterBatchEnded()
  }

  /**
   * The `t2i` channel. Frames for a batch this store does not know (or that
   * already ended) are ignored, except batch_started.
   */
  function handleT2iEvent(event: string, data: Record<string, unknown>): void {
    switch (event) {
      case 'batch_started':
        return onBatchStarted(data)
      case 'batch_step':
        return onBatchStep(data)
      case 'batch_progress':
        return onBatchProgress(data)
      case 'batch_complete':
        return onBatchEnd(data, 'complete')
      case 'batch_cancelled':
        return onBatchEnd(data, 'cancelled')
      case 'batch_error':
        return onBatchError(data)
      case 't2i_images_changed':
        void refreshImages()
        void scheduleMediaReload()
        return
    }
  }

  /** The `comfy` channel: job_update / job_progress for the job chips. */
  function handleComfyEvent(event: string, data: Record<string, unknown>): void {
    tracker.handleComfyEvent(event, data)
  }

  // ---- selection, autosave, favourite, delete -----------------------------------------

  /**
   * Selects an image and returns a copy of its (complete) form_state for the
   * dialog to load; null, with the selection untouched, for an unknown id.
   */
  function selectImage(id: number): T2iFormState | null {
    const image = images.value.find((i) => i.id === id)
    if (!image) return null
    selectedId.value = id
    return cloneJson(image.form_state)
  }

  /** Back to the scratch form ("Stop editing"). */
  function clearSelection(): void {
    selectedId.value = null
  }

  /**
   * Autosave target: writes the image's EDITABLE form_state only, then
   * mirrors the server's merged row locally so selecting the tile again loads
   * what was just saved. An empty patch sends nothing (the server refuses it).
   */
  async function saveFormState(id: number, fields: Partial<T2iFormState>): Promise<void> {
    if (Object.keys(fields).length === 0) return
    const row = await t2iApi.patchT2iImage(id, fields)
    const index = images.value.findIndex((i) => i.id === id)
    if (index >= 0) images.value[index] = row
  }

  function mirrorFavoriteToLibrary(path: string, favorite: boolean): void {
    const entry = media.allMedia.find((m) => samePath(m.file_path, path))
    if (entry) entry.is_favorite = favorite
    if (media.selectedMedia && samePath(media.selectedMedia.file_path, path)) {
      media.selectedMedia.is_favorite = favorite
    }
  }

  /** Optimistic; the library copy is updated too. Reverts and rethrows on failure. */
  async function toggleFavorite(id: number): Promise<void> {
    const image = images.value.find((i) => i.id === id)
    if (!image) return
    const next = !image.is_favorite
    image.is_favorite = next
    try {
      const updated = await updateMedia(image.file_path, { is_favorite: next })
      image.is_favorite = updated.is_favorite
      mirrorFavoriteToLibrary(image.file_path, updated.is_favorite)
    } catch (e) {
      image.is_favorite = !next
      throw e
    }
  }

  function removeFromLibrary(path: string): void {
    media.allMedia = media.allMedia.filter((m) => !samePath(m.file_path, path))
    if (media.selectedMedia && samePath(media.selectedMedia.file_path, path)) {
      media.selectedMedia = null
    }
    // A manual folder must not keep a member whose file is gone.
    folders.purgePath(path)
  }

  /**
   * Deletes the image (the server moves its file to the OS trash) and drops
   * it from the strip, the library grid and manual folders. A failure throws
   * and changes nothing. Does not touch the smart-folder path caches.
   */
  async function deleteImage(id: number): Promise<void> {
    const image = images.value.find((i) => i.id === id)
    await t2iApi.deleteT2iImage(id)
    images.value = images.value.filter((i) => i.id !== id)
    if (selectedId.value === id) selectedId.value = null
    if (image) removeFromLibrary(image.file_path)
    // Pulls one older row into the gap when there is a next page.
    void refreshImages()
  }

  // ---- scratch-form draft -------------------------------------------------------------

  // A per-viewer convenience: every access is guarded, and the dialog works
  // the same without it (private window, blocked or full storage).

  function loadDraft(): Partial<T2iFormState> | null {
    try {
      const raw = localStorage.getItem(DRAFT_STORAGE_KEY)
      return raw ? sanitizeDraft(JSON.parse(raw)) : null
    } catch {
      return null
    }
  }

  function saveDraft(form: Partial<T2iFormState>): void {
    try {
      localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(form))
    } catch {
      // Not persisted; nothing depends on it.
    }
  }

  function clearDraft(): void {
    try {
      localStorage.removeItem(DRAFT_STORAGE_KEY)
    } catch {
      // Nothing to clear.
    }
  }

  return {
    // state
    config,
    configLoading,
    configError,
    captionMeta,
    metaLoading,
    metaError,
    images,
    hasMoreImages,
    imagesLoaded,
    imagesLoading,
    imagesError,
    loadingOlder,
    selectedId,
    selectedImage,
    activeBatches,
    randomBatch,
    currentStep,
    lastFinished,
    isBusy,
    statusText,
    jobs: tracker.jobs,
    activeJobIds: tracker.activeJobIds,
    // loading
    open,
    close,
    loadConfig,
    loadCaptionMeta,
    refreshImages,
    loadOlder,
    reattach,
    refreshBatches,
    // captions and prompts
    generatePrompt,
    rollCaption,
    countCaptions,
    resolveCaption,
    // batches
    startBatch,
    cancelAll,
    dismissJob: tracker.dismiss,
    cancelJob: tracker.cancel,
    handleT2iEvent,
    handleComfyEvent,
    // selection and per-image actions
    selectImage,
    clearSelection,
    saveFormState,
    toggleFavorite,
    deleteImage,
    // draft
    loadDraft,
    saveDraft,
    clearDraft,
  }
})
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task16.mjs`
Expected: `39 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 697ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build before Task 14 prints it too).
- [ ] **Step 6: Commit**
```bash
git add frontend/src/stores/t2i.ts
git commit -m "feat(t2i): T2I Pinia store" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (the store against the real backend, then a simulated batch)

With the backend and `npm run dev` running, open http://localhost:5173, press F12 and paste:
```js
const { useT2iStore } = await import('/src/stores/t2i.ts')
const { useWebSocket } = await import('/src/composables/useWebSocket.ts')
const store = useT2iStore()
// A component would do this in its setup. From the console Vue logs an
// "onUnmounted ... no active component instance" warning: expected, ignore it.
useWebSocket('t2i', (event, data) => store.handleT2iEvent(event, data))
useWebSocket('comfy', (event, data) => store.handleComfyEvent(event, data))
window.t2iStore = store

await store.open()
console.log('models', store.config.models.map((m) => m.id))
console.log('filter columns', store.captionMeta?.columns.map((c) => c.key))
console.log('strip', store.images.length, 'images; more:', store.hasMoreImages, '; loaded:', store.imagesLoaded)
console.log('running batches', store.activeBatches.length, '; job chips', store.jobs.size)
```
Expect `models` with the four model ids `['krea2', 'qwen', 'sd', 'zimage']`, `filter columns` with the column keys, `strip N images; more: false; loaded: true` (`more` is true once you have more than 60 images) and `running batches 0 ; job chips 0` when nothing is generating. Vue logs two `onUnmounted is called when there is no active component instance` warnings for the two `useWebSocket` calls; they are expected here, because a component would make those calls in its setup.

Now feed the store the frames a Random batch would produce (no server involved):
```js
const s = window.t2iStore
s.handleT2iEvent('batch_started', { batch_id: 'demo1', mode: 'random', total_steps: 3, total_images: 6 })
s.handleT2iEvent('batch_step', { batch_id: 'demo1', step: 1, total_steps: 3, caption: 'c', aspect_ratio: '3:2', seed: 77, prompt: 'the live prompt', negative: null, warnings: [] })
console.log(s.statusText, '|', s.currentStep.prompt, '|', s.randomBatch.batch_id)
s.handleT2iEvent('batch_progress', { batch_id: 'demo1', phase: 'rendering', images_done: 2, images_failed: 0, images_total: 6, next_seed: 500 })
console.log(s.statusText)
s.handleT2iEvent('batch_complete', { batch_id: 'demo1', images_done: 6, images_failed: 0 })
console.log(s.activeBatches.length, s.lastFinished.outcome, s.lastFinished.next_seed, s.currentStep)
```
The three lines read `Batch 1/3 · writing prompts | the live prompt | demo1`, then `Batch 1/3 · 2/6 images · rendering`, then `0 complete 500 null`. The last frame also refetches the strip and the job lists at once and reloads the library grid once, about 1.5 s later (all ordinary GETs); `window.t2iStore` stays available until you reload the page.

Optional live run (needs a running ComfyUI and a registered `t2i` workflow such as `Krea2 T2I API`; it creates two real images in your library):
```js
const s = window.t2iStore
const { listPresets } = await import('/src/api/comfy.ts')
const preset = (await listPresets()).find((p) => p.kind === 't2i')
const res = await s.startBatch({ mode: 'manual', model: 'krea2', preset_id: preset.id, megapixels: 0.5, seed: 12345, seed_policy: 'increment', count_per_batch: 2, prompt: 'A red kite over a quiet beach at dawn, soft light.', aspect_ratio: '3:2' })
console.log(res)
```
`res` is `{batch_id, total_images: 2, warnings: [...], next_seed: 12347}`. While ComfyUI works, `s.activeBatches`, `s.statusText` and `[...s.jobs]` show the batch, its phase and one chip per unfinished job (with a percentage while sampling); `s.images.length` grows by two as the images land, and `s.lastFinished.outcome` becomes `'complete'`. `await s.cancelAll()` stops a run early.

## Phase 5 — Preset dialog, config tab, smart-folder rule

Independent of the dialog; Task 19 adds the store function the dialog's delete handler calls.

### Task 17: PresetRegistrationDialog `kind` prop

**Files:**
- Modify: `frontend/src/components/storyboard/PresetRegistrationDialog.vue` (new `kind` prop, default `'ref2v'`; with `kind="t2i"` the dialog hides the video target/mode selects and the video placeholder, lists only t2i presets, and validates and registers as an untagged `t2i` preset)
- Test: `frontend/src/components/storyboard/kindProp.check.ts` (throwaway type-level check: created in Step 1, deleted in Step 4, never committed. The frontend has no test runner, so the compiler is the test.)

**Interfaces:**
- Consumes: nothing from Task 14. Existing, unchanged: `listPresets`, `createPreset`, `updatePreset`, `getPreset`, `deletePreset`, `validatePreset` (`frontend/src/api/comfy.ts`; `createPreset` and `validatePreset` already accept `kind: 't2i' | 'ref' | 'ref2v'`), `WorkflowPreset` (`frontend/src/types/storyboard.ts`).
- Produces: `PresetRegistrationDialog` props `{ initialMode?: string; kind?: 'ref2v' | 't2i' }` (defaults `'ref2va'` and `'ref2v'`); emits unchanged (`close`, `registered`). With `kind="t2i"`: no video target/mode row, name placeholder `e.g. Krea2 T2I API`, "Existing presets" shows only `kind === 't2i'`, validate and create send `kind: 't2i'` with `video_target: null, video_mode: null`, and update mode (click a row, `PUT` the workflow only) uses the row's stored kind.

**Why the default behaviour is unchanged (proof).** Both existing callers, read on this branch:
- `frontend/src/components/dialogs/ConfigI2VTab.vue:280-285`: `<PresetRegistrationDialog v-if="showRegister" initial-mode="i2va" @close="showRegister = false" @registered="onRegistered" />` passes `initial-mode` only.
- `frontend/src/components/storyboard/StoryboardLanding.vue:112`: `<PresetRegistrationDialog v-if="showPresets" @close="showPresets = false" />` passes nothing.

Neither passes `kind`, so `props.kind === 'ref2v'` and `isT2i === false`, and every changed expression evaluates to exactly what it did before:

| Before | After, with `kind = 'ref2v'` |
|---|---|
| `ref('minimax')`, `ref(props.initialMode)` | `ref(defaultTarget.value)` is `'minimax'`; `ref(defaultMode.value)` is `props.initialMode` |
| `resetForm()`: `'minimax'`, `props.initialMode` | `defaultTarget.value`, `defaultMode.value`: same values, read at the same moment |
| `ref<WorkflowPreset['kind']>('ref2v')`; reset to `'ref2v'` | `props.kind`, which is `'ref2v'` |
| validate: `kind: isUpdating ? editingKind : 'ref2v'` | `: props.kind`, which is `'ref2v'` |
| create: `kind: 'ref2v'` | `kind: props.kind`, which is `'ref2v'` |
| `presets.length === 0`, `v-for="p in presets"` | `listedPresets`, which is `presets.value` itself (no filter, so t2i/ref/ref2v rows all still show) |
| `<div class="field-row">` | `v-if="!isT2i"` is true: same node, no placeholder comment |
| `placeholder="e.g. H3 ref2v"` | `:placeholder="isT2i ? … : 'e.g. H3 ref2v'"`: the same string |

Cross-checked while drafting: the base-commit component and this one were mounted in a scratch DOM harness (happy-dom, mocked `fetch`) through the same scripted session (open, select a ref2v row, validate, deselect, select and update a t2i row, validate and register with edited tags, delete a row, close) for both caller shapes. The nine DOM snapshots, the 12-request log and the emitted events were identical. A deliberately broken default (always filtering to t2i) made that run fail, so the comparison is not vacuous. The `kind="t2i"` behaviour listed above was asserted in the same harness (18 checks).

- [ ] **Step 1: Write the failing check** (`frontend/src/components/storyboard/kindProp.check.ts`)
```ts
import type PresetRegistrationDialog from './PresetRegistrationDialog.vue'

type Props = InstanceType<typeof PresetRegistrationDialog>['$props']

// The dialog must accept both kinds and nothing else.
export const t2i: Props['kind'] = 't2i'
export const ref2v: Props['kind'] = 'ref2v'
// @ts-expect-error -- 'ref' is a valid preset kind but not one this dialog registers
export const bad: Props['kind'] = 'ref'
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && npx vue-tsc -b`
Expected: FAIL — `src/components/storyboard/kindProp.check.ts(6,25): error TS2339: Property 'kind' does not exist on type '{ readonly initialMode?: string | undefined; readonly onClose?: (() => any) | undefined; readonly onRegistered?: (() => any) | undefined; } & VNodeProps & AllowedComponentProps & ComponentCustomProps'.` (a second error of the same kind follows for line 7; exit code 2).
- [ ] **Step 3: Implement** (`frontend/src/components/storyboard/PresetRegistrationDialog.vue`)
Modified file. Apply with `git apply --whitespace=nowarn <<'PATCH'`, pasting the diff below as the heredoc body and ending with a line `PATCH`.

```diff
diff --git a/frontend/src/components/storyboard/PresetRegistrationDialog.vue b/frontend/src/components/storyboard/PresetRegistrationDialog.vue
index 072d79b..5c6ce74 100644
--- a/frontend/src/components/storyboard/PresetRegistrationDialog.vue
+++ b/frontend/src/components/storyboard/PresetRegistrationDialog.vue
@@ -14,9 +14,17 @@ import { VIDEO_MODES, presetTag } from '../../types/storyboard'
 import type { WorkflowPreset } from '../../types/storyboard'
 import TextEditPopup from './TextEditPopup.vue'
 
-const props = withDefaults(defineProps<{ initialMode?: string }>(), {
-  initialMode: 'ref2va',
-})
+// `kind` is the preset kind this dialog registers: 'ref2v' (video workflows —
+// the storyboard and i2v callers, and the default) or 't2i' (the T2I config
+// tab). A t2i preset is never tagged with a video dialect.
+const props = withDefaults(
+  defineProps<{ initialMode?: string; kind?: 'ref2v' | 't2i' }>(),
+  {
+    initialMode: 'ref2va',
+    kind: 'ref2v',
+  },
+)
+const isT2i = computed(() => props.kind === 't2i')
 
 const emit = defineEmits<{
   close: []
@@ -28,8 +36,10 @@ const workflowText = ref('')
 // Dialect association (workflow_presets.video_target/video_mode) — drives
 // target-specific validation and the generate_video mismatch guard.
 // "" = untagged (legacy behavior, generic validation only).
-const videoTarget = ref('minimax')
-const videoMode = ref(props.initialMode)
+const defaultTarget = computed(() => (isT2i.value ? '' : 'minimax'))
+const defaultMode = computed(() => (isT2i.value ? '' : props.initialMode))
+const videoTarget = ref(defaultTarget.value)
+const videoMode = ref(defaultMode.value)
 
 // ---- update mode ----------------------------------------------------------
 // Selecting a preset in the list below loads it into the form for an
@@ -40,8 +50,8 @@ const videoMode = ref(props.initialMode)
 // keep pointing at it.
 const editingId = ref<number | null>(null)
 // Validation must run against the preset's own kind (legacy t2i/ref
-// presets can be updated too), not the 'ref2v' new registrations use.
-const editingKind = ref<WorkflowPreset['kind']>('ref2v')
+// presets can be updated too), not the `kind` new registrations use.
+const editingKind = ref<WorkflowPreset['kind']>(props.kind)
 const loadingPreset = ref(false)
 const savedNotice = ref<string | null>(null)
 const isUpdating = computed(() => editingId.value !== null)
@@ -82,7 +92,7 @@ async function onValidate(): Promise<void> {
   validating.value = true
   try {
     validation.value = await validatePreset({
-      kind: isUpdating.value ? editingKind.value : 'ref2v',
+      kind: isUpdating.value ? editingKind.value : props.kind,
       workflow,
       video_target: videoTarget.value || null,
       video_mode: videoMode.value || null,
@@ -104,6 +114,11 @@ async function onApplyFixes(): Promise<void> {
 }
 
 const presets = ref<WorkflowPreset[]>([])
+// The t2i dialog lists only t2i presets; the video callers keep seeing every
+// kind, exactly as before.
+const listedPresets = computed(() =>
+  isT2i.value ? presets.value.filter((p) => p.kind === 't2i') : presets.value,
+)
 const presetsLoading = ref(true)
 const deleteError = ref<string | null>(null)
 
@@ -132,10 +147,10 @@ async function onFileChange(e: Event) {
 function resetForm() {
   selectSeq++ // orphan any preset load still in flight
   editingId.value = null
-  editingKind.value = 'ref2v'
+  editingKind.value = props.kind
   name.value = ''
-  videoTarget.value = 'minimax'
-  videoMode.value = props.initialMode
+  videoTarget.value = defaultTarget.value
+  videoMode.value = defaultMode.value
   workflowText.value = ''
   jsonError.value = null
   submitError.value = null
@@ -199,11 +214,11 @@ async function submit() {
       await refreshPresets()
       return
     }
-    // The storyboard UX is video-only, so registration is fixed to the
-    // ref2v (video workflow) kind.
+    // Registration follows the `kind` prop: ref2v (video workflow) for the
+    // storyboard/i2v callers, t2i for the T2I config tab (untagged).
     const res = await createPreset({
       name: trimmedName,
-      kind: 'ref2v',
+      kind: props.kind,
       workflow,
       video_target: videoTarget.value || null,
       video_mode: videoMode.value || null,
@@ -267,11 +282,16 @@ function close() {
         <!-- Locked while updating: no edit popup, just the value. -->
         <InputText v-if="isUpdating" id="preset-name" :model-value="name" disabled />
         <TextEditPopup v-else title="Name" :value="name" @save="name = $event">
-          <InputText id="preset-name" v-model="name" placeholder="e.g. H3 ref2v" />
+          <InputText
+            id="preset-name"
+            v-model="name"
+            :placeholder="isT2i ? 'e.g. Krea2 T2I API' : 'e.g. H3 ref2v'"
+          />
         </TextEditPopup>
       </div>
 
-      <div class="field-row">
+      <!-- Video dialect tag: not applicable to a t2i preset. -->
+      <div v-if="!isT2i" class="field-row">
         <div class="field">
           <label for="preset-target">Video model</label>
           <select id="preset-target" v-model="videoTarget" :disabled="isUpdating">
@@ -354,10 +374,10 @@ function close() {
 
       <h4 class="presets-heading">Existing presets</h4>
       <div v-if="presetsLoading" class="muted">Loading…</div>
-      <div v-else-if="presets.length === 0" class="muted">No presets registered yet.</div>
+      <div v-else-if="listedPresets.length === 0" class="muted">No presets registered yet.</div>
       <div v-else class="preset-list">
         <div
-          v-for="p in presets"
+          v-for="p in listedPresets"
           :key="p.id"
           class="preset-row"
           :class="{ selected: editingId === p.id }"
```
- [ ] **Step 4: Run it and confirm it passes, then remove the check and run the build gate**
Run: `cd frontend && npx vue-tsc -b && rm src/components/storyboard/kindProp.check.ts && npm run build`
Expected: `vue-tsc` prints nothing (exit 0, so the type check passes and the `@ts-expect-error` line is satisfied because `'ref'` is rejected); then the build ends with `✓ 519 modules transformed.` and `✓ built in …ms` (on the reference branch; the module count grows as later tasks land). Make sure `git status --short` no longer lists `kindProp.check.ts`.
- [ ] **Step 5: Manual check** (two terminals: `source venv/bin/activate && python run_server.py`, and `cd frontend && npm run dev`; open http://localhost:5173)
1. Default, storyboard caller. Click the header button with the tooltip "Storyboards", then "Workflow presets…". You must see: Name placeholder `e.g. H3 ref2v`; a "Video model" select (preset to MiniMax H3) and a "Generation mode" select (preset to `ref2va`); "Existing presets" listing every registered preset of every kind, each row's grey line starting with its kind (`t2i`, `ref`, `ref2v`). This is what the dialog showed before this task.
2. Default, config caller. Back on the library, click the cog ("Configuration"), open the "Video" tab, click "Register workflow…". Same dialog, with "Generation mode" preset to `i2va`.
3. The t2i variant (temporary edit, reverted at the end). In `frontend/src/components/storyboard/StoryboardLanding.vue` line 112 add `kind="t2i"`: `<PresetRegistrationDialog v-if="showPresets" kind="t2i" @close="showPresets = false" />`. On the Storyboards page click "Workflow presets…". You must see: no "Video model" / "Generation mode" row; Name placeholder `e.g. Krea2 T2I API`; "Existing presets" listing only `t2i` rows. Paste any JSON object into "Workflow JSON" and press Validate: in DevTools, Network, the `POST /api/comfy/presets/validate` body contains `"kind":"t2i"`, `"video_target":null`, `"video_mode":null`. Click a preset row: the name locks and the "updating" chip appears; Validate again and the body still says `"kind":"t2i"`; click the same row again and the form returns to blank. Revert the temporary edit with `git checkout -- frontend/src/components/storyboard/StoryboardLanding.vue`.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/storyboard/PresetRegistrationDialog.vue
git commit -m "feat(t2i): PresetRegistrationDialog kind prop (ref2v | t2i)" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 18: ConfigT2ITab and the Text to Image config tab

**Files:**
- Create: `frontend/src/utils/t2iConfigForm.ts` (pure helpers: size-list parsing, workflow-select options, the spread-merge)
- Create: `frontend/src/components/dialogs/ConfigT2ITab.vue`
- Modify: `frontend/src/components/dialogs/ConfigDialog.vue` (new `'t2i'` `TabKey`, a "Text to Image" nav button after "Video", its panel with a Close button; mounted the way the `i2v` tab is)
- Test: `/tmp/t2i_config_form.check.mts` (throwaway `node:test` check of the helpers, outside the repo, never committed. Needs Node 22.18 or newer, where TypeScript type stripping is on by default; on an older 22.x add `--experimental-strip-types`.)

**Interfaces:**
- Consumes (Task 14; scratch stubs, F1a's real modules must export exactly these): `fetchT2iConfig(): Promise<T2iConfig>` and `t2iOutputPreview(root: string, prefix: string): Promise<T2iOutputPreview>` from `frontend/src/api/t2i.ts`; types `T2iConfig` and `T2iOutputPreview` (`{ path: string | null; error: string | null; warnings: string[] }`) from `frontend/src/types/t2i.ts`. The tab reads `output_root`, `output_prefix`, `megapixels`, `default_megapixels`, `default_model`, `model_workflows`, `content_mode` and `models[{ id, label, … }]` from `T2iConfig`.
- Consumes (existing): `fetchConfig(): Promise<Record<string, unknown>>` and `updateConfig(updates: Record<string, unknown>)` from `frontend/src/api/config.ts` (`PUT /api/config` is a shallow top-level merge: `current.update(body)`), `listPresets(): Promise<WorkflowPreset[]>` from `frontend/src/api/comfy.ts`, `DirectoryPicker.vue` (props `initialPath`, `title`; emits `select(path)`, `close`), `PresetRegistrationDialog` with `kind="t2i"` (Task 17; emits `close`, `registered`).
- Produces: `ConfigT2ITab.vue` (no props, no emits); `ConfigDialog` tab key `'t2i'`; `frontend/src/utils/t2iConfigForm.ts` exporting `T2iContentMode`, `CONTENT_MODE_OPTIONS`, `parseMegapixels(text: string): number[]`, `PresetChoice`, `presetChoices(presets, currentId: number | null): PresetChoice[]`, `T2iFormValues`, `rawSection(v: unknown): Record<string, unknown>`, `buildT2iSection(raw, form: T2iFormValues): Record<string, unknown>`. It writes the `t2i` section of `config.json` (keys `output_root`, `output_prefix`, `megapixels`, `default_megapixels`, `default_model`, `content_mode`, `model_workflows`; every other key already there is kept).

**Design notes (each is deliberate):**
- The form is filled from `GET /api/t2i/config` (already defaulted and sanitised by `get_t2i_config`), so no defaults are duplicated here except `DEFAULT_OUTPUT_PREFIX = '/%Y-%m-%d/t2i_'`, which mirrors `T2I_DEFAULT_OUTPUT_PREFIX` in `backend/config.py` and is the prefix input's placeholder. The raw section from `GET /api/config` is kept only for the spread-merge.
- Save follows the `ConfigI2VTab.vue` pattern (raw section spread first, then the edited keys). `model_workflows` is merged per model, so an entry for a model this tab does not list survives too.
- The prefix input is never disabled. Spec sections 6.4 and 10 define an empty root as `<comfy.output_root>/t2i`, a root like any other, so the prefix still applies beneath it; the video tab's "an empty root ignores the prefix" does not carry over. The tab shows whatever `GET /api/t2i/output-preview` returns (`path`, `error`, `warnings`), so it works whether or not the server resolves a preview for a blank root (the i2v route returns none).
- After a workflow is registered the tab only re-lists; it does not close the registration dialog (the video tab does). Registering a workflow without `MS_NEGATIVE` or `MS_LORA_STACK` returns warnings that the dialog shows after Save, and closing it at once would hide them.
- The default size snaps into the size list on `change` (blur/Enter), not per keystroke, so typing `20` on the way to `2` does not lose the default. `buildT2iSection` also clamps at save time.
- If `GET /api/t2i/config` fails the tab shows the error with a Retry button instead of a blank form.

Cross-checked while drafting: the real tab and `ConfigDialog` were mounted in a scratch DOM harness (happy-dom, mocked `fetch`) and 51 assertions held (load and field population, one workflow select per model with a dangling id kept visible, the 250 ms debounce and the stale-reply guard, the exact `PUT /api/config` body, the guards and failure paths, the register flow, the new tab in the dialog); six deliberate mutants (stale-reply guard removed, no snap on `change`, unguarded raw section, no merge of `model_workflows`, dialog closing on `registered`, a drifted `DEFAULT_OUTPUT_PREFIX`) each failed it. The `PUT` body was also round-tripped through the real `PUT /api/config` route: keys the tab does not edit survived, while a body of only the edited keys left just `{"output_root": "/x"}`, which is why the raw section is spread.

- [ ] **Step 1: Write the failing helper check** (`/tmp/t2i_config_form.check.mts`)
```ts
// Throwaway check for src/utils/t2iConfigForm.ts -- not committed.
// Run from the frontend/ directory:  node --test-reporter=tap /tmp/t2i_config_form.check.mts
import test from 'node:test'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'

const mod = await import(pathToFileURL(resolve('src/utils/t2iConfigForm.ts')).href)
const { CONTENT_MODE_OPTIONS, buildT2iSection, parseMegapixels, presetChoices, rawSection } = mod

test('parseMegapixels keeps positive numbers in typed order', () => {
  assert.deepEqual(parseMegapixels('0.5, 1, 1.5, 2'), [0.5, 1, 1.5, 2])
  assert.deepEqual(parseMegapixels('2,1'), [2, 1])
})

test('parseMegapixels drops junk, non-positive and duplicate entries', () => {
  assert.deepEqual(parseMegapixels(' 1 ,, 2 , x, -3, 0, 2 , 1.0'), [1, 2])
  assert.deepEqual(parseMegapixels(''), [])
  assert.deepEqual(parseMegapixels(' , ,'), [])
  assert.deepEqual(parseMegapixels('1e-1'), [0.1])
})

test('presetChoices lists the presets in order', () => {
  const presets = [
    { id: 4, name: 'Krea2 T2I API' },
    { id: 9, name: 'Qwen T2I' },
  ]
  assert.deepEqual(presetChoices(presets, 9), [
    { id: 4, label: 'Krea2 T2I API' },
    { id: 9, label: 'Qwen T2I' },
  ])
  assert.equal(presetChoices(presets, null).length, 2)
  assert.deepEqual(presetChoices([], null), [])
})

test('presetChoices keeps a dangling selection visible instead of blank', () => {
  const out = presetChoices([{ id: 4, name: 'Krea2 T2I API' }], 12)
  assert.deepEqual(out, [
    { id: 4, label: 'Krea2 T2I API' },
    { id: 12, label: '#12 (not found)' },
  ])
})

test('CONTENT_MODE_OPTIONS covers exactly the three backend modes, uncensored first', () => {
  assert.deepEqual(
    CONTENT_MODE_OPTIONS.map(([value]: [string, string]) => value),
    ['uncensored', 'sfw', 'default'],
  )
})

const form = {
  outputRoot: '  /data/t2i  ',
  outputPrefix: ' /%Y-%m-%d/t2i_ ',
  megapixels: [0.5, 1, 2],
  defaultMegapixels: 2,
  defaultModel: 'qwen',
  contentMode: 'sfw',
  modelWorkflows: { krea2: 4, qwen: 9, sd: null, zimage: null },
}

test('buildT2iSection sets every edited key (strings trimmed)', () => {
  assert.deepEqual(buildT2iSection({}, form), {
    output_root: '/data/t2i',
    output_prefix: '/%Y-%m-%d/t2i_',
    megapixels: [0.5, 1, 2],
    default_megapixels: 2,
    default_model: 'qwen',
    content_mode: 'sfw',
    model_workflows: { krea2: 4, qwen: 9, sd: null, zimage: null },
  })
})

test('buildT2iSection keeps keys the tab does not edit (spread-merge)', () => {
  const raw = {
    window: 8,
    max_batch_size: 100,
    max_count_per_batch: 16,
    identity: { sd: 'name' },
    some_future_key: [1, 2, 3],
    output_root: '/old',
    model_workflows: { krea2: 1, legacy_model: 77 },
  }
  const before = structuredClone(raw)
  const out = buildT2iSection(raw, form)
  assert.equal(out.window, 8)
  assert.equal(out.max_batch_size, 100)
  assert.equal(out.max_count_per_batch, 16)
  assert.deepEqual(out.identity, { sd: 'name' })
  assert.deepEqual(out.some_future_key, [1, 2, 3])
  assert.equal(out.output_root, '/data/t2i', 'edited keys win over the raw copy')
  // Per-model entries are merged, not replaced: an unknown model survives.
  assert.deepEqual(out.model_workflows, { krea2: 4, legacy_model: 77, qwen: 9, sd: null, zimage: null })
  assert.deepEqual(raw, before, 'the raw section is not mutated')
})

test('buildT2iSection moves a default size that left the list to the first entry', () => {
  const out = buildT2iSection({}, { ...form, megapixels: [0.5, 1], defaultMegapixels: 2 })
  assert.equal(out.default_megapixels, 0.5)
})

test('buildT2iSection preserves an explicit empty prefix and ignores a malformed model_workflows', () => {
  assert.equal(buildT2iSection({}, { ...form, outputPrefix: '' }).output_prefix, '')
  for (const junk of [null, [1, 2], 'x', 7]) {
    const out = buildT2iSection({ model_workflows: junk }, form)
    assert.deepEqual(out.model_workflows, form.modelWorkflows)
  }
})

test('rawSection returns an object section as is and {} for anything else', () => {
  const section = { window: 8 }
  assert.equal(rawSection(section), section)
  for (const junk of [undefined, null, 'oops', 7, [1, 2], true]) {
    assert.deepEqual(rawSection(junk), {})
  }
})
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node --test-reporter=tap /tmp/t2i_config_form.check.mts`
Expected: FAIL — `Error [ERR_MODULE_NOT_FOUND]: Cannot find module '<repo>/frontend/src/utils/t2iConfigForm.ts' imported from /tmp/t2i_config_form.check.mts` (`<repo>` is your checkout; exit code 1).
- [ ] **Step 3: Implement the helpers** (`frontend/src/utils/t2iConfigForm.ts`)
```ts
import type { T2iConfig } from '../types/t2i'

// Pure helpers for the Text to Image config tab (ConfigT2ITab.vue), kept out
// of the component so the parsing and the spread-merge can be reasoned about
// (and checked) without mounting anything.

export type T2iContentMode = T2iConfig['content_mode']

// [value, label] for the content-mode select, in display order. The values
// are exactly the backend's CONTENT_MODES.
export const CONTENT_MODE_OPTIONS: ReadonlyArray<readonly [T2iContentMode, string]> = [
  ['uncensored', 'Uncensored'],
  ['sfw', 'Safe for work'],
  ['default', 'Model default (no directive)'],
]

/** "0.5, 1, 1.5" -> [0.5, 1, 1.5]: positive finite numbers only, duplicates
 * dropped, in the order typed. Junk entries are skipped, not errors. */
export function parseMegapixels(text: string): number[] {
  const out: number[] = []
  for (const part of text.split(',')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const n = Number(trimmed)
    if (Number.isFinite(n) && n > 0 && !out.includes(n)) out.push(n)
  }
  return out
}

export interface PresetChoice {
  id: number
  label: string
}

/** Options for a "default workflow" select: the registered presets in order,
 * plus the current selection when it no longer exists (a deleted preset), so
 * the select never shows blank while quietly still holding a dangling id. */
export function presetChoices(
  presets: ReadonlyArray<{ id: number; name: string }>,
  currentId: number | null,
): PresetChoice[] {
  const out = presets.map((p) => ({ id: p.id, label: p.name }))
  if (currentId !== null && !presets.some((p) => p.id === currentId)) {
    out.push({ id: currentId, label: `#${currentId} (not found)` })
  }
  return out
}

export interface T2iFormValues {
  outputRoot: string
  outputPrefix: string
  /** Already parsed by parseMegapixels; the caller guarantees it is non-empty. */
  megapixels: number[]
  defaultMegapixels: number
  defaultModel: string
  contentMode: T2iContentMode
  /** model id -> default workflow preset id (null = none). */
  modelWorkflows: Record<string, number | null>
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** The raw `t2i` section of GET /api/config as an object: `{}` when it is
 * absent or malformed, so spreading it can never smear a string or array's
 * indices into the saved config. */
export function rawSection(v: unknown): Record<string, unknown> {
  return isPlainObject(v) ? v : {}
}

/** The `t2i` section to PUT. PUT /api/config is a shallow top-level merge, so
 * the raw section is spread first: keys this tab does not edit (window,
 * max_batch_size, max_count_per_batch, identity, ...) survive the save, and
 * so do per-model workflow entries for models the tab does not list. */
export function buildT2iSection(
  raw: Record<string, unknown>,
  f: T2iFormValues,
): Record<string, unknown> {
  return {
    ...raw,
    output_root: f.outputRoot.trim(),
    output_prefix: f.outputPrefix.trim(),
    megapixels: f.megapixels,
    default_megapixels: f.megapixels.includes(f.defaultMegapixels)
      ? f.defaultMegapixels
      : f.megapixels[0],
    default_model: f.defaultModel,
    content_mode: f.contentMode,
    model_workflows: {
      ...(isPlainObject(raw.model_workflows) ? raw.model_workflows : {}),
      ...f.modelWorkflows,
    },
  }
}
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node --test-reporter=tap /tmp/t2i_config_form.check.mts && npx vue-tsc -b`
Expected (the summary at the end of the TAP output):
```text
# tests 10
# pass 10
# fail 0
```
followed by no `vue-tsc` output (exit 0).
- [ ] **Step 5: Wire the dialog first and confirm the type check fails** (`frontend/src/components/dialogs/ConfigDialog.vue`)
Modified file. Apply with `git apply --whitespace=nowarn <<'PATCH'`, pasting the diff below as the heredoc body and ending with a line `PATCH`.

```diff
diff --git a/frontend/src/components/dialogs/ConfigDialog.vue b/frontend/src/components/dialogs/ConfigDialog.vue
index 0f2ebce..a26d7ed 100644
--- a/frontend/src/components/dialogs/ConfigDialog.vue
+++ b/frontend/src/components/dialogs/ConfigDialog.vue
@@ -3,6 +3,7 @@ import { ref, onMounted } from 'vue'
 import { fetchConfig, updateConfig } from '../../api/config'
 import ConfigModelsTab from './ConfigModelsTab.vue'
 import ConfigI2VTab from './ConfigI2VTab.vue'
+import ConfigT2ITab from './ConfigT2ITab.vue'
 
 const emit = defineEmits<{
   close: []
@@ -13,7 +14,7 @@ interface DirEntry {
   search_subfolders: boolean
 }
 
-type TabKey = 'directories' | 'models' | 'i2v'
+type TabKey = 'directories' | 'models' | 'i2v' | 't2i'
 
 const activeTab = ref<TabKey>('directories')
 
@@ -89,6 +90,13 @@ async function saveDirectories() {
         >
           Video
         </button>
+        <button
+          class="tab"
+          :class="{ active: activeTab === 't2i' }"
+          @click="activeTab = 't2i'"
+        >
+          Text to Image
+        </button>
       </nav>
 
       <div v-if="activeTab === 'directories'" class="tab-panel">
@@ -166,6 +174,13 @@ async function saveDirectories() {
           <button class="btn-secondary" @click="emit('close')">Close</button>
         </div>
       </div>
+
+      <div v-else-if="activeTab === 't2i'" class="tab-panel">
+        <ConfigT2ITab />
+        <div class="dialog-actions">
+          <button class="btn-secondary" @click="emit('close')">Close</button>
+        </div>
+      </div>
     </div>
   </div>
 </template>
```
Run: `cd frontend && npx vue-tsc -b`
Expected: FAIL — `src/components/dialogs/ConfigDialog.vue(6,26): error TS2307: Cannot find module './ConfigT2ITab.vue' or its corresponding type declarations.`
- [ ] **Step 6: Create the tab and run the build gate** (`frontend/src/components/dialogs/ConfigT2ITab.vue`)
```vue
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchConfig, updateConfig } from '../../api/config'
import { listPresets } from '../../api/comfy'
import { fetchT2iConfig, t2iOutputPreview } from '../../api/t2i'
import type { T2iConfig, T2iOutputPreview } from '../../types/t2i'
import type { WorkflowPreset } from '../../types/storyboard'
import {
  CONTENT_MODE_OPTIONS,
  buildT2iSection,
  parseMegapixels,
  presetChoices,
  rawSection,
  type T2iContentMode,
} from '../../utils/t2iConfigForm'
import DirectoryPicker from './DirectoryPicker.vue'
import PresetRegistrationDialog from '../storyboard/PresetRegistrationDialog.vue'

const loading = ref(true)
const loadError = ref<string | null>(null)
const saveError = ref<string | null>(null)
// Keep the raw section: PUT /api/config is a shallow top-level merge, so we
// spread-merge to avoid clobbering keys this tab does not edit (window, the
// batch limits, identity, ...). The form itself is filled from the effective
// config (GET /api/t2i/config), which the server has already defaulted and
// sanitised.
const t2iRaw = ref<Record<string, unknown>>({})
const models = ref<T2iConfig['models']>([])
const modelWorkflows = ref<Record<string, number | null>>({})
const defaultModel = ref('')
const megapixelsText = ref('')
const defaultMegapixels = ref(1)
const contentMode = ref<T2iContentMode>('uncensored')
// Must match backend/config.py::T2I_DEFAULT_OUTPUT_PREFIX.
const DEFAULT_OUTPUT_PREFIX = '/%Y-%m-%d/t2i_'
const outputRoot = ref('')
const outputPrefix = ref(DEFAULT_OUTPUT_PREFIX)
const showPicker = ref(false)
const preview = ref<T2iOutputPreview>({ path: null, error: null, warnings: [] })
const presets = ref<WorkflowPreset[]>([])
const showRegister = ref(false)
const saved = ref(false)

// The presets a T2I model can render through.
const t2iPresets = computed(() => presets.value.filter((p) => p.kind === 't2i'))

const parsedMegapixels = computed(() => parseMegapixels(megapixelsText.value))
const canSave = computed(() => parsedMegapixels.value.length > 0 && !!defaultModel.value)

// A finished edit of the size list (blur / Enter, not every keystroke) can
// drop the current default out of it; move it to the first entry then.
function syncDefaultSize() {
  const list = parsedMegapixels.value
  if (list.length && !list.includes(defaultMegapixels.value)) defaultMegapixels.value = list[0]
}

onMounted(load)

// Live "would be saved as" preview, resolved by the server so the date
// expansion and path rules have exactly one implementation. Debounced:
// it fires on every keystroke in the prefix box.
let previewTimer: ReturnType<typeof setTimeout> | null = null
let previewSeq = 0
async function refreshPreview() {
  const seq = ++previewSeq
  try {
    const res = await t2iOutputPreview(outputRoot.value, outputPrefix.value)
    if (seq === previewSeq) preview.value = res // drop out-of-order replies
  } catch (e) {
    if (seq === previewSeq) {
      preview.value = {
        path: null,
        error: e instanceof Error ? e.message : String(e),
        warnings: [],
      }
    }
  }
}
watch([outputRoot, outputPrefix], () => {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 250)
})
onBeforeUnmount(() => {
  if (previewTimer) clearTimeout(previewTimer)
})

function onPickRoot(path: string) {
  outputRoot.value = path
  showPicker.value = false
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [config, presetRows, cfg] = await Promise.all([
      fetchConfig(),
      listPresets(),
      fetchT2iConfig(),
    ])
    presets.value = presetRows
    t2iRaw.value = rawSection(config.t2i)
    models.value = cfg.models
    modelWorkflows.value = Object.fromEntries(
      cfg.models.map((m) => [m.id, cfg.model_workflows[m.id] ?? null]),
    )
    defaultModel.value = cfg.default_model
    megapixelsText.value = cfg.megapixels.join(', ')
    defaultMegapixels.value = cfg.default_megapixels
    contentMode.value = cfg.content_mode
    outputRoot.value = cfg.output_root
    // An explicit '' is a saved choice; only a missing value gets the default.
    outputPrefix.value =
      typeof cfg.output_prefix === 'string' ? cfg.output_prefix : DEFAULT_OUTPUT_PREFIX
    void refreshPreview()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function save() {
  saveError.value = null
  const t2i = buildT2iSection(t2iRaw.value, {
    outputRoot: outputRoot.value,
    outputPrefix: outputPrefix.value,
    megapixels: parsedMegapixels.value,
    defaultMegapixels: defaultMegapixels.value,
    defaultModel: defaultModel.value,
    contentMode: contentMode.value,
    modelWorkflows: modelWorkflows.value,
  })
  try {
    await updateConfig({ t2i })
    t2iRaw.value = t2i
    saved.value = true
    setTimeout(() => (saved.value = false), 1500)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e)
  }
}

// Only re-list: unlike the video tab this leaves the registration dialog
// open, so the "Saved with N warnings" notice (a workflow without
// MS_NEGATIVE / MS_LORA_STACK warns) stays readable. Close is its own button.
async function onRegistered() {
  presets.value = await listPresets()
}
</script>

<template>
  <div class="t2i-tab">
    <div v-if="loading" class="muted">Loading...</div>
    <template v-else-if="loadError">
      <p class="warn">⚠ Could not load the text-to-image settings: {{ loadError }}</p>
      <button type="button" class="btn" @click="load">Retry</button>
    </template>
    <template v-else>
      <h4>Output files</h4>
      <p class="muted">
        Where generated images are saved. Leave the directory empty to use the
        default location, a t2i folder under the ComfyUI output root.
      </p>
      <label class="row">
        <span>Root directory</span>
        <span class="root-row">
          <input
            v-model="outputRoot"
            spellcheck="false"
            placeholder="(default — t2i folder in the ComfyUI output root)"
          />
          <button type="button" class="btn" @click="showPicker = true">Browse…</button>
          <button
            type="button"
            class="btn"
            title="Back to the default location"
            :disabled="!outputRoot"
            @click="outputRoot = ''"
          >Clear</button>
        </span>
      </label>
      <label class="row">
        <span>File prefix</span>
        <input v-model="outputPrefix" spellcheck="false" :placeholder="DEFAULT_OUTPUT_PREFIX" />
      </label>
      <p class="muted hint">
        A path under the root; the last part is the file name prefix and a
        unique number is appended. Date tokens: <code>%Y</code> year,
        <code>%m</code> month, <code>%d</code> day, <code>%H</code> hour,
        <code>%M</code> minute, <code>%S</code> second.
      </p>
      <p v-for="(w, i) in preview.warnings" :key="i" class="warn">⚠ {{ w }}</p>
      <p v-if="preview.error" class="warn">⚠ {{ preview.error }}</p>
      <p v-else-if="preview.path" class="muted hint">
        Next image: <code class="preview-path">{{ preview.path }}</code>
      </p>

      <h4>Workflows</h4>
      <p class="muted">
        Each model renders through a registered text-to-image workflow. Pick
        the default for each model; the T2I dialog can still switch to any
        registered one.
      </p>
      <p v-if="t2iPresets.length" class="muted hint">
        Registered: {{ t2iPresets.map((p) => p.name).join(', ') }}
      </p>
      <p v-else class="muted hint">No text-to-image workflows registered yet.</p>
      <label v-for="m in models" :key="m.id" class="row">
        <span>{{ m.label }}</span>
        <select v-model="modelWorkflows[m.id]">
          <option :value="null">— none —</option>
          <option
            v-for="c in presetChoices(t2iPresets, modelWorkflows[m.id] ?? null)"
            :key="c.id"
            :value="c.id"
          >
            {{ c.label }}
          </option>
        </select>
      </label>
      <p class="muted hint">
        A workflow is an API-format ComfyUI graph with nodes titled MS_POSITIVE,
        MS_SEED, MS_LATENT and MS_SAVE. MS_NEGATIVE and MS_LORA_STACK are
        optional; without them the negative prompt and LoRAs do not apply.
      </p>
      <button type="button" class="btn" @click="showRegister = true">Register workflow…</button>

      <h4>Defaults</h4>
      <label class="row">
        <span>Default model</span>
        <select v-model="defaultModel">
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label }}</option>
        </select>
      </label>
      <label class="row">
        <span>Size choices (megapixels, comma-separated)</span>
        <input v-model="megapixelsText" @change="syncDefaultSize" />
      </label>
      <p v-if="!parsedMegapixels.length" class="warn">
        ⚠ Enter at least one size, for example 0.5, 1, 2.
      </p>
      <label class="row">
        <span>Default size</span>
        <select v-model.number="defaultMegapixels">
          <option v-for="m in parsedMegapixels" :key="m" :value="m">{{ m }} MP</option>
        </select>
      </label>
      <label class="row">
        <span>Prompt content</span>
        <select v-model="contentMode">
          <option v-for="[value, label] in CONTENT_MODE_OPTIONS" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </label>
      <p class="muted hint">
        How the prompt writer treats explicit content. Uncensored describes
        anything the caption states plainly, Safe for work keeps prompts clean,
        and Model default adds no directive.
      </p>

      <div class="actions">
        <button type="button" class="btn primary" :disabled="!canSave" @click="save">Save</button>
        <span v-if="saved" class="muted">Saved.</span>
        <span v-if="saveError" class="warn">⚠ {{ saveError }}</span>
      </div>
    </template>

    <DirectoryPicker
      v-if="showPicker"
      title="Root directory for generated images"
      :initial-path="outputRoot"
      @select="onPickRoot"
      @close="showPicker = false"
    />

    <PresetRegistrationDialog
      v-if="showRegister"
      kind="t2i"
      @close="showRegister = false"
      @registered="onRegistered"
    />
  </div>
</template>

<style scoped>
.t2i-tab { display: flex; flex-direction: column; gap: 10px; }
.t2i-tab h4 { margin: 12px 0 4px; font-size: 14px; color: var(--text-color); }
.t2i-tab h4:first-child { margin-top: 0; }
.row { display: flex; align-items: center; gap: 10px; }
.row > span { min-width: 220px; color: var(--text-color); }
.row select,
.row input {
  flex: 1;
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}
.root-row { flex: 1; display: flex; gap: 6px; min-width: 0; }
.root-row input { min-width: 0; font-family: monospace; font-size: 12px; }
.hint { margin: 0; font-size: 12px; }
.hint code, .preview-path { font-family: monospace; font-size: 12px; color: var(--text-color); }
.preview-path { word-break: break-all; }
.warn { margin: 0; font-size: 12px; color: var(--warn, #c90); }
.actions { margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.muted { color: var(--text-color-secondary, #888); font-size: 13px; }

.btn {
  align-self: flex-start;
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
}
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}
</style>
```
Run: `cd frontend && npm run build`
Expected: exit 0, ending `✓ 524 modules transformed.` and `✓ built in …ms` on the reference branch (the count varies with which other tasks' files are present).
- [ ] **Step 7: Manual check** (app running as in Task 17, Step 5)
1. Click the cog ("Configuration"). The tabs read Directories, Models, Video, Text to Image. Open "Text to Image".
2. Top to bottom you see: "Output files" (Root directory with Browse… and Clear, File prefix showing `/%Y-%m-%d/t2i_`, the date-token hint, and, when the server can resolve one, a line "Next image: … .png"); "Workflows" (a blurb, "Registered: …" or "No text-to-image workflows registered yet.", one select per model, the MS_* hint, "Register workflow…"); "Defaults" (Default model, Size choices `0.5, 1, 1.5, 2`, Default size, Prompt content, a blurb); Save. The model rows and their labels come from `GET /api/t2i/config`.
3. Click Browse…: the server-side directory picker opens above the dialog. Pick a folder: Root directory fills in and the "Next image:" line follows. Clear empties it again.
4. Live preview: click into File prefix, type one more character and stop. About a quarter of a second later the "Next image:" path changes. In DevTools, Network shows one `GET /api/t2i/output-preview?root=…&prefix=…` per pause, not one per keystroke. (With a blank root the line may be absent, depending on what the server returns; set a root first.)
5. Click "Register workflow…". The dialog has no "Video model" / "Generation mode" row, the Name placeholder is `e.g. Krea2 T2I API`, and "Existing presets" lists only t2i rows. Load a t2i API-format workflow (click the existing one to load its JSON, or paste one whose nodes are titled MS_POSITIVE, MS_SEED, MS_LATENT, MS_SAVE), give it a new name, press Validate (warnings such as `no_lora_stack` / `no_negative` appear when those titles are missing), then Save. "Saved “…”." and the "Saved with N warnings" line stay readable and the dialog stays open. Press Close: the new workflow is now offered in every model's select.
6. Pick a workflow for Krea 2 and SDXL, type `0.5, 1, 2` in Size choices and press Tab, set Default size to `2 MP` and Prompt content to "Safe for work", press Save. "Saved." appears next to the button.
7. Check the merge: run `python -c "import json; print(json.dumps(json.load(open('config.json'))['t2i'], indent=1))"`. The seven edited keys hold your values and every other key that was already in that section (for example `window`, `max_batch_size`, `identity`) is unchanged. Close and reopen Configuration, Text to Image: the values persisted.
8. Optional failure path: stop the backend and reopen the tab. You get "Could not load the text-to-image settings: …" with a Retry button and no form.
- [ ] **Step 8: Commit**
```bash
git add frontend/src/utils/t2iConfigForm.ts \
  frontend/src/components/dialogs/ConfigT2ITab.vue \
  frontend/src/components/dialogs/ConfigDialog.vue
git commit -m "feat(t2i): Text to Image config tab" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 19: "Generated with T2I" smart-folder rule

**Files:**
- Create: `frontend/src/utils/pathSetCache.ts` (the `makePathSetCache(fetchPaths, isReferenced)` helper, used by the t2i rule only)
- Modify: `frontend/src/types/folders.ts` (`RuleField` gains `'t2i'`)
- Modify: `frontend/src/stores/folders.ts` (`FIELD_DEFS.t2i`, the `evaluateCondition` case, the module-scope cache, `ensureT2iPaths` / `refreshT2iPaths`, the forced-`loadTagPaths` refresh, the deep watcher, the store exports)
- Modify: `frontend/src/components/dialogs/SmartFolderEditor.vue` (the sibling of its `field === 'i2v'` line)
- Modify: `frontend/src/App.vue` (always-on `t2i` WebSocket bridge, next to the `i2v` one)
- Test: `/tmp/path_set_cache.check.mts` (throwaway `node:test` check of the helper, outside the repo, never committed; Node 22.18 or newer as in Task 18)

**Interfaces:**
- Consumes (Task 14; scratch stub, F1a's real module must export exactly this): `listT2iPaths(): Promise<string[]>` from `frontend/src/api/t2i.ts` (`GET /api/t2i/paths`, native paths, Task 12). WebSocket channel `t2i`, event `t2i_images_changed` with data `{batch_id, files}` (spec section 9). Existing: `useWebSocket(channel, handler)`, `Media.file_path`.
- Produces: `RuleField` includes `'t2i'`; `FIELD_DEFS.t2i` = `{ label: 'Generated with T2I', ops: ['is', 'is_not'], value: 'bool', defaultValue: () => true }`; `evaluateCondition` handles `field: 't2i'`; `useFoldersStore()` gains `ensureT2iPaths(always = false): Promise<void>` and `refreshT2iPaths(): Promise<void>`. **Task 22's image-delete handler must call `void foldersStore.refreshT2iPaths()` after a successful `deleteT2iImage`** (spec 12.7, "after an image delete"; the I2V mirror is `void foldersStore.refreshI2vSources()` in `I2VDialog.vue`'s `onDeleteVideo`). `frontend/src/utils/pathSetCache.ts` exports `PathSetCache` (`has(path)`, `ensure(always?)`, `refresh()`, the last two resolving `true` when the set changed) and `makePathSetCache(fetchPaths: () => Promise<string[]>, isReferenced: () => boolean): PathSetCache`.

**Design notes (each is deliberate):**
- Semantics are copied from `ensureI2vSources` / `refreshI2vSources`: nothing is fetched until a saved smart folder uses the rule (or the open editor asks with `always`); `refresh` after a load refetches even if the last such folder was since deleted; `refresh` before any load is a no-op unless a folder references the rule. The i2v and tag caches are not touched (spec 12.7: I2V's copy is not migrated).
- Two improvements over the i2v copy, both covered by the check: replies are ordered by when their request started (an older reply landing after a newer one was applied is dropped, but one that lands first is used rather than discarded, so there is no blank window), and a failed fetch resolves `false` and leaves the previous set in place.
- A change bumps `tagPathsVersion`, the counter every membership computed (`scopeMedia`, `scopeCount`, the editor's live count) already watches. The helper returns "changed" instead of taking a callback so its signature stays exactly `(fetch, isReferenced)`.
- The cache is module-scoped so the synchronous `evaluateCondition` can read it; the folders live inside the store, so the store installs the real "does a saved folder use the rule" check (`t2iRuleUsed`) when it is created.
- `t2i` is not a column on `/api/media`, so the covering indexes (`idx_media_summary_added`, `idx_media_summary_modified`) do not change.

Cross-checked while drafting: the real store, `SmartFolderEditor` and `App.vue` ran in a scratch DOM harness (happy-dom, Pinia, mocked `fetch`, a fake WebSocket) and 42 assertions held (nothing fetched while no folder uses the rule; one fetch when folders arrive; membership and `scopeCount`; the forced reload; the editor loading the set on demand and driving its live count; the bridge refetching on `t2i_images_changed` only, and only when a folder references the rule); six deliberate mutants (watcher hook, forced refresh, bridge, editor line, version bump, wrong cache in the evaluator) each failed it.

**Why `migrateSmartFolder` needs no change.** It has exactly one caller, `readLegacyLocalStorage()`, the one-shot localStorage-to-API import. Every smart folder the app loads from the server, creates, or receives over the `folders` WebSocket goes through `recordToSmart`, which passes `rules` through verbatim. Inside `migrateSmartFolder` only two things happen: legacy tag operators are remapped (`c.field === 'tags'`) and conditions whose field is in `DROPPED_FIELDS` (`prompt`, `dimensions`) are dropped. A `t2i` condition takes the `if (c.field !== 'tags') { conditions.push(c); continue }` branch untouched, and the legacy blob it reads was written before this rule existed, so it cannot contain one.

- [ ] **Step 1: Write the failing helper check** (`/tmp/path_set_cache.check.mts`)
```ts
// Throwaway check for src/utils/pathSetCache.ts -- not committed.
// Run from the frontend/ directory:  node --test-reporter=tap /tmp/path_set_cache.check.mts
import test from 'node:test'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'

const { makePathSetCache } = await import(pathToFileURL(resolve('src/utils/pathSetCache.ts')).href)

// A fetch whose replies the test resolves by hand, so ordering is explicit.
function deferredFetch() {
  const calls: Array<{ resolve: (v: string[]) => void; reject: (e: Error) => void }> = []
  const fetchPaths = () =>
    new Promise<string[]>((res, rej) => calls.push({ resolve: res, reject: rej }))
  return { calls, fetchPaths }
}
const settle = () => new Promise((r) => setTimeout(r, 0))

test('nothing is fetched while nothing references the set', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => false)
  assert.equal(await cache.ensure(), false)
  assert.equal(await cache.refresh(), false)
  assert.equal(f.calls.length, 0)
  assert.equal(cache.has('/a.png'), false)
})

test('ensure() loads once when referenced, then is a no-op', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const first = cache.ensure()
  f.calls[0].resolve(['/a.png', '/b.png'])
  assert.equal(await first, true, 'resolves true: the set changed')
  assert.equal(cache.has('/a.png'), true)
  assert.equal(cache.has('/c.png'), false)
  assert.equal(await cache.ensure(), false)
  assert.equal(f.calls.length, 1, 'no second request')
})

test('ensure(true) loads for the open editor even when no saved folder uses the rule', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => false)
  const p = cache.ensure(true)
  f.calls[0].resolve(['/x.png'])
  assert.equal(await p, true)
  assert.equal(cache.has('/x.png'), true)
})

test('refresh() after a load refetches even if nothing references the set any more', async () => {
  const f = deferredFetch()
  let referenced = true
  const cache = makePathSetCache(f.fetchPaths, () => referenced)
  const a = cache.ensure()
  f.calls[0].resolve(['/old.png'])
  await a
  referenced = false // the last folder using the rule was deleted
  const b = cache.refresh()
  f.calls[1].resolve(['/new.png'])
  assert.equal(await b, true)
  assert.equal(cache.has('/old.png'), false)
  assert.equal(cache.has('/new.png'), true)
})

test('refresh() loads for the first time when a folder references the rule', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const p = cache.refresh()
  f.calls[0].resolve(['/a.png'])
  assert.equal(await p, true)
  assert.equal(cache.has('/a.png'), true)
})

test('an older reply that lands after a newer one was applied is dropped', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const first = cache.refresh() // request 0
  const second = cache.refresh() // request 1
  f.calls[1].resolve(['/fresh.png'])
  assert.equal(await second, true)
  f.calls[0].resolve(['/stale.png']) // lands last
  assert.equal(await first, false, 'the stale reply changed nothing')
  assert.equal(cache.has('/fresh.png'), true)
  assert.equal(cache.has('/stale.png'), false)
})

test('an older reply that lands first is used until the newer one arrives', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const first = cache.refresh()
  const second = cache.refresh()
  f.calls[0].resolve(['/a.png'])
  assert.equal(await first, true)
  assert.equal(cache.has('/a.png'), true, 'no blank window while the newer request is pending')
  f.calls[1].resolve(['/b.png'])
  assert.equal(await second, true)
  assert.equal(cache.has('/a.png'), false)
  assert.equal(cache.has('/b.png'), true)
})

test('a failed fetch leaves the previous set in place and resolves false', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const a = cache.refresh()
  f.calls[0].resolve(['/keep.png'])
  await a
  const b = cache.refresh()
  f.calls[1].reject(new Error('network down'))
  assert.equal(await b, false)
  assert.equal(cache.has('/keep.png'), true)
})

test('a failed first load keeps has() false and lets a later ensure() retry', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const a = cache.ensure()
  f.calls[0].reject(new Error('boom'))
  assert.equal(await a, false)
  assert.equal(cache.has('/a.png'), false)
  const b = cache.ensure()
  await settle()
  assert.equal(f.calls.length, 2, 'the retry issued a new request')
  f.calls[1].resolve(['/a.png'])
  assert.equal(await b, true)
  assert.equal(cache.has('/a.png'), true)
})

test('an empty result is a loaded (empty) set, not "unloaded"', async () => {
  const f = deferredFetch()
  const cache = makePathSetCache(f.fetchPaths, () => true)
  const a = cache.ensure()
  f.calls[0].resolve([])
  assert.equal(await a, true)
  assert.equal(await cache.ensure(), false, 'already loaded: no refetch')
  assert.equal(f.calls.length, 1)
})
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node --test-reporter=tap /tmp/path_set_cache.check.mts`
Expected: FAIL — `Error [ERR_MODULE_NOT_FOUND]: Cannot find module '<repo>/frontend/src/utils/pathSetCache.ts' imported from /tmp/path_set_cache.check.mts` (`<repo>` is your checkout; exit code 1).
- [ ] **Step 3: Implement the helper** (`frontend/src/utils/pathSetCache.ts`)
```ts
// A lazily loaded set of file paths behind a synchronous `has()`.
//
// Built for smart-folder rules whose membership lives on the server (the
// "Generated with T2I" rule): the synchronous rule evaluator reads `has()`,
// while `ensure()` / `refresh()` fetch the set. It loads only when something
// needs it -- `isReferenced()` says a saved folder uses the rule, or the
// caller passes `always` (the open editor) -- and a failed fetch leaves
// whatever was loaded before untouched.
//
// `ensure()` and `refresh()` resolve `true` when the set changed, so the
// caller knows to bump whatever version counter its computeds watch.

export interface PathSetCache {
  /** Is `path` in the loaded set? Always false until a set has loaded. */
  has(path: string): boolean
  /** Load once, if anything uses the set (or `always`); a no-op afterwards. */
  ensure(always?: boolean): Promise<boolean>
  /** Refetch after the underlying data changed. A no-op while nothing has
   * loaded the set and no folder references it. */
  refresh(): Promise<boolean>
}

export function makePathSetCache(
  fetchPaths: () => Promise<string[]>,
  isReferenced: () => boolean,
): PathSetCache {
  let paths: Set<string> | null = null
  // Requests are numbered in the order they start; a reply is applied unless
  // a newer request's reply has already been applied, so a stale reply that
  // lands last can never overwrite fresher data.
  let started = 0
  let applied = 0

  async function load(): Promise<boolean> {
    const mine = ++started
    try {
      const rows = await fetchPaths()
      if (mine < applied) return false
      applied = mine
      paths = new Set(rows)
      return true
    } catch {
      // Leave the cache as it was; a later ensure()/refresh() retries.
      return false
    }
  }

  return {
    has: (path) => paths?.has(path) ?? false,
    async ensure(always = false) {
      if (paths !== null) return false
      if (!always && !isReferenced()) return false
      return load()
    },
    async refresh() {
      if (paths === null && !isReferenced()) return false
      return load()
    },
  }
}
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node --test-reporter=tap /tmp/path_set_cache.check.mts && npx vue-tsc -b`
Expected (the summary at the end of the TAP output):
```text
# tests 10
# pass 10
# fail 0
```
followed by no `vue-tsc` output (exit 0).
- [ ] **Step 5: Widen `RuleField` and let the compiler list what must follow** (`frontend/src/types/folders.ts`)
Modified file. Apply with `git apply --whitespace=nowarn <<'PATCH'`, pasting the diff below as the heredoc body and ending with a line `PATCH`.

```diff
diff --git a/frontend/src/types/folders.ts b/frontend/src/types/folders.ts
index 89cb3f9..e61b49a 100644
--- a/frontend/src/types/folders.ts
+++ b/frontend/src/types/folders.ts
@@ -10,6 +10,7 @@ export type RuleField =
   | 'modified'
   | 'added'
   | 'i2v'
+  | 't2i'
 
 export type RuleOp =
   | 'is'
```
Run: `cd frontend && npx vue-tsc -b`
Expected: FAIL — `src/stores/folders.ts(128,65): error TS2366: Function lacks ending return statement and return type does not include 'undefined'.` and `src/stores/folders.ts(793,14): error TS2741: Property 't2i' is missing in type '{ favorite: … }' …` (the rest of that line elides the mapped type; exit code 2).
- [ ] **Step 6: Implement the rule and run the build gate** (`frontend/src/stores/folders.ts`, `frontend/src/components/dialogs/SmartFolderEditor.vue`, `frontend/src/App.vue`)
Modified files (one patch for all three). Apply with `git apply --whitespace=nowarn <<'PATCH'`, pasting the diff below as the heredoc body and ending with a line `PATCH`.

```diff
diff --git a/frontend/src/App.vue b/frontend/src/App.vue
index 46098b2..32e044f 100644
--- a/frontend/src/App.vue
+++ b/frontend/src/App.vue
@@ -33,6 +33,13 @@ useWebSocket('folders', (event, data) => {
 useWebSocket('i2v', (event) => {
   if (event === 'i2v_videos_changed') void foldersStore.refreshI2vSources()
 })
+
+// New (or deleted) T2I images change the "Generated with T2I" smart-folder
+// rule's membership. Always on, so it works whether or not the T2I dialog
+// is open.
+useWebSocket('t2i', (event) => {
+  if (event === 't2i_images_changed') void foldersStore.refreshT2iPaths()
+})
 </script>
 
 <template>
diff --git a/frontend/src/components/dialogs/SmartFolderEditor.vue b/frontend/src/components/dialogs/SmartFolderEditor.vue
index 33013dc..8896fa3 100644
--- a/frontend/src/components/dialogs/SmartFolderEditor.vue
+++ b/frontend/src/components/dialogs/SmartFolderEditor.vue
@@ -77,6 +77,7 @@ function onFieldChange(idx: number, field: RuleField) {
   // The saved folders may not use the rule yet, so load its set now for
   // the live match count.
   if (field === 'i2v') void foldersStore.ensureI2vSources(true)
+  if (field === 't2i') void foldersStore.ensureT2iPaths(true)
 }
 
 function onOpChange(idx: number, op: RuleOp) {
diff --git a/frontend/src/stores/folders.ts b/frontend/src/stores/folders.ts
index d309c3c..cd7e9fd 100644
--- a/frontend/src/stores/folders.ts
+++ b/frontend/src/stores/folders.ts
@@ -14,6 +14,8 @@ import type {
 import { fileName } from '../utils/path'
 import { fetchTagPaths } from '../api/filters'
 import { listI2vSources } from '../api/i2v'
+import { listT2iPaths } from '../api/t2i'
+import { makePathSetCache } from '../utils/pathSetCache'
 import * as foldersApi from '../api/folders'
 import type { FolderRecord } from '../api/folders'
 
@@ -120,6 +122,13 @@ let tagPathSets: Record<string, Set<string>> = {}
 // while some smart folder (or the open editor) uses the rule.
 let i2vSourcePaths: Set<string> | null = null
 
+// Output paths of every T2I image, for the 't2i' rule. Module-scoped like the
+// two caches above so the synchronous evaluateCondition() can read it. The
+// folders live inside the store, so the store installs the real "does any
+// saved folder use the rule" check when it is created.
+let t2iRuleUsed: () => boolean = () => false
+const t2iPaths = makePathSetCache(listT2iPaths, () => t2iRuleUsed())
+
 function normalizeModel(m: Media): string {
   if (Array.isArray(m.model) && m.model.length > 0) return m.model[0]
   return ''
@@ -173,6 +182,10 @@ export function evaluateCondition(m: Media, c: SmartCondition): boolean {
       const has = i2vSourcePaths?.has(m.file_path) ?? false
       return op === 'is' ? has === Boolean(value) : has !== Boolean(value)
     }
+    case 't2i': {
+      const has = t2iPaths.has(m.file_path)
+      return op === 'is' ? has === Boolean(value) : has !== Boolean(value)
+    }
     case 'modified':
     case 'added': {
       // 'modified' reads the file's mtime; 'added' reads the row's
@@ -272,6 +285,7 @@ export const useFoldersStore = defineStore('folders', () => {
     await Promise.all([
       ensureTagPathsFor(referencedTagKeys()),
       options.force ? refreshI2vSources() : ensureI2vSources(),
+      options.force ? refreshT2iPaths() : ensureT2iPaths(),
     ])
   }
 
@@ -314,11 +328,35 @@ export const useFoldersStore = defineStore('folders', () => {
     await fetchI2vSources()
   }
 
+  // --- t2i output-path cache (the 't2i' rule) --------------------------
+  // The same shape as the i2v cache above, built on makePathSetCache (the i2v
+  // cache predates the helper and is left as it was). Membership changes
+  // whenever an image is generated or deleted, so the whole set is refetched;
+  // a change bumps tagPathsVersion, the counter every membership computed
+  // watches.
+
+  t2iRuleUsed = () =>
+    smartFolders.value.some((f) =>
+      f.rules.conditions.some((c) => c.field === 't2i'),
+    )
+
+  /** Load the set once, if anything uses it (or `always`, for the editor). */
+  async function ensureT2iPaths(always = false): Promise<void> {
+    if (await t2iPaths.ensure(always)) tagPathsVersion.value++
+  }
+
+  /** Refetch after images change (the `t2i` channel's t2i_images_changed,
+   * or an image delete); a no-op while nothing has loaded the set. */
+  async function refreshT2iPaths(): Promise<void> {
+    if (await t2iPaths.refresh()) tagPathsVersion.value++
+  }
+
   watch(
     smartFolders,
     () => {
       void ensureTagPathsFor(referencedTagKeys())
       void ensureI2vSources()
+      void ensureT2iPaths()
     },
     { deep: true },
   )
@@ -761,6 +799,8 @@ export const useFoldersStore = defineStore('folders', () => {
     ensureTagPathsFor,
     ensureI2vSources,
     refreshI2vSources,
+    ensureT2iPaths,
+    refreshT2iPaths,
     onFolderCreated,
     onFolderUpdated,
     onFolderDeleted,
@@ -843,4 +883,10 @@ export const FIELD_DEFS: Record<RuleField, FieldDef> = {
     value: 'bool',
     defaultValue: () => true,
   },
+  t2i: {
+    label: 'Generated with T2I',
+    ops: ['is', 'is_not'],
+    value: 'bool',
+    defaultValue: () => true,
+  },
 }
```
Run: `cd frontend && npx vue-tsc -b && npm run build`
Expected: `vue-tsc` prints nothing, then the build ends `✓ 525 modules transformed.` and `✓ built in …ms` (module count as on the reference branch).
- [ ] **Step 7: Manual check** (app running as in Task 17, Step 5)
1. On the library page, in the left panel's smart-folder section click the `+` ("New smart folder"). In the editor's first rule open the field dropdown: the last option is "Generated with T2I". Pick it. The operator select offers "is" / "is not" and the value select "true" / "false". DevTools, Network, shows exactly one `GET /api/t2i/paths`; picking other fields afterwards does not fetch it again.
2. The footer line "N items match these rules" equals the number of T2I images still in your library (0 if you have generated none). To compare: `curl -s http://localhost:8700/api/t2i/paths | python -c "import sys, json; print(len(json.load(sys.stdin)))"`.
3. Name it "T2I images" and create it. The folder shows the same count. Reload the page: the count is right after load, and Network shows a single `GET /api/t2i/paths` for it (a page with no saved folder that uses the rule issues none).
4. Live update: open the T2I dialog (sparkles button), generate one image. When it lands, the smart folder's count goes up by one without a reload and Network shows a fresh `GET /api/t2i/paths` (the `t2i_images_changed` bridge in `App.vue`). Delete that image from the T2I strip: the count goes back down (Task 22's `refreshT2iPaths()` call).
5. Edit the folder and set the value to "false": it now shows the complement, everything not generated with T2I.
- [ ] **Step 8: Commit**
```bash
git add frontend/src/utils/pathSetCache.ts \
  frontend/src/types/folders.ts \
  frontend/src/stores/folders.ts \
  frontend/src/components/dialogs/SmartFolderEditor.vue \
  frontend/src/App.vue
git commit -m "feat(t2i): 'Generated with T2I' smart-folder rule" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

## Phase 6 — The dialog

### Task 20: Caption filter popover

**Files:**
- Create: `frontend/src/utils/captionFilter.ts` (the pure filter-editing helpers)
- Create: `frontend/src/components/t2i/CaptionFilterPopover.vue`
- Test: `frontend/t2i-checks.local/task17.mjs` (throwaway, git-ignored; needs the harness from Task 14)

**Interfaces:**
- Consumes (Task 14, `frontend/src/types/t2i.ts`): `CaptionFilter`, `CaptionMeta`, `CaptionMetaColumn`, `CaptionMetaRange`, `CaptionMetaIntRange`, `CaptionMetaOption`, `cleanFilter(filter)`, `emptyFilter()`, `activeFilterCount(filter)`, `isFilterEmpty(filter)`. The metadata is `GET /api/t2i/captions/meta` (contract 8.2): `nudity` and `aspect_ratios` are `choice` columns, `clothing` is a `tags` column, the three scores are `range` columns with a `step`, `males` / `females` are `int_range` columns; only columns present in the CSV appear.
- Produces:
  - `frontend/src/utils/captionFilter.ts`: `type NumericColumn` (a `range` or `int_range` column), `type RangeEnd = 'min' | 'max'`, `type ClothingGroup = 'any' | 'none'`; `isNumericColumn(column)`, `editableColumns(meta): CaptionMetaColumn[]` (the columns the popover can edit, in the server's order), `chosenValues(filter, key): string[]`, `toggleChoice(filter, key, value): CaptionFilter`, `rangeEndValue(filter, key, end): number | undefined`, `setRangeEnd(filter, column, end, raw): CaptionFilter`, `clothingGroupOf(filter, item): ClothingGroup | null`, `toggleClothing(filter, group, item): CaptionFilter`, `pickedClothing(filter): {group, value}[]`, `searchOptions(options, query): CaptionMetaOption[]`. Every editing function returns a NEW, already cleaned filter and never mutates its input.
  - `<CaptionFilterPopover>`: props `meta: CaptionMeta | null`, `modelValue: CaptionFilter`, `count: number | null`, `total: number`; emits `update:modelValue(filter: CaptionFilter)` and `close`. It only edits a filter: the parent owns the value, its position, and the debounced "how many match" request whose answer comes back in `count` (null while unknown).

**Design notes.**
- Only the columns in `meta` are shown, in the order the server lists them, so a caption file without (say) a Clothing column has no clothing field, and a column the filter cannot express is ignored.
- Range boxes commit on change (blur, Enter, the spinner), not per keystroke: a controlled number box that re-rendered mid-word would eat the decimal point of `0.`. The box then settles on what the filter holds: a minimum cannot pass the maximum (the server answers 400 to `min > max`), whole numbers are rounded for `males` / `females`, and an end at or beyond the column's own extreme restricts nothing, so it is dropped (the box shows the bound as its placeholder). Text that is not a number changes nothing.
- Clothing is one searchable list with two tick columns, `Has any` and `Has none`; an item is wanted or unwanted, never both, so ticking it in one column clears the other. Ticked items also show as chips above the search box, so the ones a search hides stay visible and removable. The list is capped at about 150 px and scrolls; the rest of the panel stays in view.
- The popover emits cleaned filters (`cleanFilter`: empty lists and empty ranges are removed), so `activeFilterCount` is exact and the badge counts restricted fields, `clothing_any` and `clothing_none` separately.
- Esc inside the panel closes it; there is no click-outside handling on purpose (a stray click must not hide a filter being built).

Cross-checked while drafting: the real component ran in headless Chromium against a scratch backend (Playwright, 32 checks: every field type, the debounced count, clamping, chips, Clear, Esc); 11 deliberate mutants of the helpers and the template each failed the check below.

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/task17.mjs`)

The helpers run as plain modules; the popover is rendered to a string with Vue's server renderer. All metadata is hand-written.

`frontend/t2i-checks.local/task17.mjs`:
```js
// Task 20 logic checks: the pure caption-filter editing helpers, and the
// popover rendered to a string through Vue's SSR renderer.
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { assert, check, finish, load } from './harness.mjs'

const f = await load('/src/utils/captionFilter.ts')
const Popover = (await load('/src/components/t2i/CaptionFilterPopover.vue')).default

const render = (props) => renderToString(createSSRApp({ render: () => h(Popover, props) }))

// Hand-written column metadata in the shape of GET /api/t2i/captions/meta.
const nudity = {
  key: 'nudity',
  label: 'Nudity',
  type: 'choice',
  options: [
    { value: 'full', count: 400 },
    { value: 'partial', count: 401 },
    { value: 'none', count: 199 },
  ],
}
const artistic = { key: 'artistic_quality', label: 'Artistic Quality', type: 'range', min: 0.1, max: 0.95, step: 0.05 }
const erotic = { key: 'erotic_score', label: 'Erotic Score', type: 'range', min: 0, max: 1, step: 0.05 }
const porn = { key: 'pornographic_score', label: 'Pornographic Score', type: 'range', min: 0, max: 1, step: 0.05 }
const males = { key: 'males', label: 'Males', type: 'int_range', min: 0, max: 3 }
const females = { key: 'females', label: 'Females', type: 'int_range', min: 0, max: 5 }
const aspects = {
  key: 'aspect_ratios',
  label: 'Aspect Ratio',
  type: 'choice',
  options: [
    { value: '2:3', count: 600 },
    { value: '3:2', count: 340 },
    { value: '1:1', count: 60 },
  ],
}
const clothing = {
  key: 'clothing',
  label: 'Clothing',
  type: 'tags',
  options: [
    { value: 'corset', count: 120 },
    { value: 'tie', count: 80 },
    { value: 'stockings', count: 40 },
  ],
}
const fullMeta = { total: 1000, columns: [nudity, artistic, erotic, porn, males, females, aspects, clothing] }

// ---- editableColumns -----------------------------------------------------------

await check('editableColumns: nothing without metadata', () => {
  assert.deepEqual(f.editableColumns(null), [])
  assert.deepEqual(f.editableColumns(undefined), [])
  assert.deepEqual(f.editableColumns({ total: 0, columns: [] }), [])
})

await check('editableColumns keeps the server order and drops what the filter cannot express', () => {
  const stray = { key: 'mood', label: 'Mood', type: 'choice', options: [] }
  const wrongType = { key: 'males', label: 'Males', type: 'choice', options: [] }
  const wrongTags = { key: 'nudity', label: 'Nudity', type: 'tags', options: [] }
  const meta = { total: 5, columns: [clothing, stray, males, wrongType, nudity, wrongTags] }
  assert.deepEqual(
    f.editableColumns(meta).map((c) => c.key),
    ['clothing', 'males', 'nudity'],
  )
})

await check('isNumericColumn tells the min-max columns from the rest', () => {
  assert.equal(f.isNumericColumn(artistic), true)
  assert.equal(f.isNumericColumn(males), true)
  assert.equal(f.isNumericColumn(nudity), false)
  assert.equal(f.isNumericColumn(clothing), false)
})

await check('chosenValues reads a choice column and is empty for anything else', () => {
  const flt = { nudity: ['full', 'none'], aspect_ratios: ['3:2'], clothing_any: ['tie'] }
  assert.deepEqual(f.chosenValues(flt, 'nudity'), ['full', 'none'])
  assert.deepEqual(f.chosenValues(flt, 'aspect_ratios'), ['3:2'])
  assert.deepEqual(f.chosenValues(flt, 'clothing_any'), [])
  assert.deepEqual(f.chosenValues({}, 'nudity'), [])
})

// ---- toggleChoice ------------------------------------------------------------------

await check('toggleChoice adds and removes a value and drops the emptied key', () => {
  const one = f.toggleChoice({}, 'nudity', 'full')
  assert.deepEqual(one, { nudity: ['full'] })
  const two = f.toggleChoice(one, 'nudity', 'none')
  assert.deepEqual(two, { nudity: ['full', 'none'] })
  assert.deepEqual(f.toggleChoice(two, 'nudity', 'full'), { nudity: ['none'] })
  assert.deepEqual(f.toggleChoice({ nudity: ['none'] }, 'nudity', 'none'), {})
})

await check('toggleChoice works on aspect_ratios, keeps other keys and never mutates its input', () => {
  const before = { nudity: ['full'], males: { min: 1 } }
  const snapshot = JSON.stringify(before)
  const after = f.toggleChoice(before, 'aspect_ratios', '3:2')
  assert.deepEqual(after, { nudity: ['full'], males: { min: 1 }, aspect_ratios: ['3:2'] })
  assert.equal(JSON.stringify(before), snapshot)
  assert.notEqual(after, before)
})

await check('toggleChoice ignores a key the filter does not have', () => {
  assert.deepEqual(f.toggleChoice({ nudity: ['full'] }, 'mood', 'sad'), { nudity: ['full'] })
})

// ---- rangeEndValue / setRangeEnd -----------------------------------------------------

await check('rangeEndValue reads either end and undefined when unset', () => {
  const flt = { erotic_score: { min: 0.3 } }
  assert.equal(f.rangeEndValue(flt, 'erotic_score', 'min'), 0.3)
  assert.equal(f.rangeEndValue(flt, 'erotic_score', 'max'), undefined)
  assert.equal(f.rangeEndValue(flt, 'males', 'min'), undefined)
  assert.equal(f.rangeEndValue(flt, 'mood', 'min'), undefined)
})

await check('setRangeEnd sets the two ends independently and clearing both drops the key', () => {
  let flt = f.setRangeEnd({}, erotic, 'min', '0.3')
  assert.deepEqual(flt, { erotic_score: { min: 0.3 } })
  flt = f.setRangeEnd(flt, erotic, 'max', '0.8')
  assert.deepEqual(flt, { erotic_score: { min: 0.3, max: 0.8 } })
  flt = f.setRangeEnd(flt, erotic, 'min', '')
  assert.deepEqual(flt, { erotic_score: { max: 0.8 } })
  flt = f.setRangeEnd(flt, erotic, 'max', '  ')
  assert.deepEqual(flt, {})
})

await check('setRangeEnd drops an end that sits at the column extreme (it restricts nothing)', () => {
  assert.deepEqual(f.setRangeEnd({}, artistic, 'min', '0.1'), {})
  assert.deepEqual(f.setRangeEnd({}, artistic, 'min', '-5'), {})
  assert.deepEqual(f.setRangeEnd({}, artistic, 'max', '0.95'), {})
  assert.deepEqual(f.setRangeEnd({}, artistic, 'max', '7'), {})
  assert.deepEqual(f.setRangeEnd({ artistic_quality: { min: 0.4, max: 0.6 } }, artistic, 'max', '1'), {
    artistic_quality: { min: 0.4 },
  })
})

await check('setRangeEnd clamps into the column and never lets min pass max', () => {
  assert.deepEqual(f.setRangeEnd({}, artistic, 'min', '2'), { artistic_quality: { min: 0.95 } })
  assert.deepEqual(f.setRangeEnd({ erotic_score: { max: 0.4 } }, erotic, 'min', '0.7'), {
    erotic_score: { min: 0.4, max: 0.4 },
  })
  assert.deepEqual(f.setRangeEnd({ erotic_score: { min: 0.6 } }, erotic, 'max', '0.2'), {
    erotic_score: { min: 0.6, max: 0.6 },
  })
})

await check('setRangeEnd rounds integer columns and leaves the rest of the filter alone', () => {
  const flt = { nudity: ['full'] }
  assert.deepEqual(f.setRangeEnd(flt, males, 'min', '1.6'), { nudity: ['full'], males: { min: 2 } })
  assert.deepEqual(f.setRangeEnd({}, males, 'max', '3'), {})
  assert.deepEqual(f.setRangeEnd({}, males, 'min', '0'), {})
  assert.deepEqual(f.setRangeEnd({}, females, 'max', '2'), { females: { max: 2 } })
})

await check('setRangeEnd ignores text that is not a number and never mutates its input', () => {
  const before = { erotic_score: { min: 0.3 } }
  const snapshot = JSON.stringify(before)
  assert.deepEqual(f.setRangeEnd(before, erotic, 'max', 'abc'), { erotic_score: { min: 0.3 } })
  assert.deepEqual(f.setRangeEnd(before, erotic, 'max', 'Infinity'), { erotic_score: { min: 0.3 } })
  f.setRangeEnd(before, erotic, 'max', '0.9')
  assert.equal(JSON.stringify(before), snapshot)
})

// ---- clothing ---------------------------------------------------------------------------

await check('toggleClothing puts an item in one group and moves it when the other is ticked', () => {
  let flt = f.toggleClothing({}, 'any', 'corset')
  assert.deepEqual(flt, { clothing_any: ['corset'] })
  assert.equal(f.clothingGroupOf(flt, 'corset'), 'any')
  flt = f.toggleClothing(flt, 'none', 'corset')
  assert.deepEqual(flt, { clothing_none: ['corset'] })
  assert.equal(f.clothingGroupOf(flt, 'corset'), 'none')
  flt = f.toggleClothing(flt, 'none', 'corset')
  assert.deepEqual(flt, {})
  assert.equal(f.clothingGroupOf(flt, 'corset'), null)
})

await check('toggleClothing keeps the other items of both groups', () => {
  const start = { clothing_any: ['corset', 'tie'], clothing_none: ['stockings'], nudity: ['none'] }
  const snapshot = JSON.stringify(start)
  const next = f.toggleClothing(start, 'any', 'tie')
  assert.deepEqual(next, { clothing_any: ['corset'], clothing_none: ['stockings'], nudity: ['none'] })
  assert.equal(JSON.stringify(start), snapshot)
})

await check('pickedClothing lists the "has any" items first, then the "has none" ones', () => {
  assert.deepEqual(f.pickedClothing({}), [])
  assert.deepEqual(f.pickedClothing({ clothing_any: ['a', 'b'], clothing_none: ['c'] }), [
    { group: 'any', value: 'a' },
    { group: 'any', value: 'b' },
    { group: 'none', value: 'c' },
  ])
})

await check('searchOptions matches case-insensitively on a trimmed substring and keeps the order', () => {
  const opts = clothing.options
  assert.deepEqual(f.searchOptions(opts, ''), opts)
  assert.deepEqual(f.searchOptions(opts, '   '), opts)
  assert.deepEqual(f.searchOptions(opts, ' OR').map((o) => o.value), ['corset'])
  assert.deepEqual(f.searchOptions(opts, 'e').map((o) => o.value), ['corset', 'tie'])
  assert.deepEqual(f.searchOptions(opts, 'tie').map((o) => o.value), ['tie'])
  assert.deepEqual(f.searchOptions(opts, 'zzz'), [])
  const mixed = [{ value: 'Leather Jacket', count: 5 }, ...opts]
  assert.deepEqual(f.searchOptions(mixed, 'JACKET').map((o) => o.value), ['Leather Jacket'], 'capitals in the data match too')
  assert.deepEqual(f.searchOptions(mixed, 'leather').map((o) => o.value), ['Leather Jacket'])
})

// ---- the popover, rendered ----------------------------------------------------------------

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// The opening tag of the element carrying this aria-label (SSR may order attributes either way).
const tagWith = (html, label, tag = 'input') => {
  const found = html.match(new RegExp(`<${tag}\\b[^>]*aria-label="${escapeRe(label)}"[^>]*>`))
  assert.ok(found, `no <${tag}> labelled "${label}"`)
  return found[0]
}
// How many elements carry ALL of these classes.
const withClasses = (html, ...tokens) =>
  [...html.matchAll(/class="([^"]*)"/g)]
    .map((m) => m[1].split(/\s+/))
    .filter((classes) => tokens.every((t) => classes.includes(t))).length

await check('popover shows every column in the server order, with counts', async () => {
  const html = await render({ meta: fullMeta, modelValue: {}, count: 342, total: 1000 })
  const labels = ['Nudity', 'Artistic Quality', 'Erotic Score', 'Pornographic Score', 'Males', 'Females', 'Aspect Ratio', 'Clothing']
  const at = labels.map((label) => html.indexOf(`>${label}<`))
  assert.ok(at.every((i) => i > 0), `every label is present: ${at}`)
  assert.deepEqual([...at].sort((a, b) => a - b), at, 'labels come in the order of the metadata')
  assert.match(html, />partial</)
  assert.match(html, />401</)
  assert.match(html, />600</)
  assert.match(html, /corset/)
})

await check('popover shows only the fields the metadata has', async () => {
  const only = await render({ meta: { total: 5, columns: [nudity] }, modelValue: {}, count: 5, total: 5 })
  assert.match(only, />Nudity</)
  for (const absent of ['Artistic Quality', 'Erotic Score', 'Males', 'Females', 'Aspect Ratio', 'Clothing']) {
    assert.ok(!only.includes(`>${absent}<`), `${absent} is not rendered`)
  }
})

await check('popover says how many captions match, or "..." while the count is unknown', async () => {
  const known = await render({ meta: fullMeta, modelValue: {}, count: 342, total: 640 })
  assert.match(known, /342<\/strong> captions match/)
  assert.match(known, /of 640/)
  const pending = await render({ meta: fullMeta, modelValue: {}, count: null, total: 640 })
  assert.match(pending, /\.\.\.<\/strong> captions match/)
  const zero = await render({ meta: fullMeta, modelValue: {}, count: 0, total: 640 })
  assert.match(zero, /0<\/strong> captions match/)
  const unknownTotal = await render({ meta: fullMeta, modelValue: {}, count: 3, total: 0 })
  assert.doesNotMatch(unknownTotal, /cfp-total/)
})

await check('popover reflects the filter: ticked boxes, range values, clothing chips and a badge', async () => {
  const modelValue = {
    nudity: ['partial'],
    erotic_score: { min: 0.3 },
    males: { max: 2 },
    clothing_any: ['corset'],
    clothing_none: ['tie'],
  }
  const html = await render({ meta: fullMeta, modelValue, count: 12, total: 640 })
  assert.match(tagWith(html, 'Nudity: partial'), /\schecked/)
  assert.doesNotMatch(tagWith(html, 'Nudity: full'), /\schecked/)
  assert.match(tagWith(html, 'Erotic Score minimum'), /value="0\.3"/)
  assert.match(tagWith(html, 'Erotic Score maximum'), /value=""/)
  assert.match(tagWith(html, 'Males maximum'), /value="2"/)
  assert.match(tagWith(html, 'corset: has any'), /\schecked/)
  assert.doesNotMatch(tagWith(html, 'corset: has none'), /\schecked/)
  assert.match(tagWith(html, 'tie: has none'), /\schecked/)
  assert.doesNotMatch(tagWith(html, 'tie: has any'), /\schecked/)
  assert.equal(withClasses(html, 'cfp-chip'), 2)
  assert.equal(withClasses(html, 'cfp-chip', 'none'), 1)
  assert.match(html, /class="cfp-badge"[^>]*>5</)
})

await check('popover range boxes show the column bounds as placeholders and the right step', async () => {
  const html = await render({ meta: fullMeta, modelValue: {}, count: 1000, total: 1000 })
  const artisticMin = tagWith(html, 'Artistic Quality minimum')
  assert.match(artisticMin, /placeholder="0\.1"/)
  assert.match(artisticMin, /step="0\.05"/)
  assert.match(artisticMin, /min="0\.1"/)
  assert.match(tagWith(html, 'Artistic Quality maximum'), /placeholder="0\.95"/)
  const femalesMax = tagWith(html, 'Females maximum')
  assert.match(femalesMax, /step="1"/)
  assert.match(femalesMax, /max="5"/)
  assert.match(femalesMax, /placeholder="5"/)
})

await check('Clear is disabled for an empty filter and enabled once something is set', async () => {
  const empty = await render({ meta: fullMeta, modelValue: {}, count: 1000, total: 1000 })
  tagWith(empty, 'Close filter', 'button') // throws when the close button is missing
  assert.match(empty.match(/<button[^>]*class="cfp-clear"[^>]*>/)[0], /\sdisabled/)
  assert.doesNotMatch(empty, /class="cfp-badge"/)
  const set = await render({ meta: fullMeta, modelValue: { nudity: ['none'] }, count: 3, total: 1000 })
  assert.doesNotMatch(set.match(/<button[^>]*class="cfp-clear"[^>]*>/)[0], /\sdisabled/)
})

await check('popover without metadata says the filters are loading', async () => {
  const html = await render({ meta: null, modelValue: {}, count: null, total: 0 })
  assert.match(html, /Loading filters/)
  assert.ok(!html.includes('>Nudity<'))
  const bare = await render({ meta: { total: 3, columns: [] }, modelValue: {}, count: 3, total: 3 })
  assert.match(bare, /no columns to filter on/)
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task17.mjs`
Expected: FAIL (exit code 1) — `Error: Failed to load url /src/utils/captionFilter.ts (resolved id: /src/utils/captionFilter.ts). Does the file exist?`
- [ ] **Step 3: Implement**

`frontend/src/utils/captionFilter.ts`:
```ts
// Pure editing helpers for the caption-filter popover. Every function takes a
// CaptionFilter and returns a NEW, already cleaned one (cleanFilter: nothing
// that restricts nothing survives), so the popover just emits the result and
// `activeFilterCount` / `isFilterEmpty` stay exact.
import {
  cleanFilter,
  type CaptionFilter,
  type CaptionMeta,
  type CaptionMetaColumn,
  type CaptionMetaIntRange,
  type CaptionMetaOption,
  type CaptionMetaRange,
} from '../types/t2i'

export type NumericColumn = CaptionMetaRange | CaptionMetaIntRange
export type RangeEnd = 'min' | 'max'
export type ClothingGroup = 'any' | 'none'

// The filter keys each kind of metadata column maps onto. A column outside
// these sets (a newer server, say) has nothing to edit and is not shown.
const CHOICE_KEYS = ['nudity', 'aspect_ratios'] as const
const RANGE_KEYS = ['artistic_quality', 'erotic_score', 'pornographic_score', 'males', 'females'] as const
const TAGS_KEY = 'clothing'

type ChoiceKey = (typeof CHOICE_KEYS)[number]
type RangeKey = (typeof RANGE_KEYS)[number]

const isChoiceKey = (key: string): key is ChoiceKey => (CHOICE_KEYS as readonly string[]).includes(key)
const isRangeKey = (key: string): key is RangeKey => (RANGE_KEYS as readonly string[]).includes(key)

export const isNumericColumn = (c: CaptionMetaColumn): c is NumericColumn =>
  c.type === 'range' || c.type === 'int_range'

/** The columns the popover can edit, in the order the server listed them. */
export function editableColumns(meta: CaptionMeta | null | undefined): CaptionMetaColumn[] {
  if (!meta) return []
  return meta.columns.filter((c) => {
    if (c.type === 'choice') return isChoiceKey(c.key)
    if (c.type === 'tags') return c.key === TAGS_KEY
    return isRangeKey(c.key)
  })
}

/** The values ticked in a choice column (nudity, aspect_ratios); empty for any other key. */
export function chosenValues(filter: CaptionFilter, key: string): string[] {
  return isChoiceKey(key) ? (filter[key] ?? []) : []
}

/** Ticks or unticks one value of a choice column (nudity, aspect_ratios). */
export function toggleChoice(filter: CaptionFilter, key: string, value: string): CaptionFilter {
  if (!isChoiceKey(key)) return cleanFilter(filter)
  const current = filter[key] ?? []
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
  return cleanFilter({ ...filter, [key]: next })
}

/** One end of a numeric range, undefined when that end is not set. */
export function rangeEndValue(filter: CaptionFilter, key: string, end: RangeEnd): number | undefined {
  return isRangeKey(key) ? filter[key]?.[end] : undefined
}

/**
 * Sets one end of a numeric range from what the user typed. Blank clears the
 * end; text that is not a number changes nothing. The value is rounded for an
 * integer column and clamped into the column's own bounds, then held back by
 * the other end so min can never pass max (the server answers 400 to that).
 * An end that sits at the column's extreme restricts nothing and is dropped.
 */
export function setRangeEnd(
  filter: CaptionFilter,
  column: NumericColumn,
  end: RangeEnd,
  raw: string,
): CaptionFilter {
  const key = column.key
  if (!isRangeKey(key)) return cleanFilter(filter)
  const range = { ...filter[key] }
  const text = raw.trim()
  if (text === '') {
    delete range[end]
  } else {
    let n = Number(text)
    if (!Number.isFinite(n)) return cleanFilter(filter)
    if (column.type === 'int_range') n = Math.round(n)
    n = Math.min(column.max, Math.max(column.min, n))
    const other = end === 'min' ? range.max : range.min
    if (other !== undefined) n = end === 'min' ? Math.min(n, other) : Math.max(n, other)
    if ((end === 'min' && n <= column.min) || (end === 'max' && n >= column.max)) delete range[end]
    else range[end] = n
  }
  return cleanFilter({ ...filter, [key]: range })
}

/** Which clothing group an item is in, if any. */
export function clothingGroupOf(filter: CaptionFilter, item: string): ClothingGroup | null {
  if (filter.clothing_any?.includes(item)) return 'any'
  if (filter.clothing_none?.includes(item)) return 'none'
  return null
}

/**
 * Ticks or unticks an item in "has any" or "has none". An item is wanted or
 * unwanted, never both, so ticking it in one group removes it from the other.
 */
export function toggleClothing(filter: CaptionFilter, group: ClothingGroup, item: string): CaptionFilter {
  const here = group === 'any' ? 'clothing_any' : 'clothing_none'
  const there = group === 'any' ? 'clothing_none' : 'clothing_any'
  const held = filter[here] ?? []
  const next: CaptionFilter = { ...filter }
  next[here] = held.includes(item) ? held.filter((v) => v !== item) : [...held, item]
  next[there] = (filter[there] ?? []).filter((v) => v !== item)
  return cleanFilter(next)
}

/** Every ticked clothing item, the "has any" ones first (the popover's chips). */
export function pickedClothing(filter: CaptionFilter): { group: ClothingGroup; value: string }[] {
  return [
    ...(filter.clothing_any ?? []).map((value) => ({ group: 'any' as const, value })),
    ...(filter.clothing_none ?? []).map((value) => ({ group: 'none' as const, value })),
  ]
}

/** The options whose value contains the query (case-insensitive, trimmed); server order kept. */
export function searchOptions(options: CaptionMetaOption[], query: string): CaptionMetaOption[] {
  const q = query.trim().toLowerCase()
  return q ? options.filter((o) => o.value.toLowerCase().includes(q)) : options
}
```

`frontend/src/components/t2i/CaptionFilterPopover.vue`:
```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  activeFilterCount,
  emptyFilter,
  isFilterEmpty,
  type CaptionFilter,
  type CaptionMeta,
} from '../../types/t2i'
import {
  chosenValues,
  clothingGroupOf,
  editableColumns,
  isNumericColumn,
  pickedClothing,
  rangeEndValue,
  searchOptions,
  setRangeEnd,
  toggleChoice,
  toggleClothing,
  type ClothingGroup,
  type NumericColumn,
  type RangeEnd,
} from '../../utils/captionFilter'

// The panel that narrows which caption-file rows Random Caption draws from.
// It only edits a filter: the parent owns the value (v-model), debounces the
// "how many rows match" request and passes the answer back in `count`
// (null while it is unknown). Positioning is the parent's job as well.
//
// Only the columns present in `meta` are shown, so a caption file without,
// say, a Clothing column simply has no clothing field.
const props = defineProps<{
  meta: CaptionMeta | null
  modelValue: CaptionFilter
  count: number | null
  total: number
}>()
const emit = defineEmits<{
  'update:modelValue': [value: CaptionFilter]
  close: []
}>()

const columns = computed(() => editableColumns(props.meta))
const active = computed(() => activeFilterCount(props.modelValue))
const empty = computed(() => isFilterEmpty(props.modelValue))
const picked = computed(() => pickedClothing(props.modelValue))

// One search box narrows the clothing list (there is a single tags column).
const clothingQuery = ref('')
const visibleClothing = computed(() => {
  const column = columns.value.find((c) => c.type === 'tags')
  return column && column.type === 'tags' ? searchOptions(column.options, clothingQuery.value) : []
})

const fmt = (n: number): string => n.toLocaleString()

function onChoice(key: string, value: string) {
  emit('update:modelValue', toggleChoice(props.modelValue, key, value))
}

// Range boxes commit on change (blur / Enter / the spinner), not per
// keystroke: a controlled number box that re-rendered mid-word would eat the
// decimal point of "0.".
function onRange(column: NumericColumn, end: RangeEnd, e: Event) {
  const input = e.target as HTMLInputElement
  const next = setRangeEnd(props.modelValue, column, end, input.value)
  emit('update:modelValue', next)
  // Settle the box on what the filter holds now: clamped, dropped at the
  // column's own extreme, or unchanged when the text was not a number.
  input.value = String(rangeEndValue(next, column.key, end) ?? '')
}

function onClothing(group: ClothingGroup, item: string) {
  emit('update:modelValue', toggleClothing(props.modelValue, group, item))
}

function clear() {
  emit('update:modelValue', emptyFilter())
}
</script>

<template>
  <div class="cfp" role="dialog" aria-label="Caption filter" @keydown.esc.stop="emit('close')">
    <header class="cfp-head">
      <strong class="cfp-title">Caption filter</strong>
      <span
        v-if="active > 0"
        class="cfp-badge"
        :title="`${active} filter${active === 1 ? '' : 's'} active`"
      >{{ active }}</span>
      <button type="button" class="cfp-x" title="Close" aria-label="Close filter" @click="emit('close')">✕</button>
    </header>

    <div class="cfp-body">
      <p v-if="!meta" class="cfp-note">Loading filters…</p>
      <p v-else-if="!columns.length" class="cfp-note">
        This caption file has no columns to filter on.
      </p>

      <template v-for="col in columns" :key="col.key">
        <!-- min - max: the scores (decimals) and the male / female counts (integers) -->
        <div v-if="isNumericColumn(col)" class="cfp-field cfp-range" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <input
            class="cfp-num"
            type="number"
            :min="col.min"
            :max="col.max"
            :step="col.type === 'range' ? col.step : 1"
            :value="rangeEndValue(modelValue, col.key, 'min') ?? ''"
            :placeholder="String(col.min)"
            :aria-label="`${col.label} minimum`"
            @change="onRange(col, 'min', $event)"
          />
          <span class="cfp-dash" aria-hidden="true">–</span>
          <input
            class="cfp-num"
            type="number"
            :min="col.min"
            :max="col.max"
            :step="col.type === 'range' ? col.step : 1"
            :value="rangeEndValue(modelValue, col.key, 'max') ?? ''"
            :placeholder="String(col.max)"
            :aria-label="`${col.label} maximum`"
            @change="onRange(col, 'max', $event)"
          />
        </div>

        <!-- has any / has none: one searchable list, two tick columns -->
        <div v-else-if="col.type === 'tags'" class="cfp-field" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <div v-if="picked.length" class="cfp-chips">
            <button
              v-for="p in picked"
              :key="`${p.group}:${p.value}`"
              type="button"
              class="cfp-chip"
              :class="p.group"
              :title="(p.group === 'any' ? 'Must have this item' : 'Must not have this item') + ' - click to remove'"
              @click="onClothing(p.group, p.value)"
            >{{ p.group === 'any' ? '+' : '−' }} {{ p.value }} ✕</button>
          </div>
          <input
            v-model="clothingQuery"
            class="cfp-search"
            type="search"
            placeholder="Search clothing…"
            aria-label="Search clothing"
          />
          <div class="cfp-tags-head" aria-hidden="true">
            <span title="Captions whose clothing includes at least one ticked item">Has any</span>
            <span title="Captions whose clothing includes none of the ticked items">Has none</span>
          </div>
          <div class="cfp-list">
            <div v-for="o in visibleClothing" :key="o.value" class="cfp-row">
              <span class="cfp-opt">{{ o.value }} <span class="cfp-n">{{ fmt(o.count) }}</span></span>
              <input
                type="checkbox"
                :checked="clothingGroupOf(modelValue, o.value) === 'any'"
                :aria-label="`${o.value}: has any`"
                @change="onClothing('any', o.value)"
              />
              <input
                type="checkbox"
                :checked="clothingGroupOf(modelValue, o.value) === 'none'"
                :aria-label="`${o.value}: has none`"
                @change="onClothing('none', o.value)"
              />
            </div>
            <p v-if="!visibleClothing.length" class="cfp-note">
              Nothing matches “{{ clothingQuery.trim() }}”.
            </p>
          </div>
        </div>

        <!-- pick any of: nudity, aspect ratio -->
        <div v-else class="cfp-field" role="group" :aria-label="col.label">
          <span class="cfp-label">{{ col.label }}</span>
          <div class="cfp-checks">
            <label v-for="o in col.options" :key="o.value" class="cfp-check">
              <input
                type="checkbox"
                :checked="chosenValues(modelValue, col.key).includes(o.value)"
                :aria-label="`${col.label}: ${o.value}`"
                @change="onChoice(col.key, o.value)"
              />
              <span class="cfp-opt">{{ o.value }}</span>
              <span class="cfp-n">{{ fmt(o.count) }}</span>
            </label>
          </div>
        </div>
      </template>
    </div>

    <footer class="cfp-foot">
      <span class="cfp-count" role="status" aria-live="polite">
        <strong>{{ count === null ? '...' : fmt(count) }}</strong> captions match
        <span v-if="total > 0" class="cfp-total">of {{ fmt(total) }}</span>
      </span>
      <button type="button" class="cfp-clear" :disabled="empty" @click="clear">Clear</button>
    </footer>
  </div>
</template>

<style scoped>
.cfp {
  display: flex;
  flex-direction: column;
  width: 360px;
  max-width: 92vw;
  max-height: min(72vh, 560px);
  background: var(--surface-section);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
  font-size: 13px;
}

.cfp-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--surface-border);
}

.cfp-title {
  font-size: 13px;
}

.cfp-badge {
  min-width: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--primary-color);
  color: var(--primary-color-text, #fff);
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.cfp-x {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 4px;
}

.cfp-x:hover {
  color: var(--text-color);
}

.cfp-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cfp-note {
  margin: 0;
  color: var(--text-color-secondary);
  font-size: 12px;
}

.cfp-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cfp-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  color: var(--text-color-secondary);
}

.cfp-range {
  flex-direction: row;
  align-items: center;
  gap: 6px;
}

.cfp-range .cfp-label {
  flex: 0 0 148px;
}

.cfp-num,
.cfp-search {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 4px 6px;
  box-sizing: border-box;
}

.cfp-num {
  width: 72px;
}

.cfp-search {
  width: 100%;
}

.cfp-num:focus,
.cfp-search:focus {
  outline: none;
  border-color: var(--primary-color);
}

.cfp-dash {
  color: var(--text-color-secondary);
}

.cfp-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.cfp-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}

.cfp-n {
  color: var(--text-color-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.cfp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cfp-chip {
  border: 1px solid var(--surface-border);
  border-radius: 10px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  padding: 1px 8px;
  cursor: pointer;
}

.cfp-chip.any {
  border-color: var(--primary-color);
}

.cfp-chip.none {
  border-color: #c33;
  color: #c33;
}

.cfp-tags-head {
  display: grid;
  grid-template-columns: 1fr 56px 56px;
  font-size: 11px;
  color: var(--text-color-secondary);
}

.cfp-tags-head span {
  text-align: center;
}

.cfp-tags-head span:first-child {
  grid-column: 2;
}

/* The list is the only part that can be long (over a hundred items), so it
   scrolls on its own and the rest of the panel stays in view. */
.cfp-list {
  max-height: 148px;
  overflow-y: auto;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
}

.cfp-row {
  display: grid;
  grid-template-columns: 1fr 56px 56px;
  align-items: center;
  padding: 2px 8px;
}

.cfp-row input {
  justify-self: center;
}

.cfp-row:hover {
  background: var(--surface-hover);
}

.cfp-list .cfp-note {
  padding: 6px 8px;
}

.cfp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px 10px;
  border-top: 1px solid var(--surface-border);
}

.cfp-total {
  color: var(--text-color-secondary);
  font-size: 11px;
}

.cfp-clear {
  padding: 4px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.cfp-clear:disabled {
  opacity: 0.55;
  cursor: default;
}
</style>
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task17.mjs`
Expected: `24 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 695ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build prints it too).
`vue-tsc` type-checks the popover's template even though nothing imports it yet.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/utils/captionFilter.ts \
    frontend/src/components/t2i/CaptionFilterPopover.vue
git commit -m "feat(t2i): caption filter popover" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (real clicks on the real popover)

Nothing mounts the popover yet, so mount a throwaway demo from the browser console. Start the backend (`source venv/bin/activate && python run_server.py`) and the frontend (`cd frontend && npm run dev`), open http://localhost:5173, press F12 and paste:
```js
// Mounts a throwaway demo of the caption filter popover in the top-left corner of the page.
const main = await (await fetch('/src/main.ts')).text()
const vueUrl = main.match(/"([^"]*\/deps\/vue\.js\?v=[^"]+)"/)[1]
const { createApp, h, ref } = await import(vueUrl)
const Popover = (await import('/src/components/t2i/CaptionFilterPopover.vue')).default
const { fetchCaptionMeta, countCaptions } = await import('/src/api/t2i.ts')

document.getElementById('t2i-demo')?.remove()
const host = Object.assign(document.createElement('div'), { id: 't2i-demo' })
host.style.cssText = 'position:fixed;top:12px;left:12px;z-index:99999;display:flex;gap:12px;align-items:flex-start'
document.body.appendChild(host)

const meta = ref(await fetchCaptionMeta())
const filter = ref({})
const count = ref(null)
let timer
function refreshCount() {
  count.value = null
  clearTimeout(timer)
  timer = setTimeout(async () => { count.value = (await countCaptions(filter.value)).count }, 300)
}
refreshCount()

createApp({
  render: () =>
    h('div', { style: 'display:flex;gap:12px;align-items:flex-start' }, [
      h(Popover, {
        meta: meta.value,
        modelValue: filter.value,
        count: count.value,
        total: meta.value.total,
        'onUpdate:modelValue': (v) => { filter.value = v; refreshCount() },
        onClose: () => host.remove(),
      }),
      h('pre', { id: 't2i-demo-filter', style: 'margin:0;padding:10px;border-radius:8px;background:#111;color:#ddd;font-size:11px;min-width:200px' }, JSON.stringify(filter.value, null, 2)),
    ]),
}).mount(host)
```
A panel appears in the top-left corner with a dark JSON box on its right that shows the filter the popover emits. The panel has a header `Caption filter` with a ✕; Nudity (a checkbox and a count for each value); Artistic Quality, Erotic Score and Pornographic Score (two empty boxes each, showing the column's lowest and highest value as placeholders); Males and Females (the same, whole numbers); Aspect Ratio (checkboxes with counts); Clothing (a search box and a scrolling list with `Has any` / `Has none` columns); and a footer reading `<N> captions match of <total>` with a Clear button. Only the columns your CSV has are there. Then:
1. The footer reads `<total> captions match of <total>` (for a moment `... captions match` while the count request is out); Clear is greyed out; the JSON box shows `{}`.
2. Tick one Nudity value: a blue badge `1` appears beside the title, the JSON box shows `{"nudity": ["<value>"]}`, and about 0.3 s later the footer count equals the number printed beside that value.
3. Type `0.3` into the first Erotic Score box and press Tab: the JSON gains `"erotic_score": {"min": 0.3}`, the badge reads `2` and the footer count drops.
4. Type `0.5` into the second Erotic Score box and press Tab, then type `2` into the first box and press Tab: the first box settles on `0.5` (a minimum cannot pass the maximum). Type `7` into the second box and press Tab: it empties again (a limit beyond the column's range is dropped) and the JSON is back to `{"min": 0.5}`.
5. Type `1.6` into a Males box and press Tab: it settles on `2`.
6. Type `cor` into the clothing search: the list narrows to the items containing it. Tick `Has any` for one item: a chip `+ <item> ✕` appears above the search box. Tick `Has none` for the same item: the chip turns red (`− <item> ✕`) and its `Has any` box clears. Click the chip: the item is unticked.
7. Click Clear: every box empties, the badge and the chips disappear, the JSON box shows `{}`.
8. Press Esc with the cursor in the search box (or click ✕): the demo closes. Reloading the page also removes it.

---

### Task 21: T2IDialog form and batch controls, header entry

**Files:**
- Create: `frontend/src/utils/t2iDialogForm.ts` (the pure form rules)
- Create: `frontend/src/components/dialogs/T2IDialog.vue` (the form and its batch controls; the strip arrives in Task 22)
- Modify: `frontend/src/components/layout/ContentSearchBar.vue` (a `t2i` emit and a `pi-sparkles` button immediately left of Storyboards)
- Modify: `frontend/src/views/LibraryView.vue` (`t2iOpen`, the `@t2i` binding, the `<T2IDialog v-if>` mount beside the I2V dialog)
- Test: `frontend/t2i-checks.local/task18.mjs` (throwaway, git-ignored; needs the harness from Task 14)

I2V is not touched. The dialog follows I2VDialog's conventions (overlay, card, CSS variables, plain HTML controls), but is a separate component.

**Interfaces:**
- Consumes: the store of Task 16 (`open()`, `close()`, `config`, `captionMeta`, `randomBatch`, `currentStep`, `lastFinished`, `isBusy`, `statusText`, `startBatch`, `cancelAll`, `generatePrompt`, `rollCaption`, `countCaptions`, `resolveCaption`, `loadDraft`, `saveDraft`, `reattach`, `handleT2iEvent`, `handleComfyEvent`; `selectedId`, which is always null until Task 22); the helpers of Task 14 (`plannedImages`, `effectiveBatchSize`, `effectiveCount`, `isCountEffective`, `randomSeed`, `activeFilterCount`, `cleanFilter`, `isFilterEmpty`, `withCurrentOption`, `DEFAULT_MAX_BATCH_SIZE`, `DEFAULT_MAX_COUNT_PER_BATCH`, `SEED_MAX`); `SeedControls` (Task 15); `CaptionFilterPopover` (Task 20); `listPresets()` (`api/comfy.ts`), `useWebSocket`, `useToast`, `ApiError`, `LoraListEditor` (existing).
- Produces:
  - `frontend/src/utils/t2iDialogForm.ts`: `interface T2iFields { mode; filter: CaptionFilter; caption; model; presetId: number | null; megapixels; aspect; seed; prompt; negative; loras; seedPolicy; batchSize; countPerBatch }`; `defaultPresetFor(cfg, model, presetIds): number | null`; `initialFields(cfg, presetIds, seed): T2iFields`; `toFormState(fields): T2iFormState`; `mergeFormState(fields, stored: Partial<T2iFormState>): T2iFields`; `usableDraft(draft, cfg, presetIds): Partial<T2iFormState>`; `savableForm(state): Partial<T2iFormState>`; `interface BatchContext { hasNegative; maxBatchSize; maxCount }` and `buildBatchRequest(fields, ctx): T2iBatchRequest`; `requestSignature(fields, hasNegative): string`; `unlockPatch(finished, hasNegative): Partial<T2iFormState> | null`; `interface BlockerContext { ready; csvAvailable; randomRunning; submitting }` and `generateBlocker(fields, ctx): string | null` (the reason Generate is blocked, or null).
  - `<T2IDialog>`: no props; emits `close`. It closes only through its header ✕ (there is no `@click.self` on the overlay).
  - `ContentSearchBar` emits `t2i`; `LibraryView` opens the dialog with `t2iOpen`.

**Design notes.**
- One reactive `form` object holds every input, and it stays inert (a disabled `<fieldset>`) until the settings and the workflow list have loaded; only then does it take the configured defaults and the saved draft. The workflow list (every kind-`t2i` preset) is refetched each time the dialog opens.
- Choosing a model selects its default workflow in a change handler, never a watcher: a watcher would also fire when a saved form is loaded (Task 22) and silently swap the workflow that form was rendered with.
- The server owns a batch's loop. `buildBatchRequest` takes ONE snapshot of the form per Generate; nothing typed afterwards can reach a running batch. Manual sends its prompt, aspect ratio and (as provenance) its caption; Random sends only its filter. The counts sent are the EFFECTIVE ones: Manual is locked at Batch Size 1 and Fixed forces Count per Batch to 1 (the field keeps what was typed and is greyed with a note). The negative is sent only for a model that has one and only when written.
- While a Random batch runs, Caption / Prompt / Negative / Aspect / Seed are read-only live views of `store.currentStep`: a display only, so the form itself is untouched. When the batch ends `store.lastFinished` hands its last step to the form once (`unlockPatch`): Caption, Prompt and Aspect hold the last step and Seed shows the next unused seed. Generate stays disabled for the whole run.
- The duplicate-submit guard is Manual only and component-local: `requestSignature` covers everything that makes an image different (prompt, negative, seed, model, workflow, size, aspect, LoRAs) plus the seed policy and the effective count, so switching from one image to a run of several is not mistaken for a repeat. The caption is provenance only and is not in it.
- The scratch draft is written 0.5 s after the last change and again on close, never before the form has initialised (blank defaults must not overwrite a saved draft), and only from the fields an image also stores: seed policy, Batch Size and Count per Batch are session-local. A saved model, workflow or aspect ratio the current config no longer offers is dropped, and Random mode without a caption file falls back to Manual.
- The dice loads one caption AND its aspect ratio (a Random batch takes the ratio of each caption, and previewing one should show it). The filter popover's count request is debounced by 0.3 s; `store.countCaptions` answers null when superseded, which leaves the label as it was. If the filter options failed to load when the dialog opened (the store already toasted), opening the Filter panel asks again. The panel is `position: fixed` under the Filter button (right edges aligned, no taller than the room below it, recomputed on resize and when the dialog scrolls): inside the scrolling dialog card its footer, where the match count is, was cut off.
- Caption and Prompt are plain textareas: `TextEditPopup` collapses newlines on save and would flatten them.

Cross-checked while drafting: the real dialog ran in headless Chromium against a scratch backend (Playwright, 130 checks: entry button and tooltip, defaults, Resolved caption, Generate Prompt incl. a 502, the negative box, the exact Manual and Random request bodies, seed advance, the duplicate guard, Random locking and unlocking, cancel, 409 and 400 starts, filter and dice, the filter options retry, the draft, reopening during a Random batch, a reconnect); 17 deliberate mutants of the dialog each failed those checks and 16 mutants of the helpers, the header and the mount failed the check below.

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/task18.mjs`)

The pure rules run as plain modules with hand-written configs and forms. The dialog is loaded through the SSR compiler, the header is rendered with stand-ins for what `main.ts` registers globally (PrimeVue's `Button`, the tooltip directive, the router), and `LibraryView.vue` is read as text for its wiring.

`frontend/t2i-checks.local/task18.mjs`:
```js
// Task 21 checks: the pure form rules behind T2IDialog (utils/t2iDialogForm.ts),
// that the dialog compiles, and the entry (header button, LibraryView mount).
import { readFileSync } from 'node:fs'
import { createPinia } from 'pinia'
import { createSSRApp, h } from 'vue'
import { routerKey } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { assert, check, finish, load } from './harness.mjs'

const f = await load('/src/utils/t2iDialogForm.ts')

// ---- fixtures (hand-written) ----------------------------------------------------------

const cfg = (over = {}) => ({
  output_root: '',
  output_prefix: '/%Y-%m-%d/t2i_',
  megapixels: [0.5, 1, 1.5, 2],
  default_megapixels: 1.5,
  default_model: 'krea2',
  model_workflows: { krea2: 3, qwen: 4, sd: null, zimage: 99 },
  content_mode: 'uncensored',
  identity: {},
  window: 4,
  max_batch_size: 500,
  max_count_per_batch: 32,
  models: [
    { id: 'krea2', label: 'Krea 2', has_negative: false, identity: 'ref' },
    { id: 'qwen', label: 'Qwen-Image', has_negative: true, identity: 'ref' },
    { id: 'sd', label: 'SDXL', has_negative: true, identity: 'noun' },
    { id: 'zimage', label: 'Z-Image', has_negative: false, identity: 'ref' },
  ],
  aspect_ratios: ['1:1', '4:3', '3:4', '3:2', '2:3', '16:9'],
  seed_policies: ['fixed', 'increment', 'decrement', 'random'],
  seed_max: 2147483647,
  csv: { available: true, total: 1000, error: null },
  wildcards: { slots: [], warnings: [] },
  ...over,
})

const fields = (over = {}) => ({
  mode: 'manual',
  filter: {},
  caption: 'a woman on a bench',
  model: 'krea2',
  presetId: 3,
  megapixels: 1,
  aspect: '3:2',
  seed: 321424,
  prompt: 'A woman sits on a wooden bench.',
  negative: '',
  loras: [],
  seedPolicy: 'fixed',
  batchSize: 1,
  countPerBatch: 1,
  ...over,
})

const ctx = (over = {}) => ({ hasNegative: false, maxBatchSize: 500, maxCount: 32, ...over })

// ---- initialFields / defaultPresetFor ------------------------------------------------------

await check('initialFields takes the configured defaults', () => {
  const s = f.initialFields(cfg(), [3, 4], 777)
  assert.equal(s.mode, 'manual')
  assert.equal(s.model, 'krea2')
  assert.equal(s.presetId, 3)
  assert.equal(s.megapixels, 1.5)
  assert.equal(s.aspect, '3:2')
  assert.equal(s.seed, 777)
  assert.equal(s.seedPolicy, 'fixed')
  assert.equal(s.batchSize, 1)
  assert.equal(s.countPerBatch, 1)
  assert.deepEqual(s.filter, {})
  assert.deepEqual(s.loras, [])
  assert.equal(s.caption, '')
  assert.equal(s.prompt, '')
  assert.equal(s.negative, '')
})

await check('initialFields copes with a config that has no usable default', () => {
  const odd = cfg({ default_model: 'gone', aspect_ratios: ['16:9', '1:1'] })
  const s = f.initialFields(odd, [], 5)
  assert.equal(s.model, 'krea2', 'falls back to the first model')
  assert.equal(s.presetId, null, 'no registered workflow to select')
  assert.equal(s.aspect, '16:9', 'no 3:2 in the list: the first ratio')
  const none = f.initialFields(null, [], 5)
  assert.equal(none.model, '')
  assert.equal(none.presetId, null)
  assert.equal(none.megapixels, 1)
  assert.equal(none.aspect, '3:2')
  assert.equal(none.seed, 5)
})

await check('defaultPresetFor returns the model workflow only when that preset exists', () => {
  const c = cfg()
  assert.equal(f.defaultPresetFor(c, 'krea2', [3, 4]), 3)
  assert.equal(f.defaultPresetFor(c, 'qwen', [3, 4]), 4)
  assert.equal(f.defaultPresetFor(c, 'qwen', [3]), null, 'the configured preset was deleted')
  assert.equal(f.defaultPresetFor(c, 'sd', [3, 4]), null, 'no default configured')
  assert.equal(f.defaultPresetFor(c, 'zimage', [3, 4]), null, 'points at a preset that does not exist')
  assert.equal(f.defaultPresetFor(c, 'nope', [3, 4]), null)
  assert.equal(f.defaultPresetFor(null, 'krea2', [3]), null)
})

// ---- toFormState / mergeFormState -----------------------------------------------------------------

await check('toFormState maps the dialog fields onto the stored form_state', () => {
  const s = f.toFormState(fields({ filter: { nudity: ['none'] }, loras: [{ name: 'a.safetensors', strength: 0.5 }] }))
  assert.deepEqual(s, {
    mode: 'manual',
    filter: { nudity: ['none'] },
    caption: 'a woman on a bench',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    aspect_ratio: '3:2',
    seed: 321424,
    prompt: 'A woman sits on a wooden bench.',
    negative: '',
    loras: [{ name: 'a.safetensors', strength: 0.5 }],
  })
  assert.equal(f.toFormState(fields({ filter: {} })).filter, null, 'an empty filter is null')
  assert.equal(f.toFormState(fields({ filter: { nudity: [] } })).filter, null, 'so is one that restricts nothing')
})

await check('toFormState never carries the session-local fields and copies its lists', () => {
  const src = fields({ seedPolicy: 'increment', batchSize: 9, countPerBatch: 4, loras: [{ name: 'l', strength: 1 }] })
  const s = f.toFormState(src)
  for (const key of ['seedPolicy', 'seed_policy', 'batchSize', 'batch_size', 'countPerBatch', 'count_per_batch']) {
    assert.ok(!(key in s), `${key} is not part of the form state`)
  }
  s.loras[0].name = 'changed'
  assert.equal(src.loras[0].name, 'l')
})

await check('mergeFormState fills the fields from a stored state and keeps the session-local ones', () => {
  const current = fields({ seedPolicy: 'increment', batchSize: 9, countPerBatch: 4 })
  const stored = {
    mode: 'random',
    filter: { males: { min: 1 } },
    caption: 'stored caption',
    model: 'qwen',
    preset_id: 4,
    megapixels: 2,
    aspect_ratio: '2:3',
    seed: 42,
    prompt: 'stored prompt',
    negative: 'stored negative',
    loras: [{ name: 'x.safetensors', strength: 0.7 }],
  }
  const merged = f.mergeFormState(current, stored)
  assert.deepEqual(f.toFormState(merged), stored)
  assert.equal(merged.seedPolicy, 'increment')
  assert.equal(merged.batchSize, 9)
  assert.equal(merged.countPerBatch, 4)
  assert.equal(merged.mode, 'random', 'a stored Random mode is restored (and starts nothing)')
})

await check('mergeFormState keeps the current value where the stored one is null', () => {
  const current = fields()
  const merged = f.mergeFormState(current, {
    mode: 'manual',
    filter: null,
    caption: null,
    model: null,
    preset_id: null,
    megapixels: null,
    aspect_ratio: null,
    seed: null,
    prompt: null,
    negative: null,
    loras: [],
  })
  assert.equal(merged.model, 'krea2')
  assert.equal(merged.presetId, 3)
  assert.equal(merged.megapixels, 1)
  assert.equal(merged.aspect, '3:2')
  assert.equal(merged.seed, 321424)
  assert.equal(merged.caption, '', 'a stored null text is an empty box')
  assert.equal(merged.prompt, '')
  assert.deepEqual(merged.filter, {})
  assert.equal(f.mergeFormState(current, { model: '' }).model, 'krea2', 'an empty model is not a model')
})

await check('mergeFormState treats a missing key as "leave alone" and does not mutate its input', () => {
  const current = fields({ filter: { nudity: ['full'] }, loras: [{ name: 'a', strength: 1 }] })
  const snapshot = JSON.stringify(current)
  const merged = f.mergeFormState(current, { prompt: 'only this' })
  assert.equal(merged.prompt, 'only this')
  assert.equal(merged.caption, 'a woman on a bench')
  assert.deepEqual(merged.filter, { nudity: ['full'] })
  assert.deepEqual(merged.loras, [{ name: 'a', strength: 1 }])
  assert.equal(JSON.stringify(current), snapshot)
  const stored = { filter: { nudity: ['none', 'full'], males: {} }, loras: [{ name: 'z', strength: 2 }] }
  const again = f.mergeFormState(current, stored)
  again.loras[0].name = 'changed'
  assert.equal(stored.loras[0].name, 'z', 'loaded lists are copies')
  assert.deepEqual(again.filter, { nudity: ['none', 'full'] }, 'the stored filter is cleaned')
})

// ---- usableDraft ------------------------------------------------------------------------------------

await check('usableDraft drops what the current config or workflows can no longer honour', () => {
  const c = cfg()
  const draft = {
    mode: 'manual',
    model: 'gone',
    preset_id: 3,
    aspect_ratio: '7:5',
    megapixels: 1,
    seed: 5,
    prompt: 'kept',
  }
  const out = f.usableDraft(draft, c, [3, 4])
  assert.equal(out.model, undefined, 'unknown model is dropped')
  assert.equal(out.preset_id, undefined, 'and its workflow with it')
  assert.equal(out.aspect_ratio, undefined, 'unknown ratio')
  assert.equal(out.prompt, 'kept')
  assert.equal(out.seed, 5)
  assert.equal(out.megapixels, 1)
  const noPreset = f.usableDraft({ model: 'qwen', preset_id: 44 }, c, [3, 4])
  assert.equal(noPreset.model, 'qwen')
  assert.equal(noPreset.preset_id, undefined, 'a deleted workflow')
  assert.equal(f.usableDraft({ mode: 'random' }, cfg({ csv: { available: false, total: 0, error: 'x' } }), []).mode, 'manual')
  assert.equal(f.usableDraft({ mode: 'random' }, c, []).mode, 'random')
})

await check('usableDraft does not mutate the draft', () => {
  const draft = { model: 'gone', preset_id: 3 }
  f.usableDraft(draft, cfg(), [3])
  assert.deepEqual(draft, { model: 'gone', preset_id: 3 })
})

// ---- savableForm -------------------------------------------------------------------------------------

await check('savableForm leaves out fields that must not be sent', () => {
  const s = f.toFormState(fields())
  assert.deepEqual(f.savableForm(s), s)
  assert.equal('model' in f.savableForm({ ...s, model: '' }), false)
  assert.equal('megapixels' in f.savableForm({ ...s, megapixels: 0 }), false)
  assert.equal('megapixels' in f.savableForm({ ...s, megapixels: Number.NaN }), false)
  assert.equal('seed' in f.savableForm({ ...s, seed: 1.5 }), false)
  assert.equal('seed' in f.savableForm({ ...s, seed: -1 }), false)
  assert.equal('seed' in f.savableForm({ ...s, seed: 2 ** 31 }), false)
  assert.equal('aspect_ratio' in f.savableForm({ ...s, aspect_ratio: 'wide' }), false)
  assert.equal(f.savableForm({ ...s, seed: 0 }).seed, 0, 'seed 0 is valid')
  assert.equal(f.savableForm({ ...s, preset_id: null }).preset_id, null, 'null is a valid workflow')
})

// ---- buildBatchRequest ---------------------------------------------------------------------------------

await check('a Manual request carries the prompt, aspect and caption and no filter', () => {
  const req = f.buildBatchRequest(fields({ loras: [{ name: 'a.safetensors', strength: 0.8 }] }), ctx())
  assert.deepEqual(req, {
    mode: 'manual',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    seed: 321424,
    seed_policy: 'fixed',
    batch_size: 1,
    count_per_batch: 1,
    loras: [{ name: 'a.safetensors', strength: 0.8 }],
    prompt: 'A woman sits on a wooden bench.',
    aspect_ratio: '3:2',
    caption: 'a woman on a bench',
  })
})

await check('a Manual request locks Batch Size at 1 and sends the EFFECTIVE count', () => {
  const big = fields({ batchSize: 40, countPerBatch: 6, seedPolicy: 'increment' })
  const req = f.buildBatchRequest(big, ctx())
  assert.equal(req.batch_size, 1)
  assert.equal(req.count_per_batch, 6)
  assert.equal(req.seed_policy, 'increment')
  const fixed = f.buildBatchRequest(fields({ countPerBatch: 6, seedPolicy: 'fixed' }), ctx())
  assert.equal(fixed.count_per_batch, 1, 'Fixed forces one image per prompt')
  const clamped = f.buildBatchRequest(fields({ countPerBatch: 500, seedPolicy: 'random' }), ctx({ maxCount: 32 }))
  assert.equal(clamped.count_per_batch, 32)
  const low = f.buildBatchRequest(fields({ countPerBatch: 0, seedPolicy: 'decrement' }), ctx())
  assert.equal(low.count_per_batch, 1)
})

await check('a Manual request sends the negative only for a model that has one, and only when written', () => {
  const written = fields({ negative: 'blurry, lowres' })
  assert.equal(f.buildBatchRequest(written, ctx({ hasNegative: true })).negative, 'blurry, lowres')
  assert.ok(!('negative' in f.buildBatchRequest(written, ctx({ hasNegative: false }))))
  assert.ok(!('negative' in f.buildBatchRequest(fields({ negative: '  ' }), ctx({ hasNegative: true }))))
})

await check('a Manual request leaves the caption out when the box is empty', () => {
  const req = f.buildBatchRequest(fields({ caption: '   ' }), ctx())
  assert.ok(!('caption' in req))
})

await check('a Random request carries the cleaned filter and none of the Manual fields', () => {
  const req = f.buildBatchRequest(
    fields({
      mode: 'random',
      filter: { nudity: [], males: { min: 1 } },
      batchSize: 12,
      countPerBatch: 2,
      seedPolicy: 'increment',
    }),
    ctx({ hasNegative: true }),
  )
  assert.deepEqual(req, {
    mode: 'random',
    model: 'krea2',
    preset_id: 3,
    megapixels: 1,
    seed: 321424,
    seed_policy: 'increment',
    batch_size: 12,
    count_per_batch: 2,
    loras: [],
    filter: { males: { min: 1 } },
  })
  for (const key of ['prompt', 'negative', 'caption', 'aspect_ratio']) assert.ok(!(key in req), `no ${key}`)
})

await check('a Random request clamps Batch Size to the limit and Fixed still forces the count to 1', () => {
  const a = f.buildBatchRequest(fields({ mode: 'random', batchSize: 9999 }), ctx({ maxBatchSize: 500 }))
  assert.equal(a.batch_size, 500)
  assert.equal(a.count_per_batch, 1)
  assert.deepEqual(a.filter, {}, 'no filter means the whole file')
  const b = f.buildBatchRequest(fields({ mode: 'random', batchSize: 0, countPerBatch: 3, seedPolicy: 'random' }), ctx())
  assert.equal(b.batch_size, 1)
  assert.equal(b.count_per_batch, 3)
})

await check('the request is a snapshot: later edits to the form cannot reach it', () => {
  const src = fields({ loras: [{ name: 'a', strength: 1 }], mode: 'random', filter: { nudity: ['none'] } })
  const req = f.buildBatchRequest(src, ctx())
  src.loras[0].name = 'changed'
  src.filter.nudity.push('full')
  assert.equal(req.loras[0].name, 'a')
  assert.deepEqual(req.filter, { nudity: ['none'] })
})

await check('a request needs a workflow', () => {
  assert.throws(() => f.buildBatchRequest(fields({ presetId: null }), ctx()), /workflow/i)
})

// ---- requestSignature -----------------------------------------------------------------------------------

await check('requestSignature changes with everything that makes an image different', () => {
  const base = f.requestSignature(fields(), false)
  assert.equal(base, f.requestSignature(fields(), false), 'stable')
  const variants = {
    prompt: fields({ prompt: 'another' }),
    seed: fields({ seed: 1 }),
    model: fields({ model: 'qwen' }),
    workflow: fields({ presetId: 4 }),
    megapixels: fields({ megapixels: 2 }),
    aspect: fields({ aspect: '2:3' }),
    loras: fields({ loras: [{ name: 'a', strength: 1 }] }),
    lora_strength: fields({ loras: [{ name: 'a', strength: 0.5 }] }),
    policy: fields({ seedPolicy: 'increment' }),
    count: fields({ seedPolicy: 'increment', countPerBatch: 3 }),
  }
  const seen = new Set([base])
  for (const [name, v] of Object.entries(variants)) {
    const sig = f.requestSignature(v, false)
    assert.ok(!seen.has(sig), `${name} changes the signature`)
    seen.add(sig)
  }
})

await check('requestSignature ignores what does not change the image', () => {
  const base = f.requestSignature(fields(), false)
  assert.equal(f.requestSignature(fields({ caption: 'other caption' }), false), base, 'the caption is provenance only')
  assert.equal(f.requestSignature(fields({ negative: 'x' }), false), base, 'a negative the model would not get')
  assert.equal(f.requestSignature(fields({ batchSize: 30 }), false), base, 'Manual Batch Size is locked')
  assert.equal(f.requestSignature(fields({ countPerBatch: 5 }), false), base, 'a count Fixed overrides')
  assert.notEqual(f.requestSignature(fields({ negative: 'x' }), true), f.requestSignature(fields({ negative: 'y' }), true))
})

// ---- unlockPatch ----------------------------------------------------------------------------------------

const step = (over = {}) => ({
  step: 3,
  total_steps: 3,
  caption: 'the last drawn caption',
  aspect_ratio: '16:9',
  seed: 500,
  prompt: 'The last written prompt.',
  negative: 'lowres',
  warnings: [],
  ...over,
})
const finished = (over = {}) => ({
  batch_id: 'b1',
  mode: 'random',
  outcome: 'complete',
  images_done: 3,
  images_failed: 0,
  next_seed: 503,
  step: step(),
  error: null,
  ...over,
})

await check('unlockPatch hands a finished Random batch back to the form', () => {
  assert.deepEqual(f.unlockPatch(finished(), true), {
    caption: 'the last drawn caption',
    prompt: 'The last written prompt.',
    aspect_ratio: '16:9',
    negative: 'lowres',
    seed: 503,
  })
  const noNeg = f.unlockPatch(finished(), false)
  assert.ok(!('negative' in noNeg))
  assert.deepEqual(f.unlockPatch(finished({ next_seed: null }), false), {
    caption: 'the last drawn caption',
    prompt: 'The last written prompt.',
    aspect_ratio: '16:9',
  })
})

await check('unlockPatch is null when there is nothing to hand back', () => {
  assert.equal(f.unlockPatch(finished({ mode: 'manual' }), true), null)
  assert.equal(f.unlockPatch(finished({ step: null, next_seed: null }), true), null)
  assert.deepEqual(f.unlockPatch(finished({ step: null }), true), { seed: 503 })
  assert.ok(!('negative' in f.unlockPatch(finished({ step: step({ negative: null }) }), true)))
})

// ---- generateBlocker ------------------------------------------------------------------------------------

const bctx = (over = {}) => ({ ready: true, csvAvailable: true, randomRunning: false, submitting: false, ...over })

await check('generateBlocker names the first thing that stops a Generate', () => {
  assert.equal(f.generateBlocker(fields(), bctx()), null)
  assert.match(f.generateBlocker(fields(), bctx({ ready: false })), /loading/i)
  assert.match(f.generateBlocker(fields(), bctx({ randomRunning: true })), /random/i)
  assert.match(f.generateBlocker(fields(), bctx({ submitting: true })), /submitting/i)
  assert.match(f.generateBlocker(fields({ model: '' }), bctx()), /model/i)
  assert.match(f.generateBlocker(fields({ presetId: null }), bctx()), /workflow/i)
  assert.match(f.generateBlocker(fields({ prompt: '   ' }), bctx()), /prompt/i)
})

await check('generateBlocker: Random needs the caption file, not a prompt', () => {
  const random = fields({ mode: 'random', prompt: '' })
  assert.equal(f.generateBlocker(random, bctx()), null)
  assert.match(f.generateBlocker(random, bctx({ csvAvailable: false })), /caption file/i)
})

// ---- the dialog and its entry ---------------------------------------------------------------------------

await check('the dialog compiles to a component', async () => {
  const dialog = (await load('/src/components/dialogs/T2IDialog.vue')).default
  assert.equal(typeof dialog.setup, 'function')
})

await check('the header has a Text to Image button immediately left of Storyboards', async () => {
  // The models store the bar bootstraps opens the shared WebSocket: a stand-in, no network.
  globalThis.location = { protocol: 'http:', host: 'localhost' }
  globalThis.WebSocket = class {
    static OPEN = 1
    readyState = 0
    close() {}
  }
  const Bar = (await load('/src/components/layout/ContentSearchBar.vue')).default
  // PrimeVue's Button, the tooltip directive and the router are global in the real app: stand-ins here.
  const Button = {
    props: ['icon'],
    setup: (props, { attrs }) => () => h('button', { 'aria-label': attrs['aria-label'] }, [h('i', { class: props.icon })]),
  }
  const app = createSSRApp({ render: () => h(Bar) })
  app.use(createPinia())
  app.provide(routerKey, { push() {} })
  app.component('Button', Button)
  app.directive('tooltip', {})
  const html = await renderToString(app)
  const labels = [...html.matchAll(/aria-label="([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(labels.slice(-3), ['Config', 'Text to Image', 'Storyboards'])
  assert.match(html, /<i class="pi pi-sparkles"><\/i><\/button><button aria-label="Storyboards"/)
})

await check('LibraryView mounts the dialog behind t2iOpen and the bar opens it', () => {
  const view = readFileSync(new URL('../src/views/LibraryView.vue', import.meta.url), 'utf8')
  assert.match(view, /import T2IDialog from '\.\.\/components\/dialogs\/T2IDialog\.vue'/)
  assert.match(view, /const t2iOpen = ref\(false\)/)
  assert.match(view, /<ContentSearchBar[^>]*@t2i="t2iOpen = true"/s)
  assert.match(view, /<T2IDialog v-if="t2iOpen" @close="t2iOpen = false" \/>/)
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task18.mjs`
Expected: FAIL (exit code 1) — `Error: Failed to load url /src/utils/t2iDialogForm.ts (resolved id: /src/utils/t2iDialogForm.ts). Does the file exist?`
- [ ] **Step 3: Implement**

`frontend/src/utils/t2iDialogForm.ts`:
```ts
// The pure rules behind the Text to Image dialog's form: what a fresh form
// holds, how a stored form_state (an image's, or the localStorage draft) is
// loaded into it, what a Generate sends, and when Generate is blocked.
// Nothing here touches Vue, the store or the network, so the rules can be
// checked on their own.
import {
  SEED_MAX,
  cleanFilter,
  effectiveBatchSize,
  effectiveCount,
  isFilterEmpty,
  type CaptionFilter,
  type SeedPolicy,
  type T2iBatchRequest,
  type T2iConfig,
  type T2iFinishedBatch,
  type T2iFormState,
  type T2iLora,
  type T2iMode,
} from '../types/t2i'

/**
 * Every value the dialog's inputs hold. The first eleven are the editable copy
 * an image keeps (T2iFormState, see toFormState); seed policy, Batch Size and
 * Count per Batch are session-local: actions, not properties of an image, so
 * loading an image or a draft never touches them.
 */
export interface T2iFields {
  mode: T2iMode
  filter: CaptionFilter
  caption: string
  model: string
  presetId: number | null
  megapixels: number
  aspect: string
  seed: number
  prompt: string
  negative: string
  loras: T2iLora[]
  seedPolicy: SeedPolicy
  batchSize: number
  countPerBatch: number
}

const PREFERRED_ASPECT = '3:2'

/** The workflow the config names for a model, if that preset is still registered. */
export function defaultPresetFor(
  cfg: T2iConfig | null,
  model: string,
  presetIds: number[],
): number | null {
  const id = cfg?.model_workflows[model] ?? null
  return id !== null && presetIds.includes(id) ? id : null
}

/** A fresh scratch form: the configured defaults and the given seed. */
export function initialFields(cfg: T2iConfig | null, presetIds: number[], seed: number): T2iFields {
  const models = cfg?.models ?? []
  const model = models.some((m) => m.id === cfg?.default_model)
    ? (cfg?.default_model ?? '')
    : (models[0]?.id ?? '')
  const aspects = cfg?.aspect_ratios ?? []
  return {
    mode: 'manual',
    filter: {},
    caption: '',
    model,
    presetId: defaultPresetFor(cfg, model, presetIds),
    megapixels: cfg?.default_megapixels ?? 1,
    aspect: aspects.includes(PREFERRED_ASPECT) ? PREFERRED_ASPECT : (aspects[0] ?? PREFERRED_ASPECT),
    seed,
    prompt: '',
    negative: '',
    loras: [],
    seedPolicy: 'fixed',
    batchSize: 1,
    countPerBatch: 1,
  }
}

/** The dialog's fields in the shape an image stores (and autosave / the draft send). */
export function toFormState(f: T2iFields): T2iFormState {
  return {
    mode: f.mode,
    filter: isFilterEmpty(f.filter) ? null : cleanFilter(f.filter),
    caption: f.caption,
    model: f.model,
    preset_id: f.presetId,
    megapixels: f.megapixels,
    aspect_ratio: f.aspect,
    seed: f.seed,
    prompt: f.prompt,
    negative: f.negative,
    loras: f.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
}

/**
 * Loads a stored form state into the fields and returns the result (the
 * inputs are not mutated). A key that is absent, and a null model, workflow,
 * size, aspect ratio or seed, leaves the current value alone: an image
 * ingested without one of them has nothing better to offer. A null text or
 * filter means an empty box. Seed policy, Batch Size and Count per Batch are
 * never touched, so loading an image can never queue a large re-run.
 */
export function mergeFormState(f: T2iFields, s: Partial<T2iFormState>): T2iFields {
  return {
    ...f,
    mode: s.mode ?? f.mode,
    filter: s.filter === undefined ? f.filter : s.filter === null ? {} : cleanFilter(s.filter),
    caption: s.caption === undefined ? f.caption : (s.caption ?? ''),
    model: s.model || f.model,
    presetId: s.preset_id ?? f.presetId,
    megapixels: s.megapixels ?? f.megapixels,
    aspect: s.aspect_ratio ?? f.aspect,
    seed: s.seed ?? f.seed,
    prompt: s.prompt === undefined ? f.prompt : (s.prompt ?? ''),
    negative: s.negative === undefined ? f.negative : (s.negative ?? ''),
    loras: s.loras === undefined ? f.loras : s.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
}

/**
 * The part of a stored draft the current setup can still honour: a model that
 * is no longer configured (with its workflow), a workflow that was deleted, an
 * aspect ratio the list no longer has, and Random mode without a caption file
 * are dropped. Returns a copy.
 */
export function usableDraft(
  draft: Partial<T2iFormState>,
  cfg: T2iConfig,
  presetIds: number[],
): Partial<T2iFormState> {
  const out = { ...draft }
  if (out.model && !cfg.models.some((m) => m.id === out.model)) {
    delete out.model
    delete out.preset_id
  }
  if (out.preset_id != null && !presetIds.includes(out.preset_id)) delete out.preset_id
  if (out.aspect_ratio && !cfg.aspect_ratios.includes(out.aspect_ratio)) delete out.aspect_ratio
  if (out.mode === 'random' && !cfg.csv.available) out.mode = 'manual'
  return out
}

/**
 * What autosave may send: the whole form, minus fields the server would
 * reject (an empty model, a size that is not positive, a seed outside
 * 0..2^31-1, a malformed ratio), so one bad field never blocks the rest.
 */
export function savableForm(s: T2iFormState): Partial<T2iFormState> {
  const out: Partial<T2iFormState> = { ...s }
  if (!s.model) delete out.model
  if (!(typeof s.megapixels === 'number' && s.megapixels > 0)) delete out.megapixels
  if (!(Number.isInteger(s.seed) && (s.seed as number) >= 0 && (s.seed as number) <= SEED_MAX)) delete out.seed
  if (typeof s.aspect_ratio === 'string' && !/^\d+:\d+$/.test(s.aspect_ratio)) delete out.aspect_ratio
  return out
}

export interface BatchContext {
  /** The selected model takes a negative prompt. */
  hasNegative: boolean
  maxBatchSize: number
  maxCount: number
}

/**
 * The POST /api/t2i/batches body for a Generate: one snapshot of the fields,
 * so nothing the user types afterwards can leak into a batch the server is
 * running. Manual sends its prompt, aspect ratio and (as provenance) its
 * caption; Random sends its filter. The counts are the EFFECTIVE ones: Manual
 * is locked at Batch Size 1, and Fixed forces Count per Batch to 1.
 */
export function buildBatchRequest(f: T2iFields, ctx: BatchContext): T2iBatchRequest {
  if (f.presetId === null) throw new Error('Choose a workflow first')
  const req: T2iBatchRequest = {
    mode: f.mode,
    model: f.model,
    preset_id: f.presetId,
    megapixels: f.megapixels,
    seed: f.seed,
    seed_policy: f.seedPolicy,
    batch_size: effectiveBatchSize(f.mode, f.batchSize, ctx.maxBatchSize),
    count_per_batch: effectiveCount(f.seedPolicy, f.countPerBatch, ctx.maxCount),
    loras: f.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
  if (f.mode === 'manual') {
    req.prompt = f.prompt
    req.aspect_ratio = f.aspect
    if (f.caption.trim()) req.caption = f.caption
    if (ctx.hasNegative && f.negative.trim()) req.negative = f.negative
  } else {
    req.filter = cleanFilter(f.filter)
  }
  return req
}

/**
 * Everything that makes a Manual image different from the last one, as one
 * comparable string, for the "nothing has changed" confirmation. The caption
 * is provenance only and is left out; so is a negative the model would not
 * receive. The seed policy and the effective count are in it, so switching
 * from one image to a run of several is not mistaken for a repeat.
 */
export function requestSignature(f: T2iFields, hasNegative: boolean): string {
  return JSON.stringify({
    prompt: f.prompt,
    negative: hasNegative ? f.negative : null,
    seed: f.seed,
    model: f.model,
    preset: f.presetId,
    megapixels: f.megapixels,
    aspect: f.aspect,
    loras: f.loras.map((l) => [l.name, l.strength]),
    policy: f.seedPolicy,
    count: effectiveCount(f.seedPolicy, f.countPerBatch),
  })
}

/**
 * When a Random batch ends its boxes unlock: Caption, Prompt and Aspect hold
 * the last step's values (and the negative, for a model that has one) and Seed
 * shows the next unused seed. Null when the batch was not Random or there is
 * nothing to hand back.
 */
export function unlockPatch(finished: T2iFinishedBatch, hasNegative: boolean): Partial<T2iFormState> | null {
  if (finished.mode !== 'random') return null
  const patch: Partial<T2iFormState> = {}
  const step = finished.step
  if (step) {
    patch.caption = step.caption
    patch.prompt = step.prompt
    if (step.aspect_ratio) patch.aspect_ratio = step.aspect_ratio
    if (hasNegative && step.negative !== null) patch.negative = step.negative
  }
  if (finished.next_seed !== null) patch.seed = finished.next_seed
  return Object.keys(patch).length > 0 ? patch : null
}

export interface BlockerContext {
  /** The dialog has loaded its settings and initialised the form. */
  ready: boolean
  csvAvailable: boolean
  randomRunning: boolean
  submitting: boolean
}

/** Why Generate cannot be pressed right now, in words for the user; null when it can. */
export function generateBlocker(f: T2iFields, ctx: BlockerContext): string | null {
  if (!ctx.ready) return 'Still loading'
  if (ctx.randomRunning) return 'A Random batch is running'
  if (ctx.submitting) return 'Submitting'
  if (!f.model) return 'Choose a model'
  if (f.presetId === null) return 'Choose a workflow'
  if (f.mode === 'random') return ctx.csvAvailable ? null : 'The caption file is not available'
  return f.prompt.trim() ? null : 'Write or generate a prompt first'
}
```

`frontend/src/components/dialogs/T2IDialog.vue`:
```vue
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useDebounceFn, useEventListener } from '@vueuse/core'
import {
  DEFAULT_MAX_BATCH_SIZE,
  DEFAULT_MAX_COUNT_PER_BATCH,
  SEED_MAX,
  activeFilterCount,
  effectiveBatchSize,
  effectiveCount,
  isCountEffective,
  plannedImages,
  randomSeed,
  withCurrentOption,
} from '../../types/t2i'
import type { WorkflowPreset } from '../../types/storyboard'
import { ApiError } from '../../api/client'
import { listPresets } from '../../api/comfy'
import { useT2iStore } from '../../stores/t2i'
import { useToast } from '../../composables/useToast'
import { useWebSocket } from '../../composables/useWebSocket'
import {
  buildBatchRequest,
  defaultPresetFor,
  generateBlocker,
  initialFields,
  mergeFormState,
  requestSignature,
  toFormState,
  unlockPatch,
  usableDraft,
  type T2iFields,
} from '../../utils/t2iDialogForm'
import CaptionFilterPopover from '../t2i/CaptionFilterPopover.vue'
import SeedControls from '../generation/SeedControls.vue'
import LoraListEditor from '../storyboard/LoraListEditor.vue'

const emit = defineEmits<{ close: [] }>()

const store = useT2iStore()
const toast = useToast()

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e))

// ---- the form ---------------------------------------------------------------------
// One reactive object holds every input (utils/t2iDialogForm.ts owns the rules).
// It stays inert (`ready` false) until the settings and workflows have loaded,
// and only then takes the configured defaults and the saved draft.
const form = reactive<T2iFields>(initialFields(null, [], 0))
const ready = ref(false)
const presets = ref<WorkflowPreset[]>([])
// Signature of the last Manual submit this dialog made: component-local on
// purpose, so an earlier session never counts.
let lastSubmitted: string | null = null

const models = computed(() => store.config?.models ?? [])
const hasNegative = computed(() => models.value.find((m) => m.id === form.model)?.has_negative ?? false)
const csvAvailable = computed(() => store.config?.csv.available ?? false)
const maxBatchSize = computed(() => store.config?.max_batch_size ?? DEFAULT_MAX_BATCH_SIZE)
const maxCount = computed(() => store.config?.max_count_per_batch ?? DEFAULT_MAX_COUNT_PER_BATCH)
const seedMax = computed(() => store.config?.seed_max ?? SEED_MAX)

// A stored value that has since left a list must still be shown, not blanked.
const modelOptions = computed(() =>
  form.model && !models.value.some((m) => m.id === form.model)
    ? [...models.value, { id: form.model, label: `${form.model} (not configured)` }]
    : models.value,
)
const sizeOptions = computed(() => withCurrentOption(store.config?.megapixels ?? [1], form.megapixels))
const aspectOptions = computed(() => {
  const list = store.config?.aspect_ratios ?? []
  return shownAspect.value && !list.includes(shownAspect.value) ? [...list, shownAspect.value] : list
})
const presetMissing = computed(
  () => form.presetId !== null && !presets.value.some((p) => p.id === form.presetId),
)

async function loadPresets() {
  try {
    presets.value = (await listPresets()).filter((p) => p.kind === 't2i')
  } catch (e) {
    presets.value = []
    toast.show(`Couldn't load the workflows: ${errMsg(e)}`, 'warn', 5000)
  }
}

function initForm() {
  const cfg = store.config
  if (!cfg) return // the load failed: the Retry bar is showing
  const ids = presets.value.map((p) => p.id)
  const fresh = initialFields(cfg, ids, randomSeed(cfg.seed_max))
  const draft = store.loadDraft()
  Object.assign(form, draft ? mergeFormState(fresh, usableDraft(draft, cfg, ids)) : fresh)
  lastSubmitted = null
  ready.value = true
}

let disposed = false
onMounted(async () => {
  const workflows = loadPresets() // independent of the store's session
  await store.open() // never rejects: every loader reports its own failure
  await workflows
  if (!disposed) initForm()
})

async function retryLoad() {
  const workflows = loadPresets()
  await store.open()
  await workflows
  if (!disposed) initForm()
}

// Choosing a model selects its default workflow, when the config names one.
// A change handler, not a watcher: a watcher would also fire when a saved
// form is loaded, and silently swap the workflow it was rendered with.
function onModelChange(id: string) {
  form.model = id
  const preset = defaultPresetFor(store.config, id, presets.value.map((p) => p.id))
  if (preset !== null) form.presetId = preset
}

// ---- live connection --------------------------------------------------------------------
// useWebSocket needs the component setup context, so the dialog subscribes and
// forwards every frame to the store, which never subscribes itself.
const { connected } = useWebSocket('t2i', (event, data) => store.handleT2iEvent(event, data))
useWebSocket('comfy', (event, data) => store.handleComfyEvent(event, data))
// After a reconnect some frames were missed: rebuild from the server.
watch(connected, (up) => {
  if (up && ready.value) void store.reattach()
})

// ---- Random batches take over the boxes ----------------------------------------------------
// While a Random batch runs, Caption / Prompt / Negative / Aspect / Seed are
// read-only live views of its current step. They are a display only: the form
// itself is untouched until the batch ends, when the last step's values are
// copied in (a finished batch hands its values over exactly once).
const randomLocked = computed(() => store.randomBatch !== null)
const step = computed(() => (randomLocked.value ? store.currentStep : null))
const shownCaption = computed(() => (randomLocked.value ? (step.value?.caption ?? '') : form.caption))
const shownPrompt = computed(() => (randomLocked.value ? (step.value?.prompt ?? '') : form.prompt))
const shownNegative = computed(() => (randomLocked.value ? (step.value?.negative ?? '') : form.negative))
const shownAspect = computed(() => (step.value ? step.value.aspect_ratio : form.aspect))
const shownSeed = computed(() => (step.value ? step.value.seed : form.seed))
const stepWarnings = computed(() => step.value?.warnings ?? [])

watch(
  () => store.lastFinished,
  (finished) => {
    if (!finished || !ready.value) return
    const patch = unlockPatch(finished, hasNegative.value)
    if (patch) Object.assign(form, mergeFormState(form, patch))
    if (finished.outcome === 'complete') {
      const failed = finished.images_failed > 0 ? `, ${finished.images_failed} failed` : ''
      const n = finished.images_done
      toast.show(`Batch complete: ${n} image${n === 1 ? '' : 's'}${failed}`, finished.images_failed > 0 ? 'warn' : 'success')
    }
  },
)

// ---- caption, resolved caption, prompt -----------------------------------------------------------
const generating = ref(false)
const rolling = ref(false)
const promptWarnings = ref<string[]>([])
const negativeOpen = ref(false)

// The Resolved caption: the caption with its __TOKEN__s filled in for the
// current seed and model. `key` says what it was resolved for, so it can be
// shown dimmed while it is out of date and refreshed when it is open.
const resolvedOpen = ref(false)
const resolved = ref<{ key: string; text: string; warnings: string[] } | null>(null)
const resolvedKey = () => JSON.stringify([form.caption, form.seed, form.model])
const resolvedStale = computed(() => resolved.value !== null && resolved.value.key !== resolvedKey())

async function refreshResolved() {
  if (!form.caption.trim() || !form.model) {
    resolved.value = null
    return
  }
  const key = resolvedKey()
  if (resolved.value?.key === key) return
  const r = await store.resolveCaption({ caption: form.caption, seed: form.seed, model: form.model })
  if (r && resolvedKey() === key) resolved.value = { key, text: r.resolved_caption, warnings: r.warnings }
}
const refreshResolvedSoon = useDebounceFn(refreshResolved, 400)

watch(
  () => [form.caption, form.seed, form.model],
  () => {
    if (resolvedOpen.value && !randomLocked.value) void refreshResolvedSoon()
  },
)

function toggleResolved() {
  resolvedOpen.value = !resolvedOpen.value
  if (resolvedOpen.value) void refreshResolved()
}

// Review-only: writes the prompt into the box and shows what the caption
// resolved to, and never starts a render. A 502 means the language model
// failed (or timed out): the caption is fine and a retry usually works.
async function onGeneratePrompt() {
  if (!form.caption.trim() || !form.model || generating.value) return
  const body = { caption: form.caption, seed: form.seed, model: form.model }
  generating.value = true
  try {
    const r = await store.generatePrompt(body)
    form.prompt = r.prompt
    if (hasNegative.value && r.negative !== null) form.negative = r.negative
    promptWarnings.value = r.warnings
    resolved.value = { key: JSON.stringify([body.caption, body.seed, body.model]), text: r.resolved_caption, warnings: r.warnings }
    resolvedOpen.value = true
  } catch (e) {
    toast.show(
      e instanceof ApiError && e.status === 502
        ? `The prompt writer failed (${e.message}). Press Generate Prompt to try again.`
        : `Couldn't write the prompt: ${errMsg(e)}`,
      'warn',
      6000,
    )
  } finally {
    generating.value = false
  }
}

// The dice loads one caption (and its aspect ratio) from the caption file so
// it can be read and edited before anything is rendered.
async function onRoll() {
  if (rolling.value) return
  rolling.value = true
  try {
    const row = await store.rollCaption(form.filter)
    form.caption = row.caption
    if (store.config?.aspect_ratios.includes(row.aspect_ratio)) form.aspect = row.aspect_ratio
  } catch (e) {
    toast.show(
      e instanceof ApiError && e.status === 404
        ? 'No caption matches the current filter.'
        : `Couldn't load a caption: ${errMsg(e)}`,
      'warn',
    )
  } finally {
    rolling.value = false
  }
}

// ---- the caption filter ----------------------------------------------------------------------------
const filterOpen = ref(false)
const filterAnchor = ref<HTMLElement | null>(null)
const filterStyle = ref<Record<string, string>>({})
const filterCount = ref<number | null>(null)
const filterTotal = computed(() => store.captionMeta?.total ?? store.config?.csv.total ?? 0)
const filterActive = computed(() => activeFilterCount(form.filter))

// The store answers null for a failed or superseded request: the label then
// stays as it was ("..." if nothing had arrived).
const refreshFilterCount = useDebounceFn(async () => {
  const r = await store.countCaptions(form.filter)
  if (r) filterCount.value = r.count
}, 300)

// The panel is `position: fixed` under the Filter button, right edges aligned,
// and no taller than the room below it: inside the dialog card (which scrolls
// and clips) its footer, where the match count is, would be cut off.
function placeFilter() {
  const el = filterAnchor.value
  if (!el) return
  const box = el.getBoundingClientRect()
  const top = box.bottom + 6
  filterStyle.value = {
    top: `${top}px`,
    right: `${Math.max(8, window.innerWidth - box.right)}px`,
    maxHeight: `${Math.max(240, window.innerHeight - top - 16)}px`,
  }
}
useEventListener('resize', () => {
  if (filterOpen.value) placeFilter()
})

function askFilterCount() {
  filterCount.value = null
  void refreshFilterCount()
}
watch(() => form.filter, () => {
  if (filterOpen.value) askFilterCount()
})
watch(filterOpen, (open) => {
  if (!open) return
  // The filter options failed to load when the dialog opened (the store said
  // so): opening the panel is the moment to try again.
  if (!store.captionMeta && !store.metaLoading) void store.loadCaptionMeta()
  void nextTick(placeFilter)
  askFilterCount()
})

// ---- batch numbers ------------------------------------------------------------------------------------
const shownBatchSize = computed(() => effectiveBatchSize(form.mode, form.batchSize, maxBatchSize.value))
const countIgnored = computed(() => !isCountEffective(form.seedPolicy, form.countPerBatch))
const planned = computed(() =>
  plannedImages(form.mode, form.seedPolicy, form.batchSize, form.countPerBatch, {
    maxBatchSize: maxBatchSize.value,
    maxCount: maxCount.value,
  }),
)
const generateLabel = computed(() => (planned.value > 1 ? `Generate ×${planned.value}` : 'Generate'))

// A committed number settles on what will really be used (whole, within the limits).
function onBatchSize(e: Event) {
  const input = e.target as HTMLInputElement
  form.batchSize = effectiveBatchSize('random', Number(input.value), maxBatchSize.value)
  input.value = String(form.batchSize)
}
function onCountPerBatch(e: Event) {
  const input = e.target as HTMLInputElement
  form.countPerBatch = effectiveCount('increment', Number(input.value), maxCount.value)
  input.value = String(form.countPerBatch)
}

// ---- Generate / Cancel -----------------------------------------------------------------------------------
const submitting = ref(false)
const cancelling = ref(false)
const batchWarnings = ref<string[]>([])

const blocker = computed(() =>
  generateBlocker(form, {
    ready: ready.value,
    csvAvailable: csvAvailable.value,
    randomRunning: randomLocked.value,
    submitting: submitting.value,
  }),
)

function startErrorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return 'A Random batch is already running. Cancel it or wait for it to finish.'
  }
  return errMsg(e)
}

async function onGenerate() {
  if (blocker.value !== null) return
  // The server owns the run: one snapshot of the form goes out, and nothing
  // typed afterwards can reach a batch that is already running.
  const req = buildBatchRequest(form, {
    hasNegative: hasNegative.value,
    maxBatchSize: maxBatchSize.value,
    maxCount: maxCount.value,
  })
  const signature = req.mode === 'manual' ? requestSignature(form, hasNegative.value) : null
  if (
    signature !== null &&
    signature === lastSubmitted &&
    !confirm(
      'Nothing has changed since your last Generate — same seed, model, workflow, size, ' +
        'aspect ratio and prompt. This will render an identical image.\n\n' +
        'Generate it again anyway? (Cancel, then 🎲 for a new seed.)',
    )
  ) {
    return
  }
  submitting.value = true
  try {
    const r = await store.startBatch(req)
    if (signature !== null) lastSubmitted = signature
    batchWarnings.value = r.warnings
    promptWarnings.value = []
    // The box then shows the next unused seed (unless the user already changed it).
    if (req.mode === 'manual' && r.next_seed !== null && form.seed === req.seed) form.seed = r.next_seed
    toast.show(
      r.total_images === 1 ? 'Image job queued' : `${r.total_images} image jobs queued`,
      'success',
    )
  } catch (e) {
    toast.show(startErrorText(e), 'warn', 6000)
  } finally {
    submitting.value = false
  }
}

// Cancels every running batch (the server also cancels their ComfyUI jobs).
async function onCancel() {
  if (cancelling.value) return
  cancelling.value = true
  try {
    await store.cancelAll()
    toast.show('Cancelled', 'success')
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  } finally {
    cancelling.value = false
  }
}

// ---- the scratch-form draft ---------------------------------------------------------------------------------
// Per-viewer convenience: what is in the boxes survives closing the dialog
// (every storage access is guarded inside the store). It is written only
// while the form is a scratch area, and only once it has been initialised, so
// blank defaults can never overwrite a saved draft.
function saveDraftNow() {
  if (ready.value && store.selectedId === null) store.saveDraft(toFormState(form))
}
const saveDraftSoon = useDebounceFn(() => {
  if (!disposed) saveDraftNow()
}, 500)
watch(
  () => JSON.stringify(toFormState(form)),
  () => void saveDraftSoon(),
)

function close() {
  saveDraftNow()
  store.close()
  emit('close')
}

onBeforeUnmount(() => {
  saveDraftNow()
  disposed = true
})
</script>

<template>
  <!-- No @click.self close, deliberately: a stray click on the backdrop must
       not throw away a written prompt or hide running batches. The header ✕
       is the only way out. -->
  <div class="dialog-overlay">
    <div
      class="t2i-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="t2i-title"
      @scroll="filterOpen && placeFilter()"
    >
      <header class="t2i-header">
        <h3 id="t2i-title">Text to Image</h3>
        <button type="button" class="icon-btn" title="Close" aria-label="Close" @click="close()">✕</button>
      </header>

      <div v-if="!store.config && store.configError" class="bar failed" role="alert">
        <span>Couldn't load the Text to Image settings: {{ store.configError }}</span>
        <button type="button" class="link-btn" @click="retryLoad">Retry</button>
      </div>
      <p v-else-if="!ready" class="note">Loading…</p>

      <fieldset class="t2i-form" :disabled="!ready">
        <div class="grid-row">
          <label class="lbl" for="t2i-caption">Caption</label>
          <div class="stack">
            <textarea
              id="t2i-caption"
              class="area"
              rows="3"
              spellcheck="false"
              :value="shownCaption"
              :readonly="randomLocked"
              :placeholder="randomLocked ? 'A caption is drawn from the caption file for each step…' : 'Describe the image. Character tokens like __ALICE__ and __HAIR__ are filled in for you.'"
              @input="form.caption = ($event.target as HTMLTextAreaElement).value"
            />
            <div>
              <button
                type="button"
                class="link-btn fold"
                :aria-expanded="resolvedOpen"
                :disabled="randomLocked"
                @click="toggleResolved"
              >{{ resolvedOpen ? '▾' : '▸' }} Resolved caption</button>
            </div>
            <div v-if="resolvedOpen && !randomLocked" class="resolved">
              <p v-if="!form.caption.trim()" class="note">Nothing to resolve yet: write a caption first.</p>
              <template v-else-if="resolved">
                <textarea
                  class="area resolved-text"
                  :class="{ stale: resolvedStale }"
                  rows="4"
                  readonly
                  spellcheck="false"
                  aria-label="Resolved caption"
                  :value="resolved.text"
                />
                <ul v-if="resolved.warnings.length" class="lint">
                  <li v-for="w in resolved.warnings" :key="w">⚠ {{ w }}</li>
                </ul>
              </template>
              <p v-else class="note">Resolving…</p>
            </div>
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <div class="caption-actions">
            <button
              type="button"
              class="btn"
              :disabled="generating || randomLocked || !form.caption.trim() || !form.model"
              @click="onGeneratePrompt"
            >{{ generating ? 'Writing prompt…' : 'Generate Prompt' }}</button>
            <label class="radio">
              <input v-model="form.mode" type="radio" name="t2i-mode" value="manual" :disabled="randomLocked" />
              Manual
            </label>
            <label class="radio" :title="csvAvailable ? '' : (store.config?.csv.error ?? 'The caption file is not available')">
              <input
                v-model="form.mode"
                type="radio"
                name="t2i-mode"
                value="random"
                :disabled="randomLocked || !csvAvailable"
              />
              Random Caption
            </label>
            <button
              type="button"
              class="icon-btn dice"
              title="Load a random caption from the caption file"
              aria-label="Roll a random caption"
              :disabled="rolling || randomLocked || !csvAvailable"
              @click="onRoll"
            >🎲</button>
            <span ref="filterAnchor" class="filter-anchor">
              <button
                type="button"
                class="btn"
                :aria-expanded="filterOpen"
                :disabled="!csvAvailable"
                @click="filterOpen = !filterOpen"
              >Filter<span v-if="filterActive > 0" class="badge">{{ filterActive }}</span></button>
              <CaptionFilterPopover
                v-if="filterOpen"
                v-model="form.filter"
                class="filter-pop"
                :style="filterStyle"
                :meta="store.captionMeta"
                :count="filterCount"
                :total="filterTotal"
                @close="filterOpen = false"
              />
            </span>
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <div class="params">
            <label class="fld">
              <span>Model</span>
              <select :value="form.model" @change="onModelChange(($event.target as HTMLSelectElement).value)">
                <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
              </select>
            </label>
            <label class="fld">
              <span>Workflow</span>
              <select v-model="form.presetId">
                <option :value="null" disabled>Choose a workflow…</option>
                <option v-if="presetMissing" :value="form.presetId" disabled>Workflow #{{ form.presetId }} (not found)</option>
                <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
              <small v-if="ready && !presets.length" class="hint">none registered: add one under Configuration</small>
            </label>
            <label class="fld">
              <span>Size</span>
              <select v-model="form.megapixels">
                <option v-for="m in sizeOptions" :key="m" :value="m">{{ m }} MP</option>
              </select>
            </label>
            <label class="fld">
              <span>Aspect Ratio</span>
              <select
                :value="shownAspect"
                :disabled="randomLocked"
                :title="form.mode === 'random' ? 'A Random batch takes the aspect ratio of each caption' : ''"
                @change="form.aspect = ($event.target as HTMLSelectElement).value"
              >
                <option v-for="a in aspectOptions" :key="a" :value="a">{{ a }}</option>
              </select>
              <small v-if="form.mode === 'random' && !randomLocked" class="hint">taken from each caption</small>
            </label>
            <SeedControls
              :seed="shownSeed"
              :policy="form.seedPolicy"
              :disabled="randomLocked"
              :max="seedMax"
              @update:seed="form.seed = $event"
              @update:policy="form.seedPolicy = $event"
            />
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <LoraListEditor label="LoRAs" :entries="form.loras" @change="form.loras = $event" />
        </div>

        <div class="grid-row">
          <label class="lbl" for="t2i-prompt">Prompt</label>
          <div class="stack">
            <textarea
              id="t2i-prompt"
              class="area"
              rows="7"
              spellcheck="false"
              :value="shownPrompt"
              :readonly="randomLocked"
              :placeholder="randomLocked ? 'A prompt is written for each step…' : 'The text the image model receives. Write it here, or press Generate Prompt to have it written from the caption.'"
              @input="form.prompt = ($event.target as HTMLTextAreaElement).value"
            />
            <ul v-if="promptWarnings.length || stepWarnings.length" class="lint">
              <li v-for="w in [...promptWarnings, ...stepWarnings]" :key="w">⚠ {{ w }}</li>
            </ul>
            <template v-if="hasNegative">
              <div>
                <button
                  type="button"
                  class="link-btn fold"
                  :aria-expanded="negativeOpen"
                  @click="negativeOpen = !negativeOpen"
                >{{ negativeOpen ? '▾' : '▸' }} Negative prompt{{ shownNegative.trim() ? ' •' : '' }}</button>
              </div>
              <textarea
                v-if="negativeOpen"
                class="area"
                rows="3"
                spellcheck="false"
                aria-label="Negative prompt"
                :value="shownNegative"
                :readonly="randomLocked"
                @input="form.negative = ($event.target as HTMLTextAreaElement).value"
              />
            </template>
          </div>
        </div>
      </fieldset>

      <div class="grid-row">
        <span class="lbl" />
        <div class="footer">
          <div class="actions">
            <button type="button" class="btn primary" :disabled="blocker !== null" @click="onGenerate">
              {{ submitting ? 'Submitting…' : generateLabel }}
            </button>
            <button
              type="button"
              class="btn danger"
              :disabled="!store.isBusy || cancelling"
              :title="store.isBusy ? 'Stop the running batches and drop their queued jobs' : 'Nothing is running'"
              @click="onCancel"
            >{{ cancelling ? 'Cancelling…' : 'Cancel' }}</button>
            <span v-if="blocker !== null && blocker !== 'Still loading'" class="hint">{{ blocker }}</span>
          </div>
          <div class="counts">
            <label
              class="fld fld-inline"
              title="Random Caption: how many captions to draw. Manual always renders one prompt."
            >
              <span>Batch Size</span>
              <input
                type="number"
                min="1"
                :max="maxBatchSize"
                step="1"
                :value="shownBatchSize"
                :disabled="form.mode === 'manual'"
                @change="onBatchSize"
              />
            </label>
            <label
              class="fld fld-inline"
              :class="{ 'fld-off': countIgnored }"
              :title="countIgnored ? 'Ignored while the seed is Fixed: the same seed would render the same image again' : 'How many images to render for each prompt'"
            >
              <span>Count Per Batch</span>
              <input
                type="number"
                min="1"
                :max="maxCount"
                step="1"
                :value="form.countPerBatch"
                @change="onCountPerBatch"
              />
            </label>
          </div>
        </div>
      </div>

      <div class="grid-row">
        <span class="lbl" />
        <div class="stack">
          <p class="status" role="status" aria-live="polite">{{ store.statusText }}</p>
          <ul v-if="batchWarnings.length" class="lint">
            <li v-for="w in batchWarnings" :key="w">⚠ {{ w }}</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.t2i-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 20px 26px 22px;
  width: min(1080px, 96vw);
  max-height: 94vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.t2i-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.t2i-header h3 {
  margin: 0;
  font-size: 18px;
  color: var(--text-color);
}

.icon-btn {
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
}

.icon-btn:hover:not(:disabled) {
  color: var(--text-color);
}

.icon-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.dice {
  font-size: 18px;
}

/* A fieldset only to switch every control off at once while the dialog loads. */
.t2i-form {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Label column, then the content (the wireframe's layout). */
.grid-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  column-gap: 12px;
  align-items: start;
}

.lbl {
  padding-top: 6px;
  text-align: right;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.area {
  width: 100%;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
  resize: vertical;
}

.area:read-only {
  color: var(--text-color-secondary);
}

.area:focus {
  outline: none;
  border-color: var(--primary-color);
}

.resolved-text {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.resolved-text.stale {
  opacity: 0.55;
}

.caption-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}

.radio {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
}

.filter-anchor {
  position: relative;
  margin-left: auto;
}

.filter-pop {
  position: fixed;
  z-index: 20;
}

.badge {
  margin-left: 6px;
  min-width: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--primary-color);
  color: var(--primary-color-text, #fff);
  font-size: 11px;
  line-height: 18px;
  display: inline-block;
  text-align: center;
}

.params {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
}

.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.fld select,
.fld input {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.fld select:disabled,
.fld input:disabled {
  opacity: 0.6;
}

.fld-off {
  opacity: 0.55;
}

.hint {
  color: var(--text-color-secondary);
  font-size: 11px;
}

.btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
}

.btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.btn.danger {
  border-color: #c33;
  color: #c33;
}

.btn.danger:disabled {
  border-color: var(--surface-border);
  color: var(--text-color-secondary);
}

.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}

.footer {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px 24px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.counts {
  display: flex;
  gap: 16px;
}

.fld-inline {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.fld-inline input {
  width: 72px;
}

.status {
  min-height: 1.4em;
  margin: 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.note {
  margin: 0;
  color: var(--text-color-secondary);
  font-size: 12px;
}

.lint {
  margin: 0;
  padding-left: 0;
  list-style: none;
  color: var(--warn, #c90);
  font-size: 12px;
}

.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-left: 3px solid var(--primary-color, #6366f1);
}

.bar.failed {
  border-left-color: #c33;
  color: #c33;
}

.link-btn {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: 12px;
  color: var(--primary-color, #6366f1);
  white-space: nowrap;
}

.link-btn:hover:not(:disabled) {
  text-decoration: underline;
}

.link-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.fold {
  color: var(--text-color-secondary);
}
</style>
```

`frontend/src/components/layout/ContentSearchBar.vue` and `frontend/src/views/LibraryView.vue` are existing files: apply their diffs with `git apply`.
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/components/layout/ContentSearchBar.vue b/frontend/src/components/layout/ContentSearchBar.vue
index 93bd028..889861d 100644
--- a/frontend/src/components/layout/ContentSearchBar.vue
+++ b/frontend/src/components/layout/ContentSearchBar.vue
@@ -10,6 +10,7 @@ const emit = defineEmits<{
   'find-duplicates': []
   'similarity-settings': []
   config: []
+  t2i: []
 }>()
 
 const modelsStore = useModelsStore()
@@ -85,6 +86,15 @@ function openStoryboards() {
         aria-label="Config"
         @click="emit('config')"
       />
+      <Button
+        v-tooltip.bottom="'Text to Image'"
+        icon="pi pi-sparkles"
+        severity="secondary"
+        text
+        rounded
+        aria-label="Text to Image"
+        @click="emit('t2i')"
+      />
       <Button
         v-tooltip.bottom="'Storyboards'"
         icon="pi pi-images"
PATCH
```
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/views/LibraryView.vue b/frontend/src/views/LibraryView.vue
index d38b45e..3e9e4df 100644
--- a/frontend/src/views/LibraryView.vue
+++ b/frontend/src/views/LibraryView.vue
@@ -23,6 +23,7 @@ import DuplicateFinder from '../components/dialogs/DuplicateFinder.vue'
 import UpscaleDialog from '../components/dialogs/UpscaleDialog.vue'
 import PromptPlayground from '../components/dialogs/PromptPlayground.vue'
 import I2VDialog from '../components/dialogs/I2VDialog.vue'
+import T2IDialog from '../components/dialogs/T2IDialog.vue'
 import UpscaleQueue from '../components/dialogs/UpscaleQueue.vue'
 import ConfigDialog from '../components/dialogs/ConfigDialog.vue'
 import ScopeBreadcrumb from '../components/layout/ScopeBreadcrumb.vue'
@@ -61,6 +62,7 @@ const upscaleTargets = ref<Media[]>([])
 const playgroundMedia = ref<Media | null>(null)
 const i2vMedia = ref<Media | null>(null)
 const configOpen = ref(false)
+const t2iOpen = ref(false)
 
 onMounted(async () => {
   await Promise.all([
@@ -193,6 +195,7 @@ watch(isMobile, () => {
             @find-duplicates="dupFinderOpen = true"
             @similarity-settings="simSettingsOpen = true"
             @config="configOpen = true"
+            @t2i="t2iOpen = true"
           />
           <ViewMenubar @slideshow="openSlideshow" />
           <ScopeBreadcrumb />
@@ -291,6 +294,9 @@ watch(isMobile, () => {
     <!-- Image to Video dialog -->
     <I2VDialog v-if="i2vMedia" :media="i2vMedia" @close="i2vMedia = null" />
 
+    <!-- Text to Image dialog -->
+    <T2IDialog v-if="t2iOpen" @close="t2iOpen = false" />
+
     <!-- Config dialog -->
     <ConfigDialog
       v-if="configOpen"
PATCH
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task18.mjs`
Expected: `28 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 719ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build prints it too).
- [ ] **Step 6: Commit**
```bash
git add frontend/src/utils/t2iDialogForm.ts \
    frontend/src/components/dialogs/T2IDialog.vue \
    frontend/src/components/layout/ContentSearchBar.vue \
    frontend/src/views/LibraryView.vue
git commit -m "feat(t2i): Text to Image dialog form, batch controls and header entry" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (the real dialog, one image at a time)

Prerequisites: the backend and `npm run dev` running as in Task 20, ComfyUI running with a registered kind-`t2i` workflow (for example `Krea2 T2I API`), and for the Random part the caption file in `data/t2i_captions/`. Open http://localhost:5173.
1. In the header, between the cog and the Storyboards icon, there is a new sparkles icon; hovering it shows `Text to Image`. Click it. A dialog opens with, top to bottom: a Caption box and `▸ Resolved caption`; `Generate Prompt`, the radios `Manual` / `Random Caption`, a dice and `Filter`; Model (`Krea 2`), Workflow, Size (`1 MP` unless your config says otherwise), Aspect Ratio (`3:2`) and Seed (a number, a dice, `Fixed`); LoRAs; a tall Prompt box; `Generate` (greyed, with the hint `Write or generate a prompt first`), `Cancel` (greyed), Batch Size (greyed at `1`) and Count Per Batch. Click the dark backdrop: nothing happens. Only the ✕ closes the dialog.
2. Type a caption such as `a woman on a bench at __ALICE__` and click `▸ Resolved caption`: a read-only box shows the caption with the token replaced by a description. Edit the caption: the box follows a moment later.
3. Click `Generate Prompt`. The button reads `Writing prompt…` (the first call loads the language model and can take a minute), then the Prompt box holds the prompt and the resolved caption is open. Without an installed model the prompt is the resolved caption and a `⚠` line says so. A failure shows a toast `The prompt writer failed (…). Press Generate Prompt to try again.` and leaves the Prompt box as it was.
4. Choose a Workflow if the box is empty. Set the policy to `Increment` and Count Per Batch to `2`: the button reads `Generate ×2`. Click it: a toast `2 image jobs queued`, the seed box jumps by 2, the status line reads `rendering` (later `1/2 images · rendering`) and Cancel turns red. When it ends a toast reads `Batch complete: 2 images` and the two images appear in the library grid behind the dialog. Switch the policy back to `Fixed`: Count Per Batch greys out (it keeps its `2`) and the button is a plain `Generate`. Click it twice without changing anything: the second click asks `Nothing has changed since the last image this dialog rendered or loaded…`; Cancel sends nothing.
5. Choose the model `Qwen-Image` or `SDXL` (a model with a negative prompt): a `▸ Negative prompt` line appears under the Prompt box. With `Krea 2` it is absent. A model with no default workflow in Settings leaves the Workflow box alone.
6. Click `Filter`: a panel with the Task 20 fields and a live `<N> captions match of <total>` opens under the button. Tick a value, then close the panel: `Filter` shows a badge. Click the dice: a caption from the file (and its aspect ratio) appears in the Caption box.
7. Select `Random Caption`: Batch Size unlocks and a note `taken from each caption` appears under Aspect Ratio. Set Batch Size `3`, Count Per Batch `1`, policy `Increment` and click `Generate ×3`. While it runs, Caption, Prompt, Aspect Ratio and Seed follow the current step and cannot be edited, Generate is disabled (`A Random batch is running`), the radios are locked and the status line reads `Batch 1/3 · writing prompts`, then `rendering`. When it ends the boxes unlock holding the last caption, prompt and aspect ratio, and the seed shows the next unused one. Cancel stops a run. (A second Random batch started from another browser tab is refused with the toast `A Random batch is already running…`.)
8. Type something, close the dialog with ✕ and open it again: the caption, prompt, model and workflow are back (seed policy and the two counts start fresh).

---

### Task 22: T2IDialog strip, per-image autosave, viewer and the library refresh bridge

**Files:**
- Modify: `frontend/src/components/dialogs/T2IDialog.vue` (the strip with job tiles, selection and autosave, the Editing bar, favourite and delete, the viewer snapshot)
- Modify: `frontend/src/stores/media.ts` (`loadAllMedia` takes an optional `{ silent?: boolean }`)
- Modify: `frontend/src/stores/t2i.ts` (expose `scheduleMediaReload`, which now reloads silently)
- Modify: `frontend/src/App.vue` (the always-on `t2i` bridge also reloads the library grid)
- Test: `frontend/t2i-checks.local/task19.mjs` (throwaway, git-ignored; needs the harness from Task 14)

**Order.** This task consumes Task 19: `useFoldersStore().refreshT2iPaths()` and the `useWebSocket('t2i', …)` bridge in `App.vue` that Task 19 writes and this task extends. Apply it after Task 19 (Tasks 17 and 18 need nothing from it).

**Interfaces:**
- Consumes: `ThumbStrip`, `JobTile`, `useFormAutosave` (Task 15); the store of Task 16 (`images`, `imagesLoaded`, `imagesLoading`, `imagesError`, `hasMoreImages`, `loadingOlder`, `loadOlder`, `refreshImages`, `jobs`, `isBusy`, `selectedId`, `selectedImage`, `selectImage`, `clearSelection`, `saveFormState`, `toggleFavorite`, `deleteImage`, `cancelJob`, `dismissJob`, `randomBatch`); `toStripItem`, `t2iImageToMedia`, `formatT2iTimestamp` (Task 14); `savableForm`, `mergeFormState`, `requestSignature` (Task 21); `MediaViewer` (existing, `allowDestructive` prop); `useMediaStore().loadAllMedia()` and its `loading` flag, which drives LibraryView's dimmed "Loading media…" overlay (existing); `useFoldersStore().refreshT2iPaths()` (Task 19).
- Produces: `useMediaStore().loadAllMedia(options?: { silent?: boolean }): Promise<void>` (no argument: exactly as before; `{ silent: true }`: the same reload without touching `loading`); `useT2iStore().scheduleMediaReload(): Promise<void>`, the store's one debounced (1.5 s, at most 5 s) and silent library-grid reload, now part of its public surface; the dialog's strip and per-image editing behaviour; `App.vue`'s bridge calling `refreshT2iPaths()` and `scheduleMediaReload()` on every `t2i_images_changed`.

**Design notes.**
- The strip is `ThumbStrip` with a `#jobs` slot of `JobTile`s. "No images yet" shows only when the first list load has landed, no refetch is in flight and no batch or job is busy (a job tile can vanish before the refetch lands). A failed list load shows a line with Retry.
- Clicking an image loads its stored `form_state` and starts autosaving into it: `flush()` first (pending edits belong to the image being left), then `store.selectImage`, `mergeFormState`, and `markLoaded()`, so loading never echoes back as a save (the autosave compares the form with the snapshot `markLoaded` takes). Seed policy, Batch Size and Count per Batch are never restored, and a stored Random mode only restores the mode and its filter: nothing starts. Clicking the already selected image does nothing, and the strip reports only the first click of a double-click, so opening the viewer never selects twice. Loading an image arms the duplicate-submit guard: an untouched Generate would render the same image again.
- While an image is selected the Editing bar says where edits go and offers `Stop editing` (flush, then the form keeps its values as a scratch area). A failed save turns the bar into `Couldn't save your changes to this image…` and the next edit retries. Edits are also flushed on switching images, on closing the dialog and on unmount.
- A batch start lets go of the image being edited. Once the server has accepted a Generate (Manual or Random), the image being edited is saved (`flush()`: pending edits go in as usual) and released (`clearSelection()`) BEFORE the seed advance, or a Random run's live values, touch the form. Nothing automatic can then reach an image's saved form, which stays what produced it; the form keeps its values as a scratch area (and the duplicate-submit guard keeps working on them). A refused start changes nothing. Images cannot be loaded while a Random batch runs (the boxes are live views), the unlock never overwrites an image being edited, and a Generate Prompt or dice result that arrives after another image was loaded is dropped (a note explains a prompt), so a late answer can never edit, and autosave into, an image the user did not ask about.
- Delete confirms, drops a pending save for that image, deletes, and calls `foldersStore.refreshT2iPaths()` (the smart-folder rule's paths); the star toggles the library's favourite flag.
- The viewer is `MediaViewer` (`allowDestructive` false) over a snapshot taken when it opens, so images arriving mid-batch cannot shift its index.
- `scheduleMediaReload` was internal to the store. `App.vue`'s bridge is always on and the dialog only forwards frames while it is open, so a batch that finishes after the dialog closed never reached the library grid. Both paths now call this one debounced function: the grid gains the images with the dialog closed, and an open dialog does not reload twice.
- The bridge's reload is silent. `loadAllMedia()` raised `loading` for every reload, and `loading` drives the library's dimmed "Loading media…" overlay, so a long batch behind a closed dialog would flash it every few seconds on a big library. `loadAllMedia(options?: { silent?: boolean })` leaves `loading` alone when `options?.silent === true`. Only a literal `true` counts: a caller that passes anything else (say an Event from a handler bound straight to the function) still gets the overlay, and every existing caller passes nothing. `scheduleMediaReload` passes `{ silent: true }`. A silent reload that finishes first never lowers the flag of a normal reload still in flight, and a failed silent reload still rejects (the store logs it).

Cross-checked while drafting: the real app ran in headless Chromium against a scratch backend (Playwright, 117 checks: paging, the empty state, selection without an autosave echo, autosave, switch / Stop editing / close flushes, a failing save, a value the server would reject, a stored Random image, null model, favourite, delete with the smart-folder refetch, the frozen viewer, the library gaining images with the dialog closed without the overlay ever appearing (a normal refresh still shows it) and exactly one reload with it open, a batch start letting go of the loaded image with its saved seed unchanged, a pending edit saved first, a refused start changing nothing and the duplicate guard still asking, a late prompt, job tiles); 17 deliberate mutants of the dialog, the media store and the T2I store each failed those checks and 16 of the media store, the strip, the bridge and the release failed the check below.

- [ ] **Step 1: Write the failing check** (`frontend/t2i-checks.local/task19.mjs`)

The media store, the T2I store and `App.vue` run on a real Pinia with a stand-in socket (frames are pushed through the very `useWebSocket` the app uses) and a fake `fetch` that counts, holds or fails library reloads; the overlay flag is watched synchronously, so every flip is seen. The dialog is rendered to a string from a prepared store, and its source is read as text for the order of the release and the seed advance.

`frontend/t2i-checks.local/task19.mjs`:
```js
// Task 22 checks: (1) the media store's silent reload never raises the flag
// behind the "Loading media..." overlay, and every other caller still does;
// (2) the library reload for new T2I images is ONE debounced, silent function
// shared by the dialog's forwarder and App.vue's always-on bridge, so images a
// batch finishes after the dialog is closed still reach the grid and an open
// dialog does not reload twice; (3) the dialog's strip, Editing bar and job
// tiles, rendered to a string from a prepared store; (4) a batch start lets go
// of the edited image before the seed advance (wiring, read as text).
import { readFileSync } from 'node:fs'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createSSRApp, h, watch } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { assert, check, deferred, finish, installFetch, installLocalStorage, load, sleep } from './harness.mjs'

installLocalStorage()

// The bridge subscribes through useWebSocket: give it a socket we can push frames through.
const sockets = []
globalThis.location = { protocol: 'http:', host: 'localhost' }
globalThis.WebSocket = class {
  static OPEN = 1
  readyState = 1
  constructor(url) {
    this.url = url
    sockets.push(this)
  }
  close() {}
}
const deliver = (channel, event, data = {}) =>
  sockets.at(-1).onmessage({ data: JSON.stringify({ channel, event, data }) })

// The library endpoint can be held (mediaGate) or made to fail (mediaStatus); everything else is empty.
let mediaGate = null
let mediaStatus = 200
const libraryRow = {
  file_path: '/out/a.png',
  file_name: 'a.png',
  is_favorite: false,
  is_video: false,
  playback_speed: null,
  width: 1,
  height: 1,
  file_size: 1,
  frame_rate: null,
  duration: null,
  media_type: 'image',
}
const calls = installFetch(async (c) => {
  if (c.url.startsWith('/api/media')) {
    if (mediaGate) await mediaGate.promise
    return mediaStatus === 200 ? { json: [libraryRow] } : { status: mediaStatus, json: { detail: 'boom' } }
  }
  return { json: [] }
})
const libraryReloads = () => calls.filter((c) => c.url.startsWith('/api/media')).length

// The debounce is 1.5 s in the store; wait a little longer than that.
const DEBOUNCE_SETTLE_MS = 1900

// ---- the store ---------------------------------------------------------------------------------

const pinia = createPinia()
setActivePinia(pinia)
const { useMediaStore } = await load('/src/stores/media.ts')
const { useT2iStore } = await load('/src/stores/t2i.ts')
const media = useMediaStore()
const store = useT2iStore()

// Every change of the flag behind the dimmed "Loading media..." overlay, in order.
const flips = []
watch(() => media.loading, (value) => flips.push(value), { flush: 'sync' })
const flipsDuring = async (fn) => {
  flips.length = 0
  await fn()
  return [...flips]
}

await check('a default library reload raises the overlay flag and lowers it again', async () => {
  media.allMedia = []
  assert.deepEqual(await flipsDuring(() => media.loadAllMedia()), [true, false])
  assert.equal(media.allMedia.length, 1)
})

await check('a silent reload loads the library without ever touching the flag', async () => {
  media.allMedia = []
  assert.deepEqual(await flipsDuring(() => media.loadAllMedia({ silent: true })), [])
  assert.equal(media.loading, false)
  assert.equal(media.allMedia.length, 1, 'the library was still reloaded')
})

await check('only a literal true is silent: an Event, {}, 1 or "true" are not', async () => {
  const notSilent = [
    ['no argument', undefined],
    ['an empty object', {}],
    ['silent: false', { silent: false }],
    ['silent: 1', { silent: 1 }],
    ['silent: "true"', { silent: 'true' }],
    ['a click Event', new Event('click')],
  ]
  for (const [label, arg] of notSilent) {
    assert.deepEqual(await flipsDuring(() => media.loadAllMedia(arg)), [true, false], `${label} raises the flag`)
  }
})

await check('a silent reload that finishes first does not hide the overlay of a default one still loading', async () => {
  mediaGate = deferred()
  const held = mediaGate
  const slow = media.loadAllMedia() // raises the flag; its request is held
  mediaGate = null // later requests are not held
  assert.equal(media.loading, true)
  await media.loadAllMedia({ silent: true })
  assert.equal(media.loading, true, 'the default reload has not finished')
  held.resolve()
  await slow
  assert.equal(media.loading, false)
})

await check('a failing reload still rejects; only the default one touches the flag', async () => {
  mediaStatus = 500
  assert.deepEqual(await flipsDuring(() => assert.rejects(media.loadAllMedia({ silent: true }), /boom/)), [])
  assert.deepEqual(await flipsDuring(() => assert.rejects(media.loadAllMedia(), /boom/)), [true, false])
  mediaStatus = 200
})

await check('the store exposes the debounced library reload', () => {
  assert.equal(typeof store.scheduleMediaReload, 'function')
})

await check('a burst of reload requests, from any caller, becomes one silent library reload', async () => {
  const before = libraryReloads()
  flips.length = 0
  store.scheduleMediaReload()
  store.scheduleMediaReload()
  store.handleT2iEvent('t2i_images_changed', { batch_id: 'b1', files: ['/out/a.png'] })
  await sleep(300)
  store.scheduleMediaReload()
  assert.equal(libraryReloads(), before, 'nothing yet: the reload waits for the burst to end')
  await sleep(DEBOUNCE_SETTLE_MS)
  assert.equal(libraryReloads(), before + 1)
  assert.deepEqual(flips, [], 'the overlay flag never flipped')
})

// ---- App.vue's bridge -----------------------------------------------------------------------------

const App = (await load('/src/App.vue')).default
const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/', component: { render: () => null } }],
})
const app = createSSRApp(App)
app.use(pinia)
app.use(router)
await router.push('/')
await router.isReady()
await renderToString(app)

await check('the bridge reloads the library once, silently, when images land while no dialog is open', async () => {
  const before = libraryReloads()
  flips.length = 0
  deliver('t2i', 't2i_images_changed', { batch_id: 'b2', files: ['/out/b.png'] })
  await sleep(DEBOUNCE_SETTLE_MS)
  assert.equal(libraryReloads(), before + 1)
  assert.deepEqual(flips, [], 'the overlay flag never flipped')
})

await check('an open dialog forwarding the same frame does not make it reload twice', async () => {
  const before = libraryReloads()
  flips.length = 0
  const frame = { batch_id: 'b3', files: ['/out/c.png'] }
  deliver('t2i', 't2i_images_changed', frame) // App.vue's bridge
  store.handleT2iEvent('t2i_images_changed', frame) // the dialog's forwarder
  deliver('t2i', 't2i_images_changed', frame)
  await sleep(DEBOUNCE_SETTLE_MS)
  assert.equal(libraryReloads(), before + 1)
  assert.deepEqual(flips, [], 'the overlay flag never flipped')
})

await check('other t2i frames and other channels do not reload the library', async () => {
  const before = libraryReloads()
  deliver('t2i', 'batch_progress', { batch_id: 'b4', phase: 'rendering', images_done: 1, images_failed: 0, images_total: 2 })
  deliver('t2i', 'batch_step', { batch_id: 'b4' })
  deliver('comfy', 'job_update', { job_id: 1, state: 'running' })
  deliver('i2v', 'something_else', {})
  await sleep(DEBOUNCE_SETTLE_MS)
  assert.equal(libraryReloads(), before)
})

// ---- the dialog's strip, rendered from a prepared store -----------------------------------------------------------------

const T2IDialog = (await load('/src/components/dialogs/T2IDialog.vue')).default
const renderDialog = () => renderToString(createSSRApp({ render: () => h(T2IDialog) }).use(pinia))

const image = (id, over = {}) => ({
  id,
  file_path: `/out/img_${id}.png`,
  file_name: `img_${id}.png`,
  batch_id: 'b1',
  model: 'krea2',
  preset_id: 3,
  caption: `caption ${id}`,
  prompt_used: `prompt ${id}`,
  negative_used: null,
  seed: 1000 + id,
  prompt_seed: 1000 + id,
  width: 1216,
  height: 832,
  megapixels: 1,
  aspect_ratio: '3:2',
  loras: null,
  render_s: 12,
  comfy_prompt_id: `p${id}`,
  created_at: '2026-09-29 12:00:00',
  is_favorite: false,
  form_state: {
    mode: 'manual', filter: null, caption: `caption ${id}`, model: 'krea2', preset_id: 3, megapixels: 1,
    aspect_ratio: '3:2', seed: 1000 + id, prompt: `prompt ${id}`, negative: null, loras: [],
  },
  ...over,
})
const tileClasses = (html) =>
  [...html.matchAll(/class="([^"]*)"/g)].map((m) => m[1].split(/\s+/)).filter((c) => c.includes('thumb-tile'))

await check('the strip lists the images newest first, with seed and size labels', async () => {
  store.images = [image(3), image(2, { width: 832, height: 1216 }), image(1)]
  store.imagesLoaded = true
  const html = await renderDialog()
  const at = ['seed 1003 · 1216×832', 'seed 1002 · 832×1216', 'seed 1001 · 1216×832'].map((label) => html.indexOf(label))
  assert.ok(at.every((i) => i > 0), `all labels rendered: ${at}`)
  assert.deepEqual([...at].sort((a, b) => a - b), at)
  assert.equal(tileClasses(html).length, 3)
  assert.doesNotMatch(html, /thumb-strip-empty/)
  assert.doesNotMatch(html, /media-viewer-overlay/, 'no viewer until one is opened')
})

await check('no image is being edited: no Editing bar and no selected tile', async () => {
  store.selectedId = null
  const html = await renderDialog()
  assert.doesNotMatch(html, /Editing the image/)
  assert.equal(tileClasses(html).filter((c) => c.includes('selected')).length, 0)
})

await check('an image being edited is outlined and the Editing bar offers Stop editing', async () => {
  store.selectedId = 2
  const html = await renderDialog()
  assert.match(html, /Editing the image from/)
  assert.match(html, /Stop editing/)
  assert.equal(tileClasses(html).filter((c) => c.includes('selected')).length, 1)
  store.selectedId = null
})

await check('"No images yet" waits for the first list, for any refetch and for the batches to finish', async () => {
  store.images = []
  store.imagesLoaded = false
  assert.doesNotMatch(await renderDialog(), /No images yet/, 'not before the first load')
  store.imagesLoaded = true
  assert.match(await renderDialog(), /No images yet/, 'once loaded and idle')
  store.imagesLoading = true
  assert.doesNotMatch(await renderDialog(), /No images yet/, 'not while a refetch is still on its way')
  store.imagesLoading = false
  store.jobs = new Map([[5, { state: 'running', value: 1, max: 4 }]])
  const busy = await renderDialog()
  assert.doesNotMatch(busy, /No images yet/, 'not while a job is still on its way')
  assert.match(busy, /job-tile/)
  assert.match(busy, />25%</)
  store.jobs = new Map()
})

await check('job tiles come before the image tiles, and a full page ends with Older', async () => {
  store.images = [image(9)]
  store.jobs = new Map([[6, { state: 'queued' }], [7, { state: 'failed', error: 'KSampler: out of memory' }]])
  store.hasMoreImages = true
  const html = await renderDialog()
  assert.ok(html.indexOf('job-tile') < html.indexOf('thumb-tile'), 'jobs first')
  assert.match(html, /KSampler: out of memory/)
  assert.match(html, /Older ›/)
  assert.ok(html.indexOf('thumb-tile') < html.indexOf('Older ›'), 'Older is last')
  store.jobs = new Map()
  store.hasMoreImages = false
})

await check('a failed image list says so and offers a retry', async () => {
  store.imagesError = 'HTTP 500'
  const html = await renderDialog()
  assert.match(html, /Couldn(?:'|&#39;)t load your images: HTTP 500/)
  assert.match(html, /Retry/)
  store.imagesError = null
})

// ---- what a batch start does to the edited image (wiring, read as text) ------------------------------------------------

await check('a batch start lets go of the edited image, saved first, before the seed advance', () => {
  const source = readFileSync(new URL('../src/components/dialogs/T2IDialog.vue', import.meta.url), 'utf8')
  const body = source.slice(source.indexOf('async function onGenerate()'), source.indexOf('// Cancels every running batch'))
  const started = body.indexOf('await store.startBatch(req)')
  const saved = body.indexOf('autosave.flush()')
  const released = body.indexOf('store.clearSelection()')
  const advanced = body.indexOf('form.seed = r.next_seed')
  assert.ok(
    started > 0 && saved > started && released > saved && advanced > released,
    `order: start ${started}, save ${saved}, release ${released}, seed advance ${advanced}`,
  )
  // No condition between the accepted start and the release: Manual and Random alike.
  assert.doesNotMatch(body.slice(started, released), /\bif\s*\(/)
})

await finish()
```
- [ ] **Step 2: Run it and confirm it fails**
Run: `cd frontend && node t2i-checks.local/task19.mjs`
Expected: FAIL (exit code 1) — the first failure is `FAIL a silent reload loads the library without ever touching the flag` and the run ends with `4 passed, 13 failed`.
- [ ] **Step 3: Implement**

`frontend/src/components/dialogs/T2IDialog.vue` (Task 21's file), `frontend/src/stores/media.ts` (existing), `frontend/src/stores/t2i.ts` (Task 16's) and `frontend/src/App.vue` (Task 19's) are existing files: apply their diffs with `git apply`.
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/components/dialogs/T2IDialog.vue b/frontend/src/components/dialogs/T2IDialog.vue
index a1731f4..f4692fc 100644
--- a/frontend/src/components/dialogs/T2IDialog.vue
+++ b/frontend/src/components/dialogs/T2IDialog.vue
@@ -1,5 +1,5 @@
 <script setup lang="ts">
-import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
+import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
 import { useDebounceFn, useEventListener } from '@vueuse/core'
 import {
   DEFAULT_MAX_BATCH_SIZE,
@@ -8,15 +8,23 @@ import {
   activeFilterCount,
   effectiveBatchSize,
   effectiveCount,
+  formatT2iTimestamp,
   isCountEffective,
   plannedImages,
   randomSeed,
+  t2iImageToMedia,
+  toStripItem,
   withCurrentOption,
+  type T2iFormState,
 } from '../../types/t2i'
+import type { ThumbItem } from '../../types/jobs'
+import type { Media } from '../../types/media'
 import type { WorkflowPreset } from '../../types/storyboard'
 import { ApiError } from '../../api/client'
 import { listPresets } from '../../api/comfy'
+import { useFoldersStore } from '../../stores/folders'
 import { useT2iStore } from '../../stores/t2i'
+import { useFormAutosave } from '../../composables/useFormAutosave'
 import { useToast } from '../../composables/useToast'
 import { useWebSocket } from '../../composables/useWebSocket'
 import {
@@ -26,18 +34,23 @@ import {
   initialFields,
   mergeFormState,
   requestSignature,
+  savableForm,
   toFormState,
   unlockPatch,
   usableDraft,
   type T2iFields,
 } from '../../utils/t2iDialogForm'
 import CaptionFilterPopover from '../t2i/CaptionFilterPopover.vue'
+import JobTile from '../generation/JobTile.vue'
 import SeedControls from '../generation/SeedControls.vue'
+import ThumbStrip from '../generation/ThumbStrip.vue'
 import LoraListEditor from '../storyboard/LoraListEditor.vue'
+import MediaViewer from '../viewer/MediaViewer.vue'
 
 const emit = defineEmits<{ close: [] }>()
 
 const store = useT2iStore()
+const foldersStore = useFoldersStore()
 const toast = useToast()
 
 const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e))
@@ -49,10 +62,22 @@ const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(
 const form = reactive<T2iFields>(initialFields(null, [], 0))
 const ready = ref(false)
 const presets = ref<WorkflowPreset[]>([])
-// Signature of the last Manual submit this dialog made: component-local on
-// purpose, so an earlier session never counts.
+// Signature of the last Manual submit this dialog made (or the image it last
+// loaded): component-local on purpose, so an earlier session never counts.
 let lastSubmitted: string | null = null
 
+// While an image is selected every change to the form is saved INTO THAT IMAGE's
+// form_state (never its as-rendered facts), 600 ms after the last edit, one save
+// at a time and in order. With none selected the form is a scratch area and
+// nothing is saved (the draft below is the only thing kept).
+const autosave = useFormAutosave<T2iFormState>({
+  form: () => toFormState(form),
+  selectedId: () => store.selectedId,
+  save: (id, fields) => store.saveFormState(id, fields),
+  savable: savableForm,
+})
+const saveFailed = autosave.saveFailed
+
 const models = computed(() => store.config?.models ?? [])
 const hasNegative = computed(() => models.value.find((m) => m.id === form.model)?.has_negative ?? false)
 const csvAvailable = computed(() => store.config?.csv.available ?? false)
@@ -147,7 +172,8 @@ watch(
   () => store.lastFinished,
   (finished) => {
     if (!finished || !ready.value) return
-    const patch = unlockPatch(finished, hasNegative.value)
+    // An image being edited owns the form: its values are never overwritten.
+    const patch = store.selectedId === null ? unlockPatch(finished, hasNegative.value) : null
     if (patch) Object.assign(form, mergeFormState(form, patch))
     if (finished.outcome === 'complete') {
       const failed = finished.images_failed > 0 ? `, ${finished.images_failed} failed` : ''
@@ -201,9 +227,16 @@ function toggleResolved() {
 async function onGeneratePrompt() {
   if (!form.caption.trim() || !form.model || generating.value) return
   const body = { caption: form.caption, seed: form.seed, model: form.model }
+  const target = store.selectedId
   generating.value = true
   try {
     const r = await store.generatePrompt(body)
+    if (store.selectedId !== target) {
+      // The user loaded another image while the prompt was being written:
+      // filling its box would edit (and autosave into) an image they did not ask about.
+      toast.show('The prompt was ready, but you switched images meanwhile, so it was not used.', 'info', 4000)
+      return
+    }
     form.prompt = r.prompt
     if (hasNegative.value && r.negative !== null) form.negative = r.negative
     promptWarnings.value = r.warnings
@@ -226,9 +259,11 @@ async function onGeneratePrompt() {
 // it can be read and edited before anything is rendered.
 async function onRoll() {
   if (rolling.value) return
+  const target = store.selectedId
   rolling.value = true
   try {
     const row = await store.rollCaption(form.filter)
+    if (store.selectedId !== target) return // another image was loaded meanwhile
     form.caption = row.caption
     if (store.config?.aspect_ratios.includes(row.aspect_ratio)) form.aspect = row.aspect_ratio
   } catch (e) {
@@ -350,8 +385,8 @@ async function onGenerate() {
     signature !== null &&
     signature === lastSubmitted &&
     !confirm(
-      'Nothing has changed since your last Generate — same seed, model, workflow, size, ' +
-        'aspect ratio and prompt. This will render an identical image.\n\n' +
+      'Nothing has changed since the last image this dialog rendered or loaded — same seed, ' +
+        'model, workflow, size, aspect ratio and prompt. This will render an identical image.\n\n' +
         'Generate it again anyway? (Cancel, then 🎲 for a new seed.)',
     )
   ) {
@@ -360,6 +395,13 @@ async function onGenerate() {
   submitting.value = true
   try {
     const r = await store.startBatch(req)
+    // A batch makes NEW images, so an image being edited is saved (pending edits
+    // go in as usual) and let go before anything below touches the form: neither
+    // the seed advance nor a Random batch's live and unlocked values can then
+    // reach it, and its saved form stays what produced it. The form keeps its
+    // values as a scratch area.
+    autosave.flush()
+    store.clearSelection()
     if (signature !== null) lastSubmitted = signature
     batchWarnings.value = r.warnings
     promptWarnings.value = []
@@ -390,6 +432,75 @@ async function onCancel() {
   }
 }
 
+// ---- the strip: select, favourite, delete, view ---------------------------------------------------------------
+const stripItems = computed(() => store.images.map(toStripItem))
+
+// Clicking an image loads its saved form_state into the form and starts
+// autosaving into it. Seed policy, Batch Size and Count per Batch stay as they
+// are, and nothing is started (a stored Random mode only restores the mode
+// and its filter). The strip only reports the FIRST click of a double-click,
+// so opening the viewer never selects twice.
+function onSelectTile(item: ThumbItem) {
+  const id = Number(item.id)
+  if (!ready.value || id === store.selectedId) return
+  if (randomLocked.value) {
+    toast.show('A Random batch is running: wait for it to finish before loading an image.', 'info')
+    return
+  }
+  autosave.flush() // pending edits belong to the image being left
+  const stored = store.selectImage(id)
+  if (!stored) return
+  Object.assign(form, mergeFormState(form, stored))
+  // The form now equals what the server holds, so loading must not echo back
+  // as a save. An untouched Generate would render the same image again.
+  autosave.markLoaded()
+  lastSubmitted = requestSignature(form, hasNegative.value)
+}
+
+// The form keeps its values and goes back to being a scratch area.
+function stopEditing() {
+  autosave.flush()
+  store.clearSelection()
+}
+
+async function onFavorite(item: ThumbItem) {
+  try {
+    await store.toggleFavorite(Number(item.id))
+  } catch (e) {
+    toast.show(errMsg(e), 'warn')
+  }
+}
+
+async function onDelete(item: ThumbItem) {
+  if (!confirm('Delete this image? The file goes to the OS trash.')) return
+  const id = Number(item.id)
+  // Drop a pending save rather than PATCH a row that is about to go.
+  if (store.selectedId === id) autosave.cancelPending()
+  try {
+    await store.deleteImage(id)
+    void foldersStore.refreshT2iPaths()
+  } catch (e) {
+    toast.show(errMsg(e), 'warn')
+  }
+}
+
+async function onCancelJob(jobId: number) {
+  try {
+    await store.cancelJob(jobId)
+  } catch (e) {
+    toast.show(errMsg(e), 'warn')
+  }
+}
+
+// The viewer gets a snapshot taken when it opens: images arriving mid-batch
+// would otherwise shift the list under its index.
+const viewerImages = shallowRef<Media[]>([])
+const viewerIndex = ref<number | null>(null)
+function onOpenViewer(index: number) {
+  viewerImages.value = store.images.map(t2iImageToMedia)
+  viewerIndex.value = index
+}
+
 // ---- the scratch-form draft ---------------------------------------------------------------------------------
 // Per-viewer convenience: what is in the boxes survives closing the dialog
 // (every storage access is guarded inside the store). It is written only
@@ -405,8 +516,17 @@ watch(
   () => JSON.stringify(toFormState(form)),
   () => void saveDraftSoon(),
 )
+// Back to the scratch form ("Stop editing", or the image vanished): what is in
+// the boxes becomes the draft.
+watch(
+  () => store.selectedId,
+  (id) => {
+    if (id === null) void saveDraftSoon()
+  },
+)
 
 function close() {
+  autosave.flush() // before store.close() lets go of the selected image
   saveDraftNow()
   store.close()
   emit('close')
@@ -690,7 +810,61 @@ onBeforeUnmount(() => {
           </ul>
         </div>
       </div>
+
+      <!-- Shown only while an image is selected: says where edits are going.
+           With none selected the form is an unsaved scratch area. -->
+      <div v-if="store.selectedImage" class="bar" :class="{ failed: saveFailed }" role="status">
+        <span v-if="saveFailed">
+          Couldn't save your changes to this image — they'll be retried on the next edit.
+        </span>
+        <span v-else>
+          Editing the image from {{ formatT2iTimestamp(store.selectedImage.created_at) }} ·
+          changes save automatically
+        </span>
+        <button
+          type="button"
+          class="link-btn"
+          title="Keep these values in the form, but stop saving them to this image"
+          @click="stopEditing"
+        >Stop editing</button>
+      </div>
+      <p v-if="store.imagesError" class="lint" role="alert">
+        ⚠ Couldn't load your images: {{ store.imagesError }}
+        <button type="button" class="link-btn" @click="store.refreshImages()">Retry</button>
+      </p>
+
+      <ThumbStrip
+        :items="stripItems"
+        :selected-id="store.selectedId"
+        empty-text="No images yet — generated images appear here."
+        :loading="!store.imagesLoaded || store.imagesLoading || store.isBusy"
+        :has-more="store.hasMoreImages"
+        :loading-more="store.loadingOlder"
+        @select="onSelectTile"
+        @open="onOpenViewer"
+        @favorite="onFavorite"
+        @delete="onDelete"
+        @more="store.loadOlder()"
+      >
+        <template #jobs>
+          <JobTile
+            v-for="[jobId, chip] in store.jobs"
+            :key="`job-${jobId}`"
+            :chip="chip"
+            @cancel="onCancelJob(jobId)"
+            @dismiss="store.dismissJob(jobId)"
+          />
+        </template>
+      </ThumbStrip>
     </div>
+
+    <MediaViewer
+      v-if="viewerIndex !== null"
+      :media-list="viewerImages"
+      :initial-index="viewerIndex"
+      :allow-destructive="false"
+      @close="viewerIndex = null"
+    />
   </div>
 </template>
 
PATCH
```
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/stores/media.ts b/frontend/src/stores/media.ts
index 1420efb..e55f496 100644
--- a/frontend/src/stores/media.ts
+++ b/frontend/src/stores/media.ts
@@ -80,8 +80,14 @@ export const useMediaStore = defineStore('media', () => {
     return folders.scopeMedia(displayedMedia.value)
   })
 
-  async function loadAllMedia() {
-    loading.value = true
+  // `silent` is for background refreshes that must not raise `loading`, the
+  // flag behind the dimmed "Loading media..." overlay (the T2I image bridge
+  // reloads every few seconds while a batch runs). Only a literal `true` is
+  // silent: a caller that passes anything else, say an Event from a handler
+  // bound straight to this function, gets the overlay as before.
+  async function loadAllMedia(options?: { silent?: boolean }) {
+    const silent = options?.silent === true
+    if (!silent) loading.value = true
     try {
       const data = await fetchAllMedia(sortOrder.value, false, true)
       allMedia.value = data
@@ -89,7 +95,7 @@ export const useMediaStore = defineStore('media', () => {
         data.filter((m) => m.is_favorite).map((m) => m.file_path),
       )
     } finally {
-      loading.value = false
+      if (!silent) loading.value = false
     }
   }
 
PATCH
```
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/stores/t2i.ts b/frontend/src/stores/t2i.ts
index 9b9810c..2e234b2 100644
--- a/frontend/src/stores/t2i.ts
+++ b/frontend/src/stores/t2i.ts
@@ -319,9 +319,13 @@ export const useT2iStore = defineStore('t2i', () => {
     return finished
   }
 
+  // Silent: a reload every few seconds must not flash the library's dimmed
+  // "Loading media..." overlay.
   const scheduleMediaReload = useDebounceFn(
     () => {
-      media.loadAllMedia().catch((e) => console.warn('T2I: could not reload the library', e))
+      media
+        .loadAllMedia({ silent: true })
+        .catch((e) => console.warn('T2I: could not reload the library', e))
     },
     MEDIA_RELOAD_DEBOUNCE_MS,
     { maxWait: MEDIA_RELOAD_MAX_WAIT_MS },
@@ -712,6 +716,10 @@ export const useT2iStore = defineStore('t2i', () => {
     // loading
     open,
     close,
+    // The one debounced library-grid reload. App.vue's always-on `t2i` bridge
+    // calls it too, so images a batch finishes while the dialog is closed
+    // still reach the grid, and an open dialog does not reload twice.
+    scheduleMediaReload,
     loadConfig,
     loadCaptionMeta,
     refreshImages,
PATCH
```
```bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/frontend/src/App.vue b/frontend/src/App.vue
index 32e044f..20e8af8 100644
--- a/frontend/src/App.vue
+++ b/frontend/src/App.vue
@@ -2,8 +2,10 @@
 import ToastHost from './components/layout/ToastHost.vue'
 import { useWebSocket } from './composables/useWebSocket'
 import { useFoldersStore } from './stores/folders'
+import { useT2iStore } from './stores/t2i'
 
 const foldersStore = useFoldersStore()
+const t2iStore = useT2iStore()
 
 // Live-sync folder mutations from other tabs / sessions.
 useWebSocket('folders', (event, data) => {
@@ -35,10 +37,16 @@ useWebSocket('i2v', (event) => {
 })
 
 // New (or deleted) T2I images change the "Generated with T2I" smart-folder
-// rule's membership. Always on, so it works whether or not the T2I dialog
-// is open.
+// rule's membership and belong in the library grid. Always on, so it works
+// whether or not the T2I dialog is open: batches keep running on the server
+// after the dialog closes. The grid reload is the store's own debounced
+// function, which the dialog's forwarder shares, so an open dialog does not
+// reload twice.
 useWebSocket('t2i', (event) => {
-  if (event === 't2i_images_changed') void foldersStore.refreshT2iPaths()
+  if (event === 't2i_images_changed') {
+    void foldersStore.refreshT2iPaths()
+    void t2iStore.scheduleMediaReload()
+  }
 })
 </script>
 
PATCH
```
- [ ] **Step 4: Run it and confirm it passes**
Run: `cd frontend && node t2i-checks.local/task19.mjs`
Expected: `17 passed, 0 failed`
- [ ] **Step 5: Quality gate**
Run: `cd frontend && npm run build`
Expected: exit code 0 and Vite's closing summary line, "✓ built in <time>" (for example "✓ built in 742ms"; a slower run prints seconds, such as "4.26s"). The "Some chunks are larger than 500 kB" notice below it is old (the baseline build prints it too).
The earlier checks still pass: `cd frontend && node t2i-checks.local/task17.mjs && node t2i-checks.local/task18.mjs` ends with `24 passed, 0 failed` and then `28 passed, 0 failed`.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/dialogs/T2IDialog.vue \
    frontend/src/stores/media.ts \
    frontend/src/stores/t2i.ts \
    frontend/src/App.vue
git commit -m "feat(t2i): dialog strip, per-image autosave, viewer and library refresh bridge" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
- [ ] **Step 7: Manual check** (the strip, editing, the viewer, and a batch that outlives the dialog)

Same prerequisites as Task 21. Open the Text to Image dialog.
1. Under the status line a strip lists your generated images, newest first, each labelled `seed <N> · <W>×<H>`; hovering a tile shows its model and the start of its prompt. An `Older ›` tile ends the strip once you have more than 60 images. With no images the strip says `No images yet — generated images appear here.`, but never while the list is still loading or a batch is running.
2. Set the policy to `Increment`, Count Per Batch to `3`, write a prompt and click `Generate ×3`. Dashed job tiles appear at the left of the strip (`queued`, then a percentage; a failed job turns red with ComfyUI's error and a ✕ that dismisses it). The ✕ on a live job tile cancels just that job. Each finished image takes a place at the front of the strip.
3. Click an image tile. It gets an outline, a bar appears above the strip (`Editing the image from <date> · changes save automatically`), and the form fills with that image's caption, prompt, model, workflow, size, aspect ratio, seed and LoRAs; the seed policy, Batch Size and Count Per Batch stay as they were. Change the prompt and wait a second, click another tile, then click the first again: your change is there (it was saved into that image, not into what it was rendered with; the tile's label still shows the seed it was rendered with). Click `Stop editing`: the bar disappears and the form keeps its values.
4. Load an image, set the policy to `Increment` and click `Generate`: the Editing bar disappears (the image is let go and the form keeps its values as a scratch area), the seed box moves on and the new images appear. Click the image you loaded again: its seed box shows the seed it was rendered with, not the advanced one. Edit the prompt of a loaded image and click `Generate` straight away: the edit is saved into that image first, and then it is let go. (A refused start, for instance an empty filter match in Random mode, leaves the image selected.) Click a tile made by a Random batch: `Random Caption` and its filter come back, and nothing starts.
5. Double-click a tile: a full-screen viewer opens at that image (`n / total`), with arrow keys to move and Esc to close; it has no favourite or delete buttons. Start a batch first and open the viewer while it runs: new tiles arrive in the strip but the viewer's counter does not change. Closing the viewer leaves the dialog open.
6. Hover a tile and click ★: it turns gold (the same image is a favourite in the library). Click ✕ on a tile and confirm: the tile goes, the file goes to the OS trash and the image disappears from the library grid; a smart folder using the rule `Generated with T2I` updates too.
7. Start a Manual batch (policy `Increment`, Count Per Batch `3` or more), then close the dialog with ✕ straight away. Behind it, the library grid gains the new images as they finish (within a few seconds of each), although the dialog is closed, and the dimmed `Loading media…` overlay does not flash. A normal refresh (the header's refresh button or F5) still shows it. Open the dialog again: the running batch's status line and job tiles are back.
8. Load an image, then start a Random batch: the image is let go as well. Click an image tile while it runs: a note says a Random batch is running and nothing loads. When it ends the boxes unlock with the last step's caption and prompt.

## Phase 7 — Documentation and full verification

### Task 23: Documentation and full verification

**Files:**
- Create: `docs/t2i.md`
- Modify: `README.md` (Documentation list), `docs/configuration.md` (`t2i` section), `docs/architecture.md` (two schema bullets), `docs/api-reference.md` (`/api/t2i/*` + the `t2i` channel), `CLAUDE.md` (tree, WS channel list, t2i decisions, two stale statements)

**Interfaces:**
- Consumes: every name introduced by Tasks 1-22 (module names, routes, events, config keys). The text below uses exactly those names.
- Produces: the user-facing feature guide, the reference entries, and the rules engineers must follow (CLAUDE.md). No code.

- [ ] **Step 1: Confirm the two CLAUDE.md statements this task corrects are stale**

Run:
```bash
grep -n "multiple of 16" CLAUDE.md
grep -n "I2V_DIM_MULTIPLE" metascan/core/i2v_compiler.py
grep -n "migrateSmartFolder" frontend/src/stores/folders.ts
```
Expected: `CLAUDE.md` still says `multiple of 16` (one hit, inside the i2v bullet); the code says `I2V_DIM_MULTIPLE: int = 32`; `migrateSmartFolder` has exactly two hits (its definition and one call inside `readLegacyLocalStorage`). If the code has changed since this plan was written, fix the wording in Step 4 to match the code instead of applying it blindly.

- [ ] **Step 2: Create the feature guide** (`docs/t2i.md`)

Create the file with exactly this content:

`````markdown
[← Back to README](../README.md)

# Text to Image

The **Text to Image** dialog turns a caption into a prompt and renders it through a ComfyUI workflow. Open it with the sparkles icon at the top right of the main screen, next to **Storyboards** (desktop only).

A caption comes from one of two places:

- **Manual** — you type it.
- **Random Caption** — the server draws it from `data/t2i_captions/t2i_captions.csv`, optionally narrowed by column filters.

The pipeline is always the same:

1. **Resolve** — character-name tokens (`__ALICE__`, …) and body-part tokens (`__HAIR__`, …) become seeded, deterministic character descriptions.
2. **Rewrite** — the local VLM turns the resolved caption into a prompt in the selected model's style (Krea 2, Qwen-Image, SDXL, Z-Image).
3. **Render** — a registered ComfyUI workflow renders it. Every image is its own job with its own seed.

Generated images are ordinary library media. They appear in the strip at the bottom of the dialog, and each one remembers the dialog contents that produced it.

## The form

| Control | What it does |
|---|---|
| **Caption** | The description to expand. Plain text; tokens are optional. A collapsible *Resolved caption* shows exactly what the VLM will be given. |
| **Generate Prompt** | Resolves the caption with the current seed and writes the prompt into **Prompt**. Review-only: nothing is queued. |
| **Manual / Random Caption** | Where captions come from. In Random mode the 🎲 button loads one caption so you can preview it. |
| **Filter** | Random mode only. Nudity, the three scores, Males / Females, Aspect Ratio and Clothing, with a live "N captions match". |
| **Model** | Krea 2, Qwen-Image, SDXL or Z-Image. Chooses the prompt style and the default workflow. |
| **Workflow** | Every registered `t2i` workflow. |
| **Size** | Pixel budget in megapixels. |
| **Aspect Ratio** | Manual: your choice. Random: taken from each caption's row (read-only while a batch runs). |
| **Seed** and policy | The seed, plus how it advances: **Fixed**, **Increment**, **Decrement** or **Randomize**. |
| **LoRAs** | Same editor as Image to Video. Needs an `MS_LORA_STACK` node in the workflow. |
| **Prompt** / **Negative** | The text that will be rendered. **Negative** appears only for models that write one (Qwen-Image, SDXL) and is sent only if the workflow has an `MS_NEGATIVE` node. |
| **Batch Size** | How many captions a run uses. Locked at 1 in Manual mode. |
| **Count per Batch** | How many images each caption renders, one seed apiece. Above 1 needs a non-Fixed policy. |

## Batches

**Generate** starts a batch on the server, so it keeps running if you close the dialog or reload the page; reopening the dialog reattaches to it. **Cancel** stops every running batch and cancels its unfinished jobs.

- **Manual:** one step — your caption and prompt — rendered `Count per Batch` times.
- **Random Caption:** for each of `Batch Size` steps the server picks an unused caption, takes its aspect ratio, writes a prompt, then renders `Count per Batch` images. While it runs, the Caption, Prompt, Aspect and Seed boxes show the current step and cannot be edited. Only one Random batch runs at a time.

Seeds advance across the whole run according to the policy and stop the run early, with a message, rather than leave `0` to `2147483647` (the batch then plans fewer images and the seed box is left alone, because no unused seed remains). Under **Randomize** the first image uses the seed shown in the dialog, so it reproduces that prompt, and the rest are drawn at random. The character description for a step is drawn from that step's **first** image seed, so the same caption and seed always give the same cast. With **Fixed**, every step uses the same seed and therefore the same cast. When a run ends, the seed box shows the next unused seed.

**GPU order** follows the existing `comfy.unload_vlm_during_generation` setting:

- **On (default):** all prompts are written first, then the VLM is unloaded, then the images render. The first image appears only after the last prompt is written. A Manual **Generate** also unloads the VLM, so the next **Generate Prompt** reloads it.
- **Off:** each step renders as soon as its prompt is ready, overlapping the next prompt.

Jobs are submitted through a small window (`t2i.window`, default 4 unfinished jobs per batch) so a large batch cannot bury Image to Video and storyboard jobs in ComfyUI's shared queue.

Batch state lives in memory. If the server restarts mid-run, steps that were not yet submitted are dropped; jobs already queued still finish and their images are ingested, but without a saved form.

## Caption files (`data/t2i_captions/`)

`t2i_captions.csv` needs the columns `Caption` and `Aspect Ratio`. `Nudity`, `Artistic Quality`, `Erotic Score`, `Pornographic Score`, `Males`, `Females` and `Clothing` are optional; each one present enables its filter. The file is git-ignored — it is your data.

### Tokens

A token is a name in capitals between double underscores.

- **Characters** — `__ALICE__ __BELLA__ __CLARA__ __DIANNA__ __EMMA__` (female) and `__ADAM__ __BOB__` (male). The first time a character appears it is replaced by a generated description; later mentions become a short handle tied to the same character.
- **Body-part tokens** — `__HAIR__ __BREASTS__ __VAGINA__ __PENIS__` stand in for the word itself and belong to a character (`her __HAIR__`). They resolve from a list of the same name.
- **Anything else** — `__TOKEN__` resolves from `token.txt` if that list exists, as one value used everywhere in the caption; otherwise it becomes the plain lowercase word.

Malformed underscores (`____ALICE____`) are tolerated. A caption with no tokens passes through unchanged.

### Lists

Each characteristic is a text file with one value per line; blank lines and `#` comments are ignored and duplicates are dropped.

| File | Used for |
|---|---|
| `age.txt`, `ethnicity.txt`, `skin.txt`, `eyes.txt`, `face.txt`, `hair.txt`, `body.txt` | The character description. |
| `<name>.female.txt` / `<name>.male.txt` | Override `<name>.txt` for that gender. The shipped body lists are `body.female.txt` and `body.male.txt`. |
| `breasts.txt`, `vagina.txt`, `penis.txt` | Body-part tokens. **Not shipped** — supply your own. A missing list makes the token its plain word. |

Write values so they read after `with`: `an oval face`, `green eyes`, `olive skin`, `an athletic build`. Hair values must end in ` hair` (`auburn hair`) — the short handle for a later mention is built from it (`the auburn-haired woman`).

Lists reload automatically when a file changes. **Editing or reordering a list changes which value a given caption and seed picks.**

**Adult only.** Any line in an `age` list containing a number under 18, or a spelled-out age from thirteen to seventeen, is rejected, and any line in any list containing a minor-indicating term (plurals included) is rejected. A line containing a parenthesis is rejected too, because parentheses never belong in a generated prompt. Rejections are logged and listed in the `wildcards.warnings` of `GET /api/t2i/config`.

### `characters.yml`

Optional; defaults are built in. It sets the names, the noun for each gender, which slots are drawn and in what order, which slots read as `<age> <ethnicity> woman` versus `with …`, which tokens belong to characters, and which words before `hair` mean body hair (`pubic`, `body`, …), where the plain word is used.

### How a description is placed

- If the next word is a verb, the description goes inline: *A 31-year-old West African woman with olive skin, brown eyes and an athletic build sits on a bench.*
- If the next word is a possessive, `with`, `and`, punctuation, or the name is part of a compound subject (`X and Y`), only the head noun goes inline and the details move to one trailing sentence. Parentheses are never used — ComfyUI parses them as weighting.
- A slot the caption already writes out with a token (`__HAIR__`) is left out of the description.
- The owner of a body-part token is the character a pronoun points at (`his` / `her`), otherwise the character named nearest before it. This is a heuristic: in a caption with several people the worst case swaps who gets which hair colour.

### Later mentions

| Style | Example | Default for |
|---|---|---|
| `ref` | *the copper-red-haired woman* | Krea 2, Qwen-Image, Z-Image |
| `noun` | *the woman* | SDXL |
| `name` | *Alice* (introduced as "…woman named Alice") | — |

Override per model with `t2i.identity` in `config.json`.

## Models and prompt style

Each model's guideline is an entry in `data/meta_prompt.yml`, edited live: `META_KREA2`, `META_QWEN`, `META_SDXL`, `META_ZIMAGE`. `T2I_CAPTION_PREAMBLE` tells the VLM that the caption stands in for the image. `META_KREA2` is a starting point — tune it. The **content mode** (`t2i.content_mode`) appends the existing *Uncensored* or *Keep SFW* directive, or nothing.

If no VLM is installed, or it fails inside a batch, the prompt falls back to the resolved caption (SDXL and Qwen-Image also get a stock negative, SDXL a quality prefix) and the step carries a warning.

## Workflows

Register workflows in **Configuration → Text to Image**. A `t2i` workflow is a ComfyUI API-format graph whose nodes are titled with the `MS_*` convention:

| Title | Required | Purpose |
|---|---|---|
| `MS_POSITIVE` | yes | Prompt text |
| `MS_SEED` | yes | Seed |
| `MS_LATENT` | yes | Width, height and batch size (metascan sets batch size 1) |
| `MS_SAVE` | yes | The save node metascan collects images from |
| `MS_NEGATIVE` | no | Enables the Negative box |
| `MS_LORA_STACK` | no | Enables the LoRAs editor (a stackable loader such as rgthree Power Lora Loader) |

The **Validate** button lists everything at once and warns when `MS_LORA_STACK` or `MS_NEGATIVE` is missing. The tab also sets the default workflow per model, the output folder, the file-name prefix and the size choices.

## Results

- Click a thumbnail to load the dialog contents that produced it (mode, filter, caption, model, workflow, size, aspect ratio, seed, prompt, negative, LoRAs). Edits save automatically into that image. **Stop editing** returns to a scratch form. Seed policy, Batch Size and Count per Batch are never restored, so selecting an image cannot trigger a large re-run.
- **Generate** with an image selected first saves your pending edits into it, then leaves editing mode before the seed advances (the form keeps its values), so the automatic seed advance never rewrites the saved form of the image it came from. Thumbnails cannot be selected while a Random batch runs.
- New images reach the library grid while the dialog is open *and* after you close it, without the dimming "Loading media…" overlay.
- Double-click to view; **★** stars it (the library's favorite flag); **✕** deletes the file and its library entry.
- With no image selected, the scratch form is remembered between openings in your browser.

## Smart folders

**Generated with T2I** is a rule in the smart-folder editor: it matches every library image produced by this dialog.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Random Caption is disabled | The CSV is missing or unreadable; the reason is in `csv.error` from `GET /api/t2i/config`. |
| "…has no MS_LORA_STACK node" | You added LoRAs but the workflow cannot take them; remove them or add an `MS_LORA_STACK` node. |
| A negative was ignored | The workflow has no `MS_NEGATIVE` node; the start response carries a warning. |
| Prompts are just the caption | No VLM is available or it failed; see the step's warnings. |
| Images are slow to start | ComfyUI's queue is shared with Image to Video and the storyboard. |
`````

- [ ] **Step 3: Apply the README, configuration and architecture edits**

Apply with `git apply`:

````bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/README.md b/README.md
index 218df2b..53ebcf2 100644
--- a/README.md
+++ b/README.md
@@ -143,9 +143,10 @@ Detailed documentation lives in [`docs/`](docs/):
 - **[Tech Stack](docs/tech-stack.md)** — backend, frontend, AI/media processing, infrastructure, and dev tooling
 - **[First-Time Setup](docs/first_time_setup.md)** — step-by-step, per-platform install of Python, Node, FFmpeg, virtualenv, and all dependencies
 - **[Installation](docs/installation.md)** — prerequisites, end-user setup, contributor setup, environment variables
-- **[Configuration](docs/configuration.md)** — `config.json` reference, including the `similarity`, `ui`, `models`, `comfy`, and `i2v` sections
+- **[Configuration](docs/configuration.md)** — `config.json` reference, including the `similarity`, `ui`, `models`, `comfy`, `i2v`, and `t2i` sections
 - **[Image-to-Video Workflow Setup](docs/i2v-workflow-setup.md)** — step-by-step ComfyUI build and registration for the MiniMax H3 turbo and high-quality presets
 - **[Image-to-Video cadence templates](docs/i2v-templates.md)** — the JSON découpage-template format that fixes an i2v clip's shot structure
+- **[Text to Image](docs/t2i.md)** — the Text to Image dialog: caption sources, character and wildcard files, prompt styles per model, server-side batches, and workflow requirements
 - **[API Reference](docs/api-reference.md)** — REST endpoints, WebSocket envelope, error shapes
 - **[Architecture](docs/architecture.md)** — client–server layout, database schema, backend/frontend layouts, key design decisions
 - **[Hardware Detection](docs/hardware-detection.md)** — what gets probed, tier classification, per-model gates, auto-warnings
diff --git a/docs/architecture.md b/docs/architecture.md
index a9a8b95..d777ee1 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -35,6 +35,8 @@ SQLite with WAL mode and a `threading.Lock` over a single connection.
 - **`panels`** — one shot: `scene_id` (`ON DELETE CASCADE`), `sort_order`, `action`, `duration_s`, `image_loras`/`video_loras` (JSON `[{name, strength}, ...]` lists injected into the still/video preset's `MS_LORA_STACK` node), `video_prompt`, `video_prompt_locked`, `video_prompt_source`, `video_prompt_warnings`, `video_anchor`, `video_compiled_anchor`. Since the 2026-08-17 shot/beat reorg, a panel is a thin H3-scene-like container — the compiled per-shot video prompt lives here, but every per-shot *creative* field moved down to `beats`.
 - **`beats`** — one shot-internal timeline unit (one H3 `[Shot n]` section): `panel_id` (`ON DELETE CASCADE`), `sort_order`, `duration_s`, `action`, `shot_size`, `angle`, `lens`, `subject_ids` (JSON array), `camera_motion`, `camera_amplitude`, `camera_speed`, `is_cut`, `dialog` (JSON array), `sound`, `brief`, `prompt`, `prompt_locked`, `prompt_source`, `selected_image_id` (→ `beat_images.id`, `ON DELETE SET NULL`). This is where a shot's framing, cast, and still-image prompt/keeper now live — a metascan Shot (panel) maps to one H3 generation unit, a Beat maps to one H3 `[Shot n]` section.
 - **`beat_images`** — one rendered variant: `beat_id` (`ON DELETE CASCADE`), `file_path` (→ `media.file_path`, `ON DELETE CASCADE`), `seed`, `variant_index`, `prompt_used`, `preset_id`, `comfy_prompt_id`. Renamed and re-parented from `panel_images` in the shot/beat reorg.
+- **`t2i_images`** — one Text-to-Image file: `file_path` (unique; no foreign key — list queries JOIN `media` and prune rows whose media is gone), `batch_id`, the as-rendered facts (`model`, `preset_id`, `caption`, `prompt_used`, `negative_used`, `seed`, `prompt_seed`, `width`, `height`, `megapixels`, `aspect_ratio`, `loras`, `render_s`, `comfy_prompt_id`, `created_at`) and the editable `form_state` JSON. `set_t2i_image_form_state` is the only update — the facts never change.
+- **`generation_jobs.t2i_batch_id`** — nullable TEXT correlating a ComfyUI job to a Text-to-Image batch (no `REFERENCES`, like `panel_id`, `beat_id` and `i2v_source_path`).
 
 `media.hidden` (`INTEGER NOT NULL DEFAULT 0`) keeps storyboard-generated
 variants out of the main grid until curated: ingest inserts every rendered
diff --git a/docs/configuration.md b/docs/configuration.md
index a67831d..b063115 100644
--- a/docs/configuration.md
+++ b/docs/configuration.md
@@ -116,3 +116,35 @@ Read by `backend.config.get_i2v_config` and served to the frontend by `GET /api/
 - **`default_steps`** — which entry the selector opens with. Falls back to the first entry if it isn't in `steps`.
 - **`output_root`** — absolute directory generated clips are saved under, chosen with the **Browse…** directory picker (it walks the *server's* filesystem). Empty keeps the default layout, `<comfy.output_root>/i2v/<image name>/`. The directory must already exist — a generate against a missing one returns 400 rather than creating it.
 - **`output_prefix`** — a path *relative to `output_root`* whose last component is the file name prefix; a unique number (epoch seconds) and the extension are appended. `strftime` date tokens expand at generate time: `%Y` year, `%m` month, `%d` day, `%H` hour, `%M` **minute**, `%S` second. With root `/mnt/d/Media/images` and prefix `/%Y-%m-%d/minimax_`, a clip lands at `/mnt/d/Media/images/2026-09-20/minimax_1789930000.mp4`. A leading slash is cosmetic (never the filesystem root), missing subdirectories are created, `..` is rejected, and characters illegal in file names become `-`. An existing file is never overwritten — a `_2`, `_3`… tail is added instead. Ignored while `output_root` is empty. The config tab shows a live preview of the resolved path and warns about `%M` used without `%H` (almost always a typo for `%m`).
+
+## `t2i`
+
+Read by `backend.config.get_t2i_config` and served to the frontend by `GET /api/t2i/config`. The output folder, name prefix, workflows, sizes, default model and content mode are editable from **Configuration → Text to Image**; `window`, the batch limits and `identity` are config-file only. How the feature works is covered in [Text to Image](t2i.md).
+
+```jsonc
+{
+  "t2i": {
+    "output_root": "",
+    "output_prefix": "/%Y-%m-%d/t2i_",
+    "megapixels": [0.5, 1.0, 1.5, 2.0],
+    "default_megapixels": 1.0,
+    "default_model": "krea2",
+    "model_workflows": { "krea2": null, "qwen": null, "sd": null, "zimage": null },
+    "content_mode": "uncensored",
+    "identity": {},
+    "window": 4,
+    "max_batch_size": 500,
+    "max_count_per_batch": 32
+  }
+}
+```
+
+- **`output_root`** — absolute directory generated images are saved under (the **Browse…** picker walks the *server's* filesystem). Empty means `<comfy.output_root>/t2i`, created on demand. A configured directory must already exist — starting a batch against a missing one returns 400.
+- **`output_prefix`** — a path *relative to `output_root`* whose last component is the file name prefix; a unique number and the extension are appended. Same `strftime` tokens, `..` rule, and never-overwrite behaviour as the [`i2v`](#i2v) prefix. Default `"/%Y-%m-%d/t2i_"`.
+- **`megapixels`** / **`default_megapixels`** — size choices as a pixel budget, and the entry the dialog opens with (first entry if the default is not in the list). Width and height are derived from the budget and the aspect ratio and land on a multiple of the model's grid (16, or 64 for SDXL).
+- **`default_model`** — `krea2`, `qwen`, `sd` or `zimage`; anything else falls back to `krea2`.
+- **`model_workflows`** — a `workflow_presets.id` (kind `t2i`) or `null` per model. Choosing a model in the dialog selects its default workflow.
+- **`content_mode`** — `uncensored` (append the *Uncensored / Adult Detail* directive to the prompt-writing system prompt), `sfw` (append *Keep SFW*), or `default` (append nothing). Anything else falls back to `uncensored`.
+- **`identity`** — optional per-model override of how a later mention of a character reads: `ref`, `noun` or `name`. Unknown models and styles are dropped. Defaults: `ref` for `krea2`/`qwen`/`zimage`, `noun` for `sd`.
+- **`window`** — how many unfinished jobs one batch keeps inside ComfyClient's queue at once. Default `4`, floored at `1`.
+- **`max_batch_size`** / **`max_count_per_batch`** — ceilings the batch endpoint enforces. Defaults `500` and `32`.
PATCH
````

- [ ] **Step 4: Apply the API reference edit**

````bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/docs/api-reference.md b/docs/api-reference.md
index 473e42d..372937c 100644
--- a/docs/api-reference.md
+++ b/docs/api-reference.md
@@ -744,3 +744,67 @@ where they're picked.
 ### `i2v` WebSocket channel
 - **`i2v_videos_changed`** — `{source_path, files}`, sent once a ComfyUI
   job's outputs have been downloaded and ingested as `i2v_videos` rows.
+
+## Text-to-image (`/api/t2i/*`)
+
+The `T2iRunner` singleton is constructed in the FastAPI lifespan after the `I2vRunner` and installed via `set_t2i_runner`. It owns caption resolution, prompt generation, server-side batches and image ingest. A batch is a background task: closing the dialog does not stop it. Generated images are ordinary visible library media. See [Text to Image](t2i.md) for the feature guide.
+
+Errors follow the i2v routes: **400** validation, **404** unknown id, **409** a Random batch is already running, **502** ComfyUI/VLM failure, **503** the runner is not running. The image, path and output-preview routes need neither the runner nor ComfyUI.
+
+### `GET /api/t2i/config`
+The `t2i` config section with defaults filled in (see `docs/configuration.md`), plus `models` (`[{id, label, has_negative, identity}]`), `aspect_ratios`, `seed_policies`, `seed_max`, `csv` (`{available, total, error}`) and `wildcards` (`{slots, warnings}` — `warnings` lists every rejected list line).
+
+### `GET /api/t2i/captions/meta`
+`{total, columns}` for the Filter popover. Each column is `{key, label, type: "choice" | "tags", options: [{value, count}]}`, `{key, label, type: "range", min, max, step}` (the three scores) or `{key, label, type: "int_range", min, max}` (`males`, `females`). Only columns present in the CSV appear.
+
+### `POST /api/t2i/captions/count`
+Body: `{filter}`. Returns `{count, total}`. A `filter` may carry `nudity` (any-of), `artistic_quality` / `erotic_score` / `pornographic_score` / `males` / `females` (`{min?, max?}`), `aspect_ratios` (any-of), `clothing_any`, `clothing_none`. **400** on an unknown key or a bad value.
+
+### `POST /api/t2i/captions/random`
+Body: `{filter}`. Returns one caption row `{id, caption, aspect_ratio, nudity, artistic_quality, erotic_score, pornographic_score, males, females, clothing}` with its tokens intact. **400** for a bad filter, **404** if nothing matches, **503** if the caption CSV is unavailable. `captions/count` and `captions/meta` answer `0` / empty for an unavailable CSV rather than an error.
+
+### `POST /api/t2i/captions/resolve`
+Body: `{caption, seed, model}`. Returns `{resolved_caption, characters: {NAME: {slot: value}}, warnings}`. Pure and deterministic — the same caption and seed always resolve identically.
+
+### `POST /api/t2i/prompt`
+Body: `{caption, seed, model}`. Resolves the caption, then has the VLM write the model-styled prompt; writes nothing. Returns `{prompt, negative, resolved_caption, warnings}` (`negative` is `null` for models that write none). With no VLM available it returns **200** with the resolved caption as the prompt and a warning.
+- **400** for an empty caption or unknown model.
+- **502** when the VLM call fails or times out.
+
+### `POST /api/t2i/batches`
+Body: `{mode: "manual" | "random", model, preset_id, megapixels, seed, seed_policy: "fixed" | "increment" | "decrement" | "random", batch_size = 1, count_per_batch = 1, loras: [{name, strength}], caption?, prompt?, negative?, aspect_ratio?, filter?}`. `caption`, `prompt`, `negative` and `aspect_ratio` are Manual fields; `filter` is a Random field. Returns `{batch_id, total_images, warnings}` and starts a background task.
+- **400** with a named reason for: unknown model; missing or non-`t2i` workflow; LoRAs against a workflow with no `MS_LORA_STACK`; `count_per_batch > 1` with a Fixed seed; Manual with `batch_size != 1`; counts above the configured limits; Manual with an empty prompt or an unsupported aspect ratio; Random whose filter matches nothing; a configured `output_root` that is not a directory; a seed outside `0..2147483647`; non-positive `megapixels`.
+- **409** `random_batch_active` when a Random batch is already running.
+- **502** if ComfyUI rejects the job at submit time.
+`warnings` carries non-fatal notes (e.g. a negative dropped because the workflow has no `MS_NEGATIVE`).
+
+### `GET /api/t2i/batches`
+Active batches: `[{batch_id, mode, state: "prompting" | "rendering", total_steps, step, images_total, images_done, images_failed, next_seed, started_at}]` (`next_seed` is the *planned* next unused seed from the moment the batch starts; `null` under Randomize or when the seed range ran out), where `step` is the current `{step, total_steps, caption, aspect_ratio, seed, prompt, negative, warnings}` or `null`. The dialog uses it to reattach after a reload.
+
+### `POST /api/t2i/batches/{batch_id}/cancel`
+Cancels the batch's task (including an in-flight VLM call) and every unfinished job. Returns `{status: "cancelled"}`; idempotent for a batch that already finished. **404** for an id the server has never seen.
+
+### `GET /api/t2i/images?limit=60&before_id=`
+Generated images, newest first, each with a **complete** `form_state`: `{id, file_path, file_name, batch_id, model, preset_id, caption, prompt_used, negative_used, seed, prompt_seed, width, height, megapixels, aspect_ratio, loras, render_s, comfy_prompt_id, created_at, is_favorite, form_state}`. Rows whose media is gone are pruned.
+
+### `PATCH /api/t2i/images/{image_id}`
+Autosaves the dialog's form into one image's editable `form_state`. Partial: send only what changed. The as-rendered columns are not reachable from here. **400** for an unknown field or a bad value (the message names the field); **404** for an unknown id.
+
+### `DELETE /api/t2i/images/{image_id}`
+Deletes the `t2i_images` row plus its media row (the file goes to the OS trash). Returns `{status: "deleted"}`. **404** for an unknown id.
+
+### `GET /api/t2i/paths`
+Native paths of every T2I image still in the library, for the smart-folder rule *Generated with T2I*.
+
+### `GET /api/t2i/output-preview?root=&prefix=`
+Where an image generated now would land for the given (possibly unsaved) `output_root` / `output_prefix`: `{path, error, warnings}`. Always **200**; `path` is `null` on error. A blank `root` previews the default location, `<comfy.output_root>/t2i`.
+
+### `t2i` WebSocket channel
+- **`batch_started`** — `{batch_id, mode, total_steps, total_images}`.
+- **`batch_step`** — `{batch_id, step, total_steps, caption, aspect_ratio, seed, prompt, negative, warnings}`; fills the dialog's Caption, Prompt, Aspect and Seed boxes.
+- **`batch_progress`** — `{batch_id, phase: "prompting" | "rendering", images_done, images_failed, images_total, next_seed, last_error?}`.
+- **`batch_complete`** / **`batch_cancelled`** — `{batch_id, images_done, images_failed, images_total, images_cancelled, next_seed}`; exactly one terminal event per batch.
+- **`batch_error`** — the same counters plus `error` (and `last_error` when a job failed earlier). A failed submit cancels the batch's unfinished jobs first.
+- **`t2i_images_changed`** — `{batch_id, files}`, sent once a job's outputs have been ingested as `t2i_images` rows.
+
+Per-job progress reuses the `comfy` channel (`job_update`, `job_progress`).
PATCH
````

- [ ] **Step 5: Apply the CLAUDE.md edits**

This adds the t2i module lines to the architecture tree, `t2i` to the WebSocket channel list, the t2i decisions block before `## Development Rules`, and corrects the two stale statements verified in Step 1.

````bash
git apply --whitespace=nowarn <<'PATCH'
diff --git a/CLAUDE.md b/CLAUDE.md
index de84aa2..ff586cd 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -47,6 +47,13 @@ metascan/
       vocabulary.py         # CLIP tagging vocabulary loader + encoder with .npz cache
       watcher.py            # File system monitoring (watchdog)
       hardware.py           # Tier classification, feature gates, device picker (CUDA/MPS/Vulkan/glibc/NLTK probes)
+      t2i_characters.py     # Pure caption engine: seeded character descriptions, owner-bound body-part tokens
+      t2i_wildcards.py      # characters.yml + <slot>[.female|.male].txt lists, adult-only guard, hot reload
+      t2i_captions.py       # CaptionStore: CSV index, filters, no-repeat picker
+      t2i_models.py         # Model profiles (krea2 / qwen / sd / zimage)
+      t2i_prompt.py         # Prompt composition from META_* guidelines, fallbacks
+      t2i_form.py           # Form-state validation, seed stepping, t2i_dims
+      t2i_runner.py         # T2iRunner: server-side batches, windowed submit, ingest
     extractors/         # Metadata extractors (ComfyUI, SwarmUI, Fooocus)
     cache/              # Thumbnail cache (Pillow + FFmpeg)
     workers/            # Subprocess entry points
@@ -58,6 +65,8 @@ metascan/
   data/
     vocabulary/         # CLIP tagging inputs (oidv7 / imagenet / aesthetics / nsfw / excluded)
                         # plus cached encoded matrix: vocab.<model_key>.npz
+    t2i_captions/       # t2i_captions.csv (your data, git-ignored) + characters.yml and the
+                        #   starter lists (age, ethnicity, skin, eyes, face, hair, body.*)
 
   frontend/             # Vue 3 SPA
     src/
@@ -86,7 +95,8 @@ metascan/
                         #   ImageViewer, VideoPlayer, SlideshowViewer
         dialogs/        # ScanDialog, SimilaritySettings, DuplicateFinder,
                         # UpscaleDialog, UpscaleQueue, ConfigDialog (+ ConfigModelsTab),
-                        # NewFolderDialog, SmartFolderEditor
+                        # NewFolderDialog, SmartFolderEditor, T2IDialog, ConfigT2ITab
+        generation/     # SeedControls, JobTile, ThumbStrip/ThumbTile (T2I; I2V does not use them yet)
       types/            # TypeScript interfaces (Media, FilterData, WsMessage,
                         #   folders.ts: RuleField, RuleOp, SmartRules, AnyFolder,
                         #   hardware.ts: Tier, Gate, HardwareReport, HardwarePayload)
@@ -106,7 +116,7 @@ metascan/
 - **Dim-mismatch guard.** Before FAISS search, `_assert_dim_matches` returns HTTP 409 `{code:"dim_mismatch", index_dim, model_dim, ...}` when the current CLIP model's embedding dim differs from the on-disk index. The frontend's `ApiError` in `client.ts` preserves `detail` so the UI can render an actionable "Rebuild index" banner.
 - **HuggingFace HEAD probe suppression.** `embedding_manager._check_model_needs_download` is authoritative; when weights are cached, the loader sets `HF_HUB_OFFLINE=1` around `open_clip.create_model_and_transforms` to skip the etag revalidation.
 - **Core modules use callbacks** for event dispatch: `on_progress`, `on_complete`, `on_error`, `on_status`, `on_task_added`, etc.
-- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy`, `storyboard`, `i2v`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`. The `folders` channel broadcasts `folder_created` / `folder_updated` / `folder_deleted` / `folder_items_changed` for cross-tab sync. The `storyboard` channel broadcasts `synthesis_progress` (`{storyboard_id, panel_id, beat_id, done, total, prompt_source}`), `synthesis_complete` (`{storyboard_id, synthesized, fallback, skipped_locked}`), `synthesis_error` (`{storyboard_id, error}`), and `beat_images_changed` (`{storyboard_id, panel_id, beat_id, files}`) from `StoryboardRunner`'s own `on_event` callback — the `folder_created` / `folder_items_changed` events it also emits go out on the `folders` channel, not `storyboard`. `POST /api/storyboard/{id}/synthesize` is 202 fire-and-forget (`asyncio.create_task`); `synthesis_complete`/`synthesis_error` are the only signal a client gets that the background run actually finished or died — `StoryboardRunner.synthesize` wraps the real work and always emits exactly one of the two, re-raising after `synthesis_error` so a direct (non-route) caller still sees the exception.
+- **WebSocket is multiplexed** — a single `/ws` connection carries all channels (`scan`, `upscale`, `embedding`, `watcher`, `models`, `folders`, `comfy`, `storyboard`, `i2v`, `t2i`) with JSON envelope `{channel, event, data}`. The `models` channel broadcasts `inference_status`, `inference_progress`, `download_progress`, `download_complete`, `download_error`. The `folders` channel broadcasts `folder_created` / `folder_updated` / `folder_deleted` / `folder_items_changed` for cross-tab sync. The `storyboard` channel broadcasts `synthesis_progress` (`{storyboard_id, panel_id, beat_id, done, total, prompt_source}`), `synthesis_complete` (`{storyboard_id, synthesized, fallback, skipped_locked}`), `synthesis_error` (`{storyboard_id, error}`), and `beat_images_changed` (`{storyboard_id, panel_id, beat_id, files}`) from `StoryboardRunner`'s own `on_event` callback — the `folder_created` / `folder_items_changed` events it also emits go out on the `folders` channel, not `storyboard`. `POST /api/storyboard/{id}/synthesize` is 202 fire-and-forget (`asyncio.create_task`); `synthesis_complete`/`synthesis_error` are the only signal a client gets that the background run actually finished or died — `StoryboardRunner.synthesize` wraps the real work and always emits exactly one of the two, re-raising after `synthesis_error` so a direct (non-route) caller still sees the exception.
 - **Tag inverted index tracks source.** `indices.source` is one of `'prompt'` / `'clip'` / `'both'` for tag rows, NULL for other index types. `_generate_indices` emits `(type, key, source)` triples; `_update_indices` preserves CLIP-sourced tags across rescans by downgrading `'both'` → `'clip'` before rewriting prompt rows. Use `db.add_tag_indices(path, tags, source='clip')` from the embedding worker — it upserts with conflict-merge.
 - **Folders persist via `/api/folders`.** Two tables: `folders(id, kind ∈ {manual,smart}, name, icon, rules JSON, sort_order, created_at, updated_at)` and `folder_items(folder_id, file_path, added_at)` with `ON DELETE CASCADE` on both sides. The frontend Pinia store (`stores/folders.ts`) does optimistic local updates with API-backed persistence and rolls back on failure. The `folders` WS channel broadcasts every mutation so other tabs stay in sync. A one-shot localStorage → API import runs on first load when the server returns empty; guarded by a localStorage flag.
 - **Smart-folder evaluator is synchronous and client-side.** Rules are a JSON blob evaluated per Media in `stores/folders.ts::evaluateCondition`. Tag conditions can't rely on `m.tags` because the summary endpoint omits it — the store fetches only the tag keys referenced by saved smart folders via `POST /api/filters/tag_paths` with `{keys: […]}` and evaluates against those path sets. A previous bulk-GET version fetched the entire inverted index and blocked the media list endpoint for 20+ s; never restore that shape. The "Has I2V video" rule (`field: 'i2v'`, bool) follows the same cache pattern: `GET /api/i2v/sources` returns the source images that have one or more clips still in the library (`db.list_i2v_source_paths`, JOINed on `media`). It's fetched only while a saved folder or the open editor uses the rule, and refetched on the `i2v` channel's `i2v_videos_changed`, after a clip delete, and on a forced `loadTagPaths`. It's deliberately not a column on `/api/media`, so the covering indexes don't change.
@@ -1154,7 +1164,7 @@ metascan/
   user picks only a megapixel budget (`i2v.megapixels` /
   `default_megapixels`). `i2v_compiler.i2v_dims(src_w, src_h,
   megapixels)` preserves the source aspect, snaps each edge to a
-  multiple of 16 and floors at one multiple so an extreme panorama still
+  multiple of 32 (`I2V_DIM_MULTIPLE` — H3's width/height widgets step by 32) and floors at one multiple so an extreme panorama still
   yields a usable short edge; `I2vRunner.generate` takes `megapixels`
   (never client-supplied dimensions) and resolves the source's real size
   from the media row, falling back to the file header for an image that
@@ -1382,6 +1392,15 @@ metascan/
   stale rewrite must never clobber newer typing). Never auto-apply on
   Generate: the box is "Generate uses this text".
 
+- **t2i flow (text→image).** A sparkles button at the top right of the main screen (left of Storyboards) opens `T2IDialog`. A caption — typed, or drawn at random from `data/t2i_captions/t2i_captions.csv` — goes through `resolve_caption` → a VLM rewrite in the selected model's style (`META_*` guideline) → a kind-`t2i` ComfyUI workflow. `metascan/core/t2i_runner.py::T2iRunner` mirrors `I2vRunner`'s layering: `ComfyClient` stays generic, jobs are correlated by `generation_jobs.t2i_batch_id`, ingest is keyed on it, and the `t2i` WS channel carries `batch_started` / `batch_step` / `batch_progress` / `batch_complete` / `batch_cancelled` / `batch_error` / `t2i_images_changed`. **Batches are server-side background tasks** — they survive closing the dialog, but their state is in memory, so a server restart drops unsubmitted steps (jobs already queued still ingest, with NULL `form_state`). Only one Random batch runs at a time (409 `random_batch_active`); Manual batches are unrestricted. Every image is its own job with latent `batch_size=1`: the dialog's *Batch Size* and *Count per Batch* are job counts, never the latent batch.
+- **The caption engine is a pure function of `(caption, seed, style, list snapshot)`** (`t2i_characters.resolve_caption`). Draws are `sha256(f"{seed}|{name}|{slot}|{salt}")` modulo the list length — **never `hash()`** — so the same caption and seed always resolve identically, adding a character to a caption never changes another's description, and hair is forced distinct across the cast. A first mention becomes the description: inline `with …` only when a verb follows, otherwise the head noun inline plus one trailing sentence — **never parentheses** (ComfyUI parses them as weights). Later mentions follow the model's identity style (`ref` "the copper-red-haired woman" / `noun` / `name`). `__HAIR__ __BREASTS__ __VAGINA__ __PENIS__` are owner-bound (a `his`/`her` pronoun, else the nearest character named before) and fall back to the bare word for body hair (`pubic __HAIR__`), fused suffixes (`__HAIR__brush`) or a missing list. A step's character seed is its FIRST image's seed (`t2i_images.prompt_seed`). Lists in `data/t2i_captions/` hot-reload; `age*.txt` rejects any number under 18 and every list rejects minor-indicating terms — **adult-only is a load-time invariant, do not loosen it.** `.gitignore` un-ignores `data/t2i_captions/` except `*.csv` (the CSV is user data).
+- **Caption store.** `CaptionStore` indexes the CSV in memory on first use (line offsets + numpy columns; ~0.7 s and ~54 MB for 82,880 rows, ~11 ms per filter; caption text is read lazily by offset; records are quote-balanced physical lines so an embedded newline cannot break it). Do not load it into SQLite. `CaptionPicker` samples without replacement, then reshuffles. The CSV has no Size column — Random mode takes only the aspect ratio from the row.
+- **Prompt generation reuses the existing model guidelines.** `t2i_prompt.compose_t2i_prompts` = `T2I_CAPTION_PREAMBLE` + the model's `META_*` entry (via `PromptStore`, hot-reloaded) + the existing `UNCENSORED_DIRECTIVE` / `SAFETY_DIRECTIVE` per `t2i.content_mode`; the user turn is `DESCRIPTION:\n<resolved>\n\nWrite the prompt now.` The shared `TargetModel` literal is deliberately NOT extended — `t2i_models.MODEL_PROFILES` (`krea2`, `qwen`, `sd`, `zimage`) map to YAML keys directly, and `META_KREA2` is a starter to tune. `Negative:` blocks are split with `meta_prompt_templates.split_negative_block`. No VLM → a deterministic fallback (the resolved caption; stock negatives for SDXL/Qwen) plus a warning; a VLM error inside a batch retries once, then falls back so an unattended run never dies on one bad call.
+- **GPU order and queue politeness.** In Random mode with `comfy.unload_vlm_during_generation` on, every prompt is written first, then the VLM is shut down (only when no Random batch is still prompting), then jobs are submitted; off → each step renders as soon as its prompt is ready. Jobs go through a per-batch window (`t2i.window`, default 4) that is released on terminal `job_update` events, so a large batch cannot starve i2v/storyboard jobs in `ComfyClient`'s shared FIFO. `T2iRunner._job_meta` is popped by the ingest task for `done` jobs (`job_outputs` fires BEFORE the terminal `job_update`, so popping on `done` would race the ingest) and on failed/cancelled events, with a bounded backstop.
+- **`t2i_images` mirrors `i2v_videos`.** No foreign keys (list queries JOIN `media` and prune), as-rendered facts vs the editable `form_state`; `set_t2i_image_form_state` is the ONLY update and no route may write the fact columns. Seed policy, Batch Size and Count per Batch are session-local and never restored from `form_state`, so selecting a tile cannot trigger a large re-run.
+- **T2I frontend.** The shared pieces in `components/generation/` and `composables/{useJobTracker,useFormAutosave}` are NEW files; I2V does not use them. The overlay closes only via ✕ (no `@click.self`). `useWebSocket` is called in the dialog (it needs setup context) and forwarded to `stores/t2i.ts::handleT2iEvent` / `handleComfyEvent`; the store never subscribes itself. **Autosave-drift rule:** the dialog form is written only by `initForm`, a tile load (`mergeFormState` then `autosave.markLoaded()`), the unlock patch (only when no image is selected) and the user — never add a watcher that writes it, or it will autosave into the selected image. **Starting a batch releases the selected image** (`autosave.flush()` then `store.clearSelection()` before the seed advances), and a Random batch takes the form over (tiles are refused, the boxes mirror `store.currentStep`), so an automatic change can never rewrite the saved form of the image it came from. The library grid reloads through ONE debounced, quiet `store.scheduleMediaReload` (`loadAllMedia({ silent: true })`, no dimming overlay) that both the dialog forwarder and `App.vue`'s always-on `t2i` bridge call, so images arriving while the dialog is closed still reach the grid. The viewer opens on a frozen snapshot of the strip because images arrive mid-batch; the filter panel is `position: fixed` (a panel absolutely positioned inside the scrolling card is clipped); Caption/Prompt are plain textareas because `TextEditPopup` collapses newlines; the scratch-form draft lives in localStorage under `metascan.t2i.draft.v1`. An unknown job id on the `comfy` channel is adopted by a one-off `GET /api/comfy/jobs/{id}` lookup (frames carry no batch id), so `t2i_batch_id` must stay on that route.
+- **Workflows and the smart-folder rule.** The dialog lists every kind-`t2i` preset (no schema change). `workflow_validation._KIND_VALIDATORS` runs kind-level checks regardless of target/mode (`t2i`: warns on a missing `MS_LORA_STACK` / `MS_NEGATIVE`). `PresetRegistrationDialog` takes a `kind` prop; `t2i` presets carry no video tag. The "Generated with T2I" rule (`field: 't2i'`, bool) reads `GET /api/t2i/paths` through a `makePathSetCache` helper, refetched on `t2i_images_changed`, after an image delete, and on a forced `loadTagPaths`.
+
 ## Development Rules
 
 ### Python
@@ -1523,7 +1542,7 @@ When adding new user-facing documentation:
 3. Add a `case '<field>':` to `evaluateCondition` — keep it synchronous; async work belongs in a precomputed path-set cache (see the tags pattern).
 4. If the rule reads a column not already on the `/api/media` summary, add it to the SELECT in `get_all_media_summaries` **and** to every covering index (`idx_media_summary_added`, `idx_media_summary_modified`) — otherwise `/api/media` falls back to the main-table scan. `Media` frontend type gets the new field too.
 5. If conditions carry server-resolved references (e.g. tag keys, later CLIP queries), add an endpoint that takes an explicit key list and cache responses in the store keyed by referenced values. Don't bulk-GET the whole universe.
-6. Extend `migrateSmartFolder` in `stores/folders.ts` if you're removing/renaming an existing field so persisted rules don't crash the editor.
+6. `migrateSmartFolder` in `stores/folders.ts` only runs for the legacy localStorage import (`readLegacyLocalStorage`); rules persisted through `/api/folders` are opaque JSON and never pass through it, so removing or renaming a field needs its own handling where server rules are loaded.
 
 ### Adding a new VLM caption style
 1. Add the style key to `CAPTION_STYLE_PROMPTS` in `metascan/core/vlm_prompts.py`.
PATCH
````

- [ ] **Step 6: Check every doc link and code name the new text uses**

Run:
```bash
git grep -n "docs/t2i.md" README.md CLAUDE.md
python - <<'PY'
import re, pathlib
text = pathlib.Path("docs/t2i.md").read_text()
for m in re.finditer(r"\]\(([^)#]+)(#[^)]*)?\)", text):
    target = m.group(1)
    if target.startswith("http"):
        continue
    ok = (pathlib.Path("docs") / target).exists()
    print(("ok   " if ok else "MISSING ") + target)
PY
for f in metascan/core/t2i_characters.py metascan/core/t2i_wildcards.py metascan/core/t2i_captions.py \
         metascan/core/t2i_models.py metascan/core/t2i_prompt.py metascan/core/t2i_form.py \
         metascan/core/t2i_runner.py backend/api/t2i.py backend/services/t2i_service.py; do
  test -f "$f" && echo "ok   $f" || echo "MISSING $f"
done
```
Expected: `README.md` and `CLAUDE.md` each reference `docs/t2i.md`; every link prints `ok`; every module prints `ok`.

- [ ] **Step 7: Full quality gates**

Run:
```bash
venv/bin/black --check metascan/ backend/ tests/
venv/bin/flake8 metascan/ backend/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/
venv/bin/pytest -q
cd frontend && npm run build && cd ..
```
Expected: flake8 reports `0`; mypy reports `Success: no issues found`; `npm run build` type-checks and builds without errors; pytest passes apart from the known watcher flake. `black --check` reports exactly one file, `metascan/core/i2v_compiler.py`, which already fails at the commit this branch started from (a missing blank line before `build_i2v_user_prompt`); it is I2V code, so leave it alone. That is also why `make quality` exits non-zero on black regardless of this work. If `test_file_watcher_triggers_reload` fails, re-run it alone (`venv/bin/pytest -q tests -k test_file_watcher_triggers_reload`): it is a known WSL2 flake that passes in isolation and is not a regression signal. Any other failure is real; fix it before committing.

- [ ] **Step 8: Manual walkthrough against a running app**

Start the backend (`source venv/bin/activate && python run_server.py`) and the frontend (`cd frontend && npm run dev`) with ComfyUI running and a `t2i` workflow registered. Check each item and note anything that differs:

1. The sparkles icon is at the top right of the main screen, immediately left of Storyboards, with the tooltip "Text to Image". It opens the dialog; the ✕ is the only way to close it.
2. **Configuration → Text to Image** shows your registered `t2i` workflows (Validate warns about a missing `MS_LORA_STACK` / `MS_NEGATIVE`), a default-workflow select per model, the output folder picker, and a prefix box whose live preview ends in `.png`. Save, reopen the dialog: choosing a model selects its default workflow.
3. **Manual:** type a caption using `__ALICE__` and `__HAIR__`, press **Generate Prompt**. The collapsible *Resolved caption* shows a character description and a hair colour; the prompt appears in the selected model's style. Switch to Qwen-Image or SDXL: a Negative box appears. Switch to Krea 2: it disappears.
4. Set **Count per Batch** 3 with **Increment**, press **Generate**: three job tiles with progress, then three thumbnails with consecutive seeds. The seed box now shows the next seed. The images are in the library grid and under the configured folder and prefix.
5. Set the policy to **Fixed** with Count per Batch 3: only one image is made; the field still shows 3.
6. **Random Caption:** open **Filter**, set Females 2, watch "N captions match" change. With Batch Size 3 and Count per Batch 2 press **Generate**: Caption, Prompt, Aspect and Seed lock and update per step; with `comfy.unload_vlm_during_generation` on, all three prompts are written before any image renders; six images arrive.
7. Press **Cancel** during a Random run: unfinished job tiles disappear, no more images arrive, the boxes unlock, the seed box shows the next unused seed.
8. Close the dialog mid-run and reopen it: the status line and locked boxes reattach; the run finishes.
9. Select a thumbnail: the form loads its mode, caption, prompt, seed, size and workflow. Edit the prompt, wait a second, reload the page, select the same thumbnail: the edit persisted. **Stop editing** returns to a scratch form.
10. **★** stars an image (it shows as a favorite in the library); **✕** asks to confirm, then removes the file and its library entry.
11. Double-click a thumbnail while a batch is running: the viewer stays on that image when new images arrive.
12. Create a smart folder with the rule **Generated with T2I**: it lists only these images, gains a new one when a batch finishes (no reload), and loses one when you delete it.
13. Add a LoRA against a workflow with no `MS_LORA_STACK`: the toast names the problem. With one that has it, the LoRA appears in ComfyUI's history.
14. Stop the VLM and press **Generate Prompt**: the prompt is the resolved caption and a warning says why. A Random batch still runs, with per-step warnings.
15. Kill the server mid-batch and restart it: queued jobs still finish and appear in the strip (without a saved form).
16. While a Random batch runs, Generate is disabled; a direct second `POST /api/t2i/batches` with `mode: "random"` returns 409.
17. I2V still works: open an image's Image to Video dialog and generate one clip.

- [ ] **Step 9: Commit**

```bash
git add docs/t2i.md README.md docs/configuration.md docs/architecture.md docs/api-reference.md CLAUDE.md
git commit -m "docs(t2i): feature guide, API and config reference, CLAUDE.md rules" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
