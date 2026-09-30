#!/usr/bin/env python3
"""Guideline-adherence probe for the t2i prompt writer.

A VLM's adherence to a prompt guideline cannot be unit-tested, so this script
puts it in front of you. For each model it sends a FIXED list of hand-written
captions (below, with the character and hair tokens the caption engine
expands) through a running server's POST /api/t2i/prompt, and prints what came
back -- the resolved caption, the prompt, the negative -- with a few
mechanical checks:

  vlm used            the server really asked a model (not its fallback prompt)
  word count          inside a band around the model's guideline length
  negative block      a negative prompt exactly where the model takes one, and
                      no "Negative:" line left in the prompt
  no parentheses      ComfyUI reads them as weights
  no leftover tokens  no __TOKEN__ survived
  traits kept         every drawn trait phrase the resolved caption wrote
                      (age, skin, eyes, hair, build ...) appears word for word
  phrases kept        the caption's own key nouns and camera cues survive

Read the output as well as the verdicts: the checks are cheap and blunt.

Usage:
    python scripts/t2i_prompt_probe.py [--base-url URL] [--models krea2,qwen]
                                       [--seed 101] [--only LABEL[,LABEL]]

Standard library only, and it reads no library data or caption files: the
captions are in this file and everything else comes from the server. Exit
codes: 0 every result passed; 1 a check failed or the server answered with an
error; 2 the server could not be reached or the arguments are unusable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DEFAULT_BASE_URL = "http://127.0.0.1:8700"
DEFAULT_SEED = 101

# model id -> whether the model takes a negative prompt. Mirrors the profiles
# in metascan/core/t2i_models.py; this script imports nothing from metascan.
MODELS: Dict[str, bool] = {
    "krea2": False,
    "qwen": True,
    "sd": True,
    "zimage": False,
}

# Allowed prompt length in words. Each band is the range the model's guideline
# in data/meta_prompt.yml asks for, widened a little: krea2 90-150, qwen 30-70,
# sd 60-130, zimage 50-90 (hard ceiling 110).
WORD_BANDS: Dict[str, Tuple[int, int]] = {
    "krea2": (80, 165),
    "qwen": (25, 80),
    "sd": (50, 145),
    "zimage": (40, 110),
}


@dataclass(frozen=True)
class Case:
    label: str
    text: str
    # Short phrases of the caption that must survive into the prompt.
    keep: Tuple[str, ...]


# Hand-written. A spread of what the engine has to cope with: one character
# and a later mention, a man and a woman with pronouns, a fused token, three
# characters, camera cues, and a caption with no tokens at all.
CAPTIONS: Tuple[Case, ...] = (
    Case(
        "single character",
        "__ALICE__ sits on a wooden bench in a sunlit garden, her shoulder-length "
        "wavy __HAIR__ moving in the breeze. She holds a paper cup in both hands "
        "while __ALICE__ smiles at the camera.",
        ("wooden bench", "paper cup", "sunlit garden"),
    ),
    Case(
        "man and woman",
        "__ADAM__ and __CLARA__ walk along a beach at dusk. His short __HAIR__ is "
        "damp and her __HAIR__ is tied back. __ADAM__ carries a surfboard.",
        ("beach", "surfboard"),
    ),
    Case(
        "hair and hairbrush",
        "A close-up of __ALICE__'s hands as she combs her __HAIR__ with a "
        "__HAIR__brush, one forearm showing fine body __HAIR__.",
        ("close-up", "forearm"),
    ),
    Case(
        "three friends",
        "__ALICE__, __BELLA__ and __EMMA__ share a table in a busy cafe, laughing "
        "over coffee while rain runs down the window behind them.",
        ("cafe", "coffee", "rain"),
    ),
    Case(
        "camera cues",
        "Low-angle shot of __BELLA__ leaning on a rusted railing above a harbour, "
        "wearing a red raincoat, shot on a 35mm lens with shallow depth of field.",
        ("low-angle", "rusted railing", "red raincoat", "35mm"),
    ),
    Case(
        "no tokens",
        "A lighthouse on a rocky coast at dusk, waves breaking against the cliffs "
        "and a single gull crossing the pink sky.",
        ("lighthouse", "rocky coast", "gull"),
    ),
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


# ---- the checks: pure functions over text -----------------------------------

_NEGATIVE_MARKER = re.compile(r"\bnegative\s*:", re.IGNORECASE)
_TOKEN = re.compile(r"(?<!_)_{2,}[A-Z][A-Z0-9]*_{2,}")
_LEADING_ARTICLE = re.compile(r"^(?:a|an|the)\s+")


def word_count(text: str) -> int:
    return len(text.split())


def _norm(text: str) -> str:
    """Lower-cased with every run of whitespace (line breaks too) as one space."""
    return " ".join(text.lower().split())


def check_word_count(model: str, prompt: str) -> Check:
    low, high = WORD_BANDS[model]  # KeyError for a model the probe does not know
    count = word_count(prompt)
    band = f"band {low}-{high}"
    if count < low:
        return Check("word count", False, f"{count} words: too short ({band})")
    if count > high:
        return Check("word count", False, f"{count} words: too long ({band})")
    return Check("word count", True, f"{count} words ({band})")


def check_negative(model: str, prompt: str, negative: Optional[str]) -> Check:
    name = "negative block"
    marker = bool(_NEGATIVE_MARKER.search(prompt))
    if MODELS[model]:
        if marker:
            return Check(name, False, "the prompt still contains a 'Negative:' line")
        if not negative or not negative.strip():
            return Check(
                name, False, "no negative prompt came back (this model takes one)"
            )
        terms = len([t for t in negative.split(",") if t.strip()])
        return Check(name, True, f"negative of {terms} terms")
    if negative:
        return Check(
            name, False, "a negative prompt came back but this model takes none"
        )
    if marker:
        return Check(
            name,
            False,
            "the prompt contains a 'Negative:' block but this model takes none",
        )
    return Check(name, True, "none, as expected")


def check_no_parentheses(prompt: str, negative: Optional[str]) -> Check:
    name = "no parentheses"
    found = [
        label
        for label, text in (("prompt", prompt), ("negative", negative or ""))
        if "(" in text or ")" in text
    ]
    if found:
        return Check(name, False, "parentheses in the " + " and ".join(found))
    return Check(name, True)


def check_no_leftover_tokens(prompt: str, negative: Optional[str]) -> Check:
    name = "no leftover tokens"
    tokens = _TOKEN.findall(prompt) + _TOKEN.findall(negative or "")
    if tokens:
        return Check(name, False, "left over: " + ", ".join(dict.fromkeys(tokens)))
    return Check(name, True)


def check_traits_kept(
    resolved_caption: str,
    characters: Mapping[str, Mapping[str, str]],
    prompt: str,
) -> Check:
    """Every drawn trait phrase that the resolved caption wrote must be in the
    prompt word for word (case, spacing and a leading "a"/"an" aside)."""
    name = "traits kept"
    caption, text = _norm(resolved_caption), _norm(prompt)
    required: Dict[str, str] = {}  # normalised phrase -> phrase as drawn
    for slots in characters.values():
        for value in slots.values():
            phrase = _norm(value)
            if phrase and phrase in caption:
                required.setdefault(phrase, value)
    if not required:
        return Check(name, True, "no traits were drawn for this caption")
    missing = [
        value
        for phrase, value in required.items()
        if _LEADING_ARTICLE.sub("", phrase) not in text
    ]
    if missing:
        return Check(name, False, "missing: " + ", ".join(missing))
    return Check(name, True, f"all {len(required)} present")


def check_phrases_kept(prompt: str, phrases: Sequence[str]) -> Check:
    name = "phrases kept"
    text = _norm(prompt)
    missing = [p for p in phrases if _norm(p) not in text]
    if missing:
        return Check(name, False, "missing: " + ", ".join(missing))
    return Check(name, True, f"all {len(phrases)} present")


def check_vlm_used(warnings: Sequence[str]) -> Check:
    """A prompt the server wrote itself (no model, or the model failed) says
    nothing about a guideline."""
    name = "vlm used"
    for warning in warnings:
        if warning.startswith(("VLM unavailable", "VLM failed")):
            return Check(name, False, warning)
    return Check(name, True)


def evaluate(
    model: str,
    case: Case,
    reply: Mapping[str, Any],
    characters: Mapping[str, Mapping[str, str]],
) -> List[Check]:
    """Every check for one model's answer to one caption."""
    prompt = str(reply.get("prompt") or "")
    negative = reply.get("negative")
    return [
        check_vlm_used(list(reply.get("warnings") or [])),
        check_word_count(model, prompt),
        check_negative(model, prompt, negative),
        check_no_parentheses(prompt, negative),
        check_no_leftover_tokens(prompt, negative),
        check_traits_kept(str(reply.get("resolved_caption") or ""), characters, prompt),
        check_phrases_kept(prompt, case.keep),
    ]


def format_result(
    case: Case,
    model: str,
    resolved_caption: str,
    prompt: str,
    negative: Optional[str],
    checks: Sequence[Check],
) -> str:
    lines = [
        f"== {case.label} | {model} ==",
        f"resolved: {resolved_caption}",
        f"prompt ({word_count(prompt)} words): {prompt}",
        f"negative: {negative if negative else '(none)'}",
    ]
    for check in checks:
        detail = f": {check.detail}" if check.detail else ""
        lines.append(f"  {'PASS' if check.ok else 'FAIL'}  {check.name}{detail}")
    return "\n".join(lines)


# ---- talking to the server ---------------------------------------------------


class ApiError(Exception):
    """The server answered, with an error status (or too slowly)."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class Unreachable(Exception):
    """Nothing is listening at the base URL."""


def post_json(
    base_url: str, path: str, body: Mapping[str, Any], api_key: str, timeout: float
) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload: Dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return payload
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = str(json.loads(text).get("detail", text))
        except (ValueError, AttributeError):
            detail = text
        raise ApiError(f"HTTP {exc.code}", detail[:500]) from exc
    except urllib.error.URLError as exc:
        raise Unreachable(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise ApiError("timeout", f"no answer within {timeout:g} s") from exc


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe how well the t2i prompt writer follows each model's guideline."
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="the server (default: %(default)s)"
    )
    parser.add_argument(
        "--models",
        default=",".join(MODELS),
        help="comma-separated model ids (default: %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=(
            "seed sent with each request; the characters follow each caption's "
            "text, so it does not change them (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated caption labels to run (default: all of them)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("METASCAN_API_KEY", ""),
        help="bearer token (default: $METASCAN_API_KEY, if set)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds to wait for each answer; a model may need to load (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in MODELS]
    if unknown:
        print(
            f"unknown model(s): {', '.join(unknown)}; known: {', '.join(MODELS)}",
            file=sys.stderr,
        )
        return 2
    cases = list(CAPTIONS)
    if args.only.strip():
        wanted = [label.strip() for label in args.only.split(",") if label.strip()]
        known = {case.label for case in CAPTIONS}
        missing = [label for label in wanted if label not in known]
        if missing:
            print(
                f"unknown caption label(s): {', '.join(missing)}; known: "
                + ", ".join(sorted(known)),
                file=sys.stderr,
            )
            return 2
        cases = [case for case in CAPTIONS if case.label in wanted]

    results: List[bool] = []
    for case in cases:
        for model in models:
            body = {"caption": case.text, "seed": args.seed, "model": model}
            try:
                resolved = post_json(
                    args.base_url,
                    "/api/t2i/captions/resolve",
                    body,
                    args.api_key,
                    args.timeout,
                )
                reply = post_json(
                    args.base_url, "/api/t2i/prompt", body, args.api_key, args.timeout
                )
            except Unreachable as exc:
                print(f"cannot reach {args.base_url}: {exc}", file=sys.stderr)
                return 2
            except ApiError as exc:
                print(
                    f"== {case.label} | {model} ==\n  FAIL  request: {exc}", flush=True
                )
                results.append(False)
                continue
            checks = evaluate(model, case, reply, resolved.get("characters") or {})
            print(
                format_result(
                    case,
                    model,
                    str(reply.get("resolved_caption") or ""),
                    str(reply.get("prompt") or ""),
                    reply.get("negative"),
                    checks,
                ),
                flush=True,
            )
            results.append(all(check.ok for check in checks))
    passed = sum(results)
    print(f"\n{passed} of {len(results)} results passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
