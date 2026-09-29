"""Find character cards on the board by their artwork.

The wiki card art (downloaded by `python -m gcoach.tools.wiki_images`) is matched against
the screenshot, so the cards are located and identified at once. This works for any
number of characters per side (NPC duels have 1-2) and for the shifted active card, so no
fixed per-slot layout is needed. Proportions are measured on the 16:9 game board.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

log = logging.getLogger("gcoach.vision.cards")

CARD_H = 0.259            # character card height / game height
CARD_ASPECT = 164 / 280   # width / height
INNER = (0.05, 0.04, 0.90, 0.92)  # part of the wiki image compared (x, y, w, h), skips its frame
WORK_H = 360              # frames are matched at this height (speed)
MIN_SCORE = 0.78


@dataclass
class FoundCard:
    id: str
    score: float
    rect: tuple[int, int, int, int]  # full card (x, y, w, h) in frame pixels

    @property
    def cx(self) -> float:
        return self.rect[0] + self.rect[2] / 2

    @property
    def cy(self) -> float:
        return self.rect[1] + self.rect[3] / 2


class CardFinder:
    def __init__(self, img_dir: Path, ids: Iterable[str]):
        self.img_dir = img_dir
        index_file = img_dir / "index.json"
        index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
        self.files = {cid: img_dir / index[cid] for cid in ids if cid in index}
        self._art: dict[str, np.ndarray] = {}
        self._cache: dict[tuple[int, int], dict[str, np.ndarray]] = {}

    @property
    def available(self) -> bool:
        return bool(self.files)

    def _load(self, cid: str) -> np.ndarray | None:
        if cid not in self._art:
            im = cv2.imread(str(self.files[cid]), cv2.IMREAD_COLOR)
            if im is None:
                return None
            h, w = im.shape[:2]
            x, y, iw, ih = INNER
            self._art[cid] = im[int(h * y):int(h * (y + ih)), int(w * x):int(w * (x + iw))]
        return self._art[cid]

    def _templates(self, tw: int, th: int) -> dict[str, np.ndarray]:
        key = (tw, th)
        if key not in self._cache:
            out = {}
            for cid in self.files:
                art = self._load(cid)
                if art is not None:
                    out[cid] = cv2.resize(art, (tw, th), interpolation=cv2.INTER_AREA)
            self._cache = {key: out}  # one size at a time (the window size rarely changes)
        return self._cache[key]

    def warm(self, game_h: int, game_w: int) -> None:
        """Prepare the templates for this window size ahead of the first frame."""
        s = WORK_H / game_h
        card_h = CARD_H * game_h
        self._templates(round(card_h * CARD_ASPECT * INNER[2] * s), round(card_h * INNER[3] * s))

    # ------------------------------------------------------------------------------------
    def find(self, game: np.ndarray, candidates: Iterable[str] | None = None) -> list[FoundCard]:
        t0 = time.perf_counter()
        gh, gw = game.shape[:2]
        s = WORK_H / gh
        small = cv2.resize(game, (round(gw * s), WORK_H), interpolation=cv2.INTER_AREA)
        card_h = CARD_H * gh
        th, tw = round(card_h * INNER[3] * s), round(card_h * CARD_ASPECT * INNER[2] * s)
        tpls = self._templates(tw, th)
        ids = [c for c in (candidates or tpls) if c in tpls] or list(tpls)
        peaks: list[tuple[float, str, int, int]] = []
        for cid in ids:
            res = cv2.matchTemplate(small, tpls[cid], cv2.TM_CCOEFF_NORMED)
            for _ in range(3):  # the same character can appear on both sides (mirror matches)
                _, mx, _, (x, y) = cv2.minMaxLoc(res)
                if mx < MIN_SCORE:
                    break
                peaks.append((float(mx), cid, x, y))
                res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1
        peaks.sort(reverse=True)
        kept: list[tuple[float, str, int, int]] = []
        for p in peaks:  # one card per place: the best-scoring character wins
            if all(abs(p[2] - k[2]) > tw * 0.6 or abs(p[3] - k[3]) > th * 0.6 for k in kept):
                kept.append(p)
        full_w, full_h = card_h * CARD_ASPECT, card_h
        out = []
        for score, cid, x, y in kept:
            fx = x / s - full_w * INNER[0]
            fy = y / s - full_h * INNER[1]
            out.append(FoundCard(cid, round(score, 3), (round(fx), round(fy), round(full_w), round(full_h))))
        log.debug("card finder: %d cards in %d ms", len(out), (time.perf_counter() - t0) * 1000)
        return out


def split_rows(cards: list[FoundCard], game_h: int) -> tuple[list[FoundCard], list[FoundCard]]:
    """(player row, opponent row), each ordered left to right."""
    player = sorted((c for c in cards if c.cy >= game_h * 0.5), key=lambda c: c.cx)
    opponent = sorted((c for c in cards if c.cy < game_h * 0.5), key=lambda c: c.cx)
    return player, opponent


def active_in_row(row: list[FoundCard], game_h: int, towards_centre: int) -> tuple[int | None, float]:
    """Index of the card pushed towards the board centre (the active character).
    towards_centre: -1 for the player's row (moves up), +1 for the opponent's (moves down)."""
    if len(row) < 2:
        return None, 0.0
    ys = [c.rect[1] for c in row]
    base = float(np.median(ys))
    shifts = [(y - base) * towards_centre for y in ys]
    k = int(np.argmax(shifts))
    others = [v for i, v in enumerate(shifts) if i != k]
    if shifts[k] > game_h * 0.012 and all(v < game_h * 0.006 for v in others):
        return k, min(0.97, 0.7 + shifts[k] / (game_h * 0.05))
    return None, 0.0
