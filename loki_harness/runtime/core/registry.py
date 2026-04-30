"""Registry-driven runtime tool system with ToolExecutionPolicy support.

Enhancement over the original Hermes-inspired registry:
- RuntimeToolEntry gains execution_mode (per-tool sequential/parallel override)
- Handler signature clarified
- All existing API preserved
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)


ToolExecutionMode = Literal["sequential", "parallel"]

ToolHandler = Callable[[dict[str, Any]], str | dict[str, Any] | Awaitable[str | dict[str, Any]]]
ToolCheck = Callable[[], bool]


@dataclass(slots=True)
class RuntimeToolEntry:
    """A single tool registered in the runtime.

    Attributes:
        name: Tool name exposed to the LLM
        schema: OpenAI-style function schema
        handler: Execution callback (sync or async)
        toolset: Logical group for filtering (e.g., "workspace", "mcp", "skills")
        description: Human-readable description
        check_fn: Optional availability guard
        requires_env: Environment variables required
        is_async: Whether handler returns an awaitable
        max_result_size_chars: Per-tool result size cap
        execution_mode: Override tool execution strategy for this tool
        metadata: Arbitrary extra data
    """

    name: str
    schema: dict[str, Any]
    handler: ToolHandler
    toolset: str = "runtime"
    description: str = ""
    check_fn: ToolCheck | None = None
    requires_env: list[str] = field(default_factory=list)
    is_async: bool = False
    max_result_size_chars: int | None = None
    execution_mode: ToolExecutionMode | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return self.toolset


class RuntimeToolRegistry:
    """Dynamic tool registry with toolset filtering and availability checks."""

    def __init__(self):
        self._entries: dict[str, RuntimeToolEntry] = {}

    def register(
        self,
        *,
        name: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        category: str | None = None,
        toolset: str | None = None,
        description: str = "",
        check_fn: ToolCheck | None = None,
        requires_env: list[str] | None = None,
        is_async: bool = False,
        max_result_size_chars: int | None = None,
        execution_mode: ToolExecutionMode | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        resolved_toolset = toolset or category or "runtime"
        self._entries[name] = RuntimeToolEntry(
            name=name,
            schema=schema,
            handler=handler,
            toolset=resolved_toolset,
            description=description or schema.get("function", {}).get("description", ""),
            check_fn=check_fn,
            requires_env=requires_env or [],
            is_async=is_async,
            max_result_size_chars=max_result_size_chars,
            execution_mode=execution_mode,
            metadata=metadata or {},
        )

    def deregister(self, name: str) -> None:
        self._entries.pop(name, None)

    def clear_toolset(self, toolset: str) -> None:
        doomed = [name for name, entry in self._entries.items() if entry.toolset == toolset]
        for name in doomed:
            self._entries.pop(name, None)

    def extend(self, entries: list[RuntimeToolEntry]) -> None:
        for entry in entries:
            self._entries[entry.name] = entry

    def get_entry(self, name: str) -> RuntimeToolEntry | None:
        return self._entries.get(name)

    @property
    def valid_tool_names(self) -> set[str]:
        return set(self._entries)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return self.get_definitions()

    @property
    def entries(self) -> list[RuntimeToolEntry]:
        return list(self._entries.values())

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "toolset": entry.toolset,
                "category": entry.toolset,
                "description": entry.description,
                "available": self.is_available(entry.name),
                "requires_env": entry.requires_env or [],
                "execution_mode": entry.execution_mode,
                "metadata": entry.metadata,
            }
            for entry in self._entries.values()
        ]

    def get_definitions(
        self,
        *,
        enabled_toolsets: list[str] | set[str] | None = None,
        disabled_toolsets: list[str] | set[str] | None = None,
        quiet: bool = True,
    ) -> list[dict[str, Any]]:
        enabled = set(enabled_toolsets or [])
        disabled = set(disabled_toolsets or [])
        result: list[dict[str, Any]] = []
        check_cache: dict[ToolCheck, bool] = {}
        for entry in self._entries.values():
            if enabled and entry.toolset not in enabled:
                continue
            if disabled and entry.toolset in disabled:
                continue
            if not self._entry_available(entry, check_cache=check_cache, quiet=quiet):
                continue
            result.append(entry.schema)
        return result

    def filtered(
        self,
        *,
        allowed_tools: set[str] | None = None,
        denied_tools: set[str] | None = None,
        enabled_toolsets: set[str] | None = None,
        disabled_toolsets: set[str] | None = None,
    ) -> "RuntimeToolRegistry":
        clone = RuntimeToolRegistry()
        for entry in self._entries.values():
            if allowed_tools and entry.name not in allowed_tools:
                continue
            if denied_tools and entry.name in denied_tools:
                continue
            if enabled_toolsets and entry.toolset not in enabled_toolsets:
                continue
            if disabled_toolsets and entry.toolset in disabled_toolsets:
                continue
            clone.extend([entry])
        return clone

    def is_available(self, name: str) -> bool:
        entry = self._entries.get(name)
        return bool(entry and self._entry_available(entry, check_cache={}, quiet=True))

    def get_max_result_size(self, name: str, default: int) -> int:
        entry = self._entries.get(name)
        if entry and entry.max_result_size_chars is not None:
            return entry.max_result_size_chars
        return default

    def get_tool_to_toolset_map(self) -> dict[str, str]:
        return {name: entry.toolset for name, entry in self._entries.items()}

    def check_toolset_requirements(self) -> dict[str, bool]:
        checks: dict[str, bool] = {}
        for entry in self._entries.values():
            if entry.toolset in checks:
                continue
            checks[entry.toolset] = self.is_toolset_available(entry.toolset)
        return checks

    def is_toolset_available(self, toolset: str) -> bool:
        entries = [entry for entry in self._entries.values() if entry.toolset == toolset]
        if not entries:
            return False
        return any(self._entry_available(entry, check_cache={}, quiet=True) for entry in entries)

    def has_sequential_tool(self, tool_names: list[str]) -> bool:
        """Check if any of the given tools require sequential execution."""
        for name in tool_names:
            entry = self._entries.get(name)
            if entry and entry.execution_mode == "sequential":
                return True
        return False

    async def dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        entry = self._entries.get(tool_name)
        if entry is None:
            return json.dumps({"error": f"Unknown tool '{tool_name}'"})
        if not self._entry_available(entry, check_cache={}, quiet=False):
            return json.dumps({"error": f"Tool '{tool_name}' is unavailable"})
        try:
            result = entry.handler(args)
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            logger.exception("Runtime tool %s failed", tool_name)
            return json.dumps({"error": f"Tool execution failed: {type(exc).__name__}: {exc}"})

    def _entry_available(self, entry: RuntimeToolEntry, *, check_cache: dict[ToolCheck, bool], quiet: bool) -> bool:
        if entry.check_fn is None:
            return True
        if entry.check_fn not in check_cache:
            try:
                check_cache[entry.check_fn] = bool(entry.check_fn())
            except Exception:
                check_cache[entry.check_fn] = False
                if not quiet:
                    logger.debug("Runtime tool %s check raised; skipping", entry.name, exc_info=True)
        return check_cache[entry.check_fn]
