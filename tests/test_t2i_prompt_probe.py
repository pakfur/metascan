"""Tests for scripts/t2i_prompt_probe.py.

The probe judges a VLM's adherence to the prompt guidelines, which cannot be
unit-tested. What can be is everything around that judgement: the pure check
functions, run over hand-written model outputs with known faults, and the
script's plumbing, run against a throwaway HTTP server that answers with
hand-written replies. Nothing here touches a real model, the caption CSV or
the wildcard lists.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "t2i_prompt_probe.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("t2i_prompt_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["t2i_prompt_probe"] = module  # dataclasses look the module up
    spec.loader.exec_module(module)
    return module


probe = _load()

MODELS = ("krea2", "qwen", "sd", "zimage")


def words(count: int, word: str = "word") -> str:
    return " ".join([word] * count)


class TestWordCount(unittest.TestCase):
    def test_counts_whitespace_separated_words(self) -> None:
        self.assertEqual(probe.word_count(""), 0)
        self.assertEqual(probe.word_count("   \n "), 0)
        self.assertEqual(probe.word_count("one"), 1)
        self.assertEqual(probe.word_count("a calm  beach,\nat dawn."), 5)

    def test_the_bands_cover_each_guidelines_stated_range(self) -> None:
        # The guidelines live in data/meta_prompt.yml. If one changes its
        # target length this fails and the band must be looked at again.
        from metascan.core.prompt_store import get_prompt_store

        store = get_prompt_store()
        stated = {
            "krea2": ("META_KREA2", 90, 150),
            "qwen": ("META_QWEN", 30, 70),
            "sd": ("META_SDXL", 60, 130),
            "zimage": ("META_ZIMAGE", 50, 90),
        }
        self.assertEqual(sorted(probe.WORD_BANDS), sorted(stated))
        for model, (key, low, high) in stated.items():
            with self.subTest(model=model):
                self.assertRegex(store.get(key), rf"{low}\s*-\s*{high}\s+words")
                band_low, band_high = probe.WORD_BANDS[model]
                self.assertLessEqual(band_low, low)
                self.assertGreaterEqual(band_high, high)
                # ...but a band that is far too loose would check nothing.
                self.assertGreaterEqual(band_low, low * 0.6)
                self.assertLessEqual(band_high, high * 1.3)


class TestCheckWordCount(unittest.TestCase):
    def test_inside_the_band_passes(self) -> None:
        for model in MODELS:
            low, high = probe.WORD_BANDS[model]
            for count in (low, (low + high) // 2, high):
                with self.subTest(model=model, count=count):
                    check = probe.check_word_count(model, words(count))
                    self.assertTrue(check.ok, check.detail)
                    self.assertEqual(check.name, "word count")
                    self.assertIn(str(count), check.detail)

    def test_outside_the_band_fails_and_says_which_way(self) -> None:
        for model in MODELS:
            low, high = probe.WORD_BANDS[model]
            short = probe.check_word_count(model, words(low - 1))
            long_ = probe.check_word_count(model, words(high + 1))
            self.assertFalse(short.ok)
            self.assertIn("short", short.detail)
            self.assertFalse(long_.ok)
            self.assertIn("long", long_.detail)

    def test_an_unknown_model_is_an_error(self) -> None:
        with self.assertRaises(KeyError):
            probe.check_word_count("flux9", "a prompt")


class TestCheckNegative(unittest.TestCase):
    def test_models_with_a_negative_need_one_and_a_clean_prompt(self) -> None:
        for model in ("qwen", "sd"):
            with self.subTest(model=model):
                self.assertTrue(
                    probe.check_negative(model, "A calm beach.", "blurry, watermark").ok
                )
                self.assertFalse(probe.check_negative(model, "A calm beach.", None).ok)
                self.assertFalse(probe.check_negative(model, "A calm beach.", "  ").ok)
                left = probe.check_negative(
                    model, "A calm beach.\nNegative: blurry", "blurry, watermark"
                )
                self.assertFalse(left.ok)
                self.assertIn("Negative:", left.detail)

    def test_models_without_a_negative_must_not_show_one(self) -> None:
        for model in ("krea2", "zimage"):
            with self.subTest(model=model):
                self.assertTrue(probe.check_negative(model, "A calm beach.", None).ok)
                given = probe.check_negative(model, "A calm beach.", "blurry")
                self.assertFalse(given.ok)
                marker = probe.check_negative(
                    model, "A calm beach. Negative: blurry", None
                )
                self.assertFalse(marker.ok)

    def test_the_marker_is_found_at_the_start_of_a_line_in_any_case(self) -> None:
        for prompt in (
            "Negative: x",
            "A beach.\n\nnegative: x",
            "A beach.\r\nNEGATIVE:x",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(probe.check_negative("qwen", prompt, "y").ok)
        # The word alone, mid-sentence, is prose, not the block.
        self.assertTrue(probe.check_negative("qwen", "A negative space, calm.", "y").ok)


class TestCheckNoParentheses(unittest.TestCase):
    def test_both_texts_are_checked(self) -> None:
        self.assertTrue(probe.check_no_parentheses("A beach.", "blurry").ok)
        self.assertTrue(probe.check_no_parentheses("A beach.", None).ok)
        for prompt, negative in (
            ("A (very) calm beach.", None),
            ("A calm beach)", None),
            ("(A calm beach", None),
            ("A calm beach.", "(blurry:1.3), text"),
        ):
            with self.subTest(prompt=prompt, negative=negative):
                check = probe.check_no_parentheses(prompt, negative)
                self.assertFalse(check.ok)
                self.assertEqual(check.name, "no parentheses")


class TestCheckNoLeftoverTokens(unittest.TestCase):
    def test_clean_text_passes(self) -> None:
        self.assertTrue(probe.check_no_leftover_tokens("A woman named Alice.", None).ok)
        self.assertTrue(
            probe.check_no_leftover_tokens("snake_case_word and a_b", "x").ok
        )

    def test_tokens_in_either_text_fail_and_are_named(self) -> None:
        cases = [
            ("__ALICE__ walks.", None, "__ALICE__"),
            ("She combs her __HAIR__brush.", None, "__HAIR__"),
            ("A fine ____BREASTS____ top.", None, "____BREASTS____"),
            ("A beach.", "blurry, __EMMA__", "__EMMA__"),
        ]
        for prompt, negative, token in cases:
            with self.subTest(token=token):
                check = probe.check_no_leftover_tokens(prompt, negative)
                self.assertFalse(check.ok)
                self.assertIn(token, check.detail)


class TestCheckTraitsKept(unittest.TestCase):
    RESOLVED = (
        "A 34-year-old Latina woman with olive skin, green eyes, a soft round "
        "face, auburn hair and an athletic build walks along a beach."
    )
    CHARACTERS = {
        "ALICE": {
            "age": "34-year-old",
            "ethnicity": "Latina",
            "skin": "olive skin",
            "eyes": "green eyes",
            "face": "a soft round face",
            "hair": "auburn hair",
            "body": "an athletic build",
        }
    }

    def check(self, prompt: str, resolved: Optional[str] = None) -> Any:
        return probe.check_traits_kept(
            resolved if resolved is not None else self.RESOLVED, self.CHARACTERS, prompt
        )

    def test_every_drawn_trait_present_verbatim_passes(self) -> None:
        prompt = (
            "A 34-year-old Latina woman with olive skin, green eyes, a soft round "
            "face, auburn hair and an athletic build strolls along a beach at dawn."
        )
        check = self.check(prompt)
        self.assertTrue(check.ok, check.detail)
        self.assertEqual(check.name, "traits kept")

    def test_matching_ignores_case_and_line_breaks(self) -> None:
        prompt = (
            "a 34-YEAR-OLD latina woman, OLIVE SKIN, green\neyes, a soft round face, "
            "Auburn Hair, an athletic build."
        )
        self.assertTrue(self.check(prompt).ok)

    def test_a_dropped_leading_article_is_tolerated(self) -> None:
        prompt = "34-year-old Latina woman, olive skin, green eyes, soft round face, auburn hair, athletic build."
        self.assertTrue(self.check(prompt).ok)

    def test_a_paraphrased_trait_fails_and_is_listed(self) -> None:
        prompt = (
            "A 34-year-old Latina woman with olive skin, emerald eyes, a soft round "
            "face, auburn-haired and an athletic build."
        )
        check = self.check(prompt)
        self.assertFalse(check.ok)
        self.assertIn("green eyes", check.detail)
        self.assertIn("auburn hair", check.detail)
        self.assertNotIn("olive skin", check.detail)

    def test_only_traits_the_resolved_caption_actually_wrote_are_required(self) -> None:
        # The caption spells hair out itself: the engine left it out of the
        # intro, so the intro's traits are all that must survive.
        resolved = "A 34-year-old Latina woman with olive skin brushes her long hair."
        check = self.check(
            "A 34-year-old Latina woman, olive skin, brushing long hair.", resolved
        )
        self.assertTrue(check.ok, check.detail)

    def test_several_characters(self) -> None:
        characters = {
            "ALICE": {"hair": "auburn hair", "eyes": "green eyes"},
            "ADAM": {"hair": "dark blonde hair", "eyes": "grey eyes"},
        }
        resolved = "A woman with green eyes and auburn hair meets a man with grey eyes and dark blonde hair."
        good = "A woman with green eyes and auburn hair meets a man with grey eyes and dark blonde hair."
        bad = "A woman with green eyes and auburn hair meets a man with grey eyes."
        self.assertTrue(probe.check_traits_kept(resolved, characters, good).ok)
        failed = probe.check_traits_kept(resolved, characters, bad)
        self.assertFalse(failed.ok)
        self.assertIn("dark blonde hair", failed.detail)

    def test_nothing_drawn_means_nothing_to_keep(self) -> None:
        check = probe.check_traits_kept("A lighthouse at dusk.", {}, "Anything at all.")
        self.assertTrue(check.ok)
        self.assertIn("no traits", check.detail)

    def test_the_same_phrase_for_two_characters_is_asked_for_once(self) -> None:
        characters = {"A": {"hair": "red hair"}, "B": {"hair": "red hair"}}
        check = probe.check_traits_kept(
            "Two people with red hair.", characters, "Two redheads."
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.detail.count("red hair"), 1)


class TestCheckPhrasesKept(unittest.TestCase):
    def test_every_phrase_must_survive(self) -> None:
        prompt = "Low angle on a woman in a Red Raincoat leaning on a rusted railing, 35mm lens."
        self.assertTrue(probe.check_phrases_kept(prompt, ("red raincoat", "35mm")).ok)
        missing = probe.check_phrases_kept(prompt, ("red raincoat", "harbour"))
        self.assertFalse(missing.ok)
        self.assertIn("harbour", missing.detail)
        self.assertNotIn("red raincoat", missing.detail)

    def test_no_phrases_is_a_pass(self) -> None:
        self.assertTrue(probe.check_phrases_kept("anything", ()).ok)


class TestCheckVlmUsed(unittest.TestCase):
    def test_a_fallback_prompt_is_not_a_model_output(self) -> None:
        self.assertTrue(probe.check_vlm_used([]).ok)
        self.assertTrue(probe.check_vlm_used(["no list for __BREASTS__"]).ok)
        for warning in (
            "VLM unavailable - used the resolved caption",
            "VLM failed (boom) - used the resolved caption",
        ):
            with self.subTest(warning=warning):
                check = probe.check_vlm_used(["another note", warning])
                self.assertFalse(check.ok)
                self.assertIn(warning, check.detail)


class TestCaptions(unittest.TestCase):
    def test_the_fixed_list_is_hand_written_and_well_formed(self) -> None:
        labels = [c.label for c in probe.CAPTIONS]
        self.assertGreaterEqual(len(labels), 5)
        self.assertEqual(len(labels), len(set(labels)))
        for case in probe.CAPTIONS:
            with self.subTest(case=case.label):
                self.assertTrue(case.text.strip())
                self.assertTrue(case.keep, "each caption names phrases to keep")
                for phrase in case.keep:
                    self.assertIn(phrase.lower(), case.text.lower())
        # A spread of the engine's cases: tokens, a caption with none.
        with_tokens = [c for c in probe.CAPTIONS if re.search(r"__[A-Z]+__", c.text)]
        self.assertGreaterEqual(len(with_tokens), 3)
        self.assertGreaterEqual(len(probe.CAPTIONS) - len(with_tokens), 1)
        self.assertTrue(any("__HAIR__" in c.text for c in probe.CAPTIONS))


GOOD = {
    "krea2": words(120, "calm"),
    "qwen": words(50, "calm"),
    "sd": words(90, "calm"),
    "zimage": words(70, "calm"),
}


class TestEvaluate(unittest.TestCase):
    CASE = probe.Case("demo", "__ALICE__ wears a red raincoat.", ("red raincoat",))
    CHARACTERS = {"ALICE": {"hair": "auburn hair"}}
    RESOLVED = "A woman with auburn hair wears a red raincoat."

    def evaluate(
        self,
        model: str,
        prompt: str,
        negative: Optional[str],
        warnings: Optional[List[str]] = None,
    ) -> List[Any]:
        return probe.evaluate(
            model,
            self.CASE,
            {
                "prompt": prompt,
                "negative": negative,
                "resolved_caption": self.RESOLVED,
                "warnings": warnings or [],
            },
            self.CHARACTERS,
        )

    def good_prompt(self, model: str) -> str:
        return f"auburn hair red raincoat {GOOD[model]}"

    def test_a_good_output_passes_every_check_for_every_model(self) -> None:
        for model in MODELS:
            with self.subTest(model=model):
                negative = "blurry, watermark" if model in ("qwen", "sd") else None
                checks = self.evaluate(model, self.good_prompt(model), negative)
                self.assertEqual(
                    [c.name for c in checks],
                    [
                        "vlm used",
                        "word count",
                        "negative block",
                        "no parentheses",
                        "no leftover tokens",
                        "traits kept",
                        "phrases kept",
                    ],
                )
                self.assertEqual([c for c in checks if not c.ok], [])

    def test_each_fault_fails_exactly_its_own_check(self) -> None:
        good = self.good_prompt("qwen")
        faults = {
            "vlm used": (
                good,
                "blurry",
                ["VLM unavailable - used the resolved caption"],
            ),
            "word count": ("auburn hair red raincoat", "blurry", []),
            "negative block": (good, None, []),
            "no parentheses": (good + " (soft light)", "blurry", []),
            "no leftover tokens": (good + " __ALICE__", "blurry", []),
            "traits kept": (good.replace("auburn hair", "red hair"), "blurry", []),
            "phrases kept": (good.replace("red raincoat", "coat"), "blurry", []),
        }
        for name, (prompt, negative, warnings) in faults.items():
            with self.subTest(fault=name):
                checks = self.evaluate("qwen", prompt, negative, warnings)
                self.assertEqual([c.name for c in checks if not c.ok], [name])


def report_lines(
    model: str, prompt: str, negative: Optional[str], checks: List[Any]
) -> str:
    return probe.format_result(
        probe.Case("demo", "text", ()),
        model,
        "A resolved caption.",
        prompt,
        negative,
        checks,
    )


class TestFormatting(unittest.TestCase):
    def test_a_result_shows_what_the_user_needs_to_read(self) -> None:
        checks = [
            probe.Check("word count", True, "50 words (band 25-80)"),
            probe.Check("traits kept", False, "missing: green eyes"),
        ]
        text = report_lines("qwen", "A prompt here.", "blurry", checks)
        self.assertIn("demo", text)
        self.assertIn("qwen", text)
        self.assertIn("A resolved caption.", text)
        self.assertIn("A prompt here.", text)
        self.assertIn("blurry", text)
        self.assertIn("3 words", text)  # the prompt's own word count
        self.assertIn("PASS  word count", text)
        self.assertIn("FAIL  traits kept: missing: green eyes", text)

    def test_a_missing_negative_is_shown_as_none(self) -> None:
        text = report_lines("krea2", "A prompt.", None, [])
        self.assertIn("negative: (none)", text)


class _Handler(BaseHTTPRequestHandler):
    """A throwaway server: answers /api/t2i/prompt and /api/t2i/captions/resolve
    with the canned replies its server object holds and records the requests."""

    def log_message(self, *args: Any) -> None:  # keep the test output quiet
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        server: Any = self.server
        # The request line as sent: http.server tidies a leading "//" in
        # self.path, which would hide a doubled slash from the tests.
        path = self.requestline.split()[1]
        server.requests.append((path, body, self.headers.get("Authorization")))
        status, payload = server.answer(path, body)
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FakeServer:
    def __init__(self) -> None:
        self.requests: List[Tuple[str, Dict[str, Any], Optional[str]]] = []
        self.prompt_reply: Any = self.default_prompt
        self.status = 200
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.requests = self.requests  # type: ignore[attr-defined]
        self._server.answer = self.answer  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "FakeServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)

    @staticmethod
    def default_prompt(model: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """A prompt every check passes for, whatever the caption."""
        caption = body["caption"]
        negative = "blurry, watermark" if model in ("qwen", "sd") else None
        return {
            "prompt": f"auburn hair green eyes {GOOD[model]} {CASE_KEEP[caption]}",
            "negative": negative,
            "resolved_caption": f"A woman with auburn hair and green eyes. {caption}",
            "warnings": [],
        }

    def answer(self, path: str, body: Dict[str, Any]) -> Tuple[int, Any]:
        if self.status != 200:
            return self.status, {"detail": "the VLM is unhappy"}
        if path.endswith("/captions/resolve"):
            return 200, {
                "resolved_caption": f"A woman with auburn hair and green eyes. {body['caption']}",
                "characters": {"ALICE": {"hair": "auburn hair", "eyes": "green eyes"}},
                "warnings": [],
            }
        if path.endswith("/prompt"):
            return 200, self.prompt_reply(body["model"], body)
        return 404, {"detail": "not found"}


# What each fixed caption asks the prompt to keep, joined, so the fake server can
# answer a passing prompt for any of them.
CASE_KEEP: Dict[str, str] = {c.text: " ".join(c.keep) for c in probe.CAPTIONS}


def run_main(argv: List[str]) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = probe.main(argv)
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def serving() -> Iterator[FakeServer]:
    server = FakeServer().start()
    try:
        yield server
    finally:
        server.stop()


class TestMain(unittest.TestCase):
    def test_a_clean_run_exits_zero_and_prints_every_result(self) -> None:
        with serving() as server:
            code, out, _ = run_main(
                ["--base-url", server.url, "--models", "krea2,qwen"]
            )
        self.assertEqual(code, 0, out)
        for case in probe.CAPTIONS:
            self.assertIn(case.label, out)
        self.assertEqual(out.count("PASS  word count"), 2 * len(probe.CAPTIONS))
        self.assertNotIn("FAIL", out)
        self.assertIn("negative: blurry, watermark", out)
        self.assertIn("negative: (none)", out)
        self.assertIn(f"{2 * len(probe.CAPTIONS)} of {2 * len(probe.CAPTIONS)}", out)

    def test_requests_carry_the_fixed_captions_the_seed_and_the_model(self) -> None:
        with serving() as server:
            run_main(["--base-url", server.url, "--models", "sd", "--seed", "77"])
        prompts = [
            (b["caption"], b["seed"], b["model"])
            for p, b, _ in server.requests
            if p == "/api/t2i/prompt"
        ]
        self.assertEqual(prompts, [(c.text, 77, "sd") for c in probe.CAPTIONS])
        resolves = [
            (b["caption"], b["seed"], b["model"])
            for p, b, _ in server.requests
            if p == "/api/t2i/captions/resolve"
        ]
        self.assertEqual(resolves, prompts)  # the drawn traits come from the same draw

    def test_the_default_seed_is_101_and_every_model_is_probed(self) -> None:
        with serving() as server:
            code, _, _ = run_main(["--base-url", server.url])
        self.assertEqual(code, 0)
        models = {b["model"] for p, b, _ in server.requests if p == "/api/t2i/prompt"}
        seeds = {b["seed"] for _, b, _ in server.requests}
        self.assertEqual(models, set(MODELS))
        self.assertEqual(seeds, {101})

    def test_only_selects_captions_by_label(self) -> None:
        label = probe.CAPTIONS[1].label
        with serving() as server:
            code, out, _ = run_main(
                ["--base-url", server.url, "--models", "krea2", "--only", label]
            )
        self.assertEqual(code, 0)
        sent = [b["caption"] for p, b, _ in server.requests if p == "/api/t2i/prompt"]
        self.assertEqual(sent, [probe.CAPTIONS[1].text])
        self.assertIn(label, out)

    def test_an_unknown_label_or_model_is_a_usage_error(self) -> None:
        with serving() as server:
            code, _, err = run_main(["--base-url", server.url, "--only", "nope"])
            self.assertEqual(code, 2)
            self.assertIn("nope", err)
            code, _, err = run_main(["--base-url", server.url, "--models", "flux9"])
            self.assertEqual(code, 2)
            self.assertIn("flux9", err)
            self.assertEqual(server.requests, [])  # nothing was sent

    def test_a_faulty_output_exits_one_and_names_the_check(self) -> None:
        def bad(model: str, body: Dict[str, Any]) -> Dict[str, Any]:
            reply = FakeServer.default_prompt(model, body)
            reply["prompt"] = "A (weighted) prompt. __ALICE__\nNegative: blurry"
            return reply

        with serving() as server:
            server.prompt_reply = bad
            code, out, _ = run_main(
                [
                    "--base-url",
                    server.url,
                    "--models",
                    "krea2",
                    "--only",
                    probe.CAPTIONS[0].label,
                ]
            )
        self.assertEqual(code, 1)
        for name in (
            "word count",
            "negative block",
            "no parentheses",
            "no leftover tokens",
            "traits kept",
        ):
            self.assertIn(f"FAIL  {name}", out)
        self.assertIn("0 of 1", out)

    def test_a_server_error_exits_one_and_shows_the_detail(self) -> None:
        with serving() as server:
            server.status = 502
            code, out, err = run_main(
                [
                    "--base-url",
                    server.url,
                    "--models",
                    "krea2",
                    "--only",
                    probe.CAPTIONS[0].label,
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("502", out + err)
        self.assertIn("the VLM is unhappy", out + err)

    def test_a_fallback_prompt_is_reported_as_a_failed_vlm(self) -> None:
        def fallback(model: str, body: Dict[str, Any]) -> Dict[str, Any]:
            reply = FakeServer.default_prompt(model, body)
            reply["warnings"] = ["VLM unavailable - used the resolved caption"]
            return reply

        with serving() as server:
            server.prompt_reply = fallback
            code, out, _ = run_main(
                [
                    "--base-url",
                    server.url,
                    "--models",
                    "krea2",
                    "--only",
                    probe.CAPTIONS[0].label,
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("FAIL  vlm used", out)

    def test_an_unreachable_server_exits_two(self) -> None:
        with serving() as server:
            url = server.url
        code, out, err = run_main(
            ["--base-url", url, "--models", "krea2", "--timeout", "2"]
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot reach", err)

    def test_the_api_key_is_sent_as_a_bearer_token(self) -> None:
        only = ["--models", "krea2", "--only", probe.CAPTIONS[0].label]
        with serving() as server:
            run_main(["--base-url", server.url, *only, "--api-key", "s3cret"])
        self.assertEqual({auth for _, _, auth in server.requests}, {"Bearer s3cret"})

    def test_the_key_defaults_to_the_servers_environment_variable(self) -> None:
        only = ["--models", "krea2", "--only", probe.CAPTIONS[0].label]
        with mock.patch.dict(os.environ, {"METASCAN_API_KEY": "fromenv"}):
            with serving() as server:
                run_main(["--base-url", server.url, *only])
        self.assertEqual({auth for _, _, auth in server.requests}, {"Bearer fromenv"})

    def test_no_key_sends_no_authorization_header(self) -> None:
        only = ["--models", "krea2", "--only", probe.CAPTIONS[0].label]
        with mock.patch.dict(os.environ):
            os.environ.pop("METASCAN_API_KEY", None)
            with serving() as server:
                run_main(["--base-url", server.url, *only])
        self.assertEqual({auth for _, _, auth in server.requests}, {None})

    def test_a_trailing_slash_on_the_base_url_is_fine(self) -> None:
        with serving() as server:
            code, _, _ = run_main(
                [
                    "--base-url",
                    server.url + "/",
                    "--models",
                    "krea2",
                    "--only",
                    probe.CAPTIONS[0].label,
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            {p for p, _, _ in server.requests},
            {"/api/t2i/prompt", "/api/t2i/captions/resolve"},
        )


class TestStandalone(unittest.TestCase):
    """The probe is for the user to run against a live server, from anywhere
    -- and must never read library data or caption rows itself."""

    def tree(self) -> ast.AST:
        return ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def imports(self) -> set:
        found = set()
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    def test_it_imports_only_the_standard_library(self) -> None:
        imported = self.imports()
        self.assertEqual(imported - set(sys.stdlib_module_names), set())
        self.assertFalse({"metascan", "backend", "numpy", "requests"} & imported)

    def test_it_reads_no_files_and_touches_no_database(self) -> None:
        self.assertFalse(
            {"csv", "sqlite3", "pathlib", "glob", "shutil", "tempfile"} & self.imports()
        )
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                )
                # (Reading an HTTP response body is not reading a file.)
                self.assertNotIn(
                    name,
                    ("open", "read_text", "read_bytes", "write_text", "write_bytes"),
                    ast.dump(node)[:80],
                )

    def test_it_runs_as_a_script(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        for option in (
            "--base-url",
            "--models",
            "--seed",
            "--only",
            "--api-key",
            "--timeout",
        ):
            self.assertIn(option, result.stdout)
        self.assertIn("http://127.0.0.1:8700", result.stdout)
        self.assertIn("101", result.stdout)


if __name__ == "__main__":
    unittest.main()
