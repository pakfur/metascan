# Caption classifier

One-time, fully local classifier for `data/t2i_captions/t2i_captions.csv`. For
each caption it scores:

- **emotion**: none / implicit / explicit
- **act**: the most likely sexual act from a fixed list of 19, plus a kissing score
  and whether a partner appears who isn't in the counts (POV, hands only)
- **plausibility issues**: impossible or contradictory details, with quotes from
  the caption

It writes scores only. The captions CSV is never modified.

Design: `docs/superpowers/specs/2026-10-01-caption-classifier-design.md`

## Requirements

- The `qwen3vl-30b-a3b` GGUF in `data/models/vlm/`, and `llama-server`
  (`data/bin/local/llama-server` or the bundled one)
- About 26 GB of free VRAM at `--parallel 16`. Close anything else using the GPU first.
- Run from the repo root with the project venv.

## Run

```bash
# 1. Try it on the first N rows
venv/bin/python -m scripts.caption_classifier.classify --count 20 --parallel 4

# 2. Check accuracy on the hand-written fixtures (prints a report)
venv/bin/python -m scripts.caption_classifier.eval

# 3. Full run (~3 h for ~83k captions at about 8 rows/s)
venv/bin/python -m scripts.caption_classifier.classify --parallel 16

# 4. Write classifications.csv
venv/bin/python -m scripts.caption_classifier.summarize
```

Rows already classified are skipped, so the rows from step 1 aren't redone
in step 3, and an interrupted run continues where it stopped when you rerun
it. Ctrl-C once lets the requests in flight finish; Ctrl-C twice aborts.

## Output

Everything goes to `data/t2i_captions/classifier/` (git-ignored):

| File | Contents |
|---|---|
| `results-<version>.jsonl` | Raw record, one line per caption, with the full probability distributions |
| `classifications.csv` | One row per caption: top answers, probabilities, issues; the file to review |

`<version>` is a hash of the prompt and grammar. If you change the rubric
(`rubric.py`, `prompt.py`, `grammar.py`), the next run refuses to start until
you pass `--new-run`, which begins a new results file.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--count N` | all rows | Only the first N rows |
| `--sample N --seed S` | | N random rows |
| `--parallel N` | 16 | Concurrent requests (llama-server slots) |
| `--ctx-per-slot N` | 6144 | Context tokens per slot |
| `--model ID` | `qwen3vl-30b-a3b` | Any id from `metascan/core/vlm_models.py` |
| `--new-run` | | Start a new results file after a rubric change |
| `--server-url URL` | | Use an already-running llama-server |
| `--csv`, `--out`, `--log-file` | | Input CSV, output directory, log file |

Exit codes: `0` all rows ok · `1` finished with error rows (rerun to retry
them) · `2` bad arguments or input · `3` llama-server failed · `130`
interrupted. The log is at `logs/caption_classifier.log`.

## Tests

```bash
venv/bin/pytest tests/test_caption_classifier_*.py -q
```
