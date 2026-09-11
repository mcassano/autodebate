"""The conversation engine: who speaks when, what they see, and how they answer.

The Engine is UI-agnostic. It renders through a Sink (a protocol implemented by
both the Textual TUI and the plain-stdout CLI), so the loop here never touches
a widget or a print call.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from openai import AsyncOpenAI

from . import prompts
from .config import (
    DEFAULT_MODE,
    DEFAULT_SPEED,
    MAX_CONSEC_PASSES,
    MIN_SWITCH_GAP,
    MODERATOR_MODEL,
    PERSONAS,
    PROVIDERS,
    SPEED_DELAYS,
    SUMMARY_EVERY,
    WINDOW_TURNS,
    Persona,
    key_for,
    load_personas,
    missing_keys,
    parse_spec,
)
from .tools import TOOL_BY_NAME, TOOL_SCHEMAS


@dataclass
class Entry:
    """One spoken turn in the shared transcript."""

    speaker: str
    text: str


class Sink(Protocol):
    """Everything the Engine needs from a UI. All methods are called on the UI's
    own event loop."""

    async def status(self, text: str) -> None: ...
    async def speaker_start(self, persona: Persona) -> None: ...
    async def token(self, text: str) -> None: ...
    async def speaker_end(self, persona: Persona, text: str) -> None: ...
    async def dim(self, text: str) -> None: ...
    async def user_message(self, text: str) -> None: ...
    async def error(self, text: str) -> None: ...


def is_pass(text: str) -> bool:
    t = text.strip().lower().strip("[]. *")
    return t == "pass" or t.startswith("pass ")


def short_error(e: Exception) -> str:
    """Providers sometimes answer errors with whole HTML pages — keep it one line."""
    return " ".join(str(e).split())[:240]


def quiet_asyncgen_noise() -> None:
    """Silence exactly one upstream bug, nothing else.

    openai 3.x streams over httpx2/httpcore2; when an SSE stream is torn down at
    loop shutdown, httpcore2's PoolByteStream async generator fails to close and
    asyncio logs a noisy (but harmless) "error occurred during closing of
    asynchronous generator" traceback after an otherwise clean exit. Swallow
    that specific generator's noise; every other loop error reports normally.
    Call once from each front end, on the running loop.
    """
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        message = context.get("message", "")
        asyncgen = repr(context.get("asyncgen", ""))
        if message.startswith("an error occurred during closing of asynchronous generator") and (
            "httpcore" in asyncgen or "PoolByteStream" in asyncgen
        ):
            return
        if previous:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


@dataclass
class ModeratorDecision:
    nxt: str | None
    nudge: str | None
    switch: tuple[str, str] | None  # (departing seat, arriving from the café)
    found_next: bool = False
    next_raw: str | None = None  # the literal NEXT value, even when invalid


def parse_moderator_reply(
    content: str, seated: set[str], troupe_names: set[str], last_speaker: str | None
) -> ModeratorDecision:
    """Parse the moderator's NEXT:/NUDGE:/SWITCH: reply. Pure — no I/O, so tests
    can hammer it. Anything unrecognized degrades to 'no valid choice'."""
    decision = ModeratorDecision(nxt=None, nudge=None, switch=None)
    for line in content.splitlines():
        upper = line.upper()
        if upper.startswith("NEXT:"):
            decision.found_next = True
            val = line.split(":", 1)[1].strip().strip('."* ')
            decision.next_raw = val
            # NOBODY, the last speaker, and unknown names all mean "no one valid"
            decision.nxt = val if val in seated and val != last_speaker else None
        elif upper.startswith("NUDGE:"):
            val = line.split(":", 1)[1].strip()
            decision.nudge = None if val in ("", "-") else val
        elif upper.startswith("SWITCH:") and "->" in line:
            body = line.split(":", 1)[1]
            dep, arr = (x.strip().strip('."* ') for x in body.split("->", 1))
            if dep in seated and arr in troupe_names and arr not in seated:
                decision.switch = (dep, arr)
    return decision


class TranscriptLog:
    """Appends the conversation to transcripts/debate-<ts>.md as it happens,
    so the record survives closing the app (or a crash)."""

    def __init__(self, personas: tuple[Persona, ...] = PERSONAS) -> None:
        out_dir = Path("transcripts")
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = out_dir / f"debate-{stamp}.md"
        roster = " · ".join(f"{p.name} ({p.model})" for p in personas)
        self._write(f"# Coffee Shop Debate — {datetime.now():%Y-%m-%d %H:%M}\n\n{roster}\n\n---\n")

    def _write(self, text: str) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass  # logging must never break the conversation

    def turn(self, speaker: str, text: str) -> None:
        self._write(f"\n**{speaker}:**\n{text}\n")

    def note(self, text: str) -> None:
        self._write(f"\n*{text}*\n")


class Engine:
    def __init__(
        self,
        sink: Sink,
        max_turns: int | None = None,
        exit_on_cap: bool = False,
        personas: tuple[Persona, ...] = PERSONAS,
        moderator_model: str = MODERATOR_MODEL,
        brief: str | None = None,
        mode: str = DEFAULT_MODE,
        speed: str = DEFAULT_SPEED,
        troupe: tuple[Persona, ...] = (),
        out_tonight: tuple[str, ...] = (),
    ):
        self.sink = sink
        self.personas = personas
        self.by_name = {p.name: p for p in personas}
        self.moderator_model = moderator_model
        self.brief = brief
        self.mode = mode
        self.speed = speed
        self.troupe = troupe
        self.out_tonight = out_tonight
        self.turns_since_switch = 0
        self._last_switch_in: str | None = None
        self._sitrep: tuple[str, str] | None = None  # (arriving, departing)
        self.transcript: list[Entry] = []
        self.user_queue: asyncio.Queue[str] = asyncio.Queue()
        self.running = asyncio.Event()  # set = talking, clear = paused
        self.stop = False
        self.turns = 0
        self.max_turns = max_turns
        self.exit_on_cap = exit_on_cap  # CLI mode: end the process when the table stops
        self.consecutive_passes = 0
        self.tokens = 0
        self.summary = ""
        self.summary_upto = 0  # transcript index covered by the summary
        self.last_speaker: str | None = None
        self.tools_disabled: set[str] = set()  # persona names whose models reject tools
        self.log = TranscriptLog(personas)
        self._clients: dict[str, AsyncOpenAI] = {}
        self._streamed_any = False  # set per attempt: did any text reach the UI?

    # -- providers ------------------------------------------------------------

    def _client(self, provider_key: str) -> AsyncOpenAI:
        """One cached OpenAI-compatible client per provider in use."""
        if provider_key not in self._clients:
            p = PROVIDERS[provider_key]
            key = key_for(p.key_env) if p.key_env else "not-needed"
            self._clients[provider_key] = AsyncOpenAI(base_url=p.base_url, api_key=key)
        return self._clients[provider_key]

    # -- user input ---------------------------------------------------------

    def submit_user(self, text: str) -> None:
        if text.startswith("/"):
            self.user_queue.put_nowait(("cmd", text))
        else:
            self.user_queue.put_nowait(text)
        self.running.set()

    # -- main loop ----------------------------------------------------------

    async def run(self) -> None:
        if "brave_search" not in TOOL_BY_NAME:
            await self.sink.dim(
                "· no BRAVE_SEARCH_API_KEY — the table debates without live web search"
            )
        if self.out_tonight:
            await self.sink.dim(
                f"· out tonight (provider unreachable): {', '.join(self.out_tonight)}"
            )
        while not self.stop:
            await self.running.wait()
            if self.stop:
                break

            while True:  # queued user messages are spoken first, at the turn boundary
                try:
                    item = self.user_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, tuple):  # a /command, not table speech
                    await self._handle_command(item[1])
                    continue
                self._add("You", item)
                await self.sink.user_message(item)

            if not self.transcript:
                await self._pause("paused — the table waits for you")
                continue

            if self.max_turns is not None and self.turns >= self.max_turns:
                await self.sink.dim(f"☕ reached the {self.max_turns}-turn cap — pausing")
                await self._pause("paused — turn cap reached")
                if self.exit_on_cap:
                    self.stop = True
                continue

            try:
                await self._maybe_summarize()
            except Exception:
                pass  # a stale summary never stops the table

            await self.sink.status("the moderator glances around the table…")
            try:
                decision = await self._moderator_decide()
            except Exception as e:
                await self.sink.error(f"moderator error: {short_error(e)}")
                await self._pause("paused — something went wrong")
                continue

            if decision.switch and self._apply_switch(*decision.switch):
                dep, arr = decision.switch
                await self.sink.dim(f"☕ {dep} settles their tab · {arr} slides into the booth")
                self.log.note(f"{dep} settles their tab · {arr} slides into the booth")
                if decision.nxt is None:
                    decision.nxt = arr  # the newcomer naturally gets the floor

            nxt, nudge = decision.nxt, decision.nudge
            if (
                nxt is None
                and decision.next_raw
                and decision.next_raw in {p.name for p in self.troupe}
                and decision.next_raw not in self.by_name
            ):
                # a NEXT naming someone in the café is an implicit switch request
                departing = self._least_recent_speaker()
                if self._apply_switch(departing, decision.next_raw):
                    arr = decision.next_raw
                    await self.sink.dim(
                        f"☕ {departing} settles their tab · {arr} slides into the booth"
                    )
                    self.log.note(f"{departing} settles their tab · {arr} slides into the booth")
                    nxt = arr
            if nxt is None and self.transcript and self.transcript[-1].speaker == "You":
                # the table always answers the human — NOBODY is only a valid
                # call once the personas are actually talking among themselves
                nxt = self._least_recent_speaker()
            if nxt is None:
                await self._silence()
                continue

            persona = self.by_name[nxt]
            if nudge:
                await self.sink.dim(f"☕ moderator → {nxt}: {nudge}")

            await self.sink.speaker_start(persona)
            await self.sink.status(f"{nxt} is thinking…")
            try:
                text = (await self._persona_turn(persona, nudge)).strip()
            except Exception as e:
                await self.sink.speaker_end(persona, "")
                await self.sink.error(f"{nxt} hit an error: {short_error(e)}")
                await self._pause("paused — something went wrong")
                continue

            self.turns += 1
            self.turns_since_switch += 1
            self.last_speaker = nxt

            if not text or is_pass(text):
                self.consecutive_passes += 1
                await self.sink.speaker_end(persona, "")
                await self.sink.dim(f"· {nxt} sips their coffee and lets the thought sit")
                if self.consecutive_passes >= MAX_CONSEC_PASSES:
                    await self._silence()
                continue

            self.consecutive_passes = 0
            self._add(nxt, text)
            await self.sink.speaker_end(persona, text)
            await self._paced_delay()

    async def _pause(self, status: str) -> None:
        self.running.clear()
        await self.sink.status(status)

    async def _paced_delay(self) -> None:
        """The beat between turns, set by --speed. Skipped the moment the human
        queues something, the table is paused, or we're shutting down."""
        remaining = SPEED_DELAYS.get(self.speed, 0.0)
        while remaining > 0 and not self.stop and self.running.is_set() and self.user_queue.empty():
            step = min(0.25, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _silence(self) -> None:
        await self.sink.dim("☕ the table falls into comfortable silence")
        await self._pause("paused — the table waits for you")
        if self.exit_on_cap:
            self.stop = True

    def _add(self, speaker: str, text: str) -> None:
        self.transcript.append(Entry(speaker, text))
        self.log.turn(speaker, text)

    # -- moderator ----------------------------------------------------------

    async def _moderator_decide(self) -> ModeratorDecision:
        recent = "\n".join(f"{e.speaker}: {e.text}" for e in self.transcript[-12:])
        prompt = prompts.moderator_prompt(
            recent, self.last_speaker, self.personas, self.brief, self.mode, self.troupe
        )
        provider, model = parse_spec(self.moderator_model)
        r = await self._client(provider).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,  # reasoning models burn tokens before the NEXT:/NUDGE: lines
            temperature=0.4,
        )
        self._count_usage(r.usage)
        content = (r.choices[0].message.content or "").strip()
        decision = parse_moderator_reply(
            content, set(self.by_name), {p.name for p in self.troupe}, self.last_speaker
        )
        if not decision.found_next:  # unparseable reply: fall back to fair rotation
            decision.nxt = self._least_recent_speaker()
        return decision

    def _apply_switch(self, departing: str, arriving: str, force: bool = False) -> bool:
        """Swap one seat for someone in the café. Returns False on anything fishy:
        unknown names, already seated, too soon since the last switch (unless a
        human forces it with /seat), or evicting the newest arrival."""
        if not self.troupe or departing not in self.by_name:
            return False
        candidate = next((p for p in self.troupe if p.name == arriving), None)
        if candidate is None or arriving in self.by_name:
            return False
        # the first switch may happen right away — the right cast for the
        # opening topic shouldn't have to wait; after that, the full gap
        # keeps the table stable
        gap = MIN_SWITCH_GAP if self._last_switch_in else 0
        if not force and self.turns_since_switch < gap:
            return False
        if departing == self._last_switch_in:
            return False
        idx = next(i for i, p in enumerate(self.personas) if p.name == departing)
        self.personas = tuple(candidate if i == idx else p for i, p in enumerate(self.personas))
        self.by_name = {p.name: p for p in self.personas}
        self._last_switch_in = arriving
        self.turns_since_switch = 0
        self._sitrep = (arriving, departing)
        return True

    # -- user commands (/seat, /cast) -------------------------------------------

    async def _handle_command(self, raw: str) -> None:
        parts = raw[1:].split()
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        if cmd == "seat":
            await self._cmd_seat(args)
        elif cmd == "cast":
            await self._cmd_cast(args)
        else:
            await self.sink.dim(
                f"· unknown command /{cmd} — try /seat Name [for Seat] or /cast pack"
            )

    async def _cmd_seat(self, args: list[str]) -> None:
        if not self.troupe:
            await self.sink.dim("· this table has a fixed cast — /seat works with the troupe")
            return
        if not args:
            waiting = ", ".join(p.name for p in self.troupe if p.name not in self.by_name)
            await self.sink.dim(f"· in the café tonight: {waiting or 'everyone is seated'}")
            return
        match = next((p for p in self.troupe if p.name.lower() == args[0].lower()), None)
        if match is None:
            await self.sink.dim(f"· no one called {args[0]} here tonight")
            return
        if match.name in self.by_name:
            await self.sink.dim(f"· {match.name} is already at the table")
            return
        if len(args) >= 3 and args[1].lower() == "for":
            departing = next(
                (p.name for p in self.personas if p.name.lower() == args[2].lower()), None
            )
            if departing is None:
                await self.sink.dim(f"· no seat called {args[2]} at the table")
                return
        else:
            departing = self._least_recent_speaker()
        if self._apply_switch(departing, match.name, force=True):
            await self.sink.dim(
                f"☕ {departing} settles their tab · {match.name} slides into the booth"
            )
            self.log.note(f"{departing} settles their tab · {match.name} slides into the booth")

    async def _cmd_cast(self, args: list[str]) -> None:
        if not args:
            await self.sink.dim("· /cast needs a pack name — try /cast traders")
            return
        try:
            lineup = load_personas(args[0])
        except SystemExit as e:
            await self.sink.dim(f"· {e}")
            return
        missing = missing_keys(lineup.personas, lineup.moderator or MODERATOR_MODEL)
        if missing:
            await self.sink.dim(f"· can't seat that cast — missing keys: {', '.join(missing)}")
            return
        self.personas = lineup.personas
        self.by_name = {p.name: p for p in self.personas}
        self.troupe = lineup.troupe
        if lineup.moderator:
            self.moderator_model = lineup.moderator
        if lineup.brief:
            self.brief = lineup.brief
        if lineup.mode:
            self.mode = lineup.mode
        if lineup.speed:
            self.speed = lineup.speed
        self.consecutive_passes = 0
        self.turns_since_switch = 0
        self._last_switch_in = None
        names = " · ".join(p.name for p in self.personas)
        await self.sink.dim(f"☕ the evening's cast changes — {names} take the table")
        self.log.note(f"the cast changes: {names} take the table")

    def _least_recent_speaker(self) -> str:
        last_seen = {p.name: -1 for p in self.personas}
        for i, e in enumerate(self.transcript):
            if e.speaker in last_seen:
                last_seen[e.speaker] = i
        candidates = [n for n in last_seen if n != self.last_speaker]
        return min(candidates, key=lambda n: last_seen[n])

    # -- persona turn ---------------------------------------------------------

    async def _persona_turn(self, persona: Persona, nudge: str | None) -> str:
        tools_ok = persona.name not in self.tools_disabled
        for attempt in (1, 2):
            try:
                return await self._stream_turn(persona, nudge, tools=tools_ok)
            except Exception as e:
                low = str(e).lower()
                if tools_ok and ("tool" in low or "function" in low):
                    # This model can't do tool calling here: remember, retry without.
                    tools_ok = False
                    self.tools_disabled.add(persona.name)
                    note = f"· {persona.name}'s model can't search here — continuing without it"
                    await self.sink.dim(note)
                    continue
                if attempt == 1 and not self._streamed_any:
                    await asyncio.sleep(4)  # transient: one retry before any text was shown
                    continue
                raise
        return ""

    async def _stream_turn(self, persona: Persona, nudge: str | None, tools: bool) -> str:
        """Stream one turn, looping through tool calls until a spoken answer arrives."""
        messages = self._build_messages(persona, nudge)
        provider, model = parse_spec(persona.model)
        client = self._client(provider)
        self._streamed_any = False
        all_text: list[str] = []

        for _ in range(3):  # at most two searches, then a final spoken answer
            kwargs: dict = dict(
                model=model,
                messages=messages,
                stream=True,
                temperature=0.8,
                max_tokens=4096,  # headroom for reasoning models: speech stays short
                stream_options={"include_usage": True},
            )
            if tools:
                kwargs["tools"] = TOOL_SCHEMAS
                kwargs["tool_choice"] = "auto"

            stream = await client.chat.completions.create(**kwargs)
            tool_calls: dict[int, dict] = {}
            finish = None
            round_text: list[str] = []

            try:
                async for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        self._count_usage(usage)
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    if delta and delta.content:
                        self._streamed_any = True
                        round_text.append(delta.content)
                        await self.sink.token(delta.content)
                    for tc in (delta.tool_calls if delta else None) or []:
                        slot = tool_calls.setdefault(
                            tc.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            slot["id"] += tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
                    if choice.finish_reason:
                        finish = choice.finish_reason
            finally:
                await stream.close()  # don't leave an async generator for the GC

            all_text.extend(round_text)

            if finish != "tool_calls" or not tool_calls:
                break

            call = tool_calls[sorted(tool_calls)[0]]
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await self._run_tool(call["name"] or "brave_search", args, persona)
            call_id = call["id"] or f"call_{len(messages)}"
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(round_text) or None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call["name"] or "brave_search",
                                "arguments": call["arguments"] or "{}",
                            },
                        }
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

        return "".join(all_text)

    async def _run_tool(self, name: str, args: dict, persona: Persona) -> str:
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        await self.sink.dim(f"{tool.icon} {persona.name} {tool.describe(args)}")
        await self.sink.status(f"{persona.name} is using {name}…")
        self.log.note(f"{persona.name} used {name} — {tool.describe(args)}")
        try:
            return await tool.run(args)
        except Exception as e:
            # the model hears the failure — and so does the human eavesdropping
            await self.sink.dim(f"{tool.icon} {name} failed: {short_error(e)}")
            return f"{name} failed: {e}"

    def _build_messages(self, persona: Persona, nudge: str | None) -> list[dict]:
        """Shared transcript → this persona's chat context.

        The persona's own past lines become `assistant` messages; everyone else's
        become `user` lines prefixed "Name: " (consecutive ones merged, since some
        providers require role alternation).
        """
        system = prompts.persona_prompt(persona, self.personas, self.mode)
        msgs: list[dict] = [{"role": "system", "content": system}]
        start = max(self.summary_upto, len(self.transcript) - WINDOW_TURNS)
        if self.summary:
            msgs.append(
                {
                    "role": "user",
                    "content": f"[Earlier at this table, summarized: {self.summary}]",
                }
            )
        for e in self.transcript[start:]:
            if e.speaker == persona.name:
                role, content = "assistant", e.text
            else:
                role, content = "user", f"{e.speaker}: {e.text}"
            if role == "user" and msgs[-1]["role"] == "user":
                msgs[-1]["content"] += "\n\n" + content
            else:
                msgs.append({"role": role, "content": content})
        if self._sitrep and persona.name == self._sitrep[0]:
            departing = self._sitrep[1]
            missed = self.summary or " · ".join(
                f"{e.speaker}: {e.text[:120]}" for e in self.transcript[-3:]
            )
            sitrep = (
                f"You're joining mid-conversation, taking {departing}'s seat. "
                f"What you missed: {missed}. Bring your own angle."
            )
            nudge = sitrep + (f" Also: {nudge}" if nudge else "")
            self._sitrep = None
        if nudge:
            msgs.append(
                {
                    "role": "user",
                    "content": f"(The moderator quietly suggests: {nudge})",
                }
            )
        return msgs

    # -- rolling summary ------------------------------------------------------

    async def _maybe_summarize(self) -> None:
        """Keep long sessions affordable: turns older than the verbatim window are
        folded into a one-paragraph brief by the cheap moderator model."""
        if len(self.transcript) - self.summary_upto <= WINDOW_TURNS + SUMMARY_EVERY:
            return
        cutoff = len(self.transcript) - WINDOW_TURNS
        older = self.transcript[self.summary_upto : cutoff]
        convo = "\n".join(f"{e.speaker}: {e.text}" for e in older)
        provider, model = parse_spec(self.moderator_model)
        r = await self._client(provider).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompts.summary_prompt(self.summary, convo)}],
            max_tokens=1200,  # headroom for reasoning models
            temperature=0.2,
        )
        self._count_usage(r.usage)
        summary = (r.choices[0].message.content or "").strip()
        if summary:
            self.summary = summary
            self.summary_upto = cutoff

    def _count_usage(self, usage) -> None:
        total = getattr(usage, "total_tokens", None) if usage else None
        if total:
            self.tokens += total

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
