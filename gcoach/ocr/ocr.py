"""OCR layer for the numbers the coach needs (HP, energy, dice, costs, usages, round).

Backends:
  * tesseract  - pytesseract + tesseract.exe (if installed)
  * templates  - digit templates from assets/ui/digits/0.png..9.png (capture them from
                 your own screenshots with `python -m gcoach.tools.make_template`)
  * none
`auto` picks tesseract when available, otherwise templates.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("gcoach.ocr")

GLYPH_SIZE = (20, 28)  # w, h


# ----------------------------------------------------------------------------------------
# preprocessing
# ----------------------------------------------------------------------------------------
def preprocess(img: np.ndarray, scale: float = 3.0, invert: bool | None = None) -> np.ndarray:
    """grayscale → resize → denoise → sharpen → Otsu threshold (white text on black)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21) if gray.size < 400_000 else gray
    blur = cv2.GaussianBlur(gray, (0, 0), 1.2)
    sharp = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)
    _, bw = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if invert is None:
        invert = bw.mean() > 127  # make text white on black
    if invert:
        bw = 255 - bw
    return bw


def crop(img: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    H, W = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    return img[y0:y1, x0:x1]


# ----------------------------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------------------------
def parse_int(text: str) -> int | None:
    m = re.search(r"\d+", text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1"))
    return int(m.group()) if m else None


def parse_fraction(text: str) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


@dataclass
class OCRResult:
    text: str
    value: int | None
    confidence: float
    engine: str


# ----------------------------------------------------------------------------------------
# engines
# ----------------------------------------------------------------------------------------
class TemplateDigitOCR:
    name = "templates"

    def __init__(self, digits_dir: Path | None = None, templates: dict[str, np.ndarray] | None = None):
        self.templates: dict[str, np.ndarray] = templates or {}
        if not self.templates and digits_dir and digits_dir.exists():
            for p in sorted(digits_dir.glob("*.png")):
                ch = p.stem[:1]
                if ch.isdigit() or ch == "/":
                    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        self.templates.setdefault(ch, self._norm(preprocess(img, 1.0)))

    @property
    def available(self) -> bool:
        return len(self.templates) >= 10

    @staticmethod
    def _norm(glyph: np.ndarray) -> np.ndarray:
        ys, xs = np.where(glyph > 0)
        if len(xs):
            glyph = glyph[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        return cv2.resize(glyph, GLYPH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0

    @classmethod
    def from_font(cls) -> "TemplateDigitOCR":
        """Synthetic templates rendered with an OpenCV font (tests / bootstrap only)."""
        tpls = {}
        for d in "0123456789":
            canvas = np.zeros((60, 44), np.uint8)
            cv2.putText(canvas, d, (4, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 255, 4, cv2.LINE_AA)
            tpls[d] = cls._norm(canvas)
        return cls(templates=tpls)

    def read(self, img: np.ndarray) -> OCRResult:
        bw = preprocess(img, 2.0)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        h_img = bw.shape[0]
        blobs = [stats[i] for i in range(1, n) if stats[i][cv2.CC_STAT_HEIGHT] > h_img * 0.35
                 and stats[i][cv2.CC_STAT_AREA] > 20]
        blobs.sort(key=lambda s: s[cv2.CC_STAT_LEFT])
        text, scores = "", []
        for s in blobs:
            x, y, w, h = s[:4]
            glyph = self._norm(bw[y:y + h, x:x + w])
            best, best_score = "?", -1.0
            for ch, tpl in self.templates.items():
                score = float(cv2.matchTemplate(glyph, tpl, cv2.TM_CCOEFF_NORMED)[0][0])
                if score > best_score:
                    best, best_score = ch, score
            text += best
            scores.append(best_score)
        conf = max(0.0, min(scores)) if scores else 0.0
        return OCRResult(text, parse_int(text), conf, self.name)


class BadgeDigitReader:
    """Reads the numbers on Genius Invokation badges (the HP drop): the cream digit fill is
    separated from the golden badge by colour, the badge rim is dropped by position, and each
    glyph is matched against digits rendered from the game's own font (assets/ui/digits,
    made by `python -m gcoach.tools.make_digits`)."""
    name = "badge"
    MIN_SCORE = 0.55

    def __init__(self, digits_dir: Path | None = None):
        self.templates: dict[str, np.ndarray] = {}
        if digits_dir and digits_dir.exists():
            for p in sorted(digits_dir.glob("[0-9].png")):
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.templates[p.stem] = TemplateDigitOCR._norm((img > 127).astype(np.uint8) * 255)

    @property
    def available(self) -> bool:
        return len(self.templates) == 10

    def read(self, img: np.ndarray) -> OCRResult:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 190), (180, 80, 255))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
        H, W = mask.shape
        found = []
        for i in range(1, n):
            x, y, w, h, area = st[i]
            if not (H * 0.28 < h < H * 0.9) or area < 25 or not (W * 0.15 < x + w / 2 < W * 0.85):
                continue
            glyph = TemplateDigitOCR._norm(mask[y:y + h, x:x + w])
            scores = {d: float(cv2.matchTemplate(glyph, t, cv2.TM_CCOEFF_NORMED)[0][0]) for d, t in self.templates.items()}
            best = max(scores, key=scores.get)
            if scores[best] >= self.MIN_SCORE:
                found.append((x, best, scores[best]))
        found.sort()
        text = "".join(d for _, d, _ in found)
        conf = min((s for _, _, s in found), default=0.0)
        return OCRResult(text, parse_int(text), conf, self.name)


class TesseractOCR:
    name = "tesseract"

    def __init__(self, cmd: str = ""):
        self.ok = False
        try:
            import pytesseract

            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            elif shutil.which("tesseract") is None:
                default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
                if default.exists():
                    pytesseract.pytesseract.tesseract_cmd = str(default)
                else:
                    return
            pytesseract.get_tesseract_version()
            self.pt = pytesseract
            self.ok = True
        except Exception:
            self.ok = False

    @property
    def available(self) -> bool:
        return self.ok

    def read(self, img: np.ndarray) -> OCRResult:
        bw = preprocess(img, 3.0)
        data = self.pt.image_to_data(255 - bw, config="--psm 7 -c tessedit_char_whitelist=0123456789/",
                                     output_type=self.pt.Output.DICT)
        words = [(t, float(c)) for t, c in zip(data["text"], data["conf"]) if t.strip()]
        text = "".join(t for t, _ in words)
        conf = min((c for _, c in words), default=0.0) / 100.0
        return OCRResult(text, parse_int(text), max(0.0, conf), self.name)


class OCREngine:
    def __init__(self, enabled: bool = True, engine: str = "auto", digits_dir: Path | None = None,
                 tesseract_cmd: str = ""):
        self.enabled = enabled
        self.backend = None
        self.badge = BadgeDigitReader(digits_dir) if enabled else None
        if not enabled or engine == "none":
            return
        if engine in ("auto", "tesseract"):
            t = TesseractOCR(tesseract_cmd)
            if t.available:
                self.backend = t
        if self.backend is None and engine in ("auto", "templates"):
            t = TemplateDigitOCR(digits_dir)
            if t.available:
                self.backend = t
        log.info("OCR backend: %s", self.backend.name if self.backend else "none")

    @property
    def name(self) -> str:
        return self.backend.name if self.backend else "none"

    def read_badge(self, img: np.ndarray) -> OCRResult:
        """Number on a board badge (HP drop); falls back to the generic backend."""
        if self.badge is not None and self.badge.available and img is not None and img.size:
            try:
                return self.badge.read(img)
            except Exception as exc:
                log.debug("badge OCR failed: %s", exc)
        return self.read_number(img)

    def read_number(self, img: np.ndarray) -> OCRResult:
        if self.backend is None or img is None or img.size == 0:
            return OCRResult("", None, 0.0, "none")
        try:
            return self.backend.read(img)
        except Exception as exc:
            log.debug("OCR failed: %s", exc)
            return OCRResult("", None, 0.0, self.name)
