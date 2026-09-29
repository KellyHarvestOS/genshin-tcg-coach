"""Elemental aura bookkeeping and reaction resolution (pure functions)."""
from __future__ import annotations

from ..core.enums import AURA_ELEMENTS, SWIRLABLE, Element, Reaction

C, H, P, E, A, G, D = (Element.CRYO, Element.HYDRO, Element.PYRO, Element.ELECTRO,
                       Element.ANEMO, Element.GEO, Element.DENDRO)

_PAIRS: dict[frozenset, Reaction] = {
    frozenset({P, C}): Reaction.MELT,
    frozenset({P, H}): Reaction.VAPORIZE,
    frozenset({P, E}): Reaction.OVERLOADED,
    frozenset({C, E}): Reaction.SUPERCONDUCT,
    frozenset({H, E}): Reaction.ELECTRO_CHARGED,
    frozenset({C, H}): Reaction.FROZEN,
    frozenset({D, H}): Reaction.BLOOM,
    frozenset({D, P}): Reaction.BURNING,
    frozenset({D, E}): Reaction.QUICKEN,
}

REACTION_NAMES_RU = {
    Reaction.MELT: "Таяние", Reaction.VAPORIZE: "Пар", Reaction.OVERLOADED: "Перегрузка",
    Reaction.SUPERCONDUCT: "Сверхпроводник", Reaction.ELECTRO_CHARGED: "Заряжен",
    Reaction.FROZEN: "Заморозка", Reaction.SWIRL: "Рассеивание", Reaction.CRYSTALLIZE: "Кристалл",
    Reaction.BLOOM: "Бутонизация", Reaction.BURNING: "Горение", Reaction.QUICKEN: "Стимуляция",
}


def pair_reaction(incoming: Element, aura: Element) -> Reaction | None:
    if incoming == A and aura in SWIRLABLE:
        return Reaction.SWIRL
    if incoming == G and aura in SWIRLABLE:
        return Reaction.CRYSTALLIZE
    return _PAIRS.get(frozenset({incoming, aura}))


def resolve(incoming: Element, aura: list[Element]) -> tuple[Reaction | None, list[Element], Element | None]:
    """Apply `incoming` to a character with `aura`.

    Returns (reaction, new_aura, consumed_aura_element).
    Cryo and Dendro may coexist; the first reactable aura element is consumed.
    """
    if incoming in (Element.PHYSICAL, Element.PIERCING):
        return None, list(aura), None
    for existing in aura:
        reaction = pair_reaction(incoming, existing)
        if reaction is not None:
            return reaction, [a for a in aura if a != existing], existing
    if incoming not in AURA_ELEMENTS:  # Anemo / Geo never stay
        return None, list(aura), None
    if incoming in aura:
        return None, list(aura), None
    if not aura:
        return None, [incoming], None
    if set(aura) | {incoming} == {C, D}:
        return None, list(aura) + [incoming], None
    return None, list(aura), None


def reactions_available(attacking: Element, aura: list[Element]) -> Reaction | None:
    """Which reaction would `attacking` trigger on `aura` (no state change)."""
    return resolve(attacking, aura)[0]
