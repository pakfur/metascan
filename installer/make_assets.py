"""Make the installer's icon and wizard pictures from icon.png.

python installer/make_assets.py <output folder>
"""

import sys
from pathlib import Path
from typing import Tuple

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
# Inno Setup's sizes at 100 % scale: the Welcome and Finished panel, and the
# corner of the other pages.
WIZARD_SIZE = (164, 314)
SMALL_SIZE = (58, 58)
PANEL_TOP = (232, 243, 248)
PANEL_BOTTOM = (205, 228, 236)


def _trimmed(icon: Image.Image) -> Image.Image:
    box = icon.getbbox()
    return icon.crop(box) if box else icon


def _wizard_panel(icon: Image.Image, scale: int) -> Image.Image:
    size: Tuple[int, int] = (WIZARD_SIZE[0] * scale, WIZARD_SIZE[1] * scale)
    panel = Image.new("RGB", size)
    for y in range(size[1]):
        mix = y / max(1, size[1] - 1)
        colour = tuple(
            round(a + (b - a) * mix) for a, b in zip(PANEL_TOP, PANEL_BOTTOM)
        )
        panel.paste(colour, (0, y, size[0], y + 1))
    picture = _trimmed(icon)
    width = int(size[0] * 0.72)
    picture = picture.resize(
        (width, round(picture.height * width / picture.width)), Image.LANCZOS
    )
    panel.paste(picture, ((size[0] - picture.width) // 2, int(size[1] * 0.14)), picture)
    return panel


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ROOT / "icon.png").convert("RGBA")
    icon.save(out_dir / "metascan.ico", sizes=ICON_SIZES)
    _wizard_panel(icon, 2).save(out_dir / "wizard.bmp")

    small = Image.new("RGB", (SMALL_SIZE[0] * 2, SMALL_SIZE[1] * 2), "white")
    picture = _trimmed(icon).resize(small.size, Image.LANCZOS)
    small.paste(picture, (0, 0), picture)
    small.save(out_dir / "wizard-small.bmp")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
