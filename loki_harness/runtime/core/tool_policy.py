"""ToolExecutionPolicy for Loki runtime — before/after hooks and execution modes.

Inspired by Pi packages/agent:
- beforeToolCall: can block execution, returns reason
- afterToolCall: can override result content/details/isError/terminate (field-level merge)
- ToolExecutionMode: "sequential" | "parallel"
- terminate signal: early stop when all finalized tool results set terminate=true
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from loki_harness.runtime.core.messages import AgentMessage, AssistantMessage, ToolCall, ToolResultMessage

# ---------------------------------------------------------------------------
# Execution mode
# ---------------------------------------------------------------------------

ToolExecutionMode = Literal["sequential", "parallel"]

# ---------------------------------------------------------------------------
# Context types for hooks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeforeToolCallContext:
    """Context passed to before_tool_call hook."""
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    messages: list[AgentMessage] = field(default_factory=list)


@dataclass(slots=True)
class AfterToolCallContext:
    """Context passed to after_tool_call hook."""
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    result: ToolResultMessage
    is_error: bool = False
    messages: list[AgentMessage] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hook result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeforeToolCallResult:
    """Return from before_tool_call. If block=True, tool is skipped with reason."""
    block: bool = False
    reason: str = ""


@dataclass(slots=True)
class AfterToolCallResult:
    """Return from after_tool_call. Omitted fields keep original values."""
    content: str | None = None
    details: Any = None
    is_error: bool | None = None
    terminate: bool | None = None


# ---------------------------------------------------------------------------
# Hook signatures
# ---------------------------------------------------------------------------

BeforeToolCallHook = Callable[
    [BeforeToolCallContext],
    BeforeToolCallResult | Awaitable[BeforeToolCallResult] | None,
]

AfterToolCallHook = Callable[
    [AfterToolCallContext],
    AfterToolCallResult | Awaitable[AfterToolCallResult] | None,
]


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionPolicy:
    """Configuration for how tool calls are executed.

    Attributes:
        mode: "sequential" = one at a time; "parallel" = concurrent where possible
        max_parallel: max concurrent tool calls in parallel mode
        before_tool_call: optional pre-execution hook (can block)
        after_tool_call: optional post-execution hook (can override result)
    """

    mode: ToolExecutionMode = "parallel"
    max_parallel: int = 16
    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def apply_before_hook(
    hook: BeforeToolCallHook | None,
    context: BeforeToolCallContext,
) -> BeforeToolCallResult | None:
    """Call before_tool_call hook if configured. Returns None if hook is None."""
    if hook is None:
        return None
    result = hook(context)
    if hasattr(result, "__await__"):
        result = await result
    return result


async def apply_after_hook(
    hook: AfterToolCallHook | None,
    context: AfterToolCallContext,
) -> AfterToolCallResult | None:
    """Call after_tool_call hook if configured. Returns None if hook is None."""
    if hook is None:
        return None
    result = hook(context)
    if hasattr(result, "__await__"):
        result = await result
    return result


def merge_after_result(
    original: ToolResultMessage,
    override: AfterToolCallResult | None,
) -> ToolResultMessage:
    """Merge an AfterToolCallResult into a ToolResultMessage (field-level override)."""
    if override is None:
        return original
    if override.content is not None:
        original.content = override.content
    if override.details is not None:
        original.details = override.details
    if override.is_error is not None:
        original.is_error = override.is_error
    # terminate flag is consumed by the caller (not stored on ToolResultMessage)
    return original


def should_terminate_batch(results: list[ToolResultMessage], terminate_flags: list[bool]) -> bool:
    """Early termination: true when every tool result in the batch signals terminate."""
    if not terminate_flags:
        return False
    return all(terminate_flags)
