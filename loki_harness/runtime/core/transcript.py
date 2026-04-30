"""Transcript normalization helpers for training-ready runtime traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from loki_harness.targets.base import TargetExecutionResult
from loki_harness.trace_store import RunTraceSink


def normalize_execution_transcript(execution_result: TargetExecutionResult) -> list[dict[str, Any]]:
    """Return a canonical transcript for external or internal agent execution."""

    if execution_result.transcript:
        return execution_result.transcript

    transcript: list[dict[str, Any]] = []
    for index, message in enumerate(execution_result.messages, start=1):
        transcript.append(
            {
                "type": "message",
                "turn_id": f"turn_{index}",
                "role": message.get("role", "assistant"),
                "content": message.get("content", ""),
            }
        )

    for index, call in enumerate(execution_result.tool_calls, start=1):
        transcript.append(
            {
                "type": "tool_call",
                "turn_id": call.get("turn_id", f"turn_{index}"),
                "tool_name": call.get("name", "unknown"),
                "arguments": call.get("arguments", {}),
                "source": call.get("source", "adapter_fallback"),
            }
        )

    if not transcript and execution_result.raw_log.strip():
        transcript.append(
            {
                "type": "message",
                "turn_id": "turn_1",
                "role": "system",
                "content": execution_result.raw_log[:4000],
            }
        )

    return transcript


def emit_transcript_events(
    trace_sink: RunTraceSink,
    *,
    path_id: str | None,
    transcript: list[dict[str, Any]],
) -> None:
    for index, item in enumerate(transcript, start=1):
        item_type = item.get("type", "unknown")
        turn_id = item.get("turn_id")
        refs = {}
        if item.get("tool_call_id"):
            refs["tool_call_id"] = str(item["tool_call_id"])

        if item_type == "message":
            trace_sink.emit(
                stage="runtime",
                event_type="TranscriptMessageObserved",
                actor=item.get("role", "assistant"),
                path_id=path_id,
                turn_id=turn_id,
                data={
                    "index": index,
                    "role": item.get("role", "assistant"),
                    "content": item.get("content", ""),
                },
            )
            continue

        if item_type == "tool_call":
            trace_sink.emit(
                stage="runtime",
                event_type="TranscriptToolCallObserved",
                actor="target_adapter",
                path_id=path_id,
                turn_id=turn_id,
                refs=refs or None,
                data={
                    "index": index,
                    "tool_name": item.get("tool_name", "unknown"),
                    "arguments": item.get("arguments", {}),
                    "source": item.get("source", "unknown"),
                },
            )
            continue

        if item_type == "tool_result":
            trace_sink.emit(
                stage="runtime",
                event_type="TranscriptToolResultObserved",
                actor="target_adapter",
                path_id=path_id,
                turn_id=turn_id,
                refs=refs or None,
                data={
                    "index": index,
                    "tool_name": item.get("tool_name", "unknown"),
                    "result": item.get("result", ""),
                },
            )
            continue

        trace_sink.emit(
            stage="runtime",
            event_type="TranscriptEventObserved",
            actor="target_adapter",
            path_id=path_id,
            turn_id=turn_id,
            refs=refs or None,
            data={"index": index, "payload": item},
        )


def write_transcript(path: str | Path, transcript: list[dict[str, Any]]) -> None:
    Path(path).write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
