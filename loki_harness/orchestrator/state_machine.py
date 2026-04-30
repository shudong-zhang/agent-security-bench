"""Run state transition rules for the Loki harness."""

from __future__ import annotations

from loki_harness.domain import RunState


ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.PREPARING, RunState.ABORTED},
    RunState.PREPARING: {RunState.ANALYZING_TARGET, RunState.FAILED, RunState.ABORTED},
    RunState.ANALYZING_TARGET: {RunState.BUILDING_ATTACK_GRAPH, RunState.FAILED, RunState.ABORTED},
    RunState.BUILDING_ATTACK_GRAPH: {RunState.PRIORITIZING_PATHS, RunState.FAILED, RunState.ABORTED},
    RunState.PRIORITIZING_PATHS: {RunState.CONTINUING, RunState.EXECUTING_PATH, RunState.FAILED, RunState.ABORTED},
    RunState.EXECUTING_PATH: {RunState.VERIFYING_OUTCOME, RunState.NEEDS_REVIEW, RunState.FAILED, RunState.ABORTED},
    RunState.VERIFYING_OUTCOME: {RunState.CONTINUING, RunState.COMPLETED, RunState.FAILED, RunState.NEEDS_REVIEW, RunState.ABORTED},
    RunState.NEEDS_REVIEW: {RunState.CONTINUING, RunState.ABORTED, RunState.FAILED},
    RunState.CONTINUING: {RunState.EXECUTING_PATH, RunState.COMPLETED, RunState.FAILED, RunState.ABORTED},
    RunState.COMPLETED: set(),
    RunState.FAILED: set(),
    RunState.ABORTED: set(),
}


def assert_transition(current: RunState, new: RunState) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(f"Invalid run state transition: {current.value} -> {new.value}")
