"""Use the Genshin Impact UI font from YOUR local game installation in the coach UI.

    python -m gcoach.tools.extract_font            # auto-detect the game folder
    python -m gcoach.tools.extract_font --src "D:\\Games\\Genshin Impact"

The game ships its UI font (SDK_SC_Web, with full Cyrillic) as
`...\\GenshinImpact_Data\\StreamingAssets\\MiHoYoSDKRes\\HttpServerResources\\font\\zh-cn.ttf`.
This tool subsets it to Latin + Cyrillic + punctuation and writes
`gcoach/web/fonts/genshin.woff2`. The font belongs to HoYoverse: it is used only
locally and is not part of the repository (see .gitignore).
"""
from __future__ import annotations

import argparse
import string
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parents[1] / "web" / "fonts" / "genshin.woff2"
REL = Path("GenshinImpact_Data/StreamingAssets/MiHoYoSDKRes/HttpServerResources/font/zh-cn.ttf")
SEARCH_ROOTS = [r"C:\Program Files\Epic Games", r"C:\Program Files", r"C:\Program Files (x86)",
                r"D:\\", r"E:\\", r"C:\Games", r"D:\Games"]


def find_font(src: str | None) -> Path | None:
    roots = [Path(src)] if src else [Path(r) for r in SEARCH_ROOTS]
    for root in roots:
        if not root.exists():
            continue
        for game_dir in [root, *root.glob("*Genshin*"), *root.glob("*/*Genshin*"), *root.glob("*/*/*Genshin*")]:
            for cand in (game_dir / REL, *game_dir.glob(f"*/{REL.as_posix()}"), *game_dir.glob(f"*/*/{REL.as_posix()}")):
                if cand.exists():
                    return cand
    return None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", help="Genshin Impact install folder")
    args = ap.parse_args(argv)
    font_path = find_font(args.src)
    if font_path is None:
        raise SystemExit("Шрифт игры не найден. Укажите папку игры: --src \"путь\\к\\Genshin Impact\"")
    chars = (string.printable + "".join(chr(c) for c in range(0x0400, 0x0460))
             + "«»—–…№•·→←↑↓×÷°±€₽✕✓★☆◆◇●○")
    font = TTFont(str(font_path))
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    sub = subset.Subsetter(options)
    sub.populate(text=chars)
    sub.subset(font)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.save(str(OUT))
    print(f"source: {font_path}\nsaved:  {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
