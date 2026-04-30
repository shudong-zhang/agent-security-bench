"""Target adapter boundary for objects under test."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from loki_harness.domain import TestRun, TestTask


@dataclass(slots=True)
class TargetObservation:
    observation_type: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TargetExecutionResult:
    run_id: str
    path_id: str | None
    target_name: str
    prompt: str
    raw_log: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    side_effects: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class TargetAdapter(Protocol):
    """Contract for a tested target environment."""

    def prepare(self, task: TestTask, run: TestRun) -> dict[str, Any]:
        """Prepare the target environment and return target metadata."""

    def execute(self, execution_plan: dict[str, Any]) -> TargetExecutionResult:
        """Execute a prepared plan against the target."""

    def observe(self) -> TargetObservation:
        """Capture a target-side observation."""

    def snapshot(self) -> dict[str, Any]:
        """Capture a richer target-side snapshot."""

    def cleanup(self) -> None:
        """Release resources after the run completes."""
