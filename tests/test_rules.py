import pytest

from gcoach.core.actions import Action
from gcoach.core.dice import apply_discount, plan_payment
from gcoach.core.enums import OPPONENT, PLAYER, ActionType, DieType, Element, Phase, Reaction
from gcoach.core.state import DicePool, Status, Summon
from gcoach.rules.engine import IllegalAction
from gcoach.rules.reactions import resolve


def skill(kb, char, stype):
    return next(s for s in kb.character(char).skills if s.type.value == stype)


# ------------------------------------------------------------------ dice
def test_payment_prefers_matching_then_omni():
    pool = DicePool({DieType.PYRO: 2, DieType.OMNI: 2, DieType.CRYO: 1})
    pay = plan_payment(pool, {"pyro": 3}, Element.PYRO, {Element.PYRO})
    assert pay == {DieType.PYRO: 2, DieType.OMNI: 1}


def test_payment_unaligned_uses_cheapest_dice():
    pool = DicePool({DieType.PYRO: 2, DieType.OMNI: 1, DieType.GEO: 1})
    pay = plan_payment(pool, {"unaligned": 1}, Element.PYRO, {Element.PYRO})
    assert pay == {DieType.GEO: 1}


def test_payment_same_and_unaffordable():
    pool = DicePool({DieType.PYRO: 1, DieType.CRYO: 1, DieType.OMNI: 1})
    assert plan_payment(pool, {"same": 2}, Element.PYRO, set()) is not None
    assert plan_payment(pool, {"same": 3}, Element.PYRO, set()) is None
    assert plan_payment(pool, {"hydro": 2}, Element.PYRO, set()) is None


def test_hidden_dice_are_wildcards():
    assert plan_payment(DicePool({}, 3), {"pyro": 3}) == {DieType.OMNI: 3}
    assert plan_payment(DicePool({}, 2), {"pyro": 3}) is None


def test_discount():
    assert apply_discount({"unaligned": 1}, 1) == {}
    assert apply_discount({"pyro": 3}, 1) == {"pyro": 2}


# ------------------------------------------------------------------ reactions
@pytest.mark.parametrize("incoming,aura,expected", [
    (Element.PYRO, [Element.CRYO], Reaction.MELT),
    (Element.HYDRO, [Element.PYRO], Reaction.VAPORIZE),
    (Element.ELECTRO, [Element.PYRO], Reaction.OVERLOADED),
    (Element.CRYO, [Element.ELECTRO], Reaction.SUPERCONDUCT),
    (Element.HYDRO, [Element.ELECTRO], Reaction.ELECTRO_CHARGED),
    (Element.CRYO, [Element.HYDRO], Reaction.FROZEN),
    (Element.ANEMO, [Element.PYRO], Reaction.SWIRL),
    (Element.GEO, [Element.HYDRO], Reaction.CRYSTALLIZE),
    (Element.DENDRO, [Element.HYDRO], Reaction.BLOOM),
    (Element.PYRO, [Element.DENDRO], Reaction.BURNING),
    (Element.ELECTRO, [Element.DENDRO], Reaction.QUICKEN),
])
def test_reaction_table(incoming, aura, expected):
    r, new_aura, consumed = resolve(incoming, aura)
    assert r == expected and new_aura == [] and consumed == aura[0]


def test_aura_application_rules():
    assert resolve(Element.PYRO, []) == (None, [Element.PYRO], None)
    assert resolve(Element.ANEMO, [])[1] == []  # anemo never stays
    assert resolve(Element.PHYSICAL, [Element.HYDRO])[1] == [Element.HYDRO]
    assert resolve(Element.DENDRO, [Element.CRYO])[1] == [Element.CRYO, Element.DENDRO]  # coexist
    r, aura, _ = resolve(Element.HYDRO, [Element.CRYO, Element.DENDRO])
    assert r == Reaction.FROZEN and aura == [Element.DENDRO]


# ------------------------------------------------------------------ damage / energy / statuses
def test_skill_damage_reaction_and_rain_sword(engine, kb, scenario):
    s = scenario("reaction_setup")  # Xingqiu has Hydro aura, 5 HP, Rain Sword x1
    sk = skill(kb, "Дилюк", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    xq = res.state.opponent.characters[1]
    assert xq.hp == 1  # 3 + 2 (vaporize) - 1 (rain sword)
    assert xq.aura == []
    assert res.state.opponent.combat_status("Меч дождя") is None  # used up
    assert res.state.player.characters[0].energy == 3
    assert res.state.active_player == OPPONENT
    assert s.opponent.characters[1].hp == 5  # original untouched


def test_burst_consumes_energy_and_requires_it(engine, kb, duel):
    s = duel()
    burst = skill(kb, "Дилюк", "burst")
    a = Action(ActionType.ELEMENTAL_BURST, PLAYER, actor=0, skill=burst.id)
    assert not engine.is_legal(s, a)
    s.player.characters[0].energy = 3
    res = engine.apply(s, a)
    assert res.state.player.characters[0].energy == 0
    assert res.state.player.characters[0].has_status("Пиро инфузия")
    assert res.state.opponent.characters[0].hp == 10 - 8


def test_infusion_converts_physical(engine, kb, duel):
    s = duel()
    s.player.characters[0].statuses.append(Status("Пиро инфузия", None, 2))
    na = skill(kb, "Дилюк", "normal")
    res = engine.apply(s, Action(ActionType.NORMAL_ATTACK, PLAYER, actor=0, skill=na.id))
    assert res.state.opponent.characters[0].aura == [Element.PYRO]


def test_nth_use_bonus(engine, kb, duel):
    s = duel(dice={"omni": 16})
    sk = skill(kb, "Дилюк", "skill")
    s.player.characters[0].skill_uses_round[sk.id] = 2
    s.opponent.characters[0].hp = 20
    s.opponent.characters[0].max_hp = 20
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.opponent.characters[0].hp == 20 - 5


def test_overloaded_forces_switch_and_superconduct_pierces(engine, kb, duel):
    s = duel(player=("Фишль", "Кэйа", "Сахароза"))
    s.opponent.characters[0].aura = [Element.PYRO]
    sk = skill(kb, "Фишль", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.opponent.active_index == 1
    assert any(e.kind == "reaction" and e.data["reaction"] == "overloaded" for e in res.events)
    s2 = duel(player=("Кэйа", "Фишль", "Сахароза"))
    s2.opponent.characters[0].aura = [Element.ELECTRO]
    sk2 = skill(kb, "Кэйа", "skill")
    res2 = engine.apply(s2, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk2.id))
    assert [c.hp for c in res2.state.opponent.characters][1:] == [9, 11]  # 1 piercing each (Ganyu has 12)


def test_frozen_blocks_skills(engine, kb, duel):
    s = duel()
    s.player.characters[0].statuses.append(Status("frozen"))
    na = skill(kb, "Дилюк", "normal")
    assert not engine.is_legal(s, Action(ActionType.NORMAL_ATTACK, PLAYER, actor=0, skill=na.id))
    with pytest.raises(IllegalAction):
        engine.apply(s, Action(ActionType.NORMAL_ATTACK, PLAYER, actor=0, skill=na.id))


def test_shield_absorbs(engine, kb, duel):
    s = duel()
    s.opponent.combat_statuses.append(Status("crystallize_shield", 2))
    sk = skill(kb, "Дилюк", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.opponent.characters[0].hp == 10 - 1
    assert res.state.opponent.combat_status("crystallize_shield") is None


def test_summon_created_and_end_phase_damage(engine, kb, duel):
    s = duel(player=("Фишль", "Кэйа", "Сахароза"))
    sk = skill(kb, "Фишль", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.player.summon("Оз").usages == 2
    st = res.state
    st.player.declared_end = True
    st.active_player = OPPONENT
    end = engine.apply(st, Action(ActionType.END_ROUND, OPPONENT))
    after = end.state
    assert after.phase == Phase.ROLL and after.round == 2
    assert after.player.summon("Оз").usages == 1
    assert after.opponent.characters[0].hp == 10 - 1 - 1  # skill 1 + Oz 1
    assert after.player.dice.hidden == 8  # re-rolled dice are unknown


# ------------------------------------------------------------------ switching / cards / turn flow
def test_switch_is_combat_action_and_fast_with_card(engine, kb, duel):
    s = duel(hand=["Поручи это мне!"])
    sw = Action(ActionType.SWITCH_CHARACTER, PLAYER, target=1)
    res = engine.apply(s, sw)
    assert res.state.player.active_index == 1 and res.state.active_player == OPPONENT
    s2 = engine.apply(s, Action(ActionType.PLAY_CARD, PLAYER, card="Поручи это мне!")).state
    assert s2.active_player == PLAYER and "Поручи это мне!" not in s2.player.hand.cards
    res2 = engine.apply(s2, sw)
    assert res2.state.active_player == PLAYER
    assert res2.state.player.combat_status("fast_switch") is None


def test_weapon_card_adds_damage(engine, kb, duel):
    s = duel(hand=["Меч из белого железа"], dice={"omni": 8})
    assert engine.is_legal(s, Action(ActionType.PLAY_CARD, PLAYER, card="Меч из белого железа", target=0))
    assert not engine.is_legal(s, Action(ActionType.PLAY_CARD, PLAYER, card="Меч из белого железа", target=2))
    s = engine.apply(s, Action(ActionType.PLAY_CARD, PLAYER, card="Меч из белого железа", target=0)).state
    sk = skill(kb, "Дилюк", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.opponent.characters[0].hp == 10 - 4


def test_elemental_tuning(engine, duel):
    s = duel(hand=["Стратег"], dice={"cryo": 1, "pyro": 2})
    a = Action(ActionType.ELEMENTAL_TUNING, PLAYER, card="Стратег", die=DieType.CRYO)
    res = engine.apply(s, a)
    assert res.state.player.dice.get(DieType.PYRO) == 3 and res.state.player.hand.count() == 0
    assert not engine.is_legal(s, Action(ActionType.ELEMENTAL_TUNING, PLAYER, card="Стратег", die=DieType.PYRO))


def test_end_round_order_and_priority(engine, duel):
    s = duel()
    s = engine.apply(s, Action(ActionType.END_ROUND, PLAYER)).state
    assert s.first_to_end == PLAYER and s.active_player == OPPONENT
    s = engine.apply(s, Action(ActionType.END_ROUND, OPPONENT)).state
    assert s.phase == Phase.ROLL and s.active_player == PLAYER


def test_defeat_and_victory(engine, kb, duel):
    s = duel()
    for c in s.opponent.characters[1:]:
        c.hp, c.alive = 0, False
    s.opponent.characters[0].hp = 2
    sk = skill(kb, "Дилюк", "skill")
    res = engine.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    assert res.state.phase == Phase.GAME_OVER and res.state.winner == PLAYER


def test_death_of_active_triggers_choice_when_not_auto(kb, duel):
    from gcoach.rules.engine import RulesEngine
    eng = RulesEngine(kb, auto_choose_on_death=False)
    s = duel()
    s.opponent.characters[0].hp = 2
    sk = skill(kb, "Дилюк", "skill")
    res = eng.apply(s, Action(ActionType.ELEMENTAL_SKILL, PLAYER, actor=0, skill=sk.id))
    st = res.state
    assert st.pending_choose == OPPONENT and st.phase == Phase.CHOOSE_ACTIVE
    st = eng.apply(st, Action(ActionType.CHOOSE_ACTIVE, OPPONENT, target=2)).state
    assert st.opponent.active_index == 2 and st.phase == Phase.ACTION and st.active_player == OPPONENT


def test_illegal_when_not_your_turn(engine, duel):
    s = duel()
    s.active_player = OPPONENT
    assert not engine.is_legal(s, Action(ActionType.END_ROUND, PLAYER))
