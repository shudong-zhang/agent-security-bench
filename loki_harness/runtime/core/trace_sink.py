"""Runtime event sink contract — updated for RuntimeEvent lifecycle.

Provides:
- RuntimeTraceSink: legacy protocol (backward compat)
- InMemoryRuntimeTraceSink: collects legacy RuntimeEventRecord
- EventBridgeSink: bridges RuntimeEvent → legacy emit()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from loki_harness.runtime.core.events import RuntimeEvent, event_to_dict


# ---------------------------------------------------------------------------
# Legacy types (backward compat)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuntimeEventRecord:
    stage: str
    event_type: str
    actor: str
    data: dict[str, Any] = field(default_factory=dict)
    path_id: str | None = None
    turn_id: str | None = None
    refs: dict[str, str] = field(default_factory=dict)


class RuntimeTraceSink(Protocol):
    """Legacy protocol: emit raw event fields.

    New code should use RuntimeEventSink from events.py instead.
    """

    def emit(
        self,
        *,
        stage: str,
        event_type: str,
        actor: str,
        data: dict,
        path_id: str | None = None,
        turn_id: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> None:
        """Consume a runtime event emitted from the inner agent loop."""


# ---------------------------------------------------------------------------
# In-memory collector (legacy format)
# ---------------------------------------------------------------------------


class InMemoryRuntimeTraceSink:
    """Collect runtime events in legacy format before replaying into the run trace."""

    def __init__(self):
        self.events: list[RuntimeEventRecord] = []

    def emit(
        self,
        *,
        stage: str,
        event_type: str,
        actor: str,
        data: dict,
        path_id: str | None = None,
        turn_id: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> None:
        self.events.append(
            RuntimeEventRecord(
                stage=stage,
                event_type=event_type,
                actor=actor,
                data=data,
                path_id=path_id,
                turn_id=turn_id,
                refs=refs or {},
            )
        )


# ---------------------------------------------------------------------------
# Bridge: RuntimeEvent → legacy RuntimeTraceSink
# ---------------------------------------------------------------------------


class EventBridgeSink:
    """Adapts RuntimeEvent to a legacy RuntimeTraceSink.

    Usage:
        bridge = EventBridgeSink(legacy_trace_sink)
        await bridge.emit(some_runtime_event)
    """

    def __init__(self, trace_sink: RuntimeTraceSink | None = None):
        self._trace_sink = trace_sink

    async def emit(self, event: RuntimeEvent) -> None:
        if self._trace_sink is None:
            return
        stage, event_type, actor, data = _map_event(event)
        self._trace_sink.emit(
            stage=stage,
            event_type=event_type,
            actor=actor,
            data=data,
        )


def _map_event(event: RuntimeEvent) -> tuple[str, str, str, dict[str, Any]]:
    """Map a RuntimeEvent to legacy emit() parameters."""
    # Use event_to_dict for data, derive stage/actor from event type
    data = event_to_dict(event)
    match event.type:
        case "agent_start":
            return ("runtime", "AgentStarted", "runtime_loop", data)
        case "agent_end":
            return ("runtime", "AgentEnded", "runtime_loop", data)
        case "turn_start":
            return ("runtime", "TurnStarted", "runtime_loop", data)
        case "turn_end":
            return ("runtime", "TurnEnded", "runtime_loop", data)
        case "message_start":
            return ("runtime", "MessageStarted", "runtime_loop", data)
        case "message_update":
            return ("runtime", "MessageUpdated", "runtime_loop", data)
        case "message_end":
            return ("runtime", "MessageEnded", "runtime_loop", data)
        case "tool_execution_start":
            return ("runtime", "ToolCallStarted", "assistant", data)
        case "tool_execution_update":
            return ("runtime", "ToolCallUpdated", "tool_runtime", data)
        case "tool_execution_end":
            return ("runtime", "ToolCallFinished", "tool_runtime", data)
        case _:
            return ("runtime", "UnknownEvent", "runtime_loop", data)
