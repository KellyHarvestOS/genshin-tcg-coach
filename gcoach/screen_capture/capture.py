"""Screen capture (read-only).

Only *reads* pixels from the screen with `mss`. The project deliberately has no
code that sends input to, or reads memory of, the game process.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("gcoach.capture")


class ScreenCapture:
    def __init__(self, monitor: int = 1, region: list[int] | None = None, debug_dir: Path | None = None):
        self.monitor = monitor
        self.region = list(region or [])
        self.debug_dir = debug_dir

    # ------------------------------------------------------------------------------------
    @staticmethod
    def monitors() -> list[dict]:
        import mss

        with mss.mss() as sct:
            return [{"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
                    for i, m in enumerate(sct.monitors) if i > 0]

    def grab(self) -> np.ndarray:
        """Return a BGR screenshot of the configured monitor / region."""
        import mss

        with mss.mss() as sct:
            mons = sct.monitors
            idx = self.monitor if 0 < self.monitor < len(mons) else 1
            mon = mons[idx]
            if len(self.region) == 4:
                left, top, width, height = self.region
                box = {"left": mon["left"] + left, "top": mon["top"] + top, "width": width, "height": height}
            else:
                box = mon
            shot = np.asarray(sct.grab(box))  # BGRA
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    def grab_game(self) -> np.ndarray | None:
        """Screenshot of the Genshin Impact window's client area, or None if the game is not
        the active window (so other apps on the screen are never captured or analysed)."""
        rect = game_window_rect()
        if rect is None:
            return None
        left, top, width, height = rect
        import mss

        with mss.mss() as sct:
            shot = np.asarray(sct.grab({"left": left, "top": top, "width": width, "height": height}))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    def save_debug(self, img: np.ndarray, name: str = "frame") -> Path | None:
        """Keep only the latest analysed frame (no history of the player's screen)."""
        if self.debug_dir is None:
            return None
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        latest = self.debug_dir / "latest.jpg"
        cv2.imwrite(str(latest), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return latest


GAME_TITLES = ("Genshin Impact", "原神", "YuanShen")


def game_window_rect() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of the game's client area in screen pixels when the game is the
    foreground window. Read-only window queries (title, size); nothing is sent to the game."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels on scaled displays
    except (OSError, AttributeError):
        pass
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    if buf.value.strip() not in GAME_TITLES:
        return None
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)) or rc.right < 320 or rc.bottom < 200:
        return None  # minimised
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rc.right, rc.bottom


def detect_game_area(img: np.ndarray, dark_threshold: int = 12) -> tuple[int, int, int, int]:
    """Find the game viewport inside a screenshot by trimming uniform dark borders (letterbox).

    Returns (x, y, w, h). Falls back to the whole image.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    rows = np.where(gray.max(axis=1) > dark_threshold)[0]
    cols = np.where(gray.max(axis=0) > dark_threshold)[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0, 0, img.shape[1], img.shape[0]
    x, y = int(cols[0]), int(rows[0])
    w, h = int(cols[-1] - x + 1), int(rows[-1] - y + 1)
    if w < img.shape[1] * 0.4 or h < img.shape[0] * 0.4:
        return 0, 0, img.shape[1], img.shape[0]
    return x, y, w, h


def frame_difference(a: np.ndarray | None, b: np.ndarray, size: int = 160) -> float:
    """Fraction of noticeably changed pixels between two frames (cheap change detection)."""
    if a is None or a.shape != b.shape:
        return 1.0
    sa = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (size, size * a.shape[0] // max(1, a.shape[1])))
    sb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (size, size * b.shape[0] // max(1, b.shape[1])))
    return float((cv2.absdiff(sa, sb) > 18).mean())
