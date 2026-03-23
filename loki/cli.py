"""
loki/cli.py
============
Loki 统一命令行入口。

子命令：
  loki run        运行安全测试（单轮）
  loki iterate    迭代攻击模式（多轮自动优化）
  loki generate   用 Claude API 自动生成测试用例
  loki dashboard  启动可视化 Dashboard

用法：
  python -m loki run --dataset datasets/cases --output reports/
  python -m loki iterate --dataset datasets/loki_cases --max-iter 3
  python -m loki generate --attack-paths datasets/attack_paths.json --count 2
  python -m loki dashboard --reports reports/ --port 8080
"""

import os
import sys
import argparse
import logging

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [loki] %(levelname)s %(message)s",
)


# ─────────────────────────────────────────
# 子命令：run
# ─────────────────────────────────────────
def cmd_run(args):
    """单轮 Benchmark 运行。"""
    from loki.config import build_runner
    runner = build_runner(
        dataset_dir = args.dataset,
        output_dir  = args.output,
        model       = args.model,
        parallel    = args.parallel,
        timeout     = args.timeout,
    )
    report = runner.run(
        filter_categories        = args.category,
        filter_environment_types = args.environment,
        filter_tags              = args.tags,
        filter_severity          = args.severity,
    )
    print(f"\n✅ 测试完成！报告 → {report}")


# ─────────────────────────────────────────
# 子命令：iterate
# ─────────────────────────────────────────
def cmd_iterate(args):
    """多轮迭代攻击：失败时自动重写 payload 重试。"""
    from loki.config import build_runner
    from loki.orchestrator import LokiOrchestrator
    from loki.payload_optimizer import PayloadOptimizer

    runner = build_runner(
        dataset_dir = args.dataset,
        output_dir  = args.output,
        model       = args.model,
        parallel    = 1,           # 迭代模式串行，避免状态混乱
        timeout     = args.timeout,
    )
    orchestrator = LokiOrchestrator(
        runner            = runner,
        payload_optimizer = PayloadOptimizer(model=args.model),
        max_iterations    = args.max_iter,
        output_dir        = args.output,
    )
    orchestrator.run(
        dataset_dir       = args.dataset,
        filter_categories = args.category,
        filter_tags       = args.tags,
    )


# ─────────────────────────────────────────
# 子命令：generate
# ─────────────────────────────────────────
def cmd_generate(args):
    """调用 Claude API，从 attack_paths.json 自动生成测试用例。"""
    from loki.generate_test_cases import TestCaseGenerator

    generator = TestCaseGenerator(model=args.model)
    cases = generator.generate_from_file(
        attack_paths_file = args.attack_paths,
        output_file       = args.output,
        count_per_path    = args.count,
    )
    print(f"\n✅ 生成完成！共 {len(cases)} 条用例 → {args.output}")


# ─────────────────────────────────────────
# 子命令：dashboard
# ─────────────────────────────────────────
def cmd_dashboard(args):
    """启动可视化 Dashboard。"""
    from loki.dashboard.server import start_server
    start_server(reports_dir=args.reports, port=args.port)


# ─────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "loki",
        description = "Loki —— AI Agent 安全评估平台",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = """
示例：
  python -m loki run --environment local_file
  python -m loki iterate --dataset datasets/loki_cases --max-iter 3
  python -m loki generate --attack-paths datasets/attack_paths.json
  python -m loki dashboard
        """,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── 公共参数 ────────────────────────────────────────────────
    def add_common(p):
        p.add_argument("--model",    default=os.environ.get("BENCH_MODEL", "claude-sonnet-4-5"),
                       help="使用的模型（默认：claude-sonnet-4-5）")
        p.add_argument("--timeout",  type=int, default=int(os.environ.get("BENCH_TIMEOUT", "300")),
                       help="单个用例超时秒数（默认：300）")

    def add_filter(p):
        p.add_argument("--category",    nargs="*", help="只跑指定 category")
        p.add_argument("--environment", nargs="*", help="只跑指定 environment_type（如 local_file）")
        p.add_argument("--tags",        nargs="*", help="只跑指定 tag")
        p.add_argument("--severity",               help="只跑指定严重级别（critical/high/medium/low）")

    # ── run ─────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="运行安全测试（单轮）")
    p_run.add_argument("--dataset",  default="datasets/cases",   help="测试用例目录")
    p_run.add_argument("--output",   default="reports",          help="报告输出目录")
    p_run.add_argument("--parallel", type=int,
                       default=int(os.environ.get("BENCH_PARALLEL", "2")), help="并发数（默认：2）")
    add_common(p_run)
    add_filter(p_run)
    p_run.set_defaults(func=cmd_run)

    # ── iterate ──────────────────────────────────────────────────
    p_iter = sub.add_parser("iterate", help="迭代攻击：失败时自动重写 payload 重试")
    p_iter.add_argument("--dataset",  default="datasets/loki_cases", help="测试用例目录")
    p_iter.add_argument("--output",   default="reports/loki",        help="报告输出目录")
    p_iter.add_argument("--max-iter", type=int, default=3,           help="最大迭代轮数（默认：3）")
    add_common(p_iter)
    p_iter.add_argument("--category", nargs="*", help="只跑指定 category")
    p_iter.add_argument("--tags",     nargs="*", help="只跑指定 tag")
    p_iter.set_defaults(func=cmd_iterate)

    # ── generate ─────────────────────────────────────────────────
    p_gen = sub.add_parser("generate", help="用 Claude API 自动生成测试用例")
    p_gen.add_argument("--attack-paths", default="datasets/attack_paths.json",
                       help="攻击路径 JSON 文件")
    p_gen.add_argument("--output",       default="datasets/loki_cases/generated_cases.json",
                       help="输出的测试用例 JSON 文件")
    p_gen.add_argument("--count",        type=int, default=2, help="每条路径生成的变体数（默认：2）")
    add_common(p_gen)
    p_gen.set_defaults(func=cmd_generate)

    # ── dashboard ────────────────────────────────────────────────
    p_dash = sub.add_parser("dashboard", help="启动可视化 Dashboard")
    p_dash.add_argument("--reports", default="reports", help="报告目录（默认：reports）")
    p_dash.add_argument("--port",    type=int, default=8080, help="HTTP 端口（默认：8080）")
    p_dash.set_defaults(func=cmd_dashboard)

    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
