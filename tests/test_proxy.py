"""Unit + optional live tests for the anthropic_proxy translation layer.

Run unit only:        pytest tests/test_proxy.py
Run incl. live API:   RUN_LIVE=1 pytest tests/test_proxy.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proxy"))

import main  # noqa: E402


def test_system_lifted_into_top_level():
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
    }
    body = main.openai_to_anthropic(payload)
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] > 0


def test_multiple_system_messages_joined():
    payload = {
        "messages": [
            {"role": "system", "content": "a"},
            {"role": "system", "content": "b"},
            {"role": "user", "content": "x"},
        ]
    }
    body = main.openai_to_anthropic(payload)
    assert body["system"] == "a\n\nb"


def test_conversation_must_start_with_user():
    payload = {"messages": [{"role": "assistant", "content": "hello"}]}
    body = main.openai_to_anthropic(payload)
    assert body["messages"][0]["role"] == "user"


def test_content_parts_flattened():
    payload = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "part1 "},
                {"type": "text", "text": "part2"},
            ]},
        ]
    }
    body = main.openai_to_anthropic(payload)
    assert body["messages"][0]["content"] == "part1 part2"


def test_stop_sequences_mapped():
    payload = {"messages": [{"role": "user", "content": "x"}], "stop": "END"}
    body = main.openai_to_anthropic(payload)
    assert body["stop_sequences"] == ["END"]


# --------------------------------------------------------------------------
# Optional live smoke test against the real OpenModel endpoint.
# --------------------------------------------------------------------------
import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="live test disabled")
def test_live_non_stream_drops_thinking():
    payload = {
        "model": main.DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": "Отвечай одним словом."},
            {"role": "user", "content": "Скажи привет"},
        ],
    }
    body = main.openai_to_anthropic(payload)
    result = asyncio.run(main.non_stream_translate(body, main.DEFAULT_MODEL))
    content = result["choices"][0]["message"]["content"]
    assert content.strip(), "empty answer"
    # thinking must never leak into the answer
    assert "thinking" not in content.lower()
