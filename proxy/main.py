"""
anthropic_proxy
================
Tiny translation gateway that lets any OpenAI-Chat-Completions client talk to
OpenModel.ai, which actually speaks the Anthropic Messages API.

  OpenAI  POST /v1/chat/completions   --->  Anthropic POST /v1/messages

Key behaviour:
  * `system` role messages are lifted into the Anthropic top-level `system` field.
  * `deepseek-v4-flash` is a reasoning model: it emits `thinking` content blocks
    before the real `text`. We DROP thinking/signature deltas so the downstream
    consumer (and ultimately the TTS) only ever sees the spoken answer.
  * Both streaming (SSE) and non-streaming responses are supported.

Run:  uvicorn main:app --host 0.0.0.0 --port 9100
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OPENMODEL_BASE = os.environ.get("OPENMODEL_BASE", "https://api.openmodel.ai").rstrip("/")
OPENMODEL_API_KEY = os.environ.get("OPENMODEL_API_KEY", "")
DEFAULT_MODEL = os.environ.get("OPENMODEL_MODEL", "deepseek-v4-flash")
DEFAULT_MAX_TOKENS = int(os.environ.get("OPENMODEL_MAX_TOKENS", "800"))
ANTHROPIC_VERSION = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")
# Optional outbound proxy (for restricted networks)
OUTBOUND_PROXY = os.environ.get("LLM_OUTBOUND_PROXY", "") or None
REQUEST_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))

app = FastAPI(title="anthropic_proxy", version="1.0.0")


def _anthropic_headers() -> dict[str, str]:
    return {
        "x-api-key": OPENMODEL_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _split_content(content: Any) -> str:
    """OpenAI message content may be a string or a list of parts. Flatten to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") in ("text", "input_text") and "text" in p:
                    parts.append(str(p["text"]))
                elif "content" in p:
                    parts.append(str(p["content"]))
            else:
                parts.append(str(p))
        return "".join(parts)
    return str(content)


def openai_to_anthropic(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI chat-completions body into an Anthropic messages body."""
    system_chunks: list[str] = []
    messages: list[dict[str, Any]] = []

    for m in payload.get("messages", []):
        role = m.get("role")
        text = _split_content(m.get("content"))
        if role == "system":
            if text:
                system_chunks.append(text)
            continue
        if role == "tool":
            # Represent tool output as a user message so the model keeps context.
            messages.append({"role": "user", "content": text})
            continue
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": text})

    # Anthropic requires the conversation to start with a user turn.
    if not messages or messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "."})

    body: dict[str, Any] = {
        "model": payload.get("model") or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": int(payload.get("max_tokens") or DEFAULT_MAX_TOKENS),
        "stream": bool(payload.get("stream", False)),
    }
    if system_chunks:
        body["system"] = "\n\n".join(system_chunks)
    if "temperature" in payload and payload["temperature"] is not None:
        body["temperature"] = payload["temperature"]
    if "top_p" in payload and payload["top_p"] is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("stop"):
        stop = payload["stop"]
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    # OpenModel resolves the model via the body; force its real id from env if the
    # caller sent a placeholder.
    body["model"] = DEFAULT_MODEL if body["model"] in ("", None) else body["model"]
    return body


def _chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def stream_translate(
    anthropic_body: dict[str, Any], model: str
) -> AsyncGenerator[str, None]:
    cid = "chatcmpl-" + uuid.uuid4().hex
    yield _chunk(cid, model, {"role": "assistant"})

    in_text_block = False  # are we currently inside a `text` content block?
    finish_reason = "stop"

    client_kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
    if OUTBOUND_PROXY:
        client_kwargs["proxy"] = OUTBOUND_PROXY

    async with httpx.AsyncClient(**client_kwargs) as client:
        async with client.stream(
            "POST",
            f"{OPENMODEL_BASE}/v1/messages",
            headers=_anthropic_headers(),
            json=anthropic_body,
        ) as resp:
            if resp.status_code >= 400:
                err_text = (await resp.aread()).decode("utf-8", "replace")
                yield _chunk(
                    cid, model,
                    {"content": f"[proxy upstream error {resp.status_code}: {err_text[:300]}]"},
                )
                yield _chunk(cid, model, {}, finish="stop")
                yield "data: [DONE]\n\n"
                return

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue

                etype = evt.get("type")
                if etype == "content_block_start":
                    block = evt.get("content_block", {})
                    in_text_block = block.get("type") == "text"
                elif etype == "content_block_stop":
                    in_text_block = False
                elif etype == "content_block_delta":
                    delta = evt.get("delta", {})
                    dtype = delta.get("type")
                    # Only forward real answer text. Drop thinking_delta / signature_delta.
                    if dtype == "text_delta" and in_text_block:
                        text = delta.get("text", "")
                        if text:
                            yield _chunk(cid, model, {"content": text})
                elif etype == "message_delta":
                    sr = evt.get("delta", {}).get("stop_reason")
                    if sr == "max_tokens":
                        finish_reason = "length"
                elif etype == "message_stop":
                    break

    yield _chunk(cid, model, {}, finish=finish_reason)
    yield "data: [DONE]\n\n"


async def non_stream_translate(anthropic_body: dict[str, Any], model: str) -> dict[str, Any]:
    client_kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
    if OUTBOUND_PROXY:
        client_kwargs["proxy"] = OUTBOUND_PROXY

    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.post(
            f"{OPENMODEL_BASE}/v1/messages",
            headers=_anthropic_headers(),
            json={**anthropic_body, "stream": False},
        )
        resp.raise_for_status()
        data = resp.json()

    text_parts = [
        b.get("text", "")
        for b in data.get("content", [])
        if b.get("type") == "text"
    ]
    answer = "".join(text_parts)

    stop_reason = data.get("stop_reason")
    finish = "length" if stop_reason == "max_tokens" else "stop"
    usage = data.get("usage", {})
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "upstream": OPENMODEL_BASE, "model": DEFAULT_MODEL}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": DEFAULT_MODEL, "object": "model", "created": 0, "owned_by": "openmodel"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    model = payload.get("model") or DEFAULT_MODEL
    anthropic_body = openai_to_anthropic(payload)

    if payload.get("stream"):
        return StreamingResponse(
            stream_translate(anthropic_body, model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        result = await non_stream_translate(anthropic_body, model)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        return JSONResponse(
            status_code=e.response.status_code,
            content={"error": {"message": f"upstream: {body}", "type": "upstream_error"}},
        )
    return JSONResponse(result)
