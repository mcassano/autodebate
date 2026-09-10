"""The web front end: one shared table, broadcast to every browser in the café.

Deploy anywhere that runs Python (Railway, Fly, a VPS):

    autodebate-web            # serves on $PORT (default 8000)

Optional env:
    AUTODEBATE_TOPIC          # opening message so a public café isn't silent
                              # before the first visitor speaks

Cost guard: the table only talks while at least one browser is connected —
when the café empties, the conversation pauses itself.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import MODERATOR_MODEL, PERSONAS, Lineup, Persona, check_keys, load_personas
from .engine import Engine

STATIC = Path(__file__).parent / "static"


class Hub:
    """The set of browsers currently in the café."""

    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    async def broadcast(self, event: dict) -> None:
        for ws in list(self.sockets):
            try:
                await ws.send_json(event)
            except Exception:
                self.sockets.discard(ws)


class WebSink:
    """Engine callbacks → JSON events broadcast to every browser."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.engine: Engine  # set right after construction

    async def status(self, text: str) -> None:
        await self.hub.broadcast(
            {
                "type": "status",
                "text": text,
                "tokens": self.engine.tokens,
                "queued": self.engine.user_queue.qsize(),
                "paused": not self.engine.running.is_set(),
            }
        )

    async def speaker_start(self, persona: Persona) -> None:
        await self.hub.broadcast(
            {
                "type": "speaker_start",
                "name": persona.name,
                "color": persona.color,
            }
        )

    async def token(self, text: str) -> None:
        await self.hub.broadcast({"type": "token", "text": text})

    async def speaker_end(self, persona: Persona, text: str) -> None:
        await self.hub.broadcast({"type": "speaker_end", "name": persona.name, "text": text})

    async def dim(self, text: str) -> None:
        await self.hub.broadcast({"type": "dim", "text": text})

    async def user_message(self, text: str) -> None:
        await self.hub.broadcast({"type": "user", "text": text})

    async def error(self, text: str) -> None:
        await self.hub.broadcast({"type": "error", "text": text})


def create_app(lineup: Lineup | None = None) -> FastAPI:
    if lineup is None and os.environ.get("AUTODEBATE_PERSONAS"):
        lineup = load_personas(os.environ["AUTODEBATE_PERSONAS"])
    lineup = lineup or Lineup(PERSONAS)
    check_keys(lineup.personas, lineup.moderator or MODERATOR_MODEL)
    hub = Hub()
    sink = WebSink(hub)
    engine = Engine(
        sink,
        personas=lineup.personas,
        moderator_model=lineup.moderator or MODERATOR_MODEL,
        brief=lineup.brief,
    )
    sink.engine = engine

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(engine.run())
        topic = os.environ.get("AUTODEBATE_TOPIC")
        if topic:  # a public café doesn't have to open its doors in silence
            engine.submit_user(topic)
        yield
        engine.stop = True
        engine.running.set()
        await engine.close()
        task.cancel()

    app = FastAPI(title="autodebate café", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/transcript")
    async def transcript():
        try:
            return PlainTextResponse(engine.log.path.read_text(encoding="utf-8"))
        except OSError:
            return PlainTextResponse("no transcript yet", status_code=404)

    def _init_event() -> dict:
        return {
            "type": "init",
            "transcript": [{"speaker": e.speaker, "text": e.text} for e in engine.transcript],
            "tokens": engine.tokens,
            "paused": not engine.running.is_set(),
            "personas": [
                {"name": p.name, "color": p.color, "model": p.model, "style": p.style}
                for p in lineup.personas
            ],
        }

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.sockets.add(websocket)
        await websocket.send_json(_init_event())
        engine.running.set()  # someone is listening — the table may talk
        try:
            while True:
                msg = await websocket.receive_json()
                kind = msg.get("type")
                if kind == "speak":
                    text = str(msg.get("text", "")).strip()
                    if text:
                        engine.submit_user(text)
                elif kind == "pause":
                    if engine.running.is_set():
                        engine.running.clear()
                    else:
                        engine.running.set()
                    await sink.status(
                        "paused — the table waits for you"
                        if not engine.running.is_set()
                        else "the conversation stirs…"
                    )
        except WebSocketDisconnect:
            pass
        finally:
            hub.sockets.discard(websocket)
            if not hub.sockets:  # the café emptied out mid-evening
                engine.running.clear()

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
