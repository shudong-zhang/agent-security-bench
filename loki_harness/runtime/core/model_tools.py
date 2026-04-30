"""Hermes-inspired runtime model tool helpers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import uuid
from pathlib import Path
from typing import Any


_thread_local = threading.local()


def run_coro_sync(coro):
    """Run a coroutine on a persistent per-thread event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=300)
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop
    return loop.run_until_complete(coro)


def coerce_tool_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    function = schema.get("function", {})
    parameters = function.get("parameters", {})
    properties = parameters.get("properties", {})
    coerced = dict(args)
    for key, prop in properties.items():
        if key not in coerced:
            continue
        coerced[key] = _coerce_value(coerced[key], prop.get("type"))
    return coerced


def maybe_persist_tool_result(
    *,
    content: str,
    output_dir: str | Path | None,
    turn: int,
    tool_name: str,
    tool_call_id: str,
    threshold_chars: int,
) -> str:
    if output_dir is None or len(content) <= threshold_chars:
        return content
    root = Path(output_dir) / "tool_results"
    root.mkdir(parents=True, exist_ok=True)
    file_name = f"turn_{turn:03d}_{tool_name}_{tool_call_id or uuid.uuid4().hex[:8]}.json"
    path = root / file_name
    path.write_text(content, encoding="utf-8")
    return json.dumps(
        {
            "persisted_output": True,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "path": str(path),
            "preview": content[:400],
        },
        ensure_ascii=False,
    )


def enforce_turn_budget(
    *,
    new_tool_messages: list[dict[str, Any]],
    output_dir: str | Path | None,
    turn: int,
    budget_chars: int,
    threshold_chars: int,
) -> None:
    total = sum(len(str(message.get("content", ""))) for message in new_tool_messages)
    if total <= budget_chars or output_dir is None:
        return
    largest = max(new_tool_messages, key=lambda message: len(str(message.get("content", ""))), default=None)
    if largest is None:
        return
    largest["content"] = maybe_persist_tool_result(
        content=str(largest.get("content", "")),
        output_dir=output_dir,
        turn=turn,
        tool_name="turn_budget",
        tool_call_id=largest.get("tool_call_id", ""),
        threshold_chars=threshold_chars,
    )


def maybe_compress_message_history(
    *,
    messages: list[dict[str, Any]],
    max_chars: int,
    preserve_last_messages: int = 8,
    summarizer=None,
) -> list[dict[str, Any]]:
    compressed = list(messages)
    while _message_chars(compressed) > max_chars and len(compressed) > preserve_last_messages + 2:
        compressed = _compress_once(compressed, preserve_last_messages, summarizer=summarizer)
        if compressed == messages:
            break
        messages = compressed
    return compressed


def parse_tool_call_fallback(content: str) -> tuple[str, list[dict[str, Any]]]:
    if "<tool_call>" not in content:
        return content, []
    extracted: list[dict[str, Any]] = []
    remaining = content
    while True:
        start = remaining.find("<tool_call>")
        end = remaining.find("</tool_call>")
        if start == -1 or end == -1 or end < start:
            break
        raw = remaining[start + len("<tool_call>"):end].strip()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("name"):
                extracted.append(
                    {
                        "id": parsed.get("id", f"call_{parsed['name']}"),
                        "type": "function",
                        "function": {
                            "name": parsed["name"],
                            "arguments": json.dumps(parsed.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                )
        except Exception:
            pass
        remaining = (remaining[:start] + remaining[end + len("</tool_call>"):]).strip()
    return remaining, extracted


def _coerce_value(value: Any, expected_type: Any) -> Any:
    if isinstance(expected_type, list):
        expected_type = next((item for item in expected_type if item != "null"), expected_type[0] if expected_type else None)
    if expected_type == "integer" and isinstance(value, str):
        return int(value) if value.strip().lstrip("-").isdigit() else value
    if expected_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if expected_type == "boolean" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return value


def _compress_once(messages: list[dict[str, Any]], preserve_last_messages: int, summarizer=None) -> list[dict[str, Any]]:
    head = messages[:1]
    tail = messages[-preserve_last_messages:]
    middle = messages[1:-preserve_last_messages]
    if not middle:
        return messages
    older = middle[: max(1, len(middle) // 2)]
    newer_middle = middle[len(older):]

    summary_lines = _build_summary_lines(older, summarizer=summarizer)
    if len(summary_lines) == 1:
        return head + newer_middle + tail
    summary = {"role": "system", "content": "\n".join(summary_lines)}
    return head + [summary] + newer_middle + tail


def _build_summary_lines(messages: list[dict[str, Any]], summarizer=None) -> list[str]:
    if summarizer is not None:
        try:
            summary = str(summarizer(messages)).strip()
        except Exception:
            summary = ""
        if summary:
            return ["[Compressed prior context]", summary[:2000]]

    summary_lines = ["[Compressed prior context]"]
    for message in messages:
        role = message.get("role", "unknown")
        content = _summarize_message_content(message)
        if content:
            summary_lines.append(f"- {role}: {content}")
    return summary_lines


def _summarize_message_content(message: dict[str, Any]) -> str:
    raw_content = str(message.get("content", "")).strip()
    if raw_content.startswith("[Compressed prior context]"):
        lines = [line.strip() for line in raw_content.splitlines()[1:] if line.strip()]
        informative = [line for line in lines if line not in {"- system:", "- assistant:", "- user:", "- tool:"}]
        return " | ".join(informative[:3]).replace("\n", " ")[:160]
    content = raw_content.replace("\n", " ")
    if content:
        return content[:160]
    if message.get("role") == "assistant" and message.get("tool_calls"):
        tools = ", ".join(
            call.get("function", {}).get("name", "unknown")
            for call in message.get("tool_calls", [])
            if isinstance(call, dict)
        )
        return f"tool_calls={tools}"
    return ""


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(message.get("content", ""))) for message in messages)
