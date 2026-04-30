"""Builders for source and sink specs."""

from __future__ import annotations

from loki_harness.domain import AttackPath, SinkSpec, SourceSpec, TestTask, new_id


def build_source_spec(task: TestTask, path: AttackPath) -> SourceSpec:
    config = task.metadata.get("source_spec", {}) or {}
    source_type = str(config.get("source_type") or _infer_source_type(task, path))
    origin = str(config.get("origin") or "attacker_controlled_content")
    carrier_format = str(config.get("carrier_format") or "markdown")
    trust_boundary = str(config.get("trust_boundary") or "crosses_into_agent_context")
    description = str(
        config.get("description")
        or f"Adversarial source intended to influence path '{path.title}' for target {task.target.display_name}"
    )
    return SourceSpec(
        source_id=new_id("src"),
        source_type=source_type,
        origin=origin,
        carrier_format=carrier_format,
        trust_boundary=trust_boundary,
        description=description,
        metadata={
            "path_id": path.path_id,
            "path_title": path.title,
            "target": task.target.display_name,
        },
    )


def build_sink_specs(task: TestTask, path: AttackPath) -> list[SinkSpec]:
    configured = task.metadata.get("sink_spec")
    if isinstance(configured, list) and configured:
        sinks = []
        for item in configured:
            if not isinstance(item, dict):
                continue
            sinks.append(
                SinkSpec(
                    sink_id=new_id("sink"),
                    sink_type=str(item.get("sink_type") or "prompt_context"),
                    component=str(item.get("component") or "agent_loop"),
                    sensitivity=str(item.get("sensitivity") or "high"),
                    description=str(item.get("description") or "Configured sink"),
                    metadata={k: v for k, v in item.items() if k not in {"sink_type", "component", "sensitivity", "description"}},
                )
            )
        if sinks:
            return sinks

    return [
        SinkSpec(
            sink_id=new_id("sink"),
            sink_type="prompt_context",
            component="planner_or_context_window",
            sensitivity="high",
            description="Agent reads attacker-controlled content into planning context",
            metadata={"path_title": path.title},
        ),
        SinkSpec(
            sink_id=new_id("sink"),
            sink_type="tool_invocation",
            component="tool_arguments_or_command_line",
            sensitivity="critical",
            description="Agent actions influenced enough to alter tool usage or command execution",
            metadata={"required_tools": list(path.required_tools)},
        ),
    ]


def _infer_source_type(task: TestTask, path: AttackPath) -> str:
    source_shape = str((task.metadata.get("source_materializer") or {}).get("kind", "")).lower()
    if source_shape:
        return source_shape
    if "workspace" in path.entry_vector:
        return "workspace_bootstrap"
    if task.target.target_type in {"repo", "coding_agent", "cli_agent"}:
        return "repo_content"
    return "issue_comment"
