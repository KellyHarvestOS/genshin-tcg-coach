"""Demo game states for testing the coach without Genshin Impact running.

Characters and cards are taken from the wiki-imported knowledge base; only
the *situation* (HP, dice, statuses) is invented for the demo.
"""
from __future__ import annotations

from ..core.enums import OPPONENT, PLAYER, DieType, Element, Phase
from ..core.state import Character, DicePool, GameState, Hand, PlayerState, Status, Summon
from ..knowledge.base import KnowledgeBase


def make_character(kb: KnowledgeBase, char_id: str, hp: int | None = None, energy: int = 0,
                   aura: list[Element] | None = None, statuses: list[Status] | None = None) -> Character:
    d = kb.character(char_id)
    return Character(
        id=char_id, hp=d.hp if hp is None else hp, max_hp=d.hp, energy=energy, max_energy=d.energy,
        alive=(hp is None or hp > 0), aura=list(aura or []), statuses=list(statuses or []),
    )


def _dice(**counts: int) -> DicePool:
    return DicePool({DieType(k): v for k, v in counts.items() if v})


def scenario_opening(kb: KnowledgeBase) -> GameState:
    """Round 1 of the starter-deck mirror: Diluc / Kaeya / Sucrose vs Fischl / Xingqiu / Ganyu."""
    player = PlayerState(
        PLAYER,
        [make_character(kb, "Дилюк"), make_character(kb, "Кэйа"), make_character(kb, "Сахароза")],
        active_index=0,
        dice=_dice(omni=2, pyro=3, cryo=1, anemo=1, geo=1),
        hand=Hand(["Стратег", "Поручи это мне!", "Меч из белого железа", "Звёздные знамения", "Тяжёлый удар"]),
    )
    opponent = PlayerState(
        OPPONENT,
        [make_character(kb, "Фишль"), make_character(kb, "Син Цю"), make_character(kb, "Гань Юй")],
        active_index=1,
        dice=DicePool({}, 8),
        hand=Hand([], 5),
    )
    return GameState(round=1, phase=Phase.ACTION, active_player=PLAYER, player=player, opponent=opponent,
                     metadata={"source": "demo", "scenario": "opening"})


def scenario_reaction_setup(kb: KnowledgeBase) -> GameState:
    """Mid game: enemy active is soaked with Hydro, our Diluc can Vaporize; Kaeya burst is ready."""
    player = PlayerState(
        PLAYER,
        [make_character(kb, "Дилюк", hp=7, energy=2), make_character(kb, "Кэйа", hp=8, energy=2),
         make_character(kb, "Сахароза", hp=4, energy=1)],
        active_index=0,
        dice=_dice(omni=1, pyro=3, cryo=2, anemo=1),
        hand=Hand(["Поручи это мне!", "Стратег", "Тяжёлый удар"]),
        summons=[],
    )
    opponent = PlayerState(
        OPPONENT,
        [make_character(kb, "Фишль", hp=6, energy=1), make_character(kb, "Син Цю", hp=5, energy=2,
                                                                     aura=[Element.HYDRO]),
         make_character(kb, "Гань Юй", hp=9, energy=0)],
        active_index=1,
        dice=DicePool({}, 5),
        hand=Hand([], 4),
        summons=[Summon("Оз", 2)],
        combat_statuses=[Status("Меч дождя", 1)],
    )
    return GameState(round=3, phase=Phase.ACTION, active_player=PLAYER, player=player, opponent=opponent,
                     metadata={"source": "demo", "scenario": "reaction_setup"})


def scenario_lethal(kb: KnowledgeBase) -> GameState:
    """Late game: opponent's last character is low - can we finish this turn?"""
    player = PlayerState(
        PLAYER,
        [make_character(kb, "Дилюк", hp=3, energy=3), make_character(kb, "Кэйа", hp=0),
         make_character(kb, "Сахароза", hp=5, energy=2)],
        active_index=2,
        dice=_dice(omni=2, pyro=2, anemo=2),
        hand=Hand(["Поручи это мне!"]),
        summons=[Summon("Большой Воздушный дух", 1)],
    )
    opponent = PlayerState(
        OPPONENT,
        [make_character(kb, "Фишль", hp=0), make_character(kb, "Син Цю", hp=0),
         make_character(kb, "Гань Юй", hp=7, energy=3, aura=[Element.CRYO])],
        active_index=2,
        dice=DicePool({}, 6),
        hand=Hand([], 3),
        summons=[Summon("Священная Крио жемчужина", 1)],
    )
    return GameState(round=6, phase=Phase.ACTION, active_player=PLAYER, player=player, opponent=opponent,
                     metadata={"source": "demo", "scenario": "lethal"})


SCENARIOS = {
    "opening": ("Начало партии", scenario_opening),
    "reaction_setup": ("Подготовка реакции", scenario_reaction_setup),
    "lethal": ("Шанс на победу", scenario_lethal),
}


def load_scenario(kb: KnowledgeBase, name: str) -> GameState:
    return SCENARIOS[name][1](kb)
