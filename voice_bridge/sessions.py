"""Per-chat conversation state: message history, system prompt, brain session id.

Also stores owner-level defaults (default group) and a simple long-term memory
(facts / reminders) so Odysseus can act as a real assistant.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

from voice_bridge import config

MAX_TURNS = 24  # keep last N user/assistant messages to bound context


@dataclass
class ChatSession:
    chat_id: int
    system_prompt: str = field(default_factory=config.load_persona)
    history: List[dict] = field(default_factory=list)
    # opaque brain conversation id (Odysseus conversation / Odysseus thread)
    brain_session_id: str = ""
    muted: bool = False
    last_activity: float = field(default_factory=time.time)
    # default group for owner DM commands without explicit chat_id
    default_group_id: int = config.TG_TEST_GROUP_ID
    # simple long-term memory
    facts: List[str] = field(default_factory=list)
    reminders: List[dict] = field(default_factory=list)

    def messages(self) -> List[dict]:
        msgs = [{"role": "system", "content": self.system_prompt}]
        msgs.extend(self.history[-MAX_TURNS:])
        return msgs

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.last_activity = time.time()

    def add_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.last_activity = time.time()

    def reset(self) -> None:
        self.history.clear()
        self.brain_session_id = ""

    def remember(self, fact: str) -> None:
        fact = fact.strip()
        if fact and fact not in self.facts:
            self.facts.append(fact)

    def add_reminder(self, text: str, due_ts: float = 0.0) -> None:
        self.reminders.append({"text": text.strip(), "due_ts": due_ts, "done": False})

    def pending_reminders(self) -> List[dict]:
        now = time.time()
        return [r for r in self.reminders if not r["done"] and (r["due_ts"] <= 0 or r["due_ts"] <= now)]


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[int, ChatSession] = {}

    def get(self, chat_id: int) -> ChatSession:
        s = self._sessions.get(chat_id)
        if s is None:
            s = ChatSession(chat_id=chat_id)
            self._sessions[chat_id] = s
        return s

    def drop(self, chat_id: int) -> None:
        self._sessions.pop(chat_id, None)

    def all(self) -> List[ChatSession]:
        return list(self._sessions.values())


store = SessionStore()
