"""
core/dataset.py
===============
从 JSON 文件加载 TestCase，支持按 category / environment_type / tag 过滤。

文件格式：每个 .json 文件是一个数组，包含一条或多条 TestCase 记录。
"""

import os
import json
from typing import Optional
from core.interfaces import (
    TestCase, AttackPayload, AttackGoal, EnvironmentType, Modality
)


class DatasetLoader:

    def __init__(self, dataset_dir: str):
        self.dataset_dir = dataset_dir

    def load_all(self) -> list[TestCase]:
        cases = []
        for fname in sorted(os.listdir(self.dataset_dir)):
            if fname.endswith(".json"):
                path = os.path.join(self.dataset_dir, fname)
                cases.extend(self._load_file(path))
        return cases

    def load_filtered(
    self,
    categories:        Optional[list[str]] = None,
    environment_types: Optional[list[str]] = None,
    tags:              Optional[list[str]] = None,
    severity:          Optional[str]       = None,
) -> list[TestCase]:
        all_cases = self.load_all()
        return [
            c for c in all_cases
            if (not categories        or c.category in categories)
            and (not environment_types or c.environment_type.value in environment_types)
            and (not tags              or any(t in c.tags for t in tags))
            and (not severity          or c.severity == severity)
        ]

    def _load_file(self, path: str) -> list[TestCase]:
        with open(path) as f:
            data = json.load(f)

        # 支持单条或列表
        records = data if isinstance(data, list) else [data]
        return [self._parse(r) for r in records]

    def _parse(self, r: dict) -> TestCase:
        payload_raw = r["payload"]
        payload = AttackPayload(
            content      = payload_raw["content"],
            modality     = Modality(payload_raw.get("modality", "text")),
            position_hint= payload_raw.get("position_hint"),
            metadata     = payload_raw.get("metadata", {}),
        )
        return TestCase(
            case_id          = r["case_id"],
            name             = r["name"],
            category         = r["category"],
            user_task        = r["user_task"],
            attack_goal      = AttackGoal(r["attack_goal"]),
            attack_goal_desc = r["attack_goal_desc"],
            environment_type     = EnvironmentType(r["environment_type"]),
            environment_config   = r.get("environment_config", {}),
            attack_path      = r.get("attack_path", []),
            payload          = payload,
            success_criteria = r["success_criteria"],
            severity         = r.get("severity", "high"),
            tags             = r.get("tags", []),
            references       = r.get("references", []),
        )
