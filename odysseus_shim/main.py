"""
odysseus_shim
=============
Presents an OpenAI-compatible `POST /v1/chat/completions` to the voice_bridge,
and drives the real Odysseus brain underneath via its native REST API:

    OpenAI /v1/chat/completions  --->  Odysseus /api/session  +  /api/chat_stream

Odysseus owns conversation memory, so per logical `session_id` (the Telegram
chat id) we create ONE Odysseus session and afterwards send only the newest user
message. The system/persona prompt is injected on the first turn of a session.

Odysseus details (see repo routes/chat_routes.py, session_routes.py,
model_routes.py):
  * No OpenAI passthrough exists; the chat brain lives behind /api/chat*.
  * /api/chat_stream is multipart/form-data, returns SSE `data: {"delta": "..."}`
    lines terminated by `data: [DONE]`.
  * A custom OpenAI-compatible LLM is registered with POST /api/model-endpoints
    (base_url -> our anthropic_proxy), then referenced by a session.
  * Auth can be disabled with AUTH_ENABLED=false (we run on a private docker net).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ODYSSEUS_URL = os.environ.get("ODYSSEUS_URL", "http://odysseus:7000").rstrip("/")
ODYSSEUS_TOKEN = os.environ.get("ODYSSEUS_TOKEN", "").strip()
# base_url to register in Odysseus as the LLM provider (our anthropic_proxy)
PROVIDER_BASE_URL = os.environ.get("ANTHROPIC_PROXY_URL", "http://anthropic_proxy:9100/v1")
PROVIDER_NAME = os.environ.get("ODYSSEUS_PROVIDER_NAME", "anthropic_proxy")
MODEL = os.environ.get("BRAIN_MODEL", os.environ.get("OPENMODEL_MODEL", "deepseek-v4-flash"))
REQUEST_TIMEOUT = float(os.environ.get("SHIM_TIMEOUT", "60"))

app = FastAPI(title="odysseus_shim", version="1.1.0")


def _headers() -> dict[str, str]:
    h: dict[str, str] = {}
    if ODYSSEUS_TOKEN:
        h["Authorization"] = f"Bearer {ODYSSEUS_TOKEN}"
    return h



class OdysseusBridge:
    def __init__(self) -> None:
        self._endpoint_id: Optional[str] = None
        self._sessions: dict[str, dict[str, Any]] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_headers())

    async def ensure_endpoint(self) -> Optional[str]:
        if self._endpoint_id:
            return self._endpoint_id
        async with self._client() as c:
            try:
                r = await c.get(f"{ODYSSEUS_URL}/api/model-endpoints")
                if r.status_code < 400:
                    data = r.json()
                    items = data if isinstance(data, list) else data.get("endpoints", [])
                    for ep in items:
                        if str(ep.get("base_url", "")).rstrip("/") == PROVIDER_BASE_URL.rstrip("/"):
                            self._endpoint_id = ep.get("id")
                            return self._endpoint_id
            except Exception:
                pass
            try:
                r = await c.post(
                    f"{ODYSSEUS_URL}/api/model-endpoints",
                    data={
                        "name": PROVIDER_NAME,
                        "base_url": PROVIDER_BASE_URL,
                        "api_key": "",
                        "skip_probe": "true",
                        "model_type": "llm",
                        "endpoint_kind": "auto",
                        "shared": "true",
                    },
                )
                if r.status_code < 400:
                    body = r.json()
                    self._endpoint_id = body.get("id") or body.get("endpoint", {}).get("id")
            except Exception:
                pass
        return self._endpoint_id

    async def ensure_session(self, logical_id: str) -> dict[str, Any]:
        rec = self._sessions.get(logical_id)
        if rec:
            return rec
        endpoint_id = await self.ensure_endpoint()
        sid = ""
        async with self._client() as c:
            form = {
                "name": f"hermes-{logical_id}",
                "model": MODEL,
                "skip_validation": "true",
            }
            if endpoint_id:
                form["endpoint_id"] = endpoint_id
            else:
                form["endpoint_url"] = f"{PROVIDER_BASE_URL}/chat/completions"
            try:
                r = await c.post(f"{ODYSSEUS_URL}/api/session", data=form)
                if r.status_code < 400:
                    body = r.json()
                    sid = body.get("id") or body.get("session", {}).get("id") or ""
            except Exception:
                sid = ""
        if not sid:
            sid = str(uuid.uuid4())
        rec = {"ody": sid, "sys_sent": False}
        self._sessions[logical_id] = rec
        return rec



    async def stream_chat(
        self, logical_id: str, system_prompt: str, user_text: str
    ) -> AsyncGenerator[str, None]:
        rec = await self.ensure_session(logical_id)
        ody_session = rec["ody"]

        message = user_text
        if not rec["sys_sent"] and system_prompt:
            message = (
                "[Системная инструкция — следуй ей всю беседу]\n"
                f"{system_prompt}\n\n[Сообщение пользователя]\n{user_text}"
            )
            rec["sys_sent"] = True

        form = {"message": message, "session": ody_session, "mode": "chat",
                "use_rag": "false"}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_headers()) as c:
            async with c.stream(
                "POST", f"{ODYSSEUS_URL}/api/chat_stream", data=form
            ) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", "replace")
                    yield f"[odysseus error {resp.status_code}: {err[:200]}]"
                    return
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = obj.get("delta")
                    if isinstance(delta, str) and delta:
                        yield delta


bridge = OdysseusBridge()

def _chunk(cid: str, model: str, delta: dict[str, Any], finish: Optional[str] = None) -> str:
    obj = {
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _extract(payload: dict[str, Any]) -> tuple[str, str, str]:
    """Return (logical_id, system_prompt, last_user_text)."""
    logical_id = str(payload.get("session_id") or payload.get("user") or "default")
    sys_parts, last_user = [], ""
    for m in payload.get("messages", []):
        role, content = m.get("role"), m.get("content", "")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if role == "system":
            sys_parts.append(content)
        elif role == "user":
            last_user = content
    return logical_id, "\n\n".join(sys_parts), last_user


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "odysseus": ODYSSEUS_URL, "model": MODEL}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [
        {"id": MODEL, "object": "model", "created": 0, "owned_by": "odysseus"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    model = payload.get("model") or MODEL
    logical_id, system_prompt, user_text = _extract(payload)
    if not user_text:
        user_text = "."

    if payload.get("stream"):
        async def gen() -> AsyncGenerator[str, None]:
            cid = "chatcmpl-" + uuid.uuid4().hex
            yield _chunk(cid, model, {"role": "assistant"})
            async for delta in bridge.stream_chat(logical_id, system_prompt, user_text):
                yield _chunk(cid, model, {"content": delta})
            yield _chunk(cid, model, {}, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    parts = [d async for d in bridge.stream_chat(logical_id, system_prompt, user_text)]
    answer = "".join(parts)
    return JSONResponse({
        "id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })
