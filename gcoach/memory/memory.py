"""Short-term memory (current situation) and match memory (whole game)."""
from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.enums import PLAYER
from ..core.state import GameState


@dataclass
class ShortTermMemory:
    current_state: dict[str, Any] | None = None
    last_actions: deque = field(default_factory=lambda: deque(maxlen=12))
    last_changes: deque = field(default_factory=lambda: deque(maxlen=12))
    current_plan: list[dict[str, Any]] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_actions": list(self.last_actions),
            "last_changes": list(self.last_changes),
            "current_plan": self.current_plan,
            "threats": self.threats,
        }


def state_summary(s: GameState, kb=None) -> dict[str, Any]:
    def side(p):
        return {
            "active": p.active_index,
            "characters": [{"id": c.id, "hp": c.hp, "energy": c.energy, "alive": c.alive,
                            "aura": [a.value for a in c.aura]} for c in p.characters],
            "dice": p.dice.total(),
            "hand": p.hand.count(),
            "summons": [(x.id, x.usages) for x in p.summons],
        }
    return {"round": s.round, "phase": s.phase.value, "active_player": s.active_player,
            "player": side(s.player), "opponent": side(s.opponent)}


class MatchMemory:
    def __init__(self, root: Path):
        self.root = root
        self.reset()

    def reset(self) -> None:
        self.match_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.started_at = time.time()
        self.rounds: dict[int, dict[str, list]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.deck: dict[str, Any] = {"player_characters": [], "opponent_characters": [], "cards_seen": []}
        self.result: str | None = None

    def _round(self, n: int) -> dict[str, list]:
        return self.rounds.setdefault(n, {"states": [], "actions": [], "results": []})

    def record_state(self, s: GameState) -> None:
        summary = state_summary(s)
        r = self._round(s.round)
        if not r["states"] or r["states"][-1] != summary:
            r["states"].append(summary)
        self.deck["player_characters"] = [c.id for c in s.player.characters]
        self.deck["opponent_characters"] = [c.id for c in s.opponent.characters]
        for c in s.player.hand.cards:
            if c not in self.deck["cards_seen"]:
                self.deck["cards_seen"].append(c)

    def record_decision(self, s: GameState, rec: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any]:
        d = {
            "id": len(self.decisions),
            "time": time.time(),
            "round": s.round,
            "state": state_summary(s),
            "dice_left": s.player.dice.total(),
            "burst_ready": s.player.active.alive and s.player.active.energy >= s.player.active.max_energy > 0,
            "recommended": {"label": rec.get("title"), "type": rec.get("action_type"),
                            "win_probability": rec.get("win_probability")},
            "options": options,
            "actual": None,
            "verification": None,
        }
        self.decisions.append(d)
        return d

    def record_actual(self, label: str, action_type: str, win_probability: float | None,
                      verification: str, events: list[dict[str, Any]]) -> None:
        if not self.decisions:
            return
        d = self.decisions[-1]
        if d["actual"] is not None:
            return
        d["actual"] = {"label": label, "type": action_type, "win_probability": win_probability}
        d["verification"] = verification
        self._round(d["round"])["actions"].append({"side": PLAYER, "label": label, "verification": verification})
        self.record_events(d["round"], events)

    def record_events(self, round_no: int, events: list[dict[str, Any]]) -> None:
        for e in events:
            e = dict(e)
            e["round"] = round_no
            self.events.append(e)
        if events:
            self._round(round_no)["results"].extend(ev["text"] for ev in events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id, "started_at": self.started_at, "result": self.result,
            "deck": self.deck, "rounds": {str(k): v for k, v in sorted(self.rounds.items())},
            "decisions": self.decisions, "events": self.events,
        }

    def save(self) -> Path:
        folder = self.root / self.match_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "match.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return path
