"""Game-state reconstruction: ObservedGameState (+ history + manual setup) -> GameState.

Priority for each field: fresh observation (KNOWN) > manual match setup >
previous state (INFERRED, confidence decays) > default (low confidence).
The confidence map is stored in `state.metadata["confidence"]` so the UI can
show which values are KNOWN / INFERRED / UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.enums import OPPONENT, PLAYER, DieType, Element, Knowledge, Phase
from ..core.observed import Observed, ObservedGameState, ObservedSide
from ..core.state import Character, DicePool, GameState, Hand, PlayerState
from ..knowledge.base import KnowledgeBase


@dataclass
class MatchSetup:
    """Manual team composition entered in the UI (more reliable than template matching)."""

    player: list[str] = field(default_factory=list)
    opponent: list[str] = field(default_factory=list)
    player_hand: list[str] = field(default_factory=list)
    turn: str | None = None  # "player" / "opponent" when the user sets it manually


@dataclass
class ReconstructResult:
    state: GameState
    confidence: float
    problems: list[str]
    confidence_map: dict[str, dict[str, Any]]


class Reconstructor:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def build(self, obs: ObservedGameState, previous: GameState | None = None,
              setup: MatchSetup | None = None) -> ReconstructResult:
        cmap: dict[str, dict[str, Any]] = {}

        def note(path: str, value: Any, conf: float, knowledge: Knowledge, source: str) -> None:
            cmap[path] = {"value": value, "confidence": round(conf, 3), "knowledge": knowledge.value, "source": source}

        def pick(path: str, o: Observed, prev: Any, default: Any, prev_conf: float = 0.5,
                 default_conf: float = 0.2) -> tuple[Any, float]:
            if o.is_known and o.confidence > 0:
                note(path, o.value, o.confidence, o.knowledge, o.source)
                return o.value, o.confidence
            if prev is not None:
                note(path, prev, prev_conf, Knowledge.INFERRED, "previous state")
                return prev, prev_conf
            note(path, default, default_conf, Knowledge.INFERRED if default_conf > 0 else Knowledge.UNKNOWN, "default")
            return default, default_conf

        sides = {}
        for name in (PLAYER, OPPONENT):
            o_side: ObservedSide = getattr(obs, name)
            prev_side = previous.side(name) if previous else None
            manual = (setup.player if name == PLAYER else setup.opponent) if setup else []
            sides[name] = self._side(name, o_side, prev_side, manual, setup, pick, note)

        rnd, rc = pick("round", obs.round, previous.round if previous else None, 1, 0.6, 0.3)
        if setup and setup.turn:
            active_player, apc = setup.turn, 0.95
            note("active_player", setup.turn, 0.95, Knowledge.INFERRED, "manual (UI)")
        else:
            active_player, apc = pick("active_player", obs.active_player,
                                      previous.active_player if previous else None, PLAYER, 0.45, 0.3)
        state = GameState(round=int(rnd), phase=Phase.ACTION, active_player=active_player,
                          player=sides[PLAYER][0], opponent=sides[OPPONENT][0],
                          first_to_end=previous.first_to_end if previous else None)
        if previous:
            state.history = previous.history
        # start of the match: cards on the board but no active character yet -> choose one
        start = (previous is None and len(obs.player.characters) >= 2 and not obs.player.active_index.is_known
                 and not obs.opponent.active_index.is_known)
        if start:
            state.pending_choose = PLAYER
            state.phase = Phase.CHOOSE_ACTIVE
            state.resume_player = active_player
            state.active_player = PLAYER
            note("phase", "choose_active", 0.7, Knowledge.INFERRED, "no active card on the board")
        # pending choice: our active character is defeated on screen
        for name in (PLAYER, OPPONENT):
            p = state.side(name)
            if not start and p.characters and not p.active.alive and p.alive_indices():
                state.pending_choose = name
                state.phase = Phase.CHOOSE_ACTIVE
                state.resume_player = active_player
                state.active_player = name
        if state.player.all_dead() or state.opponent.all_dead():
            state.phase = Phase.GAME_OVER
            state.winner = OPPONENT if state.player.all_dead() else PLAYER

        critical = {**sides[PLAYER][1], **{f"opp_{k}": v for k, v in sides[OPPONENT][1].items()}}
        if start:  # nobody is active yet and nothing is paid: only who is on the board matters
            critical = {k: v for k, v in critical.items() if "_identity" in k or "_hp" in k}
        # not read from the screen yet (dice faces, whose turn): assumed, reported, but not blocking
        soft = {"active_player": apc}
        soft.update({k: critical.pop(k) for k in [k for k in critical if k.endswith("dice")]})
        problems = [k for k, v in {**critical, **soft}.items() if v < 0.5]
        confidence = min(critical.values()) if critical else 0.0
        if any(v < 0.5 for v in soft.values()):
            confidence = min(confidence, 0.7)
        state.metadata = {"source": "vision", "confidence": cmap, "state_confidence": round(confidence, 3),
                          "problems": problems}
        return ReconstructResult(state, confidence, problems, cmap)

    # ------------------------------------------------------------------------------------
    def _side(self, name, o: ObservedSide, prev: PlayerState | None, manual: list[str], setup, pick, note):
        seen = [c for c in o.characters if c.identity.is_known and c.identity.confidence >= 0.75]
        if seen and len(seen) == len(o.characters):
            n = len(o.characters)  # cards located by their art: exactly what is on the board (NPCs have 1-2)
        else:
            n = max(len(o.characters), len(manual), len(prev.characters) if prev else 0, 3)
        chars: list[Character] = []
        crit: dict[str, float] = {}
        for i in range(n):
            oc = o.characters[i] if i < len(o.characters) else None
            pc = prev.characters[i] if prev and i < len(prev.characters) else None
            # identity
            path = f"{name}.characters[{i}]"
            if oc and oc.identity.is_known and oc.identity.confidence >= 0.75:
                cid, ic = oc.identity.value, oc.identity.confidence
                note(path + ".identity", cid, ic, Knowledge.KNOWN, oc.identity.source)
            elif i < len(manual) and manual[i]:
                cid, ic = manual[i], 0.95
                note(path + ".identity", cid, ic, Knowledge.INFERRED, "manual setup")
            elif pc is not None and pc.id != "unknown":
                cid, ic = pc.id, 0.85
                note(path + ".identity", cid, ic, Knowledge.INFERRED, "previous state")
            else:
                cid, ic = "unknown", 0.0
                note(path + ".identity", None, 0.0, Knowledge.UNKNOWN, "not recognised")
            d = self.kb.character(cid)
            hp, hc = pick(path + ".hp", oc.hp if oc else Observed(), pc.hp if pc else None, d.hp, 0.5, 0.25)
            energy, _ = pick(path + ".energy", oc.energy if oc else Observed(), pc.energy if pc else None, 0, 0.5, 0.3)
            aura, _ = pick(path + ".aura", oc.aura if oc else Observed(), [a.value for a in pc.aura] if pc else None,
                           [], 0.4, 0.3)
            alive_obs = oc.alive if oc else Observed()
            alive = hp > 0 and (alive_obs.value is not False or alive_obs.confidence < 0.7)
            ch = Character(cid, int(hp), d.hp, min(int(energy), d.energy), d.energy, alive,
                           [Element(a) for a in aura if a in {e.value for e in Element}],
                           [s.clone() for s in pc.statuses] if pc else [],
                           [e.clone() for e in pc.equipment] if pc else [],
                           dict(pc.skill_uses_round) if pc else {})
            if not alive:
                ch.hp = 0
            chars.append(ch)
            crit[f"c{i}_identity"] = ic
            crit[f"c{i}_hp"] = hc
        active, ac = pick(f"{name}.active_index", o.active_index, prev.active_index if prev else None, 0, 0.5, 0.1)
        crit["active_index"] = ac
        # dice
        count, cc = pick(f"{name}.dice_count", o.dice_count, prev.dice.total() if prev else None, 8, 0.4, 0.2)
        if name == PLAYER:
            faces = o.dice.value if o.dice.is_known else None
            if faces is not None and sum(faces.values()) == count and o.dice.confidence >= 0.5:
                dice = DicePool({DieType(k): v for k, v in faces.items()})
                crit["dice"] = min(cc, o.dice.confidence)
            elif prev and prev.dice.total() == count and not prev.dice.hidden:
                dice = prev.dice.clone()
                crit["dice"] = min(cc, 0.6)
            else:
                dice = DicePool({}, int(count))  # faces unknown: treated as wildcards (optimistic)
                crit["dice"] = min(cc, 0.45)
                note(f"{name}.dice_faces", None, 0.0, Knowledge.UNKNOWN, "faces not recognised")
        else:
            dice = DicePool({}, int(count))
        # hand
        if name == PLAYER:
            cards = list(o.hand.value) if o.hand.is_known else (
                list(setup.player_hand) if setup and setup.player_hand else (list(prev.hand.cards) if prev else []))
            total, _ = pick(f"{name}.hand_count", o.hand_count, prev.hand.count() if prev else None, len(cards), 0.5, 0.3)
            hand = Hand(cards, max(0, int(total) - len(cards)))
        else:
            total, _ = pick(f"{name}.hand_count", o.hand_count, prev.hand.count() if prev else None, 5, 0.5, 0.2)
            hand = Hand([], int(total))
        side = PlayerState(name, chars, int(active) if 0 <= int(active) < len(chars) else 0, dice, hand)
        if o.summons.is_known:
            from ..core.state import Summon
            side.summons = [Summon(s["id"], int(s.get("usages", 1))) for s in o.summons.value]
        elif prev:
            side.summons = [s.clone() for s in prev.summons]
        if prev:
            side.combat_statuses = [s.clone() for s in prev.combat_statuses]
            side.supports = [s.clone() for s in prev.supports]
            side.declared_end = prev.declared_end
        return side, crit
