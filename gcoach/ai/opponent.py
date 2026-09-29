"""Opponent model.

The opponent's dice faces and hand are hidden. We therefore:
  * generate only actions whose mechanics we know (skills, switches, end round);
  * weight each action by the probability that the hidden dice can pay for it;
  * never pretend to know which cards the opponent holds (card plays are a risk
    note, not a simulated branch).
The planner combines the worst case with a probability-weighted average, so the
opponent is assumed to be competent but not omniscient.
"""
from __future__ import annotations

from functools import lru_cache
from math import comb

from ..core.actions import Action
from ..core.enums import ActionType, SKILL_ACTIONS
from ..core.state import GameState
from .generator import ActionGenerator

# Probability that one hidden die can pay a specific elemental symbol after re-rolls
# (omni or the active character's element, keeping matching dice on re-roll).
P_MATCH_DIE = 0.45
P_MATCH_DIE_WITH_TUNING = 0.55


@lru_cache(maxsize=512)
def p_at_least(k: int, n: int, q: float) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, j) * q ** j * (1 - q) ** (n - j) for j in range(k, n + 1))


class OpponentModel:
    def __init__(self, generator: ActionGenerator):
        self.gen = generator
        self.engine = generator.engine

    def afford_probability(self, state: GameState, action: Action) -> float:
        side = state.side(action.side)
        cost = action.cost or {}
        total = sum(v for k, v in cost.items() if k != "energy")
        if not side.dice.hidden:
            return 1.0  # faces known (should not happen for opponent, but be exact)
        if total > side.dice.total():
            return 0.0
        elemental = sum(v for k, v in cost.items() if k not in ("energy", "unaligned"))
        q = P_MATCH_DIE_WITH_TUNING if side.hand.count() > 0 else P_MATCH_DIE
        return p_at_least(elemental, side.dice.total(), q)

    def responses(self, state: GameState, width: int = 5) -> list[tuple[Action, float]]:
        """Plausible opponent actions with prior weights (sum to 1)."""
        side_name = state.active_player
        actions = self.gen.legal_actions(state, side_name)
        scored: list[tuple[Action, float]] = []
        for a in actions:
            if a.type in (ActionType.PLAY_CARD, ActionType.ELEMENTAL_TUNING):
                continue  # hidden hand - not simulated
            p = self.afford_probability(state, a)
            if p < 0.05:
                continue
            prior = {ActionType.ELEMENTAL_BURST: 3.0, ActionType.ELEMENTAL_SKILL: 2.2,
                     ActionType.NORMAL_ATTACK: 1.5, ActionType.SWITCH_CHARACTER: 0.8,
                     ActionType.CHOOSE_ACTIVE: 1.0, ActionType.END_ROUND: 0.6}.get(a.type, 0.5)
            a.confidence = p
            scored.append((a, prior * p))
        if not scored:
            return []
        scored.sort(key=lambda t: t[1], reverse=True)
        # always keep END_ROUND as a fallback branch
        top = scored[:width]
        if not any(a.type == ActionType.END_ROUND for a, _ in top):
            end = next(((a, w) for a, w in scored if a.type == ActionType.END_ROUND), None)
            if end:
                top.append(end)
        total = sum(w for _, w in top) or 1.0
        return [(a, w / total) for a, w in top]

    @staticmethod
    def is_attack(action: Action) -> bool:
        return action.type in SKILL_ACTIONS
