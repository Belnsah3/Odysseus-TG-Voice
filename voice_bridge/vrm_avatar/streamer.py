"""Video streamer - pipes rendered frames to Telegram group call via pytgcalls.

Architecture (inspired by Frierenclaw/fern pipeline):
  renderer -> frame_queue -> ffmpeg_pipe -> pytgcalls video

For Telegram group calls, video must be piped through FFmpeg as
a raw video stream that pytgcalls consumes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Optional

import numpy as np

from .renderer import VRMRenderer
from .lipsync import text_to_visemes

log = logging.getLogger("vrm_stream")

# Telegram group call video format
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
VIDEO_FPS = 30
PIXEL_FORMAT = "rgb24"


class VideoStreamer:
    """Renders VRM frames and streams to pytgcalls.

    Usage:
        streamer = VideoStreamer("model.vrm")
        await streamer.start()
        pipe_path = streamer.get_pipe_path()  # pass to pytgcalls AudioVideoPiped
        # ...
        await streamer.stop()
    """

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.renderer = VRMRenderer(
            model_path=model_path,
            width=VIDEO_WIDTH,
            height=VIDEO_HEIGHT,
            fps=VIDEO_FPS,
        )
        self._ffmpeg_proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._pipe_path = "/tmp/odysseus_video.pipe"
        self._cancelled = False

    async def start(self) -> None:
        """Start the rendering pipeline."""
        # Create named pipe for FFmpeg
        if os.path.exists(self._pipe_path):
            os.remove(self._pipe_path)
        os.mkfifo(self._pipe_path)

        # Launch FFmpeg to convert raw frames to MPEG-TS
        # MPEG-TS is the format pytgcalls expects for streaming video
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", PIXEL_FORMAT,
            "-s", f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
            "-r", str(VIDEO_FPS),
            "-i", "pipe:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-f", "mpegts",
            self._pipe_path,
        ]

        self._ffmpeg_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await self.renderer.start()
        self._task = asyncio.create_task(self._stream_loop())
        log.info("Video streamer started: pipe=%s", self._pipe_path)

    async def _stream_loop(self) -> None:
        """Main loop: render frames and pipe to FFmpeg."""
        try:
            async for frame_bytes in self.renderer.stream_frames():
                if self._cancelled:
                    break
                if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.write(frame_bytes)
                    await self._ffmpeg_proc.stdin.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("Video stream error: %s", e)

    def speak(self, text: str) -> None:
        """Trigger lip sync for given text."""
        visemes = text_to_visemes(text)
        log.debug("Lip sync: %d visemes for text: %s", len(visemes), text[:50])
        # In production, schedule viseme updates to renderer

    def set_emotion(self, emotion: str) -> None:
        """Set facial expression."""
        self.renderer.set_emotion(emotion)

    def get_pipe_path(self) -> str:
        """Get the named pipe path for pytgcalls AudioVideoPiped."""
        return self._pipe_path

    async def stop(self) -> None:
        """Stop the rendering pipeline."""
        self._cancelled = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self.renderer.stop()
        if self._ffmpeg_proc:
            self._ffmpeg_proc.terminate()
            await self._ffmpeg_proc.wait()
        if os.path.exists(self._pipe_path):
            os.remove(self._pipe_path)
        log.info("Video streamer stopped")
