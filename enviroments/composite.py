"""
environments/composite.py
==========================
组合环境：把多个 Environment 合并成一个。

用于复杂场景，例如：
  - Agent 先访问网页获取指令，再操作本地文件
  - Agent 使用 MCP 工具，同时有本地文件作为上下文

示例：
  env = CompositeEnvironment(
      LocalFileEnvironment(),
      MockWebEnvironment(),
  )
  ctx = env.build(case, sandbox_dir)
  # ctx 包含两个环境的 extra_env、extra_mounts、extra_hosts 合并结果
"""

from pathlib import Path
from core.interfaces import Environment, EnvironmentType, EnvironmentContext, TestCase


class CompositeEnvironment(Environment):
    environment_type = EnvironmentType.COMPOSITE

    def __init__(self, *envs: Environment):
        self._envs = list(envs)
        self._contexts: list[EnvironmentContext] = []

    def build(self, case: TestCase, sandbox_dir: Path) -> EnvironmentContext:
        self._contexts = [env.build(case, sandbox_dir) for env in self._envs]
        return EnvironmentContext(
            extra_env    = self._merge_dicts([c.extra_env    for c in self._contexts]),
            extra_mounts = self._merge_lists([c.extra_mounts for c in self._contexts]),
            extra_hosts  = self._merge_dicts([c.extra_hosts  for c in self._contexts]),
            services     = self._merge_lists([c.services     for c in self._contexts]),
            notes        = self._merge_dicts([c.notes        for c in self._contexts]),
        )

    def teardown(self):
        for env in reversed(self._envs):
            try:
                env.teardown()
            except Exception:
                pass

    @staticmethod
    def _merge_dicts(dicts: list[dict]) -> dict:
        result = {}
        for d in dicts:
            result.update(d)
        return result

    @staticmethod
    def _merge_lists(lists: list[list]) -> list:
        result = []
        for lst in lists:
            result.extend(lst)
        return result
