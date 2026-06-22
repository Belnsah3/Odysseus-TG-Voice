"""Odysseus voice assistant using pipecat-ai + Telegram calls."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMContextFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService

from pytgcalls import PyTgCalls

from voice_bridge import config
from voice_bridge.pipecat_transport import TelegramTransport
from voice_bridge.sessions import SessionStore
from voice_bridge.tg_client import build_client
from voice_bridge.tg_group_info import get_group_call_info, normalize_chat_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("odysseus")


class _KeyRotator:
    """Thread-safe round-robin key rotator."""

    def __init__(self, keys: list[str], fallback: str = ""):
        self._keys = keys or ([fallback] if fallback else [])
        self._cycle = __import__("itertools").cycle(self._keys) if self._keys else None
        self._lock = threading.Lock()

    def next(self) -> str:
        if not self._cycle:
            return ""
        with self._lock:
            return next(self._cycle)

    @property
    def available(self) -> bool:
        return bool(self._keys)


_CARTESIA_ROTATOR = _KeyRotator(config.CARTESIA_API_KEYS, config.CARTESIA_API_KEY)
_DEEPGRAM_ROTATOR = _KeyRotator(config.DEEPGRAM_API_KEYS, config.DEEPGRAM_API_KEY)


def _load_persona() -> str:
    try:
        return Path(config.PERSONA_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return (
            "Ты — Odysseus, дружелюбный AI-ведущий голосового чата Telegram. "
            "Отвечай по-русски, кратко, живо, с лёгким юмором."
        )


class EmotionCartesiaTTSService(CartesiaTTSService):
    """Cartesia TTS with dynamic emotion based on the text being spoken."""

    async def process_frame(self, frame, direction: FrameDirection):
        if isinstance(frame, TextFrame):
            frame = self._set_emotion(frame)
        await super().process_frame(frame, direction)

    def _set_emotion(self, frame: TextFrame) -> TextFrame:
        text = frame.text.lower()
        emotion = "neutral:default"
        if any(w in text for w in ("ура", "круто", "отлично", "супер", "восхитительно", "здорово", "хаха", "смешно")):
            emotion = "happy:excited"
        elif any(w in text for w in ("обидно", "грустно", "печально", "жаль", "плакать")):
            emotion = "sad:default"
        elif any(w in text for w in ("злой", "бесит", "ненавижу", "достало", "урод", "ублюдок")):
            emotion = "anger:annoyed"
        elif any(w in text for w in ("что", "как", "почему", "интересно", "зачем")):
            emotion = "curiosity"
        elif any(w in text for w in ("ой", "вау", "неожиданно", "удивительно", "серьёзно")):
            emotion = "surprise:amazed"
        try:
            from pipecat.services.cartesia.tts import GenerationConfig

            self._settings.generation_config = GenerationConfig(
                speed=0.95, volume=1.0, emotion=emotion
            )
        except Exception as e:
            log.debug("emotion set failed: %s", e)
        return frame


class MemoryBridge(FrameProcessor):
    """Persist pipecat LLM context messages to the local session store."""

    def __init__(self, store: SessionStore, chat_id: int, *, name: str | None = None):
        super().__init__(name=name)
        self._store = store
        self._chat_id = chat_id

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self._store.get(self._chat_id).messages = list(frame.context.get_messages())
        await self.push_frame(frame, direction)


async def _build_pipeline(
    tgcalls: PyTgCalls,
    chat_id: int,
    store: SessionStore,
) -> tuple[PipelineTask, TelegramTransport]:
    transport = TelegramTransport(
        tgcalls=tgcalls,
        chat_id=chat_id,
        name="telegram",
    )

    stt = DeepgramSTTService(
        api_key=_DEEPGRAM_ROTATOR.next(),
        sample_rate=config.CALL_SAMPLE_RATE,
        live_options={
            "language": config.DEEPGRAM_STT_LANG,
            "model": config.DEEPGRAM_STT_MODEL,
            "punctuate": True,
            "smart_format": True,
            "interim_results": False,
            "channels": 1,
            "endpointing": 300,
        },
    )

    llm = OpenAILLMService(
        api_key=config.BRAIN_API_KEY,
        base_url=config.BRAIN_URL,
        model=config.BRAIN_MODEL,
        params={"temperature": 0.8, "max_tokens": 512},
    )

    tts = EmotionCartesiaTTSService(
        api_key=_CARTESIA_ROTATOR.next(),
        voice_id=config.CARTESIA_VOICE_ID,
        model=config.CARTESIA_MODEL_ID,
        sample_rate=config.CALL_SAMPLE_RATE,
        encoding="pcm_s16le",
        container="raw",
        params={"speed": 0.95, "volume": 1.0, "language": "ru"},
    )

    persona = _load_persona()
    context = LLMContext(messages=[{"role": "system", "content": persona}])
    agg_pair = LLMContextAggregatorPair(context)
    memory = MemoryBridge(store=store, chat_id=chat_id, name="memory")

    # Pipeline:
    # transport -> stt -> user_agg -> memory -> llm -> tts -> transport
    #                              \-> assistant_agg -> memory
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            agg_pair.user(),
            memory,
            llm,
            tts,
            agg_pair.assistant(),
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_heartbeats=True,
        ),
    )
    return task, transport


class BotApp:
    def __init__(self):
        self.store = SessionStore()
        self.client = build_client()
        self.tgcalls = PyTgCalls(self.client)
        self._runner: PipelineRunner | None = None
        self._task: PipelineTask | None = None
        self._transport: TelegramTransport | None = None

    async def start(self) -> None:
        log.info("Starting Odysseus...")
        await self.client.start()
        await self.tgcalls.start()
        log.info("Telegram userbot connected.")

    async def stop(self) -> None:
        if self._transport:
            await self._transport.leave()
        if self._runner:
            await self._runner.stop()
        await self.tgcalls.stop()
        await self.client.disconnect()

    async def join_call(self, chat_id: int) -> None:
        chat_id = await normalize_chat_id(self.client, chat_id)
        if self._transport and self._transport._chat_id == chat_id:
            log.info("Already active in %s", chat_id)
            return

        if self._transport:
            await self._transport.leave()
            if self._runner:
                await self._runner.stop()

        gc = await get_group_call_info(self.client, chat_id)
        if not gc.active:
            log.warning("No active group call in %s", chat_id)
            raise RuntimeError("no active group call")

        self._task, self._transport = await _build_pipeline(self.tgcalls, chat_id, self.store)
        self._runner = PipelineRunner()

        await self._transport.setup()
        await self._transport.join()

        asyncio.create_task(self._runner.run(self._task))
        log.info("Pipeline running for chat %s", chat_id)

    async def leave_call(self, chat_id: int | None = None) -> None:
        if self._transport and (chat_id is None or self._transport._chat_id == chat_id):
            await self._transport.leave()
            self._transport = None
        if self._runner:
            await self._runner.stop()
            self._runner = None
        self._task = None


async def main() -> None:
    app = BotApp()
    try:
        await app.start()
        default_group = app.store.get(config.TG_OWNER_ID).default_group_id or config.TG_TEST_GROUP_ID
        if default_group:
            try:
                await app.join_call(default_group)
            except Exception as e:
                log.warning("Auto-join failed for %s: %s", default_group, e)
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutting down...")
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
