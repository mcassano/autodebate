"""The Textual front end: a scrolling transcript you eavesdrop on, plus an input
box to queue things to say to the table.

The widgets never call the model — they implement the Sink protocol and the
Engine drives them.
"""

from __future__ import annotations

import asyncio

from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from .config import PERSONAS, Persona
from .engine import Engine


class ChatView(VerticalScroll):
    def __init__(self) -> None:
        super().__init__(id="chat", can_focus=True)


class Speech(Static):
    """One speaker turn: colored border + bold name header + the spoken text."""

    def __init__(self, name: str, color: str) -> None:
        super().__init__("")
        self.speaker = name
        self.color = color
        self.text = ""
        self.add_class("speech")
        self.styles.border_left = ("heavy", color)

    def set_text(self, text: str) -> None:
        self.text = text
        self.update(f"[bold {self.color}]{self.speaker}[/]\n{escape(text)}")


class TuiSink:
    """Engine callbacks → widgets. All methods run on the app's event loop."""

    def __init__(self, app: DebateApp) -> None:
        self.app = app

    async def status(self, text: str) -> None:
        self.app.set_status(text)

    async def speaker_start(self, persona: Persona) -> None:
        self.app.begin_speech(persona)

    async def token(self, text: str) -> None:
        self.app.append_token(text)

    async def speaker_end(self, persona: Persona, text: str) -> None:
        self.app.end_speech(text)

    async def dim(self, text: str) -> None:
        self.app.add_line(text, "dimline")

    async def user_message(self, text: str) -> None:
        self.app.add_speech("You", "green", text)

    async def error(self, text: str) -> None:
        self.app.add_line(f"⚠ {text}", "errorline")


class DebateApp(App):
    TITLE = "coffee shop — " + " · ".join(p.name for p in PERSONAS)

    CSS = """
    #chat { height: 1fr; padding: 1 2 0 2; }
    #status { height: 1; padding: 0 2; background: $surface; color: $text-muted; }
    #input { margin: 0 2 1 2; }
    .speech { margin-bottom: 1; padding: 0 1 0 1; }
    .dimline { color: $text-muted; }
    .errorline { color: $error; text-style: bold; }
    """

    BINDINGS = [
        Binding("ctrl+p", "toggle_pause", "Pause/Resume"),
        Binding("ctrl+q", "quit_app", "Quit"),
    ]

    def __init__(self, max_turns: int | None = None, opening: str | None = None):
        super().__init__()
        self._max_turns = max_turns
        self._opening = opening
        self._current: Speech | None = None
        self._status_text = ""
        self.engine: Engine | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChatView()
        yield Static("", id="status")
        yield Input(
            placeholder="Say something to the table…  (enter to queue · ctrl+p to pause)",
            id="input",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.chat = self.query_one(ChatView)
        self.engine = Engine(TuiSink(self), max_turns=self._max_turns)
        self._engine_task = asyncio.create_task(self.engine.run())
        self.add_line(
            "☕ Three minds settle in with their coffee. The table is quiet — "
            "say something to begin.",
            "dimline",
        )
        self.set_status("paused — the table waits for you")
        self.query_one("#input", Input).focus()
        if self._opening:
            self.engine.submit_user(self._opening)

    # -- rendering ---------------------------------------------------------

    def set_status(self, text: str) -> None:
        self._status_text = text
        self._render_status()

    def _render_status(self) -> None:
        if not self.engine:
            return
        queued = self.engine.user_queue.qsize()
        parts = [self._status_text, f"{self.engine.tokens:,} tokens"]
        if queued:
            parts.append(f"{queued} queued")
        self.query_one("#status", Static).update("   ·   ".join(parts))

    def begin_speech(self, persona: Persona) -> None:
        w = Speech(persona.name, persona.color)
        self._current = w
        self.chat.mount(w)
        self._maybe_scroll()

    def append_token(self, text: str) -> None:
        if self._current is None:
            return
        self._current.set_text(self._current.text + text)
        self._maybe_scroll()
        self._render_status()  # keep the token meter moving

    def end_speech(self, text: str) -> None:
        w, self._current = self._current, None
        if w is None:
            return
        if text.strip():
            w.set_text(text.strip())
        elif not w.text.strip():
            w.remove()  # a pass or an error leaves no empty block behind
        self._maybe_scroll()

    def add_speech(self, name: str, color: str, text: str) -> None:
        w = Speech(name, color)
        w.set_text(text)
        self.chat.mount(w)
        self._maybe_scroll()

    def add_line(self, text: str, cls: str) -> None:
        w = Static(f"[dim]{escape(text)}[/]" if cls == "dimline" else escape(text))
        w.add_class(cls)
        self.chat.mount(w)
        self._maybe_scroll()

    def _maybe_scroll(self) -> None:
        if self.chat.scroll_y >= self.chat.max_scroll_y - 3:
            self.chat.scroll_end(animate=False)

    # -- input & actions -----------------------------------------------------

    @on(Input.Submitted)
    def on_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        event.input.value = ""
        if not msg or not self.engine:
            return
        self.engine.submit_user(msg)
        self._render_status()

    def action_toggle_pause(self) -> None:
        if not self.engine:
            return
        if self.engine.running.is_set():
            self.engine.running.clear()
            self.set_status("paused — the table waits for you")
        else:
            self.engine.running.set()
            self.set_status("the conversation stirs…")

    def action_quit_app(self) -> None:
        if self.engine:
            self.engine.stop = True
            self.engine.running.set()
        self.exit()
