"""Operational ranking for attack paths."""

from __future__ import annotations

from loki_harness.domain import AttackPath


class PathRanker:
    def rank(self, paths: list[AttackPath]) -> list[AttackPath]:
        return sorted(paths, key=lambda path: path.priority_score(), reverse=True)
