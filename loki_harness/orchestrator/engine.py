"""Outer orchestrator skeleton for the rebuilt Loki harness."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from loki_harness.domain import (
    AttackPath,
    EvidenceArtifact,
    RunState,
    TestRun,
    TestTask,
    TraceEvent,
    new_id,
    utc_now,
)
from loki_harness.orchestrator.state_machine import assert_transition
from loki_harness.trace_store import JsonlTraceStore


class SecurityHarnessEngine:
    """Owns the outer lifecycle for security test runs."""

    def __init__(self, runs_root: str = "runs"):
        self.runs_root = Path(runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def create_run(self, task: TestTask) -> tuple[TestRun, JsonlTraceStore]:
        run_id = new_id("run")
        run_dir = self.runs_root / run_id
        store = JsonlTraceStore(run_dir)
        run = TestRun(
            run_id=run_id,
            task=task,
            trace_path=str(store.events_file),
            evidence_dir=str(store.evidence_dir),
        )
        store.write_task(asdict(task))
        store.write_run(asdict(run))
        self._emit(
            store,
            run,
            stage="task_intake",
            event_type="RunCreated",
            actor="orchestrator",
            data={"task": asdict(task)},
        )
        return run, store

    def transition(self, run: TestRun, store: JsonlTraceStore, new_state: RunState) -> None:
        old_state = run.state
        assert_transition(old_state, new_state)
        run.transition(new_state)
        store.write_run(asdict(run))
        self._emit(
            store,
            run,
            stage="orchestrator",
            event_type="RunStateChanged",
            actor="orchestrator",
            data={"from_state": old_state.value, "to_state": new_state.value},
        )

    def attach_attack_paths(self, run: TestRun, store: JsonlTraceStore, paths: list[AttackPath]) -> None:
        self._emit(
            store,
            run,
            stage="analysis",
            event_type="AttackGraphCreated",
            actor="analyzer",
            data={"paths": [asdict(path) for path in paths]},
        )

    def select_attack_path(self, run: TestRun, store: JsonlTraceStore, path: AttackPath) -> None:
        run.selected_path_id = path.path_id
        run.updated_at = utc_now()
        store.write_run(asdict(run))
        self._emit(
            store,
            run,
            stage="planning",
            event_type="PathSelected",
            actor="planner",
            data={"path": asdict(path), "priority_score": path.priority_score()},
            path_id=path.path_id,
        )

    def attach_evidence(
        self,
        run: TestRun,
        store: JsonlTraceStore,
        artifact_type: str,
        label: str,
        path: str,
        metadata: dict | None = None,
        path_id: str | None = None,
    ) -> EvidenceArtifact:
        artifact = EvidenceArtifact(
            artifact_id=new_id("ev"),
            run_id=run.run_id,
            path_id=path_id,
            artifact_type=artifact_type,
            label=label,
            path=path,
            metadata=metadata or {},
        )
        store.append_evidence(artifact)
        self._emit(
            store,
            run,
            stage="evidence",
            event_type="EvidenceAttached",
            actor="orchestrator",
            data={"artifact": asdict(artifact)},
            path_id=path_id,
            refs={"artifact_id": artifact.artifact_id},
        )
        return artifact

    def _emit(
        self,
        store: JsonlTraceStore,
        run: TestRun,
        stage: str,
        event_type: str,
        actor: str,
        data: dict,
        path_id: str | None = None,
        turn_id: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> None:
        store.append_event(
            TraceEvent(
                event_id=new_id("evt"),
                run_id=run.run_id,
                path_id=path_id,
                turn_id=turn_id,
                timestamp=utc_now(),
                stage=stage,
                event_type=event_type,
                actor=actor,
                data=data,
                refs=refs or {},
            )
        )
