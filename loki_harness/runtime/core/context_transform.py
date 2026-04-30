"""Context transform and compression pipeline for Loki runtime.

Inspired by Pi packages/agent transformContext:
- Operates at the AgentMessage level (before convert_to_llm)
- Handles: token estimation, compression, spilling large results, cache preservation
- transform_context is the main entry point called each turn before LLM invocation
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from loki_harness.runtime.core.messages import (
    AgentMessage,
    ArtifactMessage,
    AssistantMessage,
    CompressionSummaryMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

# Character-based estimate. For production, tiktoken or similar should be used.
_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[AgentMessage]) -> int:
    """Estimate token count for a list of AgentMessages."""
    total = 0
    for msg in messages:
        total += _estimate_message_tokens(msg)
    return total


def _estimate_message_tokens(msg: AgentMessage) -> int:
    """Estimate tokens for a single AgentMessage."""
    chars = 0
    match msg.role:
        case "user":
            chars = len(msg.content)
        case "system":
            chars = len(msg.content)
        case "assistant":
            chars = len(msg.content) + len(msg.thinking or "")
            for tc in msg.tool_calls:
                chars += len(tc.function.name) + len(tc.function.arguments)
        case "tool_result":
            chars = len(msg.content)
        case "compression_summary":
            chars = len(msg.content)
        case "artifact":
            chars = len(msg.label) + len(msg.content_preview)
        case _:
            chars = 200  # rough estimate for metadata messages
    return math.ceil(chars / _CHARS_PER_TOKEN)


def estimate_message_chars(messages: list[AgentMessage]) -> int:
    """Estimate total character count (for budget enforcement)."""
    total = 0
    for msg in messages:
        match msg.role:
            case "user" | "system" | "tool_result":
                total += len(msg.content)
            case "assistant":
                total += len(msg.content) + len(msg.thinking or "")
                for tc in msg.tool_calls:
                    total += len(tc.function.name) + len(tc.function.arguments)
            case "compression_summary":
                total += len(msg.content)
    return total


# ---------------------------------------------------------------------------
# Spilling — move large tool results to artifact references
# ---------------------------------------------------------------------------


def spill_large_messages(
    messages: list[AgentMessage],
    output_dir: str | Path | None = None,
    max_chars_per_message: int = 1600,
) -> list[AgentMessage]:
    """Replace large tool_result content with ArtifactMessage references.

    When tool results exceed max_chars_per_message, the content is written to
    disk and replaced with an ArtifactMessage pointing to the persisted file.
    """
    if output_dir is None:
        return messages

    root = Path(output_dir) / "tool_results"
    root.mkdir(parents=True, exist_ok=True)
    result: list[AgentMessage] = []

    for i, msg in enumerate(messages):
        if msg.role == "tool_result" and len(msg.content) > max_chars_per_message:
            file_name = f"spill_{msg.tool_name}_{msg.tool_call_id[:8]}.json"
            path = root / file_name
            path.write_text(msg.content, encoding="utf-8")
            preview = msg.content[:400]
            result.append(
                ArtifactMessage(
                    artifact_id=f"spill_{i}",
                    path=str(path),
                    label=f"Spilled result for {msg.tool_name}",
                    content_preview=preview,
                )
            )
            msg.content = f"[Persisted to {path}]\n{preview}"
        result.append(msg)

    return result


# ---------------------------------------------------------------------------
# Compression — summarise old messages into CompressionSummaryMessage
# ---------------------------------------------------------------------------


def _summarize_message(msg: AgentMessage, max_len: int = 120) -> str:
    """Create a one-line summary of an AgentMessage."""
    match msg.role:
        case "user":
            return f"user: {msg.content[:max_len].replace(chr(10), ' ')}"
        case "system":
            return f"system: {msg.content[:max_len].replace(chr(10), ' ')}"
        case "assistant":
            if msg.tool_calls:
                tools = ", ".join(tc.function.name for tc in msg.tool_calls)
                return f"assistant: tool_calls=[{tools}]"
            return f"assistant: {msg.content[:max_len].replace(chr(10), ' ')}"
        case "tool_result":
            return f"tool_result({msg.tool_name}): {msg.content[:max_len].replace(chr(10), ' ')}"
        case "compression_summary":
            return f"compression: {msg.content[:max_len].replace(chr(10), ' ')}"
        case _:
            return f"{msg.role}"


def compress_messages(
    messages: list[AgentMessage],
    preserve_last: int = 8,
    summarizer: Callable[[list[AgentMessage]], str] | None = None,
) -> list[AgentMessage]:
    """Compress old messages into a CompressionSummaryMessage.

    Keeps the first system message, preserves the last `preserve_last` messages,
    and compresses the middle into a summary.
    """
    if len(messages) <= preserve_last + 4:
        return messages

    # Find first system message
    head: list[AgentMessage] = []
    tail = messages[-preserve_last:]
    middle_start = 0
    for i, msg in enumerate(messages):
        if msg.role == "system":
            head.append(msg)
            middle_start = i + 1
            break

    if not head:
        head = messages[:1]
        middle_start = 1

    middle = messages[middle_start:-preserve_last]
    if len(middle) < 3:
        return messages

    # Split middle: older half gets compressed, newer half stays
    split = max(1, len(middle) // 2)
    older = middle[:split]
    newer = middle[split:]

    if summarizer is not None:
        try:
            summary_text = summarizer(older)
        except Exception:
            summary_text = "\n".join(_summarize_message(m) for m in older)
    else:
        summary_text = "\n".join(_summarize_message(m) for m in older)

    summary = CompressionSummaryMessage(
        content=summary_text[:2000],
        compressed_count=len(older),
        compressed_turn_range=None,
    )

    return head + [summary] + newer + tail


# ---------------------------------------------------------------------------
# Main transform entry point
# ---------------------------------------------------------------------------


def transform_context(
    messages: list[AgentMessage],
    *,
    max_tokens: int | None = None,
    max_chars: int | None = None,
    max_chars_per_message: int = 1600,
    preserve_last: int = 8,
    output_dir: str | Path | None = None,
    summarizer: Callable[[list[AgentMessage]], str] | None = None,
) -> list[AgentMessage]:
    """Transform agent context before LLM invocation.

    Pipeline:
    1. Spill large tool results to disk (if output_dir provided)
    2. Compress if over token/char budget

    This runs at the AgentMessage level, before convert_to_llm().
    """
    transformed = list(messages)

    # Step 1: Spill large messages
    if output_dir is not None:
        transformed = spill_large_messages(transformed, output_dir=output_dir, max_chars_per_message=max_chars_per_message)

    # Step 2: Compress if over budget
    if max_chars is not None:
        while estimate_message_chars(transformed) > max_chars and len(transformed) > preserve_last + 2:
            prev_len = len(transformed)
            transformed = compress_messages(transformed, preserve_last=preserve_last, summarizer=summarizer)
            if len(transformed) == prev_len:
                break

    if max_tokens is not None:
        while estimate_tokens(transformed) > max_tokens and len(transformed) > preserve_last + 2:
            prev_len = len(transformed)
            transformed = compress_messages(transformed, preserve_last=preserve_last, summarizer=summarizer)
            if len(transformed) == prev_len:
                break

    return transformed
