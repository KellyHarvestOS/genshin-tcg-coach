"""Costs and dice payment.

A cost is a dict as stored in the knowledge base:
    {"pyro": 3}               - 3 Pyro dice (Omni substitutes)
    {"same": 2}               - 2 dice of one matching type
    {"unaligned": 2}          - any 2 dice
    {"energy": 3}             - 3 energy of the acting character
"""
from __future__ import annotations

from .enums import DieType, Element
from .state import DicePool

ELEMENT_KEYS = {e.value for e in Element} - {"physical", "piercing"}


def dice_cost_total(cost: dict[str, int]) -> int:
    return sum(v for k, v in cost.items() if k != "energy")


def apply_discount(cost: dict[str, int], discount: int) -> dict[str, int]:
    """Reduce a cost by `discount` dice, unaligned first, then elemental/same."""
    if discount <= 0:
        return dict(cost)
    out = dict(cost)
    for key in ["unaligned", "same", *sorted(k for k in out if k in ELEMENT_KEYS)]:
        while discount > 0 and out.get(key, 0) > 0:
            out[key] -= 1
            discount -= 1
        if out.get(key) == 0:
            out.pop(key)
    return out


def die_value(die: DieType, active_element: Element | None, team_elements: set[Element]) -> float:
    """How precious a die is for the rest of the round (used for payment choice and evaluation)."""
    if die == DieType.OMNI:
        return 1.3
    el = Element(die.value)
    if active_element is not None and el == active_element:
        return 1.1
    if el in team_elements:
        return 1.0
    return 0.75


def plan_payment(pool: DicePool, cost: dict[str, int], active_element: Element | None = None,
                 team_elements: set[Element] | None = None) -> dict[DieType, int] | None:
    """Choose which dice to spend. Returns {die: count} or None if unaffordable.

    Hidden dice (opponent) are treated as wildcards: any cost is affordable if the
    total is large enough, and the payment is reported under OMNI.
    """
    team_elements = team_elements or set()
    total_needed = dice_cost_total(cost)
    if pool.hidden:
        return {DieType.OMNI: total_needed} if pool.total() >= total_needed else None

    counts = dict(pool.counts)
    paid: dict[DieType, int] = {}

    def take(die: DieType, n: int) -> None:
        counts[die] = counts.get(die, 0) - n
        paid[die] = paid.get(die, 0) + n

    # 1) element-specific requirements: matching dice first, then omni
    for key, n in cost.items():
        if key not in ELEMENT_KEYS or n <= 0:
            continue
        die = DieType(key)
        use = min(counts.get(die, 0), n)
        if use:
            take(die, use)
        rest = n - use
        if rest:
            if counts.get(DieType.OMNI, 0) < rest:
                return None
            take(DieType.OMNI, rest)

    # 2) "same" requirement: one non-omni type (topped up with omni)
    same = cost.get("same", 0)
    if same > 0:
        best: tuple[float, DieType | None, int] | None = None
        omni = counts.get(DieType.OMNI, 0)
        for die, have in counts.items():
            if die == DieType.OMNI or have <= 0:
                continue
            use = min(have, same)
            need_omni = same - use
            if need_omni > omni:
                continue
            # prefer fewer omni, then cheaper dice
            score = need_omni * 10 + use * die_value(die, active_element, team_elements)
            if best is None or score < best[0]:
                best = (score, die, use)
        if best is not None:
            _, die, use = best
            take(die, use)
            if same - use:
                take(DieType.OMNI, same - use)
        elif omni >= same:
            take(DieType.OMNI, same)
        else:
            return None

    # 3) unaligned: cheapest dice first, omni last
    unaligned = cost.get("unaligned", 0)
    if unaligned > 0:
        order = sorted((d for d, c in counts.items() if c > 0),
                       key=lambda d: die_value(d, active_element, team_elements))
        for die in order:
            if unaligned == 0:
                break
            use = min(counts[die], unaligned)
            take(die, use)
            unaligned -= use
        if unaligned:
            return None
    return {d: n for d, n in paid.items() if n > 0}


def pay(pool: DicePool, payment: dict[DieType, int]) -> None:
    if pool.hidden:
        pool.hidden -= sum(payment.values())
        if pool.hidden < 0:
            raise ValueError("opponent cannot pay this cost")
        return
    for die, n in payment.items():
        pool.remove(die, n)


def describe_payment(payment: dict[DieType, int] | None) -> str:
    if not payment:
        return "0 кубиков"
    names = {
        DieType.OMNI: "Всеэлем.", DieType.CRYO: "Крио", DieType.HYDRO: "Гидро", DieType.PYRO: "Пиро",
        DieType.ELECTRO: "Электро", DieType.ANEMO: "Анемо", DieType.GEO: "Гео", DieType.DENDRO: "Дендро",
    }
    return " + ".join(f"{n}× {names[d]}" for d, n in payment.items())
