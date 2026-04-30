"""Claude Code target adapter for Loki harness."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loki_harness.domain import TestRun, TestTask
from loki_harness.targets.base import TargetExecutionResult, TargetObservation


@dataclass(slots=True)
class _PreparedClaudeTarget:
    workspace_dir: Path
    output_dir: Path
    raw_log_file: Path
    metadata: dict[str, Any]


class ClaudeCodeTargetAdapter:
    """Execute a tested Claude Code agent in a run-scoped local workspace."""

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-5",
        max_turns: int = 20,
        timeout_seconds: int = 300,
    ):
        self.model = model
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self._prepared: _PreparedClaudeTarget | None = None
        self._task: TestTask | None = None
        self._run: TestRun | None = None

    def prepare(self, task: TestTask, run: TestRun) -> dict[str, Any]:
        self._task = task
        self._run = run

        output_dir = Path(run.evidence_dir or "runs") / "_claude"
        workspace_dir = output_dir / "workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace_files = task.metadata.get("workspace_files", {})
        for rel_path, content in workspace_files.items():
            dest = workspace_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        self._prepared = _PreparedClaudeTarget(
            workspace_dir=workspace_dir,
            output_dir=output_dir,
            raw_log_file=output_dir / "claude_raw.log",
            metadata={"workspace_files": list(workspace_files)},
        )
        return {
            "workspace_dir": str(workspace_dir),
            "workspace_files": list(workspace_files),
            "event_log_file": str(self._prepared.raw_log_file),
        }

    def execute(self, execution_plan: dict[str, Any]) -> TargetExecutionResult:
        if self._prepared is None or self._task is None or self._run is None:
            raise RuntimeError("ClaudeCodeTargetAdapter.prepare() must be called before execute()")

        prompt = execution_plan["prompt"]
        completed = subprocess.run(
            [*self._build_agent_cmd(), prompt],
            cwd=self._prepared.workspace_dir,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        raw_log = completed.stdout
        if completed.stderr.strip():
            raw_log = f"{raw_log}\n[stderr]\n{completed.stderr}".strip()
        self._prepared.raw_log_file.write_text(raw_log, encoding="utf-8")

        parsed_messages = self._extract_messages(raw_log)
        parsed_tool_calls = self._extract_tool_calls(raw_log)
        transcript = self._build_transcript(parsed_messages, parsed_tool_calls)

        return TargetExecutionResult(
            run_id=self._run.run_id,
            path_id=execution_plan.get("path_id"),
            target_name=self._task.target.display_name,
            prompt=prompt,
            raw_log=raw_log,
            messages=parsed_messages,
            tool_calls=parsed_tool_calls,
            transcript=transcript,
            side_effects={
                "command_history": self._command_history(parsed_tool_calls),
                "network_requests": [],
                "process_returncode": completed.returncode,
            },
            metadata={
                **self._prepared.metadata,
                "workspace_dir": str(self._prepared.workspace_dir),
                "sandbox_dir": str(self._prepared.output_dir),
                "event_log_file": str(self._prepared.raw_log_file),
                "stderr": completed.stderr,
            },
        )

    def observe(self) -> TargetObservation:
        return TargetObservation(
            observation_type="target_status",
            summary="Claude Code adapter captures local CLI execution output",
        )

    def snapshot(self) -> dict[str, Any]:
        if self._prepared is None:
            return {}
        return {
            "workspace_dir": str(self._prepared.workspace_dir),
            "workspace_files": self._prepared.metadata.get("workspace_files", []),
        }

    def cleanup(self) -> None:
        return None

    def _build_agent_cmd(self) -> list[str]:
        return [
            "claude",
            "--print",
            "--verbose",
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
            "--max-turns",
            str(self.max_turns),
            "--model",
            self.model,
        ]

    @staticmethod
    def _extract_tool_calls(text: str) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for match in re.finditer(r"<tool_use>\s*(\{.*?\})\s*</tool_use>", text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                calls.append(
                    {
                        "name": data.get("name", "unknown"),
                        "arguments": data.get("input", data.get("arguments", {})),
                    }
                )
            except Exception:
                pass

        for match in re.finditer(
            r"(?:Tool:|ToolUse\(name=)['\"]?(\w+)['\"]?.*?(?:Input:|input=)(\{.*?\})",
            text,
            re.DOTALL,
        ):
            try:
                calls.append({"name": match.group(1), "arguments": json.loads(match.group(2))})
            except Exception:
                pass
        return calls

    @staticmethod
    def _extract_messages(text: str) -> list[dict[str, Any]]:
        parts = re.split(r"(<tool_use>|</tool_result>)", text)
        messages = []
        for part in parts:
            part = part.strip()
            if part and part not in {"<tool_use>", "</tool_result>"}:
                messages.append({"role": "assistant", "content": part[:1000]})
        return messages

    @staticmethod
    def _build_transcript(messages: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        for index, message in enumerate(messages, start=1):
            transcript.append(
                {
                    "type": "message",
                    "turn_id": f"turn_{index}",
                    "role": message.get("role", "assistant"),
                    "content": message.get("content", ""),
                }
            )
        for index, tool_call in enumerate(tool_calls, start=1):
            transcript.append(
                {
                    "type": "tool_call",
                    "turn_id": f"turn_{index}",
                    "tool_name": tool_call.get("name", "unknown"),
                    "arguments": tool_call.get("arguments", {}),
                    "source": tool_call.get("source", "claude_trace"),
                }
            )
        return transcript

    @staticmethod
    def _command_history(tool_calls: list[dict[str, Any]]) -> str:
        commands = []
        for call in tool_calls:
            if call.get("name") not in {"bash", "shell", "exec_command"}:
                continue
            args = call.get("arguments", {})
            if isinstance(args, dict) and args.get("command"):
                commands.append(str(args["command"]))
        return "\n".join(commands)
