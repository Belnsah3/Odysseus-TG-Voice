"""Odysseus voice bridge entrypoint.

Wires Telethon userbot + pytgcalls + pipecat-ai pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

from voice_bridge import config
from voice_bridge.odysseus_tools import register_default_tools
from voice_bridge.pipecat_bot import BotApp
from voice_bridge.tg_userbot import register_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("main")


async def amain() -> None:
    if not config.TG_API_ID or not config.TG_API_HASH:
        log.error("TG_API_ID / TG_API_HASH not set")
        return
    if not config._DEEPGRAM_ROTATOR.available:
        log.warning("DEEPGRAM_API_KEYS not set - STT will fail")
    if not config._CARTESIA_ROTATOR.available:
        log.warning("CARTESIA_API_KEYS not set - TTS will fail")

    log.info(
        "TTS: Cartesia streaming (model=%s, voice=%s, keys=%d)",
        config.CARTESIA_MODEL_ID,
        config.CARTESIA_VOICE_ID,
        len(config.CARTESIA_API_KEYS),
    )
    log.info(
        "STT: Deepgram (model=%s, lang=%s, keys=%d)",
        config.DEEPGRAM_STT_MODEL,
        config.DEEPGRAM_STT_LANG,
        len(config.DEEPGRAM_API_KEYS),
    )

    app = BotApp()
    register_commands(app.client, app)
    await register_default_tools(app, app.client)

    try:
        await app.start()
        me = await app.client.get_me()
        log.info(
            "Logged in as %s (id=%s). Brain=%s @ %s",
            getattr(me, "username", None) or me.first_name,
            me.id,
            config.BRAIN_BACKEND,
            config.BRAIN_URL,
        )
        log.info(
            "Ready. Owner DM: @%s (id=%s). Test group: %s",
            config.TG_OWNER_USERNAME,
            config.TG_OWNER_ID,
            config.TG_TEST_GROUP_ID,
        )
        log.info("Commands in DM: /odysseus call | /odysseus join | /odysseus help")

        default_group = (
            app.store.get(config.TG_OWNER_ID).default_group_id or config.TG_TEST_GROUP_ID
        )
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
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
