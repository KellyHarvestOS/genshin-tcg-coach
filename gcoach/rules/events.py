"""Events emitted by the rules engine - used for explanations, verification and memory."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str  # damage, heal, reaction, summon, status, switch, death, energy, dice, card, round_end, game_over
    side: str  # side the event is about (target side for damage)
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "side": self.side, "text": self.text, "data": self.data}
