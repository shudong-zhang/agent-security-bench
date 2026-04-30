"""Append-only JSONL trace store for harness events and artifacts."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from loki_harness.domain import EvidenceArtifact, TraceEvent


class JsonlTraceStore:
    """File-backed trace and evidence registry for a single Loki run."""

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.trace_dir = self.root_dir / "trace"
        self.evidence_dir = self.root_dir / "evidence"
        self.artifact_dir = self.root_dir / "artifacts"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.trace_dir / "events.jsonl"
        self.evidence_file = self.trace_dir / "evidence.jsonl"
        self.task_file = self.root_dir / "task.json"
        self.run_file = self.root_dir / "run.json"

    def append_event(self, event: TraceEvent) -> None:
        self._append_jsonl(self.events_file, asdict(event))

    def append_evidence(self, artifact: EvidenceArtifact) -> None:
        self._append_jsonl(self.evidence_file, asdict(artifact))

    def write_task(self, payload: dict) -> None:
        self.task_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_run(self, payload: dict) -> None:
        self.run_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _append_jsonl(path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
