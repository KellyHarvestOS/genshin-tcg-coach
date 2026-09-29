"""Short spoken phrases for the voice assistant, built from a recommendation.

One phrase = what to do + at most one reason, so it can be heard between moves.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.actions import Action
from ..core.enums import ActionType
from ..core.state import GameState
from ..rules.engine import RulesEngine

SKILL_WORDS = {
    ActionType.NORMAL_ATTACK: "обычная атака",
    ActionType.ELEMENTAL_SKILL: "элементальный навык",
    ActionType.ELEMENTAL_BURST: "взрыв стихии",
}
# reasons that are planner jargon rather than something worth saying out loud
_SILENT_REASONS = ("Улучшает позицию", "Создаёт более сильную линию", "Лучший результат по оценке", "Быстрое действие")


def action_phrase(engine: RulesEngine, state: GameState, a: Action) -> str:
    side = state.side(a.side)
    if a.type in SKILL_WORDS:
        ch = side.characters[a.actor] if a.actor is not None else side.active
        name = engine.char_name(ch)
        if a.type == ActionType.NORMAL_ATTACK:
            return f"{name}: обычная атака"
        skill = engine.skill_def(ch, a.skill)
        return f"{name}: {SKILL_WORDS[a.type]} «{skill.name if skill else a.skill}»"
    if a.type == ActionType.SWITCH_CHARACTER:
        return f"Смена персонажа: {engine.char_name(side.characters[a.target])}"
    if a.type == ActionType.CHOOSE_ACTIVE:
        return f"Выберите активным: {engine.char_name(side.characters[a.target])}"
    if a.type == ActionType.PLAY_CARD:
        card = engine.kb.card(a.card)
        text = f"Разыграйте карту «{card.name if card else a.card}»"
        if a.target is not None:
            text += f", цель — {engine.char_name(side.characters[a.target])}"
        return text
    if a.type == ActionType.ELEMENTAL_TUNING:
        card = engine.kb.card(a.card) if a.card else None
        return "Перенастройте кубик" + (f", сбросив карту «{card.name if card else a.card}»" if a.card else "")
    if a.type == ActionType.END_ROUND:
        return "Завершайте раунд"
    return a.label or a.type.value


def _reason(why: list[str]) -> str:
    for w in why:
        if w.startswith("Побеждает в партии"):
            return "Это победа!"
        if w.startswith(_SILENT_REASONS):
            continue
        w = re.sub(r"\s*\([^)]*\)", "", w).strip().rstrip(".")
        return w[:1].upper() + w[1:] + "." if w else ""
    return ""


def speech_text(engine: RulesEngine, state: GameState | None, rec: dict[str, Any] | None) -> str:
    if not rec:
        return ""
    status = rec.get("status")
    if status == "game_over":
        return rec.get("message") or "Партия окончена."
    if status == "waiting_opponent":
        return "Ход противника."
    if status == "uncertain_state":
        return "Не могу распознать поле. Обновите экран."
    if status == "no_actions":
        return "Доступных действий нет."
    if status != "ok" or not rec.get("action") or state is None:
        return rec.get("message", "")
    try:
        phrase = action_phrase(engine, state, Action.from_dict(rec["action"]))
    except (IndexError, KeyError, ValueError):
        phrase = rec.get("title", "")
    reason = _reason(rec.get("why") or [])
    return f"{phrase}. {reason}" if reason else f"{phrase}."
