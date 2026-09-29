"""State verification: what happened between two observations?

After the player acts in the game, the coach compares the state BEFORE with the
state now observed. It simulates every legal player action (and the plausible
opponent replies that may already have happened) and picks the explanation that
matches the observation best. The result is CONFIRMED / DIFFERENT_ACTION /
UNCERTAIN / NO_CHANGE; on UNCERTAIN the coach re-analyses from the observation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ai.generator import ActionGenerator
from ..ai.opponent import OpponentModel
from ..core.actions import Action
from ..core.enums import OPPONENT, PLAYER
from ..core.state import GameState
from ..rules.engine import RulesEngine
from ..rules.events import Event

CONFIRM_SIMILARITY = 0.92
PARTIAL_SIMILARITY = 0.75


@dataclass
class VerificationResult:
    status: str  # CONFIRMED / DIFFERENT_ACTION / PARTIAL / UNCERTAIN / NO_CHANGE
    message: str
    similarity: float = 0.0
    matched: list[str] = field(default_factory=list)  # labels of the explaining action sequence
    matched_actions: list[Action] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "message": self.message, "similarity": round(self.similarity, 3),
                "matched": self.matched, "mismatches": self.mismatches, "changes": self.changes,
                "events": [e.to_dict() for e in self.events]}


def observable(s: GameState) -> dict[str, Any]:
    out: dict[str, Any] = {"round": s.round}
    for name in ("player", "opponent"):
        p = s.side(name)
        out[f"{name}.active"] = p.active_index
        out[f"{name}.dice"] = p.dice.total()
        out[f"{name}.hand"] = p.hand.count()
        out[f"{name}.summons"] = tuple(sorted(x.id for x in p.summons))
        for i, c in enumerate(p.characters):
            out[f"{name}.c{i}.hp"] = c.hp
            out[f"{name}.c{i}.energy"] = c.energy
            out[f"{name}.c{i}.alive"] = c.alive
    return out


WEIGHTS = {"hp": 2.0, "alive": 2.0, "active": 2.0, "energy": 1.0, "dice": 1.5, "hand": 0.5, "summons": 1.0,
           "round": 1.0}


def _weight(key: str) -> float:
    return WEIGHTS.get(key.rsplit(".", 1)[-1], 1.0)


class Verifier:
    def __init__(self, engine: RulesEngine):
        self.engine = engine
        self.gen = ActionGenerator(engine)
        self.opp = OpponentModel(self.gen)

    def diff(self, before: GameState, after: GameState) -> list[str]:
        a, b = observable(before), observable(after)
        labels = []
        for k in a:
            if k in b and a[k] != b[k]:
                labels.append(f"{k}: {a[k]} → {b[k]}")
        return labels

    def similarity(self, predicted: GameState, observed: GameState, reliable: set[str] | None = None
                   ) -> tuple[float, list[dict[str, Any]]]:
        p, o = observable(predicted), observable(observed)
        total = matched = 0.0
        mismatches = []
        for k, ov in o.items():
            if reliable is not None and k not in reliable:
                continue
            if k not in p:
                continue
            w = _weight(k)
            total += w
            if p[k] == ov:
                matched += w
            else:
                mismatches.append({"field": k, "expected": p[k], "observed": ov})
        return (matched / total if total else 0.0), mismatches

    def verify(self, before: GameState, observed: GameState, recommended: Action | None = None,
               reliable: set[str] | None = None) -> VerificationResult:
        changes = self.diff(before, observed)
        if not changes:
            return VerificationResult("NO_CHANGE", "Изменений на экране нет — ожидаем ваш ход.", 1.0)

        candidates: list[tuple[float, list[Action], list[Event], list[dict]]] = []
        for a in self.gen.legal_actions(before, PLAYER, prune=False):
            res = self.engine.apply(before, a)
            sim, mism = self.similarity(res.state, observed, reliable)
            candidates.append((sim, [a], list(res.events), mism))
            # the opponent may already have answered before the screenshot
            nxt = res.state
            if (nxt.pending_choose or nxt.active_player) == OPPONENT:
                for r, _ in self.opp.responses(nxt, 6):
                    res2 = self.engine.apply(nxt, r)
                    sim2, mism2 = self.similarity(res2.state, observed, reliable)
                    candidates.append((sim2 - 0.01, [a, r], list(res.events) + list(res2.events), mism2))
        if not candidates:
            return VerificationResult("UNCERTAIN", "ACTION RESULT UNCERTAIN — повторяю анализ.", 0.0, changes=changes)
        candidates.sort(key=lambda c: c[0], reverse=True)
        sim, acts, events, mism = candidates[0]
        labels = [x.label for x in acts]
        if sim >= CONFIRM_SIMILARITY:
            if recommended is not None and acts[0].same_as(recommended):
                status, msg = "CONFIRMED", "Состояние обновлено: действие подтверждено."
            else:
                status, msg = "DIFFERENT_ACTION", f"Выполнено другое действие: {labels[0]}."
        elif sim >= PARTIAL_SIMILARITY:
            status, msg = "PARTIAL", "Результат частично отличается от прогноза — возможно, сработал неучтённый эффект."
        else:
            status, msg = "UNCERTAIN", "ACTION RESULT UNCERTAIN — повторяю анализ."
        return VerificationResult(status, msg, sim, labels, acts, events, mism, changes)
