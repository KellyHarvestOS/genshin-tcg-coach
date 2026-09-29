"""Confidence-annotated values produced by perception.

Every important parameter read from the screen is wrapped in `Observed`, so
the rest of the system can distinguish KNOWN / INFERRED / UNKNOWN data and
never presents a guess as a fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import Knowledge


@dataclass
class Observed:
    value: Any = None
    confidence: float = 0.0
    knowledge: Knowledge = Knowledge.UNKNOWN
    source: str = ""  # e.g. "ocr:hp_badge", "template:characters", "history"

    @staticmethod
    def known(value: Any, confidence: float = 0.99, source: str = "") -> "Observed":
        return Observed(value, confidence, Knowledge.KNOWN, source)

    @staticmethod
    def inferred(value: Any, confidence: float, source: str = "") -> "Observed":
        return Observed(value, min(confidence, 0.9), Knowledge.INFERRED, source)

    @staticmethod
    def unknown(source: str = "") -> "Observed":
        return Observed(None, 0.0, Knowledge.UNKNOWN, source)

    @property
    def is_known(self) -> bool:
        return self.knowledge != Knowledge.UNKNOWN and self.value is not None


@dataclass
class ObservedCharacter:
    slot: int
    identity: Observed = field(default_factory=Observed)  # knowledge-base character id
    hp: Observed = field(default_factory=Observed)
    energy: Observed = field(default_factory=Observed)
    aura: Observed = field(default_factory=Observed)  # list[str] of elements
    statuses: Observed = field(default_factory=Observed)  # list[str] status ids
    alive: Observed = field(default_factory=Observed)


@dataclass
class ObservedSide:
    characters: list[ObservedCharacter] = field(default_factory=list)
    active_index: Observed = field(default_factory=Observed)
    dice_count: Observed = field(default_factory=Observed)
    dice: Observed = field(default_factory=Observed)  # dict[die, count]; player only
    hand: Observed = field(default_factory=Observed)  # list[str] card ids; player only
    hand_count: Observed = field(default_factory=Observed)
    summons: Observed = field(default_factory=Observed)  # list[{id, usages}]
    combat_statuses: Observed = field(default_factory=Observed)
    declared_end: Observed = field(default_factory=Observed)


@dataclass
class ObservedGameState:
    """Raw perception output - one frame worth of evidence."""

    round: Observed = field(default_factory=Observed)
    phase: Observed = field(default_factory=Observed)
    active_player: Observed = field(default_factory=Observed)
    player: ObservedSide = field(default_factory=ObservedSide)
    opponent: ObservedSide = field(default_factory=ObservedSide)
    frame_id: str = ""
    notes: list[str] = field(default_factory=list)

    def iter_fields(self):
        """Yield (path, Observed) for every observed value (used for confidence maps)."""
        yield "round", self.round
        yield "phase", self.phase
        yield "active_player", self.active_player
        for side_name in ("player", "opponent"):
            side: ObservedSide = getattr(self, side_name)
            for name in ("active_index", "dice_count", "dice", "hand", "hand_count",
                         "summons", "combat_statuses", "declared_end"):
                yield f"{side_name}.{name}", getattr(side, name)
            for ch in side.characters:
                for name in ("identity", "hp", "energy", "aura", "statuses", "alive"):
                    yield f"{side_name}.characters[{ch.slot}].{name}", getattr(ch, name)
