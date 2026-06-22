"""Central config loaded from environment (.env)."""
from __future__ import annotations

import os
from urllib.parse import urlparse


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Telegram ---
TG_API_ID = _int("TG_API_ID", 0)
TG_API_HASH = os.environ.get("TG_API_HASH", "")
TG_SESSION_NAME = os.environ.get("TG_SESSION_NAME", "odysseus_userbot")
# Optional portable session (created via tools/tg_setup.py export-string on another host)
TG_SESSION_STRING = os.environ.get("TG_SESSION_STRING", "").strip()
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SESSIONS = os.path.join(_PKG_DIR, "sessions")
SESSIONS_DIR = os.environ.get("SESSIONS_DIR", _DEFAULT_SESSIONS)

# Owner: only this user may control the bot via DM (@go_minetik)
TG_OWNER_ID = _int("TG_OWNER_ID", 8415112409)
TG_OWNER_USERNAME = os.environ.get("TG_OWNER_USERNAME", "go_minetik").lstrip("@").lower()
# Default test group for /odysseus call and DM commands without chat id
TG_TEST_GROUP_ID = _int("TG_TEST_GROUP_ID", -1003503983653)

# --- Proxy ---
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
PROXY_URL_HTTP = os.environ.get("PROXY_URL_HTTP", "").strip()
# Use proxy for Telegram MTProto (required from RF / blocked regions).
TG_USE_PROXY = os.environ.get("TG_USE_PROXY", "1").strip() not in ("0", "false", "no")
# socks5 | http — which proxy URL to use (PROXY_URL vs PROXY_URL_HTTP)
TG_PROXY_KIND = os.environ.get("TG_PROXY_KIND", "socks5").strip().lower()

# --- Deepgram STT (supports key rotation via DEEPGRAM_API_KEYS, comma-separated) ---
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_API_KEYS: list[str] = [
    k.strip() for k in os.environ.get("DEEPGRAM_API_KEYS", "").split(",") if k.strip()
]
DEEPGRAM_STT_MODEL = os.environ.get("DEEPGRAM_STT_MODEL", "nova-3")
DEEPGRAM_STT_LANG = os.environ.get("DEEPGRAM_STT_LANG", "multi")

# --- Groq Whisper STT (supports key rotation via GROQ_API_KEYS, comma-separated) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEYS: list[str] = [
    k.strip() for k in os.environ.get("GROQ_API_KEYS", "").split(",") if k.strip()
]
# If GROQ_API_KEYS is set, use rotation; otherwise fall back to single GROQ_API_KEY
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")

# --- Groq TTS ---
GROQ_TTS_MODEL = os.environ.get("GROQ_TTS_MODEL", "playai-tts")
GROQ_TTS_VOICE = os.environ.get("GROQ_TTS_VOICE", "Arista-PlayAI")

# --- Cartesia TTS (WebSocket streaming, key rotation) ---
CARTESIA_API_KEY = os.environ.get("CARTESIA_API_KEY", "")
CARTESIA_API_KEYS: list[str] = [
    k.strip() for k in os.environ.get("CARTESIA_API_KEYS", "").split(",") if k.strip()
]
CARTESIA_VOICE_ID = os.environ.get("CARTESIA_VOICE_ID", "7a62541e-5492-410e-95ff-3abd096fce87")
CARTESIA_MODEL_ID = os.environ.get("CARTESIA_MODEL_ID", "sonic-3.5")

import itertools as _it
import threading as _thr

class _KeyRotator:
    """Thread-safe round-robin key rotator."""
    def __init__(self, keys: list[str], fallback: str = ""):
        self._keys = keys or ([fallback] if fallback else [])
        self._cycle = _it.cycle(self._keys) if self._keys else None
        self._lock = _thr.Lock()

    def next(self) -> str:
        if not self._cycle:
            return ""
        with self._lock:
            return next(self._cycle)

    @property
    def available(self) -> bool:
        return bool(self._keys)

_GROQ_ROTATOR = _KeyRotator(GROQ_API_KEYS, GROQ_API_KEY)
_CARTESIA_ROTATOR = _KeyRotator(CARTESIA_API_KEYS, CARTESIA_API_KEY)
_DEEPGRAM_ROTATOR = _KeyRotator(DEEPGRAM_API_KEYS, DEEPGRAM_API_KEY)

# --- Edge-TTS (free, no API key) ---
EDGE_TTS_VOICE = os.environ.get("EDGE_TTS_VOICE", "ru-RU-SvetlanaNeural")
EDGE_TTS_RATE = os.environ.get("EDGE_TTS_RATE", "-10%")
TTS_SAMPLE_RATE = _int("TTS_SAMPLE_RATE", 48000)  # Edge-TTS decoded PCM rate

# --- Brain (OpenAI-compatible endpoint: odysseus_shim) ---
BRAIN_BACKEND = os.environ.get("BRAIN_BACKEND", "odysseus")
BRAIN_URL = os.environ.get("BRAIN_URL", "http://odysseus_shim:9200/v1").rstrip("/")
BRAIN_API_KEY = os.environ.get("BRAIN_API_KEY", "local-no-auth")
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "deepseek-v4-flash")

# --- Audio (call side, fixed by pytgcalls negotiation) ---
# Telegram group calls negotiate STEREO s16le @ 48k. Mono frames get
# interpreted as half-rate stereo -> chipmunk + static. Use 2 channels.
CALL_SAMPLE_RATE = 48000
CALL_CHANNELS = 2
FRAME_MS = 20  # frame size used for VAD and outgoing pacing

# --- Persona ---
PERSONA_FILE = os.environ.get("PERSONA_FILE", "/app/prompts/persona_odysseus.txt")


def load_persona() -> str:
    try:
        with open(PERSONA_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "Ты — Odysseus, дружелюбная ведущий голосового чата. Отвечай кратко по-русски."


def proxy_url_for(kind: str | None = None) -> str:
    """Pick SOCKS5 or HTTP proxy URL from env."""
    kind = (kind or TG_PROXY_KIND or "socks5").lower()
    if kind in ("http", "https"):
        return PROXY_URL_HTTP or PROXY_URL
    return PROXY_URL or PROXY_URL_HTTP


def parse_proxy(url: str):
    """Return a python_socks-style tuple for Telethon, or None."""
    if not url:
        return None
    import python_socks

    p = urlparse(url)
    scheme = (p.scheme or "socks5").lower()
    ptype = {
        "socks5": python_socks.ProxyType.SOCKS5,
        "socks4": python_socks.ProxyType.SOCKS4,
        "http": python_socks.ProxyType.HTTP,
        "https": python_socks.ProxyType.HTTP,
    }.get(scheme, python_socks.ProxyType.SOCKS5)
    # (proxy_type, addr, port, rdns, username, password)
    return (ptype, p.hostname, p.port, True, p.username, p.password)
