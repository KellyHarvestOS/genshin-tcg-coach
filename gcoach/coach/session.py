"""Coach session: the advisory loop.

    screenshot -> recognition -> reconstruction -> verification -> planning
               -> recommendation -> (player acts in the game) -> screenshot ...

The session never acts in the game. `DRY_RUN` is permanently true; the only
"apply action" code path is the internal simulator used for demo states.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from ..ai.evaluation import Evaluator
from ..ai.planner import Planner, PlannerConfig, PlanResult
from ..ai.recommendation import Recommendation, RecommendationBuilder
from ..config import Config
from ..core.actions import Action
from ..core.enums import OPPONENT, PLAYER, Phase
from ..core.state import GameState
from ..knowledge.base import KnowledgeBase, default_kb
from ..logging_setup import memory_handler, trace
from ..memory.memory import MatchMemory, ShortTermMemory
from ..memory.post_match import analyse, save_report, to_markdown
from ..ocr.ocr import OCREngine
from ..providers.providers import ProviderError, make_provider
from ..rules.engine import RulesEngine
from ..screen_capture.capture import ScreenCapture, frame_difference
from ..verification.verifier import Verifier
from ..vision.cards import CardFinder
from ..vision.layout import Layout
from ..vision.reconstruct import MatchSetup, Reconstructor
from ..vision.recognizer import Recognizer
from ..vision.templates import TemplateLibrary
from ..voice.speech import speech_text
from ..voice.tts import VoiceService
from .scenarios import SCENARIOS, load_scenario
from .view import view_state

log = logging.getLogger("gcoach.session")


class CoachSession:
    def __init__(self, cfg: Config, kb: KnowledgeBase | None = None):
        self.cfg = cfg
        self.kb = kb or default_kb()
        self.engine = RulesEngine(self.kb, auto_choose_on_death=True)
        self.evaluator = Evaluator(self.engine)
        self.planner = Planner(self.engine, self.evaluator, PlannerConfig(
            depth=cfg.PLANNING_DEPTH, beam_width=cfg.BEAM_WIDTH, opponent_width=cfg.OPPONENT_WIDTH,
            lethal_depth=cfg.LETHAL_DEPTH, adaptive_depth=cfg.ADAPTIVE_DEPTH,
            time_budget_ms=cfg.TIME_BUDGET_MS, robust_lambda=cfg.ROBUST_LAMBDA))
        self.builder = RecommendationBuilder(self.engine)
        self.verifier = Verifier(self.engine)
        self.short = ShortTermMemory()
        self.match = MatchMemory(cfg.path("data/matches"))
        self.provider = make_provider(cfg)
        self.debug_dir = cfg.path("debug/screenshots")
        self.capture = ScreenCapture(cfg.MONITOR, cfg.SCREEN_REGION,
                                     self.debug_dir if cfg.SAVE_DEBUG_IMAGES else None)
        self.templates = TemplateLibrary(cfg.path("assets"), cfg.TEMPLATE_THRESHOLD)
        self.ocr = OCREngine(cfg.OCR_ENABLED, cfg.OCR_ENGINE, cfg.path("assets/ui/digits"), cfg.TESSERACT_CMD)
        self.layout = Layout.load(cfg.path(cfg.LAYOUT))
        self.recognizer = Recognizer(self.layout, self.templates, self.ocr,
                                     CardFinder(cfg.path("gcoach/web/img/cards"), self.kb.characters.keys()))
        self.reconstructor = Reconstructor(self.kb)
        self.setup = MatchSetup()
        self.voice = VoiceService(cfg)
        self.rec_serial = 0  # bumps on every fresh analysis: the UI speaks new advice once

        self.state: GameState | None = None
        self.source = "none"
        self.plan: PlanResult | None = None
        self.recommendation: Recommendation | None = None
        self.verification: dict[str, Any] | None = None
        self.recognition_regions: list[dict[str, Any]] = []
        self.confidence_map: dict[str, Any] = {}
        self.state_confidence = 0.0
        self.status = {"level": "info", "message": "Загрузите демо-партию или начните захват экрана."}
        self.ai_text: str | None = None
        self.last_report: dict[str, Any] | None = None
        self.timings: dict[str, float] = {}
        self.version = 0
        self.listeners: list[Callable[[dict], None]] = []
        self.lock = threading.RLock()
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._last_frame: np.ndarray | None = None
        self._stable = 0
        self._analysed_frame: np.ndarray | None = None

    # ==================================================================================
    # notifications
    # ==================================================================================
    def _changed(self) -> None:
        self.version += 1
        snap = self.snapshot()
        for fn in list(self.listeners):
            try:
                fn(snap)
            except Exception:
                log.exception("listener failed")

    def set_status(self, level: str, message: str) -> None:
        self.status = {"level": level, "message": message}

    # ==================================================================================
    # state entry points
    # ==================================================================================
    def load_demo(self, name: str) -> None:
        with self.lock:
            self.match.reset()
            self.verification = None
            self.ai_text = None
            state = load_scenario(self.kb, name)
            self.setup = MatchSetup([c.id for c in state.player.characters], [c.id for c in state.opponent.characters],
                                    list(state.player.hand.cards))
            self._set_state(state, "demo", 1.0, {})
            self.set_status("info", f"Демо: {SCENARIOS[name][0]}. Это симуляция — в игре ничего не происходит.")
            self._analyse()
        self._changed()

    def reset(self) -> None:
        """Back to the start screen: forget the current position and stop watching the screen."""
        self._watch_stop.set()
        with self.lock:
            self.match.reset()
            self.state = None
            self.source = "none"
            self.plan = None
            self.recommendation = None
            self.verification = None
            self.ai_text = None
            self.confidence_map = {}
            self.state_confidence = 0.0
            self.recognition_regions = []
            self._last_frame = self._analysed_frame = None
            self._stable = 0
            both = [c for c in self.setup.player + self.setup.opponent if c]
            self.recognizer.candidates = both if self.setup.player and self.setup.opponent else None
            self.set_status("info", "")
        self._changed()

    def set_state_json(self, text: str) -> None:
        with self.lock:
            state = GameState.from_json(text)
            self._set_state(state, "manual", 1.0, {})
            self.set_status("info", "Состояние задано вручную.")
            self._analyse()
        self._changed()

    def set_setup(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.setup = MatchSetup(list(data.get("player", [])), list(data.get("opponent", [])),
                                    list(data.get("player_hand", [])), data.get("turn") or None)
            # known compositions: the screen search only compares these characters (faster, safer)
            both = [c for c in self.setup.player + self.setup.opponent if c]
            self.recognizer.candidates = both if self.setup.player and self.setup.opponent else None
            if self.state is not None and self.source != "vision":
                # apply the new composition to the current state as well
                from .scenarios import make_character
                for side_name, ids in ((PLAYER, self.setup.player), (OPPONENT, self.setup.opponent)):
                    side = self.state.side(side_name)
                    for i, cid in enumerate(ids[: len(side.characters)]):
                        if cid and side.characters[i].id != cid:
                            side.characters[i] = make_character(self.kb, cid)
                if self.setup.player_hand:
                    self.state.player.hand.cards = list(self.setup.player_hand)
                if self.setup.turn:
                    self.state.active_player = self.setup.turn
                self._analyse()
            self.set_status("info", "Настройки партии сохранены.")
        self._changed()

    def set_turn(self, side: str) -> None:
        with self.lock:
            self.setup.turn = side
            if self.state is not None:
                self.state.active_player = side
                if self.state.phase == Phase.CHOOSE_ACTIVE and not self.state.pending_choose:
                    self.state.phase = Phase.ACTION
                self._analyse()
        self._changed()

    def _set_state(self, state: GameState, source: str, confidence: float, cmap: dict[str, Any]) -> None:
        self.state = state
        self.source = source
        self.state_confidence = confidence
        self.confidence_map = cmap
        self.short.current_state = state.to_dict()
        self.match.record_state(state)

    # ==================================================================================
    # analysis
    # ==================================================================================
    def _analyse(self) -> None:
        s = self.state
        if s is None:
            return
        self.rec_serial += 1
        if self.state_confidence < self.cfg.CONFIDENCE_THRESHOLD:
            problems = self.state.metadata.get("problems", []) if self.state.metadata else []
            self.recommendation = self.builder.uncertain(self.state_confidence, problems)
            self.plan = None
            self.set_status("warn", self.recommendation.message)
            return
        t0 = time.perf_counter()
        self.plan = self.planner.plan(s)
        self.timings["planning_ms"] = round((time.perf_counter() - t0) * 1000)
        self.recommendation = self.builder.build(s, self.plan, self.state_confidence)
        rec = self.recommendation
        self.short.current_plan = rec.future_plan
        self.short.threats = [r for r in rec.risk if "Существенных" not in r]
        if rec.status == "ok":
            opts = [{"label": o.action.label, "type": o.action.type.value,
                     "win_probability": round(o.win_probability, 3)} for o in self.plan.options[:8]]
            self.match.record_decision(s, rec.to_dict(), opts)
        if s.phase == Phase.GAME_OVER:
            self.match.result = s.winner
        log.info("recommendation: %s (%.0f%%, depth %d, %d nodes, %d ms)", rec.title or rec.status,
                 rec.confidence * 100, self.plan.depth, self.plan.nodes, self.timings["planning_ms"])
        trace(log, "options: %s", [(o.action.label, round(o.value, 2)) for o in self.plan.options])

    def _recommended_action(self) -> Action | None:
        if self.recommendation and self.recommendation.action:
            return Action.from_dict(self.recommendation.action)
        return None

    # ==================================================================================
    # verification (shared by vision and the simulator)
    # ==================================================================================
    def _accept_new_state(self, new: GameState, source: str, confidence: float, cmap: dict[str, Any],
                          reliable: set[str] | None = None) -> None:
        before = self.state
        if before is not None and before.player.characters:
            res = self.verifier.verify(before, new, self._recommended_action(), reliable)
            self.verification = res.to_dict()
            if res.status == "NO_CHANGE":
                return  # nothing happened - keep the current plan
            self.short.last_changes.appendleft({"round": new.round, "changes": res.changes[:8]})
            if res.matched:
                for label in res.matched:
                    self.short.last_actions.appendleft(label)
                first = res.matched_actions[0] if res.matched_actions else None
                wp = None
                if first is not None and self.plan:
                    wp = next((o.win_probability for o in self.plan.options if o.action.same_as(first)), None)
                self.match.record_actual(res.matched[0], first.type.value if first else "", wp, res.status,
                                         [e.to_dict() for e in res.events])
            level = {"CONFIRMED": "ok", "DIFFERENT_ACTION": "info", "PARTIAL": "warn"}.get(res.status, "warn")
            self.set_status(level, res.message)
        self._set_state(new, source, confidence, cmap)
        self._analyse()

    def simulate_recommended(self, opponent_reply: bool = True) -> None:
        """Demo simulator: pretend the player followed the advice (internal model only)."""
        with self.lock:
            if self.state is None or self.source == "vision":
                self.set_status("warn", "Симулятор доступен только для демо/ручного состояния.")
            else:
                a = self._recommended_action()
                s = self.state
                if a is None and (s.pending_choose or s.active_player) == OPPONENT and self.plan and self.plan.options:
                    a = self.plan.options[0].action
                if a is None:
                    self.set_status("warn", "Нет действия для симуляции.")
                else:
                    res = self.engine.apply(s, a)
                    new = res.state
                    guard = 0
                    while opponent_reply and new.phase in (Phase.ACTION, Phase.CHOOSE_ACTIVE) and \
                            (new.pending_choose or new.active_player) == OPPONENT and guard < 6:
                        guard += 1
                        replies = self.planner.opponent.responses(new, 4)
                        if not replies:
                            break
                        # the most dangerous plausible reply
                        best = min(replies, key=lambda r: self.evaluator.evaluate(self.engine.apply(new, r[0]).state).score)
                        new = self.engine.apply(new, best[0]).state
                    if new.phase == Phase.ROLL:
                        new = self._demo_new_round(new)
                    self._accept_new_state(new, self.source, 1.0, {})
        self._changed()

    def _demo_new_round(self, s: GameState) -> GameState:
        """Demo only: roll 8 dice for the player so the simulation can continue."""
        import random

        from ..core.enums import DieType
        from ..core.state import DicePool
        s = s.clone()
        rng = random.Random(s.round * 7919)
        team = [d for c in s.player.characters if c.alive
                if (d := DieType.for_element(self.engine.char_def(c).element))]
        faces = [DieType.OMNI] + team
        counts: dict = {}
        for _ in range(8):
            die = rng.choice(faces + list(DieType))
            counts[die] = counts.get(die, 0) + 1
        s.player.dice = DicePool(counts)
        s.player.hand.cards += ["Стратег"] if s.player.hand.hidden else []
        s.player.hand.hidden = max(0, s.player.hand.hidden - 1)
        s.phase = Phase.ACTION
        return s

    # ==================================================================================
    # vision
    # ==================================================================================
    def process_frame(self, frame: np.ndarray, save_debug: bool = True) -> None:
        t0 = time.perf_counter()
        rec = self.recognizer.recognize(frame, annotate=self.cfg.DEBUG_MODE)
        self.timings["recognition_ms"] = round((time.perf_counter() - t0) * 1000)
        self.recognition_regions = rec.regions
        if save_debug and self.cfg.SAVE_DEBUG_IMAGES:
            self.capture.save_debug(frame, "frame")
            if rec.annotated is not None:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(self.debug_dir / "latest_annotated.jpg"), rec.annotated,
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
        with self.lock:
            prev = self.state if self.source == "vision" else None
            result = self.reconstructor.build(rec.observed, prev, self.setup)
            if prev is None and result.confidence < self.cfg.CONFIDENCE_THRESHOLD:
                # nothing recognised yet (menu, open world, another screen): stay on the start screen
                self.set_status("warn", "Не вижу партию «Священного призыва» на экране игры.")
                self._changed()
                return
            reliable = {k for k, v in result.confidence_map.items() if v["confidence"] >= 0.75}
            reliable_obs = self._reliable_observable_keys(reliable)
            if self.source != "vision":
                self.state = None  # do not verify a demo state against the screen
            self._accept_new_state(result.state, "vision", result.confidence, result.confidence_map, reliable_obs)
            if not (self.setup.player and self.setup.opponent):
                # the characters of this match are known now: later frames only compare these (fast)
                ids = [c.id for c in result.state.player.characters + result.state.opponent.characters
                       if c.id != "unknown"]
                self.recognizer.candidates = ids or None
            if result.confidence < self.cfg.CONFIDENCE_THRESHOLD:
                self.set_status("warn", "Не удалось уверенно распознать состояние. Обновите экран или сделайте скриншот.")
        self._changed()

    @staticmethod
    def _reliable_observable_keys(reliable: set[str]) -> set[str]:
        out = set()
        for k in reliable:
            k2 = k.replace("characters[", "c").replace("]", "")
            if k2.endswith(".dice_count"):
                k2 = k2.replace(".dice_count", ".dice")
            if k2.endswith(".active_index"):
                k2 = k2.replace(".active_index", ".active")
            out.add(k2)
        return out

    def capture_once(self) -> None:
        try:
            frame = self.capture.grab()
        except Exception as exc:
            with self.lock:
                self.set_status("error", f"Захват экрана не удался: {exc}")
            self._changed()
            return
        self.process_frame(frame)

    def process_image_bytes(self, data: bytes) -> None:
        arr = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            with self.lock:
                self.set_status("error", "Не удалось прочитать изображение.")
            self._changed()
            return
        self.process_frame(frame)

    # ------------------------------------------------------------------------------------
    def start_watch(self) -> None:
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        finder = self.recognizer.finder
        if finder is not None and finder.available:  # templates for a 1080p game window, in the background
            threading.Thread(target=finder.warm, args=(1080, 1920), name="card-warmup", daemon=True).start()
        self._watch_thread = threading.Thread(target=self._watch_loop, name="screen-watch", daemon=True)
        self._watch_thread.start()
        self.set_status("info", "Слежение за экраном включено. Делайте ходы в игре сами — я подскажу.")
        self._changed()

    def stop_watch(self) -> None:
        self._watch_stop.set()
        self.set_status("info", "Слежение за экраном остановлено.")
        self._changed()

    @property
    def watching(self) -> bool:
        return bool(self._watch_thread and self._watch_thread.is_alive() and not self._watch_stop.is_set())

    def _watch_loop(self) -> None:
        waiting = None
        while not self._watch_stop.is_set():
            try:
                frame = self.capture.grab_game()
                if (frame is None) != waiting:  # announce only when the game window appears/disappears
                    waiting = frame is None
                    with self.lock:
                        self.set_status("info", "Жду окно Genshin Impact — откройте игру и партию «Священного призыва»."
                                        if waiting else "Вижу Genshin Impact, анализирую экран…")
                    self._changed()
                if frame is None:
                    self._stable = 0
                    self._watch_stop.wait(self.cfg.SCREENSHOT_INTERVAL)
                    continue
                diff = frame_difference(self._last_frame, frame)
                self._last_frame = frame
                if diff < self.cfg.CHANGE_THRESHOLD:
                    self._stable += 1
                else:
                    self._stable = 0
                # analyse once the screen has settled (animations finished) and differs from the last analysis
                if self._stable == self.cfg.STABLE_FRAMES and \
                        frame_difference(self._analysed_frame, frame) >= self.cfg.CHANGE_THRESHOLD:
                    self._analysed_frame = frame
                    self.process_frame(frame)
            except Exception:
                log.exception("watch loop error")
            self._watch_stop.wait(self.cfg.SCREENSHOT_INTERVAL)

    # ==================================================================================
    # match end / AI
    # ==================================================================================
    def end_match(self, result: str | None = None, with_ai: bool = False) -> dict[str, Any]:
        with self.lock:
            if result is None and self.state is not None and self.state.phase == Phase.GAME_OVER:
                result = self.state.winner
            report = analyse(self.match, result)
            if with_ai:
                try:
                    report["ai_commentary"] = self.provider.post_match(report)
                except ProviderError as exc:
                    report["ai_commentary"] = f"(AI недоступен: {exc})"
            self.match.result = result
            self.match.save()
            path = save_report(self.match, report)
            report["markdown"] = to_markdown(report)
            report["path"] = str(path)
            self.last_report = report
            self.set_status("info", f"Разбор партии сохранён: {path.name}")
        self._changed()
        return report

    def explain_with_ai(self) -> str:
        with self.lock:
            if self.recommendation is None:
                return "Нет рекомендации для объяснения."
            rec = self.recommendation.to_dict()
            rec.pop("expected_state", None)
            context = {"recommendation": rec, "state": view_state(self.engine, self.state) if self.state else None}
        try:
            text = self.provider.explain(context)
        except ProviderError as exc:
            text = f"AI недоступен: {exc}"
        with self.lock:
            self.ai_text = text
        self._changed()
        return text

    # ==================================================================================
    # snapshot for the UI
    # ==================================================================================
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            rec = self.recommendation.to_dict() if self.recommendation else None
            if rec:
                rec.pop("expected_state", None)
                rec["speech"] = speech_text(self.engine, self.state, rec)
            candidates, tree = [], []
            if self.plan:
                for o in self.plan.options[:12]:
                    candidates.append({"label": o.action.label, "type": o.action.type.value,
                                       "value": round(o.value, 2), "win_probability": round(o.win_probability, 3),
                                       "fast": o.action.is_fast})
                for o in self.plan.options[:4]:
                    tree.append({"label": o.action.label, "win_probability": round(o.win_probability, 3),
                                 "line": [{"side": st.side, "label": st.action.label,
                                           "p": round(st.probability, 2)} for st in o.line],
                                 "replies": o.replies})
            return {
                "version": self.version,
                "dry_run": True,
                "source": self.source,
                "watching": self.watching,
                "status": self.status,
                "state": view_state(self.engine, self.state) if self.state else None,
                "state_confidence": round(self.state_confidence, 3),
                "confidence_map": self.confidence_map,
                "recommendation": rec,
                "rec_serial": self.rec_serial,
                "voice": self.voice.public(),
                "verification": self.verification,
                "memory": self.short.to_dict(),
                "ai_text": self.ai_text,
                "setup": {"player": self.setup.player, "opponent": self.setup.opponent,
                          "player_hand": self.setup.player_hand, "turn": self.setup.turn},
                "report": self.last_report,
                "debug": {
                    "enabled": self.cfg.DEBUG_MODE,
                    "candidates": candidates,
                    "tree": tree,
                    "regions": self.recognition_regions,
                    "timings": self.timings,
                    "ocr": self.ocr.name,
                    "templates": {c: self.templates.count(c) for c in self.templates.templates},
                    "layout_calibrated": self.layout.calibrated,
                    "logs": memory_handler.records[-60:],
                    "raw_state": self.state.to_dict() if self.state and self.cfg.DEBUG_MODE else None,
                },
                "knowledge": self.kb.manifest,
                "provider": self.provider.name,
                "scenarios": {k: v[0] for k, v in SCENARIOS.items()},
            }
