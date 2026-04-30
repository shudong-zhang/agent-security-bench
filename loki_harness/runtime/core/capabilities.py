"""Hermes-inspired runtime capabilities for Loki."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import json
import re
import shutil
import tempfile
import concurrent.futures

from loki_harness.runtime.core.mcp_client import McpSessionManager, McpStdioServerConfig, with_mcp_stdio_client
from loki_harness.runtime.core.registry import RuntimeToolEntry
from loki_harness.runtime.core.trace_sink import RuntimeTraceSink


@dataclass(slots=True)
class SkillDefinition:
    skill_id: str
    name: str
    content: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpServerDefinition:
    server_name: str
    tools: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubagentDefinition:
    name: str
    role: str
    system_prompt: str
    summary_template: str
    allowed_tools: list[str] = field(default_factory=list)
    max_depth: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    # Span model: set at delegation time
    agent_id: str = ""
    parent_agent_id: str | None = None
    span_id: str = ""


class RuntimeSkillRegistry:
    """Progressive-disclosure skill registry, patterned after Hermes."""

    def __init__(self, skills: list[SkillDefinition]):
        self.skills = skills
        self._by_name = {skill.name: skill for skill in skills}

    @classmethod
    def from_task_metadata(cls, metadata: dict[str, Any]) -> "RuntimeSkillRegistry":
        raw_skills = metadata.get("runtime_skills", [])
        skills: list[SkillDefinition] = []
        for index, item in enumerate(raw_skills, start=1):
            if isinstance(item, str):
                path = Path(item).expanduser()
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8")
                metadata = {"path": str(path), **_parse_frontmatter(content)}
                name = str(metadata.get("name") or (path.parent.name if path.name.upper() == "SKILL.MD" else path.stem))
                skills.append(
                    SkillDefinition(
                        skill_id=f"skill_{index}",
                        name=name,
                        content=content,
                        description=str(metadata.get("description") or f"Loaded from {path}"),
                        metadata=metadata,
                    )
                )
                continue
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("instruction") or "")
            if not content and item.get("path"):
                path = Path(str(item["path"])).expanduser()
                if path.exists():
                    content = path.read_text(encoding="utf-8")
            if not content:
                continue
            parsed = _parse_frontmatter(content)
            skills.append(
                SkillDefinition(
                    skill_id=str(item.get("skill_id") or f"skill_{index}"),
                    name=str(item.get("name") or parsed.get("name") or f"skill_{index}"),
                    content=content,
                    description=str(item.get("description") or parsed.get("description") or ""),
                    metadata={
                        **parsed,
                        **{k: v for k, v in item.items() if k not in {"skill_id", "name", "content", "instruction", "description"}},
                    },
                )
            )
        return cls(skills)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "skill_id": skill.skill_id,
                "metadata": skill.metadata,
            }
            for skill in self.skills
        ]

    def view(self, name: str) -> dict[str, Any] | None:
        skill = self._by_name.get(name)
        if skill is None:
            return None
        return {
            "name": skill.name,
            "skill_id": skill.skill_id,
            "content": skill.content,
            "description": skill.description,
            "metadata": skill.metadata,
        }

    def render_prompt_index(self) -> str:
        if not self.skills:
            return ""
        lines = ["Available skills:"]
        for skill in self.skills:
            desc = f" - {skill.description}" if skill.description else ""
            lines.append(f"- {skill.name}{desc}")
        lines.append("Use `skills_list` to inspect and `skill_view` to load one when relevant.")
        return "\n".join(lines)


class RuntimeCapabilityFactory:
    """Build Loki runtime tools that mirror Hermes capability shapes."""

    def __init__(
        self,
        trace_sink: RuntimeTraceSink | None = None,
        subagent_runner: Callable[[SubagentDefinition, dict[str, Any]], dict[str, Any]] | None = None,
        mcp_manager: McpSessionManager | None = None,
        mcp_refresh_callback: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.trace_sink = trace_sink
        self.subagent_runner = subagent_runner
        self.mcp_manager = mcp_manager
        self.mcp_refresh_callback = mcp_refresh_callback

    def build_skill_tools(self, registry: RuntimeSkillRegistry, skill_root: Path | None = None) -> list[RuntimeToolEntry]:
        entries: list[RuntimeToolEntry] = []
        if registry.skills:
            entries.extend([
            RuntimeToolEntry(
                name="skills_list",
                toolset="skills",
                description="List available runtime skills with short descriptions.",
                schema={
                    "type": "function",
                    "function": {
                        "name": "skills_list",
                        "description": "List available runtime skills with short descriptions.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                handler=self._skills_list_handler(registry),
            ),
            RuntimeToolEntry(
                name="skill_view",
                toolset="skills",
                description="Load full content for a named runtime skill.",
                schema={
                    "type": "function",
                    "function": {
                        "name": "skill_view",
                        "description": "Load full content for a named runtime skill.",
                        "parameters": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        },
                    },
                },
                handler=self._skill_view_handler(registry),
            ),
            ])
        if skill_root is not None:
            entries.append(
                RuntimeToolEntry(
                    name="skill_manage",
                    toolset="skills",
                    description="Create, edit, patch, delete, or manage files for runtime skill artifacts.",
                    schema={
                        "type": "function",
                        "function": {
                            "name": "skill_manage",
                            "description": "Create, edit, patch, delete, write_file, or remove_file for a reusable runtime skill.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
                                    },
                                    "name": {"type": "string"},
                                    "content": {"type": "string"},
                                    "old": {"type": "string"},
                                    "new": {"type": "string"},
                                    "file_path": {"type": "string"},
                                },
                                "required": ["action", "name"],
                            },
                        },
                    },
                    handler=self._skill_manage_handler(skill_root),
                )
            )
        return entries

    def build_mcp_tools(self, metadata: dict[str, Any]) -> list[RuntimeToolEntry]:
        raw_servers = metadata.get("runtime_mcp_servers", [])
        servers: list[McpServerDefinition] = []
        for item in raw_servers:
            if not isinstance(item, dict) or not item.get("server_name"):
                continue
            tool_defs = item.get("tools")
            if item.get("command"):
                tool_defs = _discover_stdio_mcp_tools(item, self.mcp_manager)
            if item.get("tool_refresh"):
                refresh_name = f"{item['server_name']}__refresh_tools"
                if not isinstance(tool_defs, list):
                    tool_defs = []
                tool_defs = [
                    *tool_defs,
                    {
                        "name": refresh_name,
                        "description": f"Refresh discovered tools for MCP server {item['server_name']}",
                        "parameters": {"type": "object", "properties": {}},
                        "response": {"server_name": item["server_name"], "refreshed": True},
                        "_refresh_only": True,
                    },
                ]
            if not isinstance(tool_defs, list) or not tool_defs:
                continue
            servers.append(
                McpServerDefinition(
                    server_name=str(item["server_name"]),
                    tools=tool_defs,
                    metadata={k: v for k, v in item.items() if k not in {"server_name", "tools"}},
                )
            )
        if not servers:
            return []

        entries: list[RuntimeToolEntry] = []
        for server in servers:
            for tool in server.tools:
                tool_name = str(tool.get("name") or "")
                if not tool_name:
                    continue
                entries.append(
                    RuntimeToolEntry(
                        name=tool_name,
                        toolset="mcp",
                        description=str(tool.get("description") or f"MCP tool {tool_name} from {server.server_name}"),
                        metadata={
                            "server_name": server.server_name,
                            "mode": "stdio" if server.metadata.get("command") else "metadata_bridge",
                            **server.metadata,
                        },
                        schema={
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": str(tool.get("description") or f"MCP tool {tool_name} from {server.server_name}"),
                                "parameters": tool.get("parameters") or tool.get("inputSchema") or {"type": "object", "properties": {}},
                            },
                        },
                        handler=self._mcp_tool_handler(server, tool),
                    )
                )
        return entries

    def build_subagent_tools(self, metadata: dict[str, Any]) -> list[RuntimeToolEntry]:
        raw_agents = metadata.get("runtime_subagents", [])
        subagents: dict[str, SubagentDefinition] = {}
        for item in raw_agents:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            definition = SubagentDefinition(
                name=str(item["name"]),
                role=str(item.get("role") or "specialist"),
                system_prompt=str(item.get("system_prompt") or item.get("instruction") or ""),
                summary_template=str(item.get("summary_template") or "Subagent {name} completed task: {task}"),
                allowed_tools=list(item.get("allowed_tools") or []),
                max_depth=int(item.get("max_depth", 1)),
                metadata={k: v for k, v in item.items() if k not in {"name", "role", "system_prompt", "instruction", "summary_template", "allowed_tools"}},
            )
            subagents[definition.name] = definition
        if not subagents:
            return []
        return [
            RuntimeToolEntry(
                name="delegate_task",
                toolset="subagent",
                description="Delegate a focused subtask to a configured runtime subagent and get back a concise summary.",
                schema={
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "description": "Delegate a focused subtask to a configured runtime subagent and get back a concise summary.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "goal": {"type": "string"},
                                "context": {"type": "string"},
                                "subagent": {"type": "string"},
                                "depth": {"type": "integer"},
                                "tasks": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "goal": {"type": "string"},
                                            "context": {"type": "string"},
                                            "subagent": {"type": "string"},
                                            "depth": {"type": "integer"},
                                        },
                                    },
                                },
                            },
                            "required": [],
                        },
                    },
                },
                handler=self._delegate_task_handler(subagents),
            )
        ]

    def _skills_list_handler(self, registry: RuntimeSkillRegistry):
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            self._emit(stage="skills", event_type="SkillsListed", actor="runtime_loop", data={"count": len(registry.skills)})
            return {"skills": registry.list()}

        return handler

    def _skill_view_handler(self, registry: RuntimeSkillRegistry):
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            name = str(args.get("name") or "")
            skill = registry.view(name)
            if skill is None:
                return {"error": f"Unknown skill '{name}'"}
            self._emit(stage="skills", event_type="SkillViewed", actor="runtime_loop", data={"name": name})
            return skill

        return handler

    def _skill_manage_handler(self, skill_root: Path):
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            action = str(args.get("action") or "")
            name = str(args.get("name") or "").strip()
            content = str(args.get("content") or "")
            if action not in {"create", "edit", "patch", "delete", "write_file", "remove_file"}:
                return {"error": f"Unsupported skill_manage action '{action}'"}
            validation_error = _validate_skill_name(name)
            if validation_error:
                return {"success": False, "error": validation_error}
            skill_dir = _resolve_child(skill_root, name)
            skill_path = skill_dir / "SKILL.md"
            if action == "delete":
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
                result = {"success": True, "action": action, "name": name, "path": str(skill_dir)}
            elif action in {"create", "edit"}:
                if not content:
                    return {"success": False, "error": "content is required"}
                validation_error = _validate_skill_content(content)
                if validation_error:
                    return {"success": False, "error": validation_error}
                skill_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(skill_path, content)
                result = {"success": True, "action": action, "name": name, "path": str(skill_path)}
            elif action == "patch":
                old = str(args.get("old") or "")
                new = str(args.get("new") or content)
                target = _resolve_skill_file(skill_dir, str(args.get("file_path") or "SKILL.md"))
                if not target.exists():
                    return {"success": False, "error": f"file does not exist: {target.name}"}
                current = target.read_text(encoding="utf-8")
                if old:
                    if old not in current:
                        return {"success": False, "error": "old text not found"}
                    updated = current.replace(old, new, 1)
                else:
                    updated = current + ("\n" if current and not current.endswith("\n") else "") + new
                if target.name == "SKILL.md":
                    validation_error = _validate_skill_content(updated)
                    if validation_error:
                        return {"success": False, "error": validation_error}
                _atomic_write_text(target, updated)
                result = {"success": True, "action": action, "name": name, "path": str(target)}
            elif action == "write_file":
                file_path = str(args.get("file_path") or "")
                target = _resolve_skill_file(skill_dir, file_path)
                validation_error = _validate_support_file(skill_dir, target)
                if validation_error:
                    return {"success": False, "error": validation_error}
                _atomic_write_text(target, content)
                result = {"success": True, "action": action, "name": name, "path": str(target)}
            else:
                file_path = str(args.get("file_path") or "")
                target = _resolve_skill_file(skill_dir, file_path)
                validation_error = _validate_support_file(skill_dir, target)
                if validation_error:
                    return {"success": False, "error": validation_error}
                if target.exists():
                    target.unlink()
                result = {"success": True, "action": action, "name": name, "path": str(target)}
            self._emit(
                stage="skills",
                event_type="SkillManaged",
                actor="runtime_loop",
                data={"action": action, "name": name, "path": result.get("path")},
            )
            return result

        return handler

    def _mcp_tool_handler(self, server: McpServerDefinition, tool: dict[str, Any]):
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            tool_name = str(tool.get("name") or "")
            response = tool.get("response")
            self._emit(
                stage="mcp",
                event_type="McpToolInvoked",
                actor="mcp_bridge",
                data={
                    "server_name": server.server_name,
                    "tool_name": tool_name,
                    "arguments": args,
                    "metadata": server.metadata,
                },
            )
            if tool.get("_refresh_only"):
                if self.mcp_refresh_callback is not None:
                    return self.mcp_refresh_callback(server.server_name)
                refreshed = _discover_stdio_mcp_tools(
                    {"server_name": server.server_name, **server.metadata},
                    self.mcp_manager,
                    force_refresh=True,
                )
                return {"server_name": server.server_name, "refreshed": True, "tool_count": len(refreshed)}
            if server.metadata.get("command"):
                config = _mcp_config_from_metadata(server.server_name, server.metadata)
                if self.mcp_manager is not None:
                    result = self.mcp_manager.call_tool(config, tool_name, args)
                else:
                    result = with_mcp_stdio_client(config, lambda client: client.call_tool(tool_name, args))
                return {
                    "server_name": server.server_name,
                    "tool_name": tool_name,
                    "mode": "stdio",
                    "result": result,
                    "arguments": args,
                }
            if callable(response):
                result = response(args)
            else:
                result = response
            if isinstance(result, str):
                return {"server_name": server.server_name, "tool_name": tool_name, "result": result}
            return {
                "server_name": server.server_name,
                "tool_name": tool_name,
                "mode": "metadata_bridge",
                "note": "This is Loki's deterministic MCP bridge. Configure real MCP transports separately before relying on external server behavior.",
                "result": result,
                "arguments": args,
            }

        return handler

    def _delegate_task_handler(self, subagents: dict[str, SubagentDefinition]):
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            tasks = args.get("tasks")
            # Server-side depth increment — never trust the LLM's depth value.
            # The caller's depth is always incremented by 1 so that the LLM
            # cannot bypass `max_depth` by always passing depth=0.
            parent_depth = int(args.get("depth") or 0)
            enforced_depth = parent_depth + 1
            if isinstance(tasks, list) and tasks:
                return self._run_batch_subagents(
                    tasks, subagents, depth_increment=1
                )
            return self._run_one_subagent(
                {
                    "subagent": str(args.get("subagent") or ""),
                    "goal": str(args.get("goal") or ""),
                    "context": str(args.get("context") or ""),
                    "depth": enforced_depth,
                    "parent_agent_id": args.get("parent_agent_id"),
                },
                subagents,
            )

        return handler

    def _run_batch_subagents(
        self,
        tasks: list[dict[str, Any]],
        subagents: dict[str, SubagentDefinition],
        depth_increment: int = 0,
    ) -> dict[str, Any]:
        normalized = []
        for item in tasks[:3]:
            if not isinstance(item, dict):
                continue
            enforced_depth = int(item.get("depth") or 0) + depth_increment
            normalized.append(
                {
                    "subagent": str(item.get("subagent") or ""),
                    "goal": str(item.get("goal") or ""),
                    "context": str(item.get("context") or ""),
                    "depth": enforced_depth,
                    "parent_agent_id": item.get("parent_agent_id"),
                }
            )
        if not normalized:
            return {"error": "No valid delegated tasks provided"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(normalized))) as pool:
            futures = [pool.submit(self._run_one_subagent, task, subagents) for task in normalized]
            results = [future.result() for future in futures]
        return {"batch": True, "results": results}

    def _run_one_subagent(self, task: dict[str, Any], subagents: dict[str, SubagentDefinition]) -> dict[str, Any]:
        import uuid

        subagent_name = task["subagent"]
        goal = task["goal"]
        context = task["context"]
        depth = task["depth"]
        definition = subagents.get(subagent_name)
        if definition is None:
            return {"error": f"Unknown subagent '{subagent_name}'"}
        if depth >= definition.max_depth:
            return {
                "error": (
                    f"Delegation depth limit reached for '{definition.name}' "
                    f"(depth={depth}, max_depth={definition.max_depth})"
                )
            }

        # Generate span identifiers
        agent_id = f"agent_{definition.name}_{uuid.uuid4().hex[:8]}"
        span_id = f"span_{uuid.uuid4().hex[:12]}"
        parent_agent_id = task.get("parent_agent_id") or definition.parent_agent_id

        self._emit(
            stage="subagent",
            event_type="SubagentDelegated",
            actor="runtime_loop",
            data={
                "subagent": definition.name,
                "role": definition.role,
                "goal": goal,
                "context": context,
                "allowed_tools": definition.allowed_tools,
                "depth": depth,
                "max_depth": definition.max_depth,
                "agent_id": agent_id,
                "parent_agent_id": parent_agent_id,
                "span_id": span_id,
            },
        )
        if self.subagent_runner is not None:
            result = self.subagent_runner(
                definition,
                {
                    "goal": goal,
                    "context": context,
                    "depth": depth,
                    "agent_id": agent_id,
                    "parent_agent_id": parent_agent_id,
                    "span_id": span_id,
                },
            )
        else:
            result = {
                "summary": definition.summary_template.format(
                    name=definition.name,
                    goal=goal,
                    task=goal,
                    context=context,
                    role=definition.role,
                ),
                "finished_naturally": False,
                "turns_used": 0,
                "messages": [],
            }
        self._emit(
            stage="subagent",
            event_type="SubagentCompleted",
            actor="runtime_loop",
            data={
                "subagent": definition.name,
                "summary": result.get("summary", ""),
                "turns_used": result.get("turns_used"),
                "agent_id": agent_id,
                "span_id": span_id,
            },
        )
        return {
            "subagent": definition.name,
            "role": definition.role,
            "goal": goal,
            "context": context,
            "system_prompt": definition.system_prompt,
            "allowed_tools": definition.allowed_tools,
            "depth": depth,
            "max_depth": definition.max_depth,
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "span_id": span_id,
            **result,
        }

    def _emit(self, *, stage: str, event_type: str, actor: str, data: dict[str, Any]) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.emit(stage=stage, event_type=event_type, actor=actor, data=data)


def dump_capability_manifest(*, skill_registry: RuntimeSkillRegistry, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "skills": skill_registry.list(),
        "mcp_servers": [
            {
                "server_name": server.get("server_name"),
                "tools": [
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                    }
                    for tool in server.get("tools", [])
                    if isinstance(tool, dict)
                ],
                "metadata": {k: v for k, v in server.items() if k not in {"server_name", "tools"}},
            }
            for server in metadata.get("runtime_mcp_servers", [])
            if isinstance(server, dict)
        ],
        "subagents": [
            {
                "name": item.get("name"),
                "role": item.get("role"),
                "allowed_tools": item.get("allowed_tools", []),
                "metadata": {k: v for k, v in item.items() if k not in {"name", "role", "system_prompt", "instruction", "summary_template", "allowed_tools"}},
            }
            for item in metadata.get("runtime_subagents", [])
            if isinstance(item, dict)
        ],
    }


def write_capability_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _discover_stdio_mcp_tools(
    server: dict[str, Any],
    manager: McpSessionManager | None = None,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    config = _mcp_config_from_metadata(str(server["server_name"]), server)
    try:
        if manager is not None:
            if not force_refresh:
                cached = manager.get_cached_tools(config.server_name)
                if cached is not None:
                    return cached
            return manager.refresh_tools(config)
        return with_mcp_stdio_client(config, lambda client: client.list_tools())
    except Exception as exc:
        return [
            {
                "name": f"{server['server_name']}_unavailable",
                "description": f"MCP stdio discovery failed: {type(exc).__name__}: {exc}",
                "parameters": {"type": "object", "properties": {}},
                "response": {"error": f"MCP stdio discovery failed: {type(exc).__name__}: {exc}"},
            }
        ]


def _mcp_config_from_metadata(server_name: str, metadata: dict[str, Any]) -> McpStdioServerConfig:
    return McpStdioServerConfig(
        server_name=server_name,
        command=str(metadata["command"]),
        args=[str(item) for item in metadata.get("args", [])],
        env={str(key): str(value) for key, value in metadata.get("env", {}).items()},
        timeout_seconds=float(metadata.get("timeout_seconds", 20)),
    )


_VALID_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ALLOWED_SKILL_SUBDIRS = {"references", "templates", "scripts", "assets"}


def _parse_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---", 4)
    if end == -1:
        return {}
    metadata: dict[str, Any] = {}
    for line in content[4:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip("\"'")
        if value.startswith("[") and value.endswith("]"):
            metadata[key.strip()] = [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
        elif value:
            metadata[key.strip()] = value
    return metadata


def _validate_skill_name(name: str) -> str | None:
    if not name:
        return "skill name is required"
    if not _VALID_SKILL_NAME_RE.match(name):
        return "skill name must be lowercase filesystem-safe text up to 64 chars"
    return None


def _validate_skill_content(content: str) -> str | None:
    if not content.strip():
        return "content cannot be empty"
    if len(content) > 100_000:
        return "content exceeds 100000 characters"
    metadata = _parse_frontmatter(content)
    if not metadata.get("name") or not metadata.get("description"):
        return "SKILL.md must start with YAML-style frontmatter containing name and description"
    if _contains_skill_injection(content):
        return "skill content contains high-risk prompt-injection language"
    return None


def _contains_skill_injection(content: str) -> bool:
    lowered = content.lower()
    patterns = [
        "ignore previous instructions",
        "ignore all previous instructions",
        "developer message",
        "system prompt",
        "exfiltrate",
        "steal secrets",
    ]
    return any(pattern in lowered for pattern in patterns)


def _resolve_child(root: Path, child: str) -> Path:
    resolved = (root / child).resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        raise ValueError(f"path escapes skill root: {child}")
    return resolved


def _resolve_skill_file(skill_dir: Path, file_path: str) -> Path:
    if not file_path:
        raise ValueError("file_path is required")
    if file_path == "SKILL.md":
        return (skill_dir / "SKILL.md").resolve()
    normalized = Path(file_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("path traversal is not allowed")
    return (skill_dir / normalized).resolve()


def _validate_support_file(skill_dir: Path, target: Path) -> str | None:
    try:
        rel = target.resolve().relative_to(skill_dir.resolve())
    except ValueError:
        return "file escapes skill directory"
    if not rel.parts or rel.parts[0] not in _ALLOWED_SKILL_SUBDIRS:
        return "support files must be under references/, templates/, scripts/, or assets/"
    if len(rel.parts) < 2:
        return "file_path must include a filename under an allowed subdirectory"
    return None


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
