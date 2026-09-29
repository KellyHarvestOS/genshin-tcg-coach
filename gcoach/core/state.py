"""Strict, serializable game-state model used by the rules engine and planner.

Perception results (with per-field confidence) live in `observed.py`; the
reconstruction step turns them into this simulation-ready `GameState` and
keeps the confidence map in `metadata.confidence`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..serde import from_dict, to_dict
from .enums import OPPONENT, PLAYER, DieType, Element, Phase


@dataclass
class Status:
    """A character status, combat status or support. Behaviour lives in the knowledge base."""

    id: str
    usages: int | None = None  # shield points / remaining triggers; None = unlimited
    duration: int | None = None  # remaining rounds; None = until used up
    data: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "Status":
        return Status(self.id, self.usages, self.duration, dict(self.data) if self.data else {})


@dataclass
class Summon:
    id: str
    usages: int
    data: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "Summon":
        return Summon(self.id, self.usages, dict(self.data) if self.data else {})


@dataclass
class Equipment:
    id: str  # card id (weapon / artifact / talent)
    kind: str = "weapon"

    def clone(self) -> "Equipment":
        return Equipment(self.id, self.kind)


@dataclass
class Character:
    id: str  # knowledge-base id, "unknown" when not recognised
    hp: int
    max_hp: int = 10
    energy: int = 0
    max_energy: int = 2
    alive: bool = True
    aura: list[Element] = field(default_factory=list)
    statuses: list[Status] = field(default_factory=list)
    equipment: list[Equipment] = field(default_factory=list)
    skill_uses_round: dict[str, int] = field(default_factory=dict)

    def clone(self) -> "Character":
        return Character(
            self.id, self.hp, self.max_hp, self.energy, self.max_energy, self.alive,
            list(self.aura), [s.clone() for s in self.statuses],
            [e.clone() for e in self.equipment], dict(self.skill_uses_round),
        )

    def status(self, status_id: str) -> Status | None:
        for s in self.statuses:
            if s.id == status_id:
                return s
        return None

    def has_status(self, status_id: str) -> bool:
        return self.status(status_id) is not None


@dataclass
class DicePool:
    counts: dict[DieType, int] = field(default_factory=dict)
    # Number of dice whose faces are not visible (opponent's dice). Their total is visible in-game.
    hidden: int = 0

    def total(self) -> int:
        return sum(self.counts.values()) + self.hidden

    def get(self, die: DieType) -> int:
        return self.counts.get(die, 0)

    def add(self, die: DieType, n: int = 1) -> None:
        self.counts[die] = self.counts.get(die, 0) + n

    def remove(self, die: DieType, n: int = 1) -> None:
        left = self.counts.get(die, 0) - n
        if left < 0:
            raise ValueError(f"not enough {die.value} dice")
        if left == 0:
            self.counts.pop(die, None)
        else:
            self.counts[die] = left

    def clone(self) -> "DicePool":
        return DicePool(dict(self.counts), self.hidden)


@dataclass
class Hand:
    cards: list[str] = field(default_factory=list)  # known card ids
    hidden: int = 0  # cards we know exist but cannot see (opponent hand / fresh draws)

    def count(self) -> int:
        return len(self.cards) + self.hidden

    def clone(self) -> "Hand":
        return Hand(list(self.cards), self.hidden)


@dataclass
class PlayerState:
    side: str
    characters: list[Character] = field(default_factory=list)
    active_index: int = 0
    dice: DicePool = field(default_factory=DicePool)
    hand: Hand = field(default_factory=Hand)
    summons: list[Summon] = field(default_factory=list)
    combat_statuses: list[Status] = field(default_factory=list)
    supports: list[Status] = field(default_factory=list)
    deck_count: int | None = None
    declared_end: bool = False
    character_died_this_round: bool = False
    hidden_information: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "PlayerState":
        return PlayerState(
            self.side, [c.clone() for c in self.characters], self.active_index,
            self.dice.clone(), self.hand.clone(), [s.clone() for s in self.summons],
            [s.clone() for s in self.combat_statuses], [s.clone() for s in self.supports],
            self.deck_count, self.declared_end, self.character_died_this_round,
            self.hidden_information,  # read-only during simulation, shared on purpose
        )

    @property
    def active(self) -> Character:
        return self.characters[self.active_index]

    def alive_indices(self) -> list[int]:
        return [i for i, c in enumerate(self.characters) if c.alive]

    def combat_status(self, status_id: str) -> Status | None:
        for s in self.combat_statuses:
            if s.id == status_id:
                return s
        return None

    def summon(self, summon_id: str) -> Summon | None:
        for s in self.summons:
            if s.id == summon_id:
                return s
        return None

    def all_dead(self) -> bool:
        return bool(self.characters) and not any(c.alive for c in self.characters)


@dataclass
class GameState:
    round: int = 1
    phase: Phase = Phase.ACTION
    active_player: str = PLAYER  # whose turn it is
    first_to_end: str | None = None  # turn priority for the next round
    player: PlayerState = field(default_factory=lambda: PlayerState(PLAYER))
    opponent: PlayerState = field(default_factory=lambda: PlayerState(OPPONENT))
    pending_choose: str | None = None  # side that must pick a new active character
    resume_player: str | None = None  # who acts after the pending choice is made
    winner: str | None = None  # "player" / "opponent" / "draw"
    battlefield: dict[str, Any] = field(default_factory=dict)
    known_effects: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- helpers -------------------------------------------------------------------------
    def side(self, name: str) -> PlayerState:
        return self.player if name == PLAYER else self.opponent

    def clone(self) -> "GameState":
        """Fast copy for simulation. History/metadata are shared - the engine never mutates them."""
        return GameState(
            self.round, self.phase, self.active_player, self.first_to_end,
            self.player.clone(), self.opponent.clone(), self.pending_choose, self.resume_player,
            self.winner,
            self.battlefield, self.known_effects, self.history, self.metadata,
        )

    def deep_copy(self) -> "GameState":
        return GameState.from_dict(self.to_dict())

    @property
    def is_over(self) -> bool:
        return self.phase == Phase.GAME_OVER

    # --- serialization -------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "GameState":
        return from_dict(GameState, data)

    @staticmethod
    def from_json(text: str) -> "GameState":
        return GameState.from_dict(json.loads(text))
