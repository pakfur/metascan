"""The metadata extractors read a PNG's text and EXIF without decoding it.

Pillow decodes the whole image the moment a PNG's ``text`` or ``getexif()``
is read. The extractors did that three times per import (the Fooocus check,
the ComfyUI check and the ComfyUI extract) on top of the scanner's own read.
``metascan/utils/png_chunks.py`` reads the chunks Pillow parsed at open, and
decodes only a file with text or EXIF after its image data. The scanner's
decode stays: it alone decides whether a PNG with no eXIf before its image
data imports, so such a PNG imports exactly when Pillow can decode it
(``test_a_png_imports_exactly_when_*``).
"""

from __future__ import annotations

import io
import json
import os
import struct
import time
import zlib
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import numpy as np
import pytest
from PIL import Image, ImageFile, PngImagePlugin

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
from metascan.extractors.comfyui import ComfyUIExtractor
from metascan.utils import png_chunks
from metascan.utils.png_chunks import (
    PNG_SIGNATURE,
    metadata_after_data,
    png_exif,
    png_text,
)

PROMPT_TEXT = "a lighthouse on a sea cliff at dusk"


@pytest.fixture(autouse=True)
def _fresh_walks():
    png_chunks._metadata_after_data.cache_clear()
    yield
    png_chunks._metadata_after_data.cache_clear()


@pytest.fixture
def decodes(monkeypatch) -> Dict[str, int]:
    """Counts Pillow's real decodes: ImageFile.load with tiles left to read
    (PNG text, getexif, seek and convert all decode through it)."""
    count = {"n": 0}
    original = ImageFile.ImageFile.load

    def counting(self):  # type: ignore[no-untyped-def]
        if getattr(self, "tile", None):
            count["n"] += 1
        return original(self)

    monkeypatch.setattr(ImageFile.ImageFile, "load", counting)
    return count


@pytest.fixture
def scanner() -> Scanner:
    s = Scanner(None)  # type: ignore[arg-type]
    s.extractor_manager.parsing_logger = None
    return s


def _api_prompt() -> Dict[str, object]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "test_model.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 64, "height": 64, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": PROMPT_TEXT, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "t", "images": ["8", 0]},
        },
    }


def _comfy_png(path: Path, size: int = 64, noise: bool = False) -> Path:
    """A PNG the way ComfyUI saves one: prompt and workflow tEXt chunks
    before the image data. ``noise`` makes it incompressible, so the image
    data spans several IDAT chunks."""
    rng = np.random.default_rng(size)
    if noise:
        arr = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    else:
        arr = np.full((size, size, 3), 120, dtype=np.uint8)
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(_api_prompt()))
    info.add_text("workflow", json.dumps({"nodes": [], "links": []}))
    Image.fromarray(arr, "RGB").save(path, pnginfo=info, compress_level=4)
    return path


def _split(data: bytes) -> List[Tuple[bytes, bytes]]:
    assert data[:8] == PNG_SIGNATURE
    chunks, pos = [], 8
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        chunks.append((kind, data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
    return chunks


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I4s", len(body), kind)
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


def _join(chunks: List[Tuple[bytes, bytes]]) -> bytes:
    return PNG_SIGNATURE + b"".join(_chunk(k, b) for k, b in chunks)


def _with_after_data(path: Path, kind: bytes, body: bytes) -> Path:
    """The same PNG with one more chunk after its image data."""
    chunks = _split(path.read_bytes())
    assert chunks[-1][0] == b"IEND"
    path.write_bytes(_join(chunks[:-1] + [(kind, body), chunks[-1]]))
    return path


def _with_before_data(path: Path, extra: List[Tuple[bytes, bytes]]) -> Path:
    """The same PNG with ``extra`` chunks just before its first IDAT."""
    chunks = _split(path.read_bytes())
    first = next(i for i, (kind, _) in enumerate(chunks) if kind == b"IDAT")
    path.write_bytes(_join(chunks[:first] + extra + chunks[first:]))
    return path


def _exif_body(**tags: object) -> bytes:
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[int(tag.lstrip("t"))] = value
    raw = exif.tobytes()
    return raw[6:] if raw.startswith(b"Exif\x00\x00") else raw


def _itxt(key: str, text: str) -> bytes:
    return key.encode() + b"\x00\x00\x00\x00\x00" + text.encode("utf-8")


def _ztxt(key: str, text: str) -> bytes:
    return key.encode() + b"\x00\x00" + zlib.compress(text.encode("latin-1"))


def _idat_spans(data: bytes) -> List[Tuple[int, int]]:
    """(start of the chunk, end of its CRC) for each IDAT chunk."""
    spans, pos = [], 8
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if kind == b"IDAT":
            spans.append((pos, pos + 12 + length))
        pos += 12 + length
    return spans


# --- noticing text or EXIF after the image data ------------------------------


def test_a_png_saved_like_comfyui_has_nothing_after_its_data(tmp_path):
    assert metadata_after_data(_comfy_png(tmp_path / "a.png")) is False


@pytest.mark.parametrize(
    "kind, body, after",
    [
        (b"tEXt", b"late\x00x", True),
        (b"iTXt", _itxt("late", "x"), True),
        (b"zTXt", _ztxt("late", "x"), True),
        (b"eXIf", _exif_body(t271="LateCam"), True),
        (b"tIME", b"\x07" * 7, False),
    ],
)
def test_a_chunk_after_the_image_data_is_noticed_only_if_it_holds_metadata(
    tmp_path, kind, body, after
):
    p = _with_after_data(_comfy_png(tmp_path / "a.png"), kind, body)
    assert metadata_after_data(p) is after


def test_the_walk_never_raises_on_odd_files(tmp_path):
    odd = {
        "not_png": b"definitely not a png",
        "empty": b"",
        "signature_only": PNG_SIGNATURE,
    }
    data = _comfy_png(tmp_path / "src.png").read_bytes()
    last = _idat_spans(data)[-1][1]
    odd["huge_length_after_data"] = data[:last] + struct.pack(">I4s", 2**31, b"tIME")
    for name, content in odd.items():
        (tmp_path / f"{name}.png").write_bytes(content)
        assert metadata_after_data(tmp_path / f"{name}.png") is False, name
    assert metadata_after_data(tmp_path / "missing.png") is False


def test_a_file_replaced_with_the_same_size_and_mtime_is_walked_again(tmp_path):
    # Same length on purpose: a 7-byte tIME body against a 7-byte tEXt body.
    p = _with_after_data(_comfy_png(tmp_path / "a.png"), b"tIME", b"\x07" * 7)
    before = os.stat(p)
    assert metadata_after_data(p) is False

    replacement = _with_after_data(
        _comfy_png(tmp_path / "b.png"), b"tEXt", b"late\x00ab"
    )
    for _ in range(400):
        p.write_bytes(replacement.read_bytes())
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))
        # ctime moves on the filesystem's own clock tick, which can be
        # coarser than this test is quick (ext4 before Linux 6.13).
        if os.stat(p).st_ctime_ns != before.st_ctime_ns:
            break
        time.sleep(0.005)
    assert os.stat(p).st_ctime_ns != before.st_ctime_ns
    assert os.stat(p).st_size == before.st_size
    assert os.stat(p).st_mtime_ns == before.st_mtime_ns

    assert metadata_after_data(p) is True


def test_the_walk_is_remembered(tmp_path):
    p = _comfy_png(tmp_path / "a.png")
    metadata_after_data(p)
    metadata_after_data(p)
    assert png_chunks._metadata_after_data.cache_info().hits == 1


# --- reading text and EXIF ----------------------------------------------------


def test_png_text_is_pillows_text_without_decoding(tmp_path, decodes):
    p = _with_before_data(
        _comfy_png(tmp_path / "a.png"),
        [
            (b"zTXt", _ztxt("ztext", "compressed text")),
            (b"iTXt", _itxt("itext", "intl text ü")),
            # Keys Pillow keeps in .text but not as text in .info: a tEXt
            # "exif" (info keeps it as bytes) and a text key a later chunk
            # of another kind reuses (gAMA writes info["gamma"]).
            (b"tEXt", b"exif\x00as text"),
            (b"tEXt", b"gamma\x00as text"),
            (b"gAMA", struct.pack(">I", 45455)),
            (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
        ],
    )
    with Image.open(p) as img:
        fast = png_text(img)
    assert decodes["n"] == 0
    with Image.open(p) as img:
        assert fast == dict(img.text)
    assert fast["exif"] == "as text" and fast["gamma"] == "as text"
    assert "dpi" not in fast


def test_text_and_exif_after_the_data_are_still_read(tmp_path):
    p = _with_after_data(
        _comfy_png(tmp_path / "a.png"), b"tEXt", b"late\x00after the image data"
    )
    with Image.open(p) as img:
        assert png_text(img)["late"] == "after the image data"

    q = _with_after_data(
        _comfy_png(tmp_path / "b.png"), b"eXIf", _exif_body(t271="LateCam")
    )
    with Image.open(q) as img:
        assert png_exif(img).get(271) == "LateCam"


def test_exif_before_the_data_is_read_without_decoding(tmp_path, decodes):
    p = tmp_path / "cam.png"
    exif = Image.Exif()
    exif[0x010F] = "EarlyCam"
    Image.new("RGB", (32, 32), (9, 9, 9)).save(p, exif=exif.tobytes())

    with Image.open(p) as img:
        assert png_exif(img).get(0x010F) == "EarlyCam"
    assert decodes["n"] == 0


def test_the_extractor_helper_reads_a_png_without_decoding(tmp_path, decodes):
    p = _comfy_png(tmp_path / "a.png")
    metadata = ComfyUIExtractor()._get_exif_metadata(p)

    assert decodes["n"] == 0
    assert json.loads(metadata["prompt"]) == _api_prompt()
    with Image.open(p) as img:
        assert metadata == dict(img.text)


def test_the_extractor_helper_still_finds_text_after_the_data(tmp_path):
    p = _with_after_data(
        _comfy_png(tmp_path / "a.png"), b"tEXt", b"late\x00after the image data"
    )
    assert ComfyUIExtractor()._get_exif_metadata(p)["late"] == "after the image data"


def test_text_is_read_the_decoding_way_when_the_parsed_text_is_not_found(
    tmp_path, monkeypatch, decodes
):
    p = _comfy_png(tmp_path / "a.png")
    monkeypatch.setattr(png_chunks, "_text_parsed_at_open", lambda img: None)
    with Image.open(p) as img:
        text = png_text(img)
    assert decodes["n"] == 1
    with Image.open(p) as img:
        assert text == dict(img.text)


def test_an_image_opened_from_memory_is_read_the_decoding_way(tmp_path):
    data = _with_after_data(
        _comfy_png(tmp_path / "a.png"), b"tEXt", b"late\x00after the image data"
    ).read_bytes()
    with Image.open(io.BytesIO(data)) as img:
        assert png_text(img)["late"] == "after the image data"


def test_a_failed_read_is_not_remembered(tmp_path, monkeypatch):
    p = _with_after_data(_comfy_png(tmp_path / "a.png"), b"tEXt", b"late\x00x")

    def no_more_files(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(png_chunks, "open", no_more_files, raising=False)
    assert metadata_after_data(p) is False
    monkeypatch.undo()
    assert metadata_after_data(p) is True


# --- the import -----------------------------------------------------------------


def test_importing_a_comfyui_png_decodes_it_once(tmp_path, scanner, decodes):
    p = _comfy_png(tmp_path / "a.png", size=96)

    media = scanner._process_media_file(p)

    assert decodes["n"] == 1  # the scanner's own: the import check
    assert media is not None
    assert (media.width, media.height, media.format) == (96, 96, "PNG")
    assert media.metadata_source == "ComfyUI"
    assert PROMPT_TEXT in (media.prompt or "")


def test_an_animated_png_imports_with_one_decode(tmp_path, scanner, decodes):
    p = tmp_path / "anim.png"
    frames = [Image.new("RGB", (40, 30), (i * 60, 0, 0)) for i in range(3)]
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(_api_prompt()))
    frames[0].save(p, save_all=True, append_images=frames[1:], pnginfo=info)

    media = scanner._process_media_file(p)

    assert decodes["n"] == 1
    assert media is not None and (media.width, media.height) == (40, 30)
    assert PROMPT_TEXT in (media.prompt or "")


def _damaged_variants(src: Path, out: Path) -> Dict[str, bytes]:
    """Ways a render can be damaged, and the whole file."""
    data = src.read_bytes()
    spans = _idat_spans(data)
    assert len(spans) >= 2, "the variants need image data in several IDAT chunks"
    (first_start, first_end), (second_start, _) = spans[0], spans[1]
    last_end = spans[-1][1]
    chunks = _split(data)
    idat_indexes = [i for i, (kind, _) in enumerate(chunks) if kind == b"IDAT"]

    def edit(fn: Callable[[bytearray], None]) -> bytes:
        b = bytearray(data)
        fn(b)
        return bytes(b)

    def flip(at: int) -> Callable[[bytearray], None]:
        def f(b: bytearray) -> None:
            b[at] ^= 0xFF

        return f

    def rename_second_idat(b: bytearray) -> None:
        b[second_start + 7] = ord("t")

    without_one_idat = [c for i, c in enumerate(chunks) if i != idat_indexes[1]]
    return {
        "whole": data,
        "cut_inside_the_image_data": data[: (first_start + first_end) // 2],
        "cut_right_after_the_first_idat": data[:first_end],
        # The last IDAT ends with the zlib Adler-32 and the chunk CRC.
        "cut_inside_the_zlib_trailer": data[: last_end - 6],
        "cut_just_before_the_zlib_trailer": data[: last_end - 9],
        "no_iend": data[:last_end],
        "cut_inside_iend": data[: last_end + 6],
        "a_data_byte_flipped": edit(flip(first_start + 8 + 100)),
        "only_a_crc_flipped": edit(flip(first_end - 2)),
        "second_idat_renamed": edit(rename_second_idat),
        "zeros_from_the_second_idat_on": data[:second_start]
        + bytes(len(data) - second_start),
        "an_idat_removed": _join(without_one_idat),
    }


def test_a_png_imports_exactly_when_pillow_can_decode_it(tmp_path, scanner):
    src = _comfy_png(tmp_path / "src.png", size=256, noise=True)
    for name, content in _damaged_variants(src, tmp_path).items():
        p = tmp_path / f"{name}.png"
        p.write_bytes(content)
        try:
            with Image.open(p) as img:
                img.load()
            decodes_ok = True
        except Exception:
            decodes_ok = False

        imported = scanner._process_media_file(p) is not None

        assert imported == decodes_ok, name
    # The set is only a test if it has both outcomes.
    assert scanner._process_media_file(tmp_path / "whole.png") is not None
    assert (
        scanner._process_media_file(tmp_path / "cut_inside_the_image_data.png") is None
    )


def test_a_broken_png_is_not_ingested(tmp_path):
    db = DatabaseManager(tmp_path / "db")
    try:
        data = _comfy_png(tmp_path / "src.png", size=256, noise=True).read_bytes()
        cut = tmp_path / "cut.png"
        cut.write_bytes(data[: len(data) // 2])

        assert Scanner(db).ingest_file(cut) is None
        assert db.get_media(cut) is None
    finally:
        db.close()
