"""Forward planning.

Architecture: depth-limited expectimax with beam pruning.

* Our nodes: every legal action is simulated once and ordered by static
  evaluation; only the best `beam` children are searched deeper (beam narrows
  with depth). Fast actions do not consume depth, but at most
  `max_fast_chain` fast actions may be chained in a row.
* Opponent nodes: the opponent model proposes plausible replies with prior
  probabilities (hidden dice => affordability probability). The node value is
  `λ·worst + (1-λ)·expected`, so we respect a competent opponent without
  assuming it is omniscient.
* Search stops at depth 0, at game over, or at the end of the round (dice are
  re-rolled, so the next round is evaluated heuristically instead of guessed).
* Iterative deepening under a time budget + transposition table; depth grows
  automatically in critical positions (possible lethal on either side).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..core.actions import Action
from ..core.enums import OPPONENT, PLAYER, Phase
from ..core.state import GameState, PlayerState
from ..rules.engine import RulesEngine
from ..rules.events import Event
from .evaluation import Evaluation, Evaluator
from .generator import ActionGenerator
from .opponent import OpponentModel

log = logging.getLogger("gcoach.planner")


@dataclass
class PlannerConfig:
    depth: int = 3
    beam_width: int = 10
    opponent_width: int = 4
    lethal_depth: int = 5
    adaptive_depth: bool = True
    time_budget_ms: int = 2500
    robust_lambda: float = 0.6
    max_fast_chain: int = 3


@dataclass
class LineStep:
    action: Action
    events: list[Event]
    side: str
    probability: float = 1.0


@dataclass
class RootOption:
    action: Action
    value: float
    win_probability: float
    line: list[LineStep] = field(default_factory=list)
    immediate_events: list[Event] = field(default_factory=list)
    after_state: GameState | None = None
    static_eval: Evaluation | None = None
    replies: list[dict] = field(default_factory=list)  # debug: opponent replies considered


@dataclass
class PlanResult:
    options: list[RootOption]
    depth: int
    nodes: int
    elapsed_ms: float
    root_eval: Evaluation
    critical: bool = False
    waiting_for_opponent: bool = False
    timed_out: bool = False


class _Timeout(Exception):
    pass


def state_key(s: GameState) -> tuple:
    def side_key(p: PlayerState) -> tuple:
        return (
            p.active_index, p.declared_end,
            tuple((c.hp, c.energy, c.alive, tuple(c.aura),
                   tuple((st.id, st.usages, st.duration) for st in c.statuses),
                   tuple(e.id for e in c.equipment), tuple(sorted(c.skill_uses_round.items())))
                  for c in p.characters),
            tuple(sorted((d.value, n) for d, n in p.dice.counts.items())), p.dice.hidden,
            tuple(sorted(p.hand.cards)), p.hand.hidden,
            tuple((x.id, x.usages) for x in p.summons),
            tuple((x.id, x.usages, x.duration) for x in p.combat_statuses), len(p.supports),
        )
    return (s.round, s.phase, s.active_player, s.first_to_end, s.pending_choose,
            side_key(s.player), side_key(s.opponent))


class Planner:
    def __init__(self, engine: RulesEngine, evaluator: Evaluator, config: PlannerConfig | None = None):
        self.engine = engine
        self.evaluator = evaluator
        self.gen = ActionGenerator(engine)
        self.opponent = OpponentModel(self.gen)
        self.cfg = config or PlannerConfig()
        self._tt: dict[tuple, tuple[float, list[LineStep]]] = {}
        self._eval_cache: dict[tuple, Evaluation] = {}
        self.nodes = 0
        self._deadline = 0.0

    # ------------------------------------------------------------------------------------
    def evaluate(self, s: GameState) -> Evaluation:
        key = state_key(s)
        ev = self._eval_cache.get(key)
        if ev is None:
            ev = self.evaluator.evaluate(s)
            self._eval_cache[key] = ev
        return ev

    def is_critical(self, s: GameState) -> bool:
        ev = self.evaluate(s)
        if ev.features.get("lethal") or ev.features.get("threat"):
            return True
        return len(s.opponent.alive_indices()) == 1 or len(s.player.alive_indices()) == 1

    def plan(self, s: GameState, depth: int | None = None, time_budget_ms: int | None = None) -> PlanResult:
        start = time.perf_counter()
        self._tt.clear()
        self._eval_cache.clear()
        self.nodes = 0
        root_eval = self.evaluate(s)
        critical = self.is_critical(s)
        target_depth = depth or self.cfg.depth
        if self.cfg.adaptive_depth and critical and depth is None:
            target_depth = max(target_depth, self.cfg.lethal_depth)
        budget = (time_budget_ms or self.cfg.time_budget_ms) / 1000.0

        mover = s.pending_choose or s.active_player
        if s.phase not in (Phase.ACTION, Phase.CHOOSE_ACTIVE) or mover != PLAYER:
            options = self._predict_opponent(s) if mover == OPPONENT and s.phase in (
                Phase.ACTION, Phase.CHOOSE_ACTIVE) else []
            return PlanResult(options, 1, self.nodes, (time.perf_counter() - start) * 1000, root_eval,
                              critical, waiting_for_opponent=True)

        roots = self.gen.legal_actions(s, PLAYER)
        applied = [(a, self.engine.apply(s, a)) for a in roots]
        best_options: list[RootOption] = []
        reached = 0
        timed_out = False
        for d in range(1, target_depth + 1):
            self._deadline = float("inf") if d == 1 else start + budget
            try:
                opts = []
                for a, res in applied:
                    nd = d if a.is_fast else d - 1
                    v, line = self._search(res.state, nd, 1 if a.is_fast else 0, ply=1)
                    if res.state.phase == Phase.GAME_OVER:
                        v = self.evaluate(res.state).score  # immediate win beats any later win
                    opt = RootOption(a, v, self.evaluator.to_probability(v),
                                     [LineStep(a, res.events, PLAYER)] + line, res.events, res.state,
                                     self.evaluate(res.state))
                    opts.append(opt)
                opts.sort(key=lambda o: o.value, reverse=True)
                best_options, reached = opts, d
            except _Timeout:
                timed_out = True
                break
        for opt in best_options[:4]:
            opt.replies = self._reply_summary(opt.after_state)
        elapsed = (time.perf_counter() - start) * 1000
        log.debug("plan: depth=%d nodes=%d %.0fms critical=%s", reached, self.nodes, elapsed, critical)
        return PlanResult(best_options, reached, self.nodes, elapsed, root_eval, critical, False, timed_out)

    # ------------------------------------------------------------------------------------
    def _beam(self, ply: int) -> int:
        return max(3, self.cfg.beam_width - 2 * (ply - 1))

    def _search(self, s: GameState, depth: int, fast_chain: int, ply: int) -> tuple[float, list[LineStep]]:
        self.nodes += 1
        if self.nodes & 63 == 0 and time.perf_counter() > self._deadline:
            raise _Timeout()
        if s.phase == Phase.GAME_OVER:
            v = self.evaluate(s).score
            # prefer faster wins and slower losses
            return (v - ply if v > 0 else v + ply if v < 0 else v), []
        if s.phase in (Phase.ROLL, Phase.END) or depth <= 0:
            return self.evaluate(s).score, []
        key = (state_key(s), depth, fast_chain)
        cached = self._tt.get(key)
        if cached is not None:
            return cached
        mover = s.pending_choose or s.active_player
        if mover == PLAYER:
            result = self._max_node(s, depth, fast_chain, ply)
        else:
            result = self._opp_node(s, depth, ply)
        self._tt[key] = result
        return result

    def _max_node(self, s: GameState, depth: int, fast_chain: int, ply: int) -> tuple[float, list[LineStep]]:
        actions = self.gen.legal_actions(s, PLAYER)
        if fast_chain >= self.cfg.max_fast_chain:
            actions = [a for a in actions if not a.is_fast]
        if not actions:
            return self.evaluate(s).score, []
        children = []
        for a in actions:
            res = self.engine.apply(s, a)
            children.append((self.evaluate(res.state).score, a, res))
        children.sort(key=lambda t: t[0], reverse=True)
        best, best_line = float("-inf"), []
        for _, a, res in children[: self._beam(ply)]:
            nd = depth if a.is_fast else depth - 1
            nf = fast_chain + 1 if a.is_fast else 0
            v, line = self._search(res.state, nd, nf, ply + 1)
            if v > best:
                best, best_line = v, [LineStep(a, res.events, PLAYER)] + line
        return best, best_line

    def _opp_node(self, s: GameState, depth: int, ply: int) -> tuple[float, list[LineStep]]:
        responses = self.opponent.responses(s, self.cfg.opponent_width)
        if not responses:
            return self.evaluate(s).score, []
        results = []
        for a, p in responses:
            res = self.engine.apply(s, a)
            nd = depth if a.is_fast else depth - 1
            v, line = self._search(res.state, nd, 0, ply + 1)
            results.append((v, p, a, res, line))
        vmin = min(r[0] for r in results)
        expected = sum(r[0] * r[1] for r in results)
        lam = self.cfg.robust_lambda
        value = lam * vmin + (1 - lam) * expected
        if value > 500:
            # a "winning" line that lets the opponent act first is exposed to their hidden cards
            value -= 3
        worst = min(results, key=lambda r: r[0])
        line = [LineStep(worst[2], worst[3].events, s.pending_choose or s.active_player, worst[1])] + worst[4]
        return value, line

    # ------------------------------------------------------------------------------------
    def _reply_summary(self, s: GameState | None) -> list[dict]:
        if s is None or (s.pending_choose or s.active_player) != OPPONENT or s.phase != Phase.ACTION:
            return []
        out = []
        for a, p in self.opponent.responses(s, self.cfg.opponent_width):
            res = self.engine.apply(s, a)
            ev = self.evaluate(res.state)
            dmg = sum(e.data.get("amount", 0) for e in res.events if e.kind == "damage" and e.side == PLAYER)
            out.append({"label": a.label, "probability": round(p, 3), "damage_to_us": dmg,
                        "win_probability": round(ev.win_probability, 3)})
        return out

    def _predict_opponent(self, s: GameState) -> list[RootOption]:
        opts = []
        for a, p in self.opponent.responses(s, self.cfg.opponent_width + 2):
            res = self.engine.apply(s, a)
            ev = self.evaluate(res.state)
            opts.append(RootOption(a, ev.score, ev.win_probability, [LineStep(a, res.events, OPPONENT, p)],
                                   res.events, res.state, ev))
            a.confidence = p
        opts.sort(key=lambda o: o.action.confidence, reverse=True)
        return opts
