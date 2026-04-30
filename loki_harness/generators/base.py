"""Contracts for attack-path generation and analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from loki_harness.domain import AttackPath, TestTask


@dataclass(slots=True)
class AnalysisContext:
    """Optional analysis hints derived from task metadata or workspace state."""

    workspace_files: list[str] = field(default_factory=list)
    target_signals: dict[str, Any] = field(default_factory=dict)


class ThreatAnalyzer(Protocol):
    """Generate structured candidate attack paths for a task."""

    def analyze(self, task: TestTask, context: AnalysisContext | None = None) -> list[AttackPath]:
        """Return ranked-later `AttackPath` candidates for the task."""
