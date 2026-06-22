"""Tool executor for Odysseus agent mode."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Coroutine

from voice_bridge import config
from voice_bridge.say_injector import say_in_call
from voice_bridge.sessions import store

log = logging.getLogger("tools")
ToolFn = Callable[..., Coroutine[Any, Any, str]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}

    def register(self, name: str, fn: ToolFn) -> None:
        self._tools[name] = fn

    async def run(self, name: str, arguments: dict[str, Any]) -> str:
        fn = self._tools.get(name)
        if fn is None:
            return f"[unknown tool {name}]"
        try:
            return await fn(**arguments)
        except Exception as e:
            log.exception("tool %s failed", name)
            return f"[error: {e}]"


def _parse_chat_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    m = re.search(r"-?\d+", str(value))
    if not m:
        raise ValueError(f"invalid chat_id: {value}")
    return int(m.group(0))


def _default_chat_id(chat_id: Any | None) -> int:
    if chat_id is None:
        return store.get(config.TG_OWNER_ID).default_group_id
    return _parse_chat_id(chat_id)


registry = ToolRegistry()

TOOL_DESCRIPTIONS = """
Доступные инструменты (вызывай только когда нужно выполнить действие):

1. call_group(chat_id) - подключиться к видеочату группы.
2. leave_group(chat_id) - выйти из видеочата.
3. mute_group(chat_id) - перестать слушать/отвечать.
4. unmute_group(chat_id) - снова слушать/отвечать.
5. say_in_group(chat_id, text) - произнести фразу в звонке.
6. send_message(chat_id, text) - отправить текстовое сообщение в чат.
7. remember(chat_id, fact) - запомнить факт.
8. set_reminder(chat_id, text, seconds) - напомнить через N секунд.
9. reset_memory(chat_id) - сбросить историю разговора.
10. set_default_group(chat_id) - установить группу по умолчанию.

Format:
<tool_call>{"name": "...", "arguments": {...}}</tool_call>
""".strip()


def extract_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    calls: list[dict[str, Any]] = []
    cleaned = text
    for m in re.finditer(r"\u003ctool_call\u003e(.*?)\u003c/tool_call\u003e", text, re.DOTALL):
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict) and "name" in obj:
                calls.append({"name": obj["name"], "arguments": obj.get("arguments", {})})
        except json.JSONDecodeError:
            continue
        cleaned = cleaned.replace(m.group(0), "")
    return calls, cleaned.strip()


async def register_default_tools(app, client) -> None:
    """Register tool implementations that need BotApp + Telethon client."""

    async def call_group(chat_id=None):
        cid = _default_chat_id(chat_id)
        await app.join_call(cid)
        return f"Подключился к группе {cid}."

    async def leave_group(chat_id=None):
        cid = _default_chat_id(chat_id)
        await app.leave_call(cid)
        return f"Вышел из группы {cid}."

    async def mute_group(chat_id=None):
        cid = _default_chat_id(chat_id)
        store.get(cid).muted = True
        return "Теперь я молчу."

    async def unmute_group(chat_id=None):
        cid = _default_chat_id(chat_id)
        store.get(cid).muted = False
        return "Снова слушаю."

    async def say_in_group(text, chat_id=None):
        cid = _default_chat_id(chat_id)
        if app._transport and app._transport._chat_id == cid:
            await say_in_call(app._transport, text)
            return f"Сказал в группе {cid}."
        return "Я не в звонке."

    async def send_message(chat_id=None, text=""):
        cid = _default_chat_id(chat_id)
        await client.send_message(cid, text)
        return f"Отправил сообщение в {cid}."

    async def remember(fact, chat_id=None):
        cid = _default_chat_id(chat_id)
        store.get(cid).remember(fact)
        return "Запомнил."

    async def set_reminder(text, seconds, chat_id=None):
        cid = _default_chat_id(chat_id)
        store.get(cid).add_reminder(text, due_ts=time.time() + float(seconds))

        async def _reminder():
            await asyncio.sleep(max(0.0, float(seconds)))
            if app._transport and app._transport._chat_id == cid:
                await say_in_call(app._transport, text)

        asyncio.create_task(_reminder())
        return f"Напомню через {seconds} секунд."

    async def reset_memory(chat_id=None):
        cid = _default_chat_id(chat_id)
        store.get(cid).reset()
        return "Память сброшена."

    async def set_default_group(chat_id):
        cid = _parse_chat_id(chat_id)
        store.get(config.TG_OWNER_ID).default_group_id = cid
        return f"Группа по умолчанию теперь {cid}."

    for name, fn in [
        ("call_group", call_group),
        ("leave_group", leave_group),
        ("mute_group", mute_group),
        ("unmute_group", unmute_group),
        ("say_in_group", say_in_group),
        ("send_message", send_message),
        ("remember", remember),
        ("set_reminder", set_reminder),
        ("reset_memory", reset_memory),
        ("set_default_group", set_default_group),
    ]:
        registry.register(name, fn)
