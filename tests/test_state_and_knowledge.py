import json

from gcoach.core.enums import DieType, Element, Knowledge
from gcoach.core.observed import Observed
from gcoach.core.state import GameState, Status
from gcoach.knowledge.wiki_parse import parse_card_cost, parse_reactions, parse_skill_cost, parse_skill_description
from gcoach.knowledge.wikitext import clean, find_templates


def test_gamestate_roundtrip_serialization(scenario):
    s = scenario("reaction_setup")
    s.player.characters[0].statuses.append(Status("Меч дождя", 2, None, {"x": 1}))
    text = s.to_json()
    back = GameState.from_json(text)
    assert back.to_dict() == s.to_dict()
    assert back.player.dice.counts[DieType.PYRO] == 3
    assert back.opponent.characters[1].aura == [Element.HYDRO]
    json.loads(text)  # valid JSON


def test_clone_is_independent(scenario):
    s = scenario("opening")
    c = s.clone()
    c.player.characters[0].hp = 1
    c.player.dice.add(DieType.CRYO, 3)
    c.player.hand.cards.append("X")
    assert s.player.characters[0].hp == 10
    assert s.player.dice.get(DieType.CRYO) == 1
    assert "X" not in s.player.hand.cards


def test_observed_never_fakes_knowledge():
    u = Observed.unknown("opponent hand")
    assert u.confidence == 0 and u.knowledge == Knowledge.UNKNOWN and not u.is_known
    inf = Observed.inferred(5, 0.99)
    assert inf.confidence <= 0.9 and inf.knowledge == Knowledge.INFERRED


def test_knowledge_base_loaded_from_wiki(kb):
    assert kb.manifest["source"].startswith("https://genshin-impact.fandom.com/ru/wiki/")
    assert len(kb.characters) > 100
    diluc = kb.character("Дилюк")
    assert diluc.hp == 10 and diluc.element == Element.PYRO and diluc.energy == 3
    burst = [s for s in diluc.skills if s.type.value == "burst"][0]
    assert burst.cost == {"pyro": 4, "energy": 3}
    assert {"op": "damage", "element": "pyro", "amount": 8} in burst.effects
    assert kb.character("Гань Юй").hp == 12  # value from the wiki, not from memory
    assert kb.reaction("melt")["bonus"] == 2


def test_unknown_character_is_flagged_generic(kb):
    c = kb.character("Совершенно неизвестный")
    assert c.generic and not kb.has_character("Совершенно неизвестный")


def test_wikitext_templates_and_parsing():
    raw = ("{{Навык|Название = Тест|Тип = Элементальный навык|Стоимость = {{СП7 Стоимость|3|Пиро}}"
           "|Описание = Наносит {{Цвет|Пиро урон}}, 3 ед. При третьем использовании этого навыка в каждом раунде "
           "наносит дополнительно 2 ед. урона.}}")
    tpl = find_templates(raw, "Навык")[0]
    assert parse_skill_cost(tpl.named["Стоимость"]) == {"pyro": 3}
    effects, statuses, unparsed, text = parse_skill_description(tpl.named["Описание"])
    assert {"op": "damage", "element": "pyro", "amount": 3} in effects
    assert {"op": "nth_use_bonus", "n": 3, "amount": 2} in effects
    assert clean("{{Цвет|Гидро урон}} ''x''") == "Гидро урон x"


def test_status_block_parsing():
    raw = ("Наносит {{Цвет|Гидро урон}}, 2 ед., создаёт '''Меч дождя'''.{{СП7 Эффект|{{Icon/СП7|Снижение урона}} "
           "'''Меч дождя'''<br>'''Когда ваш активный персонаж получает не менее 3 ед. урона: '''снижает получаемый "
           "урон на 1 ед.<br>'''Кол-во применений:''' 2}}")
    effects, statuses, unparsed, _ = parse_skill_description(raw)
    st = statuses[0]
    assert st["name"] == "Меч дождя" and st["usages"] == 2
    assert st["behaviors"]["reduce"] == {"min_damage": 3, "amount": 1}
    assert {"op": "status", "id": "Меч дождя", "scope": "combat"} in effects


def test_card_cost_parsing():
    assert parse_card_cost({"Соответствующий": "2"}) == {"same": 2}
    assert parse_card_cost({"Соответствующий": "3", "Элемент": "Крио"}) == {"cryo": 3}
    assert parse_card_cost({"Неопределённый": "1", "Энергия": "2"}) == {"unaligned": 1, "energy": 2}


def test_reaction_table_parsing():
    wt = """===Elemental Reactions: Effects===
{|class="article-table"
!Title
!Description
|-
|Melt
|Deal +2 DMG for this instance
|-
|Superconduct
|Deal +1 DMG for this instance, deal 1 Piercing DMG to all opposing characters except the target
|}"""
    r = parse_reactions(wt)
    assert r["melt"]["bonus"] == 2
    assert r["superconduct"]["piercing_others"] == 1
