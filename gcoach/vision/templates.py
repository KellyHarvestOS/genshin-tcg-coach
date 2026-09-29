"""Extensible template library.

Drop PNG files into assets/<category>/ to teach the recogniser new objects, no
code changes needed:

    assets/characters/Дилюк.png      -> id "Дилюк" (file stem = knowledge-base id)
    assets/cards/Стратег.png
    assets/summons/Оз.png
    assets/statuses/..., assets/dice/omni.png, assets/ui/end_round.png  (assets/icons is UI artwork, not templates)

An optional sidecar `<name>.json` may override {"id": "...", "threshold": 0.8}.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("gcoach.vision.templates")

# assets/icons holds UI artwork (app icon, generated icons) and is not a template category
CATEGORIES = ("characters", "cards", "statuses", "dice", "ui", "summons")


@dataclass
class Template:
    id: str
    category: str
    image: np.ndarray  # grayscale
    threshold: float | None = None


@dataclass
class Match:
    id: str | None
    score: float
    location: tuple[int, int] | None = None


class TemplateLibrary:
    def __init__(self, root: Path, default_threshold: float = 0.78):
        self.root = root
        self.default_threshold = default_threshold
        self.templates: dict[str, list[Template]] = {c: [] for c in CATEGORIES}
        self.reload()

    def reload(self) -> None:
        for cat in CATEGORIES:
            self.templates[cat] = []
            folder = self.root / cat
            if not folder.exists():
                continue
            for p in sorted(folder.glob("*.png")):
                img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)  # unicode-safe
                if img is None:
                    continue
                meta = {}
                side = p.with_suffix(".json")
                if side.exists():
                    meta = json.loads(side.read_text(encoding="utf-8"))
                self.templates[cat].append(Template(meta.get("id", p.stem), cat, img, meta.get("threshold")))
        counts = {c: len(v) for c, v in self.templates.items() if v}
        log.info("templates loaded: %s", counts or "none")

    def count(self, category: str) -> int:
        return len(self.templates.get(category, []))

    def best_match(self, region: np.ndarray, category: str, scales=(1.0, 0.9, 1.1, 0.8, 1.25)) -> Match:
        """Best template of `category` inside `region` (BGR or gray)."""
        if region is None or region.size == 0 or not self.templates.get(category):
            return Match(None, 0.0)
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
        best = Match(None, 0.0)
        for t in self.templates[category]:
            for s in scales:
                tpl = t.image
                # scale template relative to region height so templates captured at other resolutions still work
                target_h = int(gray.shape[0] * s) if tpl.shape[0] > gray.shape[0] else int(tpl.shape[0] * s)
                if target_h < 8:
                    continue
                tw = max(8, int(tpl.shape[1] * target_h / tpl.shape[0]))
                if tw > gray.shape[1] or target_h > gray.shape[0]:
                    continue
                resized = cv2.resize(tpl, (tw, target_h), interpolation=cv2.INTER_AREA)
                res = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                if score > best.score:
                    best = Match(t.id, float(score), loc)
        thr = self.default_threshold
        if best.id is not None:
            tdef = next((t for t in self.templates[category] if t.id == best.id), None)
            thr = tdef.threshold or thr if tdef else thr
            if best.score < thr:
                return Match(None, best.score)
        return best

    def save_template(self, category: str, name: str, image: np.ndarray) -> Path:
        folder = self.root / category
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        ok, buf = cv2.imencode(".png", image)
        if ok:
            buf.tofile(str(path))
        self.reload()
        return path
