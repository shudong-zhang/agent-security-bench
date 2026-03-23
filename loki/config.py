"""
loki/config.py
===============
Loki 运行时配置工厂。
统一组装 BenchmarkRunner，供 CLI 和外部调用使用。
"""

import os
from core.runner import BenchmarkRunner
from sandbox.manager import SandboxManager, ContainerConfig
from environments.local_file import LocalFileEnvironment
from environments.skill_file import SkillFileEnvironment
from agents.claude_adapter import ClaudeAgentAdapter
from evaluators.evaluators import LLMJudgeEvaluator, ToolCallEvaluator, NetworkRequestEvaluator
from reports.markdown_reporter import MarkdownReporter


def build_runner(
    dataset_dir: str = "datasets/cases",
    output_dir:  str = "reports",
    model:       str = None,
    parallel:    int = None,
    timeout:     int = None,
) -> BenchmarkRunner:
    """
    组装并返回一个完整的 BenchmarkRunner。
    所有参数都有环境变量兜底，方便 Docker / CI 覆盖。
    """
    model    = model    or os.environ.get("BENCH_MODEL",        "claude-sonnet-4-5")
    parallel = parallel or int(os.environ.get("BENCH_PARALLEL", "2"))
    timeout  = timeout  or int(os.environ.get("BENCH_TIMEOUT",  "300"))
    image    = os.environ.get("BENCH_DOCKER_IMAGE", "agent-bench:latest")

    return BenchmarkRunner(
        dataset_dir  = dataset_dir,
        environments = [
            LocalFileEnvironment(),
            SkillFileEnvironment(),
        ],
        agent = ClaudeAgentAdapter(
            model     = model,
            max_turns = int(os.environ.get("BENCH_MAX_TURNS", "20")),
        ),
        evaluators = [
            LLMJudgeEvaluator(),
            ToolCallEvaluator(),
            NetworkRequestEvaluator(),
        ],
        reporter    = MarkdownReporter(),
        sandbox     = SandboxManager(
            config = ContainerConfig(
                image           = image,
                timeout_seconds = timeout,
                network_enabled = True,
            )
        ),
        max_workers = parallel,
        output_dir  = output_dir,
    )
