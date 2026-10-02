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
    Act(
        "A",
        "none-artistic",
        0,
        0,
        0,
        False,
        "posing, nudity or suggestive framing with no sexual contact and no touching of genitals",
    ),
    Act(
        "B",
        "breast-fondling",
        0,
        1,
        1,
        True,
        "hands (her own or a partner's) cupping, squeezing or pinching breasts or nipples in a sexual setting",
    ),
    Act(
        "C",
        "female-masturbation",
        0,
        1,
        1,
        False,
        "her own hand on her vulva, crotch or between her legs",
    ),
    Act(
        "D",
        "female-toy-masturbation",
        0,
        1,
        1,
        False,
        "her own hand holding a dildo, vibrator or phallic object at or between her legs",
    ),
    Act(
        "E",
        "partner-manual-female",
        0,
        1,
        2,
        True,
        "another person's fingers or hand between her legs",
    ),
    Act(
        "F",
        "object-insertion",
        0,
        1,
        1,
        True,
        "a toy, speculum or other object inserted or held at the genitals by someone else, "
        "including clinical settings",
    ),
    Act("G", "male-masturbation", 1, 0, 1, False, "his own hand on his erect penis"),
    Act("H", "handjob", 1, 0, 2, True, "someone else's hand on his erect penis"),
    Act(
        "I",
        "fellatio",
        1,
        0,
        2,
        True,
        "an erect penis in or right next to a partner's mouth",
    ),
    Act(
        "J",
        "cunnilingus",
        0,
        1,
        2,
        True,
        "her legs parted with a partner's face or mouth at her crotch",
    ),
    Act(
        "K",
        "missionary",
        1,
        1,
        2,
        True,
        "she lies on her back, legs apart or raised, with him on top of or between them",
    ),
    Act(
        "L",
        "doggy",
        1,
        1,
        2,
        True,
        "she is bent forward or on hands and knees with him behind her; one or both nude",
    ),
    Act(
        "M",
        "cowgirl",
        1,
        1,
        2,
        True,
        "she straddles his hips or groin facing him; partly or fully nude",
    ),
    Act(
        "N",
        "reverse-cowgirl",
        1,
        1,
        2,
        True,
        "she straddles him facing away from him, toward his feet or the viewer",
    ),
    Act(
        "O",
        "spooning",
        1,
        1,
        2,
        True,
        "both lying on their sides, him behind her, pelvises together",
    ),
    Act(
        "P",
        "standing-sex",
        1,
        1,
        2,
        True,
        "both standing with pelvises joined; she may be lifted or against a wall",
    ),
    Act("Q", "paizuri", 1, 1, 2, True, "a penis between her breasts"),
    Act(
        "R",
        "ff-tribbing",
        0,
        2,
        2,
        False,
        "two women with crotches pressed together and legs interlocked",
    ),
    Act(
        "S",
        "unclear",
        0,
        0,
        0,
        False,
        "sexual content is suggested but no single act fits, or two acts are equally likely",
    ),
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
