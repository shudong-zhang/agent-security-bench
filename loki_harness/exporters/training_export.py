"""Export Loki runs into training-oriented JSON records."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .episode import EpisodeCompiler


class RunTrainingExporter:
    """Convert run artifacts into portable training records."""

    def export_run(self, run_dir: str | Path) -> dict[str, Any]:
        run_root = Path(run_dir)
        episode_path = run_root / "episode.json"
        if episode_path.exists():
            episode = self._read_json(episode_path)
        else:
            episode = EpisodeCompiler().compile_from_run_dir(run_root)

        task = self._read_json(run_root / "task.json")
        run = self._read_json(run_root / "run.json")
        verdict = self._read_json(run_root / "verdict.json")
        transcript = episode.get("trajectory", [])
        events = episode.get("raw_events", [])
        evidence = episode.get("artifact_refs", {}).get("evidence", [])

        return {
            "run_id": run.get("run_id"),
            "task_id": task.get("task_id"),
            "task_name": task.get("name"),
            "target": task.get("target", {}),
            "description": task.get("description"),
            "selected_path_id": run.get("selected_path_id"),
            "run_state": run.get("state"),
            "verdict": verdict,
            "event_count": len(events),
            "evidence_count": len(evidence),
            "transcript": episode.get("trajectory", []),
            "episode": episode,
            "events": events,
            "evidence": evidence,
            "training_views": {
                "sft_candidate": self._build_sft_candidate(task, verdict, transcript),
                "failure_recovery_candidate": self._build_failure_candidate(task, verdict, transcript),
            },
        }

    def export_runs(self, runs_root: str | Path) -> list[dict[str, Any]]:
        root = Path(runs_root)
        records = []
        for run_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name.startswith("run_")):
            verdict_path = run_dir / "verdict.json"
            if not verdict_path.exists():
                continue
            records.append(self.export_run(run_dir))
        return records

    def export_training_views(self, runs_root: str | Path) -> dict[str, list[dict[str, Any]]]:
        records = self.export_runs(runs_root)
        views = {
            "sft": [],
            "tool_use": [],
            "reward_model": [],
        }
        for record in records:
            episode = record["episode"]
            views["reward_model"].append(
                {
                    "episode_id": episode["episode_id"],
                    "task": episode["task"],
                    "trajectory": episode["trajectory"],
                    "outcome": episode["outcome"],
                    "labels": episode["labels"],
                    "attack_signals": episode["outcome"].get("metadata", {}).get("attack_signals", {}),
                }
            )

            views["sft"].append(record["training_views"]["sft_candidate"])

            for step in episode["trajectory"]:
                if not step.get("tool_interactions"):
                    continue
                views["tool_use"].append(
                    {
                        "episode_id": episode["episode_id"],
                        "task_name": episode["task"]["name"],
                        "step_id": step["step_id"],
                        "turn_id": step["turn_id"],
                        "input_context": step["input_context"],
                        "assistant_action": step["assistant_action"],
                        "tool_interactions": step["tool_interactions"],
                        "outcome": episode["outcome"],
                        "attack_signal_types": episode["labels"].get("attack_signal_types", []),
                    }
                )
        return views

    @staticmethod
    def write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_training_views(self, output_dir: str | Path, views: dict[str, list[dict[str, Any]]]) -> None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        for name, records in views.items():
            self.write_jsonl(root / f"{name}.jsonl", records)

    @staticmethod
    def _build_sft_candidate(task: dict, verdict: dict, transcript: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "instruction": task.get("description") or task.get("name"),
            "context": {
                "task_name": task.get("name"),
                "target": task.get("target", {}),
                "success_criteria": task.get("success_criteria", {}),
                "attack_signals": verdict.get("metadata", {}).get("attack_signals", {}),
            },
            "response_trace": transcript,
            "label": verdict.get("status"),
        }

    @staticmethod
    def _build_failure_candidate(task: dict, verdict: dict, transcript: list[dict[str, Any]]) -> dict[str, Any] | None:
        if verdict.get("status") not in {"failed", "inconclusive"}:
            return None
        return {
            "task_name": task.get("name"),
            "failure_summary": verdict.get("summary"),
            "partial_trace": transcript,
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | list[Any]:
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
