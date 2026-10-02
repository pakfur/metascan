# T2I caption classifier — design

Date: 2026-10-01
Status: approved in brainstorming, awaiting spec review

## 1. Purpose

`data/t2i_captions/t2i_captions.csv` holds 82,880 captions (median ~965
chars, max ~5.3k). Many are deficient in ways that hurt generation:
no emotional cue, a sexual act implied but never named, or an
anatomically / compositionally impossible arrangement.

This is a **one-time, fully local** classifier that scores every caption on
three axes and writes the scores to a sidecar CSV. It produces **scores and
flags only** — no prose, no suggested fills, no edits to the captions CSV.
The user reviews `classifications.csv` manually and later decides how to
turn it into caption updates (threshold-based emotion wildcards, an act
rewrite pass, a manual plausibility pass). Those later passes are out of
scope.

### Success criteria

- Every caption in the CSV has one row in `classifications.csv` (errors
  marked, not dropped).
- Each forced-choice answer carries a probability distribution read from
  token logprobs, not a self-reported confidence.
- The full run finishes in one overnight session on the RTX 5090 and is
  resumable after interruption.
- `eval.py` on the constructed fixtures reports per-field accuracy and
  calibration good enough for the user to trust thresholds.

### Non-goals

- Writing to or rewriting `t2i_captions.csv`.
- Any app / API / UI integration. This is a standalone script.
- Image input. Captions are classified as text only.

## 2. Data facts that shape the design

- Columns: `Caption, Aspect Ratio, Nudity, Artistic Quality, Erotic Score,
  Pornographic Score, Males, Females, Clothing`.
- 92% of rows are `Males=0, Females=1`; 4.2k are `0/2`; 1.5k are `1/1`.
- Captions use placeholders: `__ALICE__`, `__BELLA__`, `__CLARA__`,
  `__DIANNA__`, `__ADAM__`, `__HAIR__`, `__BREASTS__`, `__VAGINA__`,
  `__PENIS__`. They are given facts, not content to judge.
- `Males` / `Females` count **in-frame** subjects only. POV and
  partially visible partners (e.g. "two hands visible" on a `0/1` row) are
  common, so counts alone cannot gate partnered acts — see §4.2.

## 3. Architecture

```
t2i_captions.csv ──read-only──▶ reader ──▶ queue ──▶ N async workers ──HTTP──▶ llama-server
                                  │                        │              (qwen3vl-30b-a3b, text-only,
                     skip rows already in                  │               --parallel 16, thinking off)
                     results.jsonl (resume)                ▼
                                                   parse logprobs, gate acts,
                                                   verify quotes
                                                           │
                                                           ▼
                                            results.jsonl (append-only)
                                                           │
                                            summarize.py ──▶ classifications.csv
```

Location: `scripts/caption_classifier/`

| File | Responsibility |
|---|---|
| `rubric.py` | Act table, emotion scale, partner options, issue types, prompt text, `PROMPT_VERSION` (hash of rubric + prompt + grammar) |
| `grammar.py` | Builds the GBNF output grammar from the rubric |
| `parse.py` | Extracts per-field letter distributions from a logprobs response; applies the act participant gate; verifies quotes |
| `server.py` | Spawns / health-checks / restarts / stops `llama-server` |
| `classify.py` | CLI entry: reads the CSV, runs the worker pool, appends JSONL, resume and versioning |
| `summarize.py` | JSONL → `classifications.csv` |
| `eval.py` | Runs the classifier on constructed fixtures and reports accuracy / calibration |
| `fixtures/` | Hand-written evaluation captions with expected answers |

Reuse from the app, read-only: `metascan.utils.llama_server.binary_path()`,
`metascan.core.vlm_models.REGISTRY` (model GGUF paths), and
`metascan.core.t2i_captions.CaptionStore` for parsing the CSV (identical
handling of quoted newlines, BOM, CRLF). The app's `VlmClient` is not used,
and the script never touches a running app or port 8700.

## 4. Classification schema

One request per caption returns all fields. Order is fixed:
`partner`, `kiss`, `emotion`, `act`, `issues`.

### 4.1 Partner (uncounted participant)

| Letter | Meaning |
|---|---|
| A | none |
| B | male — a penis or male anatomy is described beyond the counts |
| C | female |
| D | unknown — hands or body parts only, gender not stated |

### 4.2 Effective counts and the act gate

`eff_M = Males + (partner == B)`, `eff_F = Females + (partner == C)`,
`eff_total = Males + Females + (partner != A)`.

An act is allowed when `eff_*` meet its minimums. Acts marked "partner
allowed: no" use the raw CSV counts only. The gate is applied **after**
generation: the model's raw act distribution over all letters is kept, then
disallowed acts are zeroed and the rest renormalised. Both distributions are
stored. A high raw probability on a disallowed act is kept as
`act_gate_conflict` — usually a sign the counts or caption are wrong.

The partner used for the gate is the argmax partner letter.

### 4.3 Kissing

Separate yes/no field (`Y` / `N`) because it co-occurs with every partnered
act. Removed from the act list.

### 4.4 Emotion

| Letter | Label | Meaning |
|---|---|---|
| A | none | no expression, mood or attitude described |
| B | implicit | only gaze, posture or vague words ("poised", "intimate") hint at a mood |
| C | explicit | a facial expression or emotional state is named (smiling, pouting, lips parted, eyes closed in pleasure, aloof, playful…) |

Rule: if any subject has an explicit expression, the answer is `C`.

### 4.5 Act

Requires = minimum (males, females, total). "Partner" = whether the
uncounted participant can satisfy the requirement.

| Letter | Act | Requires | Partner | Look for |
|---|---|---|---|---|
| A | none-artistic | — | — | Posing, nudity or suggestive framing with no sexual contact or touching of genitals |
| B | breast-fondling | –, 1, 1 | yes | Hands (her own or a partner's) cupping, squeezing or pinching breasts or nipples, in a sexual setting |
| C | female-masturbation | –, 1, 1 | no | Her own hand on her vulva, crotch or between her legs |
| D | female-toy-masturbation | –, 1, 1 | no | Her own hand holding a dildo, vibrator or phallic object at or between her legs |
| E | partner-manual-female | –, 1, 2 | yes | Another person's fingers or hand between her legs |
| F | object-insertion | –, 1, 1 | yes | A toy, speculum or other object inserted or held at the genitals by someone else, including clinical settings |
| G | male-masturbation | 1, –, 1 | no | His own hand on his erect penis |
| H | handjob | 1, –, 2 | yes | Someone else's hand on his erect penis |
| I | fellatio | 1, –, 2 | yes | Erect penis in or right next to a partner's mouth |
| J | cunnilingus | –, 1, 2 | yes | Her legs parted with a partner's face or mouth at her crotch |
| K | missionary | 1, 1, 2 | yes | She lies on her back, legs apart or raised, with him on top of or between them |
| L | doggy | 1, 1, 2 | yes | She is bent forward or on hands and knees with him behind her; one or both nude |
| M | cowgirl | 1, 1, 2 | yes | She straddles his hips or groin facing him; partly or fully nude |
| N | reverse-cowgirl | 1, 1, 2 | yes | She straddles him facing away, toward his feet or the viewer |
| O | spooning | 1, 1, 2 | yes | Both lying on their sides, him behind her, pelvises together |
| P | standing-sex | 1, 1, 2 | yes | Both standing, pelvises joined; she may be lifted or against a wall |
| Q | paizuri | 1, 1, 2 | yes | Penis between her breasts |
| R | ff-tribbing | –, 2, 2 | no | Two women, crotches pressed together, legs interlocked |
| S | unclear | — | — | Sexual content is suggested but no single act fits, or two acts are equally likely |

Masturbation acts mean the subject's **own** hand; another person's hand is
`partner-manual-female` / `handjob`. Anal, 69 and group acts are
deliberately excluded and fall to `unclear`; add later if `unclear` is
large. Letters are stable — changing the table changes `PROMPT_VERSION`.

### 4.6 Plausibility issues

Array of up to 4 entries, each `{type, quote_a, quote_b}`:

| Type | Meaning |
|---|---|
| `extra_limb` | more than two hands, arms or legs on one subject, or the same left/right limb placed in two spots |
| `gaze_conflict` | looking away while making eye contact, or with face details given |
| `facing_conflict` | facing away while front details are described |
| `count_conflict` | more people described than the counts allow |
| `impossible_contact` | contact the stated positions rule out |

Quotes are short verbatim excerpts (≤120 chars). The prompt asks the model to
report when unsure, since a person reviews these. After parsing, each quote
is checked against the caption (whitespace- and case-normalised substring);
the result is stored as `quote_verified` and unverified issues are kept, not
dropped.

## 5. Prompt and grammar

**System message** (identical for every request, so the prefix is cached):
role and placeholder note → partner question → kissing → emotion scale →
act table with "look for" text → issue types → 4–5 short constructed worked
examples (never taken from the CSV).

**User message:**
```
Counts: M=<Males> F=<Females> · Nudity: <Nudity> · Erotic <score> · Porn <score>
Caption: <caption>
```

**Thinking off** via `chat_template_kwargs: {"enable_thinking": false}`.

**Grammar:** a JSON object in the fixed field order. Letter fields are a
choice of literal single characters. `issues` is an array (max 4) with
`type` from the five names and quotes as strings without escaped quotes,
capped at 120 chars. Hyphens appear only as literals — never `\-`, which
segfaults `llama-server` (see `.claude/rules/vlm-llama.md`).

**Logprobs:** each request sets `logprobs: true, top_logprobs: 20` so every
act letter is covered. The parser locates each letter field by matching
where the field's value starts in the token stream (the tokenizer can merge
the letter with neighbouring characters), takes the distribution over that
field's allowed letters, and renormalises. Letters missing from the top-20
get probability 0.

## 6. Running the classifier

### CLI

```
python -m scripts.caption_classifier.classify \
    [--csv data/t2i_captions/t2i_captions.csv] \
    [--out data/t2i_captions/classifier/] \
    [--model qwen3vl-30b-a3b] [--parallel 16] [--ctx-per-slot 4096] \
    [--count N]            # classify only the first N rows (manual testing)
    [--sample N --seed S]  # classify a random N rows
    [--new-run]            # start a new results file when PROMPT_VERSION changed
```

`--count` and `--sample` are mutually exclusive. Without either, all rows
are classified.

### Server

Launches `binary_path()` with the model GGUF, no `--mmproj`,
`--parallel <N>`, `--ctx-size <N × ctx-per-slot>`, `--host 127.0.0.1` and a
free port. Waits on `/health` before sending work. Stopped on exit.

### Resume and versioning

`results.jsonl` rows are keyed by `(row_id, caption_sha1)`. On start, rows
already present with the current `PROMPT_VERSION` and status `ok` are
skipped, so a `--count 200` trial is not paid for again by the full run.
If the file holds rows from a different `PROMPT_VERSION`, the script refuses
to start unless `--new-run` is given, which writes to a new
`results-<version>.jsonl`.

### Failure handling

- HTTP error, 60 s timeout or unparseable output → retried twice, then the
  row is written with `status: "error"` and the reason.
- Server crash → restarted once and the run continues; a second crash stops
  the run cleanly with everything written so far kept.
- Ctrl-C → finishes in-flight requests, flushes the JSONL, stops the server.

### Progress

A progress line every 30 s (rows done / total, rows/s, ETA, error count) to
the terminal and to `logs/caption_classifier.log` via
`metascan/utils/log_files.py` (rotating handler, per CLAUDE.md).

### JSONL row

```json
{
  "row_id": 123, "caption_sha1": "…", "prompt_version": "…", "model": "qwen3vl-30b-a3b",
  "status": "ok",
  "partner": {"A": 0.91, "B": 0.06, "C": 0.01, "D": 0.02},
  "kiss": {"Y": 0.03, "N": 0.97},
  "emotion": {"A": 0.12, "B": 0.71, "C": 0.17},
  "act_raw": {"A": 0.80, "...": 0.0},
  "act_gated": {"A": 0.84, "...": 0.0},
  "act_gate_conflict": false,
  "issues": [{"type": "extra_limb", "quote_a": "…", "quote_b": "…", "quote_verified": true}],
  "raw_output": "…"
}
```

## 7. Output: `classifications.csv`

Produced by `summarize.py` from the JSONL. One row per caption, in CSV
order. Columns:

- `row_id`, `caption_sha1`, `status`
- `partner`, `partner_p`
- `kiss_p` (probability of Y)
- `emotion`, `p_emotion_none`, `p_emotion_implicit`, `p_emotion_explicit`
- `act_1`, `act_1_p`, `act_2`, `act_2_p`, `act_3`, `act_3_p` (gated)
- `act_raw_top`, `act_raw_top_p`, `act_gate_conflict`
- `issue_types` (semicolon-joined), `issues` (JSON with quotes and
  `quote_verified`)

`caption_sha1` lets a later update pass detect that the captions CSV changed
since classification.

## 8. Testing and evaluation

### Code tests (pytest, run by `make test`)

- `parse.py`: recorded and hand-written logprobs responses, including a
  letter token merged with a neighbouring character; distributions are found
  and renormalised.
- Act gate: table of counts × partner → allowed acts (e.g. `M=0 F=1` with
  partner `B` allows `fellatio`; with partner `A` it does not;
  `ff-tribbing` never accepts a partner).
- Quote verification: exact, whitespace/case variants, fabricated.
- Grammar: no `\-`, every letter present; smoke-loaded through
  `tests/_fake_llama_server.py` (extended if needed).
- Resume / versioning: 10-row CSV, interrupt, rerun → no duplicates or gaps;
  version mismatch refuses without `--new-run`; `--count` limits to the
  first N rows.
- `summarize.py`: stable column order, one row per caption, error rows
  marked.

### Classifier evaluation (`eval.py`, manual, real model)

- 60–80 hand-written fixture captions in the CSV's style (placeholders
  included), **none taken from the CSV**, each with expected answers:
  every emotion level including multi-subject cases; at least two per act
  including POV variants and near misses; each issue type plus clean
  controls with complex but valid poses.
- Corruption pairs: a clean fixture plus a copy with its expression
  sentence deleted, a second "right hand" placement added, or "facing away"
  added to a face-describing caption — the expected answer changes in a
  known way.
- Reports per-field accuracy, an act confusion table, plausibility recall
  and false-alarm rate, and calibration (predicted probability vs observed
  accuracy).
- Run before the full run and after any rubric change. Poor act accuracy is
  fixed in the "look for" text (new `PROMPT_VERSION`), not in thresholds.

### Trial run

`--count N` (or `--sample 500`) on the real CSV to check speed and skim the
output before the overnight run.

## 9. Throughput estimate

~250 new prompt tokens and ~60–100 output tokens per caption (the system
prefix is cached), so ~20M prompt and ~7M output tokens overall. At
`--parallel 16` on the 30B-A3B MoE this is a few hours. `--model
qwen38-27b` is available for a slower, possibly more careful rerun.
