"""Logging with an extra TRACE level (below DEBUG) for planning trees."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(logging.WARNING, "WARN")

LEVELS = {"ERROR": logging.ERROR, "WARN": logging.WARNING, "WARNING": logging.WARNING,
          "INFO": logging.INFO, "DEBUG": logging.DEBUG, "TRACE": TRACE}


def trace(logger: logging.Logger, msg: str, *args) -> None:
    if logger.isEnabledFor(TRACE):
        logger.log(TRACE, msg, *args)


class MemoryHandler(logging.Handler):
    """Keeps the last N records for the debug panel in the UI."""

    def __init__(self, capacity: int = 300):
        super().__init__()
        self.capacity = capacity
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append({"time": record.created, "level": record.levelname,
                             "name": record.name, "message": record.getMessage()})
        if len(self.records) > self.capacity:
            del self.records[: len(self.records) - self.capacity]


memory_handler = MemoryHandler()


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    root = logging.getLogger("gcoach")
    root.setLevel(LEVELS.get(level.upper(), logging.INFO))
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    memory_handler.setLevel(logging.DEBUG)
    root.addHandler(memory_handler)
    root.propagate = False
