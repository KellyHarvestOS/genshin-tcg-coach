"""Draw the current screen layout over a screenshot so it can be calibrated.

    python -m gcoach.tools.calibrate screenshot.png [--layout assets/layouts/default_16x9.json]

Writes `<screenshot>_layout.jpg` with every region outlined and prints what the
recogniser reads from each region. Adjust the fractions in the layout JSON until
the boxes sit on the HP badges, energy pips, dice counters etc., then set
"calibrated": true.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from ..config import load_config
from ..ocr.ocr import OCREngine
from ..vision.layout import Layout
from ..vision.recognizer import Recognizer
from ..vision.templates import TemplateLibrary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("screenshot")
    ap.add_argument("--layout")
    args = ap.parse_args(argv)
    cfg = load_config()
    layout = Layout.load(Path(args.layout) if args.layout else cfg.path(cfg.LAYOUT))
    img = cv2.imdecode(np.fromfile(args.screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("cannot read screenshot")
    rec = Recognizer(layout, TemplateLibrary(cfg.path("assets")),
                     OCREngine(cfg.OCR_ENABLED, cfg.OCR_ENGINE, cfg.path("assets/ui/digits"), cfg.TESSERACT_CMD))
    result = rec.recognize(img)
    out = Path(args.screenshot).with_name(Path(args.screenshot).stem + "_layout.jpg")
    ok, buf = cv2.imencode(".jpg", result.annotated)
    buf.tofile(str(out))
    print(f"annotated image: {out}")
    for r in result.regions:
        print(f"{r['name']:<28} value={json.dumps(r['value'], ensure_ascii=False):<20} conf={r['confidence']}")


if __name__ == "__main__":
    main()
