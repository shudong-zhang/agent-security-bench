"""AgentMessage / LLMMessage layered message model for Loki runtime.

Inspired by Pi packages/agent: the agent loop works at the AgentMessage level,
and convert_to_llm() transforms AgentMessage -> LLMMessage only at the LLM call boundary.

AgentMessage  = full runtime message space (thinking, tool_result, artifact,
                subagent_span, compression_summary, security_label, etc.)
LLMMessage    = what the model actually sees (user, assistant, tool_result, system)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass(slots=True)
class ImageContent:
    type: Literal["image"] = "image"
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCall:
    id: str = ""
    type: Literal["function"] = "function"
    function: ToolCallFunction = field(default_factory=lambda: ToolCallFunction())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        func = data.get("function", {})
        return cls(
            id=data.get("id", ""),
            type=data.get("type", "function"),
            function=ToolCallFunction(
                name=func.get("name", ""),
                arguments=func.get("arguments", "{}"),
            ),
        )


@dataclass(slots=True)
class ToolCallFunction:
    name: str = ""
    arguments: str = "{}"


# ---------------------------------------------------------------------------
# AgentMessage types — the full runtime message space
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class UserMessage:
    """A message from the user / orchestrator to the agent."""
    role: Literal["user"] = "user"
    content: str = ""
    timestamp: str | None = None


@dataclass(slots=True)
class AssistantMessage:
    """An assistant response, possibly with thinking and tool calls."""
    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str | None = None
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None
    error_message: str | None = None
    timestamp: str | None = None


@dataclass(slots=True)
class ToolResultMessage:
    """Result of a tool execution returned to the model."""
    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    content: str = ""
    details: Any = None
    is_error: bool = False
    timestamp: str | None = None


@dataclass(slots=True)
class SystemMessage:
    """System prompt / instruction message."""
    role: Literal["system"] = "system"
    content: str = ""
    timestamp: str | None = None


# ---------------------------------------------------------------------------
# Loki extension message types — not visible to the LLM
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CompressionSummaryMessage:
    """Marker that prior context was compressed into this summary."""
    role: Literal["compression_summary"] = "compression_summary"
    content: str = ""
    compressed_count: int = 0
    compressed_turn_range: tuple[int, int] | None = None


@dataclass(slots=True)
class SubagentSpanMessage:
    """Span marker for subagent lifecycle (start/end)."""
    role: Literal["subagent_span"] = "subagent_span"
    agent_id: str = ""
    parent_agent_id: str | None = None
    span_id: str = ""
    action: Literal["start", "end"] = "start"
    subagent_name: str = ""


@dataclass(slots=True)
class SecurityLabelMessage:
    """Security classification label on content."""
    role: Literal["security_label"] = "security_label"
    label: str = ""
    confidence: float = 0.0
    source: str = ""


@dataclass(slots=True)
class VerifierObservationMessage:
    """Observation from the verifier during or after a run."""
    role: Literal["verifier_observation"] = "verifier_observation"
    observation: str = ""
    turn_id: str | None = None
    rule: str = ""


@dataclass(slots=True)
class PermissionEventMessage:
    """Permission request/response event."""
    role: Literal["permission_event"] = "permission_event"
    tool_name: str = ""
    granted: bool = False
    reason: str = ""


@dataclass(slots=True)
class ArtifactMessage:
    """Reference to a persisted artifact file."""
    role: Literal["artifact"] = "artifact"
    artifact_id: str = ""
    path: str = ""
    label: str = ""
    content_preview: str = ""


# ---------------------------------------------------------------------------
# Union type alias
# ---------------------------------------------------------------------------

AgentMessage = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | SystemMessage
    | CompressionSummaryMessage
    | SubagentSpanMessage
    | SecurityLabelMessage
    | VerifierObservationMessage
    | PermissionEventMessage
    | ArtifactMessage
)

# LLMMessage = subset of AgentMessage that the model understands
LLMMessage = UserMessage | AssistantMessage | ToolResultMessage | SystemMessage

# ---------------------------------------------------------------------------
# convert_to_llm — transform boundary
# ---------------------------------------------------------------------------


def convert_to_llm(messages: list[AgentMessage]) -> list[LLMMessage]:
    """Convert AgentMessage list to LLMMessage list for model consumption.

    Extension messages (compression_summary, subagent_span, security_label,
    verifier_observation, permission_event, artifact) are mapped or filtered:
    - compression_summary -> system message
    - verifier_observation -> system message
    - everything else -> filtered out
    """
    result: list[LLMMessage] = []
    for msg in messages:
        match msg.role:
            case "user":
                result.append(msg)
            case "assistant":
                result.append(msg)
            case "tool_result":
                result.append(msg)
            case "system":
                result.append(msg)
            case "compression_summary":
                result.append(
                    SystemMessage(
                        content=f"[Compressed prior context — {msg.compressed_count} messages]\n{msg.content}"
                    )
                )
            case "verifier_observation":
                result.append(
                    SystemMessage(content=f"[Verifier: {msg.rule}] {msg.observation}")
                )
            case "subagent_span" | "security_label" | "permission_event" | "artifact":
                # These are metadata-only, not visible to LLM
                continue
            case _:
                continue
    return result


# ---------------------------------------------------------------------------
# Serialization helpers — bridge between AgentMessage and dict
# ---------------------------------------------------------------------------


def agent_message_to_dict(msg: AgentMessage) -> dict[str, Any]:
    """Serialize any AgentMessage to a plain dict (for JSON, trace store, etc.)."""
    base: dict[str, Any] = {"role": msg.role}

    match msg.role:
        case "user":
            base["content"] = msg.content
        case "assistant":
            base["content"] = msg.content
            if msg.tool_calls:
                base["tool_calls"] = [tc.to_dict() for tc in msg.tool_calls]
            if msg.thinking:
                base["thinking"] = msg.thinking
            if msg.stop_reason:
                base["stop_reason"] = msg.stop_reason
            if msg.error_message:
                base["error_message"] = msg.error_message
        case "tool_result":
            base["tool_call_id"] = msg.tool_call_id
            base["tool_name"] = msg.tool_name
            base["content"] = msg.content
            if msg.is_error:
                base["is_error"] = True
        case "system":
            base["content"] = msg.content
        case "compression_summary":
            base["content"] = msg.content
            base["compressed_count"] = msg.compressed_count
        case "subagent_span":
            base["agent_id"] = msg.agent_id
            base["parent_agent_id"] = msg.parent_agent_id
            base["span_id"] = msg.span_id
            base["action"] = msg.action
            base["subagent_name"] = msg.subagent_name
        case "security_label":
            base["label"] = msg.label
            base["confidence"] = msg.confidence
            base["source"] = msg.source
        case "verifier_observation":
            base["observation"] = msg.observation
            base["rule"] = msg.rule
        case "permission_event":
            base["tool_name"] = msg.tool_name
            base["granted"] = msg.granted
            base["reason"] = msg.reason
        case "artifact":
            base["artifact_id"] = msg.artifact_id
            base["path"] = msg.path
            base["label"] = msg.label
            base["content_preview"] = msg.content_preview

    if msg.timestamp:
        base["timestamp"] = msg.timestamp
    return base


def dict_to_agent_message(data: dict[str, Any]) -> AgentMessage:
    """Deserialize a plain dict back to the appropriate AgentMessage type."""
    role = data.get("role", "user")
    ts = data.get("timestamp")

    match role:
        case "user":
            return UserMessage(content=str(data.get("content", "")), timestamp=ts)
        case "assistant":
            tool_calls_raw = data.get("tool_calls", [])
            return AssistantMessage(
                content=str(data.get("content", "")),
                tool_calls=[ToolCall.from_dict(tc) if isinstance(tc, dict) else tc for tc in tool_calls_raw],
                thinking=data.get("thinking"),
                stop_reason=data.get("stop_reason"),
                error_message=data.get("error_message"),
                timestamp=ts,
            )
        case "tool_result":
            return ToolResultMessage(
                tool_call_id=str(data.get("tool_call_id", "")),
                tool_name=str(data.get("tool_name", "")),
                content=str(data.get("content", "")),
                is_error=bool(data.get("is_error", False)),
                timestamp=ts,
            )
        case "system":
            return SystemMessage(content=str(data.get("content", "")), timestamp=ts)
        case "compression_summary":
            return CompressionSummaryMessage(
                content=str(data.get("content", "")),
                compressed_count=int(data.get("compressed_count", 0)),
            )
        case "subagent_span":
            return SubagentSpanMessage(
                agent_id=str(data.get("agent_id", "")),
                parent_agent_id=data.get("parent_agent_id"),
                span_id=str(data.get("span_id", "")),
                action=data.get("action", "start"),
                subagent_name=str(data.get("subagent_name", "")),
            )
        case "security_label":
            return SecurityLabelMessage(
                label=str(data.get("label", "")),
                confidence=float(data.get("confidence", 0.0)),
                source=str(data.get("source", "")),
            )
        case "verifier_observation":
            return VerifierObservationMessage(
                observation=str(data.get("observation", "")),
                turn_id=data.get("turn_id"),
                rule=str(data.get("rule", "")),
            )
        case "permission_event":
            return PermissionEventMessage(
                tool_name=str(data.get("tool_name", "")),
                granted=bool(data.get("granted", False)),
                reason=str(data.get("reason", "")),
            )
        case "artifact":
            return ArtifactMessage(
                artifact_id=str(data.get("artifact_id", "")),
                path=str(data.get("path", "")),
                label=str(data.get("label", "")),
                content_preview=str(data.get("content_preview", "")),
            )
        case _:
            return UserMessage(content=str(data.get("content", "")), timestamp=ts)


def dicts_to_agent_messages(dicts: list[dict[str, Any]]) -> list[AgentMessage]:
    """Convert a list of OpenAI-style dicts to AgentMessage list."""
    return [dict_to_agent_message(d) for d in dicts]


def agent_messages_to_dicts(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Convert AgentMessage list back to OpenAI-style dicts (for trace/compat)."""
    return [agent_message_to_dict(m) for m in messages]


def llm_message_to_openai_dict(msg: LLMMessage) -> dict[str, Any]:
    """Convert a single LLMMessage to an OpenAI-compatible dict."""
    result: dict[str, Any] = {"role": msg.role}

    match msg.role:
        case "user" | "system":
            result["content"] = msg.content
        case "assistant":
            result["content"] = msg.content
            if msg.tool_calls:
                result["tool_calls"] = [tc.to_dict() for tc in msg.tool_calls]
        case "tool_result":
            result["role"] = "tool"
            result["tool_call_id"] = msg.tool_call_id
            result["content"] = msg.content

    return result


def llm_messages_to_openai_dicts(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert LLMMessage list to OpenAI-compatible dict list."""
    return [llm_message_to_openai_dict(m) for m in messages]
