"""Turn a PlanResult into a human-readable recommendation (Russian UI text)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..core.actions import Action
from ..core.dice import describe_payment
from ..core.enums import OPPONENT, PLAYER, ActionType, Element, Phase
from ..core.state import GameState
from ..rules.engine import ELEMENT_RU, RulesEngine
from ..rules.reactions import REACTION_NAMES_RU, reactions_available
from ..core.enums import Reaction
from .evaluation import FEATURE_NAMES_RU, Evaluation
from .planner import PlanResult, RootOption


@dataclass
class Recommendation:
    status: str  # ok / uncertain_state / waiting_opponent / game_over / no_actions
    message: str = ""
    title: str = ""
    action: dict[str, Any] | None = None
    action_type: str = ""
    why: list[str] = field(default_factory=list)
    cost_text: str = ""
    payment: list[dict[str, Any]] = field(default_factory=list)
    expected_result: list[str] = field(default_factory=list)
    future_plan: list[dict[str, Any]] = field(default_factory=list)
    risk: list[str] = field(default_factory=list)
    confidence: float = 0.0
    decision_confidence: float = 0.0
    state_confidence: float = 0.0
    win_probability: float = 0.5
    win_probability_now: float = 0.5
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    search: dict[str, Any] = field(default_factory=dict)
    expected_state: dict[str, Any] | None = None  # for verification after the player acts

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class RecommendationBuilder:
    def __init__(self, engine: RulesEngine):
        self.engine = engine
        self.kb = engine.kb

    # ------------------------------------------------------------------------------------
    def uncertain(self, state_confidence: float, problems: list[str]) -> Recommendation:
        return Recommendation(
            "uncertain_state",
            "Не удалось уверенно распознать состояние. Обновите экран или сделайте скриншот.",
            state_confidence=state_confidence, notes=problems,
        )

    def build(self, s: GameState, plan: PlanResult, state_confidence: float = 1.0) -> Recommendation:
        search = {"depth": plan.depth, "nodes": plan.nodes, "elapsed_ms": round(plan.elapsed_ms),
                  "critical": plan.critical, "timed_out": plan.timed_out}
        if s.phase == Phase.GAME_OVER:
            msg = {"player": "Победа!", "opponent": "Поражение.", "draw": "Ничья."}.get(s.winner or "", "Партия окончена.")
            return Recommendation("game_over", msg, state_confidence=state_confidence, search=search)
        if plan.waiting_for_opponent:
            rec = Recommendation("waiting_opponent", "Ход противника. Ждём его действия.",
                                 state_confidence=state_confidence, search=search,
                                 win_probability_now=plan.root_eval.win_probability,
                                 win_probability=plan.root_eval.win_probability)
            for o in plan.options[:3]:
                dmg = self._damage_to(o.immediate_events, PLAYER)
                rec.future_plan.append({"side": OPPONENT, "label": o.action.label,
                                        "probability": round(o.action.confidence, 2),
                                        "detail": f"до {dmg} урона" if dmg else ""})
            rec.notes.append("Вероятности учитывают только видимую информацию: руку противника не видно.")
            return rec
        if not plan.options:
            return Recommendation("no_actions", "Нет доступных действий.", state_confidence=state_confidence,
                                  search=search)

        best = plan.options[0]
        second = plan.options[1] if len(plan.options) > 1 else None
        margin = best.win_probability - (second.win_probability if second else 0.0)
        decision_conf = 1.0 if second is None else 0.5 + 0.5 * math.tanh(margin * 12)
        rec = Recommendation(
            "ok", title=best.action.label, action=best.action.to_dict(), action_type=best.action.type.value,
            state_confidence=round(state_confidence, 3),
            decision_confidence=round(decision_conf, 3),
            confidence=round(state_confidence * (0.6 + 0.4 * decision_conf), 3),
            win_probability=round(best.win_probability, 3),
            win_probability_now=round(plan.root_eval.win_probability, 3),
            search=search,
        )
        rec.cost_text = self._cost_text(best.action)
        rec.payment = [{"die": d.value, "count": n} for d, n in (best.action.payment or {}).items()]
        rec.expected_result = self._expected(s, best)
        rec.why = self._why(s, best, plan.root_eval)
        rec.future_plan = self._future(best)
        rec.risk = self._risk(s, best)
        rec.alternatives = self._alternatives(plan.options)
        rec.notes = self._notes(s, best)
        rec.expected_state = best.after_state.to_dict() if best.after_state else None
        return rec

    # ------------------------------------------------------------------------------------
    def _cost_text(self, a: Action) -> str:
        if a.type == ActionType.END_ROUND:
            return "Бесплатно"
        if a.type == ActionType.ELEMENTAL_TUNING:
            return "1 карта из руки"
        text = describe_payment(a.payment)
        if a.cost.get("energy"):
            text += f" + {a.cost['energy']} энергии"
        if a.is_fast and a.type != ActionType.CHOOSE_ACTIVE:
            text += " · быстрое действие"
        return text

    def _damage_to(self, events, side: str) -> int:
        return sum(e.data.get("amount", 0) for e in events if e.kind == "damage" and e.side == side)

    def _expected(self, s: GameState, opt: RootOption) -> list[str]:
        out = []
        for e in opt.immediate_events:
            if e.kind in ("damage", "reaction", "summon", "status", "switch", "death", "heal", "energy",
                          "card", "dice"):
                out.append(e.text)
        after = opt.after_state
        if after:
            for i, (b, a) in enumerate(zip(s.opponent.characters, after.opponent.characters)):
                if a.hp != b.hp:
                    out.append(f"{self.engine.char_name(a)}: {b.hp} → {a.hp} HP")
        seen, uniq = set(), []
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq[:6] or ["Без немедленного эффекта"]

    def _why(self, s: GameState, opt: RootOption, root: Evaluation) -> list[str]:
        reasons: list[str] = []
        a = opt.action
        after = opt.after_state
        ev = opt.immediate_events
        dmg = self._damage_to(ev, OPPONENT)
        reactions = [e for e in ev if e.kind == "reaction" and e.side == OPPONENT]
        deaths = [e for e in ev if e.kind == "death" and e.side == OPPONENT]
        if after and after.phase == Phase.GAME_OVER and after.winner == PLAYER:
            reasons.append("Побеждает в партии прямо сейчас")
        for d in deaths:
            reasons.append(d.text.replace("побеждён", "— персонаж противника побеждён"))
        if dmg:
            reasons.append(f"Наносит {dmg} урона")
        for r in reactions:
            code = r.data.get("reaction")
            bonus = self.kb.reaction(code).get("bonus")
            reasons.append(f"Вызывает реакцию «{REACTION_NAMES_RU[Reaction(code)]}»" + (f" (+{bonus} урона)" if bonus else ""))
        if after:
            reasons += self._setup_reasons(s, after)
            me_b, me_a = s.player, after.player
            if me_a.active.alive and me_a.active.energy >= me_a.active.max_energy > me_b.active.energy and a.type != ActionType.ELEMENTAL_BURST:
                reasons.append("Заполняет энергию — взрыв стихии будет готов")
            new_summons = {x.id for x in me_a.summons} - {x.id for x in me_b.summons}
            for sid in new_summons:
                sd = self.kb.summon(sid)
                if sd.damage:
                    reasons.append(f"Призыв «{sd.name}»: {sd.damage} урона в конце раунда ×{sd.usages}")
                else:
                    reasons.append(f"Призыв «{sd.name}»")
            new_status = {x.id for x in me_a.combat_statuses} - {x.id for x in me_b.combat_statuses}
            for sid in new_status:
                if sid == "fast_switch":
                    continue
                reasons.append(f"Создаёт «{self.kb.status(sid).name}»")
        if a.is_fast and a.type != ActionType.END_ROUND:
            reasons.append("Быстрое действие — ход остаётся за вами")
        if a.type == ActionType.SWITCH_CHARACTER:
            old = s.player.active
            if old.hp <= 4:
                reasons.append(f"Уводит раненого персонажа ({self.engine.char_name(old)}, {old.hp} HP) из-под удара")
            new = s.player.characters[a.target]
            el = self.engine.char_def(new).element
            if s.opponent.active.alive and reactions_available(el, s.opponent.active.aura):
                reasons.append(f"{self.engine.char_name(new)} сможет вызвать реакцию по ауре противника")
        if a.type == ActionType.END_ROUND:
            if s.first_to_end is None and not s.opponent.declared_end:
                reasons.append("Завершив раунд первым, вы ходите первым в следующем раунде")
            reasons.append("Выгодных действий на оставшиеся ресурсы нет")
        if root.features.get("threat") and after and not self._eval_feature(opt, "threat"):
            reasons.append("Снимает угрозу добивания вашего активного персонажа")
        if opt.static_eval:
            deltas = {k: opt.static_eval.features.get(k, 0) - root.features.get(k, 0)
                      for k in FEATURE_NAMES_RU if k not in ("terminal", "hp", "alive", "dice")}
            top = [k for k, v in sorted(deltas.items(), key=lambda kv: -kv[1]) if v > 0.6][:2]
            if top and len(reasons) < 5:
                reasons.append("Улучшает позицию: " + ", ".join(FEATURE_NAMES_RU[k] for k in top))
        own_line = [st for st in opt.line[1:] if st.side == PLAYER]
        if own_line and len(reasons) < 6:
            reasons.append("Создаёт более сильную линию на следующий ход")
        return reasons[:6] or ["Лучший результат по оценке позиции на несколько ходов вперёд"]

    def _eval_feature(self, opt: RootOption, key: str) -> float:
        return opt.static_eval.features.get(key, 0.0) if opt.static_eval else 0.0

    def _setup_reasons(self, s: GameState, after: GameState) -> list[str]:
        out = []
        ob, oa = s.opponent, after.opponent
        if oa.active.alive and oa.active_index == ob.active_index:
            new_aura = set(oa.active.aura) - set(ob.active.aura)
            for el in new_aura:
                for i in after.player.alive_indices():
                    our_el = self.engine.char_def(after.player.characters[i]).element
                    r = reactions_available(our_el, oa.active.aura)
                    if r:
                        out.append(f"Накладывает {ELEMENT_RU.get(el, el.value)} на врага — готовит реакцию "
                                   f"«{REACTION_NAMES_RU[r]}» для {self.engine.char_name(after.player.characters[i])}")
                        return out
        if oa.active_index != ob.active_index and oa.active.alive:
            out.append(f"Меняет активного персонажа противника на {self.engine.char_name(oa.active)}")
        return out

    def _future(self, opt: RootOption) -> list[dict[str, Any]]:
        plan = []
        for st in opt.line[1:7]:
            detail = ""
            if st.side == OPPONENT:
                dmg = self._damage_to(st.events, PLAYER)
                detail = f"до {dmg} урона вам" if dmg else ""
            else:
                dmg = self._damage_to(st.events, OPPONENT)
                detail = f"{dmg} урона" if dmg else ""
            plan.append({"side": st.side, "label": st.action.label, "probability": round(st.probability, 2),
                         "detail": detail})
        return plan

    def _risk(self, s: GameState, opt: RootOption) -> list[str]:
        risks = []
        if opt.replies:
            worst = max(opt.replies, key=lambda r: r["damage_to_us"])
            if worst["damage_to_us"] > 0:
                risks.append(f"Противник может ответить «{worst['label']}»: до {worst['damage_to_us']} урона")
        if opt.static_eval and opt.static_eval.features.get("threat"):
            risks.append("Ваш активный персонаж остаётся под угрозой добивания")
        hidden = s.opponent.hand.count()
        if hidden:
            risks.append(f"У противника {hidden} скрытых карт — их эффекты не учтены")
        if s.player.active.alive and s.player.active.aura:
            names = ", ".join(ELEMENT_RU.get(e, e.value) for e in s.player.active.aura)
            risks.append(f"На вашем активном персонаже аура ({names}) — противник может вызвать реакцию")
        return risks[:4] or ["Существенных рисков не обнаружено"]

    def _alternatives(self, options: list[RootOption]) -> list[dict[str, Any]]:
        best = options[0]
        out, seen = [], {best.action.type}
        pool = options[1:]
        # first: distinct action types, then fill with the rest
        for pass_distinct in (True, False):
            for o in pool:
                if len(out) >= 3:
                    break
                if any(x["label"] == o.action.label for x in out):
                    continue
                if pass_distinct and o.action.type in seen:
                    continue
                seen.add(o.action.type)
                out.append({
                    "label": o.action.label, "type": o.action.type.value,
                    "win_probability": round(o.win_probability, 3),
                    "delta": round(o.win_probability - best.win_probability, 3),
                    "cost": self._cost_text(o.action),
                })
        return out

    def _notes(self, s: GameState, opt: RootOption) -> list[str]:
        notes = []
        a = opt.action
        if a.skill:
            skill = self.engine.skill_def(s.player.active, a.skill)
            if skill and skill.unparsed:
                notes.append("Часть эффекта навыка не смоделирована: " + " ".join(skill.unparsed)[:160])
        for ch in s.opponent.characters:
            if not self.kb.has_character(ch.id):
                notes.append("Персонаж противника не распознан — оценка приблизительная")
                break
        unmodelled = [c for c in s.player.hand.cards if (cd := self.kb.card(c)) is None or not cd.modelled]
        if unmodelled:
            notes.append("Эффекты карт не смоделированы и не рассматриваются: " + ", ".join(dict.fromkeys(unmodelled)))
        return notes
