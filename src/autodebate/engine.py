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
    BY_NAME,
    MAX_CONSEC_PASSES,
    MODERATOR_MODEL,
    PERSONAS,
    PROVIDERS,
    SUMMARY_EVERY,
    WINDOW_TURNS,
    Persona,
    key_for,
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


class TranscriptLog:
    """Appends the conversation to transcripts/debate-<ts>.md as it happens,
    so the record survives closing the app (or a crash)."""

    def __init__(self) -> None:
        out_dir = Path("transcripts")
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = out_dir / f"debate-{stamp}.md"
        roster = " · ".join(f"{p.name} ({p.model})" for p in PERSONAS)
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
    def __init__(self, sink: Sink, max_turns: int | None = None, exit_on_cap: bool = False):
        self.sink = sink
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
        self.log = TranscriptLog()
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
        self.user_queue.put_nowait(text)
        self.running.set()

    # -- main loop ----------------------------------------------------------

    async def run(self) -> None:
        if "brave_search" not in TOOL_BY_NAME:
            await self.sink.dim(
                "· no BRAVE_SEARCH_API_KEY — the table debates without live web search"
            )
        while not self.stop:
            await self.running.wait()
            if self.stop:
                break

            while True:  # queued user messages are spoken first, at the turn boundary
                try:
                    msg = self.user_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._add("You", msg)
                await self.sink.user_message(msg)

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
                nxt, nudge = await self._moderator_pick()
            except Exception as e:
                await self.sink.error(f"moderator error: {short_error(e)}")
                await self._pause("paused — something went wrong")
                continue

            if nxt is None:
                await self._silence()
                continue

            persona = BY_NAME[nxt]
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

    async def _pause(self, status: str) -> None:
        self.running.clear()
        await self.sink.status(status)

    async def _silence(self) -> None:
        await self.sink.dim("☕ the table falls into comfortable silence")
        await self._pause("paused — the table waits for you")
        if self.exit_on_cap:
            self.stop = True

    def _add(self, speaker: str, text: str) -> None:
        self.transcript.append(Entry(speaker, text))
        self.log.turn(speaker, text)

    # -- moderator ----------------------------------------------------------

    async def _moderator_pick(self) -> tuple[str | None, str | None]:
        """→ (next speaker or None for silence, optional one-line nudge)."""
        recent = "\n".join(f"{e.speaker}: {e.text}" for e in self.transcript[-12:])
        prompt = prompts.moderator_prompt(recent, self.last_speaker)
        provider, model = parse_spec(MODERATOR_MODEL)
        r = await self._client(provider).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,  # reasoning models burn tokens before the NEXT:/NUDGE: lines
            temperature=0.4,
        )
        self._count_usage(r.usage)
        content = (r.choices[0].message.content or "").strip()

        names = BY_NAME.keys()
        nxt: str | None = None
        nudge: str | None = None
        found_next = False
        for line in content.splitlines():
            upper = line.upper()
            if upper.startswith("NEXT:"):
                found_next = True
                val = line.split(":", 1)[1].strip().strip('."* ')
                # NOBODY, the last speaker, and unknown names all mean "no one valid"
                nxt = val if val in names and val != self.last_speaker else None
            elif upper.startswith("NUDGE:"):
                val = line.split(":", 1)[1].strip()
                nudge = None if val in ("", "-") else val

        if not found_next:  # unparseable reply: fall back to fair rotation
            nxt = self._least_recent_speaker()
        return nxt, nudge

    def _least_recent_speaker(self) -> str:
        last_seen = {p.name: -1 for p in PERSONAS}
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
                    slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] += tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason

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
            return f"{name} failed: {e}"

    def _build_messages(self, persona: Persona, nudge: str | None) -> list[dict]:
        """Shared transcript → this persona's chat context.

        The persona's own past lines become `assistant` messages; everyone else's
        become `user` lines prefixed "Name: " (consecutive ones merged, since some
        providers require role alternation).
        """
        msgs: list[dict] = [{"role": "system", "content": prompts.persona_prompt(persona)}]
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
        provider, model = parse_spec(MODERATOR_MODEL)
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
