"""Unified domain schema for the rebuilt Loki harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RunState(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    ANALYZING_TARGET = "analyzing_target"
    BUILDING_ATTACK_GRAPH = "building_attack_graph"
    PRIORITIZING_PATHS = "prioritizing_paths"
    EXECUTING_PATH = "executing_path"
    VERIFYING_OUTCOME = "verifying_outcome"
    NEEDS_REVIEW = "needs_review"
    CONTINUING = "continuing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(slots=True)
class TargetProfile:
    target_id: str
    target_type: str
    display_name: str
    access_level: str
    source_availability: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TestTask:
    task_id: str
    name: str
    target: TargetProfile
    description: str
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    success_criteria: dict[str, Any] = field(default_factory=dict)
    reporting_requirements: dict[str, Any] = field(default_factory=dict)
    run_budget: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AttackPath:
    path_id: str
    title: str
    entry_vector: str
    preconditions: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    attack_steps: list[str] = field(default_factory=list)
    expected_observables: list[str] = field(default_factory=list)
    impact_type: str = ""
    estimated_impact: float = 0.0
    estimated_exploitability: float = 0.0
    expected_cost: float = 1.0
    information_gain: float = 0.0
    confidence: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    def priority_score(self) -> float:
        cost = self.expected_cost if self.expected_cost > 0 else 1.0
        return (
            self.estimated_impact
            * self.estimated_exploitability
            * self.confidence
            * max(self.information_gain, 0.1)
        ) / cost


@dataclass(slots=True)
class SourceSpec:
    source_id: str
    source_type: str
    origin: str
    carrier_format: str
    trust_boundary: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SinkSpec:
    sink_id: str
    sink_type: str
    component: str
    sensitivity: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PropagationObservation:
    observation_id: str
    source_id: str
    sink_id: str | None
    stage: str
    signal_type: str
    summary: str
    turn_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SinkHit:
    hit_id: str
    sink_id: str
    status: str
    confidence: float
    summary: str
    turn_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceArtifact:
    artifact_id: str
    run_id: str
    path_id: str | None
    artifact_type: str
    label: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class TraceEvent:
    event_id: str
    run_id: str
    path_id: str | None
    turn_id: str | None
    timestamp: str
    stage: str
    event_type: str
    actor: str
    data: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class PathVerdict:
    run_id: str
    path_id: str
    status: str
    confidence: float
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunVerdict:
    run_id: str
    status: str
    confidence: float
    summary: str
    path_verdicts: list[PathVerdict] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KnowledgeEntry:
    entry_id: str
    run_id: str
    source_type: str
    title: str
    content: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class TestRun:
    run_id: str
    task: TestTask
    state: RunState = RunState.QUEUED
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    selected_path_id: str | None = None
    trace_path: str | None = None
    evidence_dir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def transition(self, state: RunState) -> None:
        self.state = state
        self.updated_at = utc_now()
