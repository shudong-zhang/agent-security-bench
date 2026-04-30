"""Compile Loki run artifacts into a training-oriented episode schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json


class EpisodeCompiler:
    """Create a canonical `episode.json` from run artifacts."""

    def compile_from_run_dir(self, run_dir: str | Path) -> dict[str, Any]:
        run_root = Path(run_dir)
        task = self._read_json(run_root / "task.json")
        run = self._read_json(run_root / "run.json")
        verdict = self._read_json(run_root / "verdict.json")
        events = self._read_jsonl(run_root / "trace" / "events.jsonl")
        evidence = self._read_jsonl(run_root / "trace" / "evidence.jsonl")
        transcript = self._load_artifact_json(evidence, "normalized_transcript")
        runtime_events = self._load_artifact_json(evidence, "runtime_events")
        runtime_capabilities = self._load_artifact_json(evidence, "runtime_capabilities")
        selected_path = self._extract_selected_path(events, run.get("selected_path_id"))

        return {
            "episode_id": run.get("run_id"),
            "task_id": task.get("task_id"),
            "source": {
                "suite": "loki",
                "target_adapter": task.get("metadata", {}).get("target_adapter"),
                "target_type": task.get("target", {}).get("target_type"),
                "target_name": task.get("target", {}).get("display_name"),
                "model": task.get("metadata", {}).get("target_model"),
                "runtime_provider": task.get("metadata", {}).get("runtime_provider"),
                "runtime_max_turns": task.get("metadata", {}).get("runtime_max_turns"),
                "runtime_temperature": task.get("metadata", {}).get("runtime_temperature"),
                "runtime_max_tokens": task.get("metadata", {}).get("runtime_max_tokens"),
                "source_type": task.get("metadata", {}).get("source_context", {}).get("source_type"),
                "runtime_capabilities": runtime_capabilities,
            },
            "task": {
                "name": task.get("name"),
                "instruction": task.get("description") or task.get("name"),
                "success_criteria": task.get("success_criteria", {}),
                "allowed_actions": task.get("allowed_actions", []),
                "forbidden_actions": task.get("forbidden_actions", []),
                "attack_path": selected_path,
                "context": {
                    "workspace_files": sorted(task.get("metadata", {}).get("workspace_files", {}).keys()),
                    "target": task.get("target", {}),
                    "reporting_requirements": task.get("reporting_requirements", {}),
                    "run_budget": task.get("run_budget", {}),
                    "source_context": task.get("metadata", {}).get("source_context", {}),
                    "prepared_sources": task.get("metadata", {}).get("prepared_sources", {}),
                },
            },
            "trajectory": self._build_trajectory(transcript),
            "outcome": {
                "run_state": run.get("state"),
                "status": verdict.get("status"),
                "summary": verdict.get("summary"),
                "confidence": verdict.get("confidence"),
                "evidence_ids": verdict.get("evidence_ids", []),
                "metadata": verdict.get("metadata", {}),
            },
            "labels": {
                "useful_for_sft": bool(transcript),
                "useful_for_tool_use": any(item.get("type") == "tool_call" for item in transcript),
                "useful_for_failure_recovery": verdict.get("status") in {"failed", "inconclusive"},
                "useful_for_reward_model": True,
                "failure_type": verdict.get("metadata", {}).get("failure_type"),
                "safety_violation_type": verdict.get("metadata", {}).get("safety_violation_type"),
                "turn_of_failure": verdict.get("metadata", {}).get("turn_of_failure"),
                "behavior_signal_type": verdict.get("metadata", {}).get("behavior_signal_type"),
                "attack_signal_present": bool(verdict.get("metadata", {}).get("attack_signals", {}).get("has_attack_signal")),
                "attack_signal_count": verdict.get("metadata", {}).get("attack_signals", {}).get("signal_count", 0),
                "attack_signal_types": self._extract_attack_signal_types(verdict),
                "agent_compromised": verdict.get("status") == "success"
                and verdict.get("metadata", {}).get("behavior_signal_type") in {"tool_use_observed", "sink_triggered", "instruction_followed"},
            },
            "artifact_refs": {
                "evidence": evidence,
                "event_file": str(run_root / "trace" / "events.jsonl"),
                "evidence_file": str(run_root / "trace" / "evidence.jsonl"),
                "runtime_events": runtime_events,
                "runtime_capabilities": runtime_capabilities,
            },
            "source_to_sink": {
                "sources": self._extract_sources(task),
                "sinks": self._extract_sinks(task),
                "observations": self._extract_propagation_observations(events),
            },
            "raw_events": events,
        }

    @staticmethod
    def write_episode(path: str | Path, episode: dict[str, Any]) -> None:
        output = Path(path)
        output.write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_trajectory(self, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in transcript:
            grouped.setdefault(item.get("turn_id", "turn_1"), []).append(item)

        steps = []
        for index, turn_id in enumerate(sorted(grouped, key=self._turn_sort_key), start=1):
            items = grouped[turn_id]
            observation = []
            assistant_action = None
            tool_interactions = []

            pending_tool_calls: dict[str, dict[str, Any]] = {}
            for item in items:
                item_type = item.get("type")
                if item_type == "message":
                    role = item.get("role", "assistant")
                    message_entry = {"type": "message", "role": role, "content": item.get("content", "")}
                    if role in {"system", "user"}:
                        observation.append(message_entry)
                    elif role == "assistant" and assistant_action is None:
                        assistant_action = {"type": "assistant_message", "content": item.get("content", "")}
                    else:
                        observation.append(message_entry)
                    continue

                if item_type == "tool_call":
                    interaction = {
                        "tool_call_id": item.get("tool_call_id"),
                        "tool_name": item.get("tool_name"),
                        "arguments": item.get("arguments", {}),
                        "result": None,
                        "source": item.get("source"),
                    }
                    tool_interactions.append(interaction)
                    if item.get("tool_call_id"):
                        pending_tool_calls[item["tool_call_id"]] = interaction
                    continue

                if item_type == "tool_result":
                    result_payload = item.get("result", "")
                    tool_call_id = item.get("tool_call_id")
                    if tool_call_id and tool_call_id in pending_tool_calls:
                        pending_tool_calls[tool_call_id]["result"] = result_payload
                    elif tool_interactions:
                        tool_interactions[-1]["result"] = result_payload
                    else:
                        observation.append({"type": "tool_result", "content": result_payload})

            steps.append(
                {
                    "step_id": f"step_{index:03d}",
                    "turn_id": turn_id,
                    "input_context": observation,
                    "assistant_action": assistant_action,
                    "tool_interactions": tool_interactions,
                    "annotations": {
                        "tool_count": len(tool_interactions),
                        "has_assistant_action": assistant_action is not None,
                    },
                }
            )
        return steps

    @staticmethod
    def _extract_selected_path(events: list[dict[str, Any]], selected_path_id: str | None) -> dict[str, Any] | None:
        for event in events:
            if event.get("event_type") == "PathSelected":
                path = event.get("data", {}).get("path", {})
                if selected_path_id is None or path.get("path_id") == selected_path_id:
                    return path
        return None

    @staticmethod
    def _extract_sources(task: dict[str, Any]) -> list[dict[str, Any]]:
        prepared = task.get("metadata", {}).get("prepared_sources", {})
        source = prepared.get("source")
        return [source] if isinstance(source, dict) else []

    @staticmethod
    def _extract_sinks(task: dict[str, Any]) -> list[dict[str, Any]]:
        prepared = task.get("metadata", {}).get("prepared_sources", {})
        sinks = prepared.get("sinks", [])
        return sinks if isinstance(sinks, list) else []

    @staticmethod
    def _extract_propagation_observations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for event in events:
            if event.get("event_type") in {"SourcePrepared", "PropagationObserved", "SinkEvaluated", "VerificationCompleted", "AttackSignalsAnnotated"}:
                out.append(event)
        return out

    @staticmethod
    def _extract_attack_signal_types(verdict: dict[str, Any]) -> list[str]:
        findings = verdict.get("metadata", {}).get("attack_signals", {}).get("findings", [])
        out: set[str] = set()
        for item in findings:
            if not isinstance(item, dict):
                continue
            for signal in str(item.get("signal_types", "")).split(","):
                signal = signal.strip()
                if signal:
                    out.add(signal)
        return sorted(out)

    @staticmethod
    def _load_artifact_json(evidence: list[dict[str, Any]], artifact_type: str) -> list[dict[str, Any]]:
        for artifact in evidence:
            if artifact.get("artifact_type") != artifact_type:
                continue
            path = Path(artifact["path"])
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return []

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    @staticmethod
    def _turn_sort_key(turn_id: str) -> tuple[int, str]:
        if turn_id.startswith("turn_"):
            suffix = turn_id.split("_", 1)[1]
            if suffix.isdigit():
                return int(suffix), turn_id
        return 10**9, turn_id
