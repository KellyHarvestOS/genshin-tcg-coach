"""Configuration loaded from config.toml (with GCOACH_<KEY> environment overrides)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config.toml"


@dataclass
class Config:
    # capture
    MONITOR: int = 1
    SCREEN_REGION: list[int] = field(default_factory=list)
    SCREENSHOT_INTERVAL: float = 1.0
    CHANGE_THRESHOLD: float = 0.02
    STABLE_FRAMES: int = 2
    # vision
    LAYOUT: str = "assets/layouts/default_16x9.json"
    TEMPLATE_THRESHOLD: float = 0.78
    OCR_ENABLED: bool = True
    OCR_ENGINE: str = "auto"
    TESSERACT_CMD: str = ""
    # coach
    CONFIDENCE_THRESHOLD: float = 0.6
    DRY_RUN: bool = True
    DEBUG_MODE: bool = True
    SAVE_DEBUG_IMAGES: bool = True
    # planner
    PLANNING_DEPTH: int = 3
    BEAM_WIDTH: int = 10
    OPPONENT_WIDTH: int = 4
    LETHAL_DEPTH: int = 5
    ADAPTIVE_DEPTH: bool = True
    TIME_BUDGET_MS: int = 2500
    ROBUST_LAMBDA: float = 0.6
    # ai
    AI_PROVIDER: str = "mock"
    AI_MODEL: str = "claude-opus-5-5"
    AI_EFFORT: str = "low"
    AI_TIMEOUT: float = 30
    LOCAL_URL: str = "http://127.0.0.1:11434"
    LOCAL_MODEL: str = "llama3.1"
    # voice
    TTS_VOICE: str = "edge:ru-RU-SvetlanaNeural"
    TTS_RATE: float = 1.1
    GOOGLE_TTS_API_KEY: str = ""
    # server
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    OPEN_BROWSER: bool = True
    # logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/coach.log"

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    def public(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or CONFIG_FILE
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        flat = {k: v for section in data.values() if isinstance(section, dict) for k, v in section.items()}
        for f in fields(cfg):
            if f.name in flat:
                setattr(cfg, f.name, flat[f.name])
    for f in fields(cfg):
        env = os.environ.get(f"GCOACH_{f.name}")
        if env is None:
            continue
        current = getattr(cfg, f.name)
        if isinstance(current, bool):
            setattr(cfg, f.name, env.lower() in ("1", "true", "yes", "on"))
        elif isinstance(current, int):
            setattr(cfg, f.name, int(env))
        elif isinstance(current, float):
            setattr(cfg, f.name, float(env))
        elif isinstance(current, list):
            setattr(cfg, f.name, [int(x) for x in env.split(",") if x.strip()])
        else:
            setattr(cfg, f.name, env)
    cfg.DRY_RUN = True  # architectural invariant: the coach never acts in the game
    return cfg
