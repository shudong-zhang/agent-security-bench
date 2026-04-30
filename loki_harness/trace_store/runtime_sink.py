"""Bridge runtime loop events into the per-run JSONL trace store."""

from __future__ import annotations

from loki_harness.domain import TestRun, TraceEvent, new_id, utc_now
from loki_harness.trace_store.jsonl_store import JsonlTraceStore


class RunTraceSink:
    """Runtime-facing trace sink bound to a single run."""

    def __init__(self, run: TestRun, store: JsonlTraceStore):
        self.run = run
        self.store = store

    def emit(
        self,
        *,
        stage: str,
        event_type: str,
        actor: str,
        data: dict,
        path_id: str | None = None,
        turn_id: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> None:
        self.store.append_event(
            TraceEvent(
                event_id=new_id("evt"),
                run_id=self.run.run_id,
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
