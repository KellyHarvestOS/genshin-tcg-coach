"""UI view-model: GameState enriched with names, texts and element info from the knowledge base."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.dice import dice_cost_total
from ..core.state import Character, GameState, PlayerState
from ..rules.engine import RulesEngine

SKILL_TYPE_RU = {"normal": "Обычная атака", "skill": "Элементальный навык", "burst": "Взрыв стихии"}

# Card artwork downloaded from the wiki by `python -m gcoach.tools.wiki_images` (optional).
_IMG_DIR = Path(__file__).resolve().parents[1] / "web" / "img" / "cards"
_img_cache: dict[str, Any] = {"mtime": None, "index": {}}


def card_img(card_id: str | None) -> str | None:
    index_file = _IMG_DIR / "index.json"
    try:
        mtime = index_file.stat().st_mtime
    except OSError:
        return None
    if _img_cache["mtime"] != mtime:
        _img_cache["index"] = json.loads(index_file.read_text(encoding="utf-8"))
        _img_cache["mtime"] = mtime
    name = _img_cache["index"].get(card_id or "")
    return f"/static/img/cards/{name}" if name else None


def _status(engine: RulesEngine, st) -> dict[str, Any]:
    d = engine.kb.status(st.id)
    return {"id": st.id, "name": d.name, "usages": st.usages, "duration": st.duration, "text": d.text,
            "modelled": d.modelled}


def _character(engine: RulesEngine, ch: Character, index: int, active: bool) -> dict[str, Any]:
    d = engine.char_def(ch)
    return {
        "index": index, "id": ch.id, "name": d.name if not d.generic else "Не распознан",
        "known": not d.generic, "element": d.element.value, "weapon": d.weapon,
        "img": None if d.generic else card_img(d.id),
        "hp": ch.hp, "max_hp": ch.max_hp, "energy": ch.energy, "max_energy": ch.max_energy,
        "alive": ch.alive, "active": active, "aura": [a.value for a in ch.aura],
        "statuses": [_status(engine, s) for s in ch.statuses],
        "equipment": [{"id": e.id, "kind": e.kind, "name": (engine.kb.card(e.id).name if engine.kb.card(e.id) else e.id)}
                      for e in ch.equipment],
        "can_act": engine.can_use_skills(ch),
        "skills": [{"id": s.id, "name": s.name, "type": s.type.value, "type_ru": SKILL_TYPE_RU[s.type.value],
                    "cost": s.cost, "text": s.text, "modelled": s.modelled} for s in d.skills],
        "source": d.source,
    }


def _side(engine: RulesEngine, p: PlayerState) -> dict[str, Any]:
    kb = engine.kb
    hand = []
    for cid in p.hand.cards:
        c = kb.card(cid)
        hand.append({"id": cid, "name": c.name if c else cid, "type": c.type if c else "?",
                     "subtype": c.subtype if c else "", "cost": c.cost if c else {},
                     "cost_total": dice_cost_total(c.cost) if c else 0,
                     "text": c.text if c else "", "modelled": bool(c and c.modelled), "img": card_img(cid)})
    return {
        "side": p.side,
        "active_index": p.active_index,
        "characters": [_character(engine, c, i, i == p.active_index) for i, c in enumerate(p.characters)],
        "dice": [{"die": d.value, "count": n} for d, n in sorted(p.dice.counts.items(), key=lambda kv: kv[0].value)],
        "dice_hidden": p.dice.hidden,
        "dice_total": p.dice.total(),
        "hand": hand,
        "hand_hidden": p.hand.hidden,
        "hand_total": p.hand.count(),
        "summons": [{"id": s.id, "name": kb.summon(s.id).name, "usages": s.usages,
                     "element": kb.summon(s.id).element.value if kb.summon(s.id).element else None,
                     "damage": kb.summon(s.id).damage, "text": kb.summon(s.id).text,
                     "img": card_img(s.id)} for s in p.summons],
        "combat_statuses": [_status(engine, s) for s in p.combat_statuses],
        "supports": [{"id": s.id, "name": s.id} for s in p.supports],
        "declared_end": p.declared_end,
    }


def view_state(engine: RulesEngine, s: GameState) -> dict[str, Any]:
    return {
        "round": s.round, "phase": s.phase.value, "active_player": s.active_player,
        "first_to_end": s.first_to_end, "pending_choose": s.pending_choose, "winner": s.winner,
        "player": _side(engine, s.player), "opponent": _side(engine, s.opponent),
    }
