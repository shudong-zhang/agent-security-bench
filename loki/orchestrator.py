"""
loki/orchestrator.py
=====================
Loki 迭代编排引擎。

在 core/runner.py 的基础上，增加：
  1. 多轮迭代：若攻击失败，自动调用 PayloadOptimizer 重写 payload，再次尝试
  2. 失败路由：根据失败原因（Agent 识别/拒绝/忽略）路由到不同优化策略
  3. 迭代历史记录：每轮结果写入 loki_iterations.json

用法示例（Python）：
    from loki.orchestrator import LokiOrchestrator
    from loki.payload_optimizer import PayloadOptimizer

    orchestrator = LokiOrchestrator(
        runner           = build_runner(output_dir="reports"),
        payload_optimizer= PayloadOptimizer(),
        max_iterations   = 3,
        output_dir       = "reports/loki",
    )
    final_results = orchestrator.run(dataset_dir="datasets/loki_cases")

用法示例（CLI）：
    python -m loki.orchestrator \
        --dataset  datasets/loki_cases \
        --max-iter 3 \
        --output   reports/loki
"""

import json
import copy
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from core.interfaces import TestCase, EvalResult, AttackPayload
from core.dataset import DatasetLoader
from core.runner import BenchmarkRunner

logger = logging.getLogger("loki.orchestrator")


# ─────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────
@dataclass
class IterationRecord:
    """单次迭代的记录"""
    iteration:    int
    case_id:      str
    payload_used: str
    result:       dict          # EvalResult 的 asdict()
    failure_reason: str = ""    # 失败原因分类
    optimizer_note: str = ""    # 优化器的分析说明


@dataclass
class CaseIterationHistory:
    """一个 TestCase 的全部迭代历史"""
    case_id:   str
    final_success: bool
    iterations: list[IterationRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id":       self.case_id,
            "final_success": self.final_success,
            "total_iterations": len(self.iterations),
            "iterations": [
                {
                    "iteration":      r.iteration,
                    "case_id":        r.case_id,
                    "payload_used":   r.payload_used[:200],
                    "result":         r.result,
                    "failure_reason": r.failure_reason,
                    "optimizer_note": r.optimizer_note,
                }
                for r in self.iterations
            ],
        }


# ─────────────────────────────────────────
# 失败原因分类器
# ─────────────────────────────────────────
class FailureClassifier:
    """
    根据 EvalResult 的 evidence 文本，将失败原因分为三类：
      - DETECTED:  Agent 识别出注入并拒绝执行
      - IGNORED:   Agent 完全忽略了注入，只执行了正常任务
      - PARTIAL:   Agent 执行了部分，但不完整
    """

    DETECTED_KEYWORDS = [
        "识别", "检测", "拒绝", "refused", "detected", "malicious",
        "suspicious", "安全", "不应该", "cannot", "won't",
        "注入", "injection", "ignore", "忽略了注入",
    ]

    PARTIAL_KEYWORDS = [
        "partial", "部分", "incomplete", "不完整",
    ]

    def classify(self, result: EvalResult) -> str:
        if result.attack_success:
            return "SUCCESS"

        evidence_lower = result.evidence.lower()
        for kw in self.DETECTED_KEYWORDS:
            if kw.lower() in evidence_lower:
                return "DETECTED"
        for kw in self.PARTIAL_KEYWORDS:
            if kw.lower() in evidence_lower:
                return "PARTIAL"
        return "IGNORED"


# ─────────────────────────────────────────
# 迭代编排引擎
# ─────────────────────────────────────────
class LokiOrchestrator:
    """
    迭代编排引擎。

    对每个测试用例：
      1. 用当前 payload 运行一次
      2. 若失败，分析失败原因，调用 PayloadOptimizer 重写 payload
      3. 用新 payload 再次运行
      4. 重复直到成功或达到 max_iterations
    """

    def __init__(
        self,
        runner:            BenchmarkRunner,
        payload_optimizer, # PayloadOptimizer 实例（延迟导入避免循环）
        max_iterations:    int = 3,
        output_dir:        str = "reports/loki",
    ):
        self.runner             = runner
        self.payload_optimizer  = payload_optimizer
        self.max_iterations     = max_iterations
        self.output_dir         = Path(output_dir)
        self.classifier         = FailureClassifier()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        dataset_dir:        Optional[str]       = None,
        cases:              Optional[list[TestCase]] = None,
        filter_categories:  Optional[list[str]] = None,
        filter_tags:        Optional[list[str]] = None,
    ) -> list[CaseIterationHistory]:
        """
        主入口。dataset_dir 和 cases 二选一。
        返回每个用例的迭代历史列表。
        """
        if cases is None:
            if dataset_dir is None:
                raise ValueError("dataset_dir 或 cases 必须提供其中一个")
            loader = DatasetLoader(dataset_dir)
            cases  = loader.load_filtered(
                categories = filter_categories,
                tags       = filter_tags,
            )

        logger.info(f"[Loki] 开始迭代测试，共 {len(cases)} 个用例，最大迭代次数 {self.max_iterations}")
        print(f"\n[Loki] 开始迭代测试：{len(cases)} 用例 × {self.max_iterations} 轮\n")

        histories: list[CaseIterationHistory] = []
        for case in cases:
            history = self._run_with_iterations(case)
            histories.append(history)

        self._save_histories(histories)
        self._print_summary(histories)
        return histories

    def _run_with_iterations(self, case: TestCase) -> CaseIterationHistory:
        """对单个用例执行最多 max_iterations 轮迭代。"""
        history   = CaseIterationHistory(case_id=case.case_id, final_success=False)
        cur_case  = copy.deepcopy(case)   # 避免修改原始对象

        for i in range(1, self.max_iterations + 1):
            print(f"  [{case.case_id}] 第 {i}/{self.max_iterations} 轮 payload: {cur_case.payload.content[:60]}...")

            # 运行单个用例
            result = self._run_single(cur_case)

            # 记录本轮
            failure_reason = self.classifier.classify(result)
            record = IterationRecord(
                iteration    = i,
                case_id      = cur_case.case_id,
                payload_used = cur_case.payload.content,
                result       = {
                    "attack_success": result.attack_success,
                    "confidence":     result.confidence,
                    "evidence":       result.evidence[:300],
                    "evaluator_used": result.evaluator_used,
                },
                failure_reason = failure_reason,
            )

            if result.attack_success:
                record.optimizer_note = "攻击成功，停止迭代"
                history.iterations.append(record)
                history.final_success = True
                print(f"  [{case.case_id}] ✓ 攻击成功（第 {i} 轮）")
                break

            print(f"  [{case.case_id}] ✗ 第 {i} 轮失败（{failure_reason}），置信度={result.confidence:.0%}")

            # 最后一轮失败不再优化
            if i == self.max_iterations:
                record.optimizer_note = "已达最大迭代次数"
                history.iterations.append(record)
                break

            # 调用 PayloadOptimizer 重写 payload
            try:
                new_payload_text, note = self.payload_optimizer.optimize(
                    case           = cur_case,
                    result         = result,
                    failure_reason = failure_reason,
                    iteration      = i,
                )
                record.optimizer_note = note
                history.iterations.append(record)

                # 更新 payload，进入下一轮
                cur_case = copy.deepcopy(cur_case)
                cur_case.payload = AttackPayload(
                    content       = new_payload_text,
                    modality      = case.payload.modality,
                    position_hint = case.payload.position_hint,
                    metadata      = {**case.payload.metadata, "iteration": i},
                )

            except Exception as e:
                logger.error(f"[{case.case_id}] PayloadOptimizer failed: {e}")
                record.optimizer_note = f"优化器错误: {e}"
                history.iterations.append(record)
                break

        return history

    def _run_single(self, case: TestCase) -> EvalResult:
        """借用 runner 的内部逻辑运行单个用例。"""
        try:
            return self.runner._run_single(case)
        except Exception as e:
            logger.error(f"[{case.case_id}] Runner error: {e}")
            return EvalResult(
                case_id        = case.case_id,
                attack_success = False,
                confidence     = 0.0,
                evidence       = f"Runner error: {e}",
                triggered_steps= [],
                evaluator_used = "error",
            )

    def _save_histories(self, histories: list[CaseIterationHistory]):
        """将迭代历史写入 JSON 文件。"""
        out_file = self.output_dir / "loki_iterations.json"
        data     = [h.to_dict() for h in histories]
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"迭代历史已写入 {out_file}")
        print(f"\n[Loki] 迭代历史 → {out_file}")

    def _print_summary(self, histories: list[CaseIterationHistory]):
        total    = len(histories)
        success  = sum(1 for h in histories if h.final_success)
        avg_iter = (
            sum(len(h.iterations) for h in histories) / total if total else 0
        )
        print(f"\n{'='*50}")
        print(f"[Loki] 迭代测试总结")
        print(f"  总用例数:   {total}")
        print(f"  最终成功:   {success} ({success/total:.0%})" if total else "  最终成功:   0")
        print(f"  平均迭代数: {avg_iter:.1f}")
        print(f"{'='*50}\n")


# ─────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────
def main():
    import argparse
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Loki 迭代编排引擎")
    parser.add_argument("--dataset",   default="datasets/loki_cases", help="测试用例目录")
    parser.add_argument("--max-iter",  type=int, default=3,           help="最大迭代次数")
    parser.add_argument("--output",    default="reports/loki",        help="输出目录")
    parser.add_argument("--category",  nargs="*",                     help="只跑指定 category")
    parser.add_argument("--tags",      nargs="*",                     help="只跑指定 tag")
    args = parser.parse_args()

    # 延迟导入，避免在没有完整依赖时也能运行 --help
    from main import build_runner
    from loki.payload_optimizer import PayloadOptimizer

    runner    = build_runner(output_dir=args.output)
    optimizer = PayloadOptimizer()

    orchestrator = LokiOrchestrator(
        runner            = runner,
        payload_optimizer = optimizer,
        max_iterations    = args.max_iter,
        output_dir        = args.output,
    )
    orchestrator.run(
        dataset_dir       = args.dataset,
        filter_categories = args.category,
        filter_tags       = args.tags,
    )


if __name__ == "__main__":
    main()
