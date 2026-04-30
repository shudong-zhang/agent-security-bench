"""RuntimeEvent standard lifecycle for Loki agent runtime.

Inspired by Pi packages/agent AgentEvent:
- agent_start / agent_end
- turn_start / turn_end (one turn = one assistant response + tool calls/results)
- message_start / message_update (streaming) / message_end
- tool_execution_start / tool_execution_update / tool_execution_end

Events are emitted by LokiAgentLoop and consumed by trace sinks, UI, or verifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from loki_harness.runtime.core.messages import AgentMessage, ToolResultMessage


# ---------------------------------------------------------------------------
# Event types — discriminated union on `type`
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentStartEvent:
    """Agent loop has started processing."""
    type: Literal["agent_start"] = "agent_start"
    agent_id: str | None = None
    parent_agent_id: str | None = None


@dataclass(slots=True)
class AgentEndEvent:
    """Agent loop has finished (success, error, or abort)."""
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage] = field(default_factory=list)
    stop_reason: Literal["finished", "max_turns", "error", "aborted"] = "finished"
    agent_id: str | None = None


@dataclass(slots=True)
class TurnStartEvent:
    """A new turn is starting (one LLM call + its tool executions)."""
    type: Literal["turn_start"] = "turn_start"
    turn_number: int = 0


@dataclass(slots=True)
class TurnEndEvent:
    """A turn has completed with the assistant message and any tool results."""
    type: Literal["turn_end"] = "turn_end"
    turn_number: int = 0
    message: AgentMessage | None = None
    tool_results: list[ToolResultMessage] = field(default_factory=list)


@dataclass(slots=True)
class MessageStartEvent:
    """A new message is being added to the transcript."""
    type: Literal["message_start"] = "message_start"
    message: AgentMessage | None = None


@dataclass(slots=True)
class MessageUpdateEvent:
    """A streaming message has been updated (partial content/tool_calls)."""
    type: Literal["message_update"] = "message_update"
    message: AgentMessage | None = None
    delta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MessageEndEvent:
    """A message has been finalized in the transcript."""
    type: Literal["message_end"] = "message_end"
    message: AgentMessage | None = None


@dataclass(slots=True)
class ToolExecutionStartEvent:
    """A tool call is about to be executed."""
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionUpdateEvent:
    """A running tool call has produced a partial update."""
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = ""
    tool_name: str = ""
    partial_result: Any = None


@dataclass(slots=True)
class ToolExecutionEndEvent:
    """A tool call has finished executing."""
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = ""
    tool_name: str = ""
    result: Any = None
    is_error: bool = False
    terminate: bool = False


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

RuntimeEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)

# ---------------------------------------------------------------------------
# Event sink protocol
# ---------------------------------------------------------------------------


class RuntimeEventSink(Protocol):
    """Protocol for consuming runtime events."""

    async def emit(self, event: RuntimeEvent) -> None:
        """Consume a runtime event."""


# ---------------------------------------------------------------------------
# In-memory event sink (for collection during a run)
# ---------------------------------------------------------------------------


class InMemoryRuntimeEventSink:
    """Collect runtime events in memory for later replay or serialization."""

    def __init__(self):
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()


# ---------------------------------------------------------------------------
# Event-to-dict serialization (for trace store compat)
# ---------------------------------------------------------------------------


def event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    """Serialize a RuntimeEvent to a plain dict for trace storage."""
    from loki_harness.runtime.core.messages import agent_message_to_dict

    base: dict[str, Any] = {"event_type": event.type}

    match event.type:
        case "agent_start":
            base["agent_id"] = event.agent_id
            base["parent_agent_id"] = event.parent_agent_id
        case "agent_end":
            base["stop_reason"] = event.stop_reason
            base["message_count"] = len(event.messages)
            base["agent_id"] = event.agent_id
        case "turn_start":
            base["turn_number"] = event.turn_number
        case "turn_end":
            base["turn_number"] = event.turn_number
            base["tool_result_count"] = len(event.tool_results)
        case "message_start":
            if event.message is not None:
                base["message"] = agent_message_to_dict(event.message)
        case "message_update":
            base["delta"] = event.delta
        case "message_end":
            if event.message is not None:
                base["message"] = agent_message_to_dict(event.message)
        case "tool_execution_start":
            base["tool_call_id"] = event.tool_call_id
            base["tool_name"] = event.tool_name
            base["args"] = event.args
        case "tool_execution_update":
            base["tool_call_id"] = event.tool_call_id
            base["tool_name"] = event.tool_name
        case "tool_execution_end":
            base["tool_call_id"] = event.tool_call_id
            base["tool_name"] = event.tool_name
            base["is_error"] = event.is_error
            base["terminate"] = event.terminate

    return base
