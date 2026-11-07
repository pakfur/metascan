"""ffprobe and exiftool write UTF-8; a text-mode subprocess without an
explicit encoding decodes with the locale code page (cp1252 on Windows),
and a ComfyUI comment tag with one non-cp1252 byte (0x8f, as in U+200F)
then crashed subprocess's reader thread: the probe came back empty and the
video was stored without frame rate, duration or length.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, List
from unittest import mock

from metascan.utils import ffmpeg_utils

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("ffprobe", "exiftool")
CALLS = {"run", "Popen", "check_output", "call", "check_call"}

# What ffprobe prints for a ComfyUI clip whose comment holds text that is not
# cp1252 (U+200F right-to-left mark is E2 80 8F; e-acute is C3 A9).
_PROBE_JSON = '{"format": {"tags": {"comment": "caf\\u00e9 \\u200f"}}, "streams": []}'
# e-acute and the right-to-left mark, spelled as code points.
EXPECTED = "caf" + chr(0xE9) + " " + chr(0x200F)
_CHILD = (
    "import sys, json\n"
    f"sys.stdout.buffer.write(json.dumps(json.loads({_PROBE_JSON!r}), "
    "ensure_ascii=False).encode('utf-8'))\n"
)


class TestProbeDecodesUtf8(unittest.TestCase):
    def test_a_utf8_comment_that_is_not_cp1252_comes_back(self) -> None:
        real_run = subprocess.run

        def fake_ffprobe(cmd: List[str], **kwargs: Any) -> Any:
            # The real call, its options untouched; only the program differs.
            return real_run([sys.executable, "-c", _CHILD], **kwargs)

        with mock.patch.object(
            ffmpeg_utils.subprocess, "run", side_effect=fake_ffprobe
        ):
            probe = ffmpeg_utils.probe_with_timeout("clip.mp4")
        assert probe is not None
        self.assertEqual(probe["format"]["tags"]["comment"], EXPECTED)


def _tool_files() -> List[Path]:
    out = []
    for base in ("metascan", "backend"):
        for path in sorted((ROOT / base).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if any(tool in text for tool in TOOLS):
                out.append(path)
    return out


def _keyword(call: ast.Call, name: str) -> Any:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


class TestToolCallsNameTheirEncoding(unittest.TestCase):
    def test_every_text_mode_call_in_a_tool_file_passes_an_encoding(self) -> None:
        offenders = []
        files = _tool_files()
        self.assertTrue(any(p.name == "ffmpeg_utils.py" for p in files))
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                if name not in CALLS:
                    continue
                text_mode = (
                    _keyword(node, "text") is True
                    or _keyword(node, "universal_newlines") is True
                )
                has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
                if text_mode and not has_encoding:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(
            offenders,
            [],
            "text=True without encoding= decodes ffprobe / exiftool output with "
            "the locale code page; pass encoding='utf-8', errors='replace'",
        )


if __name__ == "__main__":
    unittest.main()
