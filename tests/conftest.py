import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcoach.ai.evaluation import Evaluator  # noqa: E402
from gcoach.ai.generator import ActionGenerator  # noqa: E402
from gcoach.ai.planner import Planner, PlannerConfig  # noqa: E402
from gcoach.coach.scenarios import load_scenario, make_character  # noqa: E402
from gcoach.core.enums import OPPONENT, PLAYER, DieType, Phase  # noqa: E402
from gcoach.core.state import DicePool, GameState, Hand, PlayerState  # noqa: E402
from gcoach.knowledge.base import default_kb  # noqa: E402
from gcoach.rules.engine import RulesEngine  # noqa: E402


@pytest.fixture(scope="session")
def kb():
    return default_kb()


@pytest.fixture
def engine(kb):
    return RulesEngine(kb)


@pytest.fixture
def gen(engine):
    return ActionGenerator(engine)


@pytest.fixture
def planner(engine):
    return Planner(engine, Evaluator(engine), PlannerConfig(depth=2, beam_width=6, time_budget_ms=4000))


@pytest.fixture
def scenario(kb):
    return lambda name: load_scenario(kb, name)


@pytest.fixture
def duel(kb):
    """Factory for a small mock GameState: duel(player_ids, opponent_ids, dice=..., **kw)."""
    def make(player=("Дилюк", "Кэйа", "Сахароза"), opponent=("Фишль", "Син Цю", "Гань Юй"),
             dice=None, opp_dice=8, hand=(), active=0, opp_active=0):
        p = PlayerState(PLAYER, [make_character(kb, c) for c in player], active,
                        DicePool({DieType(k): v for k, v in (dice or {"omni": 8}).items()}), Hand(list(hand)))
        o = PlayerState(OPPONENT, [make_character(kb, c) for c in opponent], opp_active, DicePool({}, opp_dice),
                        Hand([], 5))
        return GameState(round=1, phase=Phase.ACTION, active_player=PLAYER, player=p, opponent=o)
    return make
