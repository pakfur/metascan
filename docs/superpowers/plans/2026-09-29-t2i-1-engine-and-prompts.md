# Text-to-Image (t2i) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **This is plan 1 of 3** - Tasks 1-4: the caption engine, wildcard lists, caption store and prompt generation. Plan 2 (Tasks 5-13) is the backend service; plan 3 (Tasks 14-23) is the frontend and docs. Apply the plans in order.

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

## Phase 1 — Caption engine, wildcards, caption store

Pure and I/O modules with no server dependency. Each task is testable on its own.

### Task 1: Caption engine (`t2i_characters.py`)

**Files:**
- Create: `metascan/core/t2i_characters.py`
- Test: `tests/test_t2i_characters.py`

**Interfaces:**
- Consumes: nothing (standard library only; no I/O).
- Produces:
  - `IDENTITY_STYLES: Tuple[str, ...] = ("ref", "noun", "name")`
  - `DEFAULT_IDENTITY_STYLE = "ref"`
  - `@dataclass(frozen=True) class CharacterConfig` with fields (defaults are the spec 3.6 values): `female_names: Tuple[str, ...]`, `male_names: Tuple[str, ...]`, `noun_female: str`, `noun_male: str`, `slots: Tuple[str, ...]`, `head_slots: Tuple[str, ...]`, `with_slots: Tuple[str, ...]`, `token_slots: Tuple[str, ...]`, `token_gender: Tuple[Tuple[str, str], ...]`, `body_hair_prefixes: Tuple[str, ...]`
  - `@dataclass(frozen=True) class Library`: `config: CharacterConfig`, `lists: Mapping[str, Tuple[str, ...]]` (keys `<slot>`, `<slot>.female`, `<slot>.male`, and any other lowercase token name)
  - `@dataclass(frozen=True) class ResolvedCaption`: `text: str`, `characters: Dict[str, Dict[str, str]]` (NAME -> {slot: drawn value}), `warnings: List[str]`
  - `resolve_caption(caption: str, seed: int, style: str, library: Library) -> ResolvedCaption`
    - pure and deterministic (draws are `sha256(f"{seed}|{name}|{slot}|{salt}")`); never raises on data problems, it returns warnings; a caption with no tokens comes back unchanged with `characters == {}`; an unknown `style` behaves as `ref` and adds a warning.

- [ ] **Step 1: Write the failing test** (`tests/test_t2i_characters.py`)

```python
"""Tests for the pure t2i caption engine (spec section 3).

Every caption in this file is hand-written with known expected properties.
The word lists are small stand-ins defined below -- never the shipped
starter lists and never rows of the real caption CSV. The Appendix B tests
reproduce the spec's worked examples (seed 101) character for character.
"""

from __future__ import annotations

import hashlib
import time
import unittest
from typing import Dict, Mapping, Optional, Sequence, Tuple

from metascan.core.t2i_characters import (
    IDENTITY_STYLES,
    CharacterConfig,
    Library,
    ResolvedCaption,
    resolve_caption,
)

LISTS: Dict[str, Tuple[str, ...]] = {
    "age": (
        "27-year-old",
        "31-year-old",
        "24-year-old",
        "38-year-old",
        "45-year-old",
        "52-year-old",
    ),
    "ethnicity": (
        "Nordic",
        "Latina",
        "East Asian",
        "West African",
        "South Asian",
        "Mediterranean",
    ),
    "skin": ("fair skin", "olive skin", "deep brown skin", "warm tan skin"),
    "eyes": ("green eyes", "hazel eyes", "brown eyes", "blue eyes"),
    "face": (
        "an oval face",
        "a heart-shaped face",
        "high cheekbones",
        "a soft round face",
    ),
    "hair": (
        "auburn hair",
        "jet-black hair",
        "platinum blonde hair",
        "chestnut brown hair",
        "copper red hair",
    ),
    "body.female": (
        "a slim build",
        "a petite frame",
        "an athletic build",
        "a curvy build",
    ),
    "body.male": (
        "an athletic build",
        "a lean build",
        "a stocky build",
        "a broad-shouldered build",
    ),
    # breasts / vagina / penis lists are deliberately absent: they exercise
    # the bare-word fallback. Tests that need them add abstract placeholders.
}

PLACEHOLDER_LISTS: Dict[str, Tuple[str, ...]] = {
    "breasts": ("breasts-alpha", "breasts-beta", "breasts-gamma"),
    "vagina": ("vagina-alpha", "vagina-beta"),
    "penis": ("penis-alpha", "penis-beta", "penis-gamma"),
}

CONFIG = CharacterConfig()


def make_library(
    extra: Optional[Mapping[str, Tuple[str, ...]]] = None,
    drop: Sequence[str] = (),
    config: Optional[CharacterConfig] = None,
) -> Library:
    lists = dict(LISTS)
    lists.update(extra or {})
    for key in drop:
        lists.pop(key, None)
    return Library(config=config or CONFIG, lists=lists)


LIBRARY = make_library()


def resolve(
    caption: str,
    seed: int = 101,
    style: str = "ref",
    library: Optional[Library] = None,
) -> ResolvedCaption:
    return resolve_caption(caption, seed, style, library or LIBRARY)


def cap1(text: str) -> str:
    return text[:1].upper() + text[1:]


def draw(seed: int, name: str, slot: str, salt: int, size: int) -> int:
    """The spec's draw formula, restated independently of the module."""
    digest = hashlib.sha256(f"{seed}|{name}|{slot}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


# ALICE at seed 101 with the stand-in lists above (spec Appendix B):
ALICE_INLINE = (
    "a 31-year-old West African woman with olive skin, brown eyes, "
    "a soft round face, copper red hair and an athletic build"
)
ALICE_TRAIL = (
    "The copper-red-haired woman has olive skin, brown eyes, "
    "a soft round face and an athletic build."
)


class DefaultsTests(unittest.TestCase):
    def test_identity_styles(self) -> None:
        self.assertEqual(IDENTITY_STYLES, ("ref", "noun", "name"))

    def test_character_config_defaults_match_the_spec(self) -> None:
        cfg = CharacterConfig()
        self.assertEqual(
            cfg.female_names, ("ALICE", "BELLA", "CLARA", "DIANNA", "EMMA")
        )
        self.assertEqual(cfg.male_names, ("ADAM", "BOB"))
        self.assertEqual((cfg.noun_female, cfg.noun_male), ("woman", "man"))
        self.assertEqual(
            cfg.slots, ("age", "ethnicity", "skin", "eyes", "face", "hair", "body")
        )
        self.assertEqual(cfg.head_slots, ("age", "ethnicity"))
        self.assertEqual(cfg.with_slots, ("skin", "eyes", "face", "hair", "body"))
        self.assertEqual(cfg.token_slots, ("hair", "breasts", "vagina", "penis"))
        self.assertEqual(
            cfg.token_gender,
            (("breasts", "female"), ("vagina", "female"), ("penis", "male")),
        )
        self.assertEqual(
            cfg.body_hair_prefixes,
            (
                "pubic",
                "body",
                "facial",
                "chest",
                "arm",
                "leg",
                "underarm",
                "armpit",
                "stomach",
            ),
        )


class AppendixBGoldenTests(unittest.TestCase):
    """The spec's worked examples, seed 101, identity style ref."""

    def test_male_and_female_compound_subject(self) -> None:
        caption = (
            "__ADAM__ and __CLARA__ walk along a beach. His short __HAIR__ is damp "
            "and her __HAIR__ is tied back. __ADAM__ carries a surfboard."
        )
        expected = (
            "A 52-year-old South Asian man and a 31-year-old Latina woman walk "
            "along a beach. His short auburn hair is damp and her copper red hair "
            "is tied back. The auburn-haired man carries a surfboard. The "
            "auburn-haired man has warm tan skin, green eyes, high cheekbones and "
            "a lean build. The copper-red-haired woman has olive skin, green eyes, "
            "a soft round face and a curvy build."
        )
        self.assertEqual(resolve(caption).text, expected)

    def test_single_character_with_hair_token_and_later_mention(self) -> None:
        caption = (
            "__ALICE__ sits on a wooden bench in a sunlit garden, her "
            "shoulder-length wavy __HAIR__ moving in the breeze. She holds a paper "
            "cup in both hands while __ALICE__ smiles at the camera."
        )
        expected = (
            "A 31-year-old West African woman with olive skin, brown eyes, a soft "
            "round face and an athletic build sits on a wooden bench in a sunlit "
            "garden, her shoulder-length wavy copper red hair moving in the "
            "breeze. She holds a paper cup in both hands while the "
            "copper-red-haired woman smiles at the camera."
        )
        self.assertEqual(resolve(caption).text, expected)

    def test_possessive_first_mention_body_hair_and_fused_suffix(self) -> None:
        caption = (
            "A close-up of __ALICE__'s hands as she combs her __HAIR__ with a "
            "__HAIR__brush, one forearm showing fine body __HAIR__."
        )
        expected = (
            "A close-up of a 31-year-old West African woman's hands as she combs "
            "her copper red hair with a hairbrush, one forearm showing fine body "
            "hair. The copper-red-haired woman has olive skin, brown eyes, a soft "
            "round face and an athletic build."
        )
        self.assertEqual(resolve(caption).text, expected)


class IdentityStyleTests(unittest.TestCase):
    SINGLE = (
        "__ALICE__ sits on a wooden bench in a sunlit garden, her "
        "shoulder-length wavy __HAIR__ moving in the breeze. She holds a paper "
        "cup in both hands while __ALICE__ smiles at the camera."
    )
    TWO_FEMALES = (
        "__ALICE__ stands beside a window while __BELLA__ sits at a desk. Long, "
        "straight __HAIR__ falls over __BELLA__'s shoulders. __ALICE__ turns to "
        "look at __BELLA__."
    )
    POSSESSIVE = (
        "A close-up of __ALICE__'s hands as she combs her __HAIR__ with a "
        "__HAIR__brush, one forearm showing fine body __HAIR__."
    )

    def test_single_character_all_three_styles(self) -> None:
        head_rest = (
            " sits on a wooden bench in a sunlit garden, her shoulder-length wavy "
            "copper red hair moving in the breeze. She holds a paper cup in both "
            "hands while "
        )
        intro = (
            "A 31-year-old West African woman{named} with olive skin, brown eyes, "
            "a soft round face and an athletic build"
        )
        cases = {
            "ref": (intro.format(named=""), "the copper-red-haired woman"),
            "noun": (intro.format(named=""), "the woman"),
            "name": (intro.format(named=" named Alice"), "Alice"),
        }
        for style, (opening, handle) in cases.items():
            with self.subTest(style=style):
                expected = f"{opening}{head_rest}{handle} smiles at the camera."
                self.assertEqual(resolve(self.SINGLE, style=style).text, expected)

    def test_two_females_owner_tracking_all_three_styles(self) -> None:
        alice = (
            "A 31-year-old West African woman{named} with olive skin, brown eyes, "
            "a soft round face, copper red hair and an athletic build"
        )
        bella = (
            "a 24-year-old South Asian woman{named} with warm tan skin, brown "
            "eyes, a soft round face and a slim build"
        )
        expected = {
            "ref": (
                alice.format(named="")
                + " stands beside a window while "
                + bella.format(named="")
                + " sits at a desk. Long, straight chestnut brown hair falls over "
                "the chestnut-brown-haired woman's shoulders. The "
                "copper-red-haired woman turns to look at the "
                "chestnut-brown-haired woman."
            ),
            "noun": (
                alice.format(named="")
                + " stands beside a window while "
                + bella.format(named="")
                + " sits at a desk. Long, straight chestnut brown hair falls over "
                "the woman's shoulders. The woman turns to look at the woman."
            ),
            "name": (
                alice.format(named=" named Alice")
                + " stands beside a window while "
                + bella.format(named=" named Bella")
                + " sits at a desk. Long, straight chestnut brown hair falls over "
                "Bella's shoulders. Alice turns to look at Bella."
            ),
        }
        for style, text in expected.items():
            with self.subTest(style=style):
                self.assertEqual(resolve(self.TWO_FEMALES, style=style).text, text)

    def test_possessive_first_mention_all_three_styles(self) -> None:
        common_a = "A close-up of a 31-year-old West African woman"
        common_b = (
            " as she combs her copper red hair with a hairbrush, one forearm "
            "showing fine body hair. "
        )
        rest = "has olive skin, brown eyes, a soft round face and an athletic build."
        expected = {
            "ref": f"{common_a}'s hands{common_b}The copper-red-haired woman {rest}",
            "noun": f"{common_a}'s hands{common_b}The woman {rest}",
            # A name-style handle appears in the trailing sentence too.
            "name": f"{common_a} named Alice's hands{common_b}Alice {rest}",
        }
        for style, text in expected.items():
            with self.subTest(style=style):
                self.assertEqual(resolve(self.POSSESSIVE, style=style).text, text)

    def test_ref_falls_back_to_noun_when_hair_does_not_end_in_hair(self) -> None:
        library = make_library({"hair": ("long braids",)})
        res = resolve(
            "__ALICE__ waves. Later __ALICE__ smiles.", library=library, style="ref"
        )
        self.assertIn("Later the woman smiles.", res.text)
        self.assertNotIn("-haired", res.text)

    def test_noun_style_trailing_sentence_keeps_hair(self) -> None:
        text = resolve("A close-up of __ALICE__'s hands.", style="noun").text
        self.assertEqual(
            text,
            "A close-up of a 31-year-old West African woman's hands. The woman has "
            "olive skin, brown eyes, a soft round face, copper red hair and an "
            "athletic build.",
        )

    def test_ref_trailing_sentence_drops_hair_only_when_the_handle_carries_it(
        self,
    ) -> None:
        caption = "A close-up of __ALICE__'s hands."
        ref = resolve(caption, style="ref").text
        self.assertEqual(
            ref,
            "A close-up of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )
        library = make_library({"hair": ("long braids",)})
        no_handle = resolve(caption, style="ref", library=library).text
        self.assertTrue(no_handle.endswith("long braids and an athletic build."))
        self.assertIn("The woman has", no_handle)


class FirstMentionTests(unittest.TestCase):
    def test_inline_details_and_hair_omitted_when_spelled_out_by_a_token(self) -> None:
        text = resolve("__ALICE__ waves. Her __HAIR__ is loose.").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman with olive skin, brown eyes, a soft "
            "round face and an athletic build waves. Her copper red hair is loose.",
        )

    def test_with_continuation_puts_details_in_a_trailing_sentence(self) -> None:
        text = resolve("__ALICE__ with a red scarf waves.").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman with a red scarf waves. " + ALICE_TRAIL,
        )

    def test_next_word_blocklist_forces_a_trailing_sentence(self) -> None:
        for word in (
            "with",
            "and",
            "or",
            "in",
            "on",
            "at",
            "who",
            "whose",
            "wearing",
            "holding",
            "while",
            "as",
        ):
            with self.subTest(word=word):
                text = resolve(f"__ALICE__ {word} a friend waves.").text
                self.assertEqual(
                    text,
                    f"A 31-year-old West African woman {word} a friend waves. "
                    + ALICE_TRAIL,
                )

    def test_punctuation_after_the_token_forces_a_trailing_sentence(self) -> None:
        for mark in (",", ".", ";", ":", "!", "?", "\u2014"):
            with self.subTest(mark=mark):
                text = resolve(f"Meet __ALICE__{mark} she waves.").text
                self.assertEqual(
                    text,
                    f"Meet a 31-year-old West African woman{mark} she waves. "
                    + ALICE_TRAIL,
                )

    def test_a_verb_after_the_token_stays_inline(self) -> None:
        text = resolve("__ALICE__ waves.").text
        self.assertEqual(text, cap1(ALICE_INLINE) + " waves.")

    def test_token_at_the_end_of_the_string_stays_inline(self) -> None:
        self.assertEqual(resolve("Look at __ALICE__").text, "Look at " + ALICE_INLINE)

    def test_possessive_forces_a_trailing_sentence_straight_or_curly(self) -> None:
        straight = resolve("A close-up of __ALICE__'s hands.").text
        curly = resolve("A close-up of __ALICE__\u2019s hands.").text
        self.assertEqual(
            straight,
            "A close-up of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )
        self.assertEqual(curly, straight.replace("'", "\u2019"))

    def test_trailing_sentence_gets_a_full_stop_when_the_caption_has_none(self) -> None:
        text = resolve("A portrait of __ALICE__'s hands").text
        self.assertEqual(
            text,
            "A portrait of a 31-year-old West African woman's hands. " + ALICE_TRAIL,
        )

    def test_trailing_sentence_looks_through_closing_quotes_for_the_stop(self) -> None:
        stopped = resolve('She says "meet __ALICE__, please."').text
        self.assertEqual(
            stopped,
            'She says "meet a 31-year-old West African woman, please." ' + ALICE_TRAIL,
        )
        unstopped = resolve('He said "hi to __ALICE__"').text
        self.assertEqual(
            unstopped,
            'He said "hi to a 31-year-old West African woman". ' + ALICE_TRAIL,
        )

    def test_a_dangling_clause_mark_becomes_the_stop(self) -> None:
        text = resolve("A portrait of __ALICE__'s friend,").text
        self.assertEqual(
            text,
            "A portrait of a 31-year-old West African woman's friend. " + ALICE_TRAIL,
        )

    def test_trailing_whitespace_is_kept_after_the_trailing_sentence(self) -> None:
        text = resolve("__ALICE__, smiling.\n").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman, smiling. " + ALICE_TRAIL + "\n",
        )

    def test_deferred_characters_trail_in_first_mention_order(self) -> None:
        text = resolve("__ALICE__, __BELLA__ and __CLARA__ wave.").text
        self.assertEqual(
            text,
            "A 31-year-old West African woman, a 24-year-old South Asian woman "
            "and a 31-year-old Latina woman wave. The copper-red-haired woman has "
            "olive skin, brown eyes, a soft round face and an athletic build. The "
            "chestnut-brown-haired woman has warm tan skin, brown eyes, a soft "
            "round face and a slim build. The auburn-haired woman has olive skin, "
            "green eyes, a soft round face and a curvy build.",
        )

    def test_compound_subject_with_comma_and_oxford_and(self) -> None:
        text = resolve("__ALICE__, __BELLA__, and __CLARA__ wave.").text
        # Nobody gets inline details: each name is part of a compound subject.
        self.assertNotIn("woman with", text)
        self.assertEqual(text.count(" has "), 3)

    def test_no_trailing_sentence_when_there_is_nothing_left_to_say(self) -> None:
        library = Library(config=CONFIG, lists={"hair": ("auburn hair",)})
        res = resolve(
            "A close-up of __ALICE__'s hands. Her __HAIR__ shines.", library=library
        )
        self.assertEqual(
            res.text, "A close-up of a woman's hands. Her auburn hair shines."
        )

    def test_hair_is_never_lost_when_it_is_the_only_detail(self) -> None:
        library = Library(config=CONFIG, lists={"hair": ("auburn hair",)})
        for style, expected in (
            ("ref", "A close-up of a woman's hands. The woman has auburn hair."),
            ("noun", "A close-up of a woman's hands. The woman has auburn hair."),
            (
                "name",
                "A close-up of a woman named Alice's hands. Alice has auburn hair.",
            ),
        ):
            with self.subTest(style=style):
                res = resolve(
                    "A close-up of __ALICE__'s hands.", style=style, library=library
                )
                self.assertEqual(res.text, expected)

    def test_an_before_a_vowel_or_a_number_spoken_with_a_vowel(self) -> None:
        cases = (
            ({"age": ("82-year-old",)}, "An 82-year-old West African woman"),
            ({"age": ("88-year-old",)}, "An 88-year-old West African woman"),
            ({"age": ("80-year-old",)}, "An 80-year-old West African woman"),
            ({"age": ("18-year-old",)}, "An 18-year-old West African woman"),
            ({"age": ("27-year-old",)}, "A 27-year-old West African woman"),
            ({"age": ("100-year-old",)}, "A 100-year-old West African woman"),
            ({"age": ("108-year-old",)}, "A 108-year-old West African woman"),
        )
        for extra, expected in cases:
            with self.subTest(age=extra["age"][0]):
                text = resolve("__ALICE__ waves.", library=make_library(extra)).text
                self.assertTrue(text.startswith(expected), text)

    def test_an_before_ordinals_spoken_with_a_vowel(self) -> None:
        cases = (
            ("11th-generation American", "An 11th-generation American woman with"),
            ("8th-generation American", "An 8th-generation American woman with"),
            ("18th-century Parisian", "An 18th-century Parisian woman with"),
            ("7th-generation American", "A 7th-generation American woman with"),
        )
        for ethnicity, expected in cases:
            with self.subTest(ethnicity=ethnicity):
                library = make_library({"age": (), "ethnicity": (ethnicity,)})
                text = resolve("__ALICE__ waves.", library=library).text
                self.assertTrue(text.startswith(expected), text)

    def test_article_follows_the_first_word_when_there_is_no_age(self) -> None:
        cases = (
            ("Asian", "An Asian woman with"),
            ("European", "A European woman with"),
            ("Nordic", "A Nordic woman with"),
            ("Ukrainian", "A Ukrainian woman with"),
            ("Uzbek", "An Uzbek woman with"),
        )
        for ethnicity, expected in cases:
            with self.subTest(ethnicity=ethnicity):
                library = make_library({"age": (), "ethnicity": (ethnicity,)})
                text = resolve("__ALICE__ waves.", library=library).text
                self.assertTrue(text.startswith(expected), text)

    def test_head_is_just_the_noun_when_age_and_ethnicity_are_missing(self) -> None:
        library = make_library({"age": (), "ethnicity": ()})
        text = resolve("__ALICE__ waves.", library=library).text
        self.assertTrue(text.startswith("A woman with olive skin"), text)


class CharacteristicTokenTests(unittest.TestCase):
    def test_hair_token_before_the_first_mention_binds_to_the_first_female(
        self,
    ) -> None:
        text = resolve(
            "Her long __HAIR__ falls over her shoulders as __ALICE__ smiles."
        ).text
        self.assertEqual(
            text,
            "Her long copper red hair falls over her shoulders as a 31-year-old "
            "West African woman with olive skin, brown eyes, a soft round face "
            "and an athletic build smiles.",
        )

    def test_hair_with_no_pronoun_goes_to_the_nearest_named_character(self) -> None:
        res = resolve(
            "__ALICE__ and __BELLA__ pose while a stylist adjusts the __HAIR__.",
            style="noun",
        )
        alice = res.characters["ALICE"]["hair"]
        bella = res.characters["BELLA"]["hair"]
        self.assertIn(f"adjusts the {bella}.", res.text)
        # BELLA's hair is spelled out once (the token); ALICE's is not spelled
        # out, so it stays in her trailing description.
        self.assertEqual(res.text.count(bella), 1)
        self.assertEqual(res.text.count(alice), 1)

    def test_feminine_pronoun_beats_proximity(self) -> None:
        res = resolve(
            "__ALICE__ watches __ADAM__ while she brushes her __HAIR__. __ADAM__ nods.",
            style="noun",
        )
        alice = res.characters["ALICE"]["hair"]
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"she brushes her {alice}.", res.text)
        self.assertEqual(res.text.count(alice), 1)
        # ADAM's own hair is not spelled out, so it stays in his description.
        self.assertEqual(res.text.count(adam), 1)

    def test_masculine_pronoun_beats_proximity(self) -> None:
        res = resolve(
            "__ADAM__ watches __ALICE__ while he brushes his __HAIR__. __ALICE__ nods."
        )
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"he brushes his {adam}.", res.text)

    def test_the_nearest_pronoun_wins_when_both_genders_are_in_the_window(
        self,
    ) -> None:
        # "he" and "her" (or "she" and "his") are both inside the six-word
        # window; the one nearest the token decides. The names are given in
        # both orders so the nearest NAMED character is sometimes the wrong one.
        cases = (
            ("__ADAM__ sees __ALICE__ and he touches her __HAIR__.", "ALICE", "her"),
            ("__ALICE__ sees __ADAM__ and he touches her __HAIR__.", "ALICE", "her"),
            ("__ALICE__ sees __ADAM__ and she touches his __HAIR__.", "ADAM", "his"),
            ("__ADAM__ sees __ALICE__ and she touches his __HAIR__.", "ADAM", "his"),
        )
        for caption, owner, pronoun in cases:
            with self.subTest(caption=caption):
                res = resolve(caption)
                hair = res.characters[owner]["hair"]
                self.assertIn(f"touches {pronoun} {hair}.", res.text)

    def test_two_hair_tokens_in_one_sentence_follow_their_own_pronouns(self) -> None:
        # Both "Her" and "his" sit in the second token's window; "his" is nearer.
        for names in ("__ALICE__ and __ADAM__", "__ADAM__ and __ALICE__"):
            with self.subTest(names=names):
                res = resolve(
                    f"{names} stand together. "
                    "Her __HAIR__ is long and his __HAIR__ is short."
                )
                alice = res.characters["ALICE"]["hair"]
                adam = res.characters["ADAM"]["hair"]
                self.assertNotEqual(alice, adam)
                self.assertIn(f"Her {alice} is long and his {adam} is short.", res.text)

    def test_the_nearest_pronoun_beats_an_earlier_one_of_the_other_gender(
        self,
    ) -> None:
        cases = (
            (
                "__ADAM__ and __ALICE__ talk. He holds her __HAIR__ back.",
                "ALICE",
                "He holds her {} back.",
            ),
            (
                "__ALICE__ and __ADAM__ talk. He holds her __HAIR__ back.",
                "ALICE",
                "He holds her {} back.",
            ),
            (
                "__ALICE__ and __ADAM__ talk. She strokes his __HAIR__.",
                "ADAM",
                "She strokes his {}.",
            ),
            (
                "__ADAM__ and __ALICE__ talk. She strokes his __HAIR__.",
                "ADAM",
                "She strokes his {}.",
            ),
        )
        for caption, owner, phrase in cases:
            with self.subTest(caption=caption):
                res = resolve(caption)
                self.assertIn(phrase.format(res.characters[owner]["hair"]), res.text)

    def test_a_pronoun_seven_words_back_is_outside_the_window(self) -> None:
        # Six words before the token count (the sixth is "His")...
        inside = resolve(
            "__ADAM__ and __ALICE__ talk. His one two three four five __HAIR__ is long."
        )
        adam = inside.characters["ADAM"]["hair"]
        self.assertIn(f"four five {adam} is long.", inside.text)
        # ...the seventh does not, so no pronoun decides and the nearest named
        # character (ALICE) owns the token, exactly as with no pronoun at all.
        outside = resolve(
            "__ADAM__ and __ALICE__ talk. "
            "His one two three four five six __HAIR__ is long."
        )
        alice = outside.characters["ALICE"]["hair"]
        self.assertIn(f"five six {alice} is long.", outside.text)

    def test_pronoun_from_the_previous_sentence_is_ignored(self) -> None:
        res = resolve(
            "__ALICE__ meets __ADAM__. Her coat is red. The __HAIR__ is long."
        )
        adam = res.characters["ADAM"]["hair"]
        self.assertIn(f"The {adam} is long.", res.text)

    def test_pronoun_contraction_counts(self) -> None:
        # ADAM is the nearest named character; "she's" points at ALICE.
        res = resolve("__ALICE__ meets __ADAM__ and she's proud of the __HAIR__.")
        alice = res.characters["ALICE"]["hair"]
        self.assertIn(f"she's proud of the {alice}.", res.text)
        res2 = resolve("__ADAM__ meets __ALICE__ and he's proud of the __HAIR__.")
        adam = res2.characters["ADAM"]["hair"]
        self.assertIn(f"he's proud of the {adam}.", res2.text)
        # A contraction nearer the token beats an earlier plain pronoun.
        res3 = resolve(
            "__ALICE__ meets __ADAM__ and he says she's proud of the __HAIR__."
        )
        alice3 = res3.characters["ALICE"]["hair"]
        self.assertIn(f"she's proud of the {alice3}.", res3.text)

    def test_hair_with_a_masculine_pronoun_and_a_female_only_cast_is_bare(self) -> None:
        res = resolve("__ALICE__ watches while his __HAIR__ blows.")
        self.assertIn("while his hair blows.", res.text)
        # ALICE's own hair stays in her description.
        self.assertIn(res.characters["ALICE"]["hair"], res.text.split("watches")[0])

    def test_body_hair_prefixes_make_the_token_a_bare_word(self) -> None:
        for prefix in (
            "pubic",
            "body",
            "facial",
            "chest",
            "arm",
            "leg",
            "underarm",
            "armpit",
            "stomach",
        ):
            with self.subTest(prefix=prefix):
                res = resolve(f"__ALICE__ shows fine {prefix} __HAIR__.")
                self.assertTrue(
                    res.text.endswith(f"shows fine {prefix} hair."), res.text
                )
                # The bare word does not count as spelling out her hair.
                self.assertIn("copper red hair and an athletic build", res.text)

    def test_body_hair_prefix_match_is_case_insensitive(self) -> None:
        res = resolve("__ALICE__ shows fine Body __HAIR__.")
        self.assertTrue(res.text.endswith("shows fine Body hair."), res.text)

    def test_fused_suffix_makes_the_token_a_bare_word(self) -> None:
        res = resolve("__ALICE__ holds a __HAIR__brush.")
        self.assertEqual(res.text, cap1(ALICE_INLINE) + " holds a hairbrush.")

    def test_fused_vaginal_is_a_bare_word_even_with_a_list(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ attends a __VAGINA__l health talk.", library=library)
        self.assertIn("attends a vaginal health talk.", res.text)
        self.assertEqual(res.warnings, [])
        self.assertNotIn("vagina", res.characters["ALICE"])

    def test_missing_list_makes_the_token_a_bare_word_and_warns(self) -> None:
        res = resolve("__ALICE__ covers her __BREASTS__ with a scarf.")
        self.assertEqual(
            res.text,
            cap1(ALICE_INLINE) + " covers her breasts with a scarf.",
        )
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("breasts", res.warnings[0])

    def test_empty_list_behaves_like_a_missing_one(self) -> None:
        library = make_library({"breasts": ()})
        res = resolve("__ALICE__ covers her __BREASTS__.", library=library)
        self.assertIn("covers her breasts.", res.text)
        self.assertTrue(any("breasts" in w for w in res.warnings))

    def test_a_list_supplies_the_value_and_the_draw_is_per_owner(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve(
            "__ALICE__ and __BELLA__ pose. __BELLA__ covers her __BREASTS__.",
            library=library,
        )
        options = PLACEHOLDER_LISTS["breasts"]
        expected = options[draw(101, "BELLA", "breasts", 0, len(options))]
        self.assertEqual(res.characters["BELLA"]["breasts"], expected)
        self.assertIn(f"covers her {expected}.", res.text)
        # ALICE's token-only slot was never referenced, so it is not drawn.
        self.assertNotIn("breasts", res.characters["ALICE"])
        self.assertEqual(res.warnings, [])

    def test_male_token_binds_to_the_male_even_when_a_female_is_nearer(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve(
            "__ADAM__ smiles at __ALICE__ and the chart notes the __PENIS__ note.",
            library=library,
        )
        options = PLACEHOLDER_LISTS["penis"]
        expected = options[draw(101, "ADAM", "penis", 0, len(options))]
        self.assertIn(f"the chart notes the {expected} note.", res.text)
        self.assertIn("penis", res.characters["ADAM"])
        self.assertNotIn("penis", res.characters["ALICE"])

    def test_token_with_no_character_of_its_gender_is_bare_and_warns(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ points at the __PENIS__ diagram.", library=library)
        self.assertIn("points at the penis diagram.", res.text)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("penis", res.warnings[0])
        res2 = resolve("__ADAM__ points at the __BREASTS__ diagram.", library=library)
        self.assertIn("points at the breasts diagram.", res2.text)

    def test_typed_caption_with_a_hair_token_gets_a_default_owner(self) -> None:
        res = resolve("A woman brushes her __HAIR__ by the window.")
        self.assertEqual(res.text, "A woman brushes her copper red hair by the window.")
        self.assertEqual(list(res.characters), ["ALICE"])
        self.assertEqual(res.characters["ALICE"]["hair"], "copper red hair")
        self.assertEqual(res.warnings, [])

    def test_default_owner_for_a_masculine_pronoun_is_the_first_male(self) -> None:
        res = resolve("A man brushes his __HAIR__.", seed=1)
        self.assertEqual(res.text, "A man brushes his jet-black hair.")
        self.assertEqual(list(res.characters), ["ADAM"])

    def test_default_owner_for_penis_is_the_first_male(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("A diagram labelled __PENIS__.", library=library)
        options = PLACEHOLDER_LISTS["penis"]
        expected = options[draw(101, "ADAM", "penis", 0, len(options))]
        self.assertEqual(res.text, f"A diagram labelled {expected}.")
        self.assertEqual(list(res.characters), ["ADAM"])

    def test_two_default_owners_get_distinct_hair(self) -> None:
        res = resolve("Her __HAIR__ is long. His __HAIR__ is short.", seed=1)
        self.assertEqual(
            res.text, "Her auburn hair is long. His jet-black hair is short."
        )
        self.assertEqual(list(res.characters), ["ALICE", "ADAM"])

    def test_repeated_hair_tokens_for_one_owner_repeat_the_value(self) -> None:
        res = resolve("Her __HAIR__ is long. She combs her __HAIR__ daily.")
        self.assertEqual(res.text.count("copper red hair"), 2)

    def test_default_owner_does_not_appear_when_the_only_token_is_fused(self) -> None:
        res = resolve("She holds a __HAIR__brush.")
        self.assertEqual(res.text, "She holds a hairbrush.")
        self.assertEqual(res.characters, {})

    def test_missing_hair_list_makes_the_token_bare_and_the_handle_plain(self) -> None:
        library = make_library(drop=("hair",))
        res = resolve(
            "__ALICE__ combs her __HAIR__. Later __ALICE__ smiles.", library=library
        )
        self.assertIn("combs her hair.", res.text)
        self.assertIn("Later the woman smiles.", res.text)
        self.assertNotIn("hair", res.characters["ALICE"])
        self.assertTrue(any("hair" in w for w in res.warnings))

    def test_token_owner_defaults_use_the_configured_names(self) -> None:
        config = CharacterConfig(female_names=("ZOE",), male_names=("MAX",))
        library = make_library(config=config)
        res = resolve("Her __HAIR__ is long.", library=library)
        self.assertEqual(list(res.characters), ["ZOE"])


class DrawTests(unittest.TestCase):
    def test_draws_follow_the_documented_sha256_formula(self) -> None:
        res = resolve("__ALICE__ waves.", seed=101)
        slots = ("age", "ethnicity", "skin", "eyes", "face", "hair", "body")
        lists = dict(LISTS)
        lists["body"] = LISTS["body.female"]
        for slot in slots:
            options = lists[slot]
            expected = options[draw(101, "ALICE", slot, 0, len(options))]
            self.assertEqual(res.characters["ALICE"][slot], expected, slot)
        self.assertEqual(list(res.characters["ALICE"]), list(slots))

    def test_same_inputs_give_identical_results(self) -> None:
        caption = "__ALICE__ meets __ADAM__. Her __HAIR__ is long. __ADAM__ nods."
        for style in IDENTITY_STYLES:
            first = resolve(caption, seed=77, style=style)
            second = resolve(caption, seed=77, style=style)
            self.assertEqual(first, second)

    def test_different_seeds_change_the_cast(self) -> None:
        seen = {resolve("__ALICE__ waves.", seed=s).text for s in range(20)}
        self.assertGreater(len(seen), 10)

    def test_alice_is_unchanged_when_other_characters_join(self) -> None:
        for seed in range(60):
            solo = resolve("__ALICE__ waves.", seed=seed).characters["ALICE"]
            pair = resolve("__ALICE__ waves at __BELLA__.", seed=seed).characters
            trio = resolve(
                "__ALICE__ waves at __BELLA__ and __ADAM__.", seed=seed
            ).characters
            self.assertEqual(solo, pair["ALICE"], seed)
            self.assertEqual(solo, trio["ALICE"], seed)

    def test_default_alice_matches_named_alice(self) -> None:
        typed = resolve("Her __HAIR__ is long.", seed=9).characters["ALICE"]
        named = resolve("__ALICE__ waves.", seed=9).characters["ALICE"]
        self.assertEqual(typed, named)

    def test_hair_is_distinct_across_the_cast_over_many_seeds(self) -> None:
        caption = "__ALICE__, __BELLA__ and __CLARA__ wave."
        for seed in range(300):
            chars = resolve(caption, seed=seed).characters
            hairs = [chars[n]["hair"] for n in ("ALICE", "BELLA", "CLARA")]
            self.assertEqual(len(set(hairs)), 3, (seed, hairs))

    def test_hair_is_distinct_across_genders_too(self) -> None:
        for seed in range(100):
            chars = resolve("__CLARA__ and __ADAM__ wave.", seed=seed).characters
            self.assertNotEqual(chars["CLARA"]["hair"], chars["ADAM"]["hair"], seed)

    def test_too_short_a_hair_list_falls_back_to_noun_handles_and_warns(self) -> None:
        library = make_library({"hair": ("auburn hair", "jet-black hair")})
        caption = (
            "__ALICE__, __BELLA__ and __CLARA__ stand together. __ALICE__ smiles. "
            "__BELLA__ nods. __CLARA__ waves."
        )
        for seed in range(25):
            res = resolve(caption, seed=seed, library=library)
            hair = {n: res.characters[n]["hair"] for n in ("ALICE", "BELLA", "CLARA")}
            self.assertEqual(len(set(hair.values())), 2, seed)
            self.assertTrue(any("hair" in w for w in res.warnings), seed)
            shared = {v for v in hair.values() if list(hair.values()).count(v) > 1}
            for name, verb in (
                ("ALICE", "smiles"),
                ("BELLA", "nods"),
                ("CLARA", "waves"),
            ):
                if hair[name] in shared:
                    handle = "The woman"
                else:
                    handle = (
                        "The " + hair[name][:-5].replace(" ", "-") + "-haired woman"
                    )
                self.assertIn(f"{handle} {verb}.", res.text, (seed, name))

    def test_a_seven_person_cast_with_five_hair_values_never_raises(self) -> None:
        caption = (
            "__ALICE__ __BELLA__ __CLARA__ __DIANNA__ __EMMA__ __ADAM__ __BOB__ "
            "stand in a row."
        )
        for seed in (0, 1, 5, 101, 4242):
            res = resolve(caption, seed=seed)
            self.assertEqual(len(res.characters), 7)
            self.assertTrue(any("hair" in w for w in res.warnings))

    def test_gendered_lists_override_the_plain_list_for_that_gender(self) -> None:
        library = make_library(
            {
                "hair": ("plain hair one", "plain hair two"),
                "hair.male": ("buzz-cut black hair",),
            }
        )
        for seed in range(30):
            chars = resolve("__ALICE__ and __ADAM__ wave.", seed=seed, library=library)
            self.assertIn(
                chars.characters["ALICE"]["hair"], ("plain hair one", "plain hair two")
            )
            self.assertEqual(chars.characters["ADAM"]["hair"], "buzz-cut black hair")

    def test_body_lists_are_split_by_gender(self) -> None:
        for seed in range(30):
            chars = resolve("__ALICE__ and __ADAM__ wave.", seed=seed).characters
            self.assertIn(chars["ALICE"]["body"], LISTS["body.female"])
            self.assertIn(chars["ADAM"]["body"], LISTS["body.male"])

    def test_a_slot_with_a_list_for_one_gender_only_is_omitted_for_the_other(
        self,
    ) -> None:
        library = make_library(drop=("body.male",))
        res = resolve("__ALICE__ and __ADAM__ wave.", library=library)
        self.assertIn("body", res.characters["ALICE"])
        self.assertNotIn("body", res.characters["ADAM"])
        self.assertTrue(any("body" in w for w in res.warnings))

    def test_a_missing_slot_list_is_omitted_and_warned_about_once(self) -> None:
        library = make_library(drop=("eyes",))
        res = resolve("__ALICE__ and __BELLA__ wave.", library=library)
        self.assertNotIn("eyes", res.text)
        self.assertEqual(sum("eyes" in w for w in res.warnings), 1)


class PlainWildcardTests(unittest.TestCase):
    SETTING = ("a sunlit garden", "a foggy pier", "a quiet library")

    def test_wildcard_with_a_list_is_seeded_and_consistent(self) -> None:
        library = make_library({"setting": self.SETTING})
        caption = "__ALICE__ waits in __SETTING__. She stays in __SETTING__ until dusk."
        for seed in (0, 1, 101, 999):
            expected = self.SETTING[
                int.from_bytes(
                    hashlib.sha256(f"{seed}|SETTING|wildcard|0".encode()).digest()[:8],
                    "big",
                )
                % len(self.SETTING)
            ]
            text = resolve(caption, seed=seed, library=library).text
            self.assertIn(f"waits in {expected}. She stays in {expected} until", text)

    def test_wildcard_value_is_independent_of_the_cast(self) -> None:
        library = make_library({"setting": self.SETTING})
        for seed in range(20):
            digest = hashlib.sha256(f"{seed}|SETTING|wildcard|0".encode()).digest()
            value = self.SETTING[int.from_bytes(digest[:8], "big") % 3]
            alone = resolve("__SETTING__", seed=seed, library=library).text
            with_cast = resolve(
                "__ALICE__ waves in __SETTING__.", seed=seed, library=library
            ).text
            self.assertEqual(alone, cap1(value))
            self.assertTrue(with_cast.endswith(f"waves in {value}."), seed)

    def test_wildcard_without_a_list_becomes_its_lowercase_word_and_warns(self) -> None:
        res = resolve("__ALICE__ waits in __SETTING__.")
        self.assertTrue(res.text.endswith("waits in setting."), res.text)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("setting", res.warnings[0])

    def test_wildcard_at_a_sentence_start_is_capitalised(self) -> None:
        library = make_library({"setting": self.SETTING})
        text = resolve("__SETTING__ is quiet.", seed=3, library=library).text
        self.assertTrue(text[0].isupper(), text)
        self.assertTrue(text.endswith(" is quiet."), text)

    def test_wildcard_that_is_also_a_slot_name_uses_the_plain_list(self) -> None:
        # ``skin`` is a draw slot but not a token slot: __SKIN__ is a wildcard.
        res = resolve("Her __SKIN__ glows.", seed=5)
        self.assertIn(" glows.", res.text)
        expected_options = LISTS["skin"]
        self.assertTrue(any(v in res.text for v in expected_options), res.text)
        self.assertEqual(res.characters, {})


class SentenceStartTests(unittest.TestCase):
    def test_capitalised_at_the_start_of_the_text(self) -> None:
        self.assertTrue(resolve("__ALICE__ waves.").text.startswith("A 31-year-old"))

    def test_capitalised_after_sentence_punctuation(self) -> None:
        for lead in ("She waves. ", "Who? ", "Wow! ", "Well\u2026 "):
            with self.subTest(lead=lead):
                text = resolve(lead + "__ALICE__ smiles.").text
                self.assertEqual(text, lead + cap1(ALICE_INLINE) + " smiles.")

    def test_lowercase_mid_sentence(self) -> None:
        for lead in ("Then ", "Scene: ", "Then, ", "Behind "):
            with self.subTest(lead=lead):
                text = resolve(lead + "__ALICE__ smiles.").text
                self.assertEqual(text, lead + ALICE_INLINE + " smiles.")

    def test_capitalised_after_a_line_break(self) -> None:
        text = resolve("Scene one\n__ALICE__ smiles.").text
        self.assertEqual(text, "Scene one\n" + cap1(ALICE_INLINE) + " smiles.")

    def test_capitalised_after_an_opening_quote(self) -> None:
        text = resolve('"__ALICE__ smiles," he says.').text
        self.assertEqual(text, '"' + cap1(ALICE_INLINE) + ' smiles," he says.')
        text = resolve('He says: "Look. __ALICE__ smiles."').text
        self.assertIn('"Look. A 31-year-old', text)

    def test_apostrophes_are_not_sentence_boundaries(self) -> None:
        text = resolve("The girls' __HAIR__ shines.").text
        self.assertTrue(text.startswith("The girls' copper red hair shines"), text)

    def test_later_mentions_are_capitalised_at_a_sentence_start(self) -> None:
        caption = "__ALICE__ waves. __ALICE__ smiles."
        ref = resolve(caption, style="ref").text
        noun = resolve(caption, style="noun").text
        self.assertTrue(ref.endswith(" waves. The copper-red-haired woman smiles."))
        self.assertTrue(noun.endswith(" waves. The woman smiles."))

    def test_characteristic_and_bare_tokens_are_capitalised_at_a_sentence_start(
        self,
    ) -> None:
        self.assertEqual(resolve("__HAIR__ falls.").text, "Copper red hair falls.")
        res = resolve("__BREASTS__ are covered.")
        self.assertEqual(res.text, "Breasts are covered.")
        self.assertTrue(res.warnings)

    def test_mid_sentence_replacements_are_not_capitalised(self) -> None:
        text = resolve("__ALICE__ waves and __ALICE__ smiles.", style="noun").text
        self.assertTrue(text.endswith(" waves and the woman smiles."), text)


class EdgeInputTests(unittest.TestCase):
    """Review focus 2: awkward token neighbours, awkward seeds, no exceptions."""

    SEEDS = (0, 2**31 - 1, -1, -(2**31), 2**63, 10**40)

    def test_token_neighbours_never_raise_and_are_deterministic(self) -> None:
        captions = (
            "__ALICE__",
            "__ALICE__,",
            "__ALICE__.",
            "(__ALICE__)",
            '"__ALICE__"',
            "x__ALICE__y",
            "__ALICE__\u2019s",
            "__ALICE__'s",
            "__ALICE__\u2019",
            "__ALICE__\u2026",
            "__ALICE__\n",
            "\n__ALICE__",
            "__ALICE__ __BELLA__",
            "__ALICE__,__BELLA__",
            "__ALICE____BELLA__",
            "__HAIR__",
            "__HAIR__\u2019s",
            "__HAIR__brush",
            "her __HAIR__",
            "his __HAIR__.",
            "____BREASTS____",
            "____ALICE____ waves at ____BELLA____.",
        )
        for caption in captions:
            for seed in self.SEEDS:
                for style in IDENTITY_STYLES:
                    with self.subTest(caption=caption, seed=seed, style=style):
                        first = resolve(caption, seed=seed, style=style)
                        second = resolve(caption, seed=seed, style=style)
                        self.assertEqual(first, second)
                        self.assertNotIn("__ALICE__", first.text)
                        self.assertNotIn("__HAIR__", first.text)

    def test_curly_apostrophe_matches_the_straight_one(self) -> None:
        for seed in self.SEEDS:
            straight = resolve("__ALICE__'s hat.", seed=seed).text
            curly = resolve("__ALICE__\u2019s hat.", seed=seed).text
            self.assertEqual(curly, straight.replace("'", "\u2019"))

    def test_empty_and_whitespace_only_captions_are_unchanged(self) -> None:
        for caption in ("", " ", "   ", "\n", "\t \n ", "\u00a0"):
            for style in IDENTITY_STYLES:
                res = resolve(caption, style=style)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})
                self.assertEqual(res.warnings, [])

    def test_caption_with_no_tokens_is_unchanged(self) -> None:
        for caption in (
            "A red bicycle leaning against a brick wall at dusk.",
            "  Leading and trailing space.  \n",
            "Line one.\n\nLine two \u2014 with a dash, \u201cquotes\u201d and \u00e9\u00e8.",
        ):
            for style in IDENTITY_STYLES:
                res = resolve(caption, style=style)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})
                self.assertEqual(res.warnings, [])

    def test_extreme_seeds_draw_valid_values(self) -> None:
        for seed in self.SEEDS:
            res = resolve("__ALICE__ waves at __ADAM__.", seed=seed)
            self.assertIn(res.characters["ALICE"]["age"], LISTS["age"])
            self.assertIn(res.characters["ALICE"]["body"], LISTS["body.female"])
            self.assertIn(res.characters["ADAM"]["body"], LISTS["body.male"])
            self.assertEqual(res, resolve("__ALICE__ waves at __ADAM__.", seed=seed))

    def test_negative_and_positive_seeds_are_different_seeds(self) -> None:
        texts = {resolve("__ALICE__ waves.", seed=s).text for s in (-5, 5, -6, 6)}
        self.assertGreater(len(texts), 1)

    def test_malformed_underscores_parse_like_the_well_formed_token(self) -> None:
        clean = resolve("__ALICE__ waves. Her __HAIR__ is loose.").text
        for caption in (
            "____ALICE____ waves. Her __HAIR__ is loose.",
            "___ALICE__ waves. Her ___HAIR____ is loose.",
        ):
            with self.subTest(caption=caption):
                self.assertEqual(resolve(caption).text, clean)

    def test_things_that_are_not_tokens_are_left_alone(self) -> None:
        for caption in (
            "_ALICE_ waves.",
            "__alice__ waves.",
            "__Alice__ waves.",
            "__ALICE_ waves.",
            "_ALICE__ waves.",
            "__1ALICE__ waves.",
            "____",
            "__",
            "_____ _____",
            "__ __",
            "snake_case_word and __init_ trailing",
        ):
            with self.subTest(caption=caption):
                res = resolve(caption)
                self.assertEqual(res.text, caption)
                self.assertEqual(res.characters, {})

    def test_malformed_body_token_with_a_list(self) -> None:
        library = make_library(PLACEHOLDER_LISTS)
        res = resolve("__ALICE__ covers her ____BREASTS____.", library=library)
        value = res.characters["ALICE"]["breasts"]
        self.assertIn(f"covers her {value}.", res.text)

    def test_unknown_style_falls_back_to_ref_with_a_warning(self) -> None:
        caption = "__ALICE__ waves. __ALICE__ smiles."
        res = resolve(caption, style="bogus")
        self.assertEqual(res.text, resolve(caption, style="ref").text)
        self.assertTrue(any("bogus" in w for w in res.warnings))

    def test_an_empty_library_still_resolves_names_to_a_bare_noun(self) -> None:
        library = Library(config=CONFIG, lists={})
        res = resolve("__ALICE__ waves at __ADAM__.", library=library)
        self.assertEqual(res.text, "A woman waves at a man.")
        self.assertEqual(res.characters, {"ALICE": {}, "ADAM": {}})
        self.assertTrue(any("hair" in w for w in res.warnings))

    def test_names_and_nouns_come_from_the_config(self) -> None:
        config = CharacterConfig(
            female_names=("ZOE",),
            male_names=("MAX",),
            noun_female="lady",
            noun_male="gentleman",
        )
        library = make_library(config=config)
        res = resolve("__ZOE__ greets __MAX__.", library=library)
        self.assertIn("lady", res.text)
        self.assertIn("gentleman", res.text)
        self.assertEqual(list(res.characters), ["ZOE", "MAX"])
        # A name that is no longer configured is just a wildcard.
        res2 = resolve("__ALICE__ waves.", library=library)
        self.assertEqual(res2.text, "Alice waves.")
        self.assertTrue(res2.warnings)

    def test_generated_text_never_adds_parentheses(self) -> None:
        captions = (
            "__ADAM__ and __CLARA__ walk along a beach. His short __HAIR__ is damp "
            "and her __HAIR__ is tied back. __ADAM__ carries a surfboard.",
            "A close-up of __ALICE__'s hands as she combs her __HAIR__.",
            "__ALICE__, __BELLA__ and __CLARA__ wave. __BELLA__ smiles.",
        )
        for caption in captions:
            for style in IDENTITY_STYLES:
                for seed in range(40):
                    text = resolve(caption, seed=seed, style=style).text
                    self.assertNotIn("(", text)
                    self.assertNotIn(")", text)

    def test_many_tokens_are_handled_in_linear_time(self) -> None:
        caption = "__ALICE__ waves at the camera. " * 20000
        started = time.perf_counter()
        res = resolve(caption)
        elapsed = time.perf_counter() - started
        self.assertEqual(res.text.count("waves at the camera."), 20000)
        self.assertEqual(res.text.count("The copper-red-haired woman waves"), 19999)
        self.assertLess(elapsed, 5.0)

    def test_a_huge_underscore_run_is_not_quadratic(self) -> None:
        caption = "_" * 300000 + " __ALICE__ waves."
        started = time.perf_counter()
        res = resolve(caption)
        elapsed = time.perf_counter() - started
        self.assertTrue(res.text.endswith(" waves."))
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `venv/bin/pytest tests/test_t2i_characters.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'metascan.core.t2i_characters'`

- [ ] **Step 3: Implement** (`metascan/core/t2i_characters.py`)

```python
"""Pure caption engine for the text-to-image (t2i) feature.

A caption is a plain string that may contain ``__TOKEN__`` placeholders
(two or more underscores each side, ``[A-Z][A-Z0-9]*`` between). This module
turns them into seeded, deterministic character descriptions:

* **Character names** (``__ALICE__``, ``__ADAM__`` ...) -- the first mention
  becomes a description (``a 31-year-old West African woman with olive skin,
  ...``); later mentions become a short handle chosen by the identity style
  (``ref``: ``the copper-red-haired woman``, ``noun``: ``the woman``,
  ``name``: ``Alice``).
* **Characteristic tokens** (``__HAIR__``, ``__BREASTS__`` ...) -- stand in
  for the word itself and are replaced by the value drawn for the character
  that owns them, or by the bare lowercase word when that is safer
  (``__HAIR__brush``, ``pubic __HAIR__``, a missing list, no owner).
* **Plain wildcards** (any other token with a list) -- one seeded value,
  the same everywhere in the caption.

Every draw is ``sha256(f"{seed}|{name}|{slot}|{salt}")`` reduced modulo the
list length, so results are stable across processes and Python versions.
Parentheses are never emitted (ComfyUI would read them as weighting syntax).
No I/O and no exceptions on data problems: awkward input degrades to the
bare word or an omitted slot and adds a warning.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

IDENTITY_STYLES: Tuple[str, ...] = ("ref", "noun", "name")
DEFAULT_IDENTITY_STYLE = "ref"

_FEMALE = "female"
_MALE = "male"


@dataclass(frozen=True)
class CharacterConfig:
    """Names, nouns and slot layout; ``characters.yml`` overrides any field."""

    female_names: Tuple[str, ...] = ("ALICE", "BELLA", "CLARA", "DIANNA", "EMMA")
    male_names: Tuple[str, ...] = ("ADAM", "BOB")
    noun_female: str = "woman"
    noun_male: str = "man"
    slots: Tuple[str, ...] = (
        "age",
        "ethnicity",
        "skin",
        "eyes",
        "face",
        "hair",
        "body",
    )
    head_slots: Tuple[str, ...] = ("age", "ethnicity")
    with_slots: Tuple[str, ...] = ("skin", "eyes", "face", "hair", "body")
    token_slots: Tuple[str, ...] = ("hair", "breasts", "vagina", "penis")
    token_gender: Tuple[Tuple[str, str], ...] = (
        ("breasts", "female"),
        ("vagina", "female"),
        ("penis", "male"),
    )
    body_hair_prefixes: Tuple[str, ...] = (
        "pubic",
        "body",
        "facial",
        "chest",
        "arm",
        "leg",
        "underarm",
        "armpit",
        "stomach",
    )


@dataclass(frozen=True)
class Library:
    config: CharacterConfig
    # Keys: "<slot>", "<slot>.female", "<slot>.male" and any other lowercase
    # token name (plain wildcard lists). Values are already cleaned/validated.
    lists: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class ResolvedCaption:
    text: str
    characters: Dict[str, Dict[str, str]]  # NAME -> {slot: drawn value}
    warnings: List[str]


# ``(?<!_)`` keeps a long run of underscores linear: a token can only start
# at the beginning of a run, never inside one.
_TOKEN_RE = re.compile(r"(?<!_)_{2,}([A-Z][A-Z0-9]*)_{2,}")
_POSSESSIVE_RE = re.compile(r"['\u2019\u02bc][sS]\b")

# After a first mention, details stay inline ("... woman with olive skin")
# unless the next word would make that awkward.
_NON_INLINE_WORDS = frozenset(
    {
        "with",
        "and",
        "or",
        "in",
        "on",
        "at",
        "who",
        "whose",
        "wearing",
        "holding",
        "while",
        "as",
    }
)
_COMPOUND_JOINERS = frozenset({"and", ",", ", and"})
_COMPOUND_GAP_MAX = 12

_MALE_PRONOUNS = frozenset({"his", "him", "he"})
_FEMALE_PRONOUNS = frozenset({"her", "she", "hers"})
_PRONOUN_WINDOW = 6
_WORD_SCAN_LIMIT = 64  # a pronoun or body-hair prefix is never longer
_WORD_TRIM = ",;:\"'\u201c\u201d\u2018\u2019()[]{}\u2014\u2013-\u2026!?."

_SENTENCE_END = frozenset(".!?\u2026")
# Skipped when looking back for the end of the previous sentence.
_OPENERS = frozenset(" \t\r\f\v\"'\u201c\u2018([{\u00ab")
_CLOSERS = frozenset("\"'\u201d\u2019)]}\u00bb")

_MAX_HAIR_SALT = 64
_A_EXCEPTIONS = ("eu", "uk", "uni", "uru", "uga", "use")


def _draw(seed: int, name: str, slot: str, salt: int, size: int) -> int:
    digest = hashlib.sha256(f"{seed}|{name}|{slot}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _warn(warnings: List[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _cap1(text: str) -> str:
    return text[:1].upper() + text[1:]


def _join_and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _article(phrase: str) -> str:
    """``an`` before a vowel or a number spoken with one (8x, 11, 18)."""
    digits = re.match(r"\d+", phrase)
    if digits:
        run = digits.group()
        number = int(run) if len(run) <= 6 else -1
        return "an" if number in (8, 11, 18) or 80 <= number <= 89 else "a"
    lowered = phrase.lower()
    if not lowered or lowered.startswith(_A_EXCEPTIONS):
        return "a"
    return "an" if lowered[0] in "aeiou" else "a"


def _hair_stem(value: Optional[str]) -> Optional[str]:
    """``copper red hair`` -> ``copper-red-haired`` (None if not `` hair``)."""
    if not value or not value.lower().endswith(" hair"):
        return None
    words = value[:-5].split()
    return "-".join(words) + "-haired" if words else None


def _values(library: Library, slot: str, gender: Optional[str]) -> Tuple[str, ...]:
    """The list for a slot: a gendered list overrides the plain one."""
    if gender:
        specific = library.lists.get(f"{slot}.{gender}")
        if specific:
            return specific
    return library.lists.get(slot) or ()


def _name_genders(cfg: CharacterConfig) -> Dict[str, str]:
    """NAME -> gender, in canonical order (female names, then male names)."""
    genders: Dict[str, str] = {}
    for name in cfg.female_names:
        genders.setdefault(name, _FEMALE)
    for name in cfg.male_names:
        genders.setdefault(name, _MALE)
    return genders


# --- Text scanning helpers (all bounded, so the whole pass stays linear) ----


def _next_context(caption: str, pos: int) -> Tuple[str, str]:
    """What follows ``pos``: ('word', w) | ('punct', ch) | ('end', '') | ('other', '')."""
    size = len(caption)
    i = pos
    while i < size and caption[i].isspace():
        i += 1
    if i >= size:
        return "end", ""
    char = caption[i]
    if char.isalpha():
        j = i
        while j < size and caption[j].isalpha():
            j += 1
        return "word", caption[i:j].lower()
    if char.isdigit() or char == "_":
        return "other", ""
    return "punct", char


def _previous_word(caption: str, pos: int) -> str:
    """The whitespace-delimited chunk immediately before ``pos``."""
    i = pos
    while i > 0 and caption[i - 1].isspace():
        i -= 1
    floor = max(0, i - _WORD_SCAN_LIMIT)
    j = i
    while j > floor and not caption[j - 1].isspace():
        j -= 1
    return caption[j:i]


def _pronoun_gender(caption: str, pos: int) -> Optional[str]:
    """Gender of the NEAREST pronoun in the last six words of the sentence before ``pos``.

    Scans backwards from the token: the first ``his/him/he`` (male) or
    ``her/she/hers`` (female) found decides, so "he holds her __HAIR__" is
    female. No pronoun in the window means undecided.
    """
    end = pos
    for _ in range(_PRONOUN_WINDOW):
        i = end
        while i > 0 and caption[i - 1].isspace():
            i -= 1
        if i == 0:
            break
        floor = max(0, i - _WORD_SCAN_LIMIT)
        j = i
        while j > floor and not caption[j - 1].isspace():
            j -= 1
        word = caption[j:i]
        if i < end and word[-1] in ".!?":
            break  # that word closed the previous sentence
        base = word.lower().strip(_WORD_TRIM).split("'")[0].split("\u2019")[0]
        if base in _MALE_PRONOUNS:
            return _MALE
        if base in _FEMALE_PRONOUNS:
            return _FEMALE
        end = j
    return None


def _sentence_state(piece: str) -> Optional[bool]:
    """Does text ending with ``piece`` end a sentence? None if it cannot tell."""
    for char in reversed(piece):
        if char == "\n":
            return True
        if char in _OPENERS:
            continue
        return char in _SENTENCE_END
    return None


def _ends_sentence(text: str) -> bool:
    for char in reversed(text):
        if char in _CLOSERS or char.isspace():
            continue
        return char in _SENTENCE_END
    return True


class _Emitter:
    """Collects output pieces and knows whether the next one starts a sentence.

    The state is updated from each piece as it is added, so capitalisation
    costs O(1) per token instead of re-scanning everything written so far.
    """

    def __init__(self) -> None:
        self._parts: List[str] = []
        self._at_start = True

    @property
    def at_sentence_start(self) -> bool:
        return self._at_start

    def add(self, piece: str) -> None:
        if not piece:
            return
        self._parts.append(piece)
        state = _sentence_state(piece)
        if state is not None:
            self._at_start = state

    def text(self) -> str:
        return "".join(self._parts)


# --- Planning (pass 1) ------------------------------------------------------


@dataclass
class _Ref:
    match: "re.Match[str]"
    kind: str  # "char" | "slot" | "wild"
    name: str = ""  # character name, or the owner of a characteristic token
    slot: str = ""  # lowercase slot / wildcard name
    bare: bool = False  # write the lowercase word instead of a drawn value


@dataclass
class _Plan:
    refs: List[_Ref]
    named: Set[str]  # character names present in the caption
    defaults: Set[str]  # default owners created for a typed caption
    referenced: Dict[str, Set[str]]  # owner -> slots a token spelled out


def _default_owner(cfg: CharacterConfig, want: Optional[str]) -> Optional[str]:
    names = cfg.male_names if want == _MALE else cfg.female_names
    return names[0] if names else None


class _Planner:
    """Pass 1: decide what every token becomes and who owns each characteristic token."""

    def __init__(
        self,
        caption: str,
        matches: Sequence["re.Match[str]"],
        library: Library,
        genders: Dict[str, str],
        warnings: List[str],
    ) -> None:
        self.caption = caption
        self.matches = matches
        self.library = library
        self.cfg = library.config
        self.genders = genders
        self.warnings = warnings
        self.token_slots = set(self.cfg.token_slots)
        self.fixed_gender: Dict[str, str] = dict(self.cfg.token_gender)
        self.prefixes = {word.lower() for word in self.cfg.body_hair_prefixes}
        self.plan = _Plan(
            refs=[],
            named={m.group(1) for m in matches if m.group(1) in genders},
            defaults=set(),
            referenced={},
        )
        # "female" | "male" | "any" -> the latest character name seen so far
        self.current: Dict[str, str] = {}

    def run(self) -> _Plan:
        for match in self.matches:
            token = match.group(1)
            if token in self.genders:
                ref = self._character(match, token)
            elif token.lower() in self.token_slots:
                ref = self._characteristic(match, token)
            else:
                ref = self._wildcard(match, token)
            self.plan.refs.append(ref)
        return self.plan

    def _character(self, match: "re.Match[str]", token: str) -> _Ref:
        self.current[self.genders[token]] = token
        self.current["any"] = token
        return _Ref(match, "char", name=token)

    def _wildcard(self, match: "re.Match[str]", token: str) -> _Ref:
        slot = token.lower()
        bare = not self.library.lists.get(slot)
        if bare:
            _warn(
                self.warnings,
                f"no list for __{token}__ ({slot}.txt); wrote the plain word",
            )
        return _Ref(match, "wild", slot=slot, bare=bare)

    def _characteristic(self, match: "re.Match[str]", token: str) -> _Ref:
        slot = token.lower()
        bare = _Ref(match, "slot", slot=slot, bare=True)
        end = match.end()
        fused = self.caption[end : end + 1].isalpha()
        body_hair = (
            slot == "hair"
            and _previous_word(self.caption, match.start()).lower() in self.prefixes
        )
        if fused or body_hair:
            return bare  # a suffix or body-hair prefix: it is just the word
        want: Optional[str] = self.fixed_gender.get(slot)
        if want not in (_FEMALE, _MALE):
            want = _pronoun_gender(self.caption, match.start())
        owner, is_default = self._find_owner(want)
        if owner is None:
            _warn(
                self.warnings,
                f"no {want or 'matching'} character for __{token}__; "
                f"wrote the plain word '{slot}'",
            )
            return bare
        if not _values(self.library, slot, self.genders[owner]):
            _warn(
                self.warnings,
                f"no list for __{token}__ ({slot}.txt); wrote the plain word",
            )
            return bare
        if is_default:
            self.plan.defaults.add(owner)
            self.current[self.genders[owner]] = owner
            self.current["any"] = owner
        self.plan.referenced.setdefault(owner, set()).add(slot)
        return _Ref(match, "slot", name=owner, slot=slot)

    def _find_owner(self, want: Optional[str]) -> Tuple[Optional[str], bool]:
        """(owner, is_default): nearest named character before the token, else
        the first cast member of the right gender, else -- only for a typed
        caption with no cast at all -- a default owner who gets no intro."""
        nearest = self.current.get(want or "any")
        if nearest is not None:
            return nearest, False
        for name, gender in self.genders.items():
            if name in self.plan.named and (want is None or gender == want):
                return name, False
        if not self.plan.named:
            default = _default_owner(self.cfg, want)
            return default, default is not None
        return None, False


# --- Drawing ----------------------------------------------------------------


def _draw_distinct(
    seed: int,
    name: str,
    slot: str,
    options: Tuple[str, ...],
    taken: Mapping[str, List[str]],
) -> Tuple[str, bool]:
    """Draw a value no earlier character has; (value, clashed).

    Increments the salt until the value is unused; if chance keeps hitting
    used values, probes the list from the first draw so a free value is
    always found when one exists.
    """
    size = len(options)
    for salt in range(_MAX_HAIR_SALT):
        value = options[_draw(seed, name, slot, salt, size)]
        if value.casefold() not in taken:
            return value, False
    start = _draw(seed, name, slot, 0, size)
    for step in range(size):
        value = options[(start + step) % size]
        if value.casefold() not in taken:
            return value, False
    return options[start], True


def _draw_cast(
    seed: int,
    cast: Sequence[str],
    genders: Dict[str, str],
    library: Library,
    referenced: Mapping[str, Set[str]],
    warnings: List[str],
) -> Tuple[Dict[str, Dict[str, str]], Set[str]]:
    """Draw every character's slots; returns (characters, ambiguous names).

    ``hair`` is forced distinct across the cast (canonical order, so stable).
    A name is *ambiguous* when it shares hair with another character, and its
    ``ref`` handle then falls back to the plain noun.
    """
    cfg = library.config
    characters: Dict[str, Dict[str, str]] = {}
    hair_owners: Dict[str, List[str]] = {}
    ambiguous: Set[str] = set()
    for name in cast:
        gender = genders[name]
        extra = [
            s
            for s in cfg.token_slots
            if s in referenced.get(name, ()) and s not in cfg.slots
        ]
        drawn: Dict[str, str] = {}
        for slot in (*cfg.slots, *extra):
            options = _values(library, slot, gender)
            if not options:
                if slot in cfg.slots:
                    _warn(
                        warnings,
                        f"no '{slot}' list for {gender} characters; left out of descriptions",
                    )
                continue
            if slot == "hair":
                value, clashed = _draw_distinct(seed, name, slot, options, hair_owners)
                owners = hair_owners.setdefault(value.casefold(), [])
                owners.append(name)
                if clashed:
                    ambiguous.update(owners)
                    _warn(
                        warnings,
                        "the hair list has too few distinct values for this many "
                        "characters; some share hair and are called by their noun",
                    )
            else:
                value = options[_draw(seed, name, slot, 0, len(options))]
            drawn[slot] = value
        characters[name] = drawn
    return characters, ambiguous


# --- Rendering (pass 2) -----------------------------------------------------


class _Renderer:
    def __init__(
        self,
        caption: str,
        seed: int,
        style: str,
        library: Library,
        genders: Dict[str, str],
        plan: _Plan,
        characters: Dict[str, Dict[str, str]],
        ambiguous: Set[str],
    ) -> None:
        self.caption = caption
        self.seed = seed
        self.style = style
        self.library = library
        self.cfg = library.config
        self.genders = genders
        self.plan = plan
        self.characters = characters
        self.ambiguous = ambiguous
        self.deferred: List[Tuple[str, List[Tuple[str, str]]]] = []

    def noun(self, name: str) -> str:
        female = self.genders[name] == _FEMALE
        return self.cfg.noun_female if female else self.cfg.noun_male

    def handle_carries_hair(self, name: str) -> bool:
        return (
            self.style == "ref"
            and name not in self.ambiguous
            and _hair_stem(self.characters[name].get("hair")) is not None
        )

    def handle(self, name: str) -> str:
        """The later-mention phrase for the style."""
        if self.style == "name":
            return name.capitalize()
        if self.handle_carries_hair(name):
            stem = _hair_stem(self.characters[name].get("hair"))
            return f"the {stem} {self.noun(name)}"
        return f"the {self.noun(name)}"

    def inline_ok(
        self,
        match: "re.Match[str]",
        prev: Optional["re.Match[str]"],
        prev_is_char: bool,
    ) -> bool:
        end = match.end()
        if _POSSESSIVE_RE.match(self.caption, end):
            return False
        kind, word = _next_context(self.caption, end)
        if kind == "punct" or (kind == "word" and word in _NON_INLINE_WORDS):
            return False
        if prev is not None and prev_is_char:
            gap = match.start() - prev.end()
            between = (
                self.caption[prev.end() : match.start()]
                if gap <= _COMPOUND_GAP_MAX
                else ""
            )
            if between.strip() in _COMPOUND_JOINERS:
                return False  # compound subject: "X and Y", "X, Y"
        return True

    def first_mention(
        self,
        name: str,
        match: "re.Match[str]",
        prev: Optional["re.Match[str]"],
        prev_is_char: bool,
    ) -> str:
        drawn = self.characters[name]
        head = " ".join(
            [drawn[s] for s in self.cfg.head_slots if s in drawn] + [self.noun(name)]
        )
        head = f"{_article(head)} {head}"
        if self.style == "name":
            head += f" named {name.capitalize()}"
        spelled = self.plan.referenced.get(name, set())
        details = [
            (s, drawn[s])
            for s in self.cfg.with_slots
            if s in drawn and s not in spelled
        ]
        if not details:
            return head
        if self.inline_ok(match, prev, prev_is_char):
            return f"{head} with {_join_and([v for _, v in details])}"
        self.deferred.append((name, details))
        return head

    def trailing_sentence(
        self, name: str, details: List[Tuple[str, str]]
    ) -> Optional[str]:
        """``The <handle> has a, b and c.`` -- the handle already says the hair."""
        if self.handle_carries_hair(name):
            rest = [(s, v) for s, v in details if s != "hair"]
            if rest:
                return (
                    f"{_cap1(self.handle(name))} has {_join_and([v for _, v in rest])}."
                )
            # Hair was the only detail: say it, without the redundant handle.
            subject = f"the {self.noun(name)}"
            return f"{_cap1(subject)} has {_join_and([v for _, v in details])}."
        return f"{_cap1(self.handle(name))} has {_join_and([v for _, v in details])}."

    def slot_value(self, ref: _Ref) -> str:
        if ref.bare or ref.name not in self.characters:
            return ref.slot
        return self.characters[ref.name].get(ref.slot, ref.slot)

    def wildcard_value(self, ref: _Ref) -> str:
        values = self.library.lists.get(ref.slot)
        if ref.bare or not values:
            return ref.slot
        token = ref.match.group(1)
        return values[_draw(self.seed, token, "wildcard", 0, len(values))]

    def render(self) -> str:
        out = _Emitter()
        seen: Set[str] = set()
        last = 0
        prev: Optional["re.Match[str]"] = None
        prev_is_char = False
        for ref in self.plan.refs:
            match = ref.match
            out.add(self.caption[last : match.start()])
            last = match.end()
            if ref.kind == "char":
                if ref.name in seen:
                    piece = self.handle(ref.name)
                else:
                    seen.add(ref.name)
                    piece = self.first_mention(ref.name, match, prev, prev_is_char)
            elif ref.kind == "slot":
                piece = self.slot_value(ref)
            else:
                piece = self.wildcard_value(ref)
            out.add(_cap1(piece) if out.at_sentence_start else piece)
            prev, prev_is_char = match, ref.kind == "char"
        out.add(self.caption[last:])
        return self._append_trailing(out.text())

    def _append_trailing(self, text: str) -> str:
        sentences = [
            s
            for s in (self.trailing_sentence(n, d) for n, d in self.deferred)
            if s is not None
        ]
        if not sentences:
            return text
        body = text.rstrip()
        tail = text[len(body) :]
        if not _ends_sentence(body):
            body = body.rstrip(",;:") + "."  # a dangling clause mark becomes a stop
        return f"{body} {' '.join(sentences)}{tail}"


def resolve_caption(
    caption: str, seed: int, style: str, library: Library
) -> ResolvedCaption:
    """Resolve every token in ``caption`` for ``seed`` and identity ``style``.

    Pure and deterministic: the same inputs always give the same output. A
    caption with no tokens is returned unchanged. Never raises on data
    problems; an unknown ``style`` is treated as ``ref`` with a warning.
    """
    warnings: List[str] = []
    if style not in IDENTITY_STYLES:
        _warn(
            warnings,
            f"unknown identity style {style!r}; using {DEFAULT_IDENTITY_STYLE!r}",
        )
        style = DEFAULT_IDENTITY_STYLE
    matches = list(_TOKEN_RE.finditer(caption))
    if not matches:
        return ResolvedCaption(text=caption, characters={}, warnings=warnings)

    genders = _name_genders(library.config)
    plan = _Planner(caption, matches, library, genders, warnings).run()
    cast = [name for name in genders if name in plan.named or name in plan.defaults]
    characters, ambiguous = _draw_cast(
        seed, cast, genders, library, plan.referenced, warnings
    )
    renderer = _Renderer(
        caption, seed, style, library, genders, plan, characters, ambiguous
    )
    return ResolvedCaption(
        text=renderer.render(), characters=characters, warnings=warnings
    )
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `venv/bin/pytest tests/test_t2i_characters.py -q`

Expected: `100 passed in 0.13s` (the time varies).

- [ ] **Step 5: Quality gates**

```bash
venv/bin/black --check metascan/core/t2i_characters.py tests/test_t2i_characters.py
venv/bin/flake8 metascan/core/t2i_characters.py tests/test_t2i_characters.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/core/t2i_characters.py
```

Expected: black prints `All done!` and `2 files would be left unchanged.`; flake8 prints `0`; mypy prints `Success: no issues found in 1 source file`. (flake8 style warnings such as E203 or C901 are non-fatal in this repo and are not part of the gate.)

- [ ] **Step 6: Commit**

```bash
git add metascan/core/t2i_characters.py \
    tests/test_t2i_characters.py
git commit -m "feat(t2i): pure caption engine with seeded character descriptions" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 2: Wildcard lists, adult-only guard and starter data (`t2i_wildcards.py`)

**Files:**
- Create: `metascan/core/t2i_wildcards.py`
- Create: `data/t2i_captions/characters.yml`
- Create: `data/t2i_captions/age.txt`
- Create: `data/t2i_captions/ethnicity.txt`
- Create: `data/t2i_captions/skin.txt`
- Create: `data/t2i_captions/eyes.txt`
- Create: `data/t2i_captions/face.txt`
- Create: `data/t2i_captions/hair.txt`
- Create: `data/t2i_captions/body.female.txt`
- Create: `data/t2i_captions/body.male.txt`
- Modify: `.gitignore` (track `data/t2i_captions/`, keep its CSV ignored)
- Test: `tests/test_t2i_wildcards.py`

**Interfaces:**
- Consumes: `CharacterConfig`, `Library` from `metascan/core/t2i_characters.py` (Task 1); PyYAML (already a dependency).
- Produces:
  - `MINOR_TERMS: Tuple[str, ...] = ( "teen", "teenage", "teenager", "underage", "minor", "child", "kid", "preteen", "juvenile", "schoolgirl", "schoolboy", "loli", "shota", "under 18", "under eighteen", )` (the spec deny list)
  - `default_library_config() -> CharacterConfig` (the engine's built-in defaults)
  - `load_library(directory: Path) -> Tuple[Library, List[str]]`: reads `characters.yml` (missing file = defaults, malformed = defaults plus a warning) and every `*.txt` list in the directory; rejected lines and problems come back as `"<file>:<line>: rejected: <reason>"`-style warnings and are logged at WARNING; never raises
  - `class LibraryCache`: `__init__(self, directory: Path) -> None`; `LibraryCache.get(self) -> Tuple[Library, List[str]]` reloads by polling `(name, mtime_ns, size)` of `characters.yml` and every `*.txt` (a new or removed file counts) and never raises: on a failed reload it keeps the last good library and adds a warning; the returned warning list is a copy
  - `CONFIG_FILENAME = "characters.yml"`
  - tracked starter data under `data/t2i_captions/`: `characters.yml`, `age.txt`, `ethnicity.txt`, `skin.txt`, `eyes.txt`, `face.txt`, `hair.txt`, `body.female.txt`, `body.male.txt`; `.gitignore` tracks the directory but keeps `data/t2i_captions/*.csv` ignored

- [ ] **Step 1: Write the failing test** (`tests/test_t2i_wildcards.py`)

```python
"""Tests for the t2i wildcard loader (spec sections 3.6 and 3.7).

The guard tests write their own small list files with hand-written lines.
The last two classes load a copy of the starter files that ship in
``data/t2i_captions/`` (never the real caption CSV, never user lists).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from metascan.core import t2i_wildcards
from metascan.core.t2i_characters import CharacterConfig, Library, resolve_caption
from metascan.core.t2i_wildcards import (
    MINOR_TERMS,
    LibraryCache,
    default_library_config,
    load_library,
)

SHIPPED_DIR = Path(__file__).resolve().parents[1] / "data" / "t2i_captions"
STARTER_FILES = (
    "characters.yml",
    "age.txt",
    "ethnicity.txt",
    "skin.txt",
    "eyes.txt",
    "face.txt",
    "hair.txt",
    "body.female.txt",
    "body.male.txt",
)


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def later(self, name: str) -> None:
        """Push a file's mtime forward so a same-size rewrite is visible."""
        path = self.dir / name
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


class ConstantsTests(unittest.TestCase):
    def test_minor_terms_are_the_spec_deny_list(self) -> None:
        self.assertEqual(
            MINOR_TERMS,
            (
                "teen",
                "teenage",
                "teenager",
                "underage",
                "minor",
                "child",
                "kid",
                "preteen",
                "juvenile",
                "schoolgirl",
                "schoolboy",
                "loli",
                "shota",
                "under 18",
                "under eighteen",
            ),
        )

    def test_default_library_config_is_the_engine_default(self) -> None:
        self.assertEqual(default_library_config(), CharacterConfig())


class ListFileTests(TempDirCase):
    def test_lists_are_keyed_by_lowercase_file_stem(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        self.write("Eyes.txt", "green eyes\n")
        self.write("body.female.txt", "a slim build\n")
        self.write("body.male.txt", "a lean build\n")
        self.write("setting.txt", "a garden\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(
            sorted(library.lists),
            ["body.female", "body.male", "eyes", "hair", "setting"],
        )
        self.assertEqual(library.lists["body.female"], ("a slim build",))
        self.assertEqual(warnings, [])

    def test_blank_lines_comments_bom_crlf_and_dedupe(self) -> None:
        (self.dir / "hair.txt").write_bytes(
            b"\xef\xbb\xbf# starter list\r\n"
            b"auburn hair\r\n"
            b"\r\n"
            b"  Jet-Black    Hair  \r\n"
            b"AUBURN HAIR\r\n"
            b"   # an indented comment\r\n"
            b"jet-black hair\r\n"
            b"black hair"
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["hair"], ("auburn hair", "Jet-Black Hair", "black hair")
        )
        self.assertEqual(warnings, [])

    def test_file_order_is_preserved(self) -> None:
        self.write("eyes.txt", "zebra eyes\nalpha eyes\nmiddle eyes\n")
        library, _ = load_library(self.dir)
        self.assertEqual(
            library.lists["eyes"], ("zebra eyes", "alpha eyes", "middle eyes")
        )

    def test_bad_file_names_and_non_list_files_are_handled(self) -> None:
        self.write("Eye Color.txt", "green eyes\n")
        self.write("notes.md", "not a list\n")
        (self.dir / "folder.txt").mkdir()
        self.write("hair.txt", "auburn hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(sorted(library.lists), ["hair"])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("Eye Color.txt: "))

    def test_undecodable_file_is_reported_not_raised(self) -> None:
        (self.dir / "hair.txt").write_bytes(b"auburn hair\n\xff\xfe\x00bad\n")
        library, warnings = load_library(self.dir)
        self.assertNotIn("hair", library.lists)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("hair.txt: "))

    def test_a_file_with_no_usable_lines_is_reported(self) -> None:
        self.write("eyes.txt", "# nothing here yet\n\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["eyes"], ())
        self.assertEqual(warnings, ["eyes.txt: no usable lines"])

    def test_empty_directory_and_missing_directory(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(warnings, [])
        library, warnings = load_library(self.dir / "does-not-exist")
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(len(warnings), 1)
        self.assertIn("not found", warnings[0])


class AdultOnlyGuardTests(TempDirCase):
    def test_age_lines_with_an_integer_under_18_are_rejected(self) -> None:
        self.write(
            "age.txt",
            "27-year-old\n17-year-old\n18-year-old\n5-year-old\nin her 20s\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("27-year-old", "18-year-old", "in her 20s")
        )
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("age.txt:2: "), warnings[0])
        self.assertTrue(warnings[1].startswith("age.txt:4: "), warnings[1])
        self.assertIn("under 18", warnings[0])

    def test_any_integer_on_an_age_line_counts(self) -> None:
        self.write("age.txt", "30-year-old, 12 freckles\n30-year-old\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["age"], ("30-year-old",))
        self.assertTrue(warnings[0].startswith("age.txt:1: "))

    def test_gendered_age_files_are_guarded_too(self) -> None:
        self.write("age.female.txt", "16-year-old\n25-year-old\n")
        self.write("age.male.txt", "14-year-old\n26-year-old\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["age.female"], ("25-year-old",))
        self.assertEqual(library.lists["age.male"], ("26-year-old",))
        self.assertEqual(len(warnings), 2)

    def test_leading_zeros_and_huge_numbers_do_not_raise(self) -> None:
        self.write(
            "age.txt",
            "0018-year-old\n00017-year-old\n" + "9" * 6000 + "-year-old\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("0018-year-old", "9" * 6000 + "-year-old")
        )
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("age.txt:2: "))

    def test_spelled_out_ages_under_18_are_rejected_in_age_lists(self) -> None:
        self.write(
            "age.txt",
            "seventeen-year-old\nSixteen year old\nthirteen-year-old\n"
            "twenty-one-year-old\neighteen-year-old\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["age"], ("twenty-one-year-old", "eighteen-year-old")
        )
        self.assertEqual(len(warnings), 3)

    def test_small_numbers_are_fine_outside_the_age_lists(self) -> None:
        self.write("hair.txt", "3-strand braided hair\nauburn hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(
            library.lists["hair"], ("3-strand braided hair", "auburn hair")
        )
        self.assertEqual(warnings, [])

    def test_each_minor_term_is_rejected_in_any_list_case_insensitively(self) -> None:
        for term in MINOR_TERMS:
            for variant in (term, term.upper(), term.title()):
                with self.subTest(term=term, variant=variant):
                    self.write(
                        "hair.txt", f"auburn hair\nlong {variant} example hair\n"
                    )
                    library, warnings = load_library(self.dir)
                    self.assertEqual(library.lists["hair"], ("auburn hair",))
                    self.assertEqual(len(warnings), 1)
                    self.assertTrue(warnings[0].startswith("hair.txt:2: "), warnings[0])

    def test_minor_terms_are_rejected_in_every_kind_of_list(self) -> None:
        for name in ("eyes.txt", "body.female.txt", "setting.txt", "age.txt"):
            with self.subTest(name=name):
                self.write(name, "a fine value\nsomething teen something\n")
                library, warnings = load_library(self.dir)
                key = name[:-4]
                self.assertEqual(library.lists[key], ("a fine value",))
                self.assertTrue(
                    any(w.startswith(f"{name}:2: ") for w in warnings), warnings
                )

    def test_plural_and_irregular_forms_are_rejected(self) -> None:
        for phrase in (
            "in her teens",
            "two kids",
            "several minors",
            "the children",
            "some schoolgirls",
            "juveniles",
            "under-18 look",
        ):
            with self.subTest(phrase=phrase):
                self.write("face.txt", f"a soft face\n{phrase}\n")
                library, _ = load_library(self.dir)
                self.assertEqual(library.lists["face"], ("a soft face",))

    def test_words_that_merely_contain_a_term_are_accepted(self) -> None:
        values = (
            "canteen green eyes",
            "kidney red hair",
            "minority language",
            "childlike wonder",
            "nineteen freckles",
            "sixteen braids",
        )
        self.write("eyes.txt", "\n".join(values) + "\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["eyes"], values)
        self.assertEqual(warnings, [])

    def test_the_warning_names_the_term_but_not_the_whole_line(self) -> None:
        self.write("hair.txt", "auburn hair\nsecret words teen more secret words\n")
        _, warnings = load_library(self.dir)
        self.assertIn("teen", warnings[0])
        self.assertNotIn("secret", warnings[0])

    def test_rejections_are_logged_at_warning_level(self) -> None:
        self.write("hair.txt", "auburn hair\nteen hair\n")
        with self.assertLogs("metascan.core.t2i_wildcards", level="WARNING") as logs:
            load_library(self.dir)
        self.assertTrue(any("hair.txt:2" in line for line in logs.output), logs.output)

    def test_parentheses_are_rejected_in_every_list(self) -> None:
        self.write("hair.txt", "auburn hair\nauburn hair (dyed)\n(auburn:1.2) hair\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.lists["hair"], ("auburn hair",))
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("hair.txt:2: "))
        self.assertIn("parenthes", warnings[0])

    def test_line_numbers_count_every_physical_line_in_crlf_files(self) -> None:
        (self.dir / "hair.txt").write_bytes(b"one hair\r\n\r\n# c\r\ntwo teen hair\r\n")
        _, warnings = load_library(self.dir)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("hair.txt:4: "), warnings[0])


class CharactersYmlTests(TempDirCase):
    def test_missing_file_means_defaults_and_no_warning(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(warnings, [])

    def test_empty_or_comment_only_file_means_defaults(self) -> None:
        for text in ("", "# just a comment\n", "\n\n"):
            with self.subTest(text=text):
                self.write("characters.yml", text)
                library, warnings = load_library(self.dir)
                self.assertEqual(library.config, CharacterConfig())
                self.assertEqual(warnings, [])

    def test_malformed_yaml_means_defaults_and_one_warning(self) -> None:
        self.write("characters.yml", "names: [ALICE\nnouns: : :\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("characters.yml: "), warnings[0])

    def test_a_top_level_list_is_rejected(self) -> None:
        self.write("characters.yml", "- ALICE\n- BELLA\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config, CharacterConfig())
        self.assertEqual(len(warnings), 1)

    def test_valid_overrides_are_applied(self) -> None:
        self.write(
            "characters.yml",
            "names:\n  female: [zoe, Mia]\n  male: [Max]\n"
            "nouns: {female: lady, male: gentleman}\n"
            "slots: [age, hair, body]\n"
            "intro:\n  head: [age]\n  with: [hair, body]\n"
            "token_slots: [hair, chest]\n"
            "token_gender: {chest: male}\n"
            "body_hair_prefixes: [Pubic, Facial]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(
            library.config,
            CharacterConfig(
                female_names=("ZOE", "MIA"),
                male_names=("MAX",),
                noun_female="lady",
                noun_male="gentleman",
                slots=("age", "hair", "body"),
                head_slots=("age",),
                with_slots=("hair", "body"),
                token_slots=("hair", "chest"),
                token_gender=(("chest", "male"),),
                body_hair_prefixes=("pubic", "facial"),
            ),
        )

    def test_partial_file_keeps_defaults_for_the_rest(self) -> None:
        self.write("characters.yml", "nouns: {female: lady}\n")
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(library.config, CharacterConfig(noun_female="lady"))

    def test_bad_fields_warn_and_keep_the_other_fields(self) -> None:
        self.write(
            "characters.yml",
            "names: 5\nnouns: {female: lady, other: x}\nslots: []\n"
            "token_gender: {breasts: neither, penis: male}\nmystery: 1\n",
        )
        library, warnings = load_library(self.dir)
        config = library.config
        self.assertEqual(config.female_names, CharacterConfig().female_names)
        self.assertEqual(config.noun_female, "lady")
        self.assertEqual(config.slots, CharacterConfig().slots)
        self.assertEqual(config.token_gender, (("penis", "male"),))
        text = "\n".join(warnings)
        for needle in ("names", "nouns.other", "slots", "breasts", "mystery"):
            self.assertIn(needle, text)

    def test_names_are_uppercased_validated_and_deduplicated(self) -> None:
        self.write(
            "characters.yml",
            "names:\n  female: [alice, ALICE, 'bad name', 7, yes, Bella2]\n"
            "  male: [Alice, bob]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config.female_names, ("ALICE", "BELLA2"))
        self.assertEqual(library.config.male_names, ("BOB",))
        text = "\n".join(warnings)
        self.assertIn("bad name", text)
        self.assertIn("7", text)
        self.assertIn("ALICE", text)  # the female/male clash

    def test_unknown_intro_slots_are_dropped_with_a_warning(self) -> None:
        self.write(
            "characters.yml",
            "intro:\n  head: [age, nonsense]\n  with: [hair, skin, bogus]\n",
        )
        library, warnings = load_library(self.dir)
        self.assertEqual(library.config.head_slots, ("age",))
        self.assertEqual(library.config.with_slots, ("hair", "skin"))
        text = "\n".join(warnings)
        self.assertIn("nonsense", text)
        self.assertIn("bogus", text)

    def test_the_config_reaches_the_engine(self) -> None:
        self.write("characters.yml", "names: {female: [ZOE], male: [MAX]}\n")
        self.write("hair.txt", "auburn hair\n")
        library, _ = load_library(self.dir)
        result = resolve_caption("__ZOE__ waves.", 5, "ref", library)
        self.assertEqual(list(result.characters), ["ZOE"])
        self.assertTrue(result.text.startswith("A woman"))


class LibraryCacheTests(TempDirCase):
    def test_first_get_loads_and_unchanged_files_are_not_reloaded(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        first, _ = cache.get()
        second, _ = cache.get()
        self.assertEqual(first.lists["hair"], ("auburn hair",))
        self.assertIs(first, second)

    def test_reloads_after_a_size_change(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("hair.txt", "auburn hair\njet-black hair\n")
        library, _ = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair", "jet-black hair"))

    def test_reloads_after_a_mtime_only_change(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("hair.txt", "copper hair\n")  # same size, different text
        self.later("hair.txt")
        library, _ = cache.get()
        self.assertEqual(library.lists["hair"], ("copper hair",))

    def test_reloads_after_a_new_file_appears_and_after_one_is_removed(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        cache.get()
        self.write("eyes.txt", "green eyes\n")
        library, _ = cache.get()
        self.assertEqual(sorted(library.lists), ["eyes", "hair"])
        (self.dir / "eyes.txt").unlink()
        library, _ = cache.get()
        self.assertEqual(sorted(library.lists), ["hair"])

    def test_reloads_after_characters_yml_changes(self) -> None:
        cache = LibraryCache(self.dir)
        library, _ = cache.get()
        self.assertEqual(library.config, CharacterConfig())
        self.write("characters.yml", "nouns: {female: lady}\n")
        library, _ = cache.get()
        self.assertEqual(library.config.noun_female, "lady")

    def test_missing_directory_is_a_warning_not_an_error(self) -> None:
        cache = LibraryCache(self.dir / "later")
        library, warnings = cache.get()
        self.assertEqual(dict(library.lists), {})
        self.assertEqual(len(warnings), 1)
        (self.dir / "later").mkdir()
        (self.dir / "later" / "hair.txt").write_text("auburn hair\n", encoding="utf-8")
        library, warnings = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair",))
        self.assertEqual(warnings, [])

    def test_get_never_raises_and_keeps_the_last_good_library(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        good, _ = cache.get()
        self.write("hair.txt", "auburn hair\nblack hair\n")
        with mock.patch.object(
            t2i_wildcards, "load_library", side_effect=RuntimeError("boom")
        ):
            library, warnings = cache.get()
        self.assertIs(library, good)
        self.assertTrue(any("boom" in w for w in warnings))
        # The failed reload is retried on the next call.
        library, warnings = cache.get()
        self.assertEqual(library.lists["hair"], ("auburn hair", "black hair"))
        self.assertEqual(warnings, [])

    def test_returned_warnings_are_copies(self) -> None:
        self.write("hair.txt", "auburn hair\nteen hair\n")
        cache = LibraryCache(self.dir)
        _, warnings = cache.get()
        self.assertEqual(len(warnings), 1)
        warnings.append("scribble")
        _, again = cache.get()
        self.assertEqual(len(again), 1)

    def test_concurrent_gets_are_safe(self) -> None:
        self.write("hair.txt", "auburn hair\n")
        cache = LibraryCache(self.dir)
        results: List[Library] = []
        errors: List[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(40):
                    results.append(cache.get()[0])
            except BaseException as exc:  # pragma: no cover - would fail the test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 320)
        self.assertEqual(len({id(library) for library in results}), 1)


class ShippedStarterTests(TempDirCase):
    """The tracked starter data must load cleanly and satisfy the content rules."""

    def setUp(self) -> None:
        super().setUp()
        for name in STARTER_FILES:
            shutil.copy(SHIPPED_DIR / name, self.dir / name)

    def test_the_starter_files_exist_in_the_repo(self) -> None:
        for name in STARTER_FILES:
            self.assertTrue((SHIPPED_DIR / name).is_file(), name)

    def test_load_library_reports_no_warnings_and_every_slot_has_values(self) -> None:
        library, warnings = load_library(self.dir)
        self.assertEqual(warnings, [])
        self.assertEqual(library.config, default_library_config())
        for key in (
            "age",
            "ethnicity",
            "skin",
            "eyes",
            "face",
            "hair",
            "body.female",
            "body.male",
        ):
            with self.subTest(key=key):
                self.assertTrue(library.lists[key], key)

    def test_each_list_has_between_15_and_30_unique_values(self) -> None:
        library, _ = load_library(self.dir)
        for key, values in library.lists.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(len(values), 15)
                self.assertLessEqual(len(values), 30)
                self.assertEqual(len({v.casefold() for v in values}), len(values))

    def test_ages_are_adult_and_formatted_like_27_year_old(self) -> None:
        library, _ = load_library(self.dir)
        ages = library.lists["age"]
        for value in ages:
            match = re.fullmatch(r"(\d\d)-year-old", value)
            self.assertIsNotNone(match, value)
            assert match is not None
            self.assertGreaterEqual(int(match.group(1)), 21)
            self.assertLessEqual(int(match.group(1)), 58)
        self.assertEqual(min(int(a[:2]) for a in ages), 21)
        self.assertEqual(max(int(a[:2]) for a in ages), 58)

    def test_every_hair_value_ends_in_hair(self) -> None:
        library, _ = load_library(self.dir)
        for value in library.lists["hair"]:
            self.assertTrue(value.endswith(" hair"), value)

    def test_values_are_plain_phrases_without_punctuation_that_breaks_prose(
        self,
    ) -> None:
        library, _ = load_library(self.dir)
        for key, values in library.lists.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(value, value.strip())
                    self.assertNotRegex(value, r"[(),;:.]")
                    if key not in ("age", "ethnicity", "hair"):
                        self.assertNotIn(" with ", f" {value} ")

    def test_starter_lists_carry_no_anatomy_terms(self) -> None:
        library, _ = load_library(self.dir)
        banned = re.compile(
            r"\b(breast|breasts|vagina|vulva|penis|genital|genitals|nipple|nipples"
            r"|anus|buttock|buttocks|nude|naked)\b",
            re.IGNORECASE,
        )
        for key, values in library.lists.items():
            for value in values:
                self.assertIsNone(banned.search(value), (key, value))

    def test_resolve_caption_smoke_against_the_starters(self) -> None:
        library, _ = load_library(self.dir)
        result = resolve_caption(
            "__ALICE__ meets __ADAM__. Her long __HAIR__ is loose.", 7, "ref", library
        )
        self.assertEqual(result.warnings, [])
        self.assertIn("-year-old", result.text)
        self.assertNotIn("__", result.text)
        for name in ("ALICE", "ADAM"):
            drawn = result.characters[name]
            self.assertEqual(
                list(drawn),
                ["age", "ethnicity", "skin", "eyes", "face", "hair", "body"],
            )
        self.assertIn(result.characters["ALICE"]["hair"], library.lists["hair"])
        self.assertIn(result.characters["ALICE"]["body"], library.lists["body.female"])
        self.assertIn(result.characters["ADAM"]["body"], library.lists["body.male"])

    def test_starter_lists_resolve_without_warnings_across_many_seeds(self) -> None:
        library, _ = load_library(self.dir)
        caption = (
            "__ALICE__, __BELLA__, __CLARA__ and __ADAM__ pose. Her __HAIR__ shines."
        )
        for seed in range(200):
            for style in ("ref", "noun", "name"):
                result = resolve_caption(caption, seed, style, library)
                self.assertEqual(result.warnings, [], (seed, style))
                self.assertNotIn("(", result.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `venv/bin/pytest tests/test_t2i_wildcards.py -q`

Expected: FAIL — `ImportError: cannot import name 't2i_wildcards' from 'metascan.core'`

- [ ] **Step 3: Implement** (`metascan/core/t2i_wildcards.py`)

```python
"""Loads and validates the t2i wildcard lists and ``characters.yml``.

``data/t2i_captions/`` holds one plain-text list per slot -- ``hair.txt``,
``body.female.txt``, ``setting.txt`` and so on (one value per line, blank
lines and ``#`` comment lines ignored, duplicates dropped case-insensitively,
file order preserved) -- plus an optional ``characters.yml`` that overrides
the engine's names, nouns and slot layout. A missing ``characters.yml`` is
not an error: every key has a built-in default.

Two guards run on every list line (spec 3.7). Rejected lines are reported
as ``"<file>:<line>: <reason>"`` warnings, logged at WARNING, and left out:

* **Adult only.** In an ``age`` list any integer under 18, or a spelled-out
  age of thirteen to seventeen, is rejected. In every list a line containing
  a minor-indicating term is rejected (word-bounded, case-insensitive, with
  plural forms such as ``teens`` and ``children``).
* **No parentheses.** ComfyUI reads them as weighting syntax, so generated
  prompt text must never contain them.

Nothing here raises on bad data: a broken file becomes defaults or an empty
list plus a warning. ``LibraryCache`` reloads by polling file mtimes and
sizes (no watcher), so list edits apply to the next resolve.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import yaml  # type: ignore[import-untyped]

from metascan.core.t2i_characters import CharacterConfig, Library

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "characters.yml"

MINOR_TERMS: Tuple[str, ...] = (
    "teen",
    "teenage",
    "teenager",
    "underage",
    "minor",
    "child",
    "kid",
    "preteen",
    "juvenile",
    "schoolgirl",
    "schoolboy",
    "loli",
    "shota",
    "under 18",
    "under eighteen",
)

_GENDERS = ("female", "male")
_KNOWN_KEYS = (
    "names",
    "nouns",
    "slots",
    "intro",
    "token_slots",
    "token_gender",
    "body_hair_prefixes",
)

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
_IDENT_RE = re.compile(r"^[a-z][a-z0-9]*$")
_LIST_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.(?:female|male))?$")


def _term_pattern(term: str) -> str:
    return r"[\s-]+".join(re.escape(word) for word in term.split())


# ``\b`` on both sides keeps "canteen" and "kidney" legal; the optional
# suffix catches "teens", "kids", "minors"; "children" is irregular.
_MINOR_RE = re.compile(
    r"\b(?:"
    + "|".join(_term_pattern(term) for term in MINOR_TERMS)
    + r"|children)(?:s|es)?\b",
    re.IGNORECASE,
)
_SPELLED_MINOR_AGE_RE = re.compile(
    r"\b(?:thirteen|fourteen|fifteen|sixteen|seventeen)\b", re.IGNORECASE
)


def default_library_config() -> CharacterConfig:
    return CharacterConfig()


# --- characters.yml ---------------------------------------------------------


def _config_warning(message: str) -> str:
    return f"{CONFIG_FILENAME}: {message}"


def _text_items(value: Any, where: str, warnings: List[str]) -> Optional[List[str]]:
    """The non-empty text entries of a YAML list; None if it is not a list."""
    if not isinstance(value, list):
        warnings.append(_config_warning(f"'{where}' must be a list; using the default"))
        return None
    items: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        else:
            warnings.append(
                _config_warning(f"'{where}' has a non-text entry {item!r}; skipped")
            )
    return items


def _identifiers(
    value: Any, where: str, warnings: List[str]
) -> Optional[Tuple[str, ...]]:
    items = _text_items(value, where, warnings)
    if items is None:
        return None
    found: List[str] = []
    for item in items:
        ident = item.lower()
        if not _IDENT_RE.match(ident):
            warnings.append(
                _config_warning(
                    f"'{where}' entry {item!r} is not a lowercase word; skipped"
                )
            )
        elif ident not in found:
            found.append(ident)
    return tuple(found)


def _names(value: Any, where: str, warnings: List[str]) -> Optional[Tuple[str, ...]]:
    items = _text_items(value, where, warnings)
    if items is None:
        return None
    found: List[str] = []
    for item in items:
        name = item.upper()
        if not _NAME_RE.match(name):
            warnings.append(
                _config_warning(
                    f"'{where}' entry {item!r} must be letters and digits, "
                    "starting with a letter; skipped"
                )
            )
        elif name not in found:
            found.append(name)
    return tuple(found)


def _by_gender(value: Any, where: str, warnings: List[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        warnings.append(
            _config_warning(f"'{where}' must map female and male; using the defaults")
        )
        return {}
    for key in value:
        if key not in _GENDERS:
            warnings.append(
                _config_warning(f"'{where}.{key}' is not female or male; ignored")
            )
    return {gender: value[gender] for gender in _GENDERS if gender in value}


def _name_updates(value: Any, warnings: List[str]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for gender, raw in _by_gender(value, "names", warnings).items():
        names = _names(raw, f"names.{gender}", warnings)
        if names is not None:
            updates[f"{gender}_names"] = names
    female = updates.get("female_names", CharacterConfig().female_names)
    male = updates.get("male_names", CharacterConfig().male_names)
    clash = [name for name in male if name in female]
    for name in clash:
        warnings.append(
            _config_warning(f"{name} is listed as female and male; kept as female")
        )
    if clash:
        updates["male_names"] = tuple(name for name in male if name not in clash)
    return updates


def _noun_updates(value: Any, warnings: List[str]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for gender, raw in _by_gender(value, "nouns", warnings).items():
        if isinstance(raw, str) and raw.strip():
            updates[f"noun_{gender}"] = raw.strip()
        else:
            warnings.append(
                _config_warning(f"'nouns.{gender}' must be a word; using the default")
            )
    return updates


def _gender_map(
    value: Any, warnings: List[str]
) -> Optional[Tuple[Tuple[str, str], ...]]:
    if not isinstance(value, dict):
        warnings.append(
            _config_warning("'token_gender' must map a slot to female or male")
        )
        return None
    pairs: List[Tuple[str, str]] = []
    for slot, gender in value.items():
        if isinstance(slot, str) and gender in _GENDERS:
            pairs.append((slot.strip().lower(), str(gender)))
        else:
            warnings.append(
                _config_warning(
                    f"'token_gender.{slot}' must be female or male; skipped"
                )
            )
    return tuple(pairs)


def _intro_updates(
    value: Any,
    slots: Tuple[str, ...],
    token_slots: Tuple[str, ...],
    warnings: List[str],
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        warnings.append(_config_warning("'intro' must map head and with to lists"))
        return {}
    known = set(slots) | set(token_slots)
    updates: Dict[str, Any] = {}
    for key, field in (("head", "head_slots"), ("with", "with_slots")):
        if key not in value:
            continue
        found = _identifiers(value[key], f"intro.{key}", warnings)
        if found is None:
            continue
        for slot in found:
            if slot not in known:
                warnings.append(
                    _config_warning(
                        f"'intro.{key}' names unknown slot '{slot}'; skipped"
                    )
                )
        updates[field] = tuple(slot for slot in found if slot in known)
    return updates


def _word_list_updates(data: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """slots, token_slots, token_gender and body_hair_prefixes."""
    updates: Dict[str, Any] = {}
    for key in ("slots", "token_slots"):
        if key not in data:
            continue
        found = _identifiers(data[key], key, warnings)
        if found:
            updates[key] = found
        elif found is not None:
            warnings.append(_config_warning(f"'{key}' is empty; using the default"))
    if "token_gender" in data:
        pairs = _gender_map(data["token_gender"], warnings)
        if pairs is not None:
            updates["token_gender"] = pairs
    if "body_hair_prefixes" in data:
        items = _text_items(data["body_hair_prefixes"], "body_hair_prefixes", warnings)
        if items is not None:
            updates["body_hair_prefixes"] = tuple(
                dict.fromkeys(item.lower() for item in items)
            )
    return updates


def _config_updates(data: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    for key in data:
        if key not in _KNOWN_KEYS:
            warnings.append(_config_warning(f"unknown key '{key}' ignored"))
    updates: Dict[str, Any] = {}
    if "names" in data:
        updates.update(_name_updates(data["names"], warnings))
    if "nouns" in data:
        updates.update(_noun_updates(data["nouns"], warnings))
    updates.update(_word_list_updates(data, warnings))
    if "intro" in data:
        base = CharacterConfig()
        updates.update(
            _intro_updates(
                data["intro"],
                updates.get("slots", base.slots),
                updates.get("token_slots", base.token_slots),
                warnings,
            )
        )
    return updates


def _load_config(path: Path, warnings: List[str]) -> CharacterConfig:
    if not path.is_file():
        return default_library_config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "invalid syntax"
        warnings.append(
            _config_warning(f"malformed YAML{where}: {problem}; using defaults")
        )
        return default_library_config()
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(
            _config_warning(
                f"could not be read ({exc.__class__.__name__}); using defaults"
            )
        )
        return default_library_config()
    if data is None:
        return default_library_config()
    if not isinstance(data, dict):
        warnings.append(_config_warning("must be a mapping; using defaults"))
        return default_library_config()
    return replace(default_library_config(), **_config_updates(data, warnings))


# --- list files -------------------------------------------------------------


def _reject_reason(slot: str, value: str) -> Optional[str]:
    if "(" in value or ")" in value:
        return "contains parentheses (ComfyUI reads them as weights)"
    if slot == "age":
        for run in re.findall(r"\d+", value):
            digits = run.lstrip("0") or "0"
            if len(digits) <= 2 and int(digits) < 18:
                return "age under 18"
        if _SPELLED_MINOR_AGE_RE.search(value):
            return "age under 18"
    found = _MINOR_RE.search(value)
    if found:
        return f'contains minor term "{found.group(0).lower()}"'
    return None


def _read_list(path: Path, slot: str, warnings: List[str]) -> Optional[Tuple[str, ...]]:
    """The cleaned values of one list file; None if it could not be read."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(
            f"{path.name}: could not be read ({exc.__class__.__name__}); ignored"
        )
        return None
    values: List[str] = []
    seen: Set[str] = set()
    for lineno, raw in enumerate(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1
    ):
        line = " ".join(raw.split())
        if not line or line.startswith("#"):
            continue
        reason = _reject_reason(slot, line)
        if reason is not None:
            message = f"{path.name}:{lineno}: rejected: {reason}"
            logger.warning("t2i list line %s", message)
            warnings.append(message)
            continue
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            values.append(line)
    return tuple(values)


def load_library(directory: Path) -> Tuple[Library, List[str]]:
    """Read ``characters.yml`` and every ``*.txt`` list in ``directory``.

    Returns ``(library, warnings)``. Never raises on bad data.
    """
    directory = Path(directory)
    warnings: List[str] = []
    if not directory.is_dir():
        warnings.append(f"list directory not found: {directory}")
        return Library(default_library_config(), {}), warnings
    config = _load_config(directory / CONFIG_FILENAME, warnings)
    lists: Dict[str, Tuple[str, ...]] = {}
    for path in sorted(directory.glob("*.txt")):
        if not path.is_file():
            continue
        key = path.stem.lower()
        if not _LIST_KEY_RE.match(key):
            warnings.append(
                f"{path.name}: list names are lowercase letters and digits with an "
                "optional .female or .male suffix; ignored"
            )
            continue
        if key in lists:
            warnings.append(f"{path.name}: duplicate list '{key}'; ignored")
            continue
        values = _read_list(path, key.split(".")[0], warnings)
        if values is None:
            continue
        if not values:
            warnings.append(f"{path.name}: no usable lines")
        lists[key] = values
    return Library(config, lists), warnings


# --- cache ------------------------------------------------------------------


class LibraryCache:
    """Serves the library, reloading when a list or ``characters.yml`` changes.

    Each ``get()`` stats about a dozen files (name, mtime_ns, size) and
    reloads only when that signature changed -- a new or removed file counts.
    ``get()`` never raises: if a reload fails it keeps serving the last good
    library and adds a warning.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()
        self._signature: Optional[Tuple[Any, ...]] = None
        self._library = Library(default_library_config(), {})
        self._warnings: List[str] = []

    def _signature_now(self) -> Tuple[Any, ...]:
        if not self._directory.is_dir():
            return ("missing",)
        entries = [self._directory / CONFIG_FILENAME]
        entries.extend(sorted(self._directory.glob("*.txt")))
        signature: List[Tuple[str, int, int]] = []
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue  # e.g. no characters.yml
            signature.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def get(self) -> Tuple[Library, List[str]]:
        with self._lock:
            try:
                signature = self._signature_now()
                if signature != self._signature:
                    self._library, self._warnings = load_library(self._directory)
                    self._signature = signature
            except Exception as exc:  # never raise into a request or a batch
                logger.exception("t2i list reload failed; keeping the previous lists")
                return self._library, self._warnings + [
                    f"could not reload the caption lists: {exc}"
                ]
            return self._library, list(self._warnings)
```

- [ ] **Step 4: Add the starter data and the `.gitignore` rule**

Before the edit, the new directory is swallowed by `data/*`:

```bash
git check-ignore -v data/t2i_captions/age.txt
```

Expected: `.gitignore:10:data/*	data/t2i_captions/age.txt`

Create the starter files (adult-only, neutral physical descriptors, no anatomy terms; hair values end in ` hair`; the body-part lists are the user's to supply):

`data/t2i_captions/characters.yml`:

```yaml
# Character engine settings for the T2I dialog (metascan/core/t2i_characters.py).
#
# Every key is optional: a missing key -- or a missing file -- uses the built-in
# default shown here. Edits apply to the next caption that is resolved.
#
# names:               caption tokens that mean a person, e.g. __ALICE__
# nouns:               the word used for each gender in descriptions
# slots:               characteristics drawn per person, in this order; each one
#                      reads a list file named <slot>.txt, or <slot>.female.txt /
#                      <slot>.male.txt to override it for one gender
# intro:               how a first mention is assembled: "head" slots go in front
#                      of the noun, "with" slots follow the word "with"
# token_slots:         tokens that stand in for the word itself, e.g. __HAIR__
# token_gender:        slots that belong to one gender (the others follow pronouns)
# body_hair_prefixes:  a word before __HAIR__ that makes it body hair, not head hair

names:
  female: [ALICE, BELLA, CLARA, DIANNA, EMMA]
  male: [ADAM, BOB]
nouns: {female: woman, male: man}
slots: [age, ethnicity, skin, eyes, face, hair, body]
intro:
  head: [age, ethnicity]
  with: [skin, eyes, face, hair, body]
token_slots: [hair, breasts, vagina, penis]
token_gender: {breasts: female, vagina: female, penis: male}
body_hair_prefixes: [pubic, body, facial, chest, arm, leg, underarm, armpit, stomach]
```

`data/t2i_captions/age.txt`:

```text
# One value per line. Adults only: any number under 18 is rejected at load time.
21-year-old
23-year-old
24-year-old
26-year-old
27-year-old
29-year-old
31-year-old
33-year-old
34-year-old
36-year-old
38-year-old
40-year-old
42-year-old
44-year-old
46-year-old
48-year-old
50-year-old
52-year-old
55-year-old
58-year-old
```

`data/t2i_captions/ethnicity.txt`:

```text
# Read as: a <age> <ethnicity> woman / man. Keep entries neutral for both genders.
Nordic
Latin American
East Asian
West African
South Asian
Mediterranean
Southeast Asian
Middle Eastern
Central Asian
Eastern European
Western European
Irish
Scottish
Italian
Greek
Persian
Korean
Japanese
Chinese
Indian
Brazilian
Mexican
Caribbean
Ethiopian
Nigerian
Moroccan
Turkish
Polish
Vietnamese
Pacific Islander
```

`data/t2i_captions/skin.txt`:

```text
# Read after the word "with": with <value>, ...
fair skin
pale skin
light skin
porcelain skin
rosy skin
freckled skin
olive skin
warm tan skin
golden skin
sun-kissed skin
bronze skin
light brown skin
medium brown skin
warm brown skin
brown skin
deep brown skin
dark brown skin
smooth skin
weathered skin
tanned skin
```

`data/t2i_captions/eyes.txt`:

```text
# Read after the word "with": with <value>, ...
green eyes
hazel eyes
brown eyes
dark brown eyes
warm brown eyes
honey-brown eyes
amber eyes
blue eyes
light blue eyes
pale blue eyes
steel-blue eyes
grey eyes
gray-green eyes
sea-green eyes
bright green eyes
soft hazel eyes
almond-shaped brown eyes
deep-set brown eyes
wide-set green eyes
dark eyes
```

`data/t2i_captions/face.txt`:

```text
# Read after the word "with": with <value>, ...
an oval face
a round face
a soft round face
a heart-shaped face
a diamond-shaped face
a square face
a long face
a narrow face
a broad face
a delicate face
a gentle face
a friendly face
a strong jaw
a defined jawline
a pointed chin
high cheekbones
sharp cheekbones
full cheeks
angular features
soft features
```

`data/t2i_captions/hair.txt`:

```text
# Colour only -- the caption supplies length and style ("her long wavy __HAIR__").
# Every value must end in " hair": later mentions become "the copper-red-haired woman".
auburn hair
jet-black hair
black hair
raven black hair
dark brown hair
espresso brown hair
chestnut brown hair
light brown hair
caramel brown hair
mahogany hair
copper red hair
dark red hair
ginger hair
strawberry blonde hair
honey blonde hair
golden blonde hair
sandy blonde hair
ash blonde hair
platinum blonde hair
silver hair
grey hair
salt-and-pepper hair
```

`data/t2i_captions/body.female.txt`:

```text
# Read after the word "with": with <value>. body.male.txt is the male list.
a slim build
a slender frame
a petite frame
a willowy frame
a toned build
an athletic build
a compact athletic build
a lean build
a strong build
a sturdy build
a solid build
an average build
a soft curvy build
a curvy build
a full-figured build
a tall slender build
a muscular build
a graceful slim build
```

`data/t2i_captions/body.male.txt`:

```text
# Read after the word "with": with <value>. body.female.txt is the female list.
an athletic build
a lean build
a wiry build
a lanky build
a slim build
a trim build
an average build
a solid build
a sturdy build
a stocky build
a broad-shouldered build
a muscular build
a strong build
a powerful build
a rugged build
a tall lean build
a heavyset build
a compact build
```

`.gitignore`: apply with `git apply` (the diff below is the exact change, produced from the verified tree):

```diff
diff --git a/.gitignore b/.gitignore
index 4532dd2..2c7813d 100644
--- a/.gitignore
+++ b/.gitignore
@@ -14,6 +14,10 @@ data/*
 !data/workflows/
 !data/templates/
 !data/i2v_templates/
+# T2I: the starter lists and characters.yml are tracked; the caption CSV
+# (tens of MB, user-supplied) is not.
+!data/t2i_captions/
+data/t2i_captions/*.csv
 
 .claude/
 
```

Apply with `git apply --whitespace=nowarn <<'PATCH'` ... `PATCH`, pasting the diff above between the two markers.

Prove the rule: the starters are tracked, the CSV stays ignored (`git check-ignore` prints nothing and exits 1 for a path that is NOT ignored):

```bash
git check-ignore -v data/t2i_captions/age.txt; echo "exit=$?"
git check-ignore -v data/t2i_captions/characters.yml; echo "exit=$?"
git check-ignore -v data/t2i_captions/t2i_captions.csv; echo "exit=$?"
git check-ignore -v data/t2i_captions/notes.csv; echo "exit=$?"
```

Expected output:

```text
exit=1
exit=1
.gitignore:20:data/t2i_captions/*.csv	data/t2i_captions/t2i_captions.csv
exit=0
.gitignore:20:data/t2i_captions/*.csv	data/t2i_captions/notes.csv
exit=0
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/pytest tests/test_t2i_wildcards.py -q`

Expected: `51 passed in 0.12s` (the time varies). The last class loads a copy of the shipped starter files, so this also proves they load with no warnings.

- [ ] **Step 6: Quality gates**

```bash
venv/bin/black --check metascan/core/t2i_wildcards.py tests/test_t2i_wildcards.py
venv/bin/flake8 metascan/core/t2i_wildcards.py tests/test_t2i_wildcards.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/core/t2i_wildcards.py
```

Expected: black prints `All done!` and `2 files would be left unchanged.`; flake8 prints `0`; mypy prints `Success: no issues found in 1 source file`. (flake8 style warnings such as E203 or C901 are non-fatal in this repo and are not part of the gate.)

- [ ] **Step 7: Commit**

```bash
git add .gitignore \
    metascan/core/t2i_wildcards.py \
    tests/test_t2i_wildcards.py \
    data/t2i_captions/characters.yml \
    data/t2i_captions/age.txt \
    data/t2i_captions/ethnicity.txt \
    data/t2i_captions/skin.txt \
    data/t2i_captions/eyes.txt \
    data/t2i_captions/face.txt \
    data/t2i_captions/hair.txt \
    data/t2i_captions/body.female.txt \
    data/t2i_captions/body.male.txt
git commit -m "feat(t2i): wildcard list loader, adult-only guard and starter lists" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 3: Caption store (`t2i_captions.py`)

**Files:**
- Create: `metascan/core/t2i_captions.py`
- Test: `tests/test_t2i_captions.py`

**Interfaces:**
- Consumes: numpy (already a dependency); standard library only otherwise.
- Produces:
  - `class CaptionFilterError(ValueError)`: unknown filter key, bad value, a filter on a column the file lacks, or zero matches from `picker()`
  - `@dataclass(frozen=True) class CaptionRow`: `id: int`, `caption: str`, `aspect_ratio: str`, `nudity: Optional[str]`, `artistic_quality: Optional[float]`, `erotic_score: Optional[float]`, `pornographic_score: Optional[float]`, `males: Optional[int]`, `females: Optional[int]`, `clothing: Tuple[str, ...]`; `CaptionRow.to_dict(self) -> Dict[str, Any]` (clothing as a list; JSON-ready)
  - `class CaptionStore` (thread-safe, lazy, reloads when `(mtime_ns, size)` changes):
    - `__init__(self, csv_path: Path) -> None`
    - `CaptionStore.available(self) -> bool`, `CaptionStore.error(self) -> Optional[str]`, `CaptionStore.total(self) -> int`
    - `CaptionStore.meta(self) -> Dict[str, Any]`: `{"total": N, "columns": [...]}` in contract shape 8.2 (keys in order: `nudity`, `artistic_quality`, `erotic_score`, `pornographic_score`, `males`, `females`, `aspect_ratios`, `clothing`; only columns present in the CSV appear; choice/tag options are `{value, count}`, nudity in none/partial/full order, the rest by count descending)
    - `CaptionStore.count(self, flt: Optional[Mapping[str, Any]]) -> int` (0 when the file is unavailable)
    - `CaptionStore.get(self, row_id: int) -> CaptionRow` (lazy read by byte offset; `KeyError` if absent)
    - `CaptionStore.picker(self, flt: Optional[Mapping[str, Any]], rng: random.Random) -> 'CaptionPicker'` (raises `CaptionFilterError` on a bad filter, zero matches, or an unavailable file)
  - `class CaptionPicker`: attribute `pool_size: int`; `CaptionPicker.next(self) -> CaptionRow` picks without repeats, reshuffles when exhausted (never repeating the last row first), and re-applies the filter to the new file if the CSV changes mid-run
  - Filter keys: `nudity`, `artistic_quality`, `erotic_score`, `pornographic_score`, `males`, `females`, `aspect_ratios`, `clothing_any`, `clothing_none`; an empty list means no constraint; ranges are `{"min": x, "max": y}` and inclusive
  - Required CSV columns `Caption`, `Aspect Ratio` (matched ignoring case and spacing); rows with a blank caption are not indexed; a file with a CSV syntax error, no captions or a missing required column is `available() == False` with a message from `error()`

- [ ] **Step 1: Write the failing test** (`tests/test_t2i_captions.py`)

```python
"""Tests for the t2i caption store (spec section 4).

Every CSV here is written by the test itself from hand-written rows with
known properties -- never the real caption file. The Review Focus 1 tests
(``RecordRobustnessTests``) pin quote-balanced records: an embedded newline,
CRLF line endings, a UTF-8 BOM and trailing blank lines all index correctly.
"""

from __future__ import annotations

import csv
import io
import json
import os
import random
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from metascan.core.t2i_captions import (
    CaptionFilterError,
    CaptionPicker,
    CaptionRow,
    CaptionStore,
)

COLUMNS = [
    "Caption",
    "Aspect Ratio",
    "Nudity",
    "Artistic Quality",
    "Erotic Score",
    "Pornographic Score",
    "Males",
    "Females",
    "Clothing",
]

# id: caption | aspect | nudity | quality | erotic | porn | males | females | clothing
ROWS: List[List[str]] = [
    # 0
    [
        "__ALICE__ reads a book by a window.",
        "3:2",
        "none",
        "0.90",
        "0.05",
        "0.00",
        "0",
        "1",
        "['dress', 'scarf']",
    ],
    # 1
    [
        "__ALICE__ and __ADAM__ walk on a beach.",
        "2:3",
        "none",
        "0.80",
        "0.10",
        "0.00",
        "1",
        "1",
        "['shirt', 'shorts', 'dress']",
    ],
    # 2
    [
        "__BELLA__ poses in a corset.",
        "2:3",
        "partial",
        "0.70",
        "0.55",
        "0.10",
        "0",
        "1",
        "['corset']",
    ],
    # 3
    ["__CLARA__ sleeps.", "1:1", "full", "0.60", "0.90", "0.80", "0", "1", "[]"],
    # 4
    [
        "__ALICE__, __BELLA__ and __CLARA__ dance.",
        "16:9",
        "partial",
        "0.95",
        "0.40",
        "0.05",
        "0",
        "3",
        "['dress']",
    ],
    # 5
    [
        "__ADAM__ and __BOB__ argue.",
        "4:3",
        "none",
        "0.50",
        "0.00",
        "0.00",
        "2",
        "0",
        "['tie', 'suit']",
    ],
    # 6
    ["A red bicycle.", "3:2", "none", "0.30", "0.00", "0.00", "0", "0", "[]"],
    # 7
    [
        "__ALICE__ wears a tie and a corset.",
        "3:2",
        "partial",
        "0.85",
        "0.60",
        "0.20",
        "0",
        "1",
        "['tie', 'corset']",
    ],
]

ALL_IDS = list(range(len(ROWS)))


def encode_csv(
    rows: Sequence[Sequence[str]],
    header: Sequence[str] = COLUMNS,
    eol: str = "\n",
    bom: bool = False,
    tail: Optional[str] = None,
) -> bytes:
    """A CSV file's bytes, quoted the way Python's csv writer quotes it.

    ``tail`` replaces the final line terminator (``""`` drops it; extra
    terminators add trailing blank lines).
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=eol)
    writer.writerow(header)
    writer.writerows(rows)
    text = buffer.getvalue()
    if tail is not None:
        text = text[: -len(eol)] + tail
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "captions.csv"

    def write(self, data: bytes) -> None:
        self.path.write_bytes(data)

    def store(
        self, rows: Optional[Sequence[Sequence[str]]] = None, **kwargs: Any
    ) -> CaptionStore:
        self.write(encode_csv(ROWS if rows is None else rows, **kwargs))
        return CaptionStore(self.path)

    def later(self) -> None:
        stat = self.path.stat()
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    @staticmethod
    def ids(store: CaptionStore, flt: Optional[Mapping[str, Any]]) -> List[int]:
        """Every id a filter matches, found by exhausting one picker cycle."""
        picker = store.picker(flt, random.Random(0))
        return sorted(picker.next().id for _ in range(picker.pool_size))


class BasicsTests(StoreCase):
    def test_index_reports_total_and_availability(self) -> None:
        store = self.store()
        self.assertTrue(store.available())
        self.assertIsNone(store.error())
        self.assertEqual(store.total(), len(ROWS))

    def test_get_reads_each_row_lazily_and_exactly(self) -> None:
        store = self.store()
        for row_id, fields in enumerate(ROWS):
            with self.subTest(row_id=row_id):
                row = store.get(row_id)
                self.assertIsInstance(row, CaptionRow)
                self.assertEqual(row.id, row_id)
                self.assertEqual(row.caption, fields[0])
                self.assertEqual(row.aspect_ratio, fields[1])
                self.assertEqual(row.nudity, fields[2])
                self.assertEqual(row.artistic_quality, float(fields[3]))
                self.assertEqual(row.erotic_score, float(fields[4]))
                self.assertEqual(row.pornographic_score, float(fields[5]))
                self.assertEqual(row.males, int(fields[6]))
                self.assertEqual(row.females, int(fields[7]))

    def test_get_raises_keyerror_for_an_absent_id(self) -> None:
        store = self.store()
        for bad in (-1, len(ROWS), 10**9):
            with self.subTest(bad=bad):
                with self.assertRaises(KeyError):
                    store.get(bad)

    def test_row_to_dict_is_json_ready_with_clothing_as_a_list(self) -> None:
        row = self.store().get(1)
        data = row.to_dict()
        self.assertEqual(
            list(data),
            [
                "id",
                "caption",
                "aspect_ratio",
                "nudity",
                "artistic_quality",
                "erotic_score",
                "pornographic_score",
                "males",
                "females",
                "clothing",
            ],
        )
        self.assertEqual(data["clothing"], ["shirt", "shorts", "dress"])
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_rows_are_frozen(self) -> None:
        row = self.store().get(0)
        with self.assertRaises(Exception):
            row.caption = "changed"  # type: ignore[misc]

    def test_header_names_ignore_case_and_spacing(self) -> None:
        header = ["caption", " ASPECT   ratio ", "NUDITY", "males"]
        rows = [["Hello there.", "3:2", "none", "2"], ["Second.", "1:1", "full", "0"]]
        store = self.store(rows, header=header)
        self.assertTrue(store.available())
        self.assertEqual(store.get(0).males, 2)
        self.assertEqual(self.ids(store, {"nudity": ["full"]}), [1])


class FilterTests(StoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.s = self.store()

    def test_no_filter_matches_everything(self) -> None:
        for flt in (None, {}, {"nudity": None}):
            with self.subTest(flt=flt):
                self.assertEqual(self.s.count(flt), len(ROWS))
                self.assertEqual(self.ids(self.s, flt), ALL_IDS)

    def test_nudity_is_any_of_and_case_insensitive(self) -> None:
        self.assertEqual(self.ids(self.s, {"nudity": ["none"]}), [0, 1, 5, 6])
        self.assertEqual(
            self.ids(self.s, {"nudity": ["partial", "full"]}), [2, 3, 4, 7]
        )
        self.assertEqual(self.ids(self.s, {"nudity": ["NONE"]}), [0, 1, 5, 6])
        self.assertEqual(self.s.count({"nudity": ["nonexistent"]}), 0)

    def test_artistic_quality_range_is_inclusive(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"artistic_quality": {"min": 0.8, "max": 0.95}}),
            [0, 1, 4, 7],
        )
        self.assertEqual(self.ids(self.s, {"artistic_quality": {"min": 0.9}}), [0, 4])
        self.assertEqual(self.ids(self.s, {"artistic_quality": {"max": 0.5}}), [5, 6])

    def test_erotic_score_range(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"erotic_score": {"min": 0.5, "max": 1.0}}), [2, 3, 7]
        )
        # 0.05 is stored as float32; the boundary must still be inclusive.
        self.assertEqual(self.ids(self.s, {"erotic_score": {"max": 0.05}}), [0, 5, 6])

    def test_pornographic_score_range(self) -> None:
        self.assertEqual(
            self.ids(self.s, {"pornographic_score": {"min": 0.1}}), [2, 3, 7]
        )
        self.assertEqual(
            self.ids(self.s, {"pornographic_score": {"max": 0.0}}), [0, 1, 5, 6]
        )

    def test_males_and_females_int_ranges(self) -> None:
        self.assertEqual(self.ids(self.s, {"males": {"min": 1}}), [1, 5])
        self.assertEqual(self.ids(self.s, {"males": {"max": 0}}), [0, 2, 3, 4, 6, 7])
        self.assertEqual(self.ids(self.s, {"males": {"min": 2, "max": 2}}), [5])
        self.assertEqual(self.ids(self.s, {"females": {"min": 3}}), [4])
        self.assertEqual(
            self.ids(self.s, {"females": {"min": 1, "max": 1}}), [0, 1, 2, 3, 7]
        )
        self.assertEqual(self.ids(self.s, {"females": {"max": 0}}), [5, 6])

    def test_aspect_ratios_is_any_of(self) -> None:
        self.assertEqual(self.ids(self.s, {"aspect_ratios": ["3:2"]}), [0, 6, 7])
        self.assertEqual(
            self.ids(self.s, {"aspect_ratios": ["2:3", "16:9"]}), [1, 2, 4]
        )

    def test_clothing_any_and_none(self) -> None:
        self.assertEqual(self.ids(self.s, {"clothing_any": ["corset"]}), [2, 7])
        self.assertEqual(
            self.ids(self.s, {"clothing_any": ["dress", "suit"]}), [0, 1, 4, 5]
        )
        self.assertEqual(
            self.ids(self.s, {"clothing_none": ["tie"]}), [0, 1, 2, 3, 4, 6]
        )
        self.assertEqual(
            self.ids(self.s, {"clothing_any": ["corset"], "clothing_none": ["tie"]}),
            [2],
        )
        self.assertEqual(self.s.count({"clothing_any": ["nothing-like-this"]}), 0)

    def test_keys_combine_with_and(self) -> None:
        flt = {
            "nudity": ["partial"],
            "clothing_any": ["corset"],
            "females": {"min": 1},
            "erotic_score": {"min": 0.58},
        }
        self.assertEqual(self.ids(self.s, flt), [7])

    def test_empty_lists_are_no_constraint(self) -> None:
        flt = {
            "nudity": [],
            "aspect_ratios": [],
            "clothing_any": [],
            "clothing_none": [],
        }
        self.assertEqual(self.s.count(flt), len(ROWS))

    def test_count_matches_the_ids_and_is_a_plain_int(self) -> None:
        count = self.s.count({"nudity": ["partial"]})
        self.assertEqual(count, 3)
        self.assertIs(type(count), int)

    def test_unknown_filter_key_is_an_error_everywhere(self) -> None:
        for call in (
            lambda: self.s.count({"mood": ["happy"]}),
            lambda: self.s.picker({"mood": ["happy"]}, random.Random(0)),
        ):
            with self.assertRaises(CaptionFilterError) as ctx:
                call()
            self.assertIn("mood", str(ctx.exception))

    def test_bad_filter_values_are_errors(self) -> None:
        bad = [
            ["none"],  # not a mapping
            {"nudity": "none"},
            {"nudity": [1]},
            {"aspect_ratios": "3:2"},
            {"clothing_any": [None]},
            {"artistic_quality": 0.5},
            {"artistic_quality": {"min": "high"}},
            {"artistic_quality": {"min": True}},
            {"artistic_quality": {"min": float("nan")}},
            {"artistic_quality": {"lo": 0.1}},
            {"artistic_quality": {"min": 0.9, "max": 0.1}},
            {"males": {"min": 1.5}},
            {"females": [1, 2]},
        ]
        for flt in bad:
            with self.subTest(flt=flt):
                with self.assertRaises(CaptionFilterError):
                    self.s.count(flt)  # type: ignore[arg-type]

    def test_null_bounds_are_ignored(self) -> None:
        flt = {"artistic_quality": {"min": None, "max": 0.5}}
        self.assertEqual(self.ids(self.s, flt), [5, 6])

    def test_out_of_range_bounds_do_not_overflow(self) -> None:
        self.assertEqual(self.s.count({"males": {"min": -5, "max": 300}}), len(ROWS))
        self.assertEqual(
            self.s.count({"artistic_quality": {"min": -1e40, "max": 1e40}}), len(ROWS)
        )
        self.assertEqual(self.s.count({"males": {"min": 300}}), 0)

    def test_zero_matches_is_an_error_for_the_picker_but_not_for_count(self) -> None:
        flt = {"nudity": ["full"], "males": {"min": 2}}
        self.assertEqual(self.s.count(flt), 0)
        with self.assertRaises(CaptionFilterError):
            self.s.picker(flt, random.Random(0))


class PickerTests(StoreCase):
    def test_pool_is_shuffled_without_replacement_then_reshuffled(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(7))
        self.assertIsInstance(picker, CaptionPicker)
        self.assertEqual(picker.pool_size, len(ROWS))
        first = [picker.next().id for _ in range(len(ROWS))]
        second = [picker.next().id for _ in range(len(ROWS))]
        self.assertEqual(sorted(first), ALL_IDS)
        self.assertEqual(sorted(second), ALL_IDS)

    def test_picker_honours_the_filter_pool(self) -> None:
        store = self.store()
        picker = store.picker({"nudity": ["none"]}, random.Random(1))
        self.assertEqual(picker.pool_size, 4)
        seen = {picker.next().id for _ in range(4)}
        self.assertEqual(seen, {0, 1, 5, 6})

    def test_same_rng_seed_gives_the_same_sequence(self) -> None:
        store = self.store()
        a = store.picker(None, random.Random(99))
        b = store.picker(None, random.Random(99))
        self.assertEqual(
            [a.next().id for _ in range(20)], [b.next().id for _ in range(20)]
        )

    def test_every_row_can_come_first(self) -> None:
        store = self.store()
        firsts = {store.picker(None, random.Random(s)).next().id for s in range(400)}
        self.assertEqual(firsts, set(ALL_IDS))

    def test_no_immediate_repeat_across_the_reshuffle(self) -> None:
        store = self.store()
        for seed in range(60):
            picker = store.picker({"aspect_ratios": ["2:3"]}, random.Random(seed))
            self.assertEqual(picker.pool_size, 2)
            ids = [picker.next().id for _ in range(30)]
            for before, after in zip(ids, ids[1:]):
                self.assertNotEqual(before, after, (seed, ids))

    def test_a_pool_of_one_repeats_that_row(self) -> None:
        store = self.store()
        picker = store.picker({"aspect_ratios": ["16:9"]}, random.Random(3))
        self.assertEqual(picker.pool_size, 1)
        self.assertEqual({picker.next().id for _ in range(5)}, {4})

    def test_picker_returns_full_rows(self) -> None:
        store = self.store()
        picker = store.picker({"aspect_ratios": ["16:9"]}, random.Random(3))
        row = picker.next()
        self.assertEqual(row.caption, ROWS[4][0])
        self.assertEqual(row.clothing, ("dress",))

    def test_picker_survives_the_file_changing_mid_run(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(5))
        picker.next()
        new_rows = [
            [f"NEW caption {i}.", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"]
            for i in range(4)
        ]
        self.write(encode_csv(new_rows))
        self.later()
        picked = [picker.next() for _ in range(8)]
        self.assertTrue(all(row.caption.startswith("NEW caption") for row in picked))
        self.assertEqual(picker.pool_size, 4)

    def test_picker_errors_cleanly_when_the_file_disappears(self) -> None:
        store = self.store()
        picker = store.picker(None, random.Random(5))
        self.path.unlink()
        with self.assertRaises(CaptionFilterError):
            picker.next()


class ReloadAndAvailabilityTests(StoreCase):
    def test_reload_after_a_size_change(self) -> None:
        store = self.store(ROWS[:3])
        self.assertEqual(store.total(), 3)
        self.write(encode_csv(ROWS[:5]))
        self.assertEqual(store.total(), 5)
        self.assertEqual(store.get(4).caption, ROWS[4][0])

    def test_reload_after_a_mtime_only_change(self) -> None:
        store = self.store(ROWS[:2])
        self.assertEqual(store.get(0).caption, ROWS[0][0])
        swapped = [list(ROWS[0]), list(ROWS[1])]
        swapped[0][0] = swapped[0][0].upper()  # same byte length
        self.write(encode_csv(swapped))
        self.later()
        self.assertEqual(store.get(0).caption, ROWS[0][0].upper())

    def test_an_unchanged_file_keeps_its_index(self) -> None:
        store = self.store()
        store.total()
        index = store._index  # noqa: SLF001 - identity check on the cached index
        store.total()
        store.count(None)
        self.assertIs(store._index, index)

    def test_missing_file_is_unavailable_and_appears_later(self) -> None:
        store = CaptionStore(self.path)
        self.assertFalse(store.available())
        self.assertIn("not found", store.error() or "")
        self.assertEqual(store.total(), 0)
        self.assertEqual(store.count(None), 0)
        self.assertEqual(store.meta(), {"total": 0, "columns": []})
        with self.assertRaises(CaptionFilterError):
            store.picker(None, random.Random(0))
        with self.assertRaises(KeyError):
            store.get(0)
        self.write(encode_csv(ROWS))
        self.assertTrue(store.available())
        self.assertEqual(store.total(), len(ROWS))

    def test_missing_required_column_makes_the_store_unavailable(self) -> None:
        for header, missing in (
            (["Caption", "Nudity"], "Aspect Ratio"),
            (["Aspect Ratio", "Nudity"], "Caption"),
        ):
            with self.subTest(header=header):
                store = self.store([["x", "y"]], header=header)
                self.assertFalse(store.available())
                self.assertIn(missing, store.error() or "")
                self.assertEqual(store.total(), 0)
                with self.assertRaises(CaptionFilterError):
                    store.picker(None, random.Random(0))

    def test_header_only_and_empty_files_are_unavailable(self) -> None:
        for data in (encode_csv([]), b"", b"\n\n\n"):
            with self.subTest(data=data):
                self.write(data)
                store = CaptionStore(self.path)
                self.assertFalse(store.available())
                self.assertTrue(store.error())

    def test_invalid_csv_is_unavailable_with_a_line_number(self) -> None:
        self.write(b"Caption,Aspect Ratio\nfine,3:2\nbroken,1:1\rmore,4:3\n")
        store = CaptionStore(self.path)
        self.assertFalse(store.available())
        self.assertIn("line", store.error() or "")

    def test_missing_optional_columns_drop_their_filters(self) -> None:
        header = ["Caption", "Aspect Ratio", "Males"]
        store = self.store([["A.", "3:2", "1"], ["B.", "1:1", "0"]], header=header)
        keys = [column["key"] for column in store.meta()["columns"]]
        self.assertEqual(keys, ["males", "aspect_ratios"])
        with self.assertRaises(CaptionFilterError) as ctx:
            store.count({"nudity": ["none"]})
        self.assertIn("Nudity", str(ctx.exception))
        for key, value in (
            ("erotic_score", {"min": 0}),
            ("artistic_quality", {"min": 0}),
            ("pornographic_score", {"min": 0}),
            ("females", {"min": 0}),
            ("clothing_any", ["x"]),
            ("clothing_none", ["x"]),
        ):
            with self.subTest(key=key):
                with self.assertRaises(CaptionFilterError):
                    store.count({key: value})
        self.assertEqual(store.count({"males": {"min": 1}}), 1)

    def test_concurrent_counts_are_consistent(self) -> None:
        store = self.store()
        results: List[int] = []
        errors: List[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(30):
                    results.append(store.count({"nudity": ["partial"]}))
            except BaseException as exc:  # pragma: no cover - would fail the test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {3})


class MetaTests(StoreCase):
    def test_meta_lists_every_present_column_in_order(self) -> None:
        meta = self.store().meta()
        self.assertEqual(meta["total"], len(ROWS))
        self.assertEqual(
            [(c["key"], c["label"], c["type"]) for c in meta["columns"]],
            [
                ("nudity", "Nudity", "choice"),
                ("artistic_quality", "Artistic Quality", "range"),
                ("erotic_score", "Erotic Score", "range"),
                ("pornographic_score", "Pornographic Score", "range"),
                ("males", "Males", "int_range"),
                ("females", "Females", "int_range"),
                ("aspect_ratios", "Aspect Ratio", "choice"),
                ("clothing", "Clothing", "tags"),
            ],
        )

    def test_choice_options_carry_counts(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            columns["nudity"]["options"],
            [
                {"value": "none", "count": 4},
                {"value": "partial", "count": 3},
                {"value": "full", "count": 1},
            ],
        )
        self.assertEqual(
            [(o["value"], o["count"]) for o in columns["aspect_ratios"]["options"]],
            [("3:2", 3), ("2:3", 2), ("16:9", 1), ("1:1", 1), ("4:3", 1)],
        )

    def test_clothing_options_are_by_count_descending(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            [(o["value"], o["count"]) for o in columns["clothing"]["options"]],
            [
                ("dress", 3),
                ("corset", 2),
                ("tie", 2),
                ("scarf", 1),
                ("shirt", 1),
                ("shorts", 1),
                ("suit", 1),
            ],
        )

    def test_numeric_columns_report_data_ranges(self) -> None:
        columns = {c["key"]: c for c in self.store().meta()["columns"]}
        self.assertEqual(
            columns["artistic_quality"],
            {
                "key": "artistic_quality",
                "label": "Artistic Quality",
                "type": "range",
                "min": 0.3,
                "max": 0.95,
                "step": 0.05,
            },
        )
        self.assertEqual(
            (columns["erotic_score"]["min"], columns["erotic_score"]["max"]), (0.0, 0.9)
        )
        self.assertEqual(
            columns["males"],
            {"key": "males", "label": "Males", "type": "int_range", "min": 0, "max": 2},
        )
        self.assertEqual((columns["females"]["min"], columns["females"]["max"]), (0, 3))

    def test_meta_is_plain_json(self) -> None:
        meta = self.store().meta()
        self.assertEqual(json.loads(json.dumps(meta)), meta)


class ClothingParsingTests(StoreCase):
    def test_list_strings_parse_and_malformed_ones_are_empty(self) -> None:
        cases = [
            ("['a', 'b']", ("a", "b")),
            ('["x y", "z"]', ("x y", "z")),
            ("[]", ()),
            ("", ()),
            ("['unterminated", ()),
            ("not a list at all", ()),
            ("'single'", ()),
            ("[1, 'ok', None, '  ', 'ok']", ("ok",)),
            ("['dup', 'dup', 'other']", ("dup", "other")),
            ("{'a': 1}", ()),
            ("[" * 60 + "'x'" + "]" * 60, ()),
        ]
        rows = [
            [f"caption {i}", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", raw]
            for i, (raw, _) in enumerate(cases)
        ]
        store = self.store(rows)
        self.assertEqual(store.total(), len(cases))
        for i, (raw, expected) in enumerate(cases):
            with self.subTest(raw=raw):
                self.assertEqual(store.get(i).clothing, expected)

    def test_malformed_rows_stay_in_the_pool_and_match_clothing_none(self) -> None:
        rows = [
            ["ok", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "['tie']"],
            ["bad", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "['tie'"],
        ]
        store = self.store(rows)
        self.assertEqual(self.ids(store, {"clothing_none": ["tie"]}), [1])
        self.assertEqual(self.ids(store, {"clothing_any": ["tie"]}), [0])


class MessyValueTests(StoreCase):
    def test_short_rows_and_junk_numbers_become_none(self) -> None:
        lines = [
            ",".join(COLUMNS),
            "Only caption and ratio,3:2",
            "Junk,1:1,none,abc,inf,nan,x,-4,[]",
            "Big,2:3,full,0.5,0.5,0.5,999,7,[]",
        ]
        self.write(("\n".join(lines) + "\n").encode("utf-8"))
        store = CaptionStore(self.path)
        self.assertEqual(store.total(), 3)
        short = store.get(0)
        self.assertEqual(short.aspect_ratio, "3:2")
        self.assertIsNone(short.nudity)
        self.assertIsNone(short.artistic_quality)
        self.assertIsNone(short.males)
        self.assertEqual(short.clothing, ())
        junk = store.get(1)
        self.assertEqual(
            (
                junk.artistic_quality,
                junk.erotic_score,
                junk.pornographic_score,
                junk.males,
                junk.females,
            ),
            (None, None, None, None, None),
        )
        big = store.get(2)
        self.assertEqual((big.males, big.females), (None, 7))
        # Rows without a value never match a range that constrains that column.
        self.assertEqual(self.ids(store, {"artistic_quality": {"min": 0.0}}), [2])
        self.assertEqual(store.count({"males": {"min": 0}}), 0)

    def test_blank_and_whitespace_captions_are_not_indexed(self) -> None:
        rows = [
            ["First.", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["   ", "3:2", "none", "0.5", "0.1", "0.0", "0", "1", "[]"],
            ["Second.", "1:1", "full", "0.5", "0.1", "0.0", "0", "1", "[]"],
        ]
        store = self.store(rows)
        self.assertEqual(store.total(), 2)
        self.assertEqual(
            [store.get(i).caption for i in range(2)], ["First.", "Second."]
        )


class RecordRobustnessTests(StoreCase):
    """Review focus 1: records are quote-balanced, not physical lines."""

    NEWLINE_CAPTION = (
        'Line one\nLine two, with a comma and "quotes" and a trailing newline\n'
    )
    CRLF_CAPTION = "First line\r\nSecond line\r\nThird, with a comma"
    UNICODE_CAPTION = "Caf\u00e9 \u4e2d\u6587 \U0001f600 after multibyte text"

    def rows_around(self, caption: str) -> List[List[str]]:
        middle = [
            caption,
            "3:2",
            "partial",
            "0.42",
            "0.25",
            "0.75",
            "2",
            "1",
            "['coat']",
        ]
        return [list(ROWS[0]), list(ROWS[1]), middle, list(ROWS[2]), list(ROWS[3])]

    def check_round_trip(self, store: CaptionStore, rows: List[List[str]]) -> None:
        self.assertTrue(store.available(), store.error())
        self.assertEqual(store.total(), len(rows))
        for i, fields in enumerate(rows):
            with self.subTest(row=i):
                row = store.get(i)
                self.assertEqual(row.caption, fields[0])
                self.assertEqual(row.aspect_ratio, fields[1])
                self.assertEqual(row.nudity, fields[2])
                self.assertEqual(row.males, int(fields[6]))
                self.assertEqual(row.females, int(fields[7]))

    def test_an_embedded_newline_does_not_split_the_record(self) -> None:
        rows = self.rows_around(self.NEWLINE_CAPTION)
        self.check_round_trip(self.store(rows), rows)
        store = self.store(rows)
        # Filters see the rows after the multi-line record at the right ids.
        self.assertEqual(self.ids(store, {"nudity": ["partial"]}), [2, 3])
        self.assertEqual(self.ids(store, {"clothing_any": ["coat"]}), [2])

    def test_crlf_line_endings(self) -> None:
        rows = [list(r) for r in ROWS]
        store = self.store(rows, eol="\r\n")
        self.check_round_trip(store, rows)
        self.assertFalse(
            any(store.get(i).caption.endswith("\r") for i in range(len(rows)))
        )
        self.assertEqual(self.ids(store, {"aspect_ratios": ["3:2"]}), [0, 6, 7])

    def test_crlf_file_with_a_crlf_newline_inside_a_quoted_caption(self) -> None:
        rows = self.rows_around(self.CRLF_CAPTION)
        self.check_round_trip(self.store(rows, eol="\r\n"), rows)

    def test_utf8_bom_is_ignored(self) -> None:
        rows = [list(r) for r in ROWS]
        store = self.store(rows, bom=True)
        self.check_round_trip(store, rows)
        self.assertTrue(store.get(0).caption.startswith("__ALICE__"))
        self.assertEqual(self.ids(store, {"nudity": ["full"]}), [3])

    def test_trailing_blank_lines_add_no_rows(self) -> None:
        rows = [list(r) for r in ROWS]
        for tail in ("\n\n", "\n\n\n\n", "\r\n\r\n"):
            with self.subTest(tail=tail):
                eol = "\r\n" if "\r" in tail else "\n"
                self.check_round_trip(self.store(rows, eol=eol, tail=eol + tail), rows)

    def test_the_last_record_needs_no_final_newline(self) -> None:
        rows = [list(r) for r in ROWS]
        self.check_round_trip(self.store(rows, tail=""), rows)

    def test_blank_lines_between_records_are_skipped(self) -> None:
        rows = [list(r) for r in ROWS[:4]]
        data = encode_csv(rows).decode("utf-8").split("\n")
        data.insert(2, "")
        data.insert(4, "")
        self.write("\n".join(data).encode("utf-8"))
        self.check_round_trip(CaptionStore(self.path), rows)

    def test_multibyte_text_does_not_disturb_byte_offsets(self) -> None:
        rows = self.rows_around(self.UNICODE_CAPTION)
        self.check_round_trip(self.store(rows), rows)

    def test_doubled_quotes_and_commas_round_trip(self) -> None:
        caption = 'She said "hello", then "goodbye", then left, quietly.'
        rows = self.rows_around(caption)
        self.check_round_trip(self.store(rows), rows)

    def test_every_hazard_at_once(self) -> None:
        rows = [
            list(ROWS[0]),
            self.rows_around(self.NEWLINE_CAPTION)[2],
            list(ROWS[1]),
            self.rows_around(self.CRLF_CAPTION)[2],
            self.rows_around(self.UNICODE_CAPTION)[2],
            list(ROWS[2]),
        ]
        store = self.store(rows, eol="\r\n", bom=True, tail="\r\n\r\n\r\n")
        self.check_round_trip(store, rows)
        self.assertEqual(self.ids(store, {"clothing_any": ["coat"]}), [1, 3, 4])
        picker = store.picker(None, random.Random(4))
        self.assertEqual(
            sorted(picker.next().id for _ in range(picker.pool_size)), list(range(6))
        )

    def test_a_stray_quote_in_an_unquoted_field_is_literal(self) -> None:
        lines = [
            ",".join(COLUMNS),
            'A 5" tall statue,3:2,none,0.5,0.1,0.0,0,0,[]',
            "Next row,1:1,full,0.5,0.1,0.0,0,0,[]",
        ]
        self.write(("\n".join(lines) + "\n").encode("utf-8"))
        store = CaptionStore(self.path)
        self.assertEqual(store.total(), 2)
        self.assertEqual(store.get(0).caption, 'A 5" tall statue')
        self.assertEqual(store.get(1).caption, "Next row")

    def test_a_large_file_indexes_every_record_and_reads_them_back(self) -> None:
        rows: List[List[str]] = []
        for i in range(30000):
            caption = f"Generated caption {i}."
            if i % 997 == 0:
                caption = f"Generated caption {i},\nwith a second line."
            rows.append(
                [
                    caption,
                    "3:2" if i % 2 else "2:3",
                    "none",
                    "0.5",
                    "0.1",
                    "0.0",
                    str(i % 3),
                    "1",
                    "['x']",
                ]
            )
        store = self.store(rows, eol="\r\n")
        self.assertEqual(store.total(), 30000)
        for i in (0, 1, 996, 997, 998, 1994, 15000, 29999):
            self.assertEqual(store.get(i).caption, rows[i][0], i)
        self.assertEqual(store.count({"aspect_ratios": ["3:2"]}), 15000)
        self.assertEqual(store.count({"males": {"min": 2}}), 10000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `venv/bin/pytest tests/test_t2i_captions.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'metascan.core.t2i_captions'`

- [ ] **Step 3: Implement** (`metascan/core/t2i_captions.py`)

```python
"""Caption index for the t2i Random mode (spec section 4).

``CaptionStore`` indexes a large caption CSV once and answers filter, count
and pick queries from compact numpy arrays. Caption text is never held in
memory: the index keeps each record's byte range and ``get`` reads the record
back on demand.

* **Records are quote-balanced, not physical lines.** The file is fed to the
  ``csv`` module one line at a time while the byte position is tracked, so a
  caption containing a quoted newline (LF or CRLF) is still one record, a
  stray quote inside an unquoted field stays literal, and blank lines between
  or after records are skipped. A UTF-8 BOM and CRLF endings are handled.
* **Columns** are matched by name, ignoring case and spacing. ``Caption`` and
  ``Aspect Ratio`` are required; each optional column (``Nudity``, three
  scores, ``Males``, ``Females``, ``Clothing``) enables its filter and shows
  up in ``meta()`` only when present. Clothing is a Python-literal list
  string, parsed with ``ast.literal_eval`` and treated as empty on failure.
* **Reload** is by ``(mtime_ns, size)``, checked on every call.
* A missing or unusable file does not raise from ``available``, ``error``,
  ``total``, ``meta`` or ``count``: the store reports ``available() == False``
  and a message from ``error()``. ``get`` then raises ``KeyError`` and
  ``picker`` raises ``CaptionFilterError`` (as does ``CaptionPicker.next`` if
  the file disappears mid-run).
"""

from __future__ import annotations

import ast
import csv
import io
import logging
import math
import random
import threading
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Tuple,
    TypeVar,
)

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

RANGE_STEP = 0.05
_MISSING_COUNT = 255  # marker for a Males/Females cell that is blank or unusable
_MAX_COUNT = 254
_CLOTHING_CACHE_MAX = 4096
_NUDITY_ORDER = ("none", "partial", "full")

_SCORE_COLUMNS: Dict[str, Tuple[str, str]] = {
    "artistic_quality": ("artistic quality", "Artistic Quality"),
    "erotic_score": ("erotic score", "Erotic Score"),
    "pornographic_score": ("pornographic score", "Pornographic Score"),
}
_COUNT_COLUMNS: Dict[str, Tuple[str, str]] = {
    "males": ("males", "Males"),
    "females": ("females", "Females"),
}
_FILTER_KEYS = (
    "nudity",
    "artistic_quality",
    "erotic_score",
    "pornographic_score",
    "males",
    "females",
    "aspect_ratios",
    "clothing_any",
    "clothing_none",
)

BoolArray = npt.NDArray[np.bool_]
_T = TypeVar("_T", bound=np.generic)


class CaptionFilterError(ValueError):
    """A filter names an unknown key, has a bad value, or matches nothing."""


class _CaptionFileError(Exception):
    """The caption file cannot be used; the message is shown to the user."""


@dataclass(frozen=True)
class CaptionRow:
    id: int
    caption: str
    aspect_ratio: str
    nudity: Optional[str]
    artistic_quality: Optional[float]
    erotic_score: Optional[float]
    pornographic_score: Optional[float]
    males: Optional[int]
    females: Optional[int]
    clothing: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "caption": self.caption,
            "aspect_ratio": self.aspect_ratio,
            "nudity": self.nudity,
            "artistic_quality": self.artistic_quality,
            "erotic_score": self.erotic_score,
            "pornographic_score": self.pornographic_score,
            "males": self.males,
            "females": self.females,
            "clothing": list(self.clothing),
        }


# --- Cell parsing -----------------------------------------------------------


def _cell(row: List[str], position: Optional[int]) -> str:
    if position is None or position >= len(row):
        return ""
    return row[position]


def _norm_header(name: str) -> str:
    return " ".join(name.replace("\ufeff", "").split()).lower()


def _parse_score(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        return math.nan
    return value if math.isfinite(value) else math.nan


def _parse_count(text: str) -> int:
    """A whole number 0..254, or the missing marker."""
    try:
        value = float(text.strip())
    except ValueError:
        return _MISSING_COUNT
    if not math.isfinite(value) or value != int(value):
        return _MISSING_COUNT
    return int(value) if 0 <= value <= _MAX_COUNT else _MISSING_COUNT


def _parse_clothing(text: str) -> Tuple[str, ...]:
    text = text.strip()
    if not text or text == "[]":
        return ()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return ()
    if isinstance(value, (set, frozenset)):
        value = sorted(item for item in value if isinstance(item, str))
    if not isinstance(value, (list, tuple)):
        return ()
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return tuple(dict.fromkeys(items))


def _as_array(values: "array[Any]", dtype: "type[_T]") -> "npt.NDArray[_T]":
    if not len(values):
        return np.zeros(0, dtype=dtype)
    return np.frombuffer(values, dtype=dtype).copy()


# --- The index --------------------------------------------------------------


@dataclass
class _Index:
    path: Path
    signature: Tuple[int, int]
    columns: Dict[str, int]  # normalised header -> position
    starts: "npt.NDArray[np.int64]"  # byte range of each record
    ends: "npt.NDArray[np.int64]"
    aspect_values: List[str]
    aspect_codes: "npt.NDArray[np.uint16]"
    nudity_values: Optional[List[str]]
    nudity_codes: Optional["npt.NDArray[np.int8]"]  # -1 = blank
    scores: Dict[str, "npt.NDArray[np.float32]"]  # NaN = blank or unusable
    counts: Dict[str, "npt.NDArray[np.uint8]"]  # 255 = blank or unusable
    clothing: Optional[Dict[str, "npt.NDArray[np.intc]"]]  # item -> row ids

    @property
    def total(self) -> int:
        return int(self.starts.shape[0])


class _Accumulator:
    """Collects one compact array per column while the file is scanned."""

    def __init__(self, columns: Dict[str, int]) -> None:
        self.columns = columns
        self.caption_at = columns["caption"]
        self.aspect_at = columns["aspect ratio"]
        self.nudity_at = columns.get("nudity")
        self.clothing_at = columns.get("clothing")
        self.starts: "array[int]" = array("q")
        self.ends: "array[int]" = array("q")
        self.aspect_codes: "array[int]" = array("H")
        self.aspect_index: Dict[str, int] = {}
        self.nudity_codes: "array[int]" = array("b")
        self.nudity_index: Dict[str, int] = {}
        self.scores: Dict[str, "array[float]"] = {
            key: array("f")
            for key, (name, _) in _SCORE_COLUMNS.items()
            if name in columns
        }
        self.counts: Dict[str, "array[int]"] = {
            key: array("B")
            for key, (name, _) in _COUNT_COLUMNS.items()
            if name in columns
        }
        self.clothing_rows: Dict[str, "array[int]"] = {}
        self._clothing_cache: Dict[str, Tuple[str, ...]] = {}
        self.total = 0

    def add(self, row: List[str], start: int, end: int) -> None:
        if not _cell(row, self.caption_at).strip():
            return  # nothing to render from a blank caption
        row_id = self.total
        self.total += 1
        self.starts.append(start)
        self.ends.append(end)
        self.aspect_codes.append(
            self._code(
                self.aspect_index,
                _cell(row, self.aspect_at).strip(),
                65535,
                "aspect ratios",
            )
        )
        if self.nudity_at is not None:
            value = _cell(row, self.nudity_at).strip().lower()
            self.nudity_codes.append(
                self._code(self.nudity_index, value, 126, "Nudity values")
                if value
                else -1
            )
        for key, values in self.scores.items():
            values.append(
                _parse_score(_cell(row, self.columns[_SCORE_COLUMNS[key][0]]))
            )
        for key, counts in self.counts.items():
            counts.append(
                _parse_count(_cell(row, self.columns[_COUNT_COLUMNS[key][0]]))
            )
        if self.clothing_at is not None:
            for item in self._clothing(_cell(row, self.clothing_at)):
                self.clothing_rows.setdefault(item, array("i")).append(row_id)

    @staticmethod
    def _code(table: Dict[str, int], value: str, limit: int, what: str) -> int:
        code = table.get(value)
        if code is None:
            code = len(table)
            if code > limit:
                raise _CaptionFileError(f"caption file has too many distinct {what}")
            table[value] = code
        return code

    def _clothing(self, text: str) -> Tuple[str, ...]:
        cached = self._clothing_cache.get(text)
        if cached is None:
            cached = _parse_clothing(text)
            if len(self._clothing_cache) < _CLOTHING_CACHE_MAX:
                self._clothing_cache[text] = cached
        return cached

    def finish(self, path: Path, signature: Tuple[int, int]) -> _Index:
        nudity_values = None
        if self.nudity_at is not None:
            nudity_values = sorted(self.nudity_index, key=self.nudity_index.__getitem__)
        return _Index(
            path=path,
            signature=signature,
            columns=self.columns,
            starts=_as_array(self.starts, np.int64),
            ends=_as_array(self.ends, np.int64),
            aspect_values=sorted(self.aspect_index, key=self.aspect_index.__getitem__),
            aspect_codes=_as_array(self.aspect_codes, np.uint16),
            nudity_values=nudity_values,
            nudity_codes=(
                _as_array(self.nudity_codes, np.int8)
                if self.nudity_at is not None
                else None
            ),
            scores={
                key: _as_array(values, np.float32)
                for key, values in self.scores.items()
            },
            counts={
                key: _as_array(values, np.uint8) for key, values in self.counts.items()
            },
            clothing=(
                {
                    item: _as_array(rows, np.intc)
                    for item, rows in self.clothing_rows.items()
                }
                if self.clothing_at is not None
                else None
            ),
        )


def _header_columns(row: List[str]) -> Dict[str, int]:
    columns: Dict[str, int] = {}
    for position, name in enumerate(row):
        columns.setdefault(_norm_header(name), position)
    missing = [
        label
        for key, label in (("caption", "Caption"), ("aspect ratio", "Aspect Ratio"))
        if key not in columns
    ]
    if missing:
        raise _CaptionFileError(
            "caption file is missing required column(s): " + ", ".join(missing)
        )
    return columns


def _scan_records(handle: BinaryIO) -> _Accumulator:
    """Feed the file to ``csv`` line by line, tracking each record's byte range."""
    position = 0

    def lines() -> Iterator[str]:
        nonlocal position
        for raw in handle:  # splits on b"\n" only; CRLF and lone CR stay put
            position += len(raw)
            yield raw.decode("utf-8", errors="replace")

    reader = csv.reader(lines())
    accumulator: Optional[_Accumulator] = None
    while True:
        start = position
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise _CaptionFileError(
                f"caption file is not valid CSV near line {reader.line_num}: {exc}"
            ) from exc
        if not row:
            continue  # a blank line
        if accumulator is None:
            accumulator = _Accumulator(_header_columns(row))
            continue
        accumulator.add(row, start, position)
    if accumulator is None:
        raise _CaptionFileError("caption file is empty")
    return accumulator


def _build_index(path: Path, signature: Tuple[int, int]) -> _Index:
    with path.open("rb") as handle:
        accumulator = _scan_records(handle)
    if accumulator.total == 0:
        raise _CaptionFileError("caption file has no captions")
    return accumulator.finish(path, signature)


# --- Reading one record -----------------------------------------------------


def _read_row(index: _Index, row_id: int) -> CaptionRow:
    start = int(index.starts[row_id])
    end = int(index.ends[row_id])
    try:
        with index.path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(end - start)
        text = raw.decode("utf-8", errors="replace")
        fields = next(csv.reader(io.StringIO(text, newline="\n")))
    except (OSError, StopIteration, csv.Error) as exc:
        raise KeyError(row_id) from exc

    def cell(name: str) -> str:
        return _cell(fields, index.columns.get(name))

    def score(key: str) -> Optional[float]:
        if key not in index.scores:
            return None
        value = _parse_score(cell(_SCORE_COLUMNS[key][0]))
        return None if math.isnan(value) else value

    def count(key: str) -> Optional[int]:
        if key not in index.counts:
            return None
        value = _parse_count(cell(_COUNT_COLUMNS[key][0]))
        return None if value == _MISSING_COUNT else value

    return CaptionRow(
        id=row_id,
        caption=cell("caption"),
        aspect_ratio=cell("aspect ratio").strip(),
        nudity=(cell("nudity").strip().lower() or None),
        artistic_quality=score("artistic_quality"),
        erotic_score=score("erotic_score"),
        pornographic_score=score("pornographic_score"),
        males=count("males"),
        females=count("females"),
        clothing=_parse_clothing(cell("clothing")),
    )


# --- Filters ----------------------------------------------------------------


def _normalise_filter(flt: Any) -> Dict[str, Any]:
    if flt is None:
        return {}
    if not isinstance(flt, Mapping):
        raise CaptionFilterError("the filter must be an object")
    for key in flt:
        if key not in _FILTER_KEYS:
            raise CaptionFilterError(f"unknown filter key '{key}'")
    return {key: value for key, value in flt.items() if value is not None}


def _string_list(key: str, value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(v, str) for v in value
    ):
        raise CaptionFilterError(f"filter '{key}' must be a list of strings")
    return list(value)


def _bound(key: str, field: str, value: Any, whole: bool) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a finite number")
    if whole and number != int(number):
        raise CaptionFilterError(f"filter '{key}.{field}' must be a whole number")
    return number


def _bounds(
    key: str, spec: Any, whole: bool
) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(spec, Mapping):
        raise CaptionFilterError(
            f"filter '{key}' must be an object with min and/or max"
        )
    for field in spec:
        if field not in ("min", "max"):
            raise CaptionFilterError(f"filter '{key}' has unknown field '{field}'")
    low = _bound(key, "min", spec.get("min"), whole)
    high = _bound(key, "max", spec.get("max"), whole)
    if low is not None and high is not None and low > high:
        raise CaptionFilterError(f"filter '{key}': min is greater than max")
    return low, high


def _needs_column(key: str, label: str) -> CaptionFilterError:
    return CaptionFilterError(
        f"filter '{key}' is not available: the caption file has no '{label}' column"
    )


def _apply_choice(
    mask: BoolArray,
    codes: "npt.NDArray[Any]",
    values: List[str],
    wanted: List[str],
    fold: bool,
) -> None:
    lookup = {value: code for code, value in enumerate(values)}
    hits = [
        lookup[w.strip().lower() if fold else w.strip()]
        for w in wanted
        if (w.strip().lower() if fold else w.strip()) in lookup
    ]
    mask &= np.isin(codes, np.array(hits, dtype=np.int64))


def _apply_scores(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key, (_, label) in _SCORE_COLUMNS.items():
        if key not in flt:
            continue
        if key not in index.scores:
            raise _needs_column(key, label)
        low, high = _bounds(key, flt[key], whole=False)
        values = index.scores[key]
        with np.errstate(invalid="ignore"):
            if low is not None:
                mask &= values >= np.float32(max(min(low, 3e38), -3e38))
            if high is not None:
                mask &= values <= np.float32(max(min(high, 3e38), -3e38))


def _apply_counts(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key, (_, label) in _COUNT_COLUMNS.items():
        if key not in flt:
            continue
        if key not in index.counts:
            raise _needs_column(key, label)
        low, high = _bounds(key, flt[key], whole=True)
        values = index.counts[key].astype(np.int16)
        mask &= values != _MISSING_COUNT
        if low is not None:
            mask &= values >= int(max(min(low, 1000), -1))
        if high is not None:
            mask &= values <= int(max(min(high, 1000), -1))


def _apply_clothing(mask: BoolArray, index: _Index, flt: Mapping[str, Any]) -> None:
    for key in ("clothing_any", "clothing_none"):
        if key not in flt:
            continue
        if index.clothing is None:
            raise _needs_column(key, "Clothing")
        wanted = _string_list(key, flt[key])
        if not wanted:
            continue
        if key == "clothing_any":
            hit = np.zeros(index.total, dtype=np.bool_)
            for item in wanted:
                rows = index.clothing.get(item)
                if rows is not None:
                    hit[rows] = True
            mask &= hit
        else:
            for item in wanted:
                rows = index.clothing.get(item)
                if rows is not None:
                    mask[rows] = False


def _match_mask(index: _Index, flt: Any) -> BoolArray:
    """A boolean mask of the rows a filter keeps. Raises CaptionFilterError."""
    spec = _normalise_filter(flt)
    mask: BoolArray = np.ones(index.total, dtype=np.bool_)
    if "nudity" in spec:
        if index.nudity_codes is None or index.nudity_values is None:
            raise _needs_column("nudity", "Nudity")
        wanted = _string_list("nudity", spec["nudity"])
        if wanted:
            _apply_choice(
                mask, index.nudity_codes, index.nudity_values, wanted, fold=True
            )
    if "aspect_ratios" in spec:
        wanted = _string_list("aspect_ratios", spec["aspect_ratios"])
        if wanted:
            _apply_choice(
                mask, index.aspect_codes, index.aspect_values, wanted, fold=False
            )
    _apply_scores(mask, index, spec)
    _apply_counts(mask, index, spec)
    _apply_clothing(mask, index, spec)
    return mask


# --- meta() -----------------------------------------------------------------


def _options(pairs: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    ordered = sorted(pairs, key=lambda pair: (-pair[1], pair[0]))
    return [{"value": value, "count": count} for value, count in ordered]


def _code_counts(codes: "npt.NDArray[Any]", values: List[str]) -> List[Tuple[str, int]]:
    seen = codes[codes >= 0].astype(np.intp)
    tally = np.bincount(seen, minlength=len(values))
    return [(value, int(tally[code])) for code, value in enumerate(values) if value]


def _nudity_options(pairs: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    known = [p for name in _NUDITY_ORDER for p in pairs if p[0] == name]
    rest = [p for p in pairs if p[0] not in _NUDITY_ORDER]
    return [{"value": v, "count": c} for v, c in known] + _options(rest)


def _range_meta(
    key: str, label: str, values: "npt.NDArray[np.float32]"
) -> Dict[str, Any]:
    finite = values[~np.isnan(values)]
    low, high = (0.0, 1.0)
    if finite.size:
        low, high = round(float(finite.min()), 4), round(float(finite.max()), 4)
    return {
        "key": key,
        "label": label,
        "type": "range",
        "min": low,
        "max": high,
        "step": RANGE_STEP,
    }


def _int_range_meta(
    key: str, label: str, values: "npt.NDArray[np.uint8]"
) -> Dict[str, Any]:
    present = values[values != _MISSING_COUNT]
    low, high = (int(present.min()), int(present.max())) if present.size else (0, 0)
    return {"key": key, "label": label, "type": "int_range", "min": low, "max": high}


def _meta_columns(index: _Index) -> List[Dict[str, Any]]:
    columns: List[Dict[str, Any]] = []
    if index.nudity_codes is not None and index.nudity_values is not None:
        pairs = _code_counts(index.nudity_codes, index.nudity_values)
        columns.append(
            {
                "key": "nudity",
                "label": "Nudity",
                "type": "choice",
                "options": _nudity_options(pairs),
            }
        )
    for key, (_, label) in _SCORE_COLUMNS.items():
        if key in index.scores:
            columns.append(_range_meta(key, label, index.scores[key]))
    for key, (_, label) in _COUNT_COLUMNS.items():
        if key in index.counts:
            columns.append(_int_range_meta(key, label, index.counts[key]))
    aspects = _code_counts(index.aspect_codes, index.aspect_values)
    columns.append(
        {
            "key": "aspect_ratios",
            "label": "Aspect Ratio",
            "type": "choice",
            "options": _options(aspects),
        }
    )
    if index.clothing is not None:
        tags = [(item, int(rows.shape[0])) for item, rows in index.clothing.items()]
        columns.append(
            {
                "key": "clothing",
                "label": "Clothing",
                "type": "tags",
                "options": _options(tags),
            }
        )
    return columns


# --- Public classes ---------------------------------------------------------


class CaptionStore:
    """Lazy, reloading index over the caption CSV. Thread-safe."""

    def __init__(self, csv_path: Path) -> None:
        self._path = Path(csv_path)
        self._lock = threading.Lock()
        self._index: Optional[_Index] = None
        self._error: Optional[str] = None
        self._signature: Optional[Tuple[int, int]] = None

    def _current(self) -> Optional[_Index]:
        """The up-to-date index, (re)building it when the file changed."""
        with self._lock:
            try:
                stat = self._path.stat()
            except OSError:
                self._index, self._signature = None, None
                self._error = f"caption file not found: {self._path}"
                return None
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != self._signature:
                self._rebuild(signature)
            return self._index

    def _rebuild(self, signature: Tuple[int, int]) -> None:
        started = time.perf_counter()
        try:
            self._index = _build_index(self._path, signature)
        except _CaptionFileError as exc:
            self._index, self._error, self._signature = None, str(exc), signature
            logger.warning("t2i captions unavailable: %s", exc)
        except OSError as exc:
            # Possibly transient: forget the signature so the next call retries.
            self._index, self._error, self._signature = (
                None,
                f"caption file cannot be read: {exc}",
                None,
            )
            logger.warning("t2i captions unreadable: %s", exc)
        else:
            self._error, self._signature = None, signature
            logger.info(
                "t2i captions: indexed %d rows from %s in %.2fs",
                self._index.total,
                self._path.name,
                time.perf_counter() - started,
            )

    def available(self) -> bool:
        return self._current() is not None

    def error(self) -> Optional[str]:
        self._current()
        return self._error

    def total(self) -> int:
        index = self._current()
        return index.total if index is not None else 0

    def meta(self) -> Dict[str, Any]:
        """Filter columns, option lists with counts, and numeric ranges."""
        index = self._current()
        if index is None:
            return {"total": 0, "columns": []}
        return {"total": index.total, "columns": _meta_columns(index)}

    def count(self, flt: Optional[Mapping[str, Any]]) -> int:
        """Rows matching ``flt`` (0 when the file is unavailable)."""
        index = self._current()
        if index is None:
            return 0
        return int(np.count_nonzero(_match_mask(index, flt)))

    def get(self, row_id: int) -> CaptionRow:
        """One row, read from disk on demand. ``KeyError`` if there is none."""
        index = self._current()
        if index is None or not 0 <= row_id < index.total:
            raise KeyError(row_id)
        return _read_row(index, int(row_id))

    def picker(
        self, flt: Optional[Mapping[str, Any]], rng: random.Random
    ) -> "CaptionPicker":
        index = self._current()
        if index is None:
            raise CaptionFilterError(self._error or "the caption file is unavailable")
        return CaptionPicker(self, index, flt, rng)


class CaptionPicker:
    """Hands out matching rows in random order without repeats.

    When every match has been used the pool is reshuffled (never repeating
    the last row first). If the caption file changes mid-run the filter is
    re-applied to the new file and a fresh cycle starts.
    """

    pool_size: int

    def __init__(
        self,
        store: CaptionStore,
        index: _Index,
        flt: Optional[Mapping[str, Any]],
        rng: random.Random,
    ) -> None:
        self._store = store
        self._flt = _normalise_filter(flt)
        self._rng = rng
        self._index = index
        self._order: List[int] = []
        self._pos = 0
        self._last: Optional[int] = None
        self.pool_size = 0
        self._fill(index)

    def _fill(self, index: _Index) -> None:
        ids = np.flatnonzero(_match_mask(index, self._flt))
        if ids.size == 0:
            raise CaptionFilterError("no captions match the filter")
        self._index = index
        self._order = [int(i) for i in ids]
        self.pool_size = len(self._order)
        self._rng.shuffle(self._order)
        self._pos = 0
        self._last = None

    def _reshuffle(self) -> None:
        self._rng.shuffle(self._order)
        if len(self._order) > 1 and self._order[0] == self._last:
            other = self._rng.randrange(1, len(self._order))
            self._order[0], self._order[other] = self._order[other], self._order[0]
        self._pos = 0

    def next(self) -> CaptionRow:
        index = self._store._current()
        if index is None:
            raise CaptionFilterError(
                self._store._error or "the caption file is unavailable"
            )
        if index is not self._index:
            self._fill(index)
        elif self._pos >= len(self._order):
            self._reshuffle()
        row_id = self._order[self._pos]
        self._pos += 1
        self._last = row_id
        return _read_row(index, row_id)
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `venv/bin/pytest tests/test_t2i_captions.py -q`

Expected: `61 passed in 0.24s` (the time varies). `RecordRobustnessTests` pins the CSV hazards: an embedded newline, CRLF endings, a UTF-8 BOM and trailing blank lines all index correctly.

- [ ] **Step 5: Quality gates**

```bash
venv/bin/black --check metascan/core/t2i_captions.py tests/test_t2i_captions.py
venv/bin/flake8 metascan/core/t2i_captions.py tests/test_t2i_captions.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/core/t2i_captions.py
```

Expected: black prints `All done!` and `2 files would be left unchanged.`; flake8 prints `0`; mypy prints `Success: no issues found in 1 source file`. (flake8 style warnings such as E203 or C901 are non-fatal in this repo and are not part of the gate.)

- [ ] **Step 6: Commit**

```bash
git add metascan/core/t2i_captions.py \
    tests/test_t2i_captions.py
git commit -m "feat(t2i): caption CSV index with filters and no-repeat picker" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

## Phase 2 — Prompt generation

Model profiles, prompt composition and fallbacks on top of the existing prompt store.

### Task 4: Prompt generation (`split_negative_block`, `t2i_models.py`, `t2i_prompt.py`, YAML keys)

**Files:**
- Create: `metascan/core/t2i_models.py`
- Create: `metascan/core/t2i_prompt.py`
- Modify: `metascan/core/meta_prompt_templates.py` (factor the negative-block handling of `parse_output` into a public `split_negative_block`)
- Modify: `data/meta_prompt.yml` (append the five new keys)
- Modify: `tests/test_prompt_store.py` (required-key list)
- Test: `tests/test_t2i_models.py`, `tests/test_t2i_prompt.py`

**Interfaces:**
- Consumes: `IDENTITY_STYLES` from `metascan/core/t2i_characters.py` (Task 1); the existing `get_prompt_store()` (`metascan/core/prompt_store.py`) and its `SAFETY_DIRECTIVE` / `UNCENSORED_DIRECTIVE` / `META_QWEN` / `META_SDXL` / `META_ZIMAGE` keys; the existing `parse_output` and its private cleaning helpers in `meta_prompt_templates.py`.
- Produces:
  - `split_negative_block(raw: str) -> tuple[str, str | None]` in `metascan/core/meta_prompt_templates.py`: cleans a raw model reply (code fence, JSON commit header, `Block N:` labels) then splits off a `Negative:` block; `parse_output` now calls it and behaves exactly as before
  - `@dataclass(frozen=True) class T2iModelProfile`: `id: str`, `label: str`, `meta_key: str`, `has_negative: bool`, `identity: str`, `dim_multiple: int`, `max_tokens: int`
  - `MODEL_PROFILES: Dict[str, T2iModelProfile]` in this order: `krea2` (Krea 2, `META_KREA2`, no negative, `ref`, 16, 420), `qwen` (Qwen-Image, `META_QWEN`, negative, `ref`, 16, 320), `sd` (SDXL, `META_SDXL`, negative, `noun`, 64, 480), `zimage` (Z-Image, `META_ZIMAGE`, no negative, `ref`, 16, 280)
  - `class T2iModelError(KeyError)` (its `str()` is the plain message)
  - `get_profile(model_id: str) -> T2iModelProfile` (raises `T2iModelError`)
  - `effective_identity(profile: T2iModelProfile, overrides: Mapping[str, str]) -> str` (a valid override from `t2i.identity` wins, anything else falls back to `profile.identity`)
  - `CONTENT_MODES: Tuple[str, ...] = ("uncensored", "sfw", "default")`
  - `compose_t2i_prompts(profile: T2iModelProfile, resolved_caption: str, content_mode: str) -> Tuple[str, str]`: system = `T2I_CAPTION_PREAMBLE` + the profile's guideline + the existing `UNCENSORED_DIRECTIVE` / `SAFETY_DIRECTIVE` (nothing for `default`); user = `DESCRIPTION:\n<caption>\n\nWrite the prompt now.`; `ValueError` on an unknown mode
  - `fallback_prompt(profile: T2iModelProfile, resolved_caption: str) -> Tuple[str, Optional[str]]`: the resolved caption (trimmed, parentheses removed), with the stock quality prefix for `sd` and the stock negative for `sd` and `qwen`
  - extra helpers the runner should use on the VLM reply: `parse_t2i_output(profile: T2iModelProfile, raw: str) -> Tuple[str, Optional[str]]` (split with `split_negative_block`, drop a stray negative for models without one, strip parentheses) and `strip_parentheses(text: str) -> str`
  - new keys in `data/meta_prompt.yml`: `META_KREA2`, `T2I_CAPTION_PREAMBLE`, `T2I_FALLBACK_PREFIX_SD`, `T2I_FALLBACK_NEGATIVE_SD`, `T2I_FALLBACK_NEGATIVE_QWEN` (none contains a parenthesis)

This task has three small red/green cycles: A (`split_negative_block`), B (model profiles), C (YAML keys and prompt composition).

- [ ] **Step 1: Write the failing test for `split_negative_block`** (`tests/test_t2i_prompt.py`, new file; cycle C extends it)

````python
"""Tests for t2i prompt generation (spec section 5).

Every model output below is hand-written with a known expected split; nothing
comes from the prompt library or the real caption CSV.
"""

from __future__ import annotations

import unittest

from metascan.core.meta_prompt_templates import parse_output, split_negative_block


class SplitNegativeBlockTests(unittest.TestCase):
    def test_two_blocks_separated_by_a_blank_line(self) -> None:
        raw = (
            "A woman in a red sweater sits in a cafe, warm window light, 85mm lens.\n"
            "\n"
            "Negative: low quality, blurry, extra fingers"
        )
        self.assertEqual(
            split_negative_block(raw),
            (
                "A woman in a red sweater sits in a cafe, warm window light, 85mm lens.",
                "low quality, blurry, extra fingers",
            ),
        )

    def test_negative_prompt_label_variant(self) -> None:
        self.assertEqual(
            split_negative_block("positive line\n\nNegative prompt: foo, bar, baz"),
            ("positive line", "foo, bar, baz"),
        )

    def test_markdown_emphasis_around_the_label(self) -> None:
        self.assertEqual(
            split_negative_block("positive\n\n**Negative:** alpha, beta"),
            ("positive", "alpha, beta"),
        )
        self.assertEqual(
            split_negative_block("positive\n\n**Negative: alpha, beta**"),
            ("positive", "alpha, beta"),
        )

    def test_negative_split_across_lines_is_joined(self) -> None:
        raw = "positive body\n\nNegative: first terms,\nsecond terms"
        self.assertEqual(
            split_negative_block(raw), ("positive body", "first terms,\nsecond terms")
        )

    def test_negative_that_starts_on_the_line_after_the_label(self) -> None:
        raw = "positive body\n\nNegative:\nlow quality, blurry"
        self.assertEqual(
            split_negative_block(raw), ("positive body", "low quality, blurry")
        )

    def test_no_negative_block_returns_the_cleaned_text_and_none(self) -> None:
        self.assertEqual(
            split_negative_block("\n\n  just a prompt with no negative  \n"),
            ("just a prompt with no negative", None),
        )

    def test_empty_negative_is_none_not_an_empty_string(self) -> None:
        self.assertEqual(
            split_negative_block("positive\n\nNegative:"), ("positive", None)
        )

    def test_the_word_negative_inside_a_sentence_is_not_a_block(self) -> None:
        raw = "The sign reads Negative: keep out, above a red door."
        self.assertEqual(split_negative_block(raw), (raw, None))

    def test_code_fence_json_header_and_block_labels_are_cleaned_first(self) -> None:
        self.assertEqual(
            split_negative_block("```\npositive content\n\nNegative: a, b\n```"),
            ("positive content", "a, b"),
        )
        self.assertEqual(
            split_negative_block(
                '{"extracted":[1],"overridden":[],"auto":[]}\n\npositive\n\nNegative: x'
            ),
            ("positive", "x"),
        )
        self.assertEqual(
            split_negative_block(
                "Block 1: positive content here\n\nBlock 2: Negative: x, y"
            ),
            ("positive content here", "x, y"),
        )

    def test_empty_input(self) -> None:
        self.assertEqual(split_negative_block(""), ("", None))
        self.assertEqual(split_negative_block("   \n  "), ("", None))

    def test_parse_output_is_split_negative_block_for_negative_targets(self) -> None:
        raw = "score_9, 1girl, solo\n\nNegative: score_6, worst quality"
        for target in ("sd", "pony", "chroma", "qwen"):
            with self.subTest(target=target):
                self.assertEqual(parse_output(target, raw), split_negative_block(raw))

    def test_parse_output_still_leaves_prose_targets_alone(self) -> None:
        raw = "A calm harbour at dawn.\n\nNegative: not a block for this target"
        for target in ("flux1", "flux2", "zimage"):
            with self.subTest(target=target):
                positive, negative = parse_output(target, raw)
                self.assertEqual(positive, raw)
                self.assertIsNone(negative)


if __name__ == "__main__":
    unittest.main()
````

- [ ] **Step 2: Run it and confirm it fails**

Run: `venv/bin/pytest tests/test_t2i_prompt.py -q`

Expected: FAIL — `ImportError: cannot import name 'split_negative_block' from 'metascan.core.meta_prompt_templates'`

- [ ] **Step 3: Implement** (`metascan/core/meta_prompt_templates.py`)

`metascan/core/meta_prompt_templates.py`: apply with `git apply` (the diff below is the exact change, produced from the verified tree):

```diff
diff --git a/metascan/core/meta_prompt_templates.py b/metascan/core/meta_prompt_templates.py
index 8ffae18..007239e 100644
--- a/metascan/core/meta_prompt_templates.py
+++ b/metascan/core/meta_prompt_templates.py
@@ -15,6 +15,9 @@ Surface area (kept intentionally small):
   ``(positive, negative)`` for targets that emit a ``Negative:`` block
   (sd / pony / chroma / qwen). For other targets ``negative`` is ``None``.
 
+* ``split_negative_block(raw)`` is the target-independent half of that:
+  it cleans a raw response and splits off a ``Negative:`` block.
+
 * ``EXTRA_OPTION_LABELS`` / ``MUTEX_PAIRS`` / ``TARGET_PRESETS`` mirror
   the same names exported by ``prompt_templates`` so the API and the
   frontend can swap import sources without restructuring callers.
@@ -526,6 +529,43 @@ def _strip_json_commit_header(text: str) -> str:
     return stripped[end + 1 :].lstrip("\n")
 
 
+def _clean_output(raw: str) -> str:
+    """Strip a code fence, a leading JSON commit header, stray ``Block N:``
+    labels and surrounding whitespace from a model response."""
+    text = _strip_markdown_fence(raw)
+    text = _strip_json_commit_header(text)
+    return _BLOCK_LABEL_RX.sub("", text).strip()
+
+
+def split_negative_block(raw: str) -> tuple[str, str | None]:
+    """Split a model response into ``(positive, negative_or_none)``.
+
+    Takes the raw response: it is cleaned first (code fence, JSON commit
+    header, ``Block N:`` labels, whitespace), then split at the first line
+    that begins with ``Negative:`` (Markdown emphasis and the "Negative
+    prompt:" variant are accepted). With no such line the whole cleaned text
+    is the positive prompt and ``negative`` is ``None``; so is an empty
+    negative. This is the negative handling :func:`parse_output` applies to
+    targets in :data:`MODELS_WITH_NEGATIVE`, exposed for callers (the t2i
+    runner) that pick by model profile rather than by ``TargetModel``.
+    """
+    text = _clean_output(raw)
+    match = _NEGATIVE_LINE_RX.search(text)
+    if not match:
+        return text, None
+
+    positive = text[: match.start()].rstrip()
+    inline_tail = match.group(1).strip()
+    after_line = text[match.end() :].strip()
+
+    if inline_tail and after_line:
+        negative = f"{inline_tail}\n{after_line}".strip()
+    else:
+        negative = (inline_tail or after_line).strip()
+
+    return positive.strip(), (negative or None)
+
+
 def parse_output(target_model: TargetModel, raw: str) -> tuple[str, str | None]:
     """Split a Qwen3 response into ``(positive, negative_or_none)``.
 
@@ -543,27 +583,9 @@ def parse_output(target_model: TargetModel, raw: str) -> tuple[str, str | None]:
     * stray ``Block 1:`` / ``Block 2:`` labels
     * the model failing to emit a Negative block (returns ``None``)
     """
-    text = _strip_markdown_fence(raw)
-    text = _strip_json_commit_header(text)
-    text = _BLOCK_LABEL_RX.sub("", text).strip()
-
     if target_model not in MODELS_WITH_NEGATIVE:
-        return text, None
-
-    match = _NEGATIVE_LINE_RX.search(text)
-    if not match:
-        return text, None
-
-    positive = text[: match.start()].rstrip()
-    inline_tail = match.group(1).strip()
-    after_line = text[match.end() :].strip()
-
-    if inline_tail and after_line:
-        negative = f"{inline_tail}\n{after_line}".strip()
-    else:
-        negative = (inline_tail or after_line).strip()
-
-    return positive.strip(), (negative or None)
+        return _clean_output(raw), None
+    return split_negative_block(raw)
 
 
 __all__ = [
@@ -580,4 +602,5 @@ __all__ = [
     "compose_generate_prompts",
     "elements_for",
     "parse_output",
+    "split_negative_block",
 ]
```

Apply with `git apply --whitespace=nowarn <<'PATCH'` ... `PATCH`, pasting the diff above between the two markers.

- [ ] **Step 4: Run it and confirm it passes, and that the existing template tests still pass untouched**

Run: `venv/bin/pytest tests/test_t2i_prompt.py tests/test_meta_prompt_templates.py -q`

Expected: `64 passed in 0.07s` (12 new tests plus 52 existing ones; the time varies).

- [ ] **Step 5: Write the failing test for the model profiles** (`tests/test_t2i_models.py`)

```python
"""Tests for the t2i model profile table (spec section 5.1)."""

from __future__ import annotations

import dataclasses
import unittest

from metascan.core.t2i_characters import IDENTITY_STYLES
from metascan.core.t2i_models import (
    MODEL_PROFILES,
    T2iModelError,
    T2iModelProfile,
    effective_identity,
    get_profile,
)

# id, label, guideline key, has_negative, identity, dim multiple, max tokens
SPEC_TABLE = [
    ("krea2", "Krea 2", "META_KREA2", False, "ref", 16, 420),
    ("qwen", "Qwen-Image", "META_QWEN", True, "ref", 16, 320),
    ("sd", "SDXL", "META_SDXL", True, "noun", 64, 480),
    ("zimage", "Z-Image", "META_ZIMAGE", False, "ref", 16, 280),
]


class ProfileTableTests(unittest.TestCase):
    def test_table_matches_the_spec_and_keeps_its_order(self) -> None:
        self.assertEqual(list(MODEL_PROFILES), ["krea2", "qwen", "sd", "zimage"])
        for row in SPEC_TABLE:
            with self.subTest(model=row[0]):
                profile = MODEL_PROFILES[row[0]]
                self.assertEqual(
                    (
                        profile.id,
                        profile.label,
                        profile.meta_key,
                        profile.has_negative,
                        profile.identity,
                        profile.dim_multiple,
                        profile.max_tokens,
                    ),
                    row,
                )

    def test_profiles_are_frozen(self) -> None:
        profile = MODEL_PROFILES["sd"]
        self.assertIsInstance(profile, T2iModelProfile)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            profile.max_tokens = 1  # type: ignore[misc]

    def test_every_default_identity_is_a_known_style(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertIn(profile.identity, IDENTITY_STYLES)

    def test_only_qwen_and_sdxl_have_a_negative_prompt(self) -> None:
        self.assertEqual(
            sorted(p.id for p in MODEL_PROFILES.values() if p.has_negative),
            ["qwen", "sd"],
        )

    def test_dimension_multiples_are_positive_powers_of_two(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertGreater(profile.dim_multiple, 0)
            self.assertEqual(profile.dim_multiple & (profile.dim_multiple - 1), 0)


class GetProfileTests(unittest.TestCase):
    def test_known_ids_return_the_table_entry(self) -> None:
        for model_id, profile in MODEL_PROFILES.items():
            self.assertIs(get_profile(model_id), profile)

    def test_unknown_id_raises_a_keyerror_subclass_naming_the_choices(self) -> None:
        for bad in ("flux", "", "KREA2", None):
            with self.subTest(bad=bad):
                with self.assertRaises(T2iModelError) as ctx:
                    get_profile(bad)  # type: ignore[arg-type]
                self.assertIsInstance(ctx.exception, KeyError)
                message = str(ctx.exception)
                self.assertFalse(message.startswith("'"), message)
                for known in MODEL_PROFILES:
                    self.assertIn(known, message)


class EffectiveIdentityTests(unittest.TestCase):
    def test_no_override_uses_the_profile_default(self) -> None:
        for profile in MODEL_PROFILES.values():
            self.assertEqual(effective_identity(profile, {}), profile.identity)

    def test_a_valid_override_for_the_model_wins(self) -> None:
        sd = MODEL_PROFILES["sd"]
        krea = MODEL_PROFILES["krea2"]
        self.assertEqual(effective_identity(sd, {"sd": "name"}), "name")
        self.assertEqual(effective_identity(krea, {"krea2": "noun"}), "noun")
        self.assertEqual(effective_identity(krea, {"krea2": "ref"}), "ref")

    def test_overrides_for_other_models_are_ignored(self) -> None:
        self.assertEqual(
            effective_identity(MODEL_PROFILES["qwen"], {"sd": "name", "krea2": "noun"}),
            "ref",
        )

    def test_an_unknown_style_falls_back_to_the_default(self) -> None:
        for bad in ("bogus", "", "REF", None, 3):
            with self.subTest(bad=bad):
                self.assertEqual(
                    effective_identity(MODEL_PROFILES["sd"], {"sd": bad}),  # type: ignore[dict-item]
                    "noun",
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run it and confirm it fails**

Run: `venv/bin/pytest tests/test_t2i_models.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'metascan.core.t2i_models'`

- [ ] **Step 7: Implement** (`metascan/core/t2i_models.py`)

```python
"""Model profiles for the t2i dialog (spec section 5.1). Pure: no I/O.

A profile ties a dialog model id to the guideline the VLM follows when it
writes that model's prompt, whether the model takes a negative prompt, how
later mentions of a character read by default, the size grid its latents
need and the token budget for the VLM's reply.

The shared ``TargetModel`` literal (storyboard and Prompt Playground) is
deliberately not extended: profiles reach the prompt YAML through the prompt
store using ``meta_key`` directly. ``max_tokens`` values are starting points,
tunable here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from metascan.core.t2i_characters import IDENTITY_STYLES


@dataclass(frozen=True)
class T2iModelProfile:
    id: str
    label: str
    meta_key: str  # the guideline's key in data/meta_prompt.yml
    has_negative: bool
    identity: str  # default identity style: ref | noun | name
    dim_multiple: int  # both edges of the latent must be a multiple of this
    max_tokens: int  # token budget for the VLM's reply


MODEL_PROFILES: Dict[str, T2iModelProfile] = {
    "krea2": T2iModelProfile("krea2", "Krea 2", "META_KREA2", False, "ref", 16, 420),
    "qwen": T2iModelProfile("qwen", "Qwen-Image", "META_QWEN", True, "ref", 16, 320),
    "sd": T2iModelProfile("sd", "SDXL", "META_SDXL", True, "noun", 64, 480),
    "zimage": T2iModelProfile(
        "zimage", "Z-Image", "META_ZIMAGE", False, "ref", 16, 280
    ),
}


class T2iModelError(KeyError):
    """An unknown t2i model id."""

    def __str__(self) -> str:  # KeyError would show the repr, quotes and all
        return str(self.args[0]) if self.args else ""


def get_profile(model_id: str) -> T2iModelProfile:
    try:
        return MODEL_PROFILES[model_id]
    except KeyError:
        known = ", ".join(MODEL_PROFILES)
        raise T2iModelError(
            f"unknown t2i model {model_id!r}; known models: {known}"
        ) from None


def effective_identity(profile: T2iModelProfile, overrides: Mapping[str, str]) -> str:
    """The identity style for ``profile``: a valid ``t2i.identity`` override
    from config wins, anything else falls back to the profile default."""
    override = overrides.get(profile.id)
    if isinstance(override, str) and override in IDENTITY_STYLES:
        return override
    return profile.identity
```

- [ ] **Step 8: Run it and confirm it passes**

Run: `venv/bin/pytest tests/test_t2i_models.py -q`

Expected: `11 passed in 0.01s` (the time varies).

- [ ] **Step 9: Write the failing tests for the YAML keys and prompt composition**

`tests/test_prompt_store.py`: apply with `git apply` (the diff below is the exact change, produced from the verified tree):

```diff
diff --git a/tests/test_prompt_store.py b/tests/test_prompt_store.py
index e1cdf2a..2ff728c 100644
--- a/tests/test_prompt_store.py
+++ b/tests/test_prompt_store.py
@@ -194,6 +194,12 @@ def test_active_yaml_has_every_required_key() -> None:
         "TAGGING_SYSTEM_PROMPT",
         "TAGGING_USER_PROMPT",
         "TAGGING_GRAMMAR",
+        # t2i caption-to-prompt (metascan/core/t2i_prompt.py)
+        "META_KREA2",
+        "T2I_CAPTION_PREAMBLE",
+        "T2I_FALLBACK_PREFIX_SD",
+        "T2I_FALLBACK_NEGATIVE_SD",
+        "T2I_FALLBACK_NEGATIVE_QWEN",
     }
     missing = required - set(store.keys())
     assert not missing, f"data/meta_prompt.yml missing keys: {sorted(missing)}"
```

Apply with `git apply --whitespace=nowarn <<'PATCH'` ... `PATCH`, pasting the diff above between the two markers.

`tests/test_t2i_prompt.py`: apply with `git apply` (the diff below is the exact change, produced from the verified tree):

````diff
diff --git a/tests/test_t2i_prompt.py b/tests/test_t2i_prompt.py
--- a/tests/test_t2i_prompt.py
+++ b/tests/test_t2i_prompt.py
@@ -6,9 +6,48 @@
 
 from __future__ import annotations
 
+import re
 import unittest
+from typing import Optional
 
 from metascan.core.meta_prompt_templates import parse_output, split_negative_block
+from metascan.core.prompt_store import get_prompt_store
+from metascan.core.t2i_models import MODEL_PROFILES, T2iModelProfile
+from metascan.core.t2i_prompt import (
+    CONTENT_MODES,
+    compose_t2i_prompts,
+    fallback_prompt,
+    parse_t2i_output,
+    strip_parentheses,
+)
+
+NEW_KEYS = (
+    "META_KREA2",
+    "T2I_CAPTION_PREAMBLE",
+    "T2I_FALLBACK_PREFIX_SD",
+    "T2I_FALLBACK_NEGATIVE_SD",
+    "T2I_FALLBACK_NEGATIVE_QWEN",
+)
+RESOLVED = (
+    "A 31-year-old West African woman with olive skin, brown eyes and an athletic "
+    "build sits on a wooden bench in a sunlit garden, her shoulder-length wavy "
+    "copper red hair moving in the breeze."
+)
+DIRECTIVE_KEYS = {
+    "default": None,
+    "sfw": "SAFETY_DIRECTIVE",
+    "uncensored": "UNCENSORED_DIRECTIVE",
+}
+
+
+def prompt(key: str) -> str:
+    return get_prompt_store().get(key)
+
+
+def reference_negative(meta_key: str) -> str:
+    """The ``Negative: ...`` reference example a guideline ends with."""
+    found = re.findall(r"^Negative: (.+)$", prompt(meta_key), flags=re.MULTILINE)
+    return found[-1]
 
 
 class SplitNegativeBlockTests(unittest.TestCase):
@@ -106,5 +145,258 @@
                 self.assertIsNone(negative)
 
 
+class ComposeTests(unittest.TestCase):
+    def test_content_modes(self) -> None:
+        self.assertEqual(CONTENT_MODES, ("uncensored", "sfw", "default"))
+
+    def test_system_is_preamble_then_guideline_then_directive(self) -> None:
+        preamble = prompt("T2I_CAPTION_PREAMBLE").rstrip()
+        for profile in MODEL_PROFILES.values():
+            for mode, key in DIRECTIVE_KEYS.items():
+                with self.subTest(model=profile.id, mode=mode):
+                    system, _ = compose_t2i_prompts(profile, RESOLVED, mode)
+                    expected = preamble + "\n\n" + prompt(profile.meta_key)
+                    if key is not None:
+                        expected += prompt(key)
+                    self.assertEqual(system, expected)
+
+    def test_parts_appear_in_order(self) -> None:
+        preamble = prompt("T2I_CAPTION_PREAMBLE").strip()
+        for profile in MODEL_PROFILES.values():
+            with self.subTest(model=profile.id):
+                system, _ = compose_t2i_prompts(profile, RESOLVED, "sfw")
+                guideline = prompt(profile.meta_key).strip()
+                directive = prompt("SAFETY_DIRECTIVE").strip()
+                self.assertTrue(system.startswith(preamble))
+                self.assertLess(system.index(preamble), system.index(guideline))
+                self.assertLess(system.index(guideline), system.index(directive))
+                self.assertTrue(system.endswith(directive))
+
+    def test_default_mode_adds_no_directive(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            system, _ = compose_t2i_prompts(profile, RESOLVED, "default")
+            self.assertNotIn("Content constraint", system)
+            self.assertTrue(system.endswith(prompt(profile.meta_key)))
+
+    def test_sfw_and_uncensored_add_the_existing_directives(self) -> None:
+        profile = MODEL_PROFILES["krea2"]
+        sfw, _ = compose_t2i_prompts(profile, RESOLVED, "sfw")
+        explicit, _ = compose_t2i_prompts(profile, RESOLVED, "uncensored")
+        self.assertIn("fully SFW", sfw)
+        self.assertNotIn("anatomically-correct", sfw)
+        self.assertIn("anatomically-correct", explicit)
+        self.assertNotIn("fully SFW", explicit)
+
+    def test_unknown_content_mode_is_rejected(self) -> None:
+        for bad in ("", "SFW", "explicit", "none"):
+            with self.subTest(bad=bad):
+                with self.assertRaises(ValueError) as ctx:
+                    compose_t2i_prompts(MODEL_PROFILES["sd"], RESOLVED, bad)
+                self.assertIn("uncensored", str(ctx.exception))
+
+    def test_user_turn_format_is_exact_for_every_model_and_mode(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            for mode in CONTENT_MODES:
+                with self.subTest(model=profile.id, mode=mode):
+                    _, user = compose_t2i_prompts(profile, RESOLVED, mode)
+                    self.assertEqual(
+                        user, "DESCRIPTION:\n" + RESOLVED + "\n\nWrite the prompt now."
+                    )
+
+    def test_the_caption_is_carried_verbatim(self) -> None:
+        caption = "Line one.\n\nLine two, with  odd   spacing.  "
+        _, user = compose_t2i_prompts(MODEL_PROFILES["qwen"], caption, "default")
+        self.assertEqual(user, f"DESCRIPTION:\n{caption}\n\nWrite the prompt now.")
+
+    def test_each_model_gets_only_its_own_guideline(self) -> None:
+        systems = {
+            profile.id: compose_t2i_prompts(profile, RESOLVED, "default")[0]
+            for profile in MODEL_PROFILES.values()
+        }
+        for profile in MODEL_PROFILES.values():
+            for other in MODEL_PROFILES.values():
+                present = prompt(other.meta_key).strip() in systems[profile.id]
+                self.assertEqual(present, profile is other, (profile.id, other.id))
+
+
+class TemplateTextTests(unittest.TestCase):
+    """Structural checks only: the wording is meant to be tuned in the YAML."""
+
+    def test_new_keys_exist_and_are_not_empty(self) -> None:
+        for key in NEW_KEYS:
+            with self.subTest(key=key):
+                self.assertTrue(prompt(key).strip())
+
+    def test_every_profile_guideline_exists(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            with self.subTest(model=profile.id):
+                self.assertTrue(prompt(profile.meta_key).strip())
+
+    def test_no_new_template_contains_a_parenthesis(self) -> None:
+        for key in NEW_KEYS:
+            with self.subTest(key=key):
+                self.assertNotIn("(", prompt(key))
+                self.assertNotIn(")", prompt(key))
+
+    def test_composed_krea2_prompts_carry_no_parenthesis_of_our_own(self) -> None:
+        # The existing content directives open with a parenthesised header of
+        # their own; everything else in the composed prompts is t2i text.
+        for mode, key in DIRECTIVE_KEYS.items():
+            with self.subTest(mode=mode):
+                system, user = compose_t2i_prompts(
+                    MODEL_PROFILES["krea2"], "A person by a window.", mode
+                )
+                if key is not None:
+                    system = system.replace(prompt(key), "")
+                for text in (system, user):
+                    self.assertNotIn("(", text)
+                    self.assertNotIn(")", text)
+
+    def test_the_preamble_covers_the_spec_points(self) -> None:
+        text = prompt("T2I_CAPTION_PREAMBLE")
+        lowered = text.lower()
+        self.assertIn("DESCRIPTION", text)
+        self.assertIn("no image", lowered)
+        self.assertIn("ground truth", lowered)
+        self.assertIn("word for word", lowered)
+        self.assertIn("format", lowered)
+        self.assertIn("parenthes", lowered)
+
+    def test_krea2_guideline_is_prose_and_has_no_negative_block(self) -> None:
+        text = prompt("META_KREA2")
+        self.assertIn("Krea 2", text)
+        self.assertIn("prose", text.lower())
+        self.assertNotIn("Negative:", text)
+
+    def test_fallback_negatives_are_the_guidelines_reference_negatives(self) -> None:
+        self.assertEqual(
+            prompt("T2I_FALLBACK_NEGATIVE_SD").strip(), reference_negative("META_SDXL")
+        )
+        self.assertEqual(
+            prompt("T2I_FALLBACK_NEGATIVE_QWEN").strip(),
+            reference_negative("META_QWEN"),
+        )
+
+    def test_fallback_prefix_is_the_sdxl_quality_opener(self) -> None:
+        self.assertEqual(
+            prompt("T2I_FALLBACK_PREFIX_SD").strip(),
+            "masterpiece, best quality, highly detailed, sharp focus",
+        )
+
+
+class FallbackTests(unittest.TestCase):
+    def expected(self, profile_id: str) -> tuple[str, Optional[str]]:
+        caption = RESOLVED
+        if profile_id == "sd":
+            caption = f"{prompt('T2I_FALLBACK_PREFIX_SD').strip()}, {RESOLVED}"
+        negatives = {
+            "sd": prompt("T2I_FALLBACK_NEGATIVE_SD").strip(),
+            "qwen": prompt("T2I_FALLBACK_NEGATIVE_QWEN").strip(),
+        }
+        return caption, negatives.get(profile_id)
+
+    def test_each_model_gets_its_own_fallback(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            with self.subTest(model=profile.id):
+                self.assertEqual(
+                    fallback_prompt(profile, RESOLVED), self.expected(profile.id)
+                )
+
+    def test_models_without_a_negative_get_none(self) -> None:
+        for model in ("krea2", "zimage"):
+            self.assertEqual(
+                fallback_prompt(MODEL_PROFILES[model], RESOLVED), (RESOLVED, None)
+            )
+
+    def test_only_sdxl_gets_the_quality_prefix(self) -> None:
+        prefix = prompt("T2I_FALLBACK_PREFIX_SD").strip()
+        for profile in MODEL_PROFILES.values():
+            text, _ = fallback_prompt(profile, RESOLVED)
+            self.assertEqual(text.startswith(prefix), profile.id == "sd", profile.id)
+
+    def test_caption_is_stripped_and_an_empty_one_leaves_just_the_prefix(self) -> None:
+        krea = MODEL_PROFILES["krea2"]
+        self.assertEqual(fallback_prompt(krea, "  spaced out \n"), ("spaced out", None))
+        prefix = prompt("T2I_FALLBACK_PREFIX_SD").strip()
+        self.assertEqual(fallback_prompt(MODEL_PROFILES["sd"], "   ")[0], prefix)
+
+    def test_a_profile_without_a_stock_negative_gets_none(self) -> None:
+        custom = T2iModelProfile("custom", "Custom", "META_FLUX1", True, "ref", 16, 300)
+        self.assertEqual(fallback_prompt(custom, RESOLVED), (RESOLVED, None))
+
+    def test_parentheses_are_removed_from_the_fallback(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            text, negative = fallback_prompt(profile, "Alice (30) waves (twice).")
+            self.assertNotIn("(", text)
+            self.assertNotIn(")", text)
+            self.assertIn("Alice 30 waves twice.", text)
+            self.assertTrue(negative is None or "(" not in negative)
+
+
+class ParseT2iOutputTests(unittest.TestCase):
+    POSITIVE = "A woman in a red sweater sits in a cafe, warm window light, 85mm lens."
+
+    def test_models_with_a_negative_split_the_block(self) -> None:
+        raw = f"{self.POSITIVE}\n\nNegative: low quality, blurry, extra fingers"
+        for model in ("sd", "qwen"):
+            with self.subTest(model=model):
+                self.assertEqual(
+                    parse_t2i_output(MODEL_PROFILES[model], raw),
+                    (self.POSITIVE, "low quality, blurry, extra fingers"),
+                )
+
+    def test_models_without_a_negative_drop_a_stray_block(self) -> None:
+        raw = f"{self.POSITIVE}\n\nNegative: low quality, blurry"
+        for model in ("krea2", "zimage"):
+            with self.subTest(model=model):
+                self.assertEqual(
+                    parse_t2i_output(MODEL_PROFILES[model], raw), (self.POSITIVE, None)
+                )
+
+    def test_a_missing_block_is_none_for_every_model(self) -> None:
+        for profile in MODEL_PROFILES.values():
+            self.assertEqual(
+                parse_t2i_output(profile, f"  {self.POSITIVE}\n"), (self.POSITIVE, None)
+            )
+
+    def test_fenced_and_labelled_output_is_cleaned(self) -> None:
+        raw = "```\nBlock 1: a quiet harbour at dawn\n\nBlock 2: Negative: blur, noise\n```"
+        self.assertEqual(
+            parse_t2i_output(MODEL_PROFILES["qwen"], raw),
+            ("a quiet harbour at dawn", "blur, noise"),
+        )
+
+    def test_parentheses_are_removed_from_both_parts(self) -> None:
+        raw = "a woman (smiling) in a (red:1.2) coat\n\nNegative: (bad hands:1.3), blur"
+        self.assertEqual(
+            parse_t2i_output(MODEL_PROFILES["sd"], raw),
+            ("a woman smiling in a red:1.2 coat", "bad hands:1.3, blur"),
+        )
+
+
+class StripParenthesesTests(unittest.TestCase):
+    def test_text_without_parentheses_is_unchanged(self) -> None:
+        for text in ("", "plain text", "line one\nline two", "two  spaces  stay"):
+            self.assertEqual(strip_parentheses(text), text)
+
+    def test_parentheses_are_removed_and_gaps_closed(self) -> None:
+        cases = {
+            "a (b) c": "a b c",
+            "((double))": "double",
+            "x ( y ) z": "x y z",
+            "end (": "end ",
+            "a (b)\nc (d)": "a b\nc d",
+            "tab\t(x)": "tab\tx",
+        }
+        for text, expected in cases.items():
+            with self.subTest(text=text):
+                self.assertEqual(strip_parentheses(text), expected)
+
+    def test_result_never_contains_a_parenthesis(self) -> None:
+        for text in ("(((", ")))", "a)b(c", "()", "(a)(b)(c)"):
+            self.assertNotIn("(", strip_parentheses(text))
+            self.assertNotIn(")", strip_parentheses(text))
+
+
 if __name__ == "__main__":
     unittest.main()
````

Apply with `git apply --whitespace=nowarn <<'PATCH'` ... `PATCH`, pasting the diff above between the two markers.

`tests/test_t2i_prompt.py` now also covers: composition order and content modes, the exact user turn, the fallbacks, `parse_t2i_output`, `strip_parentheses`, and structural checks on the five new keys (wording of the prompts is meant to be tuned, so the tests do not pin it). Every model output in it is hand-written.

- [ ] **Step 10: Run them and confirm they fail**

Run: `venv/bin/pytest tests/test_t2i_prompt.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'metascan.core.t2i_prompt'`

Run: `venv/bin/pytest tests/test_prompt_store.py::test_active_yaml_has_every_required_key -q`

Expected: FAIL — `AssertionError: data/meta_prompt.yml missing keys: ['META_KREA2', 'T2I_CAPTION_PREAMBLE', 'T2I_FALLBACK_NEGATIVE_QWEN', 'T2I_FALLBACK_NEGATIVE_SD', 'T2I_FALLBACK_PREFIX_SD']`

- [ ] **Step 11: Implement the YAML keys and the module**

`data/meta_prompt.yml`: apply with `git apply` (the diff below is the exact change, produced from the verified tree):

```diff
diff --git a/data/meta_prompt.yml b/data/meta_prompt.yml
index b373806..74d0ff0 100644
--- a/data/meta_prompt.yml
+++ b/data/meta_prompt.yml
@@ -549,3 +549,59 @@ I2V_TEMPLATE_SYSTEM: |-
   owns it); never mention "the image", "the frame", or "the video". Keep
   every person's identity, clothing and the scene's layout consistent
   across every beat.
+
+# --- T2I caption-to-prompt (metascan/core/t2i_prompt.py) -------------------
+# The system prompt is T2I_CAPTION_PREAMBLE + the model's guideline (META_KREA2
+# below, or the existing META_QWEN / META_SDXL / META_ZIMAGE) + the usual
+# content directive. META_KREA2 is a starting point derived from the Flux.1
+# rules and public Krea 2 guidance: tune it here, edits are hot-reloaded.
+# Nothing in these five entries may contain a parenthesis, because ComfyUI
+# reads parentheses as prompt-weighting syntax.
+
+META_KREA2: |-
+  You are an expert Krea 2 prompt engineer. Analyze the provided image and output a single, cohesive natural-language prompt that would recreate it, or its style, using Krea 2.
+
+  # How Krea 2 prompts work
+  - Krea 2 understands natural prose, not comma-separated tag lists. Write flowing sentences, the way you would brief a photographer or an art director.
+  - It rewards specific, concrete language: "warm tungsten light spilling through venetian blinds onto a scuffed oak floor" beats "cinematic lighting".
+  - Longer, detailed prompts do better than short ones. Aim for 90-150 words in which every phrase adds information; never pad.
+  - Weight syntax, BREAK and negative prompts are not used with this model. Describe only what should be in the picture, and state every constraint positively.
+
+  # Cover these elements, woven into prose, roughly in this order
+  1. Subject — who or what, with distinguishing details: age, expression, clothing and its fabric, pose, hands, gaze
+  2. Action — what the subject is doing at this moment, and the instant being caught
+  3. Setting — the place, its surfaces and objects, background depth, foreground elements
+  4. Composition & camera — shot type such as close-up, medium, wide or overhead, angle, lens and focal length, focus, where the subject sits in the frame
+  5. Lighting — direction, hardness, colour temperature, time of day, how shadows and highlights fall
+  6. Colour — dominant hues, accents, saturation and contrast
+  7. Texture & materials — skin, fabric weave, wood grain, metal, glass, wetness, dust, film grain, surface wear
+  8. Mood & medium — the emotional register, and whether it reads as a photograph, an illustration or a render
+
+  # Output rules
+  - Output ONLY the Krea 2 prompt. No preamble, no labels, no markdown, no quotation marks, no explanations.
+  - One paragraph of 90-150 words. No bullets, no line breaks.
+  - Natural prose only: complete sentences, present tense, declarative. Never a list of keywords or quality tags.
+  - No weight syntax, no brackets or parentheses of any kind, and no negative block.
+  - Avoid filler that does nothing here: "masterpiece," "best quality," "8k," "trending on artstation," "highly detailed," "award-winning."
+  - Be specific rather than generic: name the lens, the fabric, the light source and the surface instead of writing "beautiful" or "stunning".
+  - If a person resembles a real public figure, describe them generically rather than by name.
+  - If the medium is ambiguous, pick the most likely one and commit; do not hedge.
+  - Begin with the subject. Do not start with "An image of" or "A photo of" unless the medium is the most important feature.
+
+T2I_CAPTION_PREAMBLE: |-
+  # Caption-to-prompt mode
+  No image is attached to this request. The user message holds a DESCRIPTION of the picture to be made. Treat the DESCRIPTION as ground truth: wherever the guideline below says "the image", read it as "the description".
+
+  - Keep every trait, garment, pose, setting and camera cue that the DESCRIPTION states. Physical-trait phrases, such as age, ethnicity, skin, eyes, face, hair and build, must appear word for word, never paraphrased.
+  - Do not add, remove or change any person, object or identity, and do not invent names or a backstory. Where the guideline asks for something the DESCRIPTION leaves open, such as lighting, lens or texture, add a plausible detail that fits the scene and contradicts nothing in it.
+  - Output exactly the format the guideline specifies, and nothing else.
+  - Never use parentheses anywhere in your output. If the guideline offers weights or emphasis marks, leave them out.
+
+T2I_FALLBACK_PREFIX_SD: |-
+  masterpiece, best quality, highly detailed, sharp focus
+
+T2I_FALLBACK_NEGATIVE_SD: |-
+  low quality, worst quality, blurry, jpeg artifacts, lowres, watermark, signature, text, deformed, disfigured, bad anatomy, extra fingers, extra limbs, fused fingers, missing fingers, oversaturated, plastic skin, cartoon, anime, 3d render, cgi, harsh lighting, flat lighting
+
+T2I_FALLBACK_NEGATIVE_QWEN: |-
+  low quality, blurry, deformed, plastic skin, oversaturated, watermark, text artifacts
```

Apply with `git apply --whitespace=nowarn <<'PATCH'` ... `PATCH`, pasting the diff above between the two markers.

`metascan/core/t2i_prompt.py`:

```python
"""Prompt composition for the t2i dialog (spec section 5).

Pure apart from reading strings out of the hot-reloading prompt store:

* :func:`compose_t2i_prompts` builds the ``(system, user)`` pair that asks the
  VLM to rewrite a resolved caption in a model's style. The system prompt is
  ``T2I_CAPTION_PREAMBLE`` (no image is attached; the description is ground
  truth) + the model's guideline (``profile.meta_key``) + the usual content
  directive; the user turn is the description and a fixed closing line.
* :func:`parse_t2i_output` turns the VLM's raw reply into ``(prompt,
  negative)`` for a model profile.
* :func:`fallback_prompt` is what an unattended run uses when there is no VLM
  or it fails: the resolved caption, with a stock quality prefix for SDXL and
  a stock negative for the models that take one.

Parentheses are ComfyUI weighting syntax, so generated prompt text must never
contain them: :func:`strip_parentheses` enforces that on everything produced
here (VLM output and fallbacks). Manual prompts are the user's own text and
are never touched.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from metascan.core.meta_prompt_templates import split_negative_block
from metascan.core.prompt_store import get_prompt_store
from metascan.core.t2i_models import T2iModelProfile

CONTENT_MODES: Tuple[str, ...] = ("uncensored", "sfw", "default")

# content_mode -> the existing directive appended to the system prompt
# ("default" appends nothing).
_DIRECTIVE_KEYS: Dict[str, str] = {
    "uncensored": "UNCENSORED_DIRECTIVE",
    "sfw": "SAFETY_DIRECTIVE",
}

# model id -> prompt-store key of its stock fallback pieces
_FALLBACK_PREFIX_KEYS: Dict[str, str] = {"sd": "T2I_FALLBACK_PREFIX_SD"}
_FALLBACK_NEGATIVE_KEYS: Dict[str, str] = {
    "sd": "T2I_FALLBACK_NEGATIVE_SD",
    "qwen": "T2I_FALLBACK_NEGATIVE_QWEN",
}

_USER_TEMPLATE = "DESCRIPTION:\n{description}\n\nWrite the prompt now."


def compose_t2i_prompts(
    profile: T2iModelProfile, resolved_caption: str, content_mode: str
) -> Tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for ``VlmClient.generate_text``.

    ``content_mode`` is one of :data:`CONTENT_MODES`: ``uncensored`` and
    ``sfw`` append the matching existing directive to the system prompt,
    ``default`` adds nothing. The caption goes into the user turn verbatim.
    """
    if content_mode not in CONTENT_MODES:
        raise ValueError(
            f"unknown content mode {content_mode!r}; expected one of: "
            + ", ".join(CONTENT_MODES)
        )
    store = get_prompt_store()
    system = store.get("T2I_CAPTION_PREAMBLE").rstrip() + "\n\n"
    system += store.get(profile.meta_key)
    directive_key = _DIRECTIVE_KEYS.get(content_mode)
    if directive_key is not None:
        system += store.get(directive_key)
    return system, _USER_TEMPLATE.format(description=resolved_caption)


def strip_parentheses(text: str) -> str:
    """Remove ``(`` and ``)`` and close the gaps they leave.

    Only runs of spaces and tabs are collapsed, and only when something was
    removed, so newlines and untouched text pass through unchanged.
    """
    if "(" not in text and ")" not in text:
        return text
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"[()]", "", text))


def parse_t2i_output(profile: T2iModelProfile, raw: str) -> Tuple[str, Optional[str]]:
    """Turn the VLM's raw reply into ``(prompt, negative_or_none)``.

    The reply is cleaned and split at a ``Negative:`` line with
    :func:`split_negative_block`. A model without a negative prompt drops any
    such block (a stray one must not reach its positive prompt). Parentheses
    are removed from both parts.
    """
    positive, negative = split_negative_block(raw)
    if not profile.has_negative or negative is None:
        return strip_parentheses(positive), None
    return strip_parentheses(positive), strip_parentheses(negative)


def fallback_prompt(
    profile: T2iModelProfile, resolved_caption: str
) -> Tuple[str, Optional[str]]:
    """``(prompt, negative)`` to use when no VLM prompt is available.

    The prompt is the resolved caption (whitespace-trimmed, parentheses
    removed), with the stock quality prefix in front for SDXL. Models that
    take a negative prompt get their stock negative; the others get ``None``.
    """
    store = get_prompt_store()
    prompt = strip_parentheses(resolved_caption.strip())
    prefix_key = _FALLBACK_PREFIX_KEYS.get(profile.id)
    if prefix_key is not None:
        prefix = store.get(prefix_key).strip()
        prompt = f"{prefix}, {prompt}" if prompt else prefix
    negative_key = _FALLBACK_NEGATIVE_KEYS.get(profile.id)
    if profile.has_negative and negative_key is not None:
        return prompt, store.get(negative_key).strip()
    return prompt, None
```

- [ ] **Step 12: Run them and confirm they pass**

Run: `venv/bin/pytest tests/test_t2i_prompt.py tests/test_t2i_models.py tests/test_prompt_store.py tests/test_meta_prompt_templates.py -q --deselect tests/test_prompt_store.py::test_file_watcher_triggers_reload`

Expected: `117 passed, 1 deselected in 0.09s`. (`test_file_watcher_triggers_reload` is a known WSL2 flake that passes on its own; it is deselected here only to keep this run deterministic.)

- [ ] **Step 13: Quality gates**

```bash
venv/bin/black --check metascan/core/meta_prompt_templates.py metascan/core/t2i_models.py metascan/core/t2i_prompt.py tests/test_prompt_store.py tests/test_t2i_models.py tests/test_t2i_prompt.py
venv/bin/flake8 metascan/core/meta_prompt_templates.py metascan/core/t2i_models.py metascan/core/t2i_prompt.py tests/test_prompt_store.py tests/test_t2i_models.py tests/test_t2i_prompt.py --count --select=E9,F63,F7,F82 --show-source --statistics
venv/bin/mypy --check-untyped-defs metascan/core/meta_prompt_templates.py metascan/core/t2i_models.py metascan/core/t2i_prompt.py
```

Expected: black prints `All done!` and `6 files would be left unchanged.`; flake8 prints `0`; mypy prints `Success: no issues found in 3 source files`. (flake8 style warnings such as E203 or C901 are non-fatal in this repo and are not part of the gate.)

- [ ] **Step 14: Commit**

```bash
git add metascan/core/meta_prompt_templates.py \
    metascan/core/t2i_models.py \
    metascan/core/t2i_prompt.py \
    data/meta_prompt.yml \
    tests/test_prompt_store.py \
    tests/test_t2i_models.py \
    tests/test_t2i_prompt.py
git commit -m "feat(t2i): split_negative_block, model profiles and caption-to-prompt composition" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
