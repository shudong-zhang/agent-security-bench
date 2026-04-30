"""Runtime core for Loki — AgentMessage / RuntimeEvent / ToolExecutionPolicy layers."""

from .agent_loop import (
    LokiAgentLoop,
    RuntimeInput,
    RuntimeResult,
    RuntimeToolCallError,
)
from .capabilities import (
    McpServerDefinition,
    RuntimeCapabilityFactory,
    RuntimeSkillRegistry,
    SkillDefinition,
    SubagentDefinition,
    dump_capability_manifest,
    write_capability_manifest,
)
from .context_transform import (
    compress_messages,
    estimate_message_chars,
    estimate_tokens,
    spill_large_messages,
    transform_context,
)
from .events import (
    AgentEndEvent,
    AgentStartEvent,
    InMemoryRuntimeEventSink,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    RuntimeEvent,
    RuntimeEventSink,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    event_to_dict,
)
from .mcp_client import (
    McpSessionManager,
    McpStdioClient,
    McpStdioServerConfig,
    with_mcp_stdio_client,
)
from .messages import (
    AgentMessage,
    ArtifactMessage,
    AssistantMessage,
    CompressionSummaryMessage,
    LLMMessage,
    PermissionEventMessage,
    SecurityLabelMessage,
    SubagentSpanMessage,
    SystemMessage,
    TextContent,
    ToolCall,
    ToolCallFunction,
    ToolResultMessage,
    UserMessage,
    VerifierObservationMessage,
    agent_message_to_dict,
    agent_messages_to_dicts,
    convert_to_llm,
    dict_to_agent_message,
    dicts_to_agent_messages,
    llm_message_to_openai_dict,
    llm_messages_to_openai_dicts,
)
from .model_tools import (
    coerce_tool_args,
    enforce_turn_budget,
    maybe_compress_message_history,
    maybe_persist_tool_result,
    parse_tool_call_fallback,
    run_coro_sync,
)
from .prompt_builder import build_runtime_system_prompt
from .providers import (
    AnthropicCompatibleProvider,
    ChatCompletionProvider,
    OpenAICompatibleProvider,
    ProviderCapabilities,
    ReplayChatCompletionProvider,
)
from .registry import RuntimeToolEntry, RuntimeToolRegistry
from .tool_policy import (
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
from .trace_sink import (
    EventBridgeSink,
    InMemoryRuntimeTraceSink,
    RuntimeEventRecord,
    RuntimeTraceSink,
)
from .transcript import (
    emit_transcript_events,
    normalize_execution_transcript,
    write_transcript,
)

__all__ = [
    # Messages
    "AgentMessage",
    "ArtifactMessage",
    "AssistantMessage",
    "CompressionSummaryMessage",
    "LLMMessage",
    "PermissionEventMessage",
    "SecurityLabelMessage",
    "SubagentSpanMessage",
    "SystemMessage",
    "TextContent",
    "ToolCall",
    "ToolCallFunction",
    "ToolResultMessage",
    "UserMessage",
    "VerifierObservationMessage",
    "agent_message_to_dict",
    "agent_messages_to_dicts",
    "convert_to_llm",
    "dict_to_agent_message",
    "dicts_to_agent_messages",
    "llm_message_to_openai_dict",
    "llm_messages_to_openai_dicts",
    # Events
    "AgentEndEvent",
    "AgentStartEvent",
    "InMemoryRuntimeEventSink",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "RuntimeEvent",
    "RuntimeEventSink",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    "event_to_dict",
    # Tool policy
    "AfterToolCallContext",
    "AfterToolCallResult",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "ToolExecutionPolicy",
    "apply_after_hook",
    "apply_before_hook",
    "merge_after_result",
    "should_terminate_batch",
    # Context transform
    "compress_messages",
    "estimate_message_chars",
    "estimate_tokens",
    "spill_large_messages",
    "transform_context",
    # Agent loop
    "LokiAgentLoop",
    "RuntimeInput",
    "RuntimeResult",
    "RuntimeToolCallError",
    "EventBridgeSink",
    # Registry
    "RuntimeToolEntry",
    "RuntimeToolRegistry",
    # Capabilities
    "McpServerDefinition",
    "RuntimeCapabilityFactory",
    "RuntimeSkillRegistry",
    "SkillDefinition",
    "SubagentDefinition",
    "dump_capability_manifest",
    "write_capability_manifest",
    # Providers
    "AnthropicCompatibleProvider",
    "ChatCompletionProvider",
    "OpenAICompatibleProvider",
    "ProviderCapabilities",
    "ReplayChatCompletionProvider",
    # MCP
    "McpSessionManager",
    "McpStdioClient",
    "McpStdioServerConfig",
    "with_mcp_stdio_client",
    # Trace
    "EventBridgeSink",
    "InMemoryRuntimeTraceSink",
    "RuntimeEventRecord",
    "RuntimeTraceSink",
    # Model tools
    "coerce_tool_args",
    "enforce_turn_budget",
    "maybe_compress_message_history",
    "maybe_persist_tool_result",
    "parse_tool_call_fallback",
    "run_coro_sync",
    # Prompt
    "build_runtime_system_prompt",
    # Transcript
    "emit_transcript_events",
    "normalize_execution_transcript",
    "write_transcript",
]
