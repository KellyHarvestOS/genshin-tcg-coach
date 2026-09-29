"""Render digit templates 0-9 from Genshin Impact's own UI font (read locally from the game).

    python -m gcoach.tools.make_digits [--src "<Genshin Impact folder>"]

The numbers on the board (HP drops, costs) use the game's font, so glyphs rendered from it
match far better than a generic OCR. Output: assets/ui/digits/0.png .. 9.png (white on black).
The font is not redistributed; the templates stay on this computer (see .gitignore).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .extract_font import find_font

OUT = Path(__file__).resolve().parents[2] / "assets" / "ui" / "digits"


def render(font_path: Path, out: Path = OUT, size: int = 96) -> int:
    font = ImageFont.truetype(str(font_path), size)
    out.mkdir(parents=True, exist_ok=True)
    for d in "0123456789":
        im = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(im).text((size // 4, size // 8), d, fill=255, font=font)
        arr = np.array(im)
        ys, xs = np.where(arr > 127)
        Image.fromarray(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]).save(out / f"{d}.png")
    return 10


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", help="Genshin Impact install folder")
    args = ap.parse_args(argv)
    font = find_font(args.src)
    if font is None:
        raise SystemExit("Шрифт игры не найден. Укажите папку игры: --src \"D:\\Games\\Genshin Impact\"")
    print(f"font: {font}\ndigits: {render(font)} -> {OUT}")


if __name__ == "__main__":
    main()
