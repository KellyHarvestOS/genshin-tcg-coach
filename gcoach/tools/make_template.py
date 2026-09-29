"""Cut a template out of a screenshot and add it to the template library.

    python -m gcoach.tools.make_template shot.png --category characters --name "Дилюк" --rect 700,620,160,200
    python -m gcoach.tools.make_template shot.png --category ui/digits --name 7 --rect 690,610,18,26

Categories: characters, cards, summons, statuses, icons, dice, ui, ui/digits.
The file name must equal the knowledge-base id (e.g. the Russian card name from the wiki).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from ..config import ROOT


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("screenshot")
    ap.add_argument("--category", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--rect", required=True, help="x,y,w,h in screenshot pixels")
    args = ap.parse_args(argv)
    img = cv2.imdecode(np.fromfile(args.screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("cannot read screenshot")
    x, y, w, h = (int(v) for v in args.rect.split(","))
    cut = img[y:y + h, x:x + w]
    folder = ROOT / "assets" / args.category
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{args.name}.png"
    ok, buf = cv2.imencode(".png", cut)
    buf.tofile(str(path))
    print(f"saved {path} ({w}x{h})")


if __name__ == "__main__":
    main()
