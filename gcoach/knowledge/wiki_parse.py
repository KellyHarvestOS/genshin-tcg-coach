"""Turn cached wiki pages into the structured knowledge base used by the rules engine.

Only mechanics that can be read unambiguously from the wiki text become
machine effects. Everything else is kept verbatim in ``unparsed`` so the
coach can say "this effect is not modelled" instead of inventing behaviour.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .wikitext import clean, find_templates

ELEMENTS_RU = {
    "Физический": "physical", "Пиро": "pyro", "Гидро": "hydro", "Электро": "electro",
    "Крио": "cryo", "Анемо": "anemo", "Гео": "geo", "Дендро": "dendro",
}
EL = "(Физический|Пиро|Гидро|Электро|Крио|Анемо|Гео|Дендро)"
SKILL_TYPES = {
    "Обычная атака": "normal", "Элементальный навык": "skill", "Взрыв стихии": "burst",
    "Пассивный навык": "passive", "Техника": "technique",
}
WEAPONS = {
    "Одноручное": "sword", "Двуручное": "claymore", "Древковое": "polearm",
    "Стрелковое": "bow", "Катализатор": "catalyst", "Прочее": "other",
}
CARD_TYPES = {
    "Карта экипировки": "equipment", "Карта поддержки": "support", "Карта события": "event",
}
ORDINALS = {"втором": 2, "третьем": 3, "четвёртом": 4, "четвертом": 4}
WIKI_URL = "https://genshin-impact.fandom.com/ru/wiki/"


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    m = re.search(r"-?\d+", text)
    return int(m.group()) if m else None


def _page_url(title: str) -> str:
    return WIKI_URL + title.replace(" ", "_")


# --------------------------------------------------------------------------------------
# costs
# --------------------------------------------------------------------------------------
def parse_skill_cost(raw: str) -> dict[str, int]:
    cost: dict[str, int] = {}
    for tpl in find_templates(raw, "СП7 Стоимость"):
        if not tpl.positional:
            continue
        n = _int(tpl.positional[0]) or 0
        kind = tpl.positional[1] if len(tpl.positional) > 1 else ""
        if kind == "":
            key = "unaligned"
        elif kind == "Энергия":
            key = "energy"
        elif kind == "Соответствующий":
            key = "same"
        elif kind in ("Неопределённый", "Несоответствующий"):
            key = "unaligned"
        elif kind in ELEMENTS_RU:
            key = ELEMENTS_RU[kind]
        else:
            key = "unknown"
        cost[key] = cost.get(key, 0) + n
    return cost


def parse_card_cost(ib: dict[str, str]) -> dict[str, int]:
    cost: dict[str, int] = {}
    same = _int(ib.get("Соответствующий"))
    element = ELEMENTS_RU.get(ib.get("Элемент", ""))
    if same is not None:
        if element and element != "physical":
            cost[element] = same  # element-specific dice (talent cards)
        elif same:
            cost["same"] = same
    unaligned = _int(ib.get("Неопределённый"))
    if unaligned:
        cost["unaligned"] = unaligned
    energy = _int(ib.get("Энергия"))
    if energy:
        cost["energy"] = energy
    return cost


# --------------------------------------------------------------------------------------
# effect blocks (statuses) inside skill descriptions
# --------------------------------------------------------------------------------------
def parse_status_block(block_raw: str) -> dict[str, Any] | None:
    """Parse one {{СП7 Эффект|...}} body describing a status. Returns None for summon refs."""
    if "{{СП7 Помощник" in block_raw:
        return None
    icon = find_templates(block_raw, "Icon/СП7")
    icon_name = icon[0].positional[0] if icon and icon[0].positional else ""
    m = re.search(r"'''(.+?)'''", block_raw)
    if not m:
        return None
    name = clean(m.group(1))
    body = clean(block_raw[m.end():])
    status: dict[str, Any] = {"name": name, "icon": icon_name, "text": body, "behaviors": {}}
    b = status["behaviors"]
    used: list[str] = []

    def hit(pattern: str, flags: int = 0):
        mm = re.search(pattern, body, flags)
        if mm:
            used.append(mm.group(0))
        return mm

    if mm := hit(r"(?:Кол-во|Количество) применений:\s*(\d+)"):
        status["usages"] = int(mm.group(1))
    if mm := hit(r"Продолжительность \(в раундах\):\s*(\d+)"):
        status["duration"] = int(mm.group(1))
    if mm := hit(r"(?:складывается|Складывается|складываться) до (\d+)"):
        status["stack_max"] = int(mm.group(1))
    parse_behaviors(body, b, hit)
    if hit(r"Действует до конца раунда"):
        status["until_round_end"] = True
    status["unparsed"] = _leftover(body, used)
    status["modelled"] = bool(b)
    status["partial"] = bool(b) and bool(status["unparsed"])
    return status


def _leftover(body: str, used: list[str]) -> str:
    """Text that no pattern consumed; short fragments are dropped."""
    leftover = body
    for u in used:
        leftover = leftover.replace(u, "")
    leftover = re.sub(r"\((?:[^()]*)\)", " ", leftover)
    leftover = re.sub(r"[\s.:;,()]+", " ", leftover).strip()
    return leftover if len(leftover) > 15 else ""


def parse_behaviors(body: str, b: dict[str, Any], hit) -> None:
    """Shared status / summon behaviour patterns (wiki Russian wording)."""
    if mm := hit(r"получает не менее (\d+) ед\. урона: снижает (?:этот )?получаемый урон на (\d+)"):
        b["reduce"] = {"min_damage": int(mm.group(1)), "amount": int(mm.group(2))}
    elif mm := hit(r"получает урон: (?:урон снижается|снижает (?:получаемый )?урон|получаемый урон снижается) на (\d+)"):
        b["reduce"] = {"min_damage": 0, "amount": int(mm.group(1))}
    elif mm := hit(r"получает на (\d+) ед\. урона меньше"):
        b["reduce"] = {"min_damage": 0, "amount": int(mm.group(1))}
    if mm := hit(r"[Щщ]ит(?:а)?,? \(?(\d+) ед"):
        b["shield"] = int(mm.group(1))
    elif mm := hit(r"(\d+) ед\.? Щит"):
        b["shield"] = int(mm.group(1))
    elif mm := hit(r"Щит ×(\d+)"):
        b["shield"] = int(mm.group(1))
    if mm := hit(rf"{EL} урон[^.;]*?(?:превращается|заменяется) (?:в|на) {EL} урон"):
        b["infusion"] = ELEMENTS_RU[mm.group(2)]
    if mm := hit(rf"Когда ваш персонаж (?:использует|совершает) обычную атаку:\s*наносит(?:ся)? {EL} урон,? (\d+) ед"):
        b["after_normal_attack_damage"] = {"element": ELEMENTS_RU[mm.group(1)], "amount": int(mm.group(2))}
    if mm := hit(rf"(?:После смены персонажа[^:]*|Когда вы (?:меняете|сменяете) активного персонажа|При смене персонажа[^:]*):[^.]*?(?:получает|наносит(?:ся)?) {EL} урон,? (\d+) ед"):
        b["after_switch_damage"] = {"element": ELEMENTS_RU[mm.group(1)], "amount": int(mm.group(2))}
    if mm := hit(rf"После того как (?:любой )?ваш персонаж[^:]*использует навык:\s*наносит {EL} урон,? (\d+) ед"):
        b["after_skill_damage"] = {"element": ELEMENTS_RU[mm.group(1)], "amount": int(mm.group(2))}
    if mm := hit(rf"Финальная фаза:\s*(?:наносит персонажу в этом состоянии|все персонажи в этом состоянии получают|персонажи в этом состоянии получают) {EL} урон,? (\d+) ед"):
        b["end_phase_self_damage"] = {"element": ELEMENTS_RU[mm.group(1)], "amount": int(mm.group(2))}
    if mm := hit(rf"{EL} урон ваших персонажей увеличивается на (\d+)"):
        b["damage_bonus"] = {"amount": int(mm.group(2)), "elements": [ELEMENTS_RU[mm.group(1)]]}
    elif mm := hit(rf"Когда активный персонаж противника получает от вас {EL} урон или {EL} урон, урон увеличивается на (\d+)"):
        b["damage_bonus"] = {"amount": int(mm.group(3)), "elements": [ELEMENTS_RU[mm.group(1)], ELEMENTS_RU[mm.group(2)]]}
    elif mm := hit(r"если у персонажа не менее (\d+) HP, текущий урон увеличится на (\d+)"):
        b["damage_bonus"] = {"amount": int(mm.group(2)), "min_hp": int(mm.group(1))}
        if mh := hit(r"не более (\d+) HP, персонаж восстановит (\d+) HP"):
            b["after_skill_heal"] = {"max_hp": int(mh.group(1)), "amount": int(mh.group(2))}
    elif mm := hit(r"(?:[Оо]бычн(?:ая|ые) атак[аи] [^.]*?|при обычной атаке )наносит на (\d+) ед\. урона больше"):
        b["damage_bonus"] = {"amount": int(mm.group(1)), "skill_types": ["normal"]}
    elif mm := hit(r"\+(\d+) к обычной атаке"):
        b["damage_bonus"] = {"amount": int(mm.group(1)), "skill_types": ["normal"]}
    elif mm := hit(r"(?:увеличивает собственный урон|Ваш урон увеличивается) на (\d+)"):
        b["damage_bonus"] = {"amount": int(mm.group(1))}
    if mm := hit(rf"{EL} урон и {EL} урон, который получает персонаж в этом состоянии, увеличиваются на (\d+)"):
        b["incoming_bonus"] = {"amount": int(mm.group(3)), "elements": [ELEMENTS_RU[mm.group(1)], ELEMENTS_RU[mm.group(2)]]}
    if mm := hit(r"(?:Смена персонажа расходует|сменяете персонажа: расходуется) на (\d+) дайс меньше"):
        b["switch_discount"] = int(mm.group(1))
    if hit(r"Персонаж не может использовать навыки"):
        b["cannot_use_skills"] = True


# --------------------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------------------
def parse_skill_description(raw: str) -> tuple[list[dict], list[dict], list[str], str]:
    """Return (effects, statuses, unparsed_sentences, plain_text)."""
    blocks = find_templates(raw, "СП7 Эффект")
    main_raw = raw
    for blk in blocks:
        main_raw = main_raw.replace(blk.raw, "")
    statuses = [s for blk in blocks if (s := parse_status_block(blk.positional[0] if blk.positional else ""))]
    summon_links = re.findall(r"призывает помощника:?\s*'*\[\[СП7:([^|\]]+)", main_raw, re.I)
    created = re.findall(r"(?:создаёт|создаются|возникает|получает|достаётся)\s+'''([^']+)'''", main_raw)
    text = clean(main_raw)
    effects: list[dict] = []
    unparsed: list[str] = []
    sentences = [s.strip() for s in re.split(r"(?<=[.!])\s+", text) if s.strip()]
    for sentence in sentences:
        consumed = False
        for mm in re.finditer(rf"[Нн]аносит {EL} урон,? (\d+) ед", sentence):
            effects.append({"op": "damage", "element": ELEMENTS_RU[mm.group(1)], "amount": int(mm.group(2))})
            consumed = True
        if mm := re.search(r"Проникающий урон,? (\d+) ед", sentence):
            effects.append({"op": "piercing", "amount": int(mm.group(1)), "target": "opponent_standby"})
            consumed = True
        if mm := re.search(rf"даёт персонажу (?:Эффект )?{EL}", sentence):
            effects.append({"op": "apply_self", "element": ELEMENTS_RU[mm.group(1)]})
            consumed = True
        if mm := re.search(r"Принуждает соперника сделать активным (предыдущего|следующего) персонажа", sentence):
            effects.append({"op": "force_switch", "side": "opponent",
                            "direction": "prev" if mm.group(1) == "предыдущего" else "next"})
            consumed = True
        if mm := re.search(r"Принудительно делает активным вашего (следующего|предыдущего) персонажа", sentence):
            effects.append({"op": "force_switch", "side": "self",
                            "direction": "prev" if mm.group(1) == "предыдущего" else "next"})
            consumed = True
        if mm := re.search(r"[Вв]осстанавливает (\d+) HP всем вашим персонажам", sentence):
            effects.append({"op": "heal", "amount": int(mm.group(1)), "target": "all"})
            consumed = True
        elif mm := re.search(r"восстанавливает (\d+) HP этому персонажу", sentence):
            effects.append({"op": "heal", "amount": int(mm.group(1)), "target": "self"})
            consumed = True
        if mm := re.search(r"При (втором|третьем|четв[её]ртом) использовании этого навыка в каждом раунде наносит дополнительно (\d+)", sentence):
            effects.append({"op": "nth_use_bonus", "n": ORDINALS[mm.group(1)], "amount": int(mm.group(2))})
            consumed = True
        if re.search(r"призывает помощника", sentence, re.I):
            consumed = True
        if re.search(r"(создаёт|возникает|получает|достаётся)", sentence) and created:
            consumed = True
        if re.fullmatch(r"всем неактивным персонажам противника[.,]?.*", sentence) and "призывает" not in sentence:
            consumed = True
        if not consumed and len(sentence) > 12:  # short fragments are sentence-split artefacts ("урона.")
            unparsed.append(sentence)
    for name in summon_links:
        effects.append({"op": "summon", "id": name.strip()})
    status_names = {s["name"] for s in statuses}
    for name in dict.fromkeys(clean(n) for n in created):
        if name in status_names:
            scope = "character" if re.search(rf"(возникает|Персонаж получает|персонажу достаётся|Персонажу достаётся)\s+'''{re.escape(name)}", main_raw) else "combat"
            effects.append({"op": "status", "id": name, "scope": scope})
    return effects, statuses, unparsed, text


# --------------------------------------------------------------------------------------
# page parsers
# --------------------------------------------------------------------------------------
def parse_character(title: str, page: dict, ib: dict[str, str], statuses_out: dict[str, dict]) -> dict:
    name = ib.get("Название") or title.removeprefix("СП7:")
    char = {
        "id": name,
        "wiki_id": ib.get("id"),
        "name": name,
        "hp": _int(ib.get("Здоровье")) or 10,
        "element": ELEMENTS_RU.get(ib.get("Элемент", ""), "physical"),
        "weapon": WEAPONS.get(ib.get("Оружие", ""), "other"),
        "faction": ib.get("Команда", ""),
        "available": not page.get("unavailable", False),
        "skills": [],
        "passives": [],
        "energy": None,
        "source": _page_url(title),
        "revid": page.get("revid"),
    }
    for sk in find_templates(page["wikitext"], "Навык"):
        stype = SKILL_TYPES.get(sk.named.get("Тип", ""), "other")
        sname = clean(sk.named.get("Название", ""))
        desc_raw = sk.named.get("Описание", "")
        if stype in ("passive", "technique", "other"):
            char["passives"].append({"name": sname, "type": stype, "text": clean(desc_raw)})
            continue
        cost = parse_skill_cost(sk.named.get("Стоимость", ""))
        effects, statuses, unparsed, text = parse_skill_description(desc_raw)
        for st in statuses:
            st_id = st["name"]
            if st_id in statuses_out and statuses_out[st_id]["text"] != st["text"]:
                st_id = f"{st['name']} ({name})"
                for e in effects:
                    if e.get("op") == "status" and e["id"] == st["name"]:
                        e["id"] = st_id
            st["id"] = st_id
            st["owner"] = name
            statuses_out[st_id] = st
        if stype == "burst" and cost.get("energy"):
            char["energy"] = cost["energy"]
        char["skills"].append({
            "id": f"{name}:{sname}",
            "name": sname,
            "type": stype,
            "cost": cost,
            "effects": effects,
            "text": text,
            "unparsed": unparsed,
            "modelled": not unparsed,
        })
    if char["energy"] is None:
        char["energy"] = 2
        char["energy_inferred"] = True
    return char


def parse_summon(title: str, page: dict, ib: dict[str, str]) -> dict:
    name = ib.get("Название") or title.removeprefix("СП7:")
    text = clean(ib.get("Эффект", ""))
    summon: dict[str, Any] = {
        "id": name, "name": name, "summoner": ib.get("Призыватель", ""),
        "from_skill": ib.get("Призывает", ""), "usages": _int(ib.get("Применение")) or 1,
        "text": text, "available": not page.get("unavailable", False),
        "source": _page_url(title), "revid": page.get("revid"),
    }
    used: list[str] = []

    def hit(pattern: str, flags: int = 0):
        mm = re.search(pattern, text, flags)
        if mm:
            used.append(mm.group(0))
        return mm

    if mm := hit(rf"Финальная фаза:\s*наносит {EL} урон,? (?:(\d+) ед|×(\d+))"):
        summon["element"] = ELEMENTS_RU[mm.group(1)]
        summon["damage"] = int(mm.group(2) or mm.group(3))
    if mm := hit(r"Проникающий урон,? (\d+) ед"):
        summon["piercing"] = int(mm.group(1))
    if mm := hit(r"восстанавливает (\d+) HP (активному персонажу|всем вашим персонажам|вашему персонажу[^.]*)"):
        summon["heal"] = int(mm.group(1))
        summon["heal_target"] = "all" if "всем" in mm.group(2) else "active"
    if mm := hit(r"(?:складывается|Складывается|складываться) до (\d+)"):
        summon["stack_max"] = int(mm.group(1))
    hit(r"(?:Кол-во|Количество) применений:?\s*\d+")
    behaviors: dict[str, Any] = {}
    parse_behaviors(text, behaviors, hit)
    if mm := hit(r"снижает (?:получаемый )?урон на (\d+)"):
        behaviors.setdefault("reduce", {"min_damage": 0, "amount": int(mm.group(1))})
    summon["behaviors"] = behaviors
    summon["unparsed"] = _leftover(text, used)
    summon["modelled"] = "damage" in summon or "heal" in summon or bool(behaviors)
    summon["partial"] = summon["modelled"] and bool(summon["unparsed"])
    return summon


def parse_action_card(title: str, page: dict, ib: dict[str, str]) -> dict:
    name = ib.get("Название") or title.removeprefix("СП7:")
    ctype = CARD_TYPES.get(ib.get("Тип", ""), "other")
    group = ib.get("Группа", "") or ""
    text = clean(ib.get("Эффект", ""))
    card: dict[str, Any] = {
        "id": name, "wiki_id": ib.get("id"), "name": name, "type": ctype, "group": group,
        "cost": parse_card_cost(ib), "text": text, "effects": [],
        "available": not page.get("unavailable", False),
        "source": _page_url(title), "revid": page.get("revid"),
    }
    effects = card["effects"]
    parsed_any = False
    if group in WEAPONS:
        card["weapon_type"] = WEAPONS[group]
        card["subtype"] = "weapon"
    elif group == "Талант":
        card["subtype"] = "talent"
        card["character"] = ib.get("Персонаж", "")
        card["skill"] = ib.get("Навык", "")
        if "Боевое действие" in text and "сразу используется" in text:
            effects.append({"op": "use_skill", "skill": card["skill"]})
            parsed_any = True
    elif group == "Артефакт":
        card["subtype"] = "artifact"
    elif group == "Еда":
        card["subtype"] = "food"
    if mm := re.search(r"Персонаж(?: с этой картой)? наносит на (\d+) ед\. урона больше", text):
        effects.append({"op": "equip_bonus", "amount": int(mm.group(1))})
        parsed_any = True
    if mm := re.search(r"Возьмите (\d+) карт", text):
        effects.append({"op": "draw", "amount": int(mm.group(1))})
        parsed_any = True
    if re.search(r"следующей смене персонажа[^.]*Быстрое действие", text):
        effects.append({"op": "status", "id": "fast_switch", "scope": "combat"})
        parsed_any = True
    if mm := re.search(r"Следующая обычная атака вашего активного персонажа в этом раунде наносит на (\d+) ед\. урона больше", text):
        effects.append({"op": "status", "id": "next_normal_attack_bonus", "scope": "combat", "amount": int(mm.group(1))})
        parsed_any = True
    if mm := re.search(r"активный персонаж получает (\d+) ед\. Энергии", text):
        effects.append({"op": "gain_energy", "amount": int(mm.group(1)), "target": "active"})
        parsed_any = True
    if mm := re.search(r"[Вв]осстанавливает (\d+) HP", text):
        effects.append({"op": "heal", "amount": int(mm.group(1)), "target": "chosen"})
        parsed_any = True
    card["modelled"] = parsed_any
    return card


# --------------------------------------------------------------------------------------
# reactions (English wiki rules page - the Russian wiki has no TCG rules page)
# --------------------------------------------------------------------------------------
REACTION_CODES = {
    "Melt": "melt", "Vaporize": "vaporize", "Overloaded": "overloaded", "Superconduct": "superconduct",
    "Electro-Charged": "electro_charged", "Frozen": "frozen", "Swirl": "swirl",
    "Crystallize": "crystallize", "Burning": "burning", "Bloom": "bloom", "Quicken": "quicken",
}


def parse_reactions(rules_wikitext: str) -> dict[str, dict]:
    start = rules_wikitext.find("Elemental Reactions: Effects")
    if start < 0:
        return {}
    table = rules_wikitext[start:rules_wikitext.find("|}", start)]
    out: dict[str, dict] = {}
    for row in table.split("|-")[1:]:
        cells = [c.strip() for c in row.strip().split("\n|") if c.strip()]
        cells = [c.lstrip("|").strip() for c in cells]
        if len(cells) < 2:
            continue
        title, desc = cells[0], re.sub(r"\[\[File:[^\]]*\]\]", "", cells[1])
        desc = re.sub(r"\{\{(?:Color\|)?(?:help\|)?([^{}|]+)\}\}", r"\1", desc)
        desc = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", desc).replace("'''", "").replace("<br>", " ")
        code = REACTION_CODES.get(title)
        if not code or code in out:
            continue
        r: dict[str, Any] = {"name_en": title, "text": desc.strip()}
        if mm := re.search(r"\+(\d+) DMG", desc):
            r["bonus"] = int(mm.group(1))
        if mm := re.search(r"deal (\d+) Piercing DMG", desc):
            r["piercing_others"] = int(mm.group(1))
        if "forcibly switched to the next" in desc:
            r["force_switch_next"] = True
        if code == "swirl" and (mm := re.search(r"Deal (\d+) ", desc)):
            r["swirl_damage"] = int(mm.group(1))
        if code == "frozen":
            r["frozen"] = True
            if mm := re.search(r"receives \+(\d+) DMG and removes", desc):
                r["frozen_break_bonus"] = int(mm.group(1))
        if code == "crystallize" and (mm := re.search(r"grant (\d+) Shield point.*?Max (\d+)", desc)):
            r["shield"], r["shield_max"] = int(mm.group(1)), int(mm.group(2))
        if code == "burning" and (mm := re.search(r"Deal (\d+) Pyro DMG\. \((\d+) Usage\. Max (\d+) stacks", desc)):
            r["summon"] = {"damage": int(mm.group(1)), "usages": int(mm.group(2)), "stack_max": int(mm.group(3))}
        if code == "bloom" and (mm := re.search(r"DMG dealt \+(\d+)\. \((\d+) Usage", desc)):
            r["core"] = {"bonus": int(mm.group(1)), "usages": int(mm.group(2))}
        if code == "quicken" and (mm := re.search(r"DMG dealt \+(\d+)\. \((\d+) Usages", desc)):
            r["field"] = {"bonus": int(mm.group(1)), "usages": int(mm.group(2))}
        out[code] = r
    return out


# --------------------------------------------------------------------------------------
def build_knowledge(raw: dict, out_dir: Path) -> dict:
    characters, summons, cards = [], [], []
    statuses: dict[str, dict] = {}
    for title, page in sorted(raw["pages"].items()):
        ibs = find_templates(page["wikitext"], "СП7 Инфобокс")
        if not ibs:
            continue
        ib = ibs[0].named
        ctype = ib.get("Тип", "")
        if ctype == "Карта персонажа":
            characters.append(parse_character(title, page, ib, statuses))
        elif ctype == "Помощник":
            summons.append(parse_summon(title, page, ib))
        elif ctype in CARD_TYPES:
            cards.append(parse_action_card(title, page, ib))
    reactions = parse_reactions(raw.get("rules_en", {}).get("wikitext", ""))

    skills = [s for c in characters for s in c["skills"]]
    report = {
        "source": raw.get("source"),
        "rules_source": raw.get("rules_en", {}).get("url"),
        "fetched_at": raw.get("fetched_at"),
        "characters": len(characters),
        "skills": len(skills),
        "skills_fully_modelled": sum(1 for s in skills if s["modelled"]),
        "summons": len(summons),
        "summons_modelled": sum(1 for s in summons if s["modelled"]),
        "statuses": len(statuses),
        "statuses_modelled": sum(1 for s in statuses.values() if s["modelled"]),
        "action_cards": len(cards),
        "action_cards_modelled": sum(1 for c in cards if c["modelled"]),
        "reactions": len(reactions),
    }
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, data: Any) -> None:
        (out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    dump("characters.json", characters)
    dump("summons.json", summons)
    dump("statuses.json", sorted(statuses.values(), key=lambda s: s["id"]))
    dump("cards.json", cards)
    dump("reactions.json", reactions)
    dump("manifest.json", report)
    return report
