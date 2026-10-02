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
        0,
        1,
        "none",
        0.10,
        0.00,
        "__CLARA__ reads on a porch swing in a yellow cardigan, grinning at "
        "something on the page, one foot tucked under her.",
        {"partner": "A", "kiss": "N", "emotion": "C", "act": "A", "issues": []},
    ),
    Example(
        0,
        1,
        "full",
        0.85,
        0.95,
        "Viewed from a man's point of view, __CLARA__ lies on her back on a hotel "
        "bed with her legs wrapped around his waist; his hands hold her knees apart.",
        {"partner": "B", "kiss": "N", "emotion": "A", "act": "K", "issues": []},
    ),
    Example(
        1,
        1,
        "partial",
        0.60,
        0.40,
        "__DIANNA__ sits on __ADAM__'s lap in an armchair, her blouse open, as "
        "they kiss deeply; his hand cups her bare breast.",
        {"partner": "A", "kiss": "Y", "emotion": "A", "act": "B", "issues": []},
    ),
    Example(
        0,
        1,
        "none",
        0.20,
        0.00,
        "__CLARA__ sits on a stool facing away from the viewer, her bare back to "
        "the camera, and smiles at the viewer. Her right hand rests on her knee, "
        "her left hand holds a mug, and her right hand brushes her hair aside.",
        {
            "partner": "A",
            "kiss": "N",
            "emotion": "C",
            "act": "A",
            "issues": [
                {
                    "type": "facing_conflict",
                    "quote_a": "facing away from the viewer",
                    "quote_b": "smiles at the viewer",
                },
                {
                    "type": "extra_limb",
                    "quote_a": "Her right hand rests on her knee",
                    "quote_b": "her right hand brushes her hair aside",
                },
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
