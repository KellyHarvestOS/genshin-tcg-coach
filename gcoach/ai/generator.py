"""Legal action generation. Never yields an action the rules engine would reject."""
from __future__ import annotations

from ..core.actions import Action
from ..core.dice import plan_payment
from ..core.enums import ActionType, DieType, Element, Phase, SKILL_TYPE_TO_ACTION
from ..core.state import GameState, PlayerState
from ..rules.engine import RulesEngine

SKILL_WORD = {
    ActionType.NORMAL_ATTACK: "Обычная атака",
    ActionType.ELEMENTAL_SKILL: "Элементальный навык",
    ActionType.ELEMENTAL_BURST: "Взрыв стихии",
}
DIE_RU = {
    DieType.OMNI: "Всеэлем.", DieType.CRYO: "Крио", DieType.HYDRO: "Гидро", DieType.PYRO: "Пиро",
    DieType.ELECTRO: "Электро", DieType.ANEMO: "Анемо", DieType.GEO: "Гео", DieType.DENDRO: "Дендро",
}


class ActionGenerator:
    def __init__(self, engine: RulesEngine):
        self.engine = engine
        self.kb = engine.kb

    # ------------------------------------------------------------------------------------
    def legal_actions(self, state: GameState, side_name: str, prune: bool = True) -> list[Action]:
        if state.phase == Phase.GAME_OVER:
            return []
        side = state.side(side_name)
        out: list[Action] = []
        if state.pending_choose:
            if state.pending_choose != side_name:
                return []
            for i in side.alive_indices():
                out.append(self._finish(state, Action(ActionType.CHOOSE_ACTIVE, side_name, target=i)))
            return [a for a in out if a.legality]
        if state.phase != Phase.ACTION or state.active_player != side_name or side.declared_end:
            return []

        # skills of the active character
        ch = side.active
        for skill in self.engine.char_def(ch).skills:
            a = Action(SKILL_TYPE_TO_ACTION[skill.type], side_name, actor=side.active_index, skill=skill.id)
            out.append(self._finish(state, a))
        # switches
        for i in side.alive_indices():
            if i != side.active_index:
                out.append(self._finish(state, Action(ActionType.SWITCH_CHARACTER, side_name, target=i)))
        # cards (only those whose mechanics are modelled - others cannot be evaluated honestly)
        for card_id in dict.fromkeys(side.hand.cards):
            card = self.kb.card(card_id)
            if card is None or not card.modelled:
                continue
            if card.subtype == "weapon":
                for i in side.alive_indices():
                    if self.engine.char_def(side.characters[i]).weapon == card.weapon_type:
                        if prune and any(eq.kind == "weapon" for eq in side.characters[i].equipment):
                            continue
                        out.append(self._finish(state, Action(ActionType.PLAY_CARD, side_name, card=card_id, target=i)))
            elif card.subtype == "food" or any(e.get("target") == "chosen" for e in card.effects):
                for i in side.alive_indices():
                    c = side.characters[i]
                    if prune and c.hp >= c.max_hp:
                        continue
                    out.append(self._finish(state, Action(ActionType.PLAY_CARD, side_name, card=card_id, target=i)))
            else:
                if prune and self._useless_card(state, side, card):
                    continue
                out.append(self._finish(state, Action(ActionType.PLAY_CARD, side_name, card=card_id)))
        # elemental tuning
        out.extend(self._tuning_actions(state, side, prune))
        out.append(self._finish(state, Action(ActionType.END_ROUND, side_name)))
        return [a for a in out if a.legality]

    # ------------------------------------------------------------------------------------
    def _useless_card(self, state: GameState, side: PlayerState, card) -> bool:
        for e in card.effects:
            if e.get("op") == "status" and e.get("id") == "fast_switch":
                if side.combat_status("fast_switch") or len(side.alive_indices()) < 2:
                    return True
            if e.get("op") == "status" and e.get("id") == "next_normal_attack_bonus":
                if side.combat_status("next_normal_attack_bonus"):
                    return True
            if e.get("op") == "gain_energy" and side.active.energy >= side.active.max_energy:
                return True
            if e.get("op") == "draw" and side.hand.count() >= 9:
                return True
        return False

    def _tuning_actions(self, state: GameState, side: PlayerState, prune: bool) -> list[Action]:
        if side.hand.count() == 0 or side.dice.hidden:
            return []
        active_el = self.engine.active_element(side)
        target_die = DieType.for_element(active_el) if active_el else None
        if target_die is None:
            return []
        card = self._cheapest_card(side)
        out: list[Action] = []
        team = self.engine.team_elements(side)
        for die in sorted(side.dice.counts, key=lambda d: d.value):
            if die in (DieType.OMNI, target_die):
                continue
            a = Action(ActionType.ELEMENTAL_TUNING, side.side, card=card, die=die)
            if prune:
                # only when tuning turns an unaffordable skill into an affordable one
                after = side.dice.clone()
                after.remove(die)
                after.add(target_die)
                helps = False
                for skill in self.engine.char_def(side.active).skills:
                    if skill.energy_cost and side.active.energy < skill.energy_cost:
                        continue
                    now = plan_payment(side.dice, skill.cost, active_el, team)
                    later = plan_payment(after, skill.cost, active_el, team)
                    if now is None and later is not None:
                        helps = True
                        break
                if not helps:
                    continue
            out.append(self._finish(state, a))
        return out

    def _cheapest_card(self, side: PlayerState) -> str | None:
        """Card to discard for tuning: unmodelled / low-value cards first."""
        if not side.hand.cards:
            return None
        def value(card_id: str) -> float:
            c = self.kb.card(card_id)
            if c is None or not c.modelled:
                return 0.0
            return 1.0 + sum(c.cost.values()) * 0.1
        return min(side.hand.cards, key=value)

    # ------------------------------------------------------------------------------------
    def _finish(self, state: GameState, a: Action) -> Action:
        ok, reason = self.engine.check(state, a)
        a.legality = ok
        if not ok:
            a.meta["reason"] = reason
            return a
        a.cost = self.engine.action_cost(state, a)
        a.payment = self.engine.payment_for(state, a) if a.type not in (
            ActionType.END_ROUND, ActionType.CHOOSE_ACTIVE, ActionType.ELEMENTAL_TUNING) else {}
        a.is_fast = self.engine.is_fast(state, a)
        a.label = self.describe(state, a)
        return a

    def describe(self, state: GameState, a: Action) -> str:
        side = state.side(a.side)
        e = self.engine
        if a.type in SKILL_WORD:
            skill = e.skill_def(side.active, a.skill)
            return f"{SKILL_WORD[a.type]}: {skill.name if skill else a.skill} ({e.char_name(side.active)})"
        if a.type == ActionType.SWITCH_CHARACTER:
            fast = " (быстро)" if a.is_fast else ""
            return f"Смена на {e.char_name(side.characters[a.target])}{fast}"
        if a.type == ActionType.CHOOSE_ACTIVE:
            return f"Выбрать активным: {e.char_name(side.characters[a.target])}"
        if a.type == ActionType.PLAY_CARD:
            card = self.kb.card(a.card)
            name = card.name if card else a.card
            if a.target is not None:
                return f"Разыграть «{name}» → {e.char_name(side.characters[a.target])}"
            return f"Разыграть «{name}»"
        if a.type == ActionType.ELEMENTAL_TUNING:
            active_el = e.active_element(side) or Element.PHYSICAL
            tgt = DieType.for_element(active_el)
            card = f" (сбросить «{a.card}»)" if a.card else ""
            return f"Перенастройка: {DIE_RU[a.die]} → {DIE_RU.get(tgt, '?')}{card}"
        if a.type == ActionType.END_ROUND:
            return "Завершить раунд"
        return a.type.value
