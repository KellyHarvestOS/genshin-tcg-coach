"""Local web server for the coach UI (FastAPI + WebSocket). Binds to 127.0.0.1 by default."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..coach.session import CoachSession
from ..screen_capture.capture import ScreenCapture
from ..voice.tts import TTSError

log = logging.getLogger("gcoach.server")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def create_app(session: CoachSession) -> FastAPI:
    app = FastAPI(title="Card Coach", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_cache(request, call_next):
        # the desktop webview otherwise keeps serving stale CSS/JS
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    clients: set[WebSocket] = set()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    async def broadcast(snapshot: dict) -> None:
        text = _dumps({"type": "snapshot", "data": snapshot})
        for ws in list(clients):
            try:
                await ws.send_text(text)
            except Exception:
                clients.discard(ws)

    def on_change(snapshot: dict) -> None:
        loop = loop_holder.get("loop")
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast(snapshot), loop)

    session.listeners.append(on_change)

    @app.on_event("startup")
    async def _startup() -> None:
        loop_holder["loop"] = asyncio.get_running_loop()

    async def run(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    # ---------------------------------------------------------------------------------
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/snapshot")
    async def snapshot() -> Response:
        return Response(_dumps(session.snapshot()), media_type="application/json")

    @app.post("/api/demo/{name}")
    async def demo(name: str) -> dict:
        if name not in session.snapshot()["scenarios"]:
            raise HTTPException(404, "unknown scenario")
        await run(session.load_demo, name)
        return {"ok": True}

    @app.post("/api/reset")
    async def reset() -> dict:
        await run(session.reset)
        return {"ok": True}

    @app.post("/api/simulate")
    async def simulate() -> dict:
        await run(session.simulate_recommended)
        return {"ok": True}

    @app.post("/api/capture")
    async def capture() -> dict:
        await run(session.capture_once)
        return {"ok": True}

    @app.post("/api/screenshot")
    async def screenshot(payload: dict) -> dict:
        data = payload.get("image", "")
        if "," in data:
            data = data.split(",", 1)[1]
        try:
            raw = base64.b64decode(data)
        except Exception as exc:
            raise HTTPException(400, "bad image") from exc
        await run(session.process_image_bytes, raw)
        return {"ok": True}

    @app.post("/api/watch")
    async def watch(payload: dict) -> dict:
        if payload.get("on"):
            await run(session.start_watch)
        else:
            await run(session.stop_watch)
        return {"ok": True, "watching": session.watching}

    @app.post("/api/state")
    async def set_state(payload: dict) -> dict:
        try:
            await run(session.set_state_json, json.dumps(payload.get("state", payload), ensure_ascii=False))
        except Exception as exc:
            raise HTTPException(400, f"invalid state: {exc}") from exc
        return {"ok": True}

    @app.post("/api/setup")
    async def setup(payload: dict) -> dict:
        await run(session.set_setup, payload)
        return {"ok": True}

    @app.post("/api/turn")
    async def turn(payload: dict) -> dict:
        side = payload.get("side")
        if side not in ("player", "opponent"):
            raise HTTPException(400, "side must be player/opponent")
        await run(session.set_turn, side)
        return {"ok": True}

    @app.post("/api/explain")
    async def explain() -> dict:
        text = await run(session.explain_with_ai)
        return {"text": text}

    @app.post("/api/match/end")
    async def end_match(payload: dict) -> Response:
        report = await run(session.end_match, payload.get("result"), bool(payload.get("ai")))
        return Response(_dumps(report), media_type="application/json")

    @app.get("/api/tts")
    async def tts(text: str) -> Response:
        try:
            audio = await run(session.voice.synthesize, text)
        except TTSError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/voice")
    async def voice_get() -> dict:
        return session.voice.public()

    @app.post("/api/voice")
    async def voice_set(payload: dict) -> dict:
        try:
            result = await run(session.voice.update, payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"invalid voice settings: {exc}") from exc
        await run(session._changed)
        return result

    @app.get("/api/monitors")
    async def monitors() -> JSONResponse:
        try:
            return JSONResponse(ScreenCapture.monitors())
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/kb/characters")
    async def kb_characters() -> JSONResponse:
        chars = sorted(session.kb.characters.values(), key=lambda c: (not c.available, c.name))
        return JSONResponse([{"id": c.id, "name": c.name, "element": c.element.value, "hp": c.hp,
                              "energy": c.energy, "available": c.available} for c in chars])

    @app.get("/api/kb/cards")
    async def kb_cards() -> JSONResponse:
        cards = sorted(session.kb.cards.values(), key=lambda c: (not c.modelled, c.name))
        return JSONResponse([{"id": c.id, "name": c.name, "type": c.type, "subtype": c.subtype,
                              "cost": c.cost, "modelled": c.modelled, "text": c.text[:200]}
                             for c in cards if c.available])

    @app.get("/debug/{name}")
    async def debug_image(name: str) -> FileResponse:
        if name not in ("latest.jpg", "latest_annotated.jpg"):
            raise HTTPException(404)
        path = session.debug_dir / name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_text(_dumps({"type": "snapshot", "data": session.snapshot()}))
            while True:
                await ws.receive_text()  # keep-alive pings from the client
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
