"""Action model shared by the rules engine, generator, planner and UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..serde import from_dict, to_dict
from .enums import ActionType, DieType


@dataclass
class Action:
    type: ActionType
    side: str
    actor: int | None = None  # acting character index (skills)
    target: int | None = None  # switch/choose target, card target character, summon index
    card: str | None = None
    skill: str | None = None  # skill id
    die: DieType | None = None  # die converted by elemental tuning
    cost: dict[str, int] = field(default_factory=dict)
    payment: dict[DieType, int] | None = None
    is_fast: bool = False
    legality: bool = True
    confidence: float = 1.0
    expected_effect: str = ""
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple:
        return (self.type, self.side, self.actor, self.target, self.card, self.skill, self.die)

    def same_as(self, other: "Action") -> bool:
        return self.key() == other.key()

    @property
    def is_combat(self) -> bool:
        return not self.is_fast

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Action":
        return from_dict(Action, data)
