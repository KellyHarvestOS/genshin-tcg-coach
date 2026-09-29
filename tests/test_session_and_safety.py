import re
from pathlib import Path

from gcoach.config import ROOT, load_config
from gcoach.coach.session import CoachSession
from gcoach.memory.memory import MatchMemory
from gcoach.memory.post_match import analyse, to_markdown
from gcoach.providers.providers import MockProvider

# Anything that could send input to / read memory of the game process is forbidden.
FORBIDDEN = [
    r"\bimport\s+pyautogui", r"\bimport\s+pynput", r"\bimport\s+keyboard\b", r"\bimport\s+mouse\b",
    r"\bimport\s+pydirectinput", r"\bimport\s+win32api", r"\bimport\s+pymem", r"from\s+pynput",
    r"SendInput", r"mouse_event", r"keybd_event", r"WriteProcessMemory", r"ReadProcessMemory",
    r"OpenProcess", r"SetWindowsHookEx", r"CreateRemoteThread", r"PostMessage", r"SendMessage\w*\(",
    r"AUTO_PLAY", r"BOT_MODE", r"CLICK_MODE",
]


def test_no_game_control_code():
    offenders = []
    for path in (ROOT / "gcoach").rglob("*"):
        if path.suffix not in (".py", ".js", ".html"):
            continue
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            if re.search(pat, text):
                offenders.append(f"{path.name}: {pat}")
    assert offenders == []


def test_dry_run_cannot_be_disabled(monkeypatch):
    monkeypatch.setenv("GCOACH_DRY_RUN", "false")
    assert load_config().DRY_RUN is True


def _session():
    cfg = load_config()
    cfg.SAVE_DEBUG_IMAGES = False
    cfg.TIME_BUDGET_MS = 1500
    cfg.PLANNING_DEPTH = 2
    return CoachSession(cfg)


def test_session_demo_flow_and_snapshot(tmp_path):
    s = _session()
    s.match = MatchMemory(tmp_path)
    s.load_demo("reaction_setup")
    snap = s.snapshot()
    assert snap["dry_run"] is True and snap["recommendation"]["status"] == "ok"
    assert snap["state"]["player"]["characters"][0]["name"] == "Дилюк"
    s.simulate_recommended()
    assert s.snapshot()["verification"]["status"] in ("CONFIRMED", "DIFFERENT_ACTION")
    report = s.end_match("player")
    assert "## MISTAKES" in report["markdown"] and Path(report["path"]).exists()


def test_reset_returns_to_start_screen(tmp_path):
    s = _session()
    s.match = MatchMemory(tmp_path)
    s.load_demo("opening")
    s.reset()
    snap = s.snapshot()
    assert snap["state"] is None and snap["recommendation"] is None and snap["source"] == "none"
    assert snap["watching"] is False


def test_session_low_confidence_refuses(tmp_path):
    import numpy as np
    s = _session()
    s.match = MatchMemory(tmp_path)
    s.process_frame(np.zeros((360, 640, 3), np.uint8), save_debug=False)  # empty screen
    snap = s.snapshot()
    assert snap["state"] is None and snap["recommendation"] is None  # stays on the start screen
    assert snap["status"]["level"] == "warn"


def test_post_match_analysis_detects_mistake(tmp_path):
    m = MatchMemory(tmp_path)
    m.decisions.append({
        "id": 0, "round": 2, "dice_left": 4, "burst_ready": True,
        "recommended": {"label": "Взрыв стихии", "type": "elemental_burst", "win_probability": 0.8},
        "options": [{"label": "Взрыв стихии", "win_probability": 0.8}, {"label": "Завершить раунд", "win_probability": 0.55}],
        "actual": {"label": "Завершить раунд", "type": "end_round", "win_probability": 0.55},
    })
    rep = analyse(m, "opponent")
    assert rep["mistakes"] and rep["mistakes"][0]["loss"] == 0.25
    assert any("взрыв" in x["text"] for x in rep["missed_opportunities"])
    md = to_markdown(rep)
    for h in ("MATCH RESULT", "KEY DECISIONS", "GOOD DECISIONS", "MISTAKES", "MISSED OPPORTUNITIES",
              "RESOURCE MANAGEMENT", "REACTION MANAGEMENT", "POSITIONING", "FUTURE IMPROVEMENTS"):
        assert f"## {h}" in md


def test_mock_provider_is_offline():
    p = MockProvider()
    text = p.explain({"recommendation": {"title": "Навык", "why": ["урон"], "future_plan": [], "risk": ["x"]}})
    assert "Навык" in text
