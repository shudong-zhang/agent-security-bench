"""
core/runner.py
==============
Benchmark 主调度器。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from core.interfaces import (
    TestCase, AgentTrace, EvalResult,
    Environment, AgentAdapter, Evaluator, Reporter,
)
from core.dataset import DatasetLoader
from sandbox.manager import SandboxManager

logger = logging.getLogger("bench.runner")


class BenchmarkRunner:

    def __init__(
        self,
        dataset_dir:  str,
        environments: list[Environment],
        agent:        AgentAdapter,
        evaluators:   list[Evaluator],
        reporter:     Reporter,
        sandbox:      Optional[SandboxManager] = None,
        max_workers:  int = 4,
        output_dir:   str = "reports",
    ):
        self.loader      = DatasetLoader(dataset_dir)
        self.agent       = agent
        self.evaluators  = evaluators
        self.reporter    = reporter
        self.sandbox     = sandbox or SandboxManager()
        self.max_workers = max_workers
        self.output_dir  = output_dir
        self._env_map    = {e.environment_type: e for e in environments}

    def run(
        self,
        filter_categories:        Optional[list[str]] = None,
        filter_environment_types: Optional[list[str]] = None,
        filter_tags:              Optional[list[str]] = None,
        filter_severity:          Optional[str]       = None,
    ) -> str:
        cases = self.loader.load_filtered(
            categories        = filter_categories,
            environment_types = filter_environment_types,
            tags              = filter_tags,
            severity          = filter_severity,
        )
        logger.info(f"Loaded {len(cases)} test cases")
        print(f"\n[info] Running {len(cases)} case(s), parallelism={self.max_workers}")

        results     = self._run_cases(cases)
        report_path = self.reporter.generate(results, cases, self.output_dir)
        logger.info(f"Report saved to {report_path}")
        return report_path

    def _run_cases(self, cases: list[TestCase]) -> list[EvalResult]:
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._run_single, c): c for c in cases}
            for future in as_completed(futures):
                case = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    status = "✓ ATTACK SUCCESS" if result.attack_success else "✗ defended"
                    logger.info(f"[{case.case_id}] {status} (conf={result.confidence:.2f})")
                except Exception as e:
                    logger.error(f"[{case.case_id}] ERROR: {e}", exc_info=True)
                    results.append(self._error_result(case, str(e)))
        return results

    def _run_single(self, case: TestCase) -> EvalResult:
        import time as _time
        t0 = _time.time()

        env         = self._get_environment(case)
        sandbox_dir = self.sandbox.prepare_sandbox(case)
        print(f"[setup]  {case.case_id}  env={case.environment_type.value}  sandbox={sandbox_dir.name}")

        try:
            # Step 1: 构建环境
            env_ctx = env.build(case, sandbox_dir)
            print(f"[inject] {case.case_id}  environment ready  notes={env_ctx.notes}")

            # Step 2: 生成 Agent 命令
            agent_cmd = self.agent.build_agent_cmd(case)

            # Step 3: 跑容器
            print(f"[run]    {case.case_id}  starting container ...")
            results_dir = Path(self.output_dir) / "sandbox_results"
            raw_trace = self.sandbox.run(
                case        = case,
                sandbox_dir = sandbox_dir,
                task_prompt = case.user_task,
                agent_cmd   = agent_cmd,
                env_ctx     = env_ctx,
                results_dir = results_dir,
            )
            duration = round(_time.time() - t0, 1)
            print(f"[done]   {case.case_id}  container finished  ({duration}s)")

            # Step 4: 解析轨迹
            trace = self.agent.parse_trace(raw_trace)
            print(f"[parse]  {case.case_id}  tool_calls={len(trace.tool_calls)}")

            # Step 5: 评估（传入 sandbox_dir）
            result = self._evaluate(trace, case)
            verdict = "ATTACK SUCCESS" if result.attack_success else "defended"
            print(f"[eval]   {case.case_id}  {verdict}  (conf={result.confidence:.0%})")

        finally:
            env.teardown()
            self.sandbox.cleanup(sandbox_dir, keep=True)

        return result

    def _get_environment(self, case: TestCase) -> Environment:
        env = self._env_map.get(case.environment_type)
        if not env:
            raise NotImplementedError(
                f"No Environment registered for '{case.environment_type}'. "
                f"Available: {list(self._env_map.keys())}"
            )
        return env

    def _evaluate(self, trace: AgentTrace, case: TestCase) -> EvalResult:
        criteria_type = case.success_criteria.get("type", "llm_judge")
        # sandbox_dir 从 trace.metadata 取，容器结束后指向 results_dir 里的归档副本
        sandbox_dir = Path(trace.metadata["sandbox_dir"]) \
            if "sandbox_dir" in trace.metadata else None

        for evaluator in self.evaluators:
            if evaluator.can_handle(criteria_type):
                return evaluator.evaluate(trace, case, sandbox_dir=sandbox_dir)

        raise NotImplementedError(f"No Evaluator for criteria type '{criteria_type}'")

    def _error_result(self, case: TestCase, error_msg: str) -> EvalResult:
        return EvalResult(
            case_id        = case.case_id,
            attack_success = False,
            confidence     = 0.0,
            evidence       = f"Runner error: {error_msg}",
            triggered_steps= [],
            evaluator_used = "error",
        )
