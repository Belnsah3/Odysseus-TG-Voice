"""Helper to inject text into an active pipecat pipeline from outside."""
from __future__ import annotations

from pipecat.frames.frames import TextFrame


async def say_in_call(transport, text: str) -> None:
    await transport.inject(TextFrame(text=text))
