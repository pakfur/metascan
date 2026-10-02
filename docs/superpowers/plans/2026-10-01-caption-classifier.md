# Caption Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone, fully local script that scores every caption in `data/t2i_captions/t2i_captions.csv` for emotional context, likely sexual act and anatomical plausibility, and writes the scores to `classifications.csv`.

**Architecture:** A package under `scripts/caption_classifier/` starts its own text-only `llama-server` (Qwen3-VL 30B-A3B), sends one grammar-constrained chat request per caption, reads each answer letter's probability distribution from the response's `top_logprobs`, applies the act participant gate, and appends one JSON line per caption to a resumable results file. `summarize.py` flattens the results into `classifications.csv`; `eval.py` measures accuracy on hand-written fixture captions.

**Tech Stack:** Python 3.11, asyncio, httpx, llama.cpp `llama-server` (OpenAI-compatible `/v1/chat/completions` with `grammar`, `logprobs`, `top_logprobs`), pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-10-01-caption-classifier-design.md`

## Global Constraints

- Python 3.11 only; format with `black` (25.11.0). Run tools from the main venv directly: `venv/bin/pytest`, `venv/bin/black` — do **not** run `make` (it re-runs `python3 -m venv venv`).
- The captions CSV is **read-only**. Nothing in this package writes to it.
- No app/API/UI integration. Never touch a running app, port 8700 or 5173.
- Text-only: the server command never passes `--mmproj`.
- Thinking off: every request sends `"chat_template_kwargs": {"enable_thinking": false}`.
- Grammar hyphens are literal only — `\-` must never appear in the GBNF (it segfaults `llama-server`).
- Logs go through `metascan/utils/log_files.py` (`rotating_file_handler`); never a bare `open(path, "a")` for a log, never a raw `FileHandler`.
- llama-server stderr must be drained continuously (a full pipe hangs it during model load).
- Answer letters are fixed: partner `A–D`, kiss `Y/N`, emotion `A–C`, act `A–S` (19 acts, `S` = unclear).
- Results are keyed by `(row_id, caption_sha1)` and versioned by `PROMPT_VERSION`.
- Fixture and prompt-example captions are hand-written; **none are taken from the captions CSV**.
- Tests import the package as `scripts.caption_classifier.<module>` (repo root is on `sys.path` because `tests/__init__.py` exists; `scripts/` is a namespace package).

**Deviations from the spec, decided while planning:**
- `--ctx-per-slot` defaults to **6144**, not 4096: the system prompt (~2.5k tokens) plus the longest caption (~1.3k tokens) plus 700 output tokens does not fit in 4096. 16 × 6144 with the model's q8 KV cache is ~5 GB on top of 18.5 GB weights — fits a 32 GB card.
- Results always go to `results-<PROMPT_VERSION>.jsonl`. A run refuses to start when results files for **other** versions exist and the current version's file does not, unless `--new-run` is given. Same protection as the spec, simpler bookkeeping.
- The act gate and quote check live in `parse.py` as specced, but `allowed_acts` lives in `rubric.py` next to the act table it reads; the prompt text and `PROMPT_VERSION` live in `prompt.py`.
- The grammar is checked structurally in pytest (every referenced rule defined, no `\-`); the real "llama-server accepts it" check is Task 11, because the fake server cannot parse GBNF.
- `classifications.csv` gets an extra trailing `error` column so error rows say why.
- The eval fixture set is 44 captions, not 60–80: one or more per act (POV variants for four acts), the emotion levels, three near misses, each issue type, three clean controls and three corruption pairs. Grow it if the eval report shows a weak spot.
- Added `--server-url` (use an already-running server; used by tests and handy for repeated manual trials) and `--log-file`.

## Review Focus

1. **Blank `Males`/`Females` cells** (`CaptionRow.males is None`) — the gate must allow every act, not crash or ban everything. Test in Task 1.
2. **A results file ending in a half-written line** after a hard kill — the next run must ignore the fragment and append cleanly, not glue the next record onto it. Test in Task 5.
3. **All act probability on acts the counts forbid** — gated distribution must fall back to `unclear = 1.0` with `act_gate_conflict = true`, never divide by zero. Test in Task 4.
4. **Non-ASCII captions** (accents, curly quotes) — quote verification must work on them, and broken UTF-8 token pieces inside the free-text `issues` region must not fail the parse. Tests in Tasks 3 and 4.
5. **A caption longer than the slot context** — llama-server answers HTTP 400; the row must become an error row after its retries and the run must continue. Test in Task 7.

---

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/caption_classifier/__init__.py` | Package marker, one-line docstring |
| `scripts/caption_classifier/rubric.py` | Answer letters, act table, issue types, `allowed_acts` |
| `scripts/caption_classifier/grammar.py` | GBNF grammar built from the rubric (`GRAMMAR`) |
| `scripts/caption_classifier/prompt.py` | System prompt, worked examples, `user_message`, `PROMPT_VERSION` |
| `scripts/caption_classifier/parse.py` | Letter distributions from logprobs, act gate, quote check, `build_record` |
| `scripts/caption_classifier/results.py` | `caption_sha1`, row selection, results file open/resume/write/load |
| `scripts/caption_classifier/server.py` | `build_command`, `LlamaServer` (spawn, health, drain, stop, restart) |
| `scripts/caption_classifier/runner.py` | Request body, retries, crash recovery, worker pool, progress |
| `scripts/caption_classifier/classify.py` | CLI entry point |
| `scripts/caption_classifier/summarize.py` | JSONL → `classifications.csv` |
| `scripts/caption_classifier/eval.py` | Fixture evaluation report |
| `scripts/caption_classifier/fixtures/fixtures.json` | Hand-written evaluation captions |
| `tests/_caption_classifier_helpers.py` | Builds fake answers + token streams for tests |
| `tests/_fake_caption_llama.py` | Stdlib fake llama-server (health, chat with logprobs, failure modes) |
| `tests/test_caption_classifier_*.py` | One test file per module |
| `.gitignore` | Ignore `data/t2i_captions/classifier/` |

---

### Task 1: Branch, rubric and act gate

**Files:**
- Create: `scripts/caption_classifier/__init__.py`
- Create: `scripts/caption_classifier/rubric.py`
- Test: `tests/test_caption_classifier_rubric.py`

**Interfaces:**
- Produces: `PARTNER: Dict[str, str]`, `KISS: Dict[str, str]`, `EMOTION: Dict[str, str]`, `Act` dataclass (`letter, name, min_males, min_females, min_total, partner_ok, look_for`), `ACTS: Tuple[Act, ...]`, `ACT_BY_LETTER: Dict[str, Act]`, `UNCLEAR = "S"`, `ISSUE_TYPES: Dict[str, str]`, `MAX_ISSUES = 4`, `MAX_QUOTE_CHARS = 120`, `allowed_acts(males: Optional[int], females: Optional[int], partner: str) -> FrozenSet[str]`.

- [ ] **Step 1: Create the branch and commit the spec**

```bash
git checkout -b feature/caption-classifier
git add docs/superpowers/specs/2026-10-01-caption-classifier-design.md docs/superpowers/plans/2026-10-01-caption-classifier.md
git commit -m "docs(caption-classifier): design spec and implementation plan"
```

- [ ] **Step 2: Write the failing tests**

`tests/test_caption_classifier_rubric.py`:

```python
"""Tests for the classifier rubric and its act participant gate."""

from __future__ import annotations

from scripts.caption_classifier.rubric import (
    ACT_BY_LETTER,
    ACTS,
    UNCLEAR,
    allowed_acts,
)


def _names(letters):
    return {ACT_BY_LETTER[letter].name for letter in letters}


def test_act_letters_run_a_to_s_in_order():
    assert "".join(a.letter for a in ACTS) == "ABCDEFGHIJKLMNOPQRS"
    assert ACT_BY_LETTER[UNCLEAR].name == "unclear"


def test_solo_woman_without_partner_gets_only_solo_acts():
    assert _names(allowed_acts(0, 1, "A")) == {
        "none-artistic",
        "breast-fondling",
        "female-masturbation",
        "female-toy-masturbation",
        "object-insertion",
        "unclear",
    }


def test_uncounted_male_partner_unlocks_pov_acts():
    got = _names(allowed_acts(0, 1, "B"))
    assert {"fellatio", "handjob", "doggy", "cowgirl", "cunnilingus"} <= got
    assert "male-masturbation" not in got  # needs a counted man
    assert "ff-tribbing" not in got


def test_unknown_partner_counts_toward_total_but_not_gender():
    got = _names(allowed_acts(0, 1, "D"))
    assert {"partner-manual-female", "cunnilingus"} <= got
    assert "fellatio" not in got
    assert "doggy" not in got


def test_tribbing_needs_two_counted_women():
    assert "ff-tribbing" not in _names(allowed_acts(0, 1, "C"))
    assert "ff-tribbing" in _names(allowed_acts(0, 2, "A"))


def test_blank_counts_disable_the_gate():
    assert allowed_acts(None, 1, "A") == frozenset(ACT_BY_LETTER)
    assert allowed_acts(1, None, "D") == frozenset(ACT_BY_LETTER)


def test_none_and_unclear_are_always_allowed():
    for males, females in [(0, 0), (1, 0), (0, 1), (2, 2)]:
        for partner in "ABCD":
            assert {"A", UNCLEAR} <= allowed_acts(males, females, partner)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_rubric.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.caption_classifier'`

- [ ] **Step 4: Write the implementation**

`scripts/caption_classifier/__init__.py`:

```python
"""One-time local classifier for the t2i caption CSV (see docs/superpowers/specs/2026-10-01-caption-classifier-design.md)."""
```

`scripts/caption_classifier/rubric.py`:

```python
"""The classification rubric: answer letters, the act table and issue types.

Everything here feeds the system prompt and the grammar, so any change also
changes ``prompt.PROMPT_VERSION`` and starts a new results file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Set, Tuple

PARTNER: Dict[str, str] = {"A": "none", "B": "male", "C": "female", "D": "unknown"}
KISS: Dict[str, str] = {"Y": "yes", "N": "no"}
EMOTION: Dict[str, str] = {"A": "none", "B": "implicit", "C": "explicit"}


@dataclass(frozen=True)
class Act:
    letter: str
    name: str
    min_males: int
    min_females: int
    min_total: int
    partner_ok: bool  # an uncounted partner can satisfy the minimums
    look_for: str


ACTS: Tuple[Act, ...] = (
    Act("A", "none-artistic", 0, 0, 0, False,
        "posing, nudity or suggestive framing with no sexual contact and no touching of genitals"),
    Act("B", "breast-fondling", 0, 1, 1, True,
        "hands (her own or a partner's) cupping, squeezing or pinching breasts or nipples in a sexual setting"),
    Act("C", "female-masturbation", 0, 1, 1, False,
        "her own hand on her vulva, crotch or between her legs"),
    Act("D", "female-toy-masturbation", 0, 1, 1, False,
        "her own hand holding a dildo, vibrator or phallic object at or between her legs"),
    Act("E", "partner-manual-female", 0, 1, 2, True,
        "another person's fingers or hand between her legs"),
    Act("F", "object-insertion", 0, 1, 1, True,
        "a toy, speculum or other object inserted or held at the genitals by someone else, "
        "including clinical settings"),
    Act("G", "male-masturbation", 1, 0, 1, False,
        "his own hand on his erect penis"),
    Act("H", "handjob", 1, 0, 2, True,
        "someone else's hand on his erect penis"),
    Act("I", "fellatio", 1, 0, 2, True,
        "an erect penis in or right next to a partner's mouth"),
    Act("J", "cunnilingus", 0, 1, 2, True,
        "her legs parted with a partner's face or mouth at her crotch"),
    Act("K", "missionary", 1, 1, 2, True,
        "she lies on her back, legs apart or raised, with him on top of or between them"),
    Act("L", "doggy", 1, 1, 2, True,
        "she is bent forward or on hands and knees with him behind her; one or both nude"),
    Act("M", "cowgirl", 1, 1, 2, True,
        "she straddles his hips or groin facing him; partly or fully nude"),
    Act("N", "reverse-cowgirl", 1, 1, 2, True,
        "she straddles him facing away from him, toward his feet or the viewer"),
    Act("O", "spooning", 1, 1, 2, True,
        "both lying on their sides, him behind her, pelvises together"),
    Act("P", "standing-sex", 1, 1, 2, True,
        "both standing with pelvises joined; she may be lifted or against a wall"),
    Act("Q", "paizuri", 1, 1, 2, True,
        "a penis between her breasts"),
    Act("R", "ff-tribbing", 0, 2, 2, False,
        "two women with crotches pressed together and legs interlocked"),
    Act("S", "unclear", 0, 0, 0, False,
        "sexual content is suggested but no single act fits, or two acts are equally likely"),
)
ACT_BY_LETTER: Dict[str, Act] = {a.letter: a for a in ACTS}
UNCLEAR = "S"

ISSUE_TYPES: Dict[str, str] = {
    "extra_limb": "more than two hands, arms or legs on one subject, "
    "or the same left/right limb placed in two different spots",
    "gaze_conflict": "looking away from the camera while also making eye contact "
    "with the viewer, or while face details only visible from the front are given",
    "facing_conflict": "facing away from the viewer while front details such as the face, "
    "chest or breasts are described as visible",
    "count_conflict": "more people described than the counts allow",
    "impossible_contact": "contact between bodies or objects that the stated positions rule out",
}
MAX_ISSUES = 4
MAX_QUOTE_CHARS = 120


def allowed_acts(
    males: Optional[int], females: Optional[int], partner: str
) -> FrozenSet[str]:
    """Act letters whose participant minimums the counts plus partner meet.

    A blank count disables the gate: every act is allowed. The uncounted
    partner (``partner`` != "A") only helps acts with ``partner_ok``; a male
    partner adds a man, a female partner a woman, an unknown one only adds to
    the total.
    """
    if males is None or females is None:
        return frozenset(ACT_BY_LETTER)
    allowed: Set[str] = set()
    for act in ACTS:
        extra = act.partner_ok and partner != "A"
        m = males + (1 if extra and partner == "B" else 0)
        f = females + (1 if extra and partner == "C" else 0)
        total = males + females + (1 if extra else 0)
        if m >= act.min_males and f >= act.min_females and total >= act.min_total:
            allowed.add(act.letter)
    return frozenset(allowed)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_rubric.py -v`
Expected: 7 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_rubric.py
git add scripts/caption_classifier tests/test_caption_classifier_rubric.py
git commit -m "feat(caption-classifier): rubric and act participant gate"
```

---

### Task 2: Grammar, prompt and PROMPT_VERSION

**Files:**
- Create: `scripts/caption_classifier/grammar.py`
- Create: `scripts/caption_classifier/prompt.py`
- Test: `tests/test_caption_classifier_prompt.py`

**Interfaces:**
- Consumes: everything in `rubric.py`; `metascan.core.t2i_captions.CaptionRow`.
- Produces: `grammar.GRAMMAR: str`; `prompt.SYSTEM_PROMPT: str`, `prompt.EXAMPLES: Tuple[Example, ...]` (`Example` fields: `males, females, nudity, erotic, porn, caption, answer: Dict[str, Any]`), `prompt.counts_line(males, females, nudity, erotic, porn) -> str`, `prompt.user_message(row: CaptionRow) -> str`, `prompt.PROMPT_VERSION: str` (12 hex chars).

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_prompt.py`:

```python
"""Tests for the classifier grammar, system prompt and prompt version."""

from __future__ import annotations

import json
import re

from metascan.core.t2i_captions import CaptionRow
from scripts.caption_classifier import prompt
from scripts.caption_classifier.grammar import GRAMMAR
from scripts.caption_classifier.rubric import (
    ACT_BY_LETTER,
    ACTS,
    EMOTION,
    ISSUE_TYPES,
    KISS,
    PARTNER,
    allowed_acts,
)

_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_CLASS = re.compile(r"\[[^\]]*\]")


def _rules():
    rules = {}
    for line in GRAMMAR.strip().splitlines():
        name, body = line.split(" ::= ", 1)
        rules[name] = body
    return rules


def test_grammar_has_no_escaped_hyphen():
    assert "\\-" not in GRAMMAR


def test_grammar_defines_every_rule_it_references():
    rules = _rules()
    assert next(iter(rules)) == "root"
    for body in rules.values():
        bare = _CLASS.sub(" ", _LITERAL.sub(" ", body))
        for ref in re.findall(r"[a-z][a-z_]*", bare):
            assert ref in rules, ref


def test_grammar_lists_every_letter_and_issue_type():
    rules = _rules()
    assert rules["act"] == "[ABCDEFGHIJKLMNOPQRS]"
    assert rules["partner"] == "[ABCD]"
    assert rules["kiss"] == "[YN]"
    assert rules["emotion"] == "[ABC]"
    for name in ISSUE_TYPES:
        assert f'"{name}"' in rules["itype"]


def test_system_prompt_names_every_act_and_issue_and_placeholders():
    for act in ACTS:
        assert f"{act.letter} {act.name}:" in prompt.SYSTEM_PROMPT
    for name in ISSUE_TYPES:
        assert name in prompt.SYSTEM_PROMPT
    assert "__ALICE__" in prompt.SYSTEM_PROMPT


def test_worked_examples_are_valid_answers():
    for ex in prompt.EXAMPLES:
        a = ex.answer
        assert a["partner"] in PARTNER and a["kiss"] in KISS
        assert a["emotion"] in EMOTION and a["act"] in ACT_BY_LETTER
        assert a["act"] in allowed_acts(ex.males, ex.females, a["partner"])
        for issue in a["issues"]:
            assert issue["type"] in ISSUE_TYPES
            assert issue["quote_a"] in ex.caption and issue["quote_b"] in ex.caption
        compact = json.dumps(a, separators=(",", ":"))
        assert compact in prompt.SYSTEM_PROMPT


def test_user_message_marks_blank_cells_with_question_marks():
    row = CaptionRow(
        id=0, caption="__ALICE__ sits.", aspect_ratio="1:1", nudity=None,
        artistic_quality=None, erotic_score=None, pornographic_score=0.25,
        males=0, females=None, clothing=(),
    )
    assert prompt.user_message(row) == (
        "Counts: M=0 F=? · Nudity: ? · Erotic ? · Porn 0.25\nCaption: __ALICE__ sits."
    )


def test_prompt_version_is_a_short_stable_hash():
    assert re.fullmatch(r"[0-9a-f]{12}", prompt.PROMPT_VERSION)
    assert prompt.PROMPT_VERSION == prompt._compute_version()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_prompt.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.grammar`

- [ ] **Step 3: Write `grammar.py`**

```python
"""GBNF grammar for the classifier's one-line JSON answer.

Fields come in a fixed order so every single-letter answer sits at a known
place in the token stream. Hyphens are never escaped (``\\-`` crashes
llama-server, see .claude/rules/vlm-llama.md); letter classes list every
letter explicitly.
"""

from __future__ import annotations

from typing import Iterable

from .rubric import (
    ACT_BY_LETTER,
    EMOTION,
    ISSUE_TYPES,
    KISS,
    MAX_ISSUES,
    MAX_QUOTE_CHARS,
    PARTNER,
)


def _lit(text: str) -> str:
    """A GBNF string literal for ``text``."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _letters(keys: Iterable[str]) -> str:
    return "[" + "".join(keys) + "]"


def build_grammar() -> str:
    root = " ".join(
        [
            _lit('{"partner":"'), "partner",
            _lit('","kiss":"'), "kiss",
            _lit('","emotion":"'), "emotion",
            _lit('","act":"'), "act",
            _lit('","issues":['), "issues",
            _lit("]}"),
        ]
    )
    issue = " ".join(
        [
            _lit('{"type":"'), "itype",
            _lit('","quote_a":"'), "quote",
            _lit('","quote_b":"'), "quote",
            _lit('"}'),
        ]
    )
    rules = [
        f"root ::= {root}",
        f"partner ::= {_letters(PARTNER)}",
        f"kiss ::= {_letters(KISS)}",
        f"emotion ::= {_letters(EMOTION)}",
        f"act ::= {_letters(ACT_BY_LETTER)}",
        f"issues ::= ( issue ( {_lit(',')} issue ){{0,{MAX_ISSUES - 1}}} )?",
        f"issue ::= {issue}",
        "itype ::= " + " | ".join(_lit(name) for name in ISSUE_TYPES),
        f"quote ::= qchar{{1,{MAX_QUOTE_CHARS}}}",
        r'qchar ::= [^"\\\r\n]',
    ]
    return "\n".join(rules) + "\n"


GRAMMAR = build_grammar()
```

- [ ] **Step 4: Write `prompt.py`**

```python
"""System prompt, per-caption user message and PROMPT_VERSION.

The system prompt is identical for every request so llama-server's prompt
cache reuses it; the caption goes last. PROMPT_VERSION hashes the prompt, the
user-message format and the grammar, so any rubric or wording change starts a
new results file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from metascan.core.t2i_captions import CaptionRow

from .grammar import GRAMMAR
from .rubric import ACTS, ISSUE_TYPES, MAX_ISSUES

USER_FORMAT = "Counts: {counts}\nCaption: {caption}"


@dataclass(frozen=True)
class Example:
    males: int
    females: int
    nudity: str
    erotic: float
    porn: float
    caption: str
    answer: Dict[str, Any]


# Hand-written; never copied from the caption CSV or the eval fixtures.
EXAMPLES: Tuple[Example, ...] = (
    Example(
        0, 1, "none", 0.10, 0.00,
        "__CLARA__ reads on a porch swing in a yellow cardigan, grinning at "
        "something on the page, one foot tucked under her.",
        {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": []},
    ),
    Example(
        0, 1, "full", 0.85, 0.95,
        "Viewed from a man's point of view, __CLARA__ lies on her back on a hotel "
        "bed with her legs wrapped around his waist; his hands hold her knees apart.",
        {"partner": "B", "kiss": "N", "emotion": "A", "act": "K", "issues": []},
    ),
    Example(
        1, 1, "partial", 0.60, 0.40,
        "__DIANNA__ sits on __ADAM__'s lap in an armchair, her blouse open, as "
        "they kiss deeply; his hand cups her bare breast.",
        {"partner": "A", "kiss": "Y", "emotion": "A", "act": "B", "issues": []},
    ),
    Example(
        0, 1, "none", 0.20, 0.00,
        "__CLARA__ sits on a stool facing away from the viewer, her bare back to "
        "the camera, and smiles at the viewer. Her right hand rests on her knee, "
        "her left hand holds a mug, and her right hand brushes her hair aside.",
        {
            "partner": "A", "kiss": "N", "emotion": "C", "act": "A",
            "issues": [
                {"type": "facing_conflict", "quote_a": "facing away from the viewer",
                 "quote_b": "smiles at the viewer"},
                {"type": "extra_limb", "quote_a": "Her right hand rests on her knee",
                 "quote_b": "her right hand brushes her hair aside"},
            ],
        },
    ),
)

_TEMPLATE = """\
You classify captions written for an image generator. Read one caption and \
answer five questions about it, using only the required one-line JSON format.

Placeholders: words in double underscores such as __ALICE__, __BELLA__, \
__CLARA__, __DIANNA__, __ADAM__, __HAIR__, __BREASTS__, __VAGINA__ and \
__PENIS__ stand for a named person or a body feature. Read them as ordinary \
words and never judge them.

Each caption comes with its counts: M is the number of men and F the number \
of women in the image. The counts only include people who are in frame.

1. partner: is there a participant the counts do not include, such as a \
point-of-view partner or someone only partly visible?
A none
B male: a penis or male anatomy belongs to someone the counts leave out
C female: female anatomy or a named woman the counts leave out
D unknown: only hands or body parts of an uncounted person, gender not stated

2. kiss: are any two subjects kissing, or are their faces very close while \
they look at each other?
Y yes
N no

3. emotion: does the caption say how anyone feels?
A none: no expression, mood or attitude is described
B implicit: only gaze, posture or vague words such as "poised" or "intimate" \
hint at a mood
C explicit: a facial expression or emotional state is named, for example \
smiling, grinning, laughing, pouting, frowning, lips parted, eyes closed in \
pleasure, aloof, playful, shy or bored
If any subject has an explicit expression, answer C.

4. act: which sexual act does the caption show or most strongly imply? Pick \
exactly one letter. Acts described as "her own hand" or "his own hand" mean \
the subject touches themself; another person's hand is a partner act.
<<ACTS>>

5. issues: list anatomically impossible or self-contradicting details, at \
most <<MAX_ISSUES>>. Types:
<<ISSUES>>
Each issue gives two short quotes, copied word for word from the caption, \
that conflict with each other. If you are unsure whether something is a \
problem, report it: a person reviews every issue. Use an empty list when \
there are none.

Answer on one line with no spaces between fields:
{"partner":"<letter>","kiss":"<Y or N>","emotion":"<letter>","act":"<letter>",\
"issues":[{"type":"<type>","quote_a":"<quote>","quote_b":"<quote>"}]}

Examples:

<<EXAMPLES>>
"""


def _fmt_count(value: Optional[int]) -> str:
    return "?" if value is None else str(value)


def _fmt_score(value: Optional[float]) -> str:
    return "?" if value is None else f"{value:.2f}"


def counts_line(
    males: Optional[int],
    females: Optional[int],
    nudity: Optional[str],
    erotic: Optional[float],
    porn: Optional[float],
) -> str:
    return (
        f"M={_fmt_count(males)} F={_fmt_count(females)} · Nudity: {nudity or '?'}"
        f" · Erotic {_fmt_score(erotic)} · Porn {_fmt_score(porn)}"
    )


def user_message(row: CaptionRow) -> str:
    counts = counts_line(
        row.males, row.females, row.nudity, row.erotic_score, row.pornographic_score
    )
    return USER_FORMAT.format(counts=counts, caption=row.caption)


def _build_system_prompt() -> str:
    acts = "\n".join(f"{a.letter} {a.name}: {a.look_for}" for a in ACTS)
    issues = "\n".join(f"{name}: {meaning}" for name, meaning in ISSUE_TYPES.items())
    examples = "\n\n".join(
        USER_FORMAT.format(
            counts=counts_line(ex.males, ex.females, ex.nudity, ex.erotic, ex.porn),
            caption=ex.caption,
        )
        + "\nAnswer: "
        + json.dumps(ex.answer, separators=(",", ":"))
        for ex in EXAMPLES
    )
    return (
        _TEMPLATE.replace("<<ACTS>>", acts)
        .replace("<<ISSUES>>", issues)
        .replace("<<MAX_ISSUES>>", str(MAX_ISSUES))
        .replace("<<EXAMPLES>>", examples)
    )


SYSTEM_PROMPT = _build_system_prompt()


def _compute_version() -> str:
    blob = "\n\x00\n".join([SYSTEM_PROMPT, USER_FORMAT, GRAMMAR])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


PROMPT_VERSION = _compute_version()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_prompt.py -v`
Expected: 7 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_prompt.py
git add scripts/caption_classifier tests/test_caption_classifier_prompt.py
git commit -m "feat(caption-classifier): GBNF grammar, system prompt and prompt version"
```

---

### Task 3: Letter distributions from logprobs

**Files:**
- Create: `scripts/caption_classifier/parse.py`
- Create: `tests/_caption_classifier_helpers.py`
- Test: `tests/test_caption_classifier_parse.py`

**Interfaces:**
- Consumes: `PARTNER, KISS, EMOTION, ACT_BY_LETTER` from `rubric.py`.
- Produces: `parse.ParseError(ValueError)`, `parse.LETTER_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...]`, `parse.letter_distribution(token: Mapping[str, Any], prefix: str, letters: Sequence[str]) -> Dict[str, float]`, `parse.field_distributions(content: str, tokens: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, float]]`. Test helper `answer(partner="A", kiss="N", emotion="A", act="A", issues=None, alts=None) -> Tuple[str, List[Dict]]`.

Token objects follow llama-server's OpenAI-compatible shape: `{"token": str, "logprob": float, "top_logprobs": [{"token": str, "logprob": float}, ...]}`, found at `choices[0].logprobs.content`.

- [ ] **Step 1: Write the test helper**

`tests/_caption_classifier_helpers.py`:

```python
"""Builds classifier answers and matching llama-server token streams for tests."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _token(text: str, dist: Dict[str, float]) -> Dict[str, Any]:
    chosen = dist.get(text, 1e-6)
    return {
        "token": text,
        "logprob": math.log(chosen),
        "top_logprobs": [
            {"token": t, "logprob": math.log(p)} for t, p in dist.items()
        ],
    }


def answer(
    partner: str = "A",
    kiss: str = "N",
    emotion: str = "A",
    act: str = "A",
    issues: Optional[Sequence[Dict[str, str]]] = None,
    alts: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """The answer text plus one token per skeleton piece and per letter.

    ``alts`` maps a field name to its candidate letters and probabilities;
    a field without alts is certain.
    """
    alts = alts or {}
    tail = json.dumps(list(issues or []), separators=(",", ":"), ensure_ascii=False)
    pieces = [
        ('{"partner":"', None), (partner, "partner"),
        ('","kiss":"', None), (kiss, "kiss"),
        ('","emotion":"', None), (emotion, "emotion"),
        ('","act":"', None), (act, "act"),
        ('","issues":' + tail + "}", None),
    ]
    tokens = [
        _token(text, alts.get(field, {text: 1.0}) if field else {text: 1.0})
        for text, field in pieces
    ]
    return "".join(text for text, _ in pieces), tokens
```

- [ ] **Step 2: Write the failing tests**

`tests/test_caption_classifier_parse.py`:

```python
"""Tests for reading answer-letter distributions out of llama-server logprobs."""

from __future__ import annotations

import math

import pytest

from scripts.caption_classifier.parse import ParseError, field_distributions
from tests._caption_classifier_helpers import answer


def test_each_field_gets_a_renormalised_distribution():
    content, tokens = answer(
        alts={
            "partner": {"A": 0.6, "B": 0.3, "{": 0.1},
            "emotion": {"A": 0.5, "B": 0.25, "C": 0.25},
        }
    )
    d = field_distributions(content, tokens)
    assert d["partner"] == {"A": 0.6667, "B": 0.3333, "C": 0.0, "D": 0.0}
    assert d["kiss"] == {"Y": 0.0, "N": 1.0}
    assert d["emotion"] == {"A": 0.5, "B": 0.25, "C": 0.25}
    assert d["act"]["A"] == 1.0 and sum(d["act"].values()) == 1.0
    assert len(d["act"]) == 19


def test_letter_merged_with_the_preceding_quote():
    content, tokens = answer()
    # Re-split '{"partner":"' + 'A' into '{"partner":' + '"A'.
    tokens[0] = dict(tokens[0], token='{"partner":')
    tokens[1] = {
        "token": '"A',
        "logprob": math.log(0.7),
        "top_logprobs": [
            {"token": '"A', "logprob": math.log(0.7)},
            {"token": '"D', "logprob": math.log(0.2)},
            {"token": '"', "logprob": math.log(0.1)},
        ],
    }
    d = field_distributions(content, tokens)
    assert d["partner"] == {"A": 0.7778, "B": 0.0, "C": 0.0, "D": 0.2222}


def test_chosen_token_missing_from_top_list_still_counts():
    content, tokens = answer()
    tokens[7] = {
        "token": "A",
        "logprob": math.log(0.25),
        "top_logprobs": [{"token": "B", "logprob": math.log(0.5)}],
    }
    assert field_distributions(content, tokens)["act"]["A"] == 0.3333


def test_token_stream_that_disagrees_with_the_text_is_rejected():
    content, tokens = answer()
    tokens[1] = dict(tokens[1], token="B")
    with pytest.raises(ParseError, match="does not match"):
        field_distributions(content, tokens)


def test_missing_field_is_rejected():
    with pytest.raises(ParseError, match="kiss"):
        field_distributions('{"partner":"A"}', [{"token": '{"partner":"A"}', "logprob": 0.0}])


def test_broken_utf8_pieces_in_the_issue_quotes_do_not_matter():
    issue = {"type": "extra_limb", "quote_a": "café table", "quote_b": "“right hand”"}
    content, tokens = answer(issues=[issue])
    tokens[-1] = dict(tokens[-1], token=tokens[-1]["token"].replace("é", "�"))
    assert field_distributions(content, tokens)["partner"]["A"] == 1.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_parse.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.parse`

- [ ] **Step 4: Write the implementation**

`scripts/caption_classifier/parse.py`:

```python
"""Turn a llama-server chat completion into one classification record.

Each single-letter answer's probabilities are read from ``top_logprobs`` at
the token that carries the letter. The tokenizer may merge the letter with
the characters before it (``"B`` instead of ``B``), so the letter is found by
character offset in the answer text, and a candidate token counts toward a
letter when it starts with the same characters as the chosen token up to the
letter. The letter fields all come before the free-text ``issues`` part, so
only that ASCII prefix has to line up with the token stream.
"""

from __future__ import annotations

import bisect
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .rubric import ACT_BY_LETTER, EMOTION, KISS, PARTNER


class ParseError(ValueError):
    """The model's answer cannot be turned into a record."""


LETTER_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("partner", tuple(PARTNER)),
    ("kiss", tuple(KISS)),
    ("emotion", tuple(EMOTION)),
    ("act", tuple(ACT_BY_LETTER)),
)


def _value_offset(content: str, field: str) -> int:
    marker = f'"{field}":"'
    index = content.find(marker)
    if index < 0:
        raise ParseError(f"field {field!r} missing from the answer")
    return index + len(marker)


def letter_distribution(
    token: Mapping[str, Any], prefix: str, letters: Sequence[str]
) -> Dict[str, float]:
    """Probabilities over ``letters`` at ``token``, renormalised to sum to 1."""
    logprobs: Dict[str, float] = {}
    candidates = list(token.get("top_logprobs") or [])
    candidates.append({"token": token["token"], "logprob": token["logprob"]})
    for cand in candidates:
        logprobs.setdefault(str(cand["token"]), float(cand["logprob"]))
    mass = {letter: 0.0 for letter in letters}
    for text, logprob in logprobs.items():
        if len(text) > len(prefix) and text.startswith(prefix):
            letter = text[len(prefix)]
            if letter in mass:
                mass[letter] += math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        raise ParseError("no candidate token carries an allowed letter")
    return {letter: round(p / total, 4) for letter, p in mass.items()}


def field_distributions(
    content: str, tokens: Sequence[Mapping[str, Any]]
) -> Dict[str, Dict[str, float]]:
    """One renormalised letter distribution per field in ``LETTER_FIELDS``."""
    offsets = {field: _value_offset(content, field) for field, _ in LETTER_FIELDS}
    usable: List[Mapping[str, Any]] = [t for t in tokens if t.get("token")]
    starts: List[int] = []
    pos = 0
    for tok in usable:
        starts.append(pos)
        pos += len(tok["token"])
    text = "".join(str(t["token"]) for t in usable)
    end = offsets[LETTER_FIELDS[-1][0]] + 1
    if text[:end] != content[:end]:
        raise ParseError("token stream does not match the answer text")
    out: Dict[str, Dict[str, float]] = {}
    for field, letters in LETTER_FIELDS:
        offset = offsets[field]
        index = bisect.bisect_right(starts, offset) - 1
        prefix = content[starts[index] : offset]
        out[field] = letter_distribution(usable[index], prefix, letters)
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_parse.py -v`
Expected: 6 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/_caption_classifier_helpers.py tests/test_caption_classifier_parse.py
git add scripts/caption_classifier tests/_caption_classifier_helpers.py tests/test_caption_classifier_parse.py
git commit -m "feat(caption-classifier): read answer-letter distributions from logprobs"
```

---

### Task 4: Act gate, quote check and the classification record

**Files:**
- Modify: `scripts/caption_classifier/parse.py` (append)
- Test: `tests/test_caption_classifier_record.py`

**Interfaces:**
- Consumes: `field_distributions`, `ParseError` (Task 3); `allowed_acts`, `ISSUE_TYPES`, `UNCLEAR` (Task 1).
- Produces: `parse.verify_quote(quote: str, caption: str) -> bool`; `parse.build_record(*, caption: str, males: Optional[int], females: Optional[int], content: str, tokens: Sequence[Mapping[str, Any]]) -> Dict[str, Any]` returning keys `partner, kiss, emotion, act_raw, act_gated, act_gate_conflict, issues, raw_output` (no row id, hash, status, model or version — the runner adds those).

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_record.py`:

```python
"""Tests for the act gate, quote verification and record assembly."""

from __future__ import annotations

import pytest

from scripts.caption_classifier.parse import ParseError, build_record, verify_quote
from tests._caption_classifier_helpers import answer

CAPTION = (
    "__ALICE__ sits at the café table. Her right hand holds a cup; "
    "her right   hand also strokes her __HAIR__."
)


def _record(males, females, **kw):
    content, tokens = answer(**kw)
    return build_record(
        caption=CAPTION, males=males, females=females, content=content, tokens=tokens
    )


def test_gate_removes_acts_the_counts_rule_out_and_flags_the_conflict():
    rec = _record(0, 1, act="I", alts={"act": {"I": 0.7, "A": 0.3}})
    assert rec["act_raw"]["I"] == 0.7
    assert rec["act_gated"]["I"] == 0.0 and rec["act_gated"]["A"] == 1.0
    assert rec["act_gate_conflict"] is True


def test_uncounted_male_partner_keeps_pov_act():
    rec = _record(0, 1, partner="B", act="I", alts={"act": {"I": 0.7, "A": 0.3}})
    assert rec["act_gated"]["I"] == 0.7
    assert rec["act_gate_conflict"] is False


def test_all_mass_on_forbidden_acts_falls_back_to_unclear():
    rec = _record(0, 1, act="L", alts={"act": {"L": 0.8, "M": 0.2}})
    assert rec["act_gated"]["S"] == 1.0
    assert sum(rec["act_gated"].values()) == 1.0
    assert rec["act_gate_conflict"] is True


def test_blank_counts_leave_the_act_ungated():
    rec = _record(None, 1, act="L", alts={"act": {"L": 0.8, "M": 0.2}})
    assert rec["act_gated"] == rec["act_raw"]
    assert rec["act_gate_conflict"] is False


def test_issues_carry_a_quote_check():
    issues = [
        {"type": "extra_limb", "quote_a": "Her right hand holds a cup",
         "quote_b": "her right hand also strokes"},
        {"type": "gaze_conflict", "quote_a": "looks away",
         "quote_b": "Her right hand holds a cup"},
    ]
    rec = _record(0, 1, issues=issues)
    assert [i["quote_verified"] for i in rec["issues"]] == [True, False]
    assert rec["issues"][0]["type"] == "extra_limb"


def test_quote_check_ignores_case_spacing_and_edge_punctuation():
    assert verify_quote("AT THE CAFÉ TABLE.", CAPTION)
    assert verify_quote("“at the café table”", "she sat “at the café table” alone")
    assert not verify_quote("at the kitchen table", CAPTION)
    assert not verify_quote("  ", CAPTION)


def test_unknown_issue_type_is_rejected():
    issues = [{"type": "bad_type", "quote_a": "a", "quote_b": "b"}]
    with pytest.raises(ParseError, match="bad_type"):
        _record(0, 1, issues=issues)


def test_truncated_answer_is_rejected():
    content, tokens = answer()
    with pytest.raises(ParseError, match="not JSON"):
        build_record(caption=CAPTION, males=0, females=1,
                     content=content[:-3], tokens=tokens)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_record.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_record'`

- [ ] **Step 3: Append the implementation to `parse.py`**

Add `import json` and `Optional` to the imports and `ISSUE_TYPES, UNCLEAR, allowed_acts` to the rubric import, then append:

```python
_QUOTE_EDGES = " .,;:!?\"'“”‘’"


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def verify_quote(quote: str, caption: str) -> bool:
    """True when ``quote`` appears in ``caption``, ignoring case, spacing and edge punctuation."""
    needle = _norm(quote).strip(_QUOTE_EDGES)
    return bool(needle) and needle in _norm(caption)


def _argmax(dist: Mapping[str, float]) -> str:
    return max(dist, key=lambda k: dist[k])


def build_record(
    *,
    caption: str,
    males: Optional[int],
    females: Optional[int],
    content: str,
    tokens: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Classification fields for one answer: distributions, gated act, issues."""
    dists = field_distributions(content, tokens)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError(f"answer is not JSON: {exc}") from exc

    partner = _argmax(dists["partner"])
    allowed = allowed_acts(males, females, partner)
    act_raw = dists["act"]
    kept = {k: (p if k in allowed else 0.0) for k, p in act_raw.items()}
    total = sum(kept.values())
    raw_top = _argmax(act_raw)
    if total > 0:
        act_gated = {k: round(p / total, 4) for k, p in kept.items()}
        conflict = raw_top not in allowed and act_raw[raw_top] >= 0.5
    else:
        act_gated = {k: (1.0 if k == UNCLEAR else 0.0) for k in act_raw}
        conflict = True

    issues: List[Dict[str, Any]] = []
    for item in parsed.get("issues") or []:
        kind = str(item.get("type", ""))
        if kind not in ISSUE_TYPES:
            raise ParseError(f"unknown issue type {kind!r}")
        quote_a = str(item.get("quote_a", ""))
        quote_b = str(item.get("quote_b", ""))
        issues.append(
            {
                "type": kind,
                "quote_a": quote_a,
                "quote_b": quote_b,
                "quote_verified": verify_quote(quote_a, caption)
                and verify_quote(quote_b, caption),
            }
        )

    return {
        "partner": dists["partner"],
        "kiss": dists["kiss"],
        "emotion": dists["emotion"],
        "act_raw": act_raw,
        "act_gated": act_gated,
        "act_gate_conflict": conflict,
        "issues": issues,
        "raw_output": content,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_record.py tests/test_caption_classifier_parse.py -v`
Expected: 14 passed

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_record.py
git add scripts/caption_classifier tests/test_caption_classifier_record.py
git commit -m "feat(caption-classifier): act gate, quote check and record assembly"
```

---

### Task 5: Results file — selection, resume, versioning

**Files:**
- Create: `scripts/caption_classifier/results.py`
- Test: `tests/test_caption_classifier_results.py`

**Interfaces:**
- Produces: `caption_sha1(text: str) -> str`; `select_rows(total: int, count: Optional[int], sample: Optional[int], seed: int) -> List[int]`; `class VersionConflict(RuntimeError)`; `results_path(out_dir: Path, version: str) -> Path`; `open_run(out_dir: Path, version: str, new_run: bool) -> Path`; `load_done(path: Path) -> Set[Tuple[int, str]]`; `load_records(path: Path) -> Dict[int, Dict[str, Any]]`; `newest_results(out_dir: Path) -> Path`; `class ResultsWriter` with `__init__(path: Path)`, `write(record: Dict[str, Any]) -> None`, `close() -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_results.py`:

```python
"""Tests for row selection and the resumable, versioned results file."""

from __future__ import annotations

import json
import os
import time

import pytest

from scripts.caption_classifier.results import (
    ResultsWriter,
    VersionConflict,
    caption_sha1,
    load_done,
    load_records,
    newest_results,
    open_run,
    results_path,
    select_rows,
)


def test_count_takes_the_first_rows():
    assert select_rows(10, 3, None, 0) == [0, 1, 2]
    assert select_rows(2, 5, None, 0) == [0, 1]
    assert select_rows(4, None, None, 0) == [0, 1, 2, 3]


def test_sample_is_sorted_unique_and_seeded():
    a = select_rows(1000, None, 50, 7)
    assert a == sorted(set(a)) and len(a) == 50
    assert a == select_rows(1000, None, 50, 7)
    assert a != select_rows(1000, None, 50, 8)
    assert select_rows(3, None, 10, 0) == [0, 1, 2]


def test_open_run_refuses_when_only_other_versions_exist(tmp_path):
    results_path(tmp_path, "aaaaaaaaaaaa").write_text("")
    with pytest.raises(VersionConflict, match="aaaaaaaaaaaa"):
        open_run(tmp_path, "bbbbbbbbbbbb", new_run=False)
    assert open_run(tmp_path, "bbbbbbbbbbbb", new_run=True) == results_path(
        tmp_path, "bbbbbbbbbbbb"
    )


def test_open_run_resumes_its_own_version(tmp_path):
    results_path(tmp_path, "aaaaaaaaaaaa").write_text("")
    results_path(tmp_path, "bbbbbbbbbbbb").write_text("")
    assert open_run(tmp_path, "bbbbbbbbbbbb", new_run=False).exists()


def test_load_done_skips_errors_and_a_half_written_last_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        json.dumps({"row_id": 0, "caption_sha1": "x", "status": "ok"}) + "\n"
        + json.dumps({"row_id": 1, "caption_sha1": "y", "status": "error"}) + "\n"
        + '{"row_id": 2, "caption_sha1": "z", "sta'
    )
    assert load_done(path) == {(0, "x")}
    assert load_done(tmp_path / "missing.jsonl") == set()


def test_writer_repairs_a_missing_final_newline(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"row_id": 0, "caption_sha1": "x", "status": "o')
    writer = ResultsWriter(path)
    writer.write({"row_id": 1, "caption_sha1": "y", "status": "ok"})
    writer.close()
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["row_id"] == 1
    assert load_done(path) == {(1, "y")}


def test_load_records_prefers_the_last_ok_record(tmp_path):
    path = tmp_path / "r.jsonl"
    rows = [
        {"row_id": 0, "caption_sha1": "x", "status": "error", "error": "boom"},
        {"row_id": 0, "caption_sha1": "x", "status": "ok", "n": 1},
        {"row_id": 0, "caption_sha1": "x", "status": "error", "error": "later"},
        {"row_id": 1, "caption_sha1": "y", "status": "error", "error": "e"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    recs = load_records(path)
    assert recs[0]["n"] == 1
    assert recs[1]["status"] == "error"


def test_newest_results_picks_the_latest_file(tmp_path):
    old = results_path(tmp_path, "aaaaaaaaaaaa")
    new = results_path(tmp_path, "bbbbbbbbbbbb")
    old.write_text("")
    new.write_text("")
    past = time.time() - 100
    os.utime(old, (past, past))
    assert newest_results(tmp_path) == new


def test_caption_sha1_is_hex_sha1():
    assert caption_sha1("abc") == "a9993e364706816aba3e25717850c26c9cd0d89d"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_results.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.results`

- [ ] **Step 3: Write the implementation**

`scripts/caption_classifier/results.py`:

```python
"""The append-only results file: which rows to run, which are done, writing.

One file per prompt version, ``results-<version>.jsonl``, one JSON object per
line. A run killed mid-write can leave a half line at the end; readers skip
it and the writer starts the next record on a fresh line.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, TextIO, Tuple


class VersionConflict(RuntimeError):
    """Results from another prompt version exist and --new-run was not given."""


def caption_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def select_rows(
    total: int, count: Optional[int], sample: Optional[int], seed: int
) -> List[int]:
    if count is not None:
        return list(range(min(count, total)))
    if sample is not None:
        picked = random.Random(seed).sample(range(total), min(sample, total))
        return sorted(picked)
    return list(range(total))


def results_path(out_dir: Path, version: str) -> Path:
    return out_dir / f"results-{version}.jsonl"


def open_run(out_dir: Path, version: str, new_run: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    mine = results_path(out_dir, version)
    others = sorted(p.name for p in out_dir.glob("results-*.jsonl") if p != mine)
    if others and not mine.exists() and not new_run:
        raise VersionConflict(
            f"{out_dir} holds results for another prompt version ({', '.join(others)}); "
            f"the current version is {version}. Pass --new-run to start "
            f"{mine.name} alongside them."
        )
    return mine


def _records(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written line from a killed run


def load_done(path: Path) -> Set[Tuple[int, str]]:
    return {
        (int(r["row_id"]), str(r["caption_sha1"]))
        for r in _records(path)
        if r.get("status") == "ok"
    }


def load_records(path: Path) -> Dict[int, Dict[str, Any]]:
    """The record to report per row: the last ``ok`` one, else the last one."""
    best: Dict[int, Dict[str, Any]] = {}
    for rec in _records(path):
        row_id = int(rec["row_id"])
        current = best.get(row_id)
        if rec.get("status") == "ok" or current is None or current.get("status") != "ok":
            best[row_id] = rec
    return best


def newest_results(out_dir: Path) -> Path:
    files = sorted(out_dir.glob("results-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no results-*.jsonl in {out_dir}")
    return files[-1]


class ResultsWriter:
    def __init__(self, path: Path) -> None:
        needs_newline = False
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                fh.seek(-1, 2)
                needs_newline = fh.read(1) != b"\n"
        self._fh: TextIO = path.open("a", encoding="utf-8")
        if needs_newline:
            self._fh.write("\n")

    def write(self, record: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_results.py -v`
Expected: 9 passed

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_results.py
git add scripts/caption_classifier tests/test_caption_classifier_results.py
git commit -m "feat(caption-classifier): resumable versioned results file"
```

---

### Task 6: llama-server supervisor and the fake server

**Files:**
- Create: `scripts/caption_classifier/server.py`
- Create: `tests/_fake_caption_llama.py`
- Test: `tests/test_caption_classifier_server.py`

**Interfaces:**
- Consumes: `metascan.core.vlm_models.REGISTRY`, `metascan.utils.app_paths.get_data_dir`, `metascan.utils.llama_server.binary_path`.
- Produces: `server.ServerError(RuntimeError)`; `server.free_port() -> int`; `server.build_command(model_id: str, port: int, parallel: int, ctx_per_slot: int) -> List[str]`; `server.LlamaServer(command: Callable[[int], List[str]], *, health_timeout: float = 600.0)` with `base_url: str` property, `alive() -> bool`, `async start()`, `async stop()`, `async restart()`. Fake server script `tests/_fake_caption_llama.py` with flags `--port`, `--response FILE` (JSON `{"content": str, "tokens": [...]}`), `--load-ms`, `--fail-first N`, `--fail-status CODE`, `--exit-after N`, `--requests-log FILE`; test helper `FAKE_SCRIPT: Path` and `fake_command(**flags) -> Callable[[int], List[str]]`.

- [ ] **Step 1: Write the fake server**

`tests/_fake_caption_llama.py`:

```python
"""A stand-in for llama-server for the caption-classifier tests (stdlib only).

Run as a script. Answers GET /health (503 until --load-ms has passed) and
POST /v1/chat/completions with the canned answer from --response, including
its logprobs. Failure modes: --fail-first N answers the first N requests with
--fail-status; --exit-after N kills the process on request N+1 without
answering. --requests-log appends every request body as a JSON line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List

FAKE_SCRIPT = Path(__file__).resolve()


def fake_command(**flags: Any) -> Callable[[int], List[str]]:
    """A LlamaServer command factory that launches this fake."""

    def command(port: int) -> List[str]:
        cmd = [sys.executable, str(FAKE_SCRIPT), "--port", str(port)]
        for key, value in flags.items():
            cmd += [f"--{key.replace('_', '-')}", str(value)]
        return cmd

    return command


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--response", default="")
    ap.add_argument("--load-ms", type=int, default=0)
    ap.add_argument("--fail-first", type=int, default=0)
    ap.add_argument("--fail-status", type=int, default=500)
    ap.add_argument("--exit-after", type=int, default=0)
    ap.add_argument("--requests-log", default="")
    args = ap.parse_args()

    payload: Dict[str, Any] = {"content": "", "tokens": []}
    if args.response:
        payload = json.loads(Path(args.response).read_text(encoding="utf-8"))
    started = time.monotonic()
    lock = threading.Lock()
    state = {"served": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._send(404, {})
                return
            ready = (time.monotonic() - started) * 1000 >= args.load_ms
            self._send(200 if ready else 503, {"status": "ok" if ready else "loading"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            with lock:
                state["served"] += 1
                served = state["served"]
                if args.requests_log:
                    with open(args.requests_log, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(body) + "\n")
            if args.exit_after and served > args.exit_after:
                os._exit(1)
            if served <= args.fail_first:
                self._send(
                    args.fail_status,
                    {"error": {"message": "the request exceeds the available context size"}},
                )
                return
            self._send(
                200,
                {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": payload["content"]},
                            "logprobs": {"content": payload["tokens"]},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

    sys.stderr.write("fake llama-server listening\n")
    sys.stderr.flush()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the failing tests**

`tests/test_caption_classifier_server.py`:

```python
"""Tests for the classifier's llama-server supervisor, against a fake server."""

from __future__ import annotations

import sys

import pytest

from scripts.caption_classifier import server as server_mod
from scripts.caption_classifier.server import LlamaServer, ServerError, build_command
from tests._fake_caption_llama import fake_command


async def test_start_waits_for_health_then_stop_kills_it():
    srv = LlamaServer(fake_command(load_ms=300), health_timeout=20)
    await srv.start()
    try:
        assert srv.alive()
        assert srv.base_url.startswith("http://127.0.0.1:")
    finally:
        await srv.stop()
    assert not srv.alive()


async def test_restart_comes_back_on_a_new_process():
    srv = LlamaServer(fake_command(), health_timeout=20)
    await srv.start()
    try:
        await srv.restart()
        assert srv.alive()
    finally:
        await srv.stop()


async def test_process_that_exits_while_loading_raises():
    srv = LlamaServer(
        lambda port: [sys.executable, "-c", "import sys; sys.exit(3)"],
        health_timeout=20,
    )
    with pytest.raises(ServerError, match="code 3"):
        await srv.start()
    assert not srv.alive()


async def test_health_timeout_raises_and_stops_the_process():
    srv = LlamaServer(fake_command(load_ms=100000), health_timeout=1.0)
    with pytest.raises(ServerError, match="not healthy"):
        await srv.start()
    assert not srv.alive()


def test_build_command_is_text_only_and_sizes_context(tmp_path, monkeypatch):
    binary = tmp_path / "llama-server"
    binary.write_text("")
    spec = server_mod.REGISTRY["qwen3vl-30b-a3b"]
    gguf = tmp_path / "models" / "vlm" / spec.gguf_filename
    gguf.parent.mkdir(parents=True)
    gguf.write_text("")
    monkeypatch.setattr(server_mod, "binary_path", lambda: binary)
    monkeypatch.setattr(server_mod, "get_data_dir", lambda: tmp_path)

    cmd = build_command("qwen3vl-30b-a3b", 9999, parallel=16, ctx_per_slot=6144)

    assert cmd[0] == str(binary)
    assert "--mmproj" not in cmd
    assert cmd[cmd.index("--model") + 1] == str(gguf)
    assert cmd[cmd.index("--parallel") + 1] == "16"
    assert cmd[cmd.index("--ctx-size") + 1] == str(16 * 6144)
    assert cmd[cmd.index("--port") + 1] == "9999"
    assert cmd[-len(spec.extra_args):] == list(spec.extra_args)


def test_build_command_rejects_unknown_model_and_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "binary_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(server_mod, "get_data_dir", lambda: tmp_path)
    with pytest.raises(ServerError, match="unknown model"):
        build_command("no-such-model", 1, 1, 1)
    with pytest.raises(ServerError, match="missing"):
        build_command("qwen3vl-30b-a3b", 1, 1, 1)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_server.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.server`

- [ ] **Step 4: Write the implementation**

`scripts/caption_classifier/server.py`:

```python
"""Start, watch and stop the llama-server the classifier talks to.

The server is the classifier's own: a free port on 127.0.0.1, the model
GGUF without ``--mmproj`` (captions are text), and its stderr drained line by
line into the classifier log so the pipe never fills during model load.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Callable, List, Optional

import httpx

from metascan.core.vlm_models import REGISTRY
from metascan.utils.app_paths import get_data_dir
from metascan.utils.llama_server import binary_path

logger = logging.getLogger("caption_classifier.server")


class ServerError(RuntimeError):
    """llama-server could not be started or kept healthy."""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_command(model_id: str, port: int, parallel: int, ctx_per_slot: int) -> List[str]:
    if model_id not in REGISTRY:
        raise ServerError(
            f"unknown model {model_id!r}; choose from {', '.join(sorted(REGISTRY))}"
        )
    spec = REGISTRY[model_id]
    binary = binary_path()
    gguf = get_data_dir() / "models" / "vlm" / spec.gguf_filename
    for path in (binary, gguf):
        if not path.exists():
            raise ServerError(f"missing {path}")
    return [
        str(binary),
        "--model", str(gguf),
        "--host", "127.0.0.1",
        "--port", str(port),
        "--parallel", str(parallel),
        "--ctx-size", str(parallel * ctx_per_slot),
        "--n-gpu-layers", "99",
        *spec.extra_args,
    ]


class LlamaServer:
    def __init__(
        self, command: Callable[[int], List[str]], *, health_timeout: float = 600.0
    ) -> None:
        self._command = command
        self._health_timeout = health_timeout
        self._port: Optional[int] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._drain: Optional["asyncio.Task[None]"] = None

    @property
    def base_url(self) -> str:
        if self._port is None:
            raise ServerError("llama-server has not been started")
        return f"http://127.0.0.1:{self._port}"

    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._port = free_port()
        cmd = self._command(self._port)
        logger.info("starting llama-server: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._drain = asyncio.create_task(self._drain_stderr(self._proc))
        try:
            await self._wait_healthy()
        except BaseException:
            await self.stop()
            raise
        logger.info("llama-server ready at %s", self.base_url)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            logger.debug("llama-server: %s", line.decode("utf-8", "replace").rstrip())

    async def _wait_healthy(self) -> None:
        assert self._proc is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._health_timeout
        async with httpx.AsyncClient(timeout=5.0) as http:
            while True:
                if self._proc.returncode is not None:
                    raise ServerError(
                        f"llama-server exited with code {self._proc.returncode} "
                        "while loading; see the classifier log"
                    )
                try:
                    resp = await http.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                if loop.time() > deadline:
                    raise ServerError(
                        f"llama-server not healthy after {self._health_timeout:.0f}s"
                    )
                await asyncio.sleep(0.5)

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        drain, self._drain = self._drain, None
        if drain is not None:
            try:
                await asyncio.wait_for(drain, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def restart(self) -> None:
        logger.warning("restarting llama-server")
        await self.stop()
        await self.start()
```

Note `alive()` reads `self._proc`, which `stop()` sets to `None`, so a stopped server is never alive. The early-exit test relies on `start()` calling `stop()` on failure, which clears `_proc`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_server.py -v`
Expected: 6 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/_fake_caption_llama.py tests/test_caption_classifier_server.py
git add scripts/caption_classifier tests/_fake_caption_llama.py tests/test_caption_classifier_server.py
git commit -m "feat(caption-classifier): llama-server supervisor with stderr drain"
```

---

### Task 7: Runner — requests, retries, crash recovery, progress

**Files:**
- Create: `scripts/caption_classifier/runner.py`
- Test: `tests/test_caption_classifier_runner.py`

**Interfaces:**
- Consumes: `prompt.SYSTEM_PROMPT`, `prompt.user_message`, `grammar.GRAMMAR`, `parse.build_record`, `parse.ParseError`, `results.caption_sha1`, `server.LlamaServer` (via a protocol), `CaptionRow`.
- Produces: `runner.REQUEST_MAX_TOKENS = 700`; `runner.FatalServerError(RuntimeError)`; `runner.request_body(row: CaptionRow) -> Dict[str, Any]`; `runner.RunStats` dataclass (`total, done, ok, errors, restarts: int`, `stopped: bool`); `runner.Runner(*, rows: RowSource, server: ServerLike, writer: RecordSink, model_id: str, prompt_version: str, workers: int, timeout: float = 60.0, attempts: int = 3, max_restarts: int = 1, progress_every: float = 30.0)` with `async run(row_ids: Sequence[int]) -> RunStats`, `request_stop() -> None`, `stats: RunStats`. Protocols: `RowSource.get(row_id: int) -> CaptionRow`; `ServerLike.base_url`, `alive() -> bool`, `async restart() -> None`; `RecordSink.write(record: Dict[str, Any]) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_runner.py`:

```python
"""Tests for the classifier runner against the fake llama-server."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from metascan.core.t2i_captions import CaptionRow
from scripts.caption_classifier.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from scripts.caption_classifier.runner import FatalServerError, Runner
from scripts.caption_classifier.server import LlamaServer
from tests._caption_classifier_helpers import answer
from tests._fake_caption_llama import fake_command


class Rows:
    def __init__(self, n: int) -> None:
        self._rows = [
            CaptionRow(
                id=i, caption=f"__ALICE__ stands in room {i}.", aspect_ratio="1:1",
                nudity="none", artistic_quality=0.5, erotic_score=0.1,
                pornographic_score=0.0, males=0, females=1, clothing=(),
            )
            for i in range(n)
        ]

    def get(self, row_id: int) -> CaptionRow:
        return self._rows[row_id]


class Sink:
    def __init__(self, on_write=None) -> None:
        self.records: List[Dict[str, Any]] = []
        self._on_write = on_write

    def write(self, record: Dict[str, Any]) -> None:
        self.records.append(record)
        if self._on_write:
            self._on_write()


@pytest.fixture
def response_file(tmp_path):
    content, tokens = answer(emotion="B", alts={"emotion": {"B": 0.8, "A": 0.2}})
    path = tmp_path / "response.json"
    path.write_text(json.dumps({"content": content, "tokens": tokens}))
    return path


async def _run(server_flags, n, response_file, tmp_path, workers=1, sink=None, **kw):
    log = tmp_path / "requests.jsonl"
    srv = LlamaServer(
        fake_command(response=response_file, requests_log=log, **server_flags),
        health_timeout=20,
    )
    sink = sink or Sink()
    runner = Runner(
        rows=Rows(n), server=srv, writer=sink, model_id="fake",
        prompt_version=PROMPT_VERSION, workers=workers, timeout=10,
        progress_every=0.1, **kw,
    )
    await srv.start()
    try:
        stats = await runner.run(list(range(n)))
    finally:
        await srv.stop()
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    return runner, stats, sink, requests


async def test_every_row_gets_an_ok_record(response_file, tmp_path):
    _, stats, sink, requests = await _run({}, 3, response_file, tmp_path, workers=2)
    assert stats.ok == 3 and stats.errors == 0
    assert sorted(r["row_id"] for r in sink.records) == [0, 1, 2]
    rec = sink.records[0]
    assert rec["status"] == "ok" and rec["model"] == "fake"
    assert rec["prompt_version"] == PROMPT_VERSION
    assert len(rec["caption_sha1"]) == 40
    assert rec["emotion"] == {"A": 0.2, "B": 0.8, "C": 0.0}
    body = requests[0]
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0 and "grammar" in body


async def test_one_failed_request_is_retried(response_file, tmp_path):
    _, stats, sink, requests = await _run({"fail_first": 1}, 1, response_file, tmp_path)
    assert stats.ok == 1 and len(requests) == 2


async def test_context_overflow_becomes_an_error_row_and_the_run_continues(
    response_file, tmp_path
):
    _, stats, sink, requests = await _run(
        {"fail_first": 3, "fail_status": 400}, 2, response_file, tmp_path
    )
    assert [r["status"] for r in sink.records] == ["error", "ok"]
    assert "400" in sink.records[0]["error"]
    assert "context size" in sink.records[0]["error"]
    assert stats.errors == 1 and len(requests) == 4


async def test_one_crash_restarts_the_server_and_finishes(response_file, tmp_path):
    _, stats, sink, _ = await _run({"exit_after": 2}, 3, response_file, tmp_path)
    assert stats.restarts == 1
    assert [r["status"] for r in sink.records] == ["ok", "ok", "ok"]


async def test_second_crash_stops_the_run_and_keeps_written_rows(response_file, tmp_path):
    sink = Sink()
    with pytest.raises(FatalServerError):
        await _run({"exit_after": 1}, 4, response_file, tmp_path, sink=sink)
    assert [r["row_id"] for r in sink.records] == [0, 1]


async def test_stop_request_finishes_the_current_row_then_stops(response_file, tmp_path):
    holder: Dict[str, Runner] = {}
    sink = Sink(on_write=lambda: holder["runner"].request_stop())
    log = tmp_path / "requests.jsonl"
    srv = LlamaServer(fake_command(response=response_file, requests_log=log), health_timeout=20)
    runner = Runner(
        rows=Rows(5), server=srv, writer=sink, model_id="fake",
        prompt_version=PROMPT_VERSION, workers=1, timeout=10,
    )
    holder["runner"] = runner
    await srv.start()
    try:
        stats = await runner.run(list(range(5)))
    finally:
        await srv.stop()
    assert len(sink.records) == 1 and stats.stopped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_runner.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.runner`

- [ ] **Step 3: Write the implementation**

`scripts/caption_classifier/runner.py`:

```python
"""Send captions to llama-server and write one record per caption.

A pool of workers takes row ids off a shared queue. Each caption gets up to
``attempts`` tries; a parse failure, an HTTP error status or a timeout is
retried, and after the last try the row is written with ``status: "error"``
so the run moves on. A dropped connection with a dead server restarts the
server once; a second death raises ``FatalServerError``. ``request_stop``
lets in-flight captions finish and then ends the run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Protocol, Sequence

import httpx

from metascan.core.t2i_captions import CaptionRow

from .grammar import GRAMMAR
from .parse import ParseError, build_record
from .prompt import SYSTEM_PROMPT, user_message
from .results import caption_sha1

logger = logging.getLogger("caption_classifier.runner")

REQUEST_MAX_TOKENS = 700


class FatalServerError(RuntimeError):
    """llama-server died again after its one restart."""


class RowSource(Protocol):
    def get(self, row_id: int) -> CaptionRow: ...


class ServerLike(Protocol):
    @property
    def base_url(self) -> str: ...

    def alive(self) -> bool: ...

    async def restart(self) -> None: ...


class RecordSink(Protocol):
    def write(self, record: Dict[str, Any]) -> None: ...


def request_body(row: CaptionRow) -> Dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message(row)},
        ],
        "temperature": 0.0,
        "max_tokens": REQUEST_MAX_TOKENS,
        "grammar": GRAMMAR,
        "logprobs": True,
        "top_logprobs": 20,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }


@dataclass
class RunStats:
    total: int = 0
    done: int = 0
    ok: int = 0
    errors: int = 0
    restarts: int = 0
    stopped: bool = False


class Runner:
    def __init__(
        self,
        *,
        rows: RowSource,
        server: ServerLike,
        writer: RecordSink,
        model_id: str,
        prompt_version: str,
        workers: int,
        timeout: float = 60.0,
        attempts: int = 3,
        max_restarts: int = 1,
        progress_every: float = 30.0,
    ) -> None:
        self._rows = rows
        self._server = server
        self._writer = writer
        self._model_id = model_id
        self._prompt_version = prompt_version
        self._workers = workers
        self._timeout = timeout
        self._attempts = attempts
        self._max_restarts = max_restarts
        self._progress_every = progress_every
        self._stop = False
        self._restart_lock: Optional[asyncio.Lock] = None
        self._http: Optional[httpx.AsyncClient] = None
        self.stats = RunStats()

    def request_stop(self) -> None:
        self._stop = True

    async def run(self, row_ids: Sequence[int]) -> RunStats:
        self.stats = RunStats(total=len(row_ids))
        self._restart_lock = asyncio.Lock()
        queue: Deque[int] = deque(row_ids)
        started = time.monotonic()
        progress = asyncio.create_task(self._progress(started))
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                self._http = http
                try:
                    async with asyncio.TaskGroup() as group:
                        for _ in range(self._workers):
                            group.create_task(self._worker(queue))
                except BaseExceptionGroup as eg:
                    raise eg.exceptions[0]
        finally:
            progress.cancel()
            self._http = None
        self.stats.stopped = self._stop and self.stats.done < self.stats.total
        self._log_progress(started)
        return self.stats

    async def _worker(self, queue: Deque[int]) -> None:
        while queue and not self._stop:
            row = self._rows.get(queue.popleft())
            record = await self._classify(row)
            self._writer.write(record)
            self.stats.done += 1
            if record["status"] == "ok":
                self.stats.ok += 1
            else:
                self.stats.errors += 1
                logger.warning("row %d failed: %s", row.id, record["error"])

    async def _classify(self, row: CaptionRow) -> Dict[str, Any]:
        reason = ""
        for _ in range(self._attempts):
            try:
                return self._record(row, "ok", await self._classify_once(row))
            except ParseError as exc:
                reason = f"unparseable answer: {exc}"
            except httpx.HTTPStatusError as exc:
                reason = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            except httpx.TimeoutException:
                reason = f"timed out after {self._timeout:.0f}s"
            except httpx.TransportError as exc:
                reason = f"connection failed: {exc!r}"
                await self._recover()
        return self._record(row, "error", {"error": reason})

    async def _classify_once(self, row: CaptionRow) -> Dict[str, Any]:
        assert self._http is not None
        resp = await self._http.post(
            f"{self._server.base_url}/v1/chat/completions", json=request_body(row)
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        tokens = (choice.get("logprobs") or {}).get("content") or []
        if not tokens:
            raise ParseError("response carries no logprobs")
        return build_record(
            caption=row.caption,
            males=row.males,
            females=row.females,
            content=choice["message"]["content"],
            tokens=tokens,
        )

    async def _recover(self) -> None:
        assert self._restart_lock is not None
        async with self._restart_lock:
            for _ in range(10):  # the exit can lag the dropped connection
                if not self._server.alive():
                    break
                await asyncio.sleep(0.1)
            else:
                return  # still running: a transient drop, just retry
            if self.stats.restarts >= self._max_restarts:
                raise FatalServerError("llama-server crashed again after a restart")
            self.stats.restarts += 1
            await self._server.restart()

    def _record(self, row: CaptionRow, status: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "row_id": row.id,
            "caption_sha1": caption_sha1(row.caption),
            "prompt_version": self._prompt_version,
            "model": self._model_id,
            "status": status,
            **fields,
        }

    async def _progress(self, started: float) -> None:
        while True:
            await asyncio.sleep(self._progress_every)
            self._log_progress(started)

    def _log_progress(self, started: float) -> None:
        s = self.stats
        elapsed = max(time.monotonic() - started, 1e-6)
        rate = s.done / elapsed
        left = (s.total - s.done) / rate if rate > 0 else float("inf")
        eta = "?" if left == float("inf") else time.strftime("%H:%M:%S", time.gmtime(left))
        logger.info(
            "%d/%d rows · %.2f rows/s · ETA %s · %d errors · %d restarts",
            s.done, s.total, rate, eta, s.errors, s.restarts,
        )
```

Note on the second-crash test: with one worker and `exit_after=1`, row 0 succeeds, row 1 crashes the server → restart (count 1) → row 1 succeeds on the fresh process (its first request), row 2 crashes it again → `FatalServerError`. Rows 0 and 1 are written; nothing else.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_runner.py -v`
Expected: 6 passed

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_runner.py
git add scripts/caption_classifier tests/test_caption_classifier_runner.py
git commit -m "feat(caption-classifier): worker pool with retries and crash recovery"
```

---

### Task 8: `classify.py` CLI

**Files:**
- Create: `scripts/caption_classifier/classify.py`
- Modify: `.gitignore` (add one line after `data/t2i_captions/*.csv`)
- Test: `tests/test_caption_classifier_cli.py`

**Interfaces:**
- Consumes: `CaptionStore` (`available()`, `error()`, `total()`, `get(row_id)`), `results.*`, `server.LlamaServer`, `server.build_command`, `server.ServerError`, `runner.Runner`, `runner.FatalServerError`, `prompt.PROMPT_VERSION`, `metascan.utils.log_files.rotating_file_handler`.
- Produces: `classify.main(argv: Optional[Sequence[str]] = None) -> int`; `classify.ExternalServer(base_url: str)`; `classify.setup_logging(log_file: Path) -> None`; `classify.DEFAULT_CSV`, `DEFAULT_OUT`, `DEFAULT_LOG`. Exit codes: 0 all ok, 1 some error rows, 2 bad arguments/input, 3 server failure, 130 interrupted.

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_cli.py`:

```python
"""End-to-end tests for the classify CLI against a fake llama-server."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time

import httpx
import pytest

from scripts.caption_classifier import classify
from scripts.caption_classifier.prompt import PROMPT_VERSION
from scripts.caption_classifier.results import results_path
from scripts.caption_classifier.server import free_port
from tests._caption_classifier_helpers import answer
from tests._fake_caption_llama import FAKE_SCRIPT

HEADER = ["Caption", "Aspect Ratio", "Nudity", "Artistic Quality", "Erotic Score",
          "Pornographic Score", "Males", "Females", "Clothing"]


def _write_csv(path, captions):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for text in captions:
            w.writerow([text, "1:1", "none", "0.5", "0.1", "0.0", "0", "1", "[]"])


@pytest.fixture
def fake(tmp_path):
    content, tokens = answer()
    response = tmp_path / "response.json"
    response.write_text(json.dumps({"content": content, "tokens": tokens}))
    log = tmp_path / "requests.jsonl"
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_SCRIPT), "--port", str(port),
         "--response", str(response), "--requests-log", str(log)],
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.05)
    yield url, log
    proc.terminate()
    proc.wait(timeout=10)


def _args(tmp_path, url, *extra):
    return ["--csv", str(tmp_path / "c.csv"), "--out", str(tmp_path / "out"),
            "--server-url", url, "--log-file", str(tmp_path / "cls.log"), *extra]


def _requested(log):
    if not log.exists():
        return 0
    return len(log.read_text().splitlines())


def _ok_rows(tmp_path):
    path = results_path(tmp_path / "out", PROMPT_VERSION)
    return sorted(json.loads(l)["row_id"] for l in path.read_text().splitlines())


def test_count_limits_to_the_first_rows_and_rerun_skips_them(tmp_path, fake):
    url, log = fake
    _write_csv(tmp_path / "c.csv", [f"__ALICE__ pose {i}." for i in range(5)])
    assert classify.main(_args(tmp_path, url, "--count", "2")) == 0
    assert _ok_rows(tmp_path) == [0, 1]
    assert classify.main(_args(tmp_path, url, "--count", "3")) == 0
    assert _ok_rows(tmp_path) == [0, 1, 2]
    assert _requested(log) == 3


def test_edited_caption_is_classified_again(tmp_path, fake):
    url, log = fake
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one.", "__ALICE__ two."])
    assert classify.main(_args(tmp_path, url)) == 0
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one, edited.", "__ALICE__ two."])
    assert classify.main(_args(tmp_path, url)) == 0
    assert _requested(log) == 3


def test_results_from_another_version_need_new_run(tmp_path, fake):
    url, _ = fake
    _write_csv(tmp_path / "c.csv", ["__ALICE__ one."])
    (tmp_path / "out").mkdir()
    results_path(tmp_path / "out", "000000000000").write_text("")
    assert classify.main(_args(tmp_path, url)) == 2
    assert classify.main(_args(tmp_path, url, "--new-run")) == 0


def test_missing_csv_exits_2(tmp_path, fake):
    url, _ = fake
    assert classify.main(_args(tmp_path, url)) == 2


def test_count_and_sample_are_mutually_exclusive(tmp_path, fake):
    url, _ = fake
    with pytest.raises(SystemExit) as exc:
        classify.main(_args(tmp_path, url, "--count", "1", "--sample", "1"))
    assert exc.value.code == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_cli.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.classify`

- [ ] **Step 3: Write the implementation**

`scripts/caption_classifier/classify.py`:

```python
"""Classify the t2i caption CSV with a local llama-server.

Usage:
    python -m scripts.caption_classifier.classify [--csv PATH] [--out DIR]
        [--model qwen3vl-30b-a3b] [--parallel 16] [--ctx-per-slot 6144]
        [--count N | --sample N [--seed S]] [--new-run]
        [--server-url URL] [--log-file PATH]

--count N classifies only the first N rows (for trying the classifier out);
rows already classified with the current prompt version are skipped, so a
later full run does not redo them. --server-url uses an already running
llama-server instead of starting one.

The captions CSV is only read. Results go to <out>/results-<version>.jsonl;
run summarize.py to produce classifications.csv.

Exit codes: 0 every selected row ok; 1 finished with error rows; 2 unusable
arguments or input; 3 llama-server failed; 130 interrupted.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Union

from metascan.core.t2i_captions import CaptionStore
from metascan.utils.log_files import rotating_file_handler

from .prompt import PROMPT_VERSION
from .results import (
    ResultsWriter,
    VersionConflict,
    caption_sha1,
    load_done,
    open_run,
    select_rows,
)
from .runner import FatalServerError, Runner
from .server import LlamaServer, ServerError, build_command

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "data" / "t2i_captions" / "t2i_captions.csv"
DEFAULT_OUT = REPO_ROOT / "data" / "t2i_captions" / "classifier"
DEFAULT_LOG = REPO_ROOT / "logs" / "caption_classifier.log"

logger = logging.getLogger("caption_classifier")


class ExternalServer:
    """A llama-server someone else started; it is never restarted or stopped."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def alive(self) -> bool:
        return True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def restart(self) -> None:
        raise FatalServerError(f"external server {self.base_url} cannot be restarted")


def setup_logging(log_file: Path) -> None:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logger.addHandler(console)
    file_handler = rotating_file_handler(log_file)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default="qwen3vl-30b-a3b")
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--ctx-per-slot", type=int, default=6144)
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--count", type=int, help="classify only the first N rows")
    pick.add_argument("--sample", type=int, help="classify N random rows")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--new-run", action="store_true")
    ap.add_argument("--server-url", default="")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    return ap.parse_args(argv)


async def _run(
    args: argparse.Namespace, store: CaptionStore, row_ids: List[int], path: Path
) -> int:
    server: Union[ExternalServer, LlamaServer]
    if args.server_url:
        server = ExternalServer(args.server_url)
    else:
        server = LlamaServer(
            lambda port: build_command(args.model, port, args.parallel, args.ctx_per_slot)
        )
    writer = ResultsWriter(path)
    runner = Runner(
        rows=store, server=server, writer=writer, model_id=args.model,
        prompt_version=PROMPT_VERSION, workers=args.parallel,
    )
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    presses = 0

    def on_sigint() -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            logger.warning("stopping after the captions in flight; Ctrl-C again to abort")
            runner.request_stop()
        elif main_task is not None:
            main_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGINT, on_sigint)
    except (NotImplementedError, RuntimeError):
        pass
    try:
        await server.start()
        stats = await runner.run(row_ids)
    except (ServerError, FatalServerError) as exc:
        logger.error("%s", exc)
        return 3
    except asyncio.CancelledError:
        logger.warning("aborted; rows written so far are kept")
        return 130
    finally:
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, RuntimeError):
            pass
        writer.close()
        await server.stop()
    logger.info("finished: %d ok, %d errors → %s", stats.ok, stats.errors, path)
    if stats.stopped:
        return 130
    return 0 if stats.errors == 0 else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_file)
    store = CaptionStore(args.csv)
    if not store.available():
        logger.error("cannot use %s: %s", args.csv, store.error())
        return 2
    try:
        path = open_run(args.out, PROMPT_VERSION, args.new_run)
    except VersionConflict as exc:
        logger.error("%s", exc)
        return 2
    row_ids = select_rows(store.total(), args.count, args.sample, args.seed)
    done = load_done(path)
    pending = [r for r in row_ids if (r, caption_sha1(store.get(r).caption)) not in done]
    logger.info(
        "prompt version %s · %d selected · %d already done · %d to classify → %s",
        PROMPT_VERSION, len(row_ids), len(row_ids) - len(pending), len(pending), path,
    )
    if not pending:
        return 0
    return asyncio.run(_run(args, store, pending, path))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Ignore the output directory**

In `.gitignore`, directly after the line `data/t2i_captions/*.csv`, add:

```
data/t2i_captions/classifier/
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_cli.py -v`
Expected: 5 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_cli.py
git add .gitignore scripts/caption_classifier tests/test_caption_classifier_cli.py
git commit -m "feat(caption-classifier): classify CLI with --count, resume and versioning"
```

---

### Task 9: `summarize.py` → `classifications.csv`

**Files:**
- Create: `scripts/caption_classifier/summarize.py`
- Test: `tests/test_caption_classifier_summarize.py`

**Interfaces:**
- Consumes: `results.load_records`, `results.newest_results`, `rubric.PARTNER`, `rubric.EMOTION`, `rubric.ACT_BY_LETTER`, `classify.DEFAULT_OUT`.
- Produces: `summarize.COLUMNS: List[str]`; `summarize.summarize_record(rec: Dict[str, Any]) -> Dict[str, str]`; `summarize.write_csv(records: Dict[int, Dict[str, Any]], output: Path) -> int`; `summarize.main(argv: Optional[Sequence[str]] = None) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_caption_classifier_summarize.py`:

```python
"""Tests for flattening results into classifications.csv."""

from __future__ import annotations

import csv
import json

from scripts.caption_classifier import summarize


def _ok(row_id):
    act = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    act.update({"L": 0.6, "M": 0.3, "A": 0.1})
    return {
        "row_id": row_id, "caption_sha1": "h%d" % row_id, "status": "ok",
        "partner": {"A": 0.1, "B": 0.9, "C": 0.0, "D": 0.0},
        "kiss": {"Y": 0.25, "N": 0.75},
        "emotion": {"A": 0.7, "B": 0.2, "C": 0.1},
        "act_raw": dict(act, I=0.0), "act_gated": act, "act_gate_conflict": False,
        "issues": [{"type": "extra_limb", "quote_a": "a", "quote_b": "b",
                    "quote_verified": True}],
    }


def test_columns_are_in_the_documented_order():
    assert summarize.COLUMNS == [
        "row_id", "caption_sha1", "status", "partner", "partner_p", "kiss_p",
        "emotion", "p_emotion_none", "p_emotion_implicit", "p_emotion_explicit",
        "act_1", "act_1_p", "act_2", "act_2_p", "act_3", "act_3_p",
        "act_raw_top", "act_raw_top_p", "act_gate_conflict",
        "issue_types", "issues", "error",
    ]


def test_ok_record_is_flattened_with_names():
    row = summarize.summarize_record(_ok(4))
    assert row["partner"] == "male" and row["partner_p"] == "0.9"
    assert row["kiss_p"] == "0.25"
    assert row["emotion"] == "none" and row["p_emotion_none"] == "0.7"
    assert (row["act_1"], row["act_1_p"]) == ("doggy", "0.6")
    assert (row["act_2"], row["act_3"]) == ("cowgirl", "none-artistic")
    assert row["act_raw_top"] == "doggy" and row["act_gate_conflict"] == "false"
    assert row["issue_types"] == "extra_limb"
    assert json.loads(row["issues"])[0]["quote_verified"] is True


def test_zero_probability_acts_leave_the_slot_blank():
    rec = _ok(0)
    rec["act_gated"] = {letter: 0.0 for letter in "ABCDEFGHIJKLMNOPQRS"}
    rec["act_gated"]["A"] = 1.0
    row = summarize.summarize_record(rec)
    assert row["act_1"] == "none-artistic"
    assert row["act_2"] == "" and row["act_2_p"] == ""


def test_error_record_is_kept_and_marked(tmp_path):
    records = {
        1: {"row_id": 1, "caption_sha1": "x", "status": "error", "error": "HTTP 400"},
        0: _ok(0),
    }
    out = tmp_path / "classifications.csv"
    assert summarize.write_csv(records, out) == 2
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["row_id"] for r in rows] == ["0", "1"]
    assert rows[1]["status"] == "error" and rows[1]["error"] == "HTTP 400"
    assert rows[1]["act_1"] == ""


def test_main_reads_the_newest_results_file(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "results-aaaaaaaaaaaa.jsonl").write_text(json.dumps(_ok(0)) + "\n")
    assert summarize.main(["--out", str(out_dir)]) == 0
    assert (out_dir / "classifications.csv").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_summarize.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.summarize`

- [ ] **Step 3: Write the implementation**

`scripts/caption_classifier/summarize.py`:

```python
"""Flatten a results file into classifications.csv, one row per caption.

Usage:
    python -m scripts.caption_classifier.summarize [--out DIR] [--results PATH]
        [--output PATH]

Without --results the newest results-*.jsonl in --out is used; the CSV is
written next to it unless --output says otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .classify import DEFAULT_OUT
from .results import load_records, newest_results
from .rubric import ACT_BY_LETTER, EMOTION, PARTNER

COLUMNS: List[str] = [
    "row_id", "caption_sha1", "status", "partner", "partner_p", "kiss_p",
    "emotion", "p_emotion_none", "p_emotion_implicit", "p_emotion_explicit",
    "act_1", "act_1_p", "act_2", "act_2_p", "act_3", "act_3_p",
    "act_raw_top", "act_raw_top_p", "act_gate_conflict",
    "issue_types", "issues", "error",
]


def _ranked(dist: Dict[str, float]) -> List[Tuple[str, float]]:
    return sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))


def _p(value: float) -> str:
    return f"{value:.4g}"


def summarize_record(rec: Dict[str, Any]) -> Dict[str, str]:
    row = {col: "" for col in COLUMNS}
    row.update(
        row_id=str(rec["row_id"]),
        caption_sha1=str(rec["caption_sha1"]),
        status=str(rec["status"]),
    )
    if rec["status"] != "ok":
        row["error"] = str(rec.get("error", ""))
        return row
    partner, partner_p = _ranked(rec["partner"])[0]
    emotion, _ = _ranked(rec["emotion"])[0]
    row.update(
        partner=PARTNER[partner],
        partner_p=_p(partner_p),
        kiss_p=_p(rec["kiss"]["Y"]),
        emotion=EMOTION[emotion],
        p_emotion_none=_p(rec["emotion"]["A"]),
        p_emotion_implicit=_p(rec["emotion"]["B"]),
        p_emotion_explicit=_p(rec["emotion"]["C"]),
    )
    for i, (letter, p) in enumerate(_ranked(rec["act_gated"])[:3], start=1):
        if p > 0:
            row[f"act_{i}"] = ACT_BY_LETTER[letter].name
            row[f"act_{i}_p"] = _p(p)
    raw_letter, raw_p = _ranked(rec["act_raw"])[0]
    row.update(
        act_raw_top=ACT_BY_LETTER[raw_letter].name,
        act_raw_top_p=_p(raw_p),
        act_gate_conflict="true" if rec["act_gate_conflict"] else "false",
        issue_types=";".join(i["type"] for i in rec["issues"]),
        issues=json.dumps(rec["issues"], ensure_ascii=False) if rec["issues"] else "",
    )
    return row


def write_csv(records: Dict[int, Dict[str, Any]], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row_id in sorted(records):
            writer.writerow(summarize_record(records[row_id]))
    return len(records)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Write classifications.csv from a results file.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    try:
        results = args.results or newest_results(args.out)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    output = args.output or results.parent / "classifications.csv"
    count = write_csv(load_records(results), output)
    print(f"{count} rows from {results.name} → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_summarize.py -v`
Expected: 5 passed

- [ ] **Step 5: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_summarize.py
git add scripts/caption_classifier tests/test_caption_classifier_summarize.py
git commit -m "feat(caption-classifier): summarize results into classifications.csv"
```

---

### Task 10: Evaluation fixtures and `eval.py`

**Files:**
- Create: `scripts/caption_classifier/fixtures/fixtures.json`
- Create: `scripts/caption_classifier/eval.py`
- Test: `tests/test_caption_classifier_eval.py`

**Interfaces:**
- Consumes: `Runner`, `LlamaServer`, `build_command`, `ExternalServer`, `setup_logging`, `DEFAULT_LOG` (classify), rubric tables and `allowed_acts`, `CaptionRow`.
- Produces: `eval.FIXTURES: Path`; `eval.load_fixtures(path: Path) -> List[Dict[str, Any]]` (raises `ValueError` on an invalid fixture); `eval.fixture_row(index: int, fx: Dict[str, Any]) -> CaptionRow`; `eval.score(fixtures: List[Dict[str, Any]], records: Dict[int, Dict[str, Any]]) -> Dict[str, Any]`; `eval.format_report(report: Dict[str, Any]) -> str`; `eval.main(argv) -> int`.

Fixture schema: `{"id", "males", "females", "nudity", "erotic", "porn", "caption", "expect": {"partner", "kiss", "emotion", "act", "issues": [type, ...]}, "derived_from"?}`. Expected letters use the rubric letters.

- [ ] **Step 1: Write the fixtures**

`scripts/caption_classifier/fixtures/fixtures.json` — hand-written, none from the captions CSV, none matching the prompt's worked examples:

```json
[
{"id": "emo-none", "males": 0, "females": 1, "nudity": "none", "erotic": 0.1, "porn": 0.0, "caption": "__ALICE__ stands beside a tall window in a pale blue linen dress. Her __HAIR__ falls over one shoulder and her hands rest at her sides. Soft daylight from the left lights a white wall with a single framed print.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "emo-implicit-gaze", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ sits on a wooden park bench in a cream sweater and jeans, her gaze directed toward the viewer. Her hands rest folded in her lap and autumn leaves cover the path behind her.", "expect": {"partner": "A", "kiss": "N", "emotion": "B", "act": "A", "issues": []}},
{"id": "emo-explicit-smile", "males": 0, "females": 1, "nudity": "none", "erotic": 0.1, "porn": 0.0, "caption": "__ALICE__ leans against a brick wall in a denim jacket, smiling broadly at the viewer with her head tilted slightly to one side. Her right hand holds a takeaway coffee cup at chest height.", "expect": {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": []}},
{"id": "emo-two-one-explicit", "males": 0, "females": 2, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ and __BELLA__ sit side by side on a green sofa. __ALICE__ looks straight ahead with her hands in her lap, while __BELLA__ laughs with her eyes crinkled, one hand covering her mouth.", "expect": {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": []}},
{"id": "emo-implicit-poised", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.5, "porn": 0.1, "caption": "__ALICE__ poses on a velvet chaise in a black lace bodysuit, her posture poised and her chin raised slightly. Her left arm rests along the back of the chaise and warm lamplight falls across her shoulders.", "expect": {"partner": "A", "kiss": "N", "emotion": "B", "act": "A", "issues": []}},
{"id": "emo-explicit-pleasure", "males": 0, "females": 1, "nudity": "full", "erotic": 0.8, "porn": 0.5, "caption": "__ALICE__ lies nude on white sheets with her back arched, her eyes closed in pleasure and her lips parted. Both arms are stretched above her head, her wrists crossed on the pillow.", "expect": {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": []}},
{"id": "act-A-nude-pose", "males": 0, "females": 1, "nudity": "full", "erotic": 0.6, "porn": 0.2, "caption": "__ALICE__ stands nude in a sunlit studio, one hand resting on her hip and the other hanging at her side. A grey backdrop fills the frame and the light comes from a large window on the right.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "act-B-breast-self", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.8, "porn": 0.5, "caption": "__ALICE__ kneels on a bed in an unbuttoned white shirt, both hands cupping her bare __BREASTS__ and squeezing gently. Her knees are apart on the rumpled duvet and a lamp glows on the nightstand.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "B", "issues": []}},
{"id": "act-C-masturbation", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 0.9, "caption": "__ALICE__ reclines nude against a pile of pillows with her legs spread. Her right hand is between her legs, her fingers pressed against her __VAGINA__, while her left hand grips the sheet beside her.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "C", "issues": []}},
{"id": "act-D-toy", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 0.9, "caption": "__ALICE__ lies on her back on a sofa, nude, holding a pink dildo in her right hand and pressing it between her parted legs. Her left knee is raised against the backrest.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "D", "issues": []}},
{"id": "act-E-partner-hand", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 0.9, "caption": "__ALICE__ lies on her back on a bed with her legs apart. A second person's hand reaches in from the right edge of the frame, two fingers slipping into her __VAGINA__. Her own hands rest on her stomach.", "expect": {"partner": "D", "kiss": "N", "emotion": "A", "act": "E", "issues": []}},
{"id": "act-F-clinical", "males": 0, "females": 1, "nudity": "full", "erotic": 0.7, "porn": 0.9, "caption": "In a clinical room, __ALICE__ lies on an examination table with her feet in stirrups. A gloved hand inserts a metal speculum into her __VAGINA__ while another hand holds a small light.", "expect": {"partner": "D", "kiss": "N", "emotion": "A", "act": "F", "issues": []}},
{"id": "act-G-male-solo", "males": 1, "females": 0, "nudity": "full", "erotic": 0.8, "porn": 0.9, "caption": "__ADAM__ sits nude on the edge of a bed, his right hand wrapped around his erect __PENIS__. His left hand braces against the mattress behind him and the curtains are drawn.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "G", "issues": []}},
{"id": "act-H-handjob", "males": 1, "females": 1, "nudity": "full", "erotic": 0.8, "porn": 0.9, "caption": "__ADAM__ lies on his back on a couch while __BELLA__ kneels beside him, her hand wrapped around his erect __PENIS__. Both are nude and a television glows in the background.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "H", "issues": []}},
{"id": "act-I-fellatio", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__BELLA__ kneels on the floor in front of __ADAM__, who stands nude; his erect __PENIS__ is in her mouth and her hands rest on his thighs.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "I", "issues": []}},
{"id": "act-J-cunnilingus", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 0.9, "caption": "__BELLA__ lies on her back on the bed with her legs parted while __ADAM__ lies between them, his face pressed to her crotch and his hands holding her thighs.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "J", "issues": []}},
{"id": "act-K-missionary", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__BELLA__ lies on her back on a bed with her legs raised and apart, and __ADAM__ is on top of her between her thighs, his hips pressed against hers. Both are nude.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "K", "issues": []}},
{"id": "act-L-doggy", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__BELLA__ is on her hands and knees on the bed, nude, and __ADAM__ kneels behind her with his hands on her hips, his pelvis pressed against her buttocks.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "L", "issues": []}},
{"id": "act-M-cowgirl", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__ADAM__ lies on his back on the bed while __BELLA__ straddles his hips facing him, nude, her hands resting on his chest.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "M", "issues": []}},
{"id": "act-N-reverse-cowgirl", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__ADAM__ lies on his back while __BELLA__ sits astride his hips facing away from him, toward his feet and the viewer, nude, leaning forward with her hands on his knees.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "N", "issues": []}},
{"id": "act-O-spooning", "males": 1, "females": 1, "nudity": "full", "erotic": 0.8, "porn": 0.9, "caption": "__BELLA__ and __ADAM__ lie on their sides on a bed, nude, both facing the same way; he is pressed behind her, his pelvis against her buttocks and his arm around her waist.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "O", "issues": []}},
{"id": "act-P-standing", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__BELLA__ is lifted against a bathroom wall with her legs wrapped around __ADAM__'s waist; he stands nude, his hips pressed between her thighs.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "P", "issues": []}},
{"id": "act-Q-paizuri", "males": 1, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "__BELLA__ kneels in front of __ADAM__ and presses her bare __BREASTS__ together around his erect __PENIS__ with both hands.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "Q", "issues": []}},
{"id": "act-R-tribbing", "males": 0, "females": 2, "nudity": "full", "erotic": 0.9, "porn": 0.9, "caption": "__ALICE__ and __BELLA__ lie nude on a bed with their legs scissored together, their crotches pressed against each other, each holding the other's ankle.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "R", "issues": []}},
{"id": "act-S-unclear", "males": 1, "females": 2, "nudity": "full", "erotic": 0.8, "porn": 0.9, "caption": "__ADAM__, __ALICE__ and __BELLA__ lie tangled together on a large bed, nude, limbs overlapping beneath a thin sheet, their bodies pressed close.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "S", "issues": []}},
{"id": "kiss-clothed", "males": 1, "females": 1, "nudity": "none", "erotic": 0.3, "porn": 0.0, "caption": "__ADAM__ and __BELLA__ stand on a rainy street under one umbrella, both in long coats, their lips pressed together in a kiss.", "expect": {"partner": "A", "kiss": "Y", "emotion": "A", "act": "A", "issues": []}},
{"id": "pov-I", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "Shot from the viewer's point of view, __ALICE__ kneels on a tiled bathroom floor, her lips around an erect __PENIS__ that enters the frame from the bottom edge.", "expect": {"partner": "B", "kiss": "N", "emotion": "A", "act": "I", "issues": []}},
{"id": "pov-L", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "From a first-person view, __ALICE__ is on her hands and knees on a bed, nude, looking back over her shoulder; a man's hands grip her hips from behind and his groin is pressed against her buttocks.", "expect": {"partner": "B", "kiss": "N", "emotion": "B", "act": "L", "issues": []}},
{"id": "pov-M", "males": 0, "females": 1, "nudity": "full", "erotic": 0.9, "porn": 1.0, "caption": "Shot from below in first person, __ALICE__ straddles the viewer's hips, nude, her hands pressed on a man's bare chest at the bottom of the frame.", "expect": {"partner": "B", "kiss": "N", "emotion": "A", "act": "M", "issues": []}},
{"id": "pov-H", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.8, "porn": 0.9, "caption": "In a point-of-view shot, __ALICE__ sits on a couch in her underwear, her hand wrapped around a man's erect __PENIS__ that reaches into the frame from below.", "expect": {"partner": "B", "kiss": "N", "emotion": "A", "act": "H", "issues": []}},
{"id": "near-thigh", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.6, "porn": 0.2, "caption": "__ALICE__ sits on the edge of a bed in a silk slip, her right hand resting on her upper thigh just above the knee. Her left hand holds the strap of the slip at her shoulder.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "near-toy-unused", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.6, "porn": 0.3, "caption": "__ALICE__ lies on her stomach on a bed in lingerie, propped on her elbows and reading a magazine; a pink vibrator lies on the duvet beside her pillow.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "near-embrace", "males": 1, "females": 1, "nudity": "none", "erotic": 0.3, "porn": 0.0, "caption": "__ADAM__ stands behind __BELLA__ with his arms around her waist, both fully clothed in evening wear, on a balcony overlooking the city lights.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "iss-extra-limb", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ sits at a café table. Her right hand holds a cup of tea and her left hand rests on a closed book. Her right hand also tucks a strand of __HAIR__ behind her ear.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": ["extra_limb"]}},
{"id": "iss-gaze", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ stands in a field of tall grass, looking away from the camera toward the horizon, while her eyes meet the viewer's with a direct, steady gaze.", "expect": {"partner": "A", "kiss": "N", "emotion": "B", "act": "A", "issues": ["gaze_conflict"]}},
{"id": "iss-facing", "males": 0, "females": 1, "nudity": "partial", "erotic": 0.5, "porn": 0.1, "caption": "__ALICE__ stands at the edge of a pool facing away from the viewer, her back to the camera; her bare __BREASTS__ and her face are clearly visible.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": ["facing_conflict"]}},
{"id": "iss-count", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ and __BELLA__ stand side by side in a kitchen, __ALICE__ chopping vegetables and __BELLA__ stirring a pot on the stove.", "expect": {"partner": "C", "kiss": "N", "emotion": "A", "act": "A", "issues": ["count_conflict"]}},
{"id": "iss-contact", "males": 0, "females": 2, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ and __BELLA__ stand back to back on opposite sides of a wide hall, three metres apart, while __ALICE__ holds __BELLA__'s hand.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": ["impossible_contact"]}},
{"id": "ctl-hands", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ sits cross-legged on the floor. Her right hand holds a paintbrush over a canvas, her left hand steadies a palette on her knee, and her right foot is tucked beneath her left thigh.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "ctl-over-shoulder", "males": 0, "females": 1, "nudity": "none", "erotic": 0.3, "porn": 0.0, "caption": "__ALICE__ stands with her back to the viewer and turns her head over her left shoulder, so her profile and one eye are visible as she glances toward the camera.", "expect": {"partner": "A", "kiss": "N", "emotion": "B", "act": "A", "issues": []}},
{"id": "ctl-two-people", "males": 1, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ADAM__ sits on a park bench reading a newspaper while __BELLA__ stands behind him, her left hand on his shoulder and her right hand holding a dog's lead.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "cor-no-smile", "derived_from": "emo-explicit-smile", "males": 0, "females": 1, "nudity": "none", "erotic": 0.1, "porn": 0.0, "caption": "__ALICE__ leans against a brick wall in a denim jacket with her head tilted slightly to one side. Her right hand holds a takeaway coffee cup at chest height.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
{"id": "cor-extra-hand", "derived_from": "ctl-hands", "males": 0, "females": 1, "nudity": "none", "erotic": 0.2, "porn": 0.0, "caption": "__ALICE__ sits cross-legged on the floor. Her right hand holds a paintbrush over a canvas, her left hand steadies a palette on her knee, and her right hand scratches her cheek.", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": ["extra_limb"]}},
{"id": "cor-facing", "derived_from": "emo-explicit-smile", "males": 0, "females": 1, "nudity": "none", "erotic": 0.1, "porn": 0.0, "caption": "__ALICE__ leans against a brick wall in a denim jacket, facing away from the viewer, smiling broadly at the viewer with her head tilted slightly to one side.", "expect": {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": ["facing_conflict"]}}
]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_caption_classifier_eval.py`:

```python
"""Tests for the fixture file and the evaluation scoring (no model involved)."""

from __future__ import annotations

import json

import pytest

from scripts.caption_classifier import eval as ev


def _dist(letters, pick, p):
    out = {k: 0.0 for k in letters}
    out[pick] = p
    rest = [k for k in letters if k != pick]
    out[rest[0]] = round(1 - p, 4)
    return out


def _rec(partner="A", kiss="N", emotion="A", act="A", p=0.9, issues=()):
    return {
        "status": "ok",
        "partner": _dist("ABCD", partner, p),
        "kiss": _dist("YN", kiss, p),
        "emotion": _dist("ABC", emotion, p),
        "act_gated": _dist("ABCDEFGHIJKLMNOPQRS", act, p),
        "issues": [{"type": t} for t in issues],
    }


def test_shipped_fixtures_are_valid():
    fixtures = ev.load_fixtures(ev.FIXTURES)
    assert len(fixtures) >= 40
    ids = [fx["id"] for fx in fixtures]
    assert len(ids) == len(set(ids))
    acts = {fx["expect"]["act"] for fx in fixtures}
    assert acts == set("ABCDEFGHIJKLMNOPQRS")


def test_invalid_fixture_is_rejected(tmp_path):
    bad = [{"id": "x", "males": 0, "females": 1, "nudity": "none", "erotic": 0.1,
            "porn": 0.0, "caption": "c",
            "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "L",
                       "issues": []}}]
    path = tmp_path / "f.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="x"):
        ev.load_fixtures(path)


def test_score_counts_accuracy_recall_false_alarms_and_calibration():
    fixtures = [
        {"id": "a", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
        {"id": "b", "expect": {"partner": "A", "kiss": "N", "emotion": "C", "act": "L", "issues": ["extra_limb"]}},
        {"id": "c", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}},
    ]
    records = {
        0: _rec(),
        1: _rec(emotion="A", act="M", p=0.6, issues=["extra_limb", "gaze_conflict"]),
        2: _rec(issues=["gaze_conflict"]),
    }
    report = ev.score(fixtures, records)
    assert report["accuracy"]["partner"] == (3, 3)
    assert report["accuracy"]["emotion"] == (2, 3)
    assert report["accuracy"]["act"] == (2, 3)
    assert report["confusion"][("doggy", "cowgirl")] == 1
    assert report["issue_recall"] == (1, 1)
    assert report["issue_false_alarms"] == (1, 2)
    assert report["errors"] == []
    bucket = dict((b[0], (b[1], b[2])) for b in report["calibration"])
    assert bucket["0.5-0.7"] == (4, 2)


def test_error_records_are_listed_not_scored():
    fixtures = [{"id": "a", "expect": {"partner": "A", "kiss": "N", "emotion": "A", "act": "A", "issues": []}}]
    report = ev.score(fixtures, {0: {"status": "error", "error": "HTTP 400"}})
    assert report["errors"] == ["a: HTTP 400"]
    assert report["accuracy"]["act"] == (0, 0)
    assert "a: HTTP 400" in ev.format_report(report)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/pytest tests/test_caption_classifier_eval.py -v`
Expected: FAIL with `ImportError` for `scripts.caption_classifier.eval`

- [ ] **Step 4: Write the implementation**

`scripts/caption_classifier/eval.py`:

```python
"""Measure the classifier on hand-written captions with known answers.

Usage:
    python -m scripts.caption_classifier.eval [--server-url URL]
        [--model qwen3vl-30b-a3b] [--parallel 4] [--fixtures PATH]

Prints per-field accuracy, an act confusion list, plausibility recall and
false alarms, and calibration (top-letter probability against how often that
letter was right). Run it before the full run and after any rubric change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from metascan.core.t2i_captions import CaptionRow

from .classify import DEFAULT_LOG, ExternalServer, setup_logging
from .prompt import PROMPT_VERSION
from .rubric import ACT_BY_LETTER, EMOTION, ISSUE_TYPES, KISS, PARTNER, allowed_acts
from .runner import FatalServerError, Runner
from .server import LlamaServer, ServerError, build_command

FIXTURES = Path(__file__).with_name("fixtures") / "fixtures.json"
FIELDS = (("partner", "partner"), ("kiss", "kiss"), ("emotion", "emotion"), ("act", "act_gated"))
BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


def load_fixtures(path: Path) -> List[Dict[str, Any]]:
    fixtures: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    for fx in fixtures:
        e = fx["expect"]
        ok = (
            e["partner"] in PARTNER
            and e["kiss"] in KISS
            and e["emotion"] in EMOTION
            and e["act"] in ACT_BY_LETTER
            and e["act"] in allowed_acts(fx["males"], fx["females"], e["partner"])
            and all(t in ISSUE_TYPES for t in e["issues"])
        )
        if not ok:
            raise ValueError(f"fixture {fx['id']!r} has an impossible expected answer")
    return fixtures


def fixture_row(index: int, fx: Dict[str, Any]) -> CaptionRow:
    return CaptionRow(
        id=index, caption=fx["caption"], aspect_ratio="1:1", nudity=fx["nudity"],
        artistic_quality=None, erotic_score=fx["erotic"], pornographic_score=fx["porn"],
        males=fx["males"], females=fx["females"], clothing=(),
    )


def _top(dist: Dict[str, float]) -> Tuple[str, float]:
    return max(dist.items(), key=lambda kv: kv[1])


def score(fixtures: List[Dict[str, Any]], records: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    accuracy = {name: [0, 0] for name, _ in FIELDS}
    confusion: Counter = Counter()
    recall = [0, 0]
    false_alarms = [0, 0]
    buckets = [[0, 0] for _ in BUCKETS]
    errors: List[str] = []
    for index, fx in enumerate(fixtures):
        rec = records.get(index)
        if rec is None or rec.get("status") != "ok":
            errors.append(f"{fx['id']}: {(rec or {}).get('error', 'no result')}")
            continue
        expect = fx["expect"]
        for name, key in FIELDS:
            letter, p = _top(rec[key])
            right = letter == expect[name]
            accuracy[name][0] += int(right)
            accuracy[name][1] += 1
            for b, (lo, hi) in enumerate(BUCKETS):
                if lo <= p < hi:
                    buckets[b][0] += 1
                    buckets[b][1] += int(right)
            if name == "act" and not right:
                confusion[(ACT_BY_LETTER[expect["act"]].name, ACT_BY_LETTER[letter].name)] += 1
        found = {i["type"] for i in rec["issues"]}
        if expect["issues"]:
            recall[0] += sum(1 for t in expect["issues"] if t in found)
            recall[1] += len(expect["issues"])
        else:
            false_alarms[0] += int(bool(found))
            false_alarms[1] += 1
    return {
        "accuracy": {k: (v[0], v[1]) for k, v in accuracy.items()},
        "confusion": dict(confusion),
        "issue_recall": (recall[0], recall[1]),
        "issue_false_alarms": (false_alarms[0], false_alarms[1]),
        "calibration": [
            (f"{lo:.1f}-{min(hi, 1.0):.1f}", n, right)
            for (lo, hi), (n, right) in zip(BUCKETS, buckets)
        ],
        "errors": errors,
    }


def _ratio(pair: Tuple[int, int]) -> str:
    right, total = pair
    return f"{right}/{total}" + (f" ({right / total:.0%})" if total else "")


def format_report(report: Dict[str, Any]) -> str:
    lines = [f"prompt version {PROMPT_VERSION}", "", "accuracy:"]
    lines += [f"  {name:8} {_ratio(pair)}" for name, pair in report["accuracy"].items()]
    lines += ["", "act confusions (expected → got):"]
    lines += [f"  {exp} → {got}: {n}" for (exp, got), n in sorted(report["confusion"].items())]
    lines += [
        "",
        f"issue recall:        {_ratio(report['issue_recall'])}",
        f"issue false alarms:  {_ratio(report['issue_false_alarms'])} clean captions flagged",
        "",
        "calibration (top probability → answers, right):",
    ]
    lines += [f"  {label}: {n} answers, {right} right" for label, n, right in report["calibration"]]
    if report["errors"]:
        lines += ["", "errors:"] + [f"  {e}" for e in report["errors"]]
    return "\n".join(lines)


class _Rows:
    def __init__(self, rows: List[CaptionRow]) -> None:
        self._rows = rows

    def get(self, row_id: int) -> CaptionRow:
        return self._rows[row_id]


class _Collect:
    def __init__(self) -> None:
        self.records: Dict[int, Dict[str, Any]] = {}

    def write(self, record: Dict[str, Any]) -> None:
        self.records[int(record["row_id"])] = record


async def _classify(args: argparse.Namespace, rows: List[CaptionRow]) -> Dict[int, Dict[str, Any]]:
    server: Union[ExternalServer, LlamaServer]
    if args.server_url:
        server = ExternalServer(args.server_url)
    else:
        server = LlamaServer(lambda port: build_command(args.model, port, args.parallel, 6144))
    sink = _Collect()
    runner = Runner(
        rows=_Rows(rows), server=server, writer=sink, model_id=args.model,
        prompt_version=PROMPT_VERSION, workers=args.parallel,
    )
    await server.start()
    try:
        await runner.run([r.id for r in rows])
    finally:
        await server.stop()
    return sink.records


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate the classifier on fixture captions.")
    ap.add_argument("--fixtures", type=Path, default=FIXTURES)
    ap.add_argument("--model", default="qwen3vl-30b-a3b")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--server-url", default="")
    ap.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)
    setup_logging(args.log_file)
    fixtures = load_fixtures(args.fixtures)
    rows = [fixture_row(i, fx) for i, fx in enumerate(fixtures)]
    try:
        records = asyncio.run(_classify(args, rows))
    except (ServerError, FatalServerError) as exc:
        print(exc, file=sys.stderr)
        return 3
    print(format_report(score(fixtures, records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Check against the test: in `test_score_counts_accuracy_recall_false_alarms_and_calibration` record 1 has p=0.6 for all four fields → four answers in bucket 0.5–0.7; partner and kiss are right, emotion and act wrong → `(4, 2)`. Records 0 and 2 have p=0.9 → bucket 0.9–1.0.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/pytest tests/test_caption_classifier_eval.py -v`
Expected: 4 passed

- [ ] **Step 6: Format and commit**

```bash
venv/bin/black scripts/caption_classifier tests/test_caption_classifier_eval.py
git add scripts/caption_classifier tests/test_caption_classifier_eval.py
git commit -m "feat(caption-classifier): evaluation fixtures and eval report"
```

---

### Task 11: Full test suite and real-model verification

This task runs against the real `llama-server` and GPU. It changes code only if a check fails; any fix goes back through a failing test first.

**Files:**
- Possibly modify: whichever module a failing check points at.

- [ ] **Step 1: Run the whole suite and quality checks**

Run:
```bash
venv/bin/pytest -q
venv/bin/black --check metascan/ backend/ tests/ scripts/caption_classifier/
venv/bin/flake8 metascan/ backend/ tests/ scripts/caption_classifier/ --count --select=E9,F63,F7,F82 --show-source --statistics
```
Expected: pytest all green except the known flake `test_file_watcher_triggers_reload` (it fails often on this WSL2 box and is not a regression signal); black clean; flake8 `0`.

- [ ] **Step 2: Confirm nothing else is using the GPU**

Run: `nvidia-smi --query-gpu=memory.used,memory.total --format=csv`
Expected: well under 9 GB used. If the app's VLM or ComfyUI holds VRAM, stop and ask the user before going on.

- [ ] **Step 3: A 20-row trial with the real model**

Run: `venv/bin/python -m scripts.caption_classifier.classify --count 20 --parallel 4`
Expected: exit 0; the log shows `llama-server ready`; 20 `ok` rows. If llama-server rejects the grammar or crashes on load, look at `logs/caption_classifier.log` for the `llama-server:` lines and fix `grammar.py` (with a new pytest case reproducing the bad construct).

- [ ] **Step 4: Check that the probabilities are real distributions**

Run:
```bash
venv/bin/python - <<'EOF'
import json, glob
path = sorted(glob.glob("data/t2i_captions/classifier/results-*.jsonl"))[-1]
recs = [json.loads(l) for l in open(path)]
for field in ("partner", "kiss", "emotion", "act_raw"):
    soft = sum(1 for r in recs if max(r[field].values()) < 0.999)
    print(field, f"{soft}/{len(recs)} not one-hot")
EOF
```
Expected: at least some non-one-hot rows for `emotion` and `act_raw`. If **every** distribution is one-hot, llama-server is reporting post-sampling probabilities at temperature 0: add `"post_sampling_probs": False` to `request_body` in `runner.py` (assert it in `test_every_row_gets_an_ok_record`), bump nothing else, delete `data/t2i_captions/classifier/results-<version>.jsonl` (its rows were scored without real distributions; the prompt version is unchanged so it would otherwise be resumed), and rerun Steps 3–4.

- [ ] **Step 5: Run the evaluation**

Run: `venv/bin/python -m scripts.caption_classifier.eval --parallel 4`
Expected: a report prints. Share it with the user verbatim; do not tune the rubric without their go-ahead.

- [ ] **Step 6: Measure throughput at full parallelism**

Run: `venv/bin/python -m scripts.caption_classifier.classify --sample 500 --parallel 16`
Expected: exit 0 or 1. Note the final `rows/s` from the log and compute the full-run ETA as `82880 / rows_per_s`. Watch `nvidia-smi` during the run; if it runs out of memory, retry with `--parallel 12`.

- [ ] **Step 7: Summarize the trial and hand over**

Run: `venv/bin/python -m scripts.caption_classifier.summarize`
Expected: about 520 rows (the 20-row trial plus the 500 sample) → `data/t2i_captions/classifier/classifications.csv`. The trial and sample rows are written to the default results file on purpose, so the full run skips them. Report to the user: test results, eval report, rows/s and full-run ETA, error count and reasons, and the exact full-run command:

```bash
venv/bin/python -m scripts.caption_classifier.classify --parallel 16
venv/bin/python -m scripts.caption_classifier.summarize
```

The user starts the full run themselves.
