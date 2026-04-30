"""Provider adapters for Loki runtime core — now with capability matrix.

Adds:
- ProviderCapabilities: feature flags per provider
- AnthropicCompatibleProvider: supports reasoning + tool-use natively
- Capability-aware reasoning extraction
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProviderCapabilities:
    """Declared capabilities of an LLM provider.

    Used by the runtime to decide: reasoning extraction strategy,
    tool call format, max context window, etc.
    """

    supports_reasoning: bool = False
    supports_tool_calls: bool = True
    supports_streaming: bool = False
    supports_parallel_tool_calls: bool = True
    max_context_tokens: int = 128000
    reasoning_levels: list[str] = field(default_factory=list)
    provider_family: str = "openai"


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class ChatCompletionProvider(Protocol):
    """Protocol for LLM providers used by LokiAgentLoop."""

    async def chat_completion(self, **kwargs) -> Any:
        """Return an OpenAI-style chat completion response."""

    def get_capabilities(self) -> ProviderCapabilities:
        """Return this provider's capability matrix."""


# ---------------------------------------------------------------------------
# OpenAI-compatible provider
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OpenAICompatibleProvider:
    """Thin async adapter over an OpenAI-compatible async client."""

    client: Any
    model: str
    capabilities: ProviderCapabilities = field(default_factory=lambda: ProviderCapabilities(
        supports_reasoning=True,
        supports_tool_calls=True,
        supports_parallel_tool_calls=True,
        max_context_tokens=128000,
        reasoning_levels=["low", "medium", "high"],
        provider_family="openai",
    ))

    async def chat_completion(self, **kwargs) -> Any:
        payload = {"model": self.model, **kwargs}
        return await self.client.chat.completions.create(**payload)

    def get_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    @classmethod
    def from_env(
        cls,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url_env: str = "OPENAI_BASE_URL",
    ) -> "OpenAICompatibleProvider":
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "`openai` package is not installed; cannot use openai_compatible runtime provider"
            ) from exc

        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Environment variable {api_key_env} is required for openai_compatible runtime provider"
            )

        base_url = os.environ.get(base_url_env)
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = AsyncOpenAI(**client_kwargs)
        return cls(client=client, model=model)


# ---------------------------------------------------------------------------
# Anthropic-compatible provider (native reasoning + tool-use)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AnthropicCompatibleProvider:
    """Provider that uses the Anthropic SDK with native thinking/tool-use support."""

    client: Any
    model: str
    capabilities: ProviderCapabilities = field(default_factory=lambda: ProviderCapabilities(
        supports_reasoning=True,
        supports_tool_calls=True,
        supports_streaming=False,
        supports_parallel_tool_calls=True,
        max_context_tokens=200000,
        reasoning_levels=["minimal", "low", "medium", "high", "xhigh"],
        provider_family="anthropic",
    ))
    thinking_level: str | None = None
    thinking_budget_tokens: int = 2000

    async def chat_completion(self, **kwargs) -> Any:
        messages = kwargs.pop("messages", [])
        tools = kwargs.pop("tools", None)
        temperature = kwargs.pop("temperature", 0.2)
        max_tokens = kwargs.pop("max_tokens", None) or 4096
        extra_body = kwargs.pop("extra_body", None)
        kwargs.pop("n", 1)  # Anthropic does not support n > 1

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools and len(tools) > 0:
            payload["tools"] = self._convert_tools_to_anthropic(tools)

        if self.thinking_level and self.thinking_level != "off":
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget_tokens,
            }

        # Forward extra_body as top-level keys (Anthropic supports metadata, etc.)
        if extra_body and isinstance(extra_body, dict):
            for key, value in extra_body.items():
                if key not in payload:
                    payload[key] = value

        response = await self.client.messages.create(**payload)

        # Convert Anthropic response to OpenAI-compatible format
        return self._convert_response(response)

    def get_capabilities(self) -> ProviderCapabilities:
        return self.capabilities

    @staticmethod
    def _convert_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tool schemas to Anthropic format."""
        result = []
        for tool in tools:
            func = tool.get("function", tool)
            converted = {
                "name": func.get("name", tool.get("name", "")),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
            result.append(converted)
        return result

    def _convert_response(self, response: Any) -> Any:
        """Convert an Anthropic response to OpenAI-compatible format."""
        content = ""
        tool_calls = []
        reasoning = None

        for block in getattr(response, "content", []):
            if hasattr(block, "text") and block.text:
                content += block.text
            elif hasattr(block, "type"):
                if block.type == "tool_use":
                    tool_calls.append(
                        SimpleNamespace(
                            id=getattr(block, "id", ""),
                            type="function",
                            function=SimpleNamespace(
                                name=getattr(block, "name", ""),
                                arguments=json.dumps(getattr(block, "input", {})),
                            ),
                        )
                    )
                elif block.type == "thinking":
                    reasoning = getattr(block, "thinking", "")

        # Also check stop_reason content blocks
        stop_blocks = getattr(response, "stop_reason_content", [])
        for block in stop_blocks:
            if hasattr(block, "text") and block.text:
                content += block.text

        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
            reasoning=reasoning,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    @classmethod
    def from_env(
        cls,
        *,
        model: str,
        api_key_env: str = "ANTHROPIC_API_KEY",
        thinking_level: str | None = None,
        thinking_budget_tokens: int = 2000,
    ) -> "AnthropicCompatibleProvider":
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "`anthropic` package is not installed; cannot use anthropic runtime provider"
            ) from exc

        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Environment variable {api_key_env} is required for anthropic runtime provider"
            )

        client = AsyncAnthropic(api_key=api_key)
        return cls(
            client=client,
            model=model,
            thinking_level=thinking_level,
            thinking_budget_tokens=thinking_budget_tokens,
        )


# ---------------------------------------------------------------------------
# Replay provider (deterministic testing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReplayChatCompletionProvider:
    """Deterministic provider for exercising Loki's own runtime loop."""

    script: list[dict[str, Any]]
    fallback_message: str = "Runtime script exhausted."
    _index: int = field(default=0, init=False)

    async def chat_completion(self, **kwargs) -> Any:
        if self._index < len(self.script):
            step = self.script[self._index]
            self._index += 1
        else:
            step = {"content": self.fallback_message, "tool_calls": []}

        content = step.get("content", "")
        tool_calls = [self._build_tool_call(tc) for tc in step.get("tool_calls", [])]
        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls,
            reasoning=step.get("reasoning"),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_reasoning=False,
            supports_tool_calls=True,
            supports_parallel_tool_calls=True,
            provider_family="replay",
        )

    @staticmethod
    def _build_tool_call(tool_call: dict[str, Any]) -> Any:
        return SimpleNamespace(
            id=tool_call.get("id", f"call_{tool_call.get('name', 'tool')}"),
            type="function",
            function=SimpleNamespace(
                name=tool_call["name"],
                arguments=json.dumps(tool_call.get("arguments", {}), ensure_ascii=False),
            ),
        )
