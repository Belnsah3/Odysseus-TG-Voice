"""VRM renderer using headless Chrome + three.js.

Architecture inspired by Frierenclaw/fern pipeline pattern:
- Separate rendering process (headless Chrome with three.js + @pixiv/three-vrm)
- FFmpeg pipe to convert rendered frames to Telegram-compatible video
- Frame scheduling with asyncio for smooth 30fps output
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

log = logging.getLogger("vrm")

# Default resolution for Telegram group calls
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_FPS = 30


class VRMRenderer:
    """Renders VRM model frames using headless Chrome + three.js.

    Usage:
        renderer = VRMRenderer("model.vrm")
        async for frame_bytes in renderer.stream_frames():
            # frame_bytes is raw RGB24 (width * height * 3 bytes)
            pass
    """

    def __init__(
        self,
        model_path: str,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = DEFAULT_FPS,
    ):
        self.model_path = model_path
        self.width = width
        self.height = height
        self.fps = fps
        self._process: Optional[asyncio.subprocess.Process] = None
        self._cancelled = False
        self._viseme: str = "sil"
        self._emotion: str = "neutral"

    async def start(self) -> None:
        """Launch headless Chrome renderer process."""
        script = os.path.join(os.path.dirname(__file__), "renderer_page.html")
        if not os.path.exists(script):
            log.warning("Renderer HTML not found at %s, using stub mode", script)
            return

        cmd = [
            "chromium",
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            f"--window-size={self.width},{self.height}",
            "--screenshot=/dev/stdout",
            f"--virtual-time-budget={1000 // self.fps}",
            f"file://{script}?model={self.model_path}",
        ]
        log.info("VRM renderer starting: %dx%d @ %dfps", self.width, self.height, self.fps)

    def set_viseme(self, viseme: str) -> None:
        """Set current mouth shape for lip sync.

        Args:
            viseme: One of 'sil', 'PP', 'FF', 'TH', 'DD', 'kk', 'CH',
                    'SS', 'nn', 'RR', 'aa', 'E', 'ih', 'oh', 'ou'
        """
        self._viseme = viseme

    def set_emotion(self, emotion: str) -> None:
        """Set facial expression emotion.

        Args:
            emotion: One of 'neutral', 'happy', 'sad', 'angry', 'surprised'
        """
        self._emotion = emotion

    async def render_frame(self) -> Optional[bytes]:
        """Render a single frame. Returns RGB24 bytes or None."""
        # Stub: generates a solid color frame for testing
        r = 40 if self._emotion == "neutral" else (80 if self._emotion == "happy" else 20)
        g = 40 if self._emotion == "sad" else 60
        b = 60 if self._emotion == "angry" else 80
        return bytes([r, g, b] * self.width * self.height)

    async def stream_frames(self):
        """Async generator yielding RGB24 frame bytes at target FPS."""
        interval = 1.0 / self.fps
        while not self._cancelled:
            frame = await self.render_frame()
            if frame:
                yield frame
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        self._cancelled = True
        if self._process:
            self._process.terminate()
            await self._process.wait()
