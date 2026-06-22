"""Telethon userbot: control Odysseus from DMs or in-chat commands.

Owner DM (@go_minetik): send commands in private messages to the userbot account.
In a group: outgoing /odysseus ... still works.

Commands:
  /odysseus call              start/join video chat in TG_TEST_GROUP (creates call if needed)
  /odysseus join [chat_id]    join active call (in this chat, or target id from DM)
  /odysseus leave [chat_id]
  /odysseus mute|unmute [chat_id]
  /odysseus reset [chat_id]
  /odysseus prompt <text>
  /odysseus say <text> [chat_id]
  /odysseus groups
  /odysseus info [chat_id]
  /odysseus help
"""
from __future__ import annotations

import json
import logging
import os
import re

from telethon import TelegramClient, events
from telethon.errors import ChatAdminRequiredError, RPCError
from telethon.tl.types import User

from pytgcalls.exceptions import NoActiveGroupCall

from voice_bridge import config
from voice_bridge.pipecat_bot import BotApp
from voice_bridge.say_injector import say_in_call
from voice_bridge.sessions import store
from voice_bridge.tg_group_info import (
    get_group_call_info,
    list_groups,
    normalize_chat_id,
    save_snapshot,
    snapshot_group,
)

log = logging.getLogger("userbot")

HELP = (
    "Odysseus — команды:\n"
    "/odysseus call — зайти/создать звонок в группе по умолчанию\n"
    "/odysseus join [chat_id] — войти в активный видеочат\n"
    "/odysseus leave [chat_id] — выйти\n"
    "/odysseus setgroup <chat_id> — группа по умолчанию для звонков\n"
    "/odysseus groups — список групп\n"
    "/odysseus mute | unmute [chat_id]\n"
    "/odysseus reset [chat_id] — сброс памяти\n"
    "/odysseus prompt <текст> — сменить персону\n"
    "/odysseus say <текст> [chat_id]\n"
    "/odysseus info [chat_id] — данные группы в JSON\n"
    "/odysseus help\n"
    "/help — этот список"
)

_CHAT_ID_RE = re.compile(r"^-?\d+$")


async def _is_owner(event: events.NewMessage.Event) -> bool:
    if event.out:
        return True
    sender = await event.get_sender()
    if not isinstance(sender, User):
        return False
    if sender.id == config.TG_OWNER_ID:
        return True
    uname = (sender.username or "").lower()
    return uname == config.TG_OWNER_USERNAME


def _parse_chat_id(token: str) -> int | None:
    token = token.strip()
    if _CHAT_ID_RE.match(token):
        return int(token)
    return None


def _owner_default_group() -> int:
    """Return the owner's saved default group, or TG_TEST_GROUP_ID."""
    return store.get(config.TG_OWNER_ID).default_group_id


def _set_owner_default_group(chat_id: int) -> None:
    store.get(config.TG_OWNER_ID).default_group_id = chat_id


def _resolve_target_chat(event: events.NewMessage.Event, sub: str, rest: str) -> tuple[int, str]:
    """Return (group_chat_id, remaining_args)."""
    # Commands whose first positional arg is NOT a chat_id.
    if sub in ("setgroup", "prompt", "say"):
        if event.is_private and sub in ("call", "join", "leave", "mute", "unmute", "reset", "info", "say"):
            return _owner_default_group(), rest
        if not event.is_private:
            return event.chat_id, rest
        return _owner_default_group(), rest

    parts = rest.split()
    if parts and _parse_chat_id(parts[0]) is not None:
        return int(parts[0]), " ".join(parts[1:])

    # DM with owner: default to the configured default group
    if event.is_private and sub in (
        "call", "join", "leave", "mute", "unmute", "reset", "info", "say",
    ):
        return _owner_default_group(), rest

    # Inside a group/supergroup — use this chat
    if not event.is_private:
        return event.chat_id, rest

    return _owner_default_group(), rest


async def _reply(event: events.NewMessage.Event, text: str) -> None:
    text = text[:4000]
    if event.out:
        try:
            await event.edit(text)
        except Exception:
            await event.respond(text)
    else:
        await event.respond(text)


def register_commands(client: TelegramClient, app: BotApp) -> None:
    @client.on(events.NewMessage(pattern=r"^/help(?:\s+.*)?$"))
    async def _help_cmd(event: events.NewMessage.Event):  # noqa: ANN001
        if not await _is_owner(event):
            return
        await _reply(event, HELP)

    @client.on(events.NewMessage(pattern=r"^/odysseus(?:\s+(.*))?$"))
    async def _cmd(event: events.NewMessage.Event):  # noqa: ANN001
        if not await _is_owner(event):
            return

        raw = (event.pattern_match.group(1) or "").strip()
        parts = raw.split(maxsplit=1)
        sub = (parts[0].lower() if parts else "help")
        rest = parts[1] if len(parts) > 1 else ""

        target_chat, rest = _resolve_target_chat(event, sub, rest)
        target_chat = await normalize_chat_id(client, target_chat)

        try:
            if sub == "call":
                gc = await get_group_call_info(client, target_chat)
                if gc.active:
                    await _reply(
                        event,
                        f"Odysseus: подключаюсь к звонку в {target_chat} "
                        f"({gc.participants_count or '?'} уч.)…",
                    )
                    await app.join_call(target_chat)
                else:
                    await _reply(
                        event,
                        f"Odysseus: в группе {target_chat} нет активного звонка.\n"
                        "Запусти звонок вручную или используй /odysseus join",
                    )
                    return
                if app._transport:
                    await say_in_call(app._transport, "Всем привет, Odysseus на связи.")
                await _reply(event, f"Odysseus: в звонке (группа {target_chat}). Слушаю.")

            elif sub == "join":
                gc = await get_group_call_info(client, target_chat)
                if not gc.active:
                    await _reply(
                        event,
                        f"Odysseus: в группе {target_chat} нет активного видеочата.\n"
                        "Открой звонок вручную или используй /odysseus call",
                    )
                    return
                await _reply(
                    event,
                    f"Odysseus: подключаюсь к {target_chat} "
                    f"({gc.participants_count or '?'} уч.)…",
                )
                await app.join_call(target_chat)
                if app._transport:
                    await say_in_call(app._transport, "Всем привет, Odysseus на связи.")
                await _reply(event, f"Odysseus: в звонке (группа {target_chat}).")

            elif sub == "leave":
                await app.leave_call(target_chat)
                await _reply(event, f"Odysseus: вышел из звонка (группа {target_chat}).")

            elif sub == "mute":
                store.get(target_chat).muted = True
                await _reply(event, f"Odysseus: mute (группа {target_chat}).")

            elif sub == "unmute":
                store.get(target_chat).muted = False
                await _reply(event, f"Odysseus: unmute (группа {target_chat}).")

            elif sub == "reset":
                store.get(target_chat).reset()
                await _reply(event, f"Odysseus: память сброшена (группа {target_chat}).")

            elif sub == "prompt":
                if rest:
                    store.get(target_chat).system_prompt = rest
                    await _reply(event, "Odysseus: персона обновлена.")
                else:
                    await _reply(event, "Odysseus: дай текст промпта.")

            elif sub == "say":
                text = rest.strip()
                say_chat = target_chat
                if event.is_private and rest:
                    tokens = rest.rsplit(maxsplit=1)
                    if len(tokens) == 2 and _parse_chat_id(tokens[1]) is not None:
                        text, say_chat = tokens[0], int(tokens[1])
                if app._transport and app._transport._chat_id == say_chat and text:
                    await _reply(event, "Odysseus: говорю…")
                    await say_in_call(app._transport, text)
                else:
                    await _reply(
                        event,
                        f"Odysseus: не в звонке (группа {say_chat}) или нет текста.",
                    )

            elif sub == "setgroup":
                cid = _parse_chat_id(rest.strip())
                if cid is None:
                    await _reply(event, "Odysseus: укажи chat_id. Пример: /odysseus setgroup -1003503983653")
                    return
                cid = await normalize_chat_id(client, cid)
                _set_owner_default_group(cid)
                await _reply(event, f"Odysseus: группа по умолчанию = {cid}.")

            elif sub == "groups":
                rows = await list_groups(client, limit=30)
                default = _owner_default_group()
                lines = ["Группы (📞 = активный звонок):"]
                for r in rows[:25]:
                    flag = "📞" if r["active_call"] else "  "
                    mark = " *" if r["chat_id"] == default else ""
                    lines.append(f"{flag} {r['chat_id']} | {r['title']}{mark}")
                lines.append(f"\n* группа по умолчанию")
                await _reply(event, "\n".join(lines))

            elif sub == "info":
                await _reply(event, f"Odysseus: собираю данные группы {target_chat}…")
                snap = await snapshot_group(client, target_chat)
                out = os.path.join(config.SESSIONS_DIR, f"group_{target_chat}.json")
                os.makedirs(config.SESSIONS_DIR, exist_ok=True)
                save_snapshot(out, snap)
                summary = {
                    "chat_id": snap.chat_id,
                    "title": snap.title,
                    "members": snap.members_count,
                    "active_call": snap.group_call.active,
                    "call_participants": snap.group_call.participants_count,
                    "saved": out,
                }
                await _reply(
                    event,
                    "Odysseus info:\n" + json.dumps(summary, ensure_ascii=False, indent=2),
                )

            else:
                await _reply(event, HELP)

        except NoActiveGroupCall:
            await _reply(
                event,
                f"Odysseus: pytgcalls не видит активный звонок в {target_chat}.\n"
                "Убедись что звонок запущен в этой группе, затем /odysseus join",
            )
        except ChatAdminRequiredError:
            await _reply(
                event,
                "Odysseus: нет прав создать звонок — нужно право «Управление видеочатами».\n"
                "Или запусти звонок вручную → /odysseus join",
            )
        except Exception as e:
            log.exception("command error")
            await _reply(event, f"Odysseus error: {e}")
