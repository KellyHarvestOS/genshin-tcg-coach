"""Knowledge base: typed access to the data imported from the wiki.

Data files live in ``gcoach/knowledge/data`` and are produced by
``python -m gcoach.tools.wiki_import``. Built-in definitions below cover only
engine-level objects that are *rules*, not cards (reaction by-products,
generic markers); their numbers come from ``reactions.json`` when possible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.enums import Element, SkillType

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class SkillDef:
    id: str
    name: str
    type: SkillType
    cost: dict[str, int]
    effects: list[dict[str, Any]]
    text: str = ""
    unparsed: list[str] = field(default_factory=list)
    modelled: bool = True

    @property
    def energy_cost(self) -> int:
        return self.cost.get("energy", 0)


@dataclass
class CharacterDef:
    id: str
    name: str
    hp: int
    element: Element
    weapon: str
    energy: int
    skills: list[SkillDef]
    passives: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True
    source: str = ""
    generic: bool = False  # True for the placeholder used when a character is not recognised

    def skill(self, skill_id: str) -> SkillDef | None:
        for s in self.skills:
            if s.id == skill_id or s.name == skill_id:
                return s
        return None

    def skills_of(self, stype: SkillType) -> list[SkillDef]:
        return [s for s in self.skills if s.type == stype]


@dataclass
class SummonDef:
    id: str
    name: str
    usages: int
    element: Element | None = None
    damage: int = 0
    piercing: int = 0
    heal: int = 0
    heal_target: str = "active"
    stack_max: int | None = None
    behaviors: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    modelled: bool = True


@dataclass
class StatusDef:
    id: str
    name: str
    usages: int | None = None
    duration: int | None = None
    stack_max: int | None = None
    behaviors: dict[str, Any] = field(default_factory=dict)
    until_round_end: bool = False
    text: str = ""
    modelled: bool = True


@dataclass
class CardDef:
    id: str
    name: str
    type: str  # equipment / event / support
    cost: dict[str, int]
    effects: list[dict[str, Any]]
    subtype: str = ""  # weapon / talent / artifact / food
    group: str = ""
    weapon_type: str | None = None
    character: str | None = None
    skill: str | None = None
    text: str = ""
    modelled: bool = False
    available: bool = True
    source: str = ""


# Engine-level statuses (rules, not cards).
BUILTIN_STATUSES: dict[str, StatusDef] = {
    "fast_switch": StatusDef("fast_switch", "Следующая смена — быстрое действие", usages=1,
                             behaviors={"fast_switch": True}),
    "next_normal_attack_bonus": StatusDef("next_normal_attack_bonus", "Тяжёлый удар", usages=1,
                                          behaviors={"damage_bonus": {"amount": 1, "skill_types": ["normal"]}},
                                          until_round_end=True),
    "frozen": StatusDef("frozen", "Заморозка", behaviors={"cannot_use_skills": True, "frozen": True},
                        until_round_end=True),
    "crystallize_shield": StatusDef("crystallize_shield", "Кристалл: щит", usages=1, stack_max=2,
                                    behaviors={"shield": 1}),
    "dendro_core": StatusDef("dendro_core", "Дендро ядро", usages=1,
                             behaviors={"damage_bonus": {"amount": 2, "elements": ["pyro", "electro"],
                                                         "opponent_active_only": True}}),
    "catalyzing_field": StatusDef("catalyzing_field", "Катализирующее поле", usages=2,
                                  behaviors={"damage_bonus": {"amount": 1, "elements": ["electro", "dendro"],
                                                              "opponent_active_only": True}}),
    "satiated": StatusDef("satiated", "Сытость", behaviors={"satiated": True}, until_round_end=True),
}
BUILTIN_SUMMONS: dict[str, SummonDef] = {
    "burning_flame": SummonDef("burning_flame", "Пламя горения", usages=1, element=Element.PYRO,
                               damage=1, stack_max=2),
}


def _generic_character(char_id: str = "unknown", element: Element = Element.PHYSICAL) -> CharacterDef:
    """Placeholder for an unrecognised character. Flagged `generic` so it is never shown as fact."""
    dmg_el = element.value if element != Element.PHYSICAL else "physical"
    cost_el = element.value if element not in (Element.PHYSICAL, Element.PIERCING) else "unaligned"
    return CharacterDef(
        id=char_id, name="Неизвестный персонаж", hp=10, element=element, weapon="other", energy=2,
        skills=[
            SkillDef(f"{char_id}:normal", "Обычная атака", SkillType.NORMAL, {cost_el: 1, "unaligned": 2},
                     [{"op": "damage", "element": "physical", "amount": 2}], modelled=False),
            SkillDef(f"{char_id}:skill", "Элементальный навык", SkillType.SKILL, {cost_el: 3},
                     [{"op": "damage", "element": dmg_el, "amount": 3}], modelled=False),
            SkillDef(f"{char_id}:burst", "Взрыв стихии", SkillType.BURST, {cost_el: 3, "energy": 2},
                     [{"op": "damage", "element": dmg_el, "amount": 4}], modelled=False),
        ],
        generic=True,
    )


class KnowledgeBase:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.characters: dict[str, CharacterDef] = {}
        self.summons: dict[str, SummonDef] = dict(BUILTIN_SUMMONS)
        self.statuses: dict[str, StatusDef] = dict(BUILTIN_STATUSES)
        self.cards: dict[str, CardDef] = {}
        self.reactions: dict[str, dict[str, Any]] = {}
        self.manifest: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------------------------
    def _read(self, name: str, default: Any) -> Any:
        path = self.data_dir / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _load(self) -> None:
        for c in self._read("characters.json", []):
            skills = [
                SkillDef(s["id"], s["name"], SkillType(s["type"]), s["cost"], s["effects"],
                         s.get("text", ""), s.get("unparsed", []), s.get("modelled", True))
                for s in c["skills"] if s["type"] in ("normal", "skill", "burst")
            ]
            self.characters[c["id"]] = CharacterDef(
                c["id"], c["name"], c["hp"], Element(c["element"]), c["weapon"], c["energy"], skills,
                c.get("passives", []), c.get("available", True), c.get("source", ""),
            )
        for s in self._read("summons.json", []):
            self.summons[s["id"]] = SummonDef(
                s["id"], s["name"], s.get("usages", 1),
                Element(s["element"]) if s.get("element") else None,
                s.get("damage", 0), s.get("piercing", 0), s.get("heal", 0), s.get("heal_target", "active"),
                s.get("stack_max"), s.get("behaviors", {}), s.get("text", ""), s.get("modelled", False),
            )
        for s in self._read("statuses.json", []):
            self.statuses[s["id"]] = StatusDef(
                s["id"], s["name"], s.get("usages"), s.get("duration"), s.get("stack_max"),
                s.get("behaviors", {}), s.get("until_round_end", False), s.get("text", ""),
                s.get("modelled", False),
            )
        for c in self._read("cards.json", []):
            self.cards[c["id"]] = CardDef(
                c["id"], c["name"], c["type"], c.get("cost", {}), c.get("effects", []),
                c.get("subtype", ""), c.get("group", ""), c.get("weapon_type"), c.get("character"),
                c.get("skill"), c.get("text", ""), c.get("modelled", False), c.get("available", True),
                c.get("source", ""),
            )
        self.reactions = self._read("reactions.json", {})
        self.manifest = self._read("manifest.json", {})
        self._apply_reaction_numbers()

    def _apply_reaction_numbers(self) -> None:
        """Keep reaction by-products in sync with the imported rules text."""
        bloom = self.reactions.get("bloom", {}).get("core")
        if bloom:
            self.statuses["dendro_core"].behaviors["damage_bonus"]["amount"] = bloom["bonus"]
            self.statuses["dendro_core"].usages = bloom["usages"]
        field_ = self.reactions.get("quicken", {}).get("field")
        if field_:
            self.statuses["catalyzing_field"].behaviors["damage_bonus"]["amount"] = field_["bonus"]
            self.statuses["catalyzing_field"].usages = field_["usages"]
        cryst = self.reactions.get("crystallize", {})
        if "shield" in cryst:
            self.statuses["crystallize_shield"].behaviors["shield"] = cryst["shield"]
            self.statuses["crystallize_shield"].stack_max = cryst.get("shield_max", 2)
        burn = self.reactions.get("burning", {}).get("summon")
        if burn:
            s = self.summons["burning_flame"]
            s.damage, s.usages, s.stack_max = burn["damage"], burn["usages"], burn["stack_max"]

    # ------------------------------------------------------------------------------------
    def character(self, char_id: str | None) -> CharacterDef:
        if char_id and char_id in self.characters:
            return self.characters[char_id]
        return _generic_character(char_id or "unknown")

    def has_character(self, char_id: str | None) -> bool:
        return bool(char_id) and char_id in self.characters

    def status(self, status_id: str) -> StatusDef:
        if status_id in self.statuses:
            return self.statuses[status_id]
        return StatusDef(status_id, status_id, modelled=False)

    def summon(self, summon_id: str) -> SummonDef:
        if summon_id in self.summons:
            return self.summons[summon_id]
        return SummonDef(summon_id, summon_id, usages=1, modelled=False)

    def card(self, card_id: str) -> CardDef | None:
        return self.cards.get(card_id)

    def reaction(self, code: str) -> dict[str, Any]:
        return self.reactions.get(code, {})

    def search_characters(self, query: str) -> list[CharacterDef]:
        q = query.lower()
        return [c for c in self.characters.values() if q in c.name.lower()]


@lru_cache(maxsize=1)
def default_kb() -> KnowledgeBase:
    return KnowledgeBase()
