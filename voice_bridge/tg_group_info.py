"""Telegram group/channel introspection: members, active video chat, metadata."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from telethon import TelegramClient, utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import Channel, Chat, User

log = logging.getLogger("tg_group")


@dataclass
class GroupCallInfo:
    active: bool = False
    call_id: Optional[int] = None
    title: Optional[str] = None
    participants_count: Optional[int] = None
    unmuted_video_limit: Optional[int] = None
    version: Optional[int] = None


@dataclass
class MemberInfo:
    id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    is_bot: bool
    phone: Optional[str] = None


@dataclass
class GroupSnapshot:
    chat_id: int
    title: str
    username: Optional[str]
    type: str  # group | supergroup | channel
    members_count: Optional[int]
    group_call: GroupCallInfo = field(default_factory=GroupCallInfo)
    members: list[MemberInfo] = field(default_factory=list)
    exported_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _group_call_from_full(full_chat) -> GroupCallInfo:  # noqa: ANN001
    call = getattr(full_chat, "call", None)
    if not call:
        return GroupCallInfo(active=False)
    return GroupCallInfo(
        active=True,
        call_id=getattr(call, "id", None),
        title=getattr(call, "title", None),
        participants_count=getattr(call, "participants_count", None),
        unmuted_video_limit=getattr(call, "unmuted_video_limit", None),
        version=getattr(call, "version", None),
    )


async def normalize_chat_id(client: TelegramClient, chat_id: int) -> int:
    """Resolve legacy/basic group ids to the real supergroup peer id (-100…).

    If the entity cannot be fetched (private chat, no access), keep the id as-is.
    """
    try:
        entity = await client.get_entity(chat_id)
        return utils.get_peer_id(entity)
    except Exception as exc:
        log.warning("normalize_chat_id %s failed (%s), using raw id", chat_id, exc)
        return chat_id


async def get_group_call_info(client: TelegramClient, chat_id: int) -> GroupCallInfo:
    """Return whether a group/supergroup has an active group video/voice call."""
    try:
        entity = await client.get_entity(chat_id)
    except Exception as exc:
        log.warning("get_group_call_info cannot fetch entity %s: %s", chat_id, exc)
        return GroupCallInfo(active=False)
    if isinstance(entity, Channel):
        full = await client(GetFullChannelRequest(entity))
        return await _group_call_from_full(full.full_chat)
    if isinstance(entity, Chat):
        full = await client(GetFullChatRequest(entity.id))
        return await _group_call_from_full(full.full_chat)
    return GroupCallInfo(active=False)


async def snapshot_group(
    client: TelegramClient,
    chat_id: int,
    *,
    max_members: int = 500,
) -> GroupSnapshot:
    """Fetch metadata, active call status, and member list for a group chat."""
    entity = await client.get_entity(chat_id)
    title = getattr(entity, "title", None) or str(chat_id)
    username = getattr(entity, "username", None)
    if isinstance(entity, Channel):
        chat_type = "channel" if entity.broadcast else "supergroup"
        full = await client(GetFullChannelRequest(entity))
        gc = await _group_call_from_full(full.full_chat)
        members_count = getattr(full.full_chat, "participants_count", None)
    elif isinstance(entity, Chat):
        chat_type = "group"
        full = await client(GetFullChatRequest(entity.id))
        gc = await _group_call_from_full(full.full_chat)
        members_count = getattr(full.full_chat, "participants_count", None)
    else:
        chat_type = "unknown"
        gc = GroupCallInfo(active=False)
        members_count = None

    members: list[MemberInfo] = []
    try:
        async for p in client.iter_participants(entity, limit=max_members):
            if not isinstance(p, User):
                continue
            members.append(
                MemberInfo(
                    id=p.id,
                    username=p.username,
                    first_name=p.first_name,
                    last_name=p.last_name,
                    is_bot=bool(p.bot),
                    phone=p.phone if getattr(p, "phone", None) else None,
                )
            )
    except Exception as e:
        log.warning("iter_participants failed for %s: %s", chat_id, e)

    return GroupSnapshot(
        chat_id=chat_id,
        title=title,
        username=username,
        type=chat_type,
        members_count=members_count or len(members),
        group_call=gc,
        members=members,
    )


async def list_groups(client: TelegramClient, limit: int = 100) -> list[dict[str, Any]]:
    """List group/supergroup dialogs with active-call flag."""
    out: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs(limit=limit):
        ent = dialog.entity
        if not isinstance(ent, (Chat, Channel)):
            continue
        if isinstance(ent, Channel) and ent.broadcast:
            continue  # skip broadcast channels
        gc = await get_group_call_info(client, dialog.id)
        out.append({
            "chat_id": dialog.id,
            "title": dialog.title or dialog.name,
            "username": getattr(ent, "username", None),
            "type": "supergroup" if isinstance(ent, Channel) else "group",
            "unread": dialog.unread_count,
            "active_call": gc.active,
            "call_participants": gc.participants_count,
        })
    return out


def save_snapshot(path: str, snap: GroupSnapshot) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap.to_dict(), f, ensure_ascii=False, indent=2)
