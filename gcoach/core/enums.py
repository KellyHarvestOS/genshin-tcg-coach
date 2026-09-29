"""Core enumerations shared by every layer of the coach."""
from __future__ import annotations

from enum import Enum


class Element(str, Enum):
    CRYO = "cryo"
    HYDRO = "hydro"
    PYRO = "pyro"
    ELECTRO = "electro"
    ANEMO = "anemo"
    GEO = "geo"
    DENDRO = "dendro"
    PHYSICAL = "physical"
    PIERCING = "piercing"


# Elements that can stay on a character as an aura.
AURA_ELEMENTS = {Element.CRYO, Element.HYDRO, Element.PYRO, Element.ELECTRO, Element.DENDRO}
# Elements that Swirl / Crystallize can react with.
SWIRLABLE = {Element.CRYO, Element.HYDRO, Element.PYRO, Element.ELECTRO}


class DieType(str, Enum):
    OMNI = "omni"
    CRYO = "cryo"
    HYDRO = "hydro"
    PYRO = "pyro"
    ELECTRO = "electro"
    ANEMO = "anemo"
    GEO = "geo"
    DENDRO = "dendro"

    @staticmethod
    def for_element(element: Element) -> "DieType | None":
        try:
            return DieType(element.value)
        except ValueError:
            return None


class Phase(str, Enum):
    ROLL = "roll"
    ACTION = "action"
    END = "end"
    CHOOSE_ACTIVE = "choose_active"  # a side must pick a new active character
    GAME_OVER = "game_over"
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    NORMAL_ATTACK = "normal_attack"
    ELEMENTAL_SKILL = "elemental_skill"
    ELEMENTAL_BURST = "elemental_burst"
    PLAY_CARD = "play_card"
    SWITCH_CHARACTER = "switch_character"
    ELEMENTAL_TUNING = "elemental_tuning"
    CHOOSE_ACTIVE = "choose_active"
    END_ROUND = "end_round"


SKILL_ACTIONS = {ActionType.NORMAL_ATTACK, ActionType.ELEMENTAL_SKILL, ActionType.ELEMENTAL_BURST}


class SkillType(str, Enum):
    NORMAL = "normal"
    SKILL = "skill"
    BURST = "burst"


SKILL_TYPE_TO_ACTION = {
    SkillType.NORMAL: ActionType.NORMAL_ATTACK,
    SkillType.SKILL: ActionType.ELEMENTAL_SKILL,
    SkillType.BURST: ActionType.ELEMENTAL_BURST,
}


class Reaction(str, Enum):
    MELT = "melt"
    VAPORIZE = "vaporize"
    OVERLOADED = "overloaded"
    SUPERCONDUCT = "superconduct"
    ELECTRO_CHARGED = "electro_charged"
    FROZEN = "frozen"
    SWIRL = "swirl"
    CRYSTALLIZE = "crystallize"
    BLOOM = "bloom"
    BURNING = "burning"
    QUICKEN = "quicken"


class Knowledge(str, Enum):
    """How a value got into the state. Never present a guess as a fact."""

    KNOWN = "known"  # read directly from the screen with high confidence
    INFERRED = "inferred"  # derived from rules / history / partial evidence
    UNKNOWN = "unknown"  # not visible - must not be invented


PLAYER = "player"
OPPONENT = "opponent"


def other_side(side: str) -> str:
    return OPPONENT if side == PLAYER else PLAYER
