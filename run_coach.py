"""Start the Card Coach as a desktop application.

    python run_coach.py              # desktop window (default)
    python run_coach.py --browser    # open in the web browser instead
    python run_coach.py --demo lethal

The coach only reads the screen and gives advice. You play the game yourself.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from gcoach.coach.session import CoachSession
from gcoach.config import load_config
from gcoach.logging_setup import setup_logging
from gcoach.server.app import create_app


class WindowApi:
    """Exposed to the page as window.pywebview.api (window chrome only - nothing touches the game)."""

    def __init__(self) -> None:
        self._window = None  # private: pywebview must not introspect the native window

    def set_on_top(self, value: bool) -> bool:
        if self._window is not None:
            self._window.on_top = bool(value)
        return bool(value)

    def toggle_fullscreen(self) -> None:
        if self._window is not None:
            self._window.toggle_fullscreen()


def _wait_ready(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "api/snapshot", timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Card Coach (advisor only)")
    ap.add_argument("--demo", help="load a demo scenario on start (opening / reaction_setup / lethal)")
    ap.add_argument("--browser", action="store_true", help="use the web browser instead of the app window")
    ap.add_argument("--no-browser", action="store_true", help="server only (no window, no browser)")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    cfg = load_config()
    if sys.stderr is None or sys.stdout is None:  # started with pythonw.exe (no console)
        log_path = cfg.path("logs/app_console.txt")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    if args.port:
        cfg.PORT = args.port
    setup_logging(cfg.LOG_LEVEL, cfg.path(cfg.LOG_FILE))
    session = CoachSession(cfg)
    if args.demo:
        session.load_demo(args.demo)
    app = create_app(session)
    url = f"http://{cfg.HOST}:{cfg.PORT}/"

    if args.browser or args.no_browser:
        print(f"\n  Card Coach → {url}\n  Режим советника: приложение ничего не нажимает в игре.\n")
        if not args.no_browser:
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        uvicorn.run(app, host=cfg.HOST, port=cfg.PORT, log_level="warning")
        return

    import webview  # pywebview: native window backed by Edge WebView2

    # spoken advice must play without a click in the window (the player is looking at the game);
    # this variable replaces pywebview's own browser arguments, so they are repeated here
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                          "--disable-features=ElasticOverscroll --autoplay-policy=no-user-gesture-required")
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.HOST, port=cfg.PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, name="coach-server", daemon=True)
    thread.start()
    if not _wait_ready(url):
        raise SystemExit(f"Сервер не запустился на {url} (порт занят? попробуйте --port 8770)")

    api = WindowApi()
    window = webview.create_window(
        "Card Coach", url, js_api=api, width=1480, height=940, min_size=(960, 640),
        background_color="#080a18", text_select=True,
    )
    api._window = window
    icon = cfg.path("assets/icons/app_icon.ico")
    if sys.platform == "win32":
        # own taskbar identity, so Windows shows the app icon instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CardCoach.App")
    webview.start(icon=str(icon) if icon.exists() else None)  # blocks until the window is closed
    session.stop_watch()
    server.should_exit = True
    thread.join(timeout=3)


if __name__ == "__main__":
    main()
