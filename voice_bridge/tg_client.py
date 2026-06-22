"""Minimal Telethon client factory (no voice/orchestrator deps)."""
from __future__ import annotations

import logging
import os

from telethon import TelegramClient

from voice_bridge import config

log = logging.getLogger("tg_client")


def build_client(*, use_proxy: bool | None = None, proxy_kind: str | None = None) -> TelegramClient:
    """Build Telethon client. Proxy is required from blocked regions (RF)."""
    from telethon.sessions import StringSession

    os.makedirs(config.SESSIONS_DIR, exist_ok=True)
    if config.TG_SESSION_STRING:
        session = StringSession(config.TG_SESSION_STRING)
        session_label = "string env"
    else:
        session_path = os.path.join(config.SESSIONS_DIR, config.TG_SESSION_NAME)
        session = session_path
        session_label = session_path
    if use_proxy is None:
        use_proxy = config.TG_USE_PROXY
    proxy = None
    proxy_label = "direct"
    if use_proxy:
        url = config.proxy_url_for(proxy_kind)
        proxy = config.parse_proxy(url)
        if proxy:
            proxy_label = url.split("://")[0] if "://" in url else "proxy"
    if proxy:
        log.info("Using %s proxy %s:%s, session=%s", proxy_label, proxy[1], proxy[2], session_label)
    elif use_proxy:
        log.warning("TG_USE_PROXY=1 but no PROXY_URL configured")
    else:
        log.info("Telegram: direct connection (no proxy)")
    return TelegramClient(session, config.TG_API_ID, config.TG_API_HASH, proxy=proxy)
