"""Telegram group-call transport for pipecat.

Acts as both input and output FrameProcessor. Input audio from pytgcalls is
pushed downstream as InputAudioRawFrame. OutputAudioRawFrame coming from the
pipeline is buffered and paced to pytgcalls in real-time 20 ms chunks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pytgcalls import PyTgCalls, filters as ptg_filters
from pytgcalls.exceptions import NoActiveGroupCall
from pytgcalls.types import (
    Device,
    Direction,
    ExternalMedia,
    GroupCallConfig,
    MediaStream,
    RecordStream,
    StreamFrames,
)
from pytgcalls.types.raw import AudioParameters

from voice_bridge import config
from voice_bridge.audio_utils import frame_size_bytes, mono_to_stereo, stereo_to_mono

log = logging.getLogger(__name__)

OUT_FRAME_BYTES = frame_size_bytes(config.CALL_SAMPLE_RATE, config.FRAME_MS, config.CALL_CHANNELS)
OUT_FRAME_SEC = config.FRAME_MS / 1000.0
_ALREADY_JOINED_MESSAGES = ("already joined", "already in call", "already_participating")


class TelegramTransport(FrameProcessor):
    """Pipecat transport backed by a live Telegram group call."""

    def __init__(
        self,
        tgcalls: PyTgCalls,
        chat_id: int,
        *,
        name: str | None = None,
        on_joined: Callable[[int], Awaitable[None]] | None = None,
        on_left: Callable[[int], Awaitable[None]] | None = None,
    ):
        super().__init__(name=name)
        self._tgcalls = tgcalls
        self._chat_id = chat_id
        self._on_joined = on_joined
        self._on_left = on_left

        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._pacer_task: asyncio.Task | None = None
        self._joined = False

    def input(self) -> FrameProcessor:
        return self

    def output(self) -> FrameProcessor:
        return self

    async def setup(self):
        """Register pytgcalls update handlers."""
        @self._tgcalls.on_update(ptg_filters.stream_frame(directions=Direction.INCOMING))
        async def _on_frames(_, update: StreamFrames):
            if update.chat_id != self._chat_id or not self._joined:
                return
            for fr in update.frames:
                if fr.frame:
                    await self._handle_incoming(fr.frame)

    async def _handle_incoming(self, pcm: bytes) -> None:
        try:
            if config.CALL_CHANNELS == 2:
                pcm = stereo_to_mono(pcm)
            await self.push_frame(
                InputAudioRawFrame(
                    audio=pcm,
                    sample_rate=config.CALL_SAMPLE_RATE,
                    num_channels=1,
                ),
                FrameDirection.DOWNSTREAM,
            )
        except Exception as e:
            log.debug("incoming handler error: %s", e)

    async def join(self) -> None:
        params = AudioParameters(config.CALL_SAMPLE_RATE, config.CALL_CHANNELS)
        try:
            await self._tgcalls.play(
                self._chat_id,
                MediaStream(ExternalMedia.AUDIO, audio_parameters=params),
                config=GroupCallConfig(auto_start=True),
            )
        except NoActiveGroupCall:
            raise
        except Exception as exc:
            msg = str(exc).lower()
            if any(m in msg for m in _ALREADY_JOINED_MESSAGES):
                log.info("already in call %s", self._chat_id)
            else:
                raise
        try:
            await self._tgcalls.record(
                self._chat_id,
                RecordStream(audio=True, audio_parameters=params),
            )
        except Exception as e:
            log.warning("record failed for %s: %s", self._chat_id, e)

        self._joined = True
        self._pacer_task = asyncio.create_task(self._pacer())
        await self.push_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        if self._on_joined:
            try:
                await self._on_joined(self._chat_id)
            except Exception as e:
                log.debug("on_joined error: %s", e)
        log.info("joined call in chat %s", self._chat_id)

    async def leave(self) -> None:
        if not self._joined:
            return
        self._joined = False
        await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        if self._pacer_task:
            self._pacer_task.cancel()
            try:
                await self._pacer_task
            except asyncio.CancelledError:
                pass
            self._pacer_task = None
        try:
            await self._tgcalls.leave_call(self._chat_id)
        except Exception as e:
            log.debug("leave_call: %s", e)
        if self._on_left:
            try:
                await self._on_left(self._chat_id)
            except Exception as e:
                log.debug("on_left error: %s", e)
        log.info("left call in chat %s", self._chat_id)

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame):
            await self._handle_output(frame)
        elif isinstance(frame, InterruptionFrame):
            await self._clear_output()

    async def _handle_output(self, frame: OutputAudioRawFrame) -> None:
        pcm = frame.audio
        if frame.num_channels == 1 and config.CALL_CHANNELS == 2:
            pcm = mono_to_stereo(pcm)
        await self._output_queue.put(pcm)

    async def _clear_output(self) -> None:
        try:
            while True:
                self._output_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def _pacer(self) -> None:
        leftover = b""
        next_tick = time.monotonic()
        try:
            while True:
                if len(leftover) < OUT_FRAME_BYTES:
                    try:
                        chunk = await asyncio.wait_for(self._output_queue.get(), timeout=0.5)
                        leftover += chunk
                        continue
                    except asyncio.TimeoutError:
                        if not leftover:
                            next_tick = time.monotonic()
                            continue
                        leftover = leftover.ljust(OUT_FRAME_BYTES, b"\x00")

                frame = leftover[:OUT_FRAME_BYTES]
                leftover = leftover[OUT_FRAME_BYTES:]
                try:
                    await self._tgcalls.send_frame(self._chat_id, Device.MICROPHONE, frame)
                except Exception as e:
                    log.debug("send_frame error: %s", e)

                next_tick += OUT_FRAME_SEC
                delay = next_tick - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_tick = time.monotonic()
        except asyncio.CancelledError:
            pass

    async def inject(self, frame):
        """Push a frame into the pipeline from an external source (e.g. /say)."""
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)
