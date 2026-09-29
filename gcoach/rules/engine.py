"""Deterministic rules engine for Genius Invokation TCG.

The engine knows nothing about AI. It validates actions, computes costs, applies
actions to a *copy* of the state and reports what happened as `Event`s.
Card / skill mechanics come from the wiki-imported knowledge base; effects the
importer could not model are simply not applied (and are flagged elsewhere).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.actions import Action
from ..core.dice import apply_discount, dice_cost_total, pay, plan_payment
from ..core.enums import (OPPONENT, PLAYER, ActionType, DieType, Element, Phase, Reaction,
                          SkillType, other_side)
from ..core.state import Character, GameState, PlayerState, Status, Summon
from ..knowledge.base import KnowledgeBase, SkillDef
from .events import Event
from .reactions import REACTION_NAMES_RU, resolve

# Core rules (Genius Invokation TCG rules page).
DICE_PER_ROUND = 8
SWITCH_COST = {"unaligned": 1}
MAX_SUMMONS = 4
MAX_SUPPORTS = 4
MAX_HAND = 10
MAX_ROUNDS = 15
CARDS_DRAWN_AT_END = 2

ELEMENT_RU = {
    Element.CRYO: "Крио", Element.HYDRO: "Гидро", Element.PYRO: "Пиро", Element.ELECTRO: "Электро",
    Element.ANEMO: "Анемо", Element.GEO: "Гео", Element.DENDRO: "Дендро",
    Element.PHYSICAL: "Физ.", Element.PIERCING: "Пронз.",
}


class IllegalAction(ValueError):
    pass


@dataclass
class DamageSource:
    kind: str  # skill / summon / status / reaction / swirl
    attacker_side: str
    char_index: int | None = None
    skill_type: SkillType | None = None
    label: str = ""


@dataclass
class ApplyResult:
    state: GameState
    events: list[Event] = field(default_factory=list)


class RulesEngine:
    def __init__(self, kb: KnowledgeBase, auto_choose_on_death: bool = True):
        self.kb = kb
        self.auto_choose_on_death = auto_choose_on_death

    # ==================================================================================
    # helpers
    # ==================================================================================
    def char_name(self, ch: Character) -> str:
        return self.kb.character(ch.id).name if self.kb.has_character(ch.id) else (ch.id or "?")

    def char_def(self, ch: Character):
        return self.kb.character(ch.id)

    def team_elements(self, side: PlayerState) -> set[Element]:
        return {self.char_def(c).element for c in side.characters if c.alive}

    def active_element(self, side: PlayerState) -> Element | None:
        if not side.characters:
            return None
        return self.char_def(side.active).element

    def skill_def(self, ch: Character, skill_id: str | None) -> SkillDef | None:
        return self.char_def(ch).skill(skill_id) if skill_id else None

    def can_use_skills(self, ch: Character) -> bool:
        if not ch.alive:
            return False
        for s in ch.statuses:
            if self.kb.status(s.id).behaviors.get("cannot_use_skills"):
                return False
        return True

    def switch_cost(self, side: PlayerState) -> dict[str, int]:
        discount = sum(self.kb.status(s.id).behaviors.get("switch_discount", 0) for s in side.combat_statuses)
        return apply_discount(SWITCH_COST, discount)

    def switch_is_fast(self, side: PlayerState) -> bool:
        return any(self.kb.status(s.id).behaviors.get("fast_switch") for s in side.combat_statuses)

    def action_cost(self, state: GameState, action: Action) -> dict[str, int]:
        side = state.side(action.side)
        if action.type in (ActionType.NORMAL_ATTACK, ActionType.ELEMENTAL_SKILL, ActionType.ELEMENTAL_BURST):
            skill = self.skill_def(side.characters[action.actor if action.actor is not None else side.active_index],
                                   action.skill)
            return dict(skill.cost) if skill else {}
        if action.type == ActionType.SWITCH_CHARACTER:
            return self.switch_cost(side)
        if action.type == ActionType.PLAY_CARD:
            card = self.kb.card(action.card or "")
            return dict(card.cost) if card else {}
        return {}

    def payment_for(self, state: GameState, action: Action) -> dict[DieType, int] | None:
        side = state.side(action.side)
        cost = self.action_cost(state, action)
        active_el = self.active_element(side)
        if action.type == ActionType.SWITCH_CHARACTER and action.target is not None:
            # keep the dice the *incoming* character will need
            active_el = self.char_def(side.characters[action.target]).element
        return plan_payment(side.dice, cost, active_el, self.team_elements(side))

    def is_fast(self, state: GameState, action: Action) -> bool:
        if action.type in (ActionType.ELEMENTAL_TUNING, ActionType.CHOOSE_ACTIVE):
            return True
        if action.type == ActionType.SWITCH_CHARACTER:
            return self.switch_is_fast(state.side(action.side))
        if action.type == ActionType.PLAY_CARD:
            card = self.kb.card(action.card or "")
            return not (card and any(e.get("op") == "use_skill" for e in card.effects))
        return False

    # ==================================================================================
    # legality
    # ==================================================================================
    def check(self, state: GameState, action: Action) -> tuple[bool, str]:
        if state.phase == Phase.GAME_OVER:
            return False, "партия окончена"
        side = state.side(action.side)
        if state.pending_choose:
            if action.type != ActionType.CHOOSE_ACTIVE or action.side != state.pending_choose:
                return False, "нужно выбрать нового активного персонажа"
            t = action.target
            if t is None or not (0 <= t < len(side.characters)) or not side.characters[t].alive:
                return False, "персонаж недоступен"
            return True, ""
        if action.type == ActionType.CHOOSE_ACTIVE:
            return False, "выбор персонажа сейчас не требуется"
        if state.phase != Phase.ACTION:
            return False, "сейчас не фаза действий"
        if state.active_player != action.side:
            return False, "сейчас не ваш ход"
        if side.declared_end:
            return False, "раунд уже завершён этой стороной"

        if action.type == ActionType.END_ROUND:
            return True, ""

        if action.type in (ActionType.NORMAL_ATTACK, ActionType.ELEMENTAL_SKILL, ActionType.ELEMENTAL_BURST):
            ch = side.active
            if action.actor is not None and action.actor != side.active_index:
                return False, "навык может использовать только активный персонаж"
            if not self.can_use_skills(ch):
                return False, "персонаж не может использовать навыки"
            skill = self.skill_def(ch, action.skill)
            if skill is None:
                return False, "навык не найден"
            if skill.energy_cost and ch.energy < skill.energy_cost:
                return False, "недостаточно энергии"
            if self.payment_for(state, action) is None:
                return False, "недостаточно кубиков"
            return True, ""

        if action.type == ActionType.SWITCH_CHARACTER:
            t = action.target
            if t is None or not (0 <= t < len(side.characters)):
                return False, "нет такого персонажа"
            if t == side.active_index:
                return False, "персонаж уже активен"
            if not side.characters[t].alive:
                return False, "персонаж побеждён"
            if self.payment_for(state, action) is None:
                return False, "недостаточно кубиков"
            return True, ""

        if action.type == ActionType.ELEMENTAL_TUNING:
            if side.hand.count() == 0:
                return False, "нет карт для перенастройки"
            if action.card is not None and action.card not in side.hand.cards:
                return False, "карты нет в руке"
            target_die = DieType.for_element(self.active_element(side) or Element.PHYSICAL)
            if target_die is None:
                return False, "активный персонаж без элемента"
            if action.die is None or action.die in (DieType.OMNI, target_die):
                return False, "этот кубик нельзя перенастроить"
            if side.dice.hidden == 0 and side.dice.get(action.die) <= 0:
                return False, "нет такого кубика"
            return True, ""

        if action.type == ActionType.PLAY_CARD:
            card = self.kb.card(action.card or "")
            if card is None:
                return False, "карта неизвестна"
            if action.card not in side.hand.cards:
                return False, "карты нет в руке"
            if card.subtype == "weapon":
                t = action.target if action.target is not None else side.active_index
                if not side.characters[t].alive:
                    return False, "персонаж побеждён"
                if self.char_def(side.characters[t]).weapon != card.weapon_type:
                    return False, "неподходящий тип оружия"
            if card.subtype == "talent":
                if self.char_def(side.active).name != card.character:
                    return False, "талант подходит только персонажу " + (card.character or "?")
                use = next((e for e in card.effects if e.get("op") == "use_skill"), None)
                if use:
                    if not self.can_use_skills(side.active):
                        return False, "персонаж не может использовать навыки"
                    skill = self.char_def(side.active).skill(f"{card.character}:{use['skill']}")
                    if skill and skill.energy_cost and side.active.energy < skill.energy_cost:
                        return False, "недостаточно энергии"
            if card.subtype == "food":
                t = action.target if action.target is not None else side.active_index
                if side.characters[t].has_status("satiated"):
                    return False, "персонаж сыт"
            if card.type == "support" and len(side.supports) >= MAX_SUPPORTS:
                return False, "зона поддержки заполнена"
            energy = card.cost.get("energy", 0)
            if energy and side.active.energy < energy:
                return False, "недостаточно энергии"
            if self.payment_for(state, action) is None:
                return False, "недостаточно кубиков"
            return True, ""
        return False, "неизвестное действие"

    def is_legal(self, state: GameState, action: Action) -> bool:
        return self.check(state, action)[0]

    # ==================================================================================
    # apply
    # ==================================================================================
    def apply(self, state: GameState, action: Action) -> ApplyResult:
        ok, reason = self.check(state, action)
        if not ok:
            raise IllegalAction(reason)
        s = state.clone()
        ev: list[Event] = []
        side = s.side(action.side)
        t = action.type
        combat = not self.is_fast(state, action)

        if t == ActionType.CHOOSE_ACTIVE:
            side.active_index = action.target
            ev.append(Event("switch", action.side, f"Выбран активный персонаж: {self.char_name(side.active)}",
                            {"to": action.target, "choose": True}))
            s.pending_choose = None
            s.phase = Phase.ACTION
            s.active_player = s.resume_player or other_side(action.side)
            s.resume_player = None
            return ApplyResult(s, ev)

        if t == ActionType.END_ROUND:
            side.declared_end = True
            if s.first_to_end is None:
                s.first_to_end = action.side
            ev.append(Event("round_end", action.side, "Объявлен конец раунда"))
            other = s.side(other_side(action.side))
            if other.declared_end:
                self.end_phase(s, ev)
            else:
                s.active_player = other.side
            return ApplyResult(s, ev)

        if t == ActionType.ELEMENTAL_TUNING:
            target_die = DieType.for_element(self.active_element(side))
            self._discard(side, action.card)
            if side.dice.hidden:
                pass  # opponent: faces unknown, count unchanged
            else:
                side.dice.remove(action.die)
                side.dice.add(target_die)
            ev.append(Event("dice", action.side, f"Перенастройка: {action.die.value} → {target_die.value}"))
            return ApplyResult(s, ev)

        payment = self.payment_for(s, action)
        if payment is None:
            raise IllegalAction("недостаточно кубиков")
        pay(side.dice, payment)

        if t == ActionType.SWITCH_CHARACTER:
            if not combat:
                self._consume_combat_behavior(side, "fast_switch")
            self._consume_combat_behavior(side, "switch_discount")
            self._switch(s, action.side, action.target, ev)
            for st in list(side.combat_statuses):
                beh = self.kb.status(st.id).behaviors.get("after_switch_damage")
                if beh:
                    self._status_damage(s, action.side, st, beh, ev)
        elif t in (ActionType.NORMAL_ATTACK, ActionType.ELEMENTAL_SKILL, ActionType.ELEMENTAL_BURST):
            skill = self.skill_def(side.active, action.skill)
            self._use_skill(s, action.side, skill, ev)
        elif t == ActionType.PLAY_CARD:
            self._play_card(s, action, ev)

        self._handle_deaths(s, ev)
        if s.phase != Phase.GAME_OVER:
            self._pass_turn(s, action.side, combat)
        return ApplyResult(s, ev)

    # ==================================================================================
    # turn flow
    # ==================================================================================
    def _pass_turn(self, s: GameState, actor: str, combat: bool) -> None:
        nxt = actor
        if combat:
            other = other_side(actor)
            nxt = actor if s.side(other).declared_end else other
        if s.pending_choose:
            s.resume_player = nxt
            s.active_player = s.pending_choose
            s.phase = Phase.CHOOSE_ACTIVE
        else:
            s.active_player = nxt

    def end_phase(self, s: GameState, ev: list[Event]) -> None:
        s.phase = Phase.END
        order = [s.first_to_end or PLAYER]
        order.append(other_side(order[0]))
        for side_name in order:
            side = s.side(side_name)
            for summon in list(side.summons):
                if s.phase == Phase.GAME_OVER:
                    break
                self._summon_end_phase(s, side_name, summon, ev)
            for idx, ch in enumerate(side.characters):
                for st in list(ch.statuses):
                    beh = self.kb.status(st.id).behaviors.get("end_phase_self_damage")
                    if beh and ch.alive:
                        self.deal_damage(s, DamageSource("status", other_side(side_name), label=st.id),
                                         side_name, idx, Element(beh["element"]), beh["amount"], ev)
            self._handle_deaths(s, ev, auto=True)
        if s.phase == Phase.GAME_OVER:
            return
        for side_name in order:
            side = s.side(side_name)
            for ch in side.characters:
                ch.statuses = [st for st in ch.statuses if self._tick_status(st)]
                ch.skill_uses_round = {}
            side.combat_statuses = [st for st in side.combat_statuses if self._tick_status(st)]
            side.declared_end = False
            side.character_died_this_round = False
            drawn = min(CARDS_DRAWN_AT_END, max(0, MAX_HAND - side.hand.count()))
            side.hand.hidden += drawn
            # Dice are re-rolled: faces unknown until the next screenshot.
            side.dice = type(side.dice)({}, DICE_PER_ROUND)
        ev.append(Event("round_end", PLAYER, f"Конец раунда {s.round}"))
        if s.round >= MAX_ROUNDS:
            s.phase = Phase.GAME_OVER
            s.winner = "draw"
            ev.append(Event("game_over", PLAYER, "Лимит раундов — ничья"))
            return
        s.round += 1
        s.phase = Phase.ROLL
        s.active_player = s.first_to_end or PLAYER
        s.first_to_end = None

    def _tick_status(self, st: Status) -> bool:
        sdef = self.kb.status(st.id)
        if sdef.until_round_end:
            return False
        if st.duration is not None:
            st.duration -= 1
            if st.duration <= 0:
                return False
        return True

    # ==================================================================================
    # skills and cards
    # ==================================================================================
    def _use_skill(self, s: GameState, side_name: str, skill: SkillDef, ev: list[Event]) -> None:
        side = s.side(side_name)
        ch = side.active
        uses = ch.skill_uses_round.get(skill.id, 0) + 1
        ch.skill_uses_round[skill.id] = uses
        ev.append(Event("skill", side_name, f"{self.char_name(ch)}: {skill.name}",
                        {"skill": skill.id, "type": skill.type.value}))
        extra = 0
        for e in skill.effects:
            if e.get("op") == "nth_use_bonus" and uses == e["n"]:
                extra += e["amount"]
        source = DamageSource("skill", side_name, side.active_index, skill.type, skill.name)
        first_damage = True
        for e in skill.effects:
            op = e.get("op")
            if s.phase == Phase.GAME_OVER:
                break
            if op == "damage":
                amount = e["amount"] + (extra if first_damage else 0)
                first_damage = False
                opp = s.side(other_side(side_name))
                self.deal_damage(s, source, opp.side, opp.active_index, Element(e["element"]), amount, ev)
            elif op == "piercing":
                opp = s.side(other_side(side_name))
                for i, oc in enumerate(opp.characters):
                    if i != opp.active_index and oc.alive:
                        self.deal_damage(s, source, opp.side, i, Element.PIERCING, e["amount"], ev)
            else:
                self._apply_effect(s, side_name, e, ev, actor=side.active_index)
        # energy
        if skill.type == SkillType.BURST:
            ch.energy = 0
        else:
            ch.energy = min(ch.max_energy, ch.energy + 1)
        # after-skill triggers
        for st in list(side.combat_statuses) + list(ch.statuses):
            beh = self.kb.status(st.id).behaviors
            if skill.type == SkillType.NORMAL and "after_normal_attack_damage" in beh:
                self._status_damage(s, side_name, st, beh["after_normal_attack_damage"], ev)
            if "after_skill_damage" in beh:
                self._status_damage(s, side_name, st, beh["after_skill_damage"], ev)
            if "after_skill_heal" in beh and ch.alive and ch.hp <= beh["after_skill_heal"]["max_hp"]:
                self._heal(ch, beh["after_skill_heal"]["amount"], side_name, ev)

    def _play_card(self, s: GameState, action: Action, ev: list[Event]) -> None:
        side = s.side(action.side)
        card = self.kb.card(action.card)
        self._discard(side, action.card)
        ev.append(Event("card", action.side, f"Разыграна карта: {card.name}", {"card": card.id}))
        if card.cost.get("energy"):
            side.active.energy -= card.cost["energy"]
        target = action.target if action.target is not None else side.active_index
        if card.type == "equipment":
            ch = side.characters[target]
            if card.subtype in ("weapon", "artifact"):
                ch.equipment = [eq for eq in ch.equipment if eq.kind != card.subtype]
            from ..core.state import Equipment
            ch.equipment.append(Equipment(card.id, card.subtype or "equipment"))
        elif card.type == "support":
            side.supports.append(Status(card.id))
        if card.subtype == "food":
            side.characters[target].statuses.append(Status("satiated"))
        for e in card.effects:
            op = e.get("op")
            if op == "use_skill":
                skill = self.char_def(side.active).skill(f"{card.character}:{e['skill']}")
                if skill:
                    self._use_skill(s, action.side, skill, ev)
            elif op == "equip_bonus":
                continue  # read from equipment during damage calculation
            else:
                self._apply_effect(s, action.side, e, ev, actor=target)

    def _apply_effect(self, s: GameState, side_name: str, e: dict, ev: list[Event], actor: int) -> None:
        side = s.side(side_name)
        op = e.get("op")
        if op == "apply_self":
            ch = side.characters[actor]
            _, ch.aura, _ = resolve(Element(e["element"]), ch.aura)
        elif op == "summon":
            self._add_summon(side, e["id"], ev)
        elif op == "status":
            if e.get("scope") == "character":
                self._add_status(side.characters[actor].statuses, e["id"], side_name, ev, e)
            else:
                self._add_status(side.combat_statuses, e["id"], side_name, ev, e)
        elif op == "force_switch":
            target_side = side_name if e.get("side") == "self" else other_side(side_name)
            ts = s.side(target_side)
            nxt = self._neighbour(ts, e.get("direction", "next"))
            if nxt is not None:
                self._switch(s, target_side, nxt, ev, forced=True)
        elif op == "heal":
            tgt = e.get("target", "self")
            if tgt == "all":
                for c in side.characters:
                    if c.alive:
                        self._heal(c, e["amount"], side_name, ev)
            else:
                self._heal(side.characters[actor], e["amount"], side_name, ev)
        elif op == "draw":
            n = min(e["amount"], max(0, MAX_HAND - side.hand.count()))
            side.hand.hidden += n
            ev.append(Event("card", side_name, f"Взято карт: {n}"))
        elif op == "gain_energy":
            ch = side.active
            ch.energy = min(ch.max_energy, ch.energy + e["amount"])
            ev.append(Event("energy", side_name, f"{self.char_name(ch)} +{e['amount']} энергии"))

    def _add_status(self, bucket: list[Status], status_id: str, side_name: str, ev: list[Event],
                    effect: dict | None = None) -> None:
        sdef = self.kb.status(status_id)
        usages = sdef.usages
        if status_id == "next_normal_attack_bonus" and effect and "amount" in effect:
            data = {"amount": effect["amount"]}
        else:
            data = {}
        if sdef.behaviors.get("shield") and usages is None:
            usages = sdef.behaviors["shield"]
        for st in bucket:
            if st.id == status_id:
                if sdef.stack_max and st.usages is not None and usages is not None:
                    st.usages = min(sdef.stack_max, st.usages + usages)
                else:
                    st.usages = max(st.usages or 0, usages) if usages is not None else st.usages
                st.duration = sdef.duration if sdef.duration is not None else st.duration
                return
        bucket.append(Status(status_id, usages, sdef.duration, data))
        ev.append(Event("status", side_name, f"Статус: {sdef.name}", {"status": status_id}))

    def _add_summon(self, side: PlayerState, summon_id: str, ev: list[Event]) -> None:
        sdef = self.kb.summon(summon_id)
        existing = side.summon(summon_id)
        if existing:
            if sdef.stack_max:
                existing.usages = min(sdef.stack_max, existing.usages + sdef.usages)
            else:
                existing.usages = max(existing.usages, sdef.usages)
            return
        if len(side.summons) >= MAX_SUMMONS:
            return
        side.summons.append(Summon(summon_id, sdef.usages))
        ev.append(Event("summon", side.side, f"Призыв: {sdef.name}", {"summon": summon_id}))

    def _summon_end_phase(self, s: GameState, side_name: str, summon: Summon, ev: list[Event]) -> None:
        sdef = self.kb.summon(summon.id)
        side = s.side(side_name)
        acted = False
        if sdef.damage and sdef.element is not None:
            opp = s.side(other_side(side_name))
            src = DamageSource("summon", side_name, label=sdef.name)
            self.deal_damage(s, src, opp.side, opp.active_index, sdef.element, sdef.damage, ev)
            if sdef.piercing:
                for i, oc in enumerate(opp.characters):
                    if i != opp.active_index and oc.alive:
                        self.deal_damage(s, src, opp.side, i, Element.PIERCING, sdef.piercing, ev)
            acted = True
        if sdef.heal:
            targets = side.characters if sdef.heal_target == "all" else [side.active]
            for c in targets:
                if c.alive:
                    self._heal(c, sdef.heal, side_name, ev)
            acted = True
        if acted:
            summon.usages -= 1
            if summon.usages <= 0 and summon in side.summons:
                side.summons.remove(summon)
                ev.append(Event("summon", side_name, f"{sdef.name} покидает поле", {"summon": summon.id}))

    # ==================================================================================
    # damage
    # ==================================================================================
    def deal_damage(self, s: GameState, src: DamageSource, target_side: str, target_idx: int,
                    element: Element, amount: int, ev: list[Event]) -> int:
        tside = s.side(target_side)
        if not (0 <= target_idx < len(tside.characters)):
            return 0
        tgt = tside.characters[target_idx]
        if not tgt.alive:
            return 0
        if element == Element.PIERCING:
            dealt = min(tgt.hp, amount)
            tgt.hp -= dealt
            ev.append(Event("damage", target_side, f"{self.char_name(tgt)}: −{amount} (пронзающий)",
                            {"target": target_idx, "amount": amount, "element": "piercing", "source": src.label}))
            return dealt
        aside = s.side(src.attacker_side)
        attacker = aside.characters[src.char_index] if src.char_index is not None else None

        # 1) element conversion (infusion) for the attacker's own physical skill damage
        if src.kind == "skill" and attacker is not None and element == Element.PHYSICAL:
            for st in attacker.statuses + aside.combat_statuses:
                inf = self.kb.status(st.id).behaviors.get("infusion")
                if inf:
                    element = Element(inf)
                    break

        # 2) additive bonuses
        dmg = amount
        is_target_active = target_idx == tside.active_index
        if src.kind == "skill" and attacker is not None:
            for eq in attacker.equipment:
                card = self.kb.card(eq.id)
                if card:
                    dmg += sum(e["amount"] for e in card.effects if e.get("op") == "equip_bonus")
        buckets = [aside.combat_statuses] + ([attacker.statuses] if attacker is not None else [])
        for bucket in buckets:
            for st in list(bucket):
                beh = self.kb.status(st.id).behaviors.get("damage_bonus")
                if not beh:
                    continue
                if beh.get("elements"):
                    if element.value not in beh["elements"]:
                        continue
                    if beh.get("opponent_active_only") and not is_target_active:
                        continue
                elif src.kind != "skill":
                    continue
                if beh.get("skill_types") and (src.skill_type is None or src.skill_type.value not in beh["skill_types"]):
                    continue
                if beh.get("min_hp") and (attacker is None or attacker.hp < beh["min_hp"]):
                    continue
                dmg += st.data.get("amount", beh["amount"])
                self._use_up(bucket, st)

        # 3) reaction
        reaction, new_aura, consumed = resolve(element, tgt.aura)
        tgt.aura = new_aura
        rinfo = self.kb.reaction(reaction.value) if reaction else {}
        if reaction:
            dmg += rinfo.get("bonus", 0)
            ev.append(Event("reaction", target_side, f"Реакция: {REACTION_NAMES_RU[reaction]}",
                            {"reaction": reaction.value, "target": target_idx}))

        # 4) frozen break
        frozen = tgt.status("frozen")
        if frozen and element in (Element.PHYSICAL, Element.PYRO):
            dmg += self.kb.reaction("frozen").get("frozen_break_bonus", 2)
            tgt.statuses.remove(frozen)

        # 5) vulnerability on the target
        for st in tgt.statuses:
            beh = self.kb.status(st.id).behaviors.get("incoming_bonus")
            if beh and element.value in beh.get("elements", []):
                dmg += beh["amount"]

        # 6) reductions and shields
        dmg = self._defend(tside, tgt, is_target_active, dmg)
        dealt = min(tgt.hp, dmg)
        tgt.hp -= dealt
        ev.append(Event("damage", target_side,
                        f"{self.char_name(tgt)}: −{dmg} ({ELEMENT_RU.get(element, element.value)})",
                        {"target": target_idx, "amount": dmg, "element": element.value, "source": src.label,
                         "reaction": reaction.value if reaction else None}))

        # 7) reaction side effects
        if reaction:
            self._reaction_effects(s, src, reaction, rinfo, target_side, target_idx, consumed, ev)
        return dealt

    def _defend(self, tside: PlayerState, tgt: Character, is_active: bool, dmg: int) -> int:
        if dmg <= 0:
            return 0
        reducers = [tgt.statuses] + ([tside.combat_statuses] if is_active else [])
        for bucket in reducers:
            for st in list(bucket):
                beh = self.kb.status(st.id).behaviors.get("reduce")
                if beh and dmg >= max(1, beh.get("min_damage", 0)) and dmg > 0:
                    dmg = max(0, dmg - beh["amount"])
                    self._use_up(bucket, st)
        if is_active:
            for summon in list(tside.summons):
                beh = self.kb.summon(summon.id).behaviors.get("reduce")
                if beh and dmg > 0 and dmg >= max(1, beh.get("min_damage", 0)):
                    dmg = max(0, dmg - beh["amount"])
                    summon.usages -= 1
                    if summon.usages <= 0:
                        tside.summons.remove(summon)
        shields = [tgt.statuses] + ([tside.combat_statuses] if is_active else [])
        for bucket in shields:
            for st in list(bucket):
                if dmg <= 0:
                    break
                if self.kb.status(st.id).behaviors.get("shield") and st.usages:
                    absorbed = min(st.usages, dmg)
                    st.usages -= absorbed
                    dmg -= absorbed
                    if st.usages <= 0:
                        bucket.remove(st)
        return dmg

    def _reaction_effects(self, s: GameState, src: DamageSource, reaction: Reaction, rinfo: dict,
                          target_side: str, target_idx: int, consumed: Element | None, ev: list[Event]) -> None:
        tside = s.side(target_side)
        aside = s.side(src.attacker_side)
        tgt = tside.characters[target_idx]
        if reaction in (Reaction.SUPERCONDUCT, Reaction.ELECTRO_CHARGED):
            n = rinfo.get("piercing_others", 1)
            for i, c in enumerate(tside.characters):
                if i != target_idx and c.alive:
                    self.deal_damage(s, DamageSource("reaction", src.attacker_side, label=reaction.value),
                                     target_side, i, Element.PIERCING, n, ev)
        elif reaction == Reaction.OVERLOADED:
            if target_idx == tside.active_index and tgt.hp > 0:
                nxt = self._neighbour(tside, "next")
                if nxt is not None:
                    self._switch(s, target_side, nxt, ev, forced=True)
        elif reaction == Reaction.FROZEN:
            if tgt.hp > 0 and not tgt.has_status("frozen"):
                tgt.statuses.append(Status("frozen"))
        elif reaction == Reaction.SWIRL and consumed is not None and src.kind != "swirl":
            n = rinfo.get("swirl_damage", 1)
            for i, c in enumerate(tside.characters):
                if i != target_idx and c.alive:
                    self.deal_damage(s, DamageSource("swirl", src.attacker_side, label="swirl"),
                                     target_side, i, consumed, n, ev)
        elif reaction == Reaction.CRYSTALLIZE:
            self._add_status(aside.combat_statuses, "crystallize_shield", aside.side, ev)
        elif reaction == Reaction.BLOOM:
            self._add_status(aside.combat_statuses, "dendro_core", aside.side, ev)
        elif reaction == Reaction.QUICKEN:
            self._add_status(aside.combat_statuses, "catalyzing_field", aside.side, ev)
        elif reaction == Reaction.BURNING:
            self._add_summon(aside, "burning_flame", ev)

    def _status_damage(self, s: GameState, side_name: str, st: Status, beh: dict, ev: list[Event]) -> None:
        side = s.side(side_name)
        opp = s.side(other_side(side_name))
        self.deal_damage(s, DamageSource("status", side_name, label=st.id), opp.side, opp.active_index,
                         Element(beh["element"]), beh["amount"], ev)
        for bucket in [side.combat_statuses] + [c.statuses for c in side.characters]:
            if st in bucket:
                self._use_up(bucket, st)
                break

    # ==================================================================================
    # small mechanics
    # ==================================================================================
    def _use_up(self, bucket: list[Status], st: Status) -> None:
        if st.usages is None:
            return
        st.usages -= 1
        if st.usages <= 0 and st in bucket:
            bucket.remove(st)

    def _consume_combat_behavior(self, side: PlayerState, key: str) -> None:
        for st in list(side.combat_statuses):
            if self.kb.status(st.id).behaviors.get(key):
                self._use_up(side.combat_statuses, st)
                return

    def _heal(self, ch: Character, amount: int, side_name: str, ev: list[Event]) -> None:
        before = ch.hp
        ch.hp = min(ch.max_hp, ch.hp + amount)
        if ch.hp > before:
            ev.append(Event("heal", side_name, f"{self.char_name(ch)}: +{ch.hp - before} HP"))

    def _neighbour(self, side: PlayerState, direction: str) -> int | None:
        n = len(side.characters)
        step = 1 if direction == "next" else -1
        for k in range(1, n):
            i = (side.active_index + step * k) % n
            if side.characters[i].alive:
                return i
        return None

    def _switch(self, s: GameState, side_name: str, target: int, ev: list[Event], forced: bool = False) -> None:
        side = s.side(side_name)
        if target == side.active_index or not side.characters[target].alive:
            return
        side.active_index = target
        verb = "Принудительная смена" if forced else "Смена"
        ev.append(Event("switch", side_name, f"{verb}: {self.char_name(side.active)}",
                        {"to": target, "forced": forced}))

    def _discard(self, side: PlayerState, card_id: str | None) -> None:
        if card_id is not None and card_id in side.hand.cards:
            side.hand.cards.remove(card_id)
        elif side.hand.hidden > 0:
            side.hand.hidden -= 1

    def best_replacement(self, side: PlayerState) -> int | None:
        alive = side.alive_indices()
        if not alive:
            return None
        return max(alive, key=lambda i: side.characters[i].hp + 1.5 * side.characters[i].energy)

    def _handle_deaths(self, s: GameState, ev: list[Event], auto: bool | None = None) -> None:
        auto = self.auto_choose_on_death if auto is None else auto
        for side_name in (PLAYER, OPPONENT):
            side = s.side(side_name)
            for i, ch in enumerate(side.characters):
                if ch.alive and ch.hp <= 0:
                    ch.alive = False
                    ch.hp = 0
                    ch.energy = 0
                    ch.statuses = []
                    ch.equipment = []
                    ch.aura = []
                    side.character_died_this_round = True
                    ev.append(Event("death", side_name, f"{self.char_name(ch)} побеждён", {"index": i}))
            if side.all_dead():
                s.phase = Phase.GAME_OVER
                s.winner = other_side(side_name)
                ev.append(Event("game_over", side_name, "Все персонажи побеждены"))
                return
            if not side.active.alive:
                repl = self.best_replacement(side)
                if auto or repl is None:
                    if repl is not None:
                        side.active_index = repl
                        ev.append(Event("switch", side_name, f"Новый активный: {self.char_name(side.active)}",
                                        {"to": repl, "choose": True}))
                else:
                    s.pending_choose = side_name

    # ==================================================================================
    # threat estimation (used by evaluation / explanations, not by rules)
    # ==================================================================================
    def max_skill_damage(self, s: GameState, side_name: str, dice_budget: int | None = None) -> tuple[int, str]:
        """Rough upper bound of damage the side's active character can deal with one skill now."""
        side = s.side(side_name)
        opp = s.side(other_side(side_name))
        if not side.characters or not self.can_use_skills(side.active):
            return 0, ""
        budget = side.dice.total() if dice_budget is None else dice_budget
        best, best_name = 0, ""
        for skill in self.char_def(side.active).skills:
            if skill.energy_cost and side.active.energy < skill.energy_cost:
                continue
            if dice_cost_total(skill.cost) > budget:
                continue
            dmg = 0
            for e in skill.effects:
                if e.get("op") == "damage":
                    dmg += e["amount"]
                    el = Element(e["element"])
                    if opp.characters and opp.active.alive:
                        r, _, _ = resolve(el, opp.active.aura)
                        if r:
                            dmg += self.kb.reaction(r.value).get("bonus", 0)
                elif e.get("op") == "piercing":
                    dmg += e["amount"]
            if dmg > best:
                best, best_name = dmg, skill.name
        return best, best_name
