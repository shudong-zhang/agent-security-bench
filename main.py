"""
main.py
=======
框架入口。组装并运行完整的 Benchmark。

扩展方式：
  - 新增注入场景 → 在 environments/ 下加一个子类，在 build_runner() 里注册
  - 新增 Agent    → 在 agents/ 下加一个适配器
  - 新增评估器    → 在 evaluators/ 下加一个类，在 build_runner() 里注册
"""

import os
import logging
import argparse

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from core.runner     import BenchmarkRunner
from sandbox.manager import SandboxManager, ContainerConfig
from environments    import LocalFileEnvironment, MockWebEnvironment
from evaluators.evaluators import LLMJudgeEvaluator, ToolCallEvaluator, NetworkRequestEvaluator
from reports.markdown_reporter import MarkdownReporter


def build_runner(output_dir: str) -> BenchmarkRunner:
    from agents.claude_adapter import ClaudeAgentAdapter

    return BenchmarkRunner(
        dataset_dir  = "datasets/cases",
        environments = [
            LocalFileEnvironment(),
            # MockWebEnvironment(),
            # SkillFileEnvironment(),   ← 将来加
            # MockMCPEnvironment(),     ← 将来加
            # MockEmailEnvironment(),   ← 将来加
        ],
        agent = ClaudeAgentAdapter(
            model     = os.environ.get("BENCH_MODEL", "claude-sonnet-4-5"),
            max_turns = int(os.environ.get("BENCH_MAX_TURNS", "20")),
        ),
        evaluators = [
            LLMJudgeEvaluator(),
            ToolCallEvaluator(),
            NetworkRequestEvaluator(),
        ],
        reporter = MarkdownReporter(),
        sandbox  = SandboxManager(
            config = ContainerConfig(
                image           = os.environ.get("BENCH_DOCKER_IMAGE", "agent-bench:latest"),
                timeout_seconds = int(os.environ.get("BENCH_TIMEOUT", "300")),
                network_enabled = True,
            )
        ),
        max_workers = int(os.environ.get("BENCH_PARALLEL", "2")),
        output_dir  = output_dir,
    )


def main():
    parser = argparse.ArgumentParser(description="Agent Security Benchmark")
    parser.add_argument("--output",      default="reports",    help="报告输出目录")
    parser.add_argument("--category",    nargs="*",            help="只跑指定 category")
    parser.add_argument("--environment", nargs="*",            help="只跑指定 environment_type（如 local_file）")
    parser.add_argument("--tags",        nargs="*",            help="只跑包含指定 tag 的 case")
    parser.add_argument("--severity",                          help="只跑指定严重级别（high/medium/low）")
    args = parser.parse_args()

    runner = build_runner(output_dir=args.output)
    report_path = runner.run(
        filter_categories        = args.category,
        filter_environment_types = args.environment,
        filter_tags              = args.tags,
        filter_severity          = args.severity,
    )
    print(f"\n✅ Benchmark 完成！报告：{report_path}")


if __name__ == "__main__":
    main()
