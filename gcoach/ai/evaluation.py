"""Position evaluation: many weighted features, not just damage.

`evaluate()` returns a score from the player's perspective plus the individual
feature contributions (used to explain recommendations). The score is mapped to
a win-probability estimate with a logistic curve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core.dice import die_value
from ..core.enums import OPPONENT, PLAYER, Element, Phase, other_side
from ..core.state import GameState, PlayerState
from ..rules.engine import RulesEngine
from ..rules.reactions import reactions_available


@dataclass
class EvalWeights:
    win: float = 1000.0
    hp: float = 1.0
    alive: float = 5.0
    low_hp: float = 1.0
    energy: float = 1.6
    burst_ready: float = 1.5
    dice: float = 0.55
    cards: float = 0.6
    summon: float = 0.8
    status: float = 0.7
    tempo: float = 0.8
    reaction_setup: float = 0.8
    exposed_aura: float = 0.6
    lethal: float = 2.5
    threat: float = 2.5
    disabled_active: float = 2.5
    prob_scale: float = 10.0


@dataclass
class Evaluation:
    score: float
    win_probability: float
    features: dict[str, float] = field(default_factory=dict)
    terminal: bool = False


class Evaluator:
    def __init__(self, engine: RulesEngine, weights: EvalWeights | None = None):
        self.engine = engine
        self.kb = engine.kb
        self.w = weights or EvalWeights()

    # ------------------------------------------------------------------------------------
    def evaluate(self, s: GameState, perspective: str = PLAYER) -> Evaluation:
        sign = 1.0 if perspective == PLAYER else -1.0
        if s.phase == Phase.GAME_OVER:
            if s.winner == "draw":
                return Evaluation(0.0, 0.5, {"terminal": 0.0}, True)
            won = s.winner == perspective
            return Evaluation(self.w.win if won else -self.w.win, 1.0 if won else 0.0,
                              {"terminal": self.w.win if won else -self.w.win}, True)
        f: dict[str, float] = {}
        me, opp = s.player, s.opponent
        f["hp"] = self.w.hp * (self._hp(me) - self._hp(opp))
        f["alive"] = self.w.alive * (len(me.alive_indices()) - len(opp.alive_indices()))
        f["low_hp"] = -self.w.low_hp * (self._low(me) - self._low(opp))
        f["energy"] = self._energy(me) - self._energy(opp)
        f["dice"] = self.w.dice * (self._dice(s, me) - self._dice(s, opp))
        f["cards"] = self.w.cards * (min(me.hand.count(), 10) - min(opp.hand.count(), 10))
        f["summons"] = self.w.summon * (self._summons(me) - self._summons(opp))
        f["statuses"] = self.w.status * (self._statuses(me) - self._statuses(opp))
        f["tempo"] = self._tempo(s)
        f["reactions"] = self._reaction_setup(s, me, opp) - self._reaction_setup(s, opp, me)
        f["lethal"], f["threat"] = self._lethal(s)
        f["disabled"] = self.w.disabled_active * (self._disabled(opp) - self._disabled(me))
        score = sum(f.values()) * sign
        if sign < 0:
            f = {k: -v for k, v in f.items()}
        return Evaluation(score, self.to_probability(score), f)

    def to_probability(self, score: float) -> float:
        return 1.0 / (1.0 + math.exp(-score / self.w.prob_scale))

    # ------------------------------------------------------------------------------------
    def _hp(self, side: PlayerState) -> float:
        return sum(c.hp for c in side.characters if c.alive)

    def _low(self, side: PlayerState) -> float:
        return sum(1 for c in side.characters if c.alive and c.hp <= 3)

    def _energy(self, side: PlayerState) -> float:
        total = 0.0
        for i, c in enumerate(side.characters):
            if not c.alive or c.max_energy <= 0:
                continue
            total += self.w.energy * (c.energy / c.max_energy) * 2
            if c.energy >= c.max_energy and i == side.active_index:
                total += self.w.burst_ready
        return total

    def _dice(self, s: GameState, side: PlayerState) -> float:
        if s.phase != Phase.ACTION or side.declared_end:
            return 0.0  # dice do not carry over to the next round
        if side.dice.hidden:
            return side.dice.hidden * 1.0
        active_el = self.engine.active_element(side)
        team = self.engine.team_elements(side)
        return sum(die_value(d, active_el, team) * n for d, n in side.dice.counts.items())

    def _summons(self, side: PlayerState) -> float:
        total = 0.0
        for sm in side.summons:
            d = self.kb.summon(sm.id)
            per_turn = d.damage + d.piercing * 0.8 + d.heal * 0.6
            beh = d.behaviors.get("reduce")
            if beh:
                per_turn += beh.get("amount", 1) * 0.7
            if per_turn == 0 and not d.modelled:
                per_turn = 1.0  # unknown summon still has some value
            total += per_turn * sm.usages
        return total

    def _statuses(self, side: PlayerState) -> float:
        total = 0.0
        buckets = [side.combat_statuses] + [c.statuses for c in side.characters if c.alive]
        for bucket in buckets:
            for st in bucket:
                d = self.kb.status(st.id)
                b = d.behaviors
                n = st.usages if st.usages is not None else (st.duration or 1)
                if "shield" in b:
                    total += 1.3 * (st.usages or b["shield"])
                if "reduce" in b:
                    total += b["reduce"]["amount"] * n
                if "damage_bonus" in b:
                    total += b["damage_bonus"]["amount"] * n * 0.9
                for key in ("after_normal_attack_damage", "after_switch_damage", "after_skill_damage"):
                    if key in b:
                        total += b[key]["amount"] * n
                if "infusion" in b:
                    total += 0.7
                if "fast_switch" in b:
                    total += 0.8
                if "switch_discount" in b:
                    total += 0.7 * n
                if "incoming_bonus" in b:
                    total -= b["incoming_bonus"]["amount"] * 1.0
                if "end_phase_self_damage" in b:
                    total -= b["end_phase_self_damage"]["amount"] * n
        return total

    def _tempo(self, s: GameState) -> float:
        v = 0.0
        if s.phase == Phase.ROLL:
            return self.w.tempo if s.active_player == PLAYER else -self.w.tempo
        if s.first_to_end == PLAYER:
            v += self.w.tempo
        elif s.first_to_end == OPPONENT:
            v -= self.w.tempo
        if s.opponent.declared_end and not s.player.declared_end:
            v += 0.15 * s.player.dice.total()
        if s.player.declared_end and not s.opponent.declared_end:
            v -= 0.15 * s.opponent.dice.total()
        return v

    def _reaction_setup(self, s: GameState, attacker: PlayerState, defender: PlayerState) -> float:
        if not attacker.characters or not defender.characters or not defender.active.alive:
            return 0.0
        aura = defender.active.aura
        if not aura:
            return 0.0
        best = 0.0
        for i in attacker.alive_indices():
            el = self.engine.char_def(attacker.characters[i]).element
            r = reactions_available(el, aura)
            if r:
                bonus = self.kb.reaction(r.value).get("bonus", 1)
                weight = 1.0 if i == attacker.active_index else 0.6
                best = max(best, (1 + bonus * 0.5) * weight)
        w = self.w.reaction_setup if attacker.side == PLAYER else self.w.exposed_aura
        return w * best

    def _lethal(self, s: GameState) -> tuple[float, float]:
        if s.phase != Phase.ACTION:
            return 0.0, 0.0
        me, opp = s.player, s.opponent
        lethal = threat = 0.0
        if not me.declared_end and opp.active.alive:
            dmg, _ = self.engine.max_skill_damage(s, PLAYER)
            if dmg >= opp.active.hp:
                lethal = self.w.lethal
        if not opp.declared_end and me.active.alive:
            dmg, _ = self.engine.max_skill_damage(s, OPPONENT)
            if dmg >= me.active.hp:
                threat = -self.w.threat
        return lethal, threat

    def _disabled(self, side: PlayerState) -> float:
        if not side.characters or not side.active.alive:
            return 0.0
        return 0.0 if self.engine.can_use_skills(side.active) else 1.0


FEATURE_NAMES_RU = {
    "hp": "преимущество по HP", "alive": "живые персонажи", "low_hp": "персонажи на грани",
    "energy": "энергия", "dice": "кубики", "cards": "карты в руке", "summons": "призывы",
    "statuses": "статусы и щиты", "tempo": "темп", "reactions": "подготовка реакций",
    "lethal": "возможность добить", "threat": "угроза от противника", "disabled": "контроль",
    "terminal": "исход партии",
}
