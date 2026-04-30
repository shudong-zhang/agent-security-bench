"""Verifier boundary for converting traces and evidence into verdicts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class VerificationResult:
    status: str
    confidence: float
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Verifier(Protocol):
    def verify(
        self,
        *,
        run_id: str,
        path_id: str | None,
        trace_path: str,
        evidence_dir: str,
        criteria: dict[str, Any],
    ) -> VerificationResult:
        """Return an independent verdict for a path attempt."""
