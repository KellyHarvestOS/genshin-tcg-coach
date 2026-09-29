from gcoach.ai.evaluation import Evaluator
from gcoach.ai.opponent import OpponentModel, p_at_least
from gcoach.ai.recommendation import RecommendationBuilder
from gcoach.core.actions import Action
from gcoach.core.enums import OPPONENT, PLAYER, ActionType, DieType, Phase
from gcoach.core.state import DicePool


def test_generator_only_legal_actions(engine, gen, scenario):
    s = scenario("opening")
    actions = gen.legal_actions(s, PLAYER)
    assert actions and all(engine.is_legal(s, a) for a in actions)
    types = {a.type for a in actions}
    assert {ActionType.ELEMENTAL_SKILL, ActionType.NORMAL_ATTACK, ActionType.SWITCH_CHARACTER,
            ActionType.END_ROUND, ActionType.PLAY_CARD} <= types
    assert ActionType.ELEMENTAL_BURST not in types  # no energy yet
    assert gen.legal_actions(s, OPPONENT) == []  # not their turn


def test_generator_respects_dice(gen, duel):
    s = duel(dice={"geo": 1})
    types = {a.type for a in gen.legal_actions(s, PLAYER)}
    assert ActionType.ELEMENTAL_SKILL not in types and ActionType.SWITCH_CHARACTER in types


def test_generator_skips_unmodelled_cards(gen, duel, kb):
    unmodelled = next(c.id for c in kb.cards.values() if not c.modelled and c.type == "event")
    s = duel(hand=[unmodelled])
    assert not any(a.card == unmodelled for a in gen.legal_actions(s, PLAYER))


def test_evaluation_features(engine, duel):
    ev = Evaluator(engine)
    s = duel()
    base = ev.evaluate(s)
    hurt = s.clone()
    hurt.opponent.characters[0].hp = 3
    assert ev.evaluate(hurt).score > base.score
    assert set(base.features) >= {"hp", "energy", "dice", "cards", "summons", "statuses", "tempo", "lethal", "threat"}
    won = s.clone()
    won.phase, won.winner = Phase.GAME_OVER, PLAYER
    assert ev.evaluate(won).win_probability == 1.0


def test_dice_worthless_after_declaring_end(engine, duel):
    ev = Evaluator(engine)
    s = duel()
    ended = s.clone()
    ended.player.declared_end = True
    assert ev.evaluate(ended).features["dice"] < ev.evaluate(s).features["dice"]


def test_opponent_model_probabilities(engine, gen, duel):
    s = duel()
    s.active_player = OPPONENT
    om = OpponentModel(gen)
    replies = om.responses(s, width=4)
    assert abs(sum(p for _, p in replies) - 1) < 1e-6
    assert all(a.type not in (ActionType.PLAY_CARD, ActionType.ELEMENTAL_TUNING) for a, _ in replies)
    s.opponent.dice = DicePool({}, 1)
    types = {a.type for a, _ in om.responses(s, width=6)}
    assert ActionType.ELEMENTAL_SKILL not in types
    assert p_at_least(3, 8, 0.45) > p_at_least(3, 3, 0.45)


def test_planner_finds_lethal(planner, scenario):
    r = planner.plan(scenario("lethal"))
    best = r.options[0]
    assert r.critical and best.win_probability > 0.99
    assert best.line[-1].action.type == ActionType.ELEMENTAL_BURST
    assert all(st.side == PLAYER for st in best.line)  # wins without letting the opponent act


def test_planner_uses_reaction_to_defeat(planner, duel):
    from gcoach.core.enums import Element
    s = duel(opponent=("Син Цю", "Фишль", "Гань Юй"), dice={"pyro": 3})
    xq = s.opponent.characters[0]
    xq.hp, xq.aura = 5, [Element.HYDRO]  # only Pyro skill + Vaporize (3+2) defeats it
    r = planner.plan(s)
    best = r.options[0]
    assert best.action.skill == "Дилюк:Огненный натиск"
    assert any(e.kind == "death" for e in best.immediate_events)


def test_planner_depth_configurable(engine, scenario):
    from gcoach.ai.planner import Planner, PlannerConfig
    p = Planner(engine, Evaluator(engine), PlannerConfig(depth=1, adaptive_depth=False))
    r = p.plan(scenario("opening"))
    assert r.depth == 1 and r.options


def test_planner_waits_on_opponent_turn(planner, scenario):
    s = scenario("opening")
    s.active_player = OPPONENT
    r = planner.plan(s)
    assert r.waiting_for_opponent and all(o.action.side == OPPONENT for o in r.options)


def test_recommendation_format(planner, engine, scenario):
    s = scenario("reaction_setup")
    rec = RecommendationBuilder(engine).build(s, planner.plan(s), 0.95)
    assert rec.status == "ok" and rec.title and rec.why and rec.cost_text and rec.risk
    assert rec.expected_result and 0 < rec.confidence <= 0.95
    assert len(rec.alternatives) <= 3


def test_uncertain_state_message(engine):
    rec = RecommendationBuilder(engine).uncertain(0.2, ["active_player"])
    assert rec.status == "uncertain_state"
    assert rec.message == "Не удалось уверенно распознать состояние. Обновите экран или сделайте скриншот."
