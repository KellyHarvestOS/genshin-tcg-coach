"""Computer-vision recogniser: screenshot -> ObservedGameState (every field with confidence).

It only reports what it can actually see. Anything it cannot read is returned as
UNKNOWN (confidence 0) - reconstruction decides how to fill it (history, manual
match setup) and the coach refuses to advise when critical data is missing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..core.enums import DieType
from ..core.observed import Observed, ObservedCharacter, ObservedGameState, ObservedSide
from ..ocr.ocr import OCREngine, crop
from ..screen_capture.capture import detect_game_area
from .cards import CardFinder, FoundCard, active_in_row, split_rows
from .layout import Layout, Rect
from .templates import TemplateLibrary

log = logging.getLogger("gcoach.vision")

# HSV centres (OpenCV: H 0-180) of element colours; also used by the synthetic test renderer.
ELEMENT_HSV = {
    "pyro": (5, 200, 230), "geo": (22, 200, 230), "dendro": (48, 200, 210), "anemo": (80, 180, 210),
    "cryo": (92, 110, 240), "hydro": (108, 210, 230), "electro": (142, 170, 220),
}
OMNI_HSV = (0, 0, 245)
ENERGY_HSV = (27, 190, 245)


def hsv_to_bgr(hsv: tuple[int, int, int]) -> tuple[int, int, int]:
    px = np.uint8([[list(hsv)]])
    b, g, r = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0][0]
    return int(b), int(g), int(r)


def classify_color(hsv_mean: tuple[float, float, float]) -> tuple[str | None, float]:
    h, s, v = hsv_mean
    if v < 70:
        return None, 0.0
    if s < 45 and v > 190:
        return "omni", min(1.0, (v - 190) / 50 + 0.5)
    if s < 70:
        return None, 0.0
    best, best_d = None, 1e9
    for name, (ch, cs, cv) in ELEMENT_HSV.items():
        dh = min(abs(h - ch), 180 - abs(h - ch))
        d = dh * 3 + abs(s - cs) * 0.15 + abs(v - cv) * 0.05
        if d < best_d:
            best, best_d = name, d
    conf = max(0.0, 1.0 - best_d / 45)
    return (best, conf) if conf > 0.2 else (None, 0.0)


@dataclass
class Recognition:
    observed: ObservedGameState
    regions: list[dict[str, Any]] = field(default_factory=list)
    game_area: Rect = (0, 0, 0, 0)
    annotated: np.ndarray | None = None


class Recognizer:
    def __init__(self, layout: Layout, templates: TemplateLibrary, ocr: OCREngine,
                 finder: CardFinder | None = None):
        self.layout = layout
        self.templates = templates
        self.ocr = ocr
        self.finder = finder
        self.candidates: list[str] | None = None  # character ids from the match setup (faster search)

    # ------------------------------------------------------------------------------------
    def recognize(self, frame: np.ndarray, annotate: bool = True) -> Recognition:
        gx, gy, gw, gh = detect_game_area(frame)
        game = frame[gy:gy + gh, gx:gx + gw]
        obs = ObservedGameState()
        regions: list[dict[str, Any]] = []

        def reg(name: str, rect: Rect, value: Any, conf: float) -> None:
            regions.append({"name": name, "rect": [rect[0] + gx, rect[1] + gy, rect[2], rect[3]],
                            "value": value, "confidence": round(conf, 2)})

        cards = []
        if self.finder and self.finder.available:
            cards = self.finder.find(game, self.candidates)
            if self.candidates and len(cards) < 2:  # other characters on the board: search everyone
                cards = self.finder.find(game)
        if cards:  # cards located by their artwork: positions follow the real board
            rows = split_rows(cards, gh)
            obs.player = self._side_from_cards(game, rows[0], -1, "player", reg)
            obs.opponent = self._side_from_cards(game, rows[1], 1, "opponent", reg)
        else:
            for side_name in ("player", "opponent"):
                side = self._side(game, side_name, gw, gh, reg)
                setattr(obs, side_name, side)

        r = self.layout.to_px(self.layout.data["round"], gw, gh)
        res = self.ocr.read_number(crop(game, r))
        if res.value is not None and 1 <= res.value <= 15 and res.confidence > 0.5:
            obs.round = Observed.known(res.value, res.confidence, "ocr:round")
        reg("round", r, obs.round.value, obs.round.confidence)

        # turn / phase from UI templates (optional)
        t = self.layout.to_px(self.layout.data["turn_indicator"], gw, gh)
        m = self.templates.best_match(crop(game, t), "ui")
        if m.id in ("turn_player", "turn_opponent"):
            obs.active_player = Observed.known("player" if m.id == "turn_player" else "opponent", m.score, "template:ui")
        reg("turn", t, obs.active_player.value, obs.active_player.confidence)
        if obs.player.characters:
            obs.phase = Observed.inferred("action", 0.6, "board visible")

        rec = Recognition(obs, regions, (gx, gy, gw, gh))
        if annotate:
            rec.annotated = self.annotate(frame, regions)
        return rec

    # ------------------------------------------------------------------------------------
    def _side_from_cards(self, game: np.ndarray, row: list[FoundCard], towards: int, side_name: str,
                         reg) -> ObservedSide:
        """Characters of one side from cards found by their art (HP drop, energy and aura are
        read at fixed proportions of the found card)."""
        side = ObservedSide()
        k, conf = active_in_row(row, game.shape[0], towards)
        if k is not None:
            side.active_index = Observed.known(k, conf, "vision:card_shift")
        elif len(row) == 1:
            side.active_index = Observed.inferred(0, 0.7, "single character")
        for i, c in enumerate(row):
            x, y, w, h = c.rect
            ch = ObservedCharacter(i)
            ch.identity = Observed.known(c.id, c.score, "vision:card_art")
            reg(f"{side_name}.card{i}", c.rect, c.id, c.score)
            art = crop(game, (x + int(w * .15), y + int(h * .3), int(w * .7), int(h * .4)))
            if art.size:
                sat = float(cv2.cvtColor(art, cv2.COLOR_BGR2HSV)[..., 1].mean())
                ch.alive = Observed.known(sat > 35, 0.75 if abs(sat - 35) > 15 else 0.5, "vision:saturation")
            hp_rect = (int(x - .09 * h), int(y - .05 * h), int(.24 * h), int(.25 * h))
            res = self.ocr.read_badge(crop(game, hp_rect))
            if res.value is not None and 0 <= res.value <= 20 and res.confidence > 0.45:
                ch.hp = Observed.known(res.value, res.confidence, f"ocr:{res.engine}")
            elif ch.alive.value is False and ch.alive.confidence >= 0.7:
                ch.hp = Observed.inferred(0, 0.7, "defeated card")
            reg(f"{side_name}.c{i}.hp", hp_rect, ch.hp.value, ch.hp.confidence)
            en_rect = (int(x + w - .04 * h), int(y + .08 * h), int(.1 * h), int(.34 * h))
            ch.energy = self._energy(crop(game, en_rect), pip_width=en_rect[2])
            reg(f"{side_name}.c{i}.energy", en_rect, ch.energy.value, ch.energy.confidence)
            au_rect = (x + int(w * .1), int(y - .13 * h), int(w * .8), int(.1 * h))
            ch.aura = self._aura(crop(game, au_rect))
            reg(f"{side_name}.c{i}.aura", au_rect, ch.aura.value, ch.aura.confidence)
            side.characters.append(ch)
        return side

    def _side(self, game: np.ndarray, side_name: str, gw: int, gh: int, reg) -> ObservedSide:
        cfg = self.layout.side(side_name)
        side = ObservedSide()
        offsets = []
        for slot in range(len(cfg["cards"])):
            offsets.append(self._card_offset(game, side_name, slot, gw, gh))
        shift_px = cfg.get("active_shift", 0.0) * gh
        # active = the card displaced towards the centre
        scores = [(o / shift_px) if shift_px else 0.0 for o, _ in offsets]
        best = int(np.argmax(scores)) if scores else 0
        others = [s for i, s in enumerate(scores) if i != best]
        if scores and scores[best] > 0.55 and all(s < 0.35 for s in others):
            conf = min(0.97, 0.6 + 0.4 * min(1.0, scores[best] - max(others or [0])))
            side.active_index = Observed.known(best, conf, "vision:card_offset")
        for slot in range(len(cfg["cards"])):
            shifted = side.active_index.value == slot
            card = self.layout.card_rect(side_name, slot, gw, gh, shifted)
            ch = ObservedCharacter(slot)
            art = crop(game, Layout.part(card, self.layout.data["card_parts"]["art"]))
            if art.size:
                sat = float(cv2.cvtColor(art, cv2.COLOR_BGR2HSV)[..., 1].mean())
                ch.alive = Observed.known(sat > 35, 0.75 if abs(sat - 35) > 15 else 0.5, "vision:saturation")
                m = self.templates.best_match(art, "characters")
                if m.id:
                    ch.identity = Observed.known(m.id, m.score, "template:characters")
            reg(f"{side_name}.card{slot}", card, ch.identity.value, ch.identity.confidence)
            hp_rect = Layout.part(card, self.layout.data["card_parts"]["hp"])
            res = self.ocr.read_number(crop(game, hp_rect))
            if res.value is not None and 0 <= res.value <= 20 and res.confidence > 0.45:
                ch.hp = Observed.known(res.value, res.confidence, f"ocr:{res.engine}")
            elif ch.alive.value is False and ch.alive.confidence >= 0.7:
                ch.hp = Observed.inferred(0, 0.7, "defeated card")
            reg(f"{side_name}.c{slot}.hp", hp_rect, ch.hp.value, ch.hp.confidence)
            en_rect = Layout.part(card, self.layout.data["card_parts"]["energy"])
            ch.energy = self._energy(crop(game, en_rect))
            reg(f"{side_name}.c{slot}.energy", en_rect, ch.energy.value, ch.energy.confidence)
            au_rect = Layout.part(card, self.layout.data["card_parts"]["aura"])
            ch.aura = self._aura(crop(game, au_rect))
            reg(f"{side_name}.c{slot}.aura", au_rect, ch.aura.value, ch.aura.confidence)
            side.characters.append(ch)
        d = self.layout.to_px(cfg["dice_count"], gw, gh)
        res = self.ocr.read_number(crop(game, d))
        if res.value is not None and 0 <= res.value <= 16 and res.confidence > 0.45:
            side.dice_count = Observed.known(res.value, res.confidence, f"ocr:{res.engine}")
        reg(f"{side_name}.dice_count", d, side.dice_count.value, side.dice_count.confidence)
        if "dice_column" in cfg:
            col = self.layout.to_px(cfg["dice_column"], gw, gh)
            side.dice = self._dice_faces(crop(game, col), cfg.get("dice_slots", 8))
            reg(f"{side_name}.dice", col, side.dice.value, side.dice.confidence)
        summons = []
        conf_s = 1.0
        for j, frac in enumerate(cfg.get("summons", [])):
            r = self.layout.to_px(frac, gw, gh)
            m = self.templates.best_match(crop(game, r), "summons")
            if m.id:
                u = self.ocr.read_number(crop(game, Layout.part(r, self.layout.data["summon_usage"])))
                summons.append({"id": m.id, "usages": u.value if u.value is not None else 1})
                conf_s = min(conf_s, m.score, u.confidence if u.value is not None else 0.5)
        if self.templates.count("summons"):
            side.summons = Observed.known(summons, conf_s if summons else 0.6, "template:summons")
        return side

    def _card_offset(self, game: np.ndarray, side_name: str, slot: int, gw: int, gh: int) -> tuple[float, float]:
        """Vertical displacement (px) of the card's top edge relative to its resting position."""
        cfg = self.layout.side(side_name)
        x, y, w, h = self.layout.card_rect(side_name, slot, gw, gh)
        shift = abs(cfg.get("active_shift", 0.0) * gh)
        y0, y1 = max(0, int(y - 1.8 * shift)), min(game.shape[0], int(y + 1.8 * shift) + 2)
        band = game[y0:y1, x + w // 5: x + w - w // 5]
        if band.size == 0:
            return 0.0, 0.0
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).astype(np.float32)
        edge = np.abs(np.diff(gray, axis=0)).mean(axis=1)
        k = int(np.argmax(edge))
        strength = float(edge[k] / (edge.mean() + 1e-3))
        return float(y0 + k + 1 - y), strength

    def _energy(self, region: np.ndarray, pip_width: int | None = None) -> Observed:
        if region.size == 0:
            return Observed.unknown()
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        h, s, v = ENERGY_HSV
        mask = cv2.inRange(hsv, (h - 8, 110, 170), (h + 8, 255, 255))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        min_area = max(6, region.shape[0] * region.shape[1] // 400)
        if pip_width:  # real board: a lit pip is a filled diamond; ignore the golden card edge
            min_area = max(min_area, int(0.2 * pip_width * pip_width))
        pips = sum(1 for i in range(1, n) if stats[i][cv2.CC_STAT_AREA] >= min_area)
        conf = 0.8 if pips <= 4 else 0.3
        return Observed.known(pips, conf, "vision:energy_pips")

    def _aura(self, region: np.ndarray) -> Observed:
        if region.size == 0:
            return Observed.unknown()
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 90, 120), (180, 255, 255))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        min_area = max(8, region.shape[0] * region.shape[1] // 60)
        found, confs = [], []
        for i in range(1, n):
            if stats[i][cv2.CC_STAT_AREA] < min_area:
                continue
            mean = cv2.mean(hsv, mask=(labels == i).astype(np.uint8))[:3]
            name, c = classify_color(mean)
            if name and name != "omni" and name not in found:
                found.append(name)
                confs.append(c)
        return Observed.known(found, min(confs) * 0.85 if confs else 0.7, "vision:aura_color")

    def _dice_faces(self, column: np.ndarray, slots: int) -> Observed:
        if column.size == 0 or slots <= 0:
            return Observed.unknown()
        hsv = cv2.cvtColor(column, cv2.COLOR_BGR2HSV)
        step = column.shape[0] / slots
        counts: dict[str, int] = {}
        confs = []
        for k in range(slots):
            cell = hsv[int(k * step + step * 0.2): int((k + 1) * step - step * 0.2),
                       int(column.shape[1] * 0.2): int(column.shape[1] * 0.8)]
            if cell.size == 0:
                continue
            mean = cell.reshape(-1, 3).mean(axis=0)
            if mean[2] < 70:
                continue  # empty slot
            name, c = classify_color(tuple(mean))
            if name is None:
                confs.append(0.0)
                continue
            counts[name] = counts.get(name, 0) + 1
            confs.append(c)
        if not confs:
            return Observed.known({}, 0.5, "vision:dice_color")
        return Observed.known(counts, float(min(confs)), "vision:dice_color")

    # ------------------------------------------------------------------------------------
    @staticmethod
    def annotate(frame: np.ndarray, regions: list[dict[str, Any]]) -> np.ndarray:
        out = frame.copy()
        for r in regions:
            x, y, w, h = r["rect"]
            c = r["confidence"]
            color = (80, 200, 120) if c >= 0.75 else (60, 190, 240) if c >= 0.4 else (70, 70, 230)
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 1)
            label = f"{r['name'].split('.')[-1]}={r['value'] if r['value'] is not None else '?'}"
            cv2.putText(out, label[:22], (x, max(10, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
        return out


def die_from_name(name: str) -> DieType | None:
    try:
        return DieType(name)
    except ValueError:
        return None
