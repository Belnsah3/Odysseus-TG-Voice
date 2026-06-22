"""VRM Avatar integration with Odysseus voice bridge.

Connects the VRM video streamer with the orchestrator's TTS pipeline.
When TTS speaks, the avatar's lips sync. Emotions are detected from text.

Architecture (from Frierenclaw/fern pattern):
  STT -> Brain -> TTS -> [Audio to Telegram] + [Viseme to VRM Renderer -> Video to Telegram]
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .streamer import VideoStreamer
from .lipsync import text_to_visemes

log = logging.getLogger("vrm_avatar")


class AvatarController:
    """Controls VRM avatar state during a group call.

    Integrates with the orchestrator to sync lip movements
    and emotions with TTS output.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "models", "avatar.vrm"
        )
        self._streamer: Optional[VideoStreamer] = None
        self._viseme_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the avatar video stream."""
        self._streamer = VideoStreamer(self.model_path)
        await self._streamer.start()
        log.info("Avatar started, pipe: %s", self._streamer.get_pipe_path())

    def get_video_source(self) -> str:
        """Get video source path for pytgcalls join_group_call."""
        if self._streamer:
            return self._streamer.get_pipe_path()
        return ""

    async def on_tts_start(self, text: str) -> None:
        """Called when TTS starts speaking. Triggers lip sync."""
        if not self._streamer:
            return

        # Detect emotion from text
        emotion = self._detect_emotion(text)
        self._streamer.set_emotion(emotion)

        # Start lip sync
        visemes = text_to_visemes(text)
        self._viseme_task = asyncio.create_task(self._animate_visemes(visemes))

    async def on_tts_end(self) -> None:
        """Called when TTS stops speaking."""
        if self._viseme_task:
            self._viseme_task.cancel()
            try:
                await self._viseme_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._streamer:
            self._streamer.set_emotion("neutral")
            self._streamer.renderer.set_viseme("sil")

    async def _animate_visemes(self, visemes: list) -> None:
        """Animate viseme sequence with timing."""
        try:
            for viseme, duration_ms in visemes:
                if self._streamer:
                    self._streamer.renderer.set_viseme(viseme)
                await asyncio.sleep(duration_ms / 1000.0)
        except asyncio.CancelledError:
            pass

    def _detect_emotion(self, text: str) -> str:
        t = text.lower()
        if any(w in t for w in ["!", " ура", " круто", " отлично"]):
            return "happy"
        if any(w in t for w in ["грустн", " жаль"]):
            return "sad"
        if any(w in t for w in ["зл", " бесят"]):
            return "angry"
        if any(w in t for w in ["?", " интересно"]):
            return "surprised"
        return "neutral"

    async def stop(self) -> None:
        """Stop avatar and clean up."""
        await self.on_tts_end()
        if self._streamer:
            await self._streamer.stop()
        log.info("Avatar stopped")
