"""Deterministic rewrites for hand-edited i2v prompts (no model call).

Two things users type naturally that MiniMax H3 wants in a fixed form
(data/prompt_guides/minimax-h3/VIDEO_PROMPT_WRITING_GUIDE_base_en.md):

* camera motion (section 4): one verb phrase from the motion table,
  then ``with small/large amplitude`` and ``at slow/fast speed`` --
  "The camera slowly dollies in." -> "The camera pushes in at slow speed."
* speech (section 4.4): speaker phrase + id outside ``<d>``, language tag
  and the VERBATIM words inside -- ``She says, "Hi."`` ->
  ``She (S1) says: <d>[English] Hi.</d>``

Pure module. Every rewrite is conservative: when the intent is ambiguous
("moves left" -- a pan or a truck?) or the speaker cannot be found, the
finding is reported WITHOUT a replacement and the text is left alone. A
fix is only ever offered, never applied behind the user's back -- the
dialog's prompt box is "Generate uses this text".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from metascan.core.h3_compiler import _CAMERA_PHRASES


@dataclass(frozen=True)
class I2vFinding:
    code: str  # "camera_phrase" | "speech_format"
    message: str
    start: int
    end: int
    replacement: Optional[str] = None

    @property
    def fixable(self) -> bool:
        return self.replacement is not None


# ---- camera ------------------------------------------------------------

_CAMERA_SENTENCE_RE = re.compile(r"\bThe camera ([a-z][^.]*)\.")
_KNOWN_PHRASES: Tuple[str, ...] = tuple(_CAMERA_PHRASES.values())
# "cuts to" is shot grammar, not a motion -- the lint has always allowed it.
_ACCEPTED_PREFIXES: Tuple[str, ...] = _KNOWN_PHRASES + ("cuts to",)

_SPEED_WORDS = {
    "slowly": "slow",
    "gradually": "slow",
    "steadily": "slow",
    "leisurely": "slow",
    "quickly": "fast",
    "rapidly": "fast",
    "swiftly": "fast",
    "fast": "fast",
    "abruptly": "fast",
}
_AMPLITUDE_WORDS = {
    "slightly": "small",
    "subtly": "small",
    "gently": "small",
    "softly": "small",
    "barely": "small",
    "dramatically": "large",
    "widely": "large",
    "sweepingly": "large",
}
# Carry no guide meaning; dropped rather than blocking the rewrite.
_FILLER_WORDS = {"smoothly", "then", "now", "also", "just"}
_MODIFIER_RE = re.compile(
    r"^(?P<word>"
    + "|".join(sorted({*_SPEED_WORDS, *_AMPLITUDE_WORDS, *_FILLER_WORDS}))
    + r")\b[\s,]*(?:and\s+)?",
    re.I,
)

# Where a dropped object ends and a clause worth keeping begins.
_CLAUSE_BOUNDARY_RE = re.compile(
    r",|\s(?:as|while|until|and|before|when|revealing|to reveal|past|through)\b"
)

_DIR = r"(?:to the |toward the |towards the )?(?P<dir>left|right)(?:wards?)?\b"
_MOVERS = (
    r"(?:dollies|dolly|moves|glides|creeps|drifts|travels|eases|inches|pushes|tracks)"
)

# (pattern, phrase or callable(match)->phrase, mode, tail preposition)
#   mode "keep": the rest of the sentence follows the phrase unchanged
#   mode "drop": the rest up to the next clause boundary is the object the
#                guide phrase already names ("the subject") -- dropped
#   a (?P<prep>...) group that matched turns the rest into "<tail prep> <rest>"
# ORDER MATTERS: "tracks left" (truck) and "tracks in" (push) must win
# over bare "tracks" (tracking shot).
_SYNONYMS: Tuple[Tuple["re.Pattern[str]", Any, str, str], ...] = tuple(
    (re.compile(p, re.I), phrase, mode, prep)
    for p, phrase, mode, prep in (
        # push in
        (
            _MOVERS + r" in(?: closer)?(?P<prep> on| to| towards?)?\b",
            "pushes in",
            "keep",
            "toward",
        ),
        (
            r"(?:" + _MOVERS[3:-1] + r"|comes|gets) (?:closer|nearer|forwards?)"
            r"(?P<prep> to| towards?| on)?\b",
            "pushes in",
            "keep",
            "toward",
        ),
        (_MOVERS + r"(?P<prep> towards?)\b", "pushes in", "keep", "toward"),
        # pull out
        (
            r"(?:dollies|dolly|moves|glides|drifts|pulls|eases|backs|tracks|travels) "
            r"(?:out|back|backwards?|away)\b(?P<prep> from)?",
            "pulls out",
            "keep",
            "from",
        ),
        (
            r"(?:retreats|recedes|withdraws|backs up|backs off)\b(?P<prep> from)?",
            "pulls out",
            "keep",
            "from",
        ),
        # zoom
        (
            r"zooms (?:closer|tighter)\b(?P<prep> in on| on| to)?",
            "zooms in",
            "keep",
            "on",
        ),
        (r"punches in\b(?P<prep> on| to)?", "zooms in", "keep", "on"),
        (
            r"(?:zooms (?:back|away|wider)|widens)\b(?P<prep> from)?",
            "zooms out",
            "keep",
            "from",
        ),
        # roll (before pan: "rolls to the right" is not a pan)
        (
            r"rolls " + _DIR,
            lambda m: (
                "rolls clockwise"
                if m.group("dir").lower() == "right"
                else "rolls counterclockwise"
            ),
            "keep",
            "",
        ),
        (
            r"(?:rolls|rotates|spins|twists|turns) clockwise\b",
            "rolls clockwise",
            "keep",
            "",
        ),
        (
            r"(?:rolls|rotates|spins|twists|turns) "
            r"(?:counter-?clockwise|anti-?clockwise)\b",
            "rolls counterclockwise",
            "keep",
            "",
        ),
        # pan / tilt / truck / pedestal
        (
            r"(?:pans|swings|sweeps|swivels|turns|rotates|pivots) " + _DIR,
            lambda m: f"pans {m.group('dir').lower()}",
            "keep",
            "",
        ),
        (
            r"(?:tilts|angles|pivots|looks|points|tips|glances) (?P<dir>up|down)(?:wards?)?\b",
            lambda m: f"tilts {m.group('dir').lower()}",
            "keep",
            "",
        ),
        (
            r"(?:slides|glides|trucks|strafes|tracks|dollies|crabs) " + _DIR,
            lambda m: f"trucks {m.group('dir').lower()}",
            "keep",
            "",
        ),
        (
            r"(?:slides|glides|moves|trucks|strafes) sideways " + _DIR,
            lambda m: f"trucks {m.group('dir').lower()}",
            "keep",
            "",
        ),
        (
            r"(?:rises|ascends|lifts|elevates)\b(?: up(?:wards?)?\b)?",
            "pedestals up",
            "keep",
            "",
        ),
        (
            r"(?:cranes|booms|jibs|moves|travels|glides|drifts|floats) up(?:wards?)?\b",
            "pedestals up",
            "keep",
            "",
        ),
        (
            r"(?:lowers|descends|sinks|drops)\b(?: down(?:wards?)?\b)?",
            "pedestals down",
            "keep",
            "",
        ),
        (
            r"(?:cranes|booms|jibs|moves|travels|glides|drifts|floats) down(?:wards?)?\b",
            "pedestals down",
            "keep",
            "",
        ),
        # static
        (
            r"(?:stays|remains|is|keeps|sits|rests|holds) "
            r"(?:perfectly |completely |totally |entirely )?"
            r"(?:still|fixed|static|stationary|locked off|locked down|locked|steady|"
            r"motionless|put|in place|unmoving|on a tripod)\b",
            "holds a static shot",
            "keep",
            "",
        ),
        (r"(?:does not|doesn't|never) moves?\b", "holds a static shot", "keep", ""),
        (r"(?:holds|lingers)\b", "holds a static shot", "keep", ""),
        # shake
        (
            r"(?:shakes|rattles|jolts|judders|vibrates|bounces) "
            r"(?:violently|hard|heavily|wildly|strongly|intensely|a lot)\b",
            "shakes strongly",
            "keep",
            "",
        ),
        (
            r"(?:shakes|trembles|jitters|wobbles|vibrates|quivers|sways|bobs)\b(?: a little| a bit| lightly)?",
            "shakes slightly",
            "keep",
            "",
        ),
        (r"is (?:handheld|hand-held|shaky)\b", "shakes slightly", "keep", ""),
        # arc / tracking -- the guide phrase already names "the subject"
        (
            r"(?:orbits|circles|revolves|arcs|swings|rotates|moves|sweeps|spins|travels|glides) around\b",
            "arcs around the subject",
            "drop",
            "",
        ),
        (r"(?:orbits|circles|encircles)\b", "arcs around the subject", "drop", ""),
        (
            r"(?:follows|tracks|trails|chases|shadows|pursues)\b",
            "tracks the subject",
            "drop",
            "",
        ),
        (
            r"(?:stays|keeps pace|keeps up|moves|travels) with\b",
            "tracks the subject",
            "drop",
            "",
        ),
    )
)

# Motions that take no amplitude/speed modifier in the guide.
_NO_MODIFIERS = {"holds a static shot", "shakes slightly", "shakes strongly"}


def _accepted(fragment: str) -> bool:
    return fragment.startswith(_ACCEPTED_PREFIXES)


def _take_modifiers(text: str, mods: Dict[str, str]) -> str:
    """Strip leading adverbs off ``text`` into ``mods``; return the rest."""
    while True:
        m = _MODIFIER_RE.match(text)
        if not m:
            return text
        word = m.group("word").lower()
        if word in _SPEED_WORDS:
            mods.setdefault("speed", _SPEED_WORDS[word])
        elif word in _AMPLITUDE_WORDS:
            mods.setdefault("amplitude", _AMPLITUDE_WORDS[word])
        text = text[m.end() :]


def rewrite_camera_fragment(fragment: str) -> Optional[str]:
    """Guide phrasing for what follows "The camera " (no final period),
    or None when it is already fine or cannot be mapped with confidence."""
    frag = " ".join(fragment.split())
    if not frag or _accepted(frag):
        return None
    mods: Dict[str, str] = {}
    body = _take_modifiers(frag, mods)

    phrase: Optional[str] = None
    rest = ""
    if _accepted(body):  # only the leading adverb was in the way
        phrase = next(p for p in _ACCEPTED_PREFIXES if body.startswith(p))
        rest = body[len(phrase) :].strip()
        if phrase == "cuts to":
            return None
    else:
        for pattern, target, mode, tail_prep in _SYNONYMS:
            m = pattern.match(body)
            if not m:
                continue
            phrase = target(m) if callable(target) else target
            rest = body[m.end() :].strip()
            if mode == "drop":
                boundary = _CLAUSE_BOUNDARY_RE.search(rest)
                obj = rest[: boundary.start()] if boundary else rest
                rest = rest[boundary.start() :].strip() if boundary else ""
                for word in obj.split():  # "circles around her slowly"
                    _take_modifiers(word, mods)
            else:
                rest = _take_modifiers(rest, mods)  # "dollies in slowly"
                if rest and tail_prep and m.groupdict().get("prep"):
                    rest = f"{tail_prep} {rest}"
            break
    if phrase is None:
        return None

    out = phrase
    if phrase not in _NO_MODIFIERS:
        if mods.get("amplitude") and "amplitude" not in rest:
            out += f" with {mods['amplitude']} amplitude"
        if mods.get("speed") and "speed" not in rest:
            out += f" at {mods['speed']} speed"
    if rest:
        out += rest if rest.startswith(",") else f" {rest}"
    # A fix must produce what the lint accepts, or it is not a fix.
    return out if _accepted(out) and out != frag else None


def camera_findings(text: str) -> List[I2vFinding]:
    findings: List[I2vFinding] = []
    for m in _CAMERA_SENTENCE_RE.finditer(text):
        frag = m.group(1)
        if _accepted(frag):
            continue
        new = rewrite_camera_fragment(frag)
        findings.append(
            I2vFinding(
                code="camera_phrase",
                message=f"camera sentence uses non-guide phrasing: 'The camera {frag}.'",
                start=m.start(),
                end=m.end(),
                replacement=f"The camera {new}." if new else None,
            )
        )
    return findings


# ---- speech ------------------------------------------------------------

_QUOTE_RE = re.compile(r'"([^"\n]{1,400})"|“([^”\n]{1,400})”')
_D_SPAN_RE = re.compile(r"<d>.*?</d>", re.S)
_SPEAKER_ID_RE = re.compile(r"\(S(\d+)(?:\s*,\s*S\d+)*\)")
# Everything a sentence can start after. "]" covers "[Shot 1] ", the
# label alternative the field name that opens the description.
_SENTENCE_BREAK_RE = re.compile(r'(?:[.!?]["”]?\s+|</d>\s*|\n+|\]\s+|_description:\s*)')
_SPEECH_VERBS = (
    r"(?:calls out|cries out|says|said|say|whispers?|whispered|shouts?|shouted|"
    r"yells?|yelled|murmurs?|mutters?|asks?|asked|replies|reply|replied|answers?|"
    r"exclaims?|calls|cries|announces?|adds?|continues?|sings?|speaks?|states?|"
    r"declares?|sighs?|gasps?|screams?|moans?|groans?|laughs?|breathes?|tells?|told|"
    r"greets?|pleads?|begs?|warns?|snaps?|growls?|hisses?|stammers?|stutters?|"
    r"purrs?|giggles?|chuckles?)"
)
# Speech verb, up to four words ("to the camera"), optional , or : -- and
# then the quote must start.
_PRE_ATTRIBUTION_RE = re.compile(
    r"\b(?P<verb>" + _SPEECH_VERBS + r")\b(?P<mid>(?:\s+[\w'-]+){0,4}?)\s*[,:]?\s*$",
    re.I,
)
# '"Wait!" the girl shouts.'
_POST_ATTRIBUTION_RE = re.compile(
    r"\s*(?P<speaker>(?:the |a |an )?[\w'-]+(?:\s+[\w'-]+){0,3}?)\s+"
    r"(?P<adverb>\w+ly\s+)?(?P<verb>"
    + _SPEECH_VERBS
    + r")\b(?P<adverb2>\s+\w+ly)?\s*[.!]?",
    re.I,
)
_PRONOUN_RE = re.compile(r"^\s*(?:she|he|they|it|we|i)\b", re.I)
_TRAILING_ADVERB_RE = re.compile(r"\s+\w+ly\s*$")
# Quoted text that is SEEN, not spoken.
_VISIBLE_TEXT_RE = re.compile(
    r"\b(?:sign|signs|label|labels|text|caption|banner|title|screen|written|reads|"
    r"reading|poster|note|lettering|letters|subtitle|subtitles|words?|logo|printed|"
    r"print|engraved|headline|tattoo|display|displays|shows)\b",
    re.I,
)
_FEMALE = {
    "she", "her", "woman", "girl", "lady", "mother", "mom", "wife", "daughter",
    "sister", "queen", "actress", "waitress", "bride", "female",
}  # fmt: skip
_MALE = {
    "he", "his", "him", "man", "boy", "guy", "father", "dad", "husband", "son",
    "brother", "king", "actor", "waiter", "groom", "male", "gentleman",
}  # fmt: skip


def _language(spoken: str) -> str:
    """Language tag from the script. Latin script says nothing about the
    language, so it stays the English default the guide's examples use."""
    if re.search(r"[぀-ヿ]", spoken):
        return "Japanese"
    if re.search(r"[가-힯]", spoken):
        return "Korean"
    if re.search(r"[一-鿿]", spoken):
        return "Chinese"
    if re.search(r"[Ѐ-ӿ]", spoken):
        return "Russian"
    return "English"


def _speaker_key(phrase: str) -> str:
    """Same key => same speaker id. "She" and "the woman" share one; an
    unrecognized noun phrase is its own speaker."""
    words = {w.lower() for w in re.findall(r"[A-Za-z']+", phrase)}
    female, male = bool(words & _FEMALE), bool(words & _MALE)
    if female != male:
        return "female" if female else "male"
    return "np:" + " ".join(sorted(words - {"the", "a", "an"}))


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


class _SpeakerIds:
    def __init__(self, text: str) -> None:
        self._next = 1 + max((int(n) for n in re.findall(r"\(S(\d+)", text)), default=0)
        self._by_key: Dict[str, int] = {}

    def for_speaker(self, phrase: str) -> str:
        key = _speaker_key(phrase)
        if key not in self._by_key:
            self._by_key[key] = self._next
            self._next += 1
        return f"(S{self._by_key[key]})"


def _unfixable(start: int, end: int, excerpt: str) -> I2vFinding:
    return I2vFinding(
        code="speech_format",
        message=(
            f"quoted text is not in MiniMax dialogue form: {excerpt} -- if it is "
            "spoken, write: <speaker> (S1) says: <d>[English] ...</d>"
        ),
        start=start,
        end=end,
    )


def speech_findings(text: str) -> List[I2vFinding]:
    d_spans = [(m.start(), m.end()) for m in _D_SPAN_RE.finditer(text)]
    ids = _SpeakerIds(text)
    findings: List[I2vFinding] = []
    for q in _QUOTE_RE.finditer(text):
        if any(s <= q.start() < e for s, e in d_spans):
            continue
        spoken = q.group(1) if q.group(1) is not None else q.group(2)
        excerpt = q.group(0) if len(q.group(0)) <= 60 else q.group(0)[:57] + '..."'

        sentence_start = 0
        for brk in _SENTENCE_BREAK_RE.finditer(text, 0, q.start()):
            sentence_start = brk.end()
        lead = text[sentence_start : q.start()]
        if _VISIBLE_TEXT_RE.search(lead):
            continue  # a sign, a label, a caption: seen, not spoken

        message = f"spoken line is not in MiniMax dialogue form (<d>): {excerpt}"
        pre = _PRE_ATTRIBUTION_RE.search(lead)
        if pre and lead[: pre.start("verb")].strip():
            clause = lead[: pre.start("verb")]
            if _SPEAKER_ID_RE.search(clause):
                head = clause  # already identified -- only the form is off
            else:
                pronoun = _PRONOUN_RE.match(clause)
                if pronoun:
                    at = pronoun.end()  # the id belongs to the speaker...
                else:
                    adverb = _TRAILING_ADVERB_RE.search(clause)
                    at = adverb.start() if adverb else len(clause.rstrip())
                sid = ids.for_speaker(clause)
                head = f"{clause[:at].rstrip()} {sid} {clause[at:].lstrip()}"
            said = re.sub(r"[\s,:]+$", "", head + lead[pre.start("verb") :])
            findings.append(
                I2vFinding(
                    code="speech_format",
                    message=message,
                    start=sentence_start,
                    end=q.end(),
                    replacement=f"{said}: <d>[{_language(spoken)}] {spoken}</d>",
                )
            )
            continue

        post = _POST_ATTRIBUTION_RE.match(text, q.end()) if not lead.strip() else None
        if post and not _VISIBLE_TEXT_RE.search(post.group("speaker")):
            speaker = post.group("speaker").strip()
            verb = (
                (post.group("adverb") or "")
                + post.group("verb")
                + (post.group("adverb2") or "")
            )
            # '"Hello," he says.' -- that comma is attribution punctuation,
            # not part of what was said.
            line = re.sub(r",$", ".", spoken)
            findings.append(
                I2vFinding(
                    code="speech_format",
                    message=message,
                    start=q.start(),
                    end=post.end(),
                    replacement=(
                        f"{_capitalize(speaker)} {ids.for_speaker(speaker)} {verb}: "
                        f"<d>[{_language(spoken)}] {line}</d>"
                    ),
                )
            )
            continue

        findings.append(_unfixable(q.start(), q.end(), excerpt))
    return findings


# ---- apply / report ------------------------------------------------------


def apply_i2v_fixes(text: str, findings: List[I2vFinding]) -> str:
    """Apply every fixable finding. Spans are replaced right-to-left so
    earlier offsets stay valid; an overlapping span loses to the one
    before it rather than corrupting the text."""
    chosen: List[I2vFinding] = []
    last_end = -1
    for f in sorted((f for f in findings if f.fixable), key=lambda f: f.start):
        if f.start >= last_end:
            chosen.append(f)
            last_end = f.end
    for f in reversed(chosen):
        text = text[: f.start] + str(f.replacement) + text[f.end :]
    return text


def lint_i2v_report(text: str, duration_s: float) -> Dict[str, Any]:
    """The dialog's lint payload: every advisory warning, the subset that
    can be fixed (with before/after), and the prompt with them applied."""
    # Function-level: i2v_compiler imports this module for its lint.
    from metascan.core.i2v_compiler import lint_i2v_prompt

    findings = sorted(
        camera_findings(text) + speech_findings(text), key=lambda f: f.start
    )
    fixes = [
        {
            "code": f.code,
            "message": f.message,
            "original": text[f.start : f.end],
            "replacement": f.replacement,
        }
        for f in findings
        if f.fixable
    ]
    return {
        "warnings": lint_i2v_prompt(text, duration_s),
        "fixes": fixes,
        "fixed_prompt": apply_i2v_fixes(text, findings) if fixes else None,
    }


__all__ = [
    "I2vFinding",
    "apply_i2v_fixes",
    "camera_findings",
    "lint_i2v_report",
    "rewrite_camera_fragment",
    "speech_findings",
]
