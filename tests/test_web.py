#!/usr/bin/env python3
"""Web smoke test: boot the café server, connect over WebSocket, say something,
wait for a streamed persona turn, check the transcript endpoint, shut down.

    python tests/test_web.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

import websockets

from autodebate.config import check_keys

PORT = 8123
TIMEOUT_S = 150
OPENING = "Give the table one idea about the future you'd defend even if everyone here disagrees."


async def wait_for_server() -> None:
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2) as r:
                if r.status == 200 and b"autodebate" in r.read():
                    return
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("server did not come up")


async def main() -> int:
    check_keys()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "autodebate.web:app", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ},
    )
    try:
        await wait_for_server()
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
            init = json.loads(await asyncio.wait_for(ws.recv(), 10))
            assert init["type"] == "init", init
            assert len(init["personas"]) == 3, init["personas"]

            await ws.send(json.dumps({"type": "speak", "text": OPENING}))
            deadline = time.monotonic() + TIMEOUT_S
            answer = None
            while time.monotonic() < deadline and answer is None:
                remaining = max(1.0, deadline - time.monotonic())
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), remaining))
                except TimeoutError:
                    break
                if ev["type"] == "speaker_end" and ev.get("text", "").strip():
                    answer = (ev["name"], ev["text"].strip())

        # the transcript file should be served over HTTP too
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/transcript", timeout=5) as r:
            served = r.read().decode()
        transcript_ok = "You" in served and (answer is None or answer[0] in served)

        ok = answer is not None and transcript_ok
        print(
            f"WEB smoke: {'OK' if ok else 'FAILED'}"
            + (f" ({answer[0]} answered)" if answer else " (no answer in time)")
        )
        if answer:
            print(f"--- {answer[0]}: {answer[1][:400]}")
        return 0 if ok else 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
