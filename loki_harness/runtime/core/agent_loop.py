"""Loki runtime loop — rebuilt on AgentMessage / RuntimeEvent / ToolExecutionPolicy layers.

Core design (inspired by Pi packages/agent):
- Internal messages are AgentMessage (not raw dicts)
- convert_to_llm() called only at the LLM call boundary
- RuntimeEvent standard lifecycle emitted throughout
- ToolExecutionPolicy governs before/after hooks and execution mode
- transform_context runs at AgentMessage level before convert_to_llm
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from loki_harness.runtime.core.context_transform import transform_context
from loki_harness.runtime.core.events import (
    AgentEndEvent,
    AgentStartEvent,
    InMemoryRuntimeEventSink,
    MessageEndEvent,
    MessageStartEvent,
    RuntimeEvent,
    RuntimeEventSink,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from loki_harness.runtime.core.messages import (
    AgentMessage,
    AssistantMessage,
    LLMMessage,
    SystemMessage,
    ToolCall,
    ToolCallFunction,
    ToolResultMessage,
    UserMessage,
    agent_message_to_dict,
    agent_messages_to_dicts,
    convert_to_llm,
    dict_to_agent_message,
    dicts_to_agent_messages,
    llm_messages_to_openai_dicts,
)
from loki_harness.runtime.core.model_tools import (
    coerce_tool_args,
    maybe_persist_tool_result,
    parse_tool_call_fallback,
)
from loki_harness.runtime.core.registry import RuntimeToolRegistry
from loki_harness.runtime.core.tool_policy import (
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ToolExecutionPolicy,
    apply_after_hook,
    apply_before_hook,
    merge_after_result,
    should_terminate_batch,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RuntimeToolCallError:
    turn: int
    tool_name: str
    arguments: str
    error: str
    tool_result: str


@dataclass(slots=True)
class RuntimeInput:
    """Input for a LokiAgentLoop run.

    Accepts both legacy dict messages and AgentMessage. Dicts are auto-converted.
    """

    run_id: str
    path_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    agent_messages: list[AgentMessage] = field(default_factory=list)
    max_turns: int = 30
    temperature: float = 0.2
    max_tokens: int | None = None
    extra_body: dict[str, Any] | None = None
    output_dir: str | None = None
    tool_result_max_chars: int = 1600
    parallel_tool_calls: bool = True
    max_parallel_tool_calls: int = 16
    history_compression_chars: int = 20000
    preserve_recent_messages: int = 10
    compression_summarizer: Callable[[list[dict[str, Any]]], str] | None = None
    # Subagent span model
    agent_id: str | None = None
    parent_agent_id: str | None = None
    span_id: str | None = None
    # Context transform (AgentMessage level)
    transform_context_fn: Callable[[list[AgentMessage]], list[AgentMessage]] | None = None

    def get_agent_messages(self) -> list[AgentMessage]:
        """Return messages as AgentMessage list, converting dicts if needed."""
        if self.agent_messages:
            return list(self.agent_messages)
        if self.messages:
            return dicts_to_agent_messages(list(self.messages))
        return []

    def get_agent_id(self) -> str:
        return self.agent_id or self.run_id


@dataclass(slots=True)
class RuntimeResult:
    """Result from a LokiAgentLoop run.

    Provides both AgentMessage and dict views for backward compat.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    agent_messages: list[AgentMessage] = field(default_factory=list)
    turns_used: int = 0
    finished_naturally: bool = False
    reasoning_per_turn: list[str | None] = field(default_factory=list)
    tool_errors: list[RuntimeToolCallError] = field(default_factory=list)

    @classmethod
    def from_agent_messages(
        cls,
        agent_messages: list[AgentMessage],
        turns_used: int,
        finished_naturally: bool,
        reasoning_per_turn: list[str | None],
        tool_errors: list[RuntimeToolCallError],
    ) -> "RuntimeResult":
        return cls(
            messages=agent_messages_to_dicts(agent_messages),
            agent_messages=agent_messages,
            turns_used=turns_used,
            finished_naturally=finished_naturally,
            reasoning_per_turn=reasoning_per_turn,
            tool_errors=tool_errors,
        )


# ---------------------------------------------------------------------------
# Reasoning extraction
# ---------------------------------------------------------------------------


def _extract_reasoning_from_message(message: Any, capabilities: Any | None = None) -> str | None:
    """Extract reasoning/thinking from LLM response.

    Uses ProviderCapabilities when available to choose the right extraction strategy.
    """
    # Provider-guided extraction
    if capabilities is not None and not getattr(capabilities, "supports_reasoning", True):
        return None

    if hasattr(message, "reasoning_content") and getattr(message, "reasoning_content"):
        return message.reasoning_content
    if hasattr(message, "reasoning") and getattr(message, "reasoning"):
        return message.reasoning
    if hasattr(message, "reasoning_details") and getattr(message, "reasoning_details"):
        for detail in message.reasoning_details:
            if hasattr(detail, "text") and detail.text:
                return detail.text
            if isinstance(detail, dict) and detail.get("text"):
                return detail["text"]
    return None


# ---------------------------------------------------------------------------
# LokiAgentLoop
# ---------------------------------------------------------------------------


class LokiAgentLoop:
    """Agent tool-calling loop with AgentMessage/RuntimeEvent/ToolExecutionPolicy.

    Parameters:
        server: LLM provider (ChatCompletionProvider protocol)
        tool_registry: RuntimeToolRegistry for tool dispatch
        tool_policy: ToolExecutionPolicy for hooks and execution mode
        event_sink: Optional RuntimeEventSink for lifecycle events
        agent_id: Identifier for this agent (for span model)
        parent_agent_id: Parent agent identifier (for nested subagents)
    """

    def __init__(
        self,
        server: Any,
        tool_registry: RuntimeToolRegistry,
        tool_policy: ToolExecutionPolicy | None = None,
        event_sink: RuntimeEventSink | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ):
        self.server = server
        self.tool_registry = tool_registry
        self.tool_policy = tool_policy or ToolExecutionPolicy()
        self._event_sink = event_sink or InMemoryRuntimeEventSink()
        self.agent_id = agent_id
        self.parent_agent_id = parent_agent_id

    async def run(self, runtime_input: RuntimeInput) -> RuntimeResult:
        agent_messages = runtime_input.get_agent_messages()
        reasoning_per_turn: list[str | None] = []
        tool_errors: list[RuntimeToolCallError] = []
        agent_id = runtime_input.get_agent_id()

        # Emit agent_start
        await self._emit(
            AgentStartEvent(
                agent_id=agent_id,
                parent_agent_id=runtime_input.parent_agent_id,
            )
        )

        for turn in range(1, runtime_input.max_turns + 1):
            # Emit turn_start
            await self._emit(TurnStartEvent(turn_number=turn))

            # ---- Transform context (AgentMessage level) ----
            agent_messages = self._apply_transform_context(agent_messages, runtime_input)

            # ---- Convert to LLM messages ----
            llm_messages = convert_to_llm(agent_messages)
            openai_messages = llm_messages_to_openai_dicts(llm_messages)

            # ---- Build LLM request ----
            chat_kwargs: dict[str, Any] = {
                "messages": openai_messages,
                "n": 1,
                "temperature": runtime_input.temperature,
            }
            tool_schemas = self.tool_registry.get_definitions()
            if tool_schemas:
                chat_kwargs["tools"] = tool_schemas
            if runtime_input.max_tokens is not None:
                chat_kwargs["max_tokens"] = runtime_input.max_tokens
            if runtime_input.extra_body:
                chat_kwargs["extra_body"] = runtime_input.extra_body

            # ---- Call LLM (with error handling) ----
            try:
                response = await self.server.chat_completion(**chat_kwargs)
                assistant_msg_raw = response.choices[0].message
            except Exception as exc:
                logger.exception("LLM call failed at turn %d", turn)
                agent_messages.append(
                    AssistantMessage(
                        content="",
                        stop_reason="error",
                        error_message=f"{type(exc).__name__}: {exc}",
                    )
                )
                await self._emit(
                    AgentEndEvent(
                        messages=list(agent_messages),
                        stop_reason="error",
                        agent_id=agent_id,
                    )
                )
                return RuntimeResult.from_agent_messages(
                    agent_messages=agent_messages,
                    turns_used=turn,
                    finished_naturally=False,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )
            reasoning = _extract_reasoning_from_message(
                assistant_msg_raw,
                capabilities=self.server.get_capabilities() if hasattr(self.server, "get_capabilities") else None,
            )
            reasoning_per_turn.append(reasoning)

            # ---- Parse assistant response ----
            raw_content = str(getattr(assistant_msg_raw, "content", "") or "")
            tool_calls_raw = list(getattr(assistant_msg_raw, "tool_calls", None) or [])

            # Fallback: parse <tool_call> from content
            if not tool_calls_raw and raw_content and self.tool_registry.schemas:
                parsed_content, parsed_calls = parse_tool_call_fallback(raw_content)
                if parsed_calls:
                    raw_content = parsed_content
                    tool_calls_raw = parsed_calls

            # Build AgentMessage
            normalized_tool_calls = [self._normalize_tool_call(tc) for tc in tool_calls_raw]
            assistant_msg = AssistantMessage(
                content=raw_content,
                tool_calls=normalized_tool_calls,
                thinking=reasoning,
            )

            # Emit message events for assistant
            await self._emit(MessageStartEvent(message=assistant_msg))
            await self._emit(MessageEndEvent(message=assistant_msg))
            agent_messages.append(assistant_msg)

            # ---- No tool calls → finish ----
            if not normalized_tool_calls:
                await self._emit(
                    TurnEndEvent(turn_number=turn, message=assistant_msg, tool_results=[])
                )
                await self._emit(
                    AgentEndEvent(
                        messages=list(agent_messages),
                        stop_reason="finished",
                        agent_id=agent_id,
                    )
                )
                return RuntimeResult.from_agent_messages(
                    agent_messages=agent_messages,
                    turns_used=turn,
                    finished_naturally=True,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

            # ---- Execute tool calls ----
            tool_messages, tool_terminate_flags = await self._execute_turn_tool_calls(
                runtime_input=runtime_input,
                turn=turn,
                assistant_msg=assistant_msg,
                agent_messages=agent_messages,
                tool_errors=tool_errors,
            )
            agent_messages.extend(tool_messages)

            # Emit turn_end
            await self._emit(
                TurnEndEvent(
                    turn_number=turn,
                    message=assistant_msg,
                    tool_results=[m for m in tool_messages if isinstance(m, ToolResultMessage)],
                )
            )

            # Check early termination from tool batch
            if should_terminate_batch(
                [m for m in tool_messages if isinstance(m, ToolResultMessage)],
                tool_terminate_flags,
            ):
                await self._emit(
                    AgentEndEvent(
                        messages=list(agent_messages),
                        stop_reason="finished",
                        agent_id=agent_id,
                    )
                )
                return RuntimeResult.from_agent_messages(
                    agent_messages=agent_messages,
                    turns_used=turn,
                    finished_naturally=True,
                    reasoning_per_turn=reasoning_per_turn,
                    tool_errors=tool_errors,
                )

        # Max turns reached
        await self._emit(
            AgentEndEvent(
                messages=list(agent_messages),
                stop_reason="max_turns",
                agent_id=agent_id,
            )
        )
        return RuntimeResult.from_agent_messages(
            agent_messages=agent_messages,
            turns_used=runtime_input.max_turns,
            finished_naturally=False,
            reasoning_per_turn=reasoning_per_turn,
            tool_errors=tool_errors,
        )

    # -------------------------------------------------------------------
    # Tool execution
    # -------------------------------------------------------------------

    async def _execute_tool_call(
        self,
        *,
        turn: int,
        tool_name: str,
        raw_args: str,
        tool_call_id: str,
        assistant_msg: AssistantMessage,
        agent_messages: list[AgentMessage],
        tool_errors: list[RuntimeToolCallError],
    ) -> tuple[ToolResultMessage, bool]:
        """Execute a single tool call. Returns (result_message, terminate_flag)."""
        terminate = False

        # Check tool exists
        if tool_name not in self.tool_registry.valid_tool_names:
            result = ToolResultMessage(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=json.dumps({"error": f"Unknown tool '{tool_name}'"}),
                is_error=True,
            )
            tool_errors.append(
                RuntimeToolCallError(
                    turn=turn,
                    tool_name=tool_name,
                    arguments=raw_args[:500],
                    error=f"Unknown tool '{tool_name}'",
                    tool_result=result.content,
                )
            )
            return result, terminate

        # Parse args
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as exc:
            result = ToolResultMessage(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=json.dumps({"error": f"Invalid JSON in tool arguments: {exc}"}),
                is_error=True,
            )
            tool_errors.append(
                RuntimeToolCallError(
                    turn=turn,
                    tool_name=tool_name,
                    arguments=raw_args[:500],
                    error=f"Invalid JSON: {exc}",
                    tool_result=result.content,
                )
            )
            return result, terminate

        # Coerce args
        try:
            entry = self.tool_registry.get_entry(tool_name)
            if entry is not None:
                args = coerce_tool_args(entry.schema, args)
        except Exception:
            pass

        # Build ToolCall object for hooks
        tool_call = ToolCall(
            id=tool_call_id,
            function=ToolCallFunction(name=tool_name, arguments=raw_args),
        )

        # --- before_tool_call hook ---
        before_ctx = BeforeToolCallContext(
            assistant_message=assistant_msg,
            tool_call=tool_call,
            args=args,
            messages=list(agent_messages),
        )
        before_result = await apply_before_hook(self.tool_policy.before_tool_call, before_ctx)
        if before_result is not None and before_result.block:
            content = json.dumps({"error": before_result.reason or "Tool execution was blocked"})
            result = ToolResultMessage(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=content,
                is_error=True,
            )
            return result, terminate

        # --- Execute ---
        try:
            dispatch_result = await self.tool_registry.dispatch(tool_name, args)
            result = ToolResultMessage(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=str(dispatch_result),
                is_error=False,
            )
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            result = ToolResultMessage(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=json.dumps({"error": f"Tool execution failed: {type(exc).__name__}: {exc}"}),
                is_error=True,
            )
            tool_errors.append(
                RuntimeToolCallError(
                    turn=turn,
                    tool_name=tool_name,
                    arguments=raw_args[:500],
                    error=f"{type(exc).__name__}: {exc}",
                    tool_result=result.content,
                )
            )

        # --- after_tool_call hook ---
        after_ctx = AfterToolCallContext(
            assistant_message=assistant_msg,
            tool_call=tool_call,
            args=args,
            result=result,
            is_error=result.is_error,
            messages=list(agent_messages),
        )
        after_result = await apply_after_hook(self.tool_policy.after_tool_call, after_ctx)
        result = merge_after_result(result, after_result)
        if after_result is not None and after_result.terminate is not None:
            terminate = after_result.terminate

        return result, terminate

    async def _execute_turn_tool_calls(
        self,
        *,
        runtime_input: RuntimeInput,
        turn: int,
        assistant_msg: AssistantMessage,
        agent_messages: list[AgentMessage],
        tool_errors: list[RuntimeToolCallError],
    ) -> tuple[list[AgentMessage], list[bool]]:
        """Execute all tool calls in a turn. Returns (tool_messages, terminate_flags)."""
        normalized_calls = assistant_msg.tool_calls
        if not normalized_calls:
            return [], []

        mode = self.tool_policy.mode
        use_parallel = runtime_input.parallel_tool_calls and mode == "parallel" and len(normalized_calls) > 1
        semaphore = asyncio.Semaphore(max(1, runtime_input.max_parallel_tool_calls))

        async def run_one(tc: ToolCall) -> tuple[ToolResultMessage, bool]:
            tool_name = tc.function.name
            raw_args = tc.function.arguments
            tool_call_id = tc.id

            # Emit tool_execution_start
            await self._emit(
                ToolExecutionStartEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=json.loads(raw_args) if raw_args else {},
                )
            )

            async with semaphore:
                result_msg, terminate = await self._execute_tool_call(
                    turn=turn,
                    tool_name=tool_name,
                    raw_args=raw_args,
                    tool_call_id=tool_call_id,
                    assistant_msg=assistant_msg,
                    agent_messages=agent_messages,
                    tool_errors=tool_errors,
                )

            # Persist large results
            threshold = self.tool_registry.get_max_result_size(tool_name, runtime_input.tool_result_max_chars)
            result_msg.content = maybe_persist_tool_result(
                content=result_msg.content,
                output_dir=runtime_input.output_dir,
                turn=turn,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                threshold_chars=threshold,
            )

            # Emit tool_execution_end
            await self._emit(
                ToolExecutionEndEvent(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result=result_msg.content[:500],
                    is_error=result_msg.is_error,
                    terminate=terminate,
                )
            )

            # Emit message events for tool result
            await self._emit(MessageStartEvent(message=result_msg))
            await self._emit(MessageEndEvent(message=result_msg))

            return result_msg, terminate

        if use_parallel:
            results = list(await asyncio.gather(*(run_one(tc) for tc in normalized_calls)))
        else:
            results = []
            for tc in normalized_calls:
                results.append(await run_one(tc))

        tool_messages: list[AgentMessage] = [r[0] for r in results]
        terminate_flags = [r[1] for r in results]
        return tool_messages, terminate_flags

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _apply_transform_context(
        self,
        agent_messages: list[AgentMessage],
        runtime_input: RuntimeInput,
    ) -> list[AgentMessage]:
        """Apply context transform before LLM call."""
        # User-provided transform function (takes precedence)
        if runtime_input.transform_context_fn is not None:
            return runtime_input.transform_context_fn(agent_messages)

        # Built-in compression
        if runtime_input.history_compression_chars > 0:
            return transform_context(
                agent_messages,
                max_chars=runtime_input.history_compression_chars,
                preserve_last=runtime_input.preserve_recent_messages,
                max_chars_per_message=runtime_input.tool_result_max_chars,
                output_dir=runtime_input.output_dir,
                summarizer=runtime_input.compression_summarizer,  # type: ignore[arg-type]
            )
        return agent_messages

    @staticmethod
    def _normalize_tool_call(tool_call: Any) -> ToolCall:
        """Normalize tool call from various sources into ToolCall."""
        if isinstance(tool_call, dict):
            func = tool_call.get("function", {})
            return ToolCall(
                id=tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                type=tool_call.get("type", "function"),
                function=ToolCallFunction(
                    name=func.get("name", tool_call.get("name", "")),
                    arguments=func.get("arguments", tool_call.get("arguments", "{}")),
                ),
            )
        # Object with attributes (e.g., OpenAI SDK response)
        return ToolCall(
            id=getattr(tool_call, "id", f"call_{uuid.uuid4().hex[:8]}"),
            type=getattr(tool_call, "type", "function"),
            function=ToolCallFunction(
                name=tool_call.function.name if hasattr(tool_call, "function") else "",
                arguments=tool_call.function.arguments if hasattr(tool_call, "function") else "{}",
            ),
        )

    async def _emit(self, event: RuntimeEvent) -> None:
        """Emit a RuntimeEvent to the event sink."""
        await self._event_sink.emit(event)


