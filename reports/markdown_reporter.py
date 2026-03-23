"""
reports/markdown_reporter.py
=============================
生成 Markdown + JSON 双格式报告。
"""

import json
import datetime
from pathlib import Path
from core.interfaces import Reporter, EvalResult, TestCase


class MarkdownReporter(Reporter):

    def generate(
        self,
        results:    list[EvalResult],
        cases:      list[TestCase],
        output_dir: str,
    ) -> str:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        md_file  = output_path / f"benchmark_{ts}.md"
        json_file= output_path / f"benchmark_{ts}.json"

        cases_map = {c.case_id: c for c in cases}
        total     = len(results)
        success   = sum(1 for r in results if r.attack_success)

        # ── Markdown ────────────────────────────────────────────
        lines = [
            f"# Agent Security Benchmark 报告",
            f"",
            f"生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## 汇总",
            f"",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 总用例数 | {total} |",
            f"| 攻击成功 | {success} |",
            f"| 成功率   | {success/total:.0%} |" if total else "| 成功率 | — |",
            f"",
            f"## 详细结果",
            f"",
            f"| 用例ID | 名称 | 结果 | 置信度 | 评估器 |",
            f"|--------|------|------|--------|--------|",
        ]
        for r in results:
            case   = cases_map.get(r.case_id)
            name   = case.name if case else r.case_id
            status = "✓ 成功" if r.attack_success else "✗ 失败"
            lines.append(
                f"| {r.case_id} | {name} | {status} | {r.confidence:.0%} | {r.evaluator_used} |"
            )

        lines += [
            "",
            "## 用例详情",
            "",
        ]
        for r in results:
            case = cases_map.get(r.case_id)
            lines += [
                f"### {r.case_id}",
                f"",
                f"- **结果**：{'攻击成功' if r.attack_success else '防御成功'}",
                f"- **置信度**：{r.confidence:.0%}",
                f"- **评估器**：{r.evaluator_used}",
                f"- **证据**：{r.evidence[:200]}",
                f"",
            ]
            if r.triggered_steps:
                lines.append("**触发步骤：**")
                for step in r.triggered_steps:
                    lines.append(f"- {step}")
                lines.append("")

        md_file.write_text("\n".join(lines), encoding="utf-8")

        # ── JSON ────────────────────────────────────────────────
        json_data = {
            "generated_at": datetime.datetime.now().isoformat(),
            "summary": {
                "total":        total,
                "success":      success,
                "success_rate": round(success / total, 4) if total else 0,
            },
            "results": [
                {
                    "case_id":        r.case_id,
                    "attack_success": r.attack_success,
                    "confidence":     r.confidence,
                    "evidence":       r.evidence,
                    "triggered_steps": r.triggered_steps,
                    "evaluator_used": r.evaluator_used,
                }
                for r in results
            ],
        }
        json_file.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n[report] Markdown → {md_file}")
        print(f"[report] JSON     → {json_file}")
        return str(md_file)
