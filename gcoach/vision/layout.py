"""Screen layout: where UI elements are, as fractions of the game viewport."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Rect = tuple[int, int, int, int]


@dataclass
class Layout:
    data: dict[str, Any]

    @staticmethod
    def load(path: Path) -> "Layout":
        return Layout(json.loads(path.read_text(encoding="utf-8")))

    @property
    def calibrated(self) -> bool:
        return bool(self.data.get("calibrated"))

    def side(self, name: str) -> dict[str, Any]:
        return self.data["sides"][name]

    @staticmethod
    def to_px(frac: list[float], w: int, h: int, ox: int = 0, oy: int = 0) -> Rect:
        x, y, fw, fh = frac
        return ox + int(round(x * w)), oy + int(round(y * h)), max(1, int(round(fw * w))), max(1, int(round(fh * h)))

    @staticmethod
    def part(card: Rect, rel: list[float]) -> Rect:
        """Rect relative to a card rect (fractions of the card size)."""
        x, y, w, h = card
        rx, ry, rw, rh = rel
        return x + int(rx * w), y + int(ry * h), max(1, int(rw * w)), max(1, int(rh * h))

    def card_rect(self, side: str, slot: int, w: int, h: int, shifted: bool = False) -> Rect:
        s = self.side(side)
        x, y, fw, fh = s["cards"][slot]
        if shifted:
            y += s.get("active_shift", 0.0)
        return self.to_px([x, y, fw, fh], w, h)
