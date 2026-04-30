"""Result types for source-centered harness preparation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loki_harness.domain import SinkSpec, SourceSpec


@dataclass(slots=True)
class PreparedSourceInput:
    source: SourceSpec
    content: str
    rationale: str
    materialized_files: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedSourceBundle:
    source: PreparedSourceInput
    sinks: list[SinkSpec]
    workspace_files: dict[str, str]
    prompt_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
