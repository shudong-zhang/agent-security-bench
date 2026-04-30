"""Codex CLI target adapter for the rebuilt Loki harness."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loki_harness.domain import TestRun, TestTask
from loki_harness.targets.base import TargetExecutionResult, TargetObservation


@dataclass(slots=True)
class _PreparedCodexTarget:
    workspace_dir: Path
    output_dir: Path
    last_message_file: Path
    event_log_file: Path
    metadata: dict[str, Any]


class CodexCliTargetAdapter:
    """Execute the tested agent via `codex exec` and preserve event logs."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        sandbox_mode: str = "workspace-write",
        timeout_seconds: int = 300,
    ):
        self.model = model
        self.sandbox_mode = sandbox_mode
        self.timeout_seconds = timeout_seconds
        self._prepared: _PreparedCodexTarget | None = None
        self._task: TestTask | None = None
        self._run: TestRun | None = None

    def prepare(self, task: TestTask, run: TestRun) -> dict[str, Any]:
        self._task = task
        self._run = run

        output_dir = Path(run.evidence_dir or "runs") / "_codex"
        workspace_dir = output_dir / "workspace"
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace_files = task.metadata.get("workspace_files", {})
        for rel_path, content in workspace_files.items():
            dest = workspace_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        last_message_file = output_dir / "last_message.txt"
        event_log_file = output_dir / "events.jsonl"
        self._prepared = _PreparedCodexTarget(
            workspace_dir=workspace_dir,
            output_dir=output_dir,
            last_message_file=last_message_file,
            event_log_file=event_log_file,
            metadata={"workspace_files": list(workspace_files)},
        )
        return {
            "workspace_dir": str(workspace_dir),
            "workspace_files": list(workspace_files),
            "event_log_file": str(event_log_file),
        }

    def execute(self, execution_plan: dict[str, Any]) -> TargetExecutionResult:
        if self._prepared is None or self._task is None or self._run is None:
            raise RuntimeError("CodexCliTargetAdapter.prepare() must be called before execute()")

        prompt = execution_plan["prompt"]
        cmd = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            self.sandbox_mode,
            "--cd",
            str(self._prepared.workspace_dir),
            "--model",
            self.model,
            "--output-last-message",
            str(self._prepared.last_message_file),
            "--ephemeral",
            prompt,
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        self._prepared.event_log_file.write_text(completed.stdout, encoding="utf-8")

        events = self._parse_jsonl(completed.stdout)
        raw_log = completed.stdout
        stderr_text = completed.stderr.strip()
        if stderr_text:
            raw_log = f"{raw_log}\n[stderr]\n{stderr_text}".strip()

        tool_calls = self._extract_tool_calls(events)
        messages = self._extract_messages(events)
        command_history = self._extract_command_history(events)
        transcript = self._extract_transcript(events)

        return TargetExecutionResult(
            run_id=self._run.run_id,
            path_id=execution_plan.get("path_id"),
            target_name=self._task.target.display_name,
            prompt=prompt,
            raw_log=raw_log,
            messages=messages,
            tool_calls=tool_calls,
            transcript=transcript,
            side_effects={
                "command_history": command_history,
                "network_requests": [],
                "process_returncode": completed.returncode,
            },
            metadata={
                **self._prepared.metadata,
                "workspace_dir": str(self._prepared.workspace_dir),
                "sandbox_dir": str(self._prepared.output_dir),
                "event_log_file": str(self._prepared.event_log_file),
                "last_message_file": str(self._prepared.last_message_file),
                "stderr": completed.stderr,
            },
        )

    def observe(self) -> TargetObservation:
        return TargetObservation(
            observation_type="target_status",
            summary="Codex CLI adapter captures event-level stdout JSONL",
        )

    def snapshot(self) -> dict[str, Any]:
        if self._prepared is None:
            return {}
        return {
            "workspace_dir": str(self._prepared.workspace_dir),
            "event_log_file": str(self._prepared.event_log_file),
        }

    def cleanup(self) -> None:
        return None

    @staticmethod
    def _parse_jsonl(raw_output: str) -> list[dict[str, Any]]:
        events = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = {"type": "raw_line", "text": line}
            if isinstance(parsed, dict):
                events.append(parsed)
        return events

    @staticmethod
    def _extract_messages(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = []
        for event in events:
            event_type = event.get("type", "")
            if event_type in {"agent_message", "assistant_message", "message"}:
                text = event.get("text") or event.get("content") or ""
                role = event.get("role", "assistant")
                if text:
                    messages.append({"role": role, "content": str(text)[:2000]})
            elif "message" in event and isinstance(event["message"], str):
                messages.append({"role": "assistant", "content": event["message"][:2000]})
        return messages

    @staticmethod
    def _extract_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls = []
        for event in events:
            if "tool_name" in event:
                calls.append(
                    {
                        "name": event.get("tool_name", "unknown"),
                        "arguments": event.get("arguments") or event.get("input") or {},
                    }
                )
                continue

            payload = event.get("tool_call")
            if isinstance(payload, dict):
                calls.append(
                    {
                        "name": payload.get("name", "unknown"),
                        "arguments": payload.get("arguments") or payload.get("input") or {},
                    }
                )
        return calls

    @staticmethod
    def _extract_command_history(events: list[dict[str, Any]]) -> str:
        commands = []
        for event in events:
            if event.get("type") in {"exec_command", "shell_command"}:
                command = event.get("command") or event.get("input")
                if command:
                    commands.append(str(command))
            elif event.get("tool_name") in {"bash", "shell", "exec_command"}:
                command = event.get("arguments") or event.get("input")
                if isinstance(command, dict):
                    command = command.get("command") or json.dumps(command, ensure_ascii=False)
                if command:
                    commands.append(str(command))
        return "\n".join(commands)

    @staticmethod
    def _extract_transcript(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transcript: list[dict[str, Any]] = []
        turn_index = 0
        for event in events:
            event_type = event.get("type", "")

            if event_type in {"agent_message", "assistant_message", "message"}:
                turn_index += 1
                content = event.get("text") or event.get("content") or event.get("message") or ""
                transcript.append(
                    {
                        "type": "message",
                        "turn_id": f"turn_{turn_index}",
                        "role": event.get("role", "assistant"),
                        "content": str(content),
                    }
                )
                continue

            if event_type == "raw_line":
                turn_index += 1
                transcript.append(
                    {
                        "type": "message",
                        "turn_id": f"turn_{turn_index}",
                        "role": "system",
                        "content": str(event.get("text", "")),
                    }
                )
                continue

            if "tool_name" in event:
                turn_id = f"turn_{turn_index or 1}"
                transcript.append(
                    {
                        "type": "tool_call",
                        "turn_id": turn_id,
                        "tool_call_id": event.get("id") or event.get("call_id"),
                        "tool_name": event.get("tool_name", "unknown"),
                        "arguments": event.get("arguments") or event.get("input") or {},
                        "source": "codex_jsonl",
                    }
                )
                if "result" in event or "output" in event:
                    transcript.append(
                        {
                            "type": "tool_result",
                            "turn_id": turn_id,
                            "tool_call_id": event.get("id") or event.get("call_id"),
                            "tool_name": event.get("tool_name", "unknown"),
                            "result": event.get("result") or event.get("output"),
                        }
                    )
                continue

            payload = event.get("tool_call")
            if isinstance(payload, dict):
                transcript.append(
                    {
                        "type": "tool_call",
                        "turn_id": f"turn_{turn_index or 1}",
                        "tool_call_id": payload.get("id"),
                        "tool_name": payload.get("name", "unknown"),
                        "arguments": payload.get("arguments") or payload.get("input") or {},
                        "source": "codex_jsonl",
                    }
                )
        return transcript
