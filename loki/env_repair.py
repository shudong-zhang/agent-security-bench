"""
loki/env_repair.py
===================
环境修复 Agent：比较期望的环境状态 vs 容器运行后的实际状态，诊断并修复差异。

主要用途：
  - 发现 TestCase 的 environment_config 配置错误导致的环境问题
  - 识别"注入文件没有生成"、"受害文件内容不对"等环境错误
  - 区分"环境错误导致的失败"和"Agent 防御导致的失败"

使用方式：
    from loki.env_repair import EnvRepairAgent

    repair_agent = EnvRepairAgent()

    # 检查单个用例的环境
    report = repair_agent.check(case, sandbox_dir)
    if report.has_issues:
        fixed_config = repair_agent.suggest_fix(report)
        print(fixed_config)

    # 批量分析所有失败用例
    issues = repair_agent.analyze_failures(histories, cases_map)
"""

import json
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.interfaces import TestCase, EnvironmentType

logger = logging.getLogger("loki.env_repair")


# ─────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────
@dataclass
class EnvIssue:
    """单个环境问题"""
    severity:    str   # "error" / "warning" / "info"
    issue_type:  str   # 问题类型标识
    description: str   # 人读的描述
    fix_hint:    str   # 修复建议


@dataclass
class EnvCheckReport:
    """环境检查报告"""
    case_id:     str
    sandbox_dir: str
    issues:      list[EnvIssue] = field(default_factory=list)
    actual_files: list[str]     = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    def summary(self) -> str:
        errors   = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        if not self.issues:
            return f"[{self.case_id}] 环境检查通过 ✓"
        lines = [f"[{self.case_id}] 环境检查结果：{len(errors)} 个错误，{len(warnings)} 个警告"]
        for issue in self.issues:
            icon = "✗" if issue.severity == "error" else "⚠"
            lines.append(f"  {icon} [{issue.issue_type}] {issue.description}")
            lines.append(f"    → 修复建议: {issue.fix_hint}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "case_id":     self.case_id,
            "sandbox_dir": self.sandbox_dir,
            "has_issues":  self.has_issues,
            "actual_files": self.actual_files,
            "issues": [
                {
                    "severity":    i.severity,
                    "issue_type":  i.issue_type,
                    "description": i.description,
                    "fix_hint":    i.fix_hint,
                }
                for i in self.issues
            ],
        }


# ─────────────────────────────────────────
# 检查器（按环境类型）
# ─────────────────────────────────────────
class _LocalFileChecker:
    """检查 local_file 类型环境"""

    def check(self, case: TestCase, sandbox_dir: Path) -> list[EnvIssue]:
        issues  = []
        cfg     = case.environment_config
        filename = cfg.get("filename", "document.txt")
        template = cfg.get("template", "")

        # 1. 注入文件是否存在
        inject_file = sandbox_dir / filename
        if not inject_file.exists():
            issues.append(EnvIssue(
                severity    = "error",
                issue_type  = "INJECT_FILE_MISSING",
                description = f"注入文件 '{filename}' 不存在于 sandbox",
                fix_hint    = f"检查 environment_config.filename 配置，或确认 LocalFileEnvironment.build() 正确执行",
            ))
        else:
            # 2. 注入文件内容是否包含 payload
            content = inject_file.read_text(encoding="utf-8", errors="replace")
            if case.payload.content not in content:
                # 可能是模板里没有 {payload}
                if "{payload}" not in template:
                    issues.append(EnvIssue(
                        severity    = "error",
                        issue_type  = "TEMPLATE_NO_PLACEHOLDER",
                        description = "template 字段中没有 {payload} 占位符，payload 未被注入",
                        fix_hint    = "在 environment_config.template 中加入 {payload} 占位符",
                    ))
                else:
                    issues.append(EnvIssue(
                        severity    = "warning",
                        issue_type  = "PAYLOAD_NOT_IN_FILE",
                        description = "注入文件存在，但文件内容中未找到 payload（可能已被修改）",
                        fix_hint    = "检查 build() 后是否有其他程序覆盖了文件内容",
                    ))

        # 3. extra_files 是否都存在
        extra_files = cfg.get("extra_files", {})
        for fname in extra_files:
            ef = sandbox_dir / fname
            if not ef.exists():
                issues.append(EnvIssue(
                    severity    = "error",
                    issue_type  = "EXTRA_FILE_MISSING",
                    description = f"预置文件 '{fname}' 不存在",
                    fix_hint    = f"检查 environment_config.extra_files['{fname}'] 的内容是否为空或构建失败",
                ))
            elif ef.stat().st_size == 0 and extra_files[fname]:
                issues.append(EnvIssue(
                    severity    = "warning",
                    issue_type  = "EXTRA_FILE_EMPTY",
                    description = f"预置文件 '{fname}' 存在但为空",
                    fix_hint    = f"检查 environment_config.extra_files['{fname}'] 的值是否正确",
                ))

        return issues


class _SkillFileChecker:
    """检查 skill_file 类型环境"""

    def check(self, case: TestCase, sandbox_dir: Path) -> list[EnvIssue]:
        issues     = []
        cfg        = case.environment_config
        skill_name = cfg.get("skill_name", "")

        skill_dir = sandbox_dir / ".claude" / "skills" / skill_name
        if not skill_dir.exists():
            issues.append(EnvIssue(
                severity    = "error",
                issue_type  = "SKILL_DIR_MISSING",
                description = f"skill 目录 .claude/skills/{skill_name} 不存在",
                fix_hint    = f"确认 data/skill_file/skills/{skill_name}/ 目录存在且包含 SKILL.md",
            ))
            return issues

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            issues.append(EnvIssue(
                severity    = "error",
                issue_type  = "SKILL_MD_MISSING",
                description = "SKILL.md 文件不存在",
                fix_hint    = f"在 data/skill_file/skills/{skill_name}/ 下创建 SKILL.md",
            ))
        else:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            if case.payload.content not in content:
                inject_method = cfg.get("inject_method", "line")
                issues.append(EnvIssue(
                    severity    = "warning",
                    issue_type  = "PAYLOAD_NOT_IN_SKILL",
                    description = f"SKILL.md 中未找到 payload（inject_method={inject_method}）",
                    fix_hint    = "检查注入方法配置，或确认 SKILL.md 格式与注入方法兼容",
                ))

        return issues


# ─────────────────────────────────────────
# 主类
# ─────────────────────────────────────────
class EnvRepairAgent:
    """
    环境状态检查与修复建议生成器。
    """

    _CHECKERS = {
        EnvironmentType.LOCAL_FILE:  _LocalFileChecker(),
        EnvironmentType.SKILL_FILE:  _SkillFileChecker(),
    }

    def check(self, case: TestCase, sandbox_dir: Path) -> EnvCheckReport:
        """
        检查指定 sandbox_dir 的环境状态，与 TestCase 期望对比。
        返回 EnvCheckReport。
        """
        sandbox_dir = Path(sandbox_dir)
        report = EnvCheckReport(
            case_id     = case.case_id,
            sandbox_dir = str(sandbox_dir),
        )

        # 列出实际文件
        if sandbox_dir.exists():
            report.actual_files = [
                f.name for f in sandbox_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
        else:
            report.issues.append(EnvIssue(
                severity    = "error",
                issue_type  = "SANDBOX_DIR_MISSING",
                description = f"sandbox 目录不存在: {sandbox_dir}",
                fix_hint    = "确认 SandboxManager.prepare_sandbox() 正确执行",
            ))
            return report

        # 按环境类型检查
        checker = self._CHECKERS.get(case.environment_type)
        if checker:
            issues = checker.check(case, sandbox_dir)
            report.issues.extend(issues)
        else:
            report.issues.append(EnvIssue(
                severity    = "info",
                issue_type  = "NO_CHECKER",
                description = f"环境类型 '{case.environment_type.value}' 暂无专用检查器",
                fix_hint    = f"可在 loki/env_repair.py 中为 {case.environment_type.value} 添加检查器",
            ))

        # 通用检查：agent_stdout.txt 是否存在（说明容器正常运行过）
        if not (sandbox_dir / "agent_stdout.txt").exists():
            report.issues.append(EnvIssue(
                severity    = "warning",
                issue_type  = "NO_STDOUT_LOG",
                description = "agent_stdout.txt 不存在，容器可能未正常运行",
                fix_hint    = "检查 Docker 镜像和容器运行日志",
            ))

        return report

    def suggest_fix(self, report: EnvCheckReport, case: TestCase) -> dict:
        """
        根据 EnvCheckReport，生成修复后的 environment_config 建议。
        返回修正后的 environment_config dict。
        """
        fixed = dict(case.environment_config)
        suggestions = []

        for issue in report.issues:
            if issue.issue_type == "TEMPLATE_NO_PLACEHOLDER":
                # 在模板末尾追加 payload 占位符
                template = fixed.get("template", "")
                fixed["template"] = template + "\n\n{payload}\n"
                suggestions.append("在 template 末尾追加了 {payload} 占位符")

            elif issue.issue_type == "INJECT_FILE_MISSING":
                # 确认 filename 字段存在
                if "filename" not in fixed:
                    fixed["filename"] = "document.txt"
                suggestions.append(f"确认 filename={fixed['filename']}")

        return {
            "fixed_environment_config": fixed,
            "applied_fixes":            suggestions,
            "remaining_issues": [
                i.description for i in report.issues
                if i.severity == "error"
            ],
        }

    def analyze_failures(
        self,
        histories:  list,               # list[CaseIterationHistory]
        cases_map:  dict[str, TestCase],# {case_id: TestCase}
        results_dir: Optional[str] = None,
    ) -> list[EnvCheckReport]:
        """
        批量分析所有最终失败的用例，识别哪些是环境问题导致的失败。

        histories:   来自 LokiOrchestrator.run() 的结果
        cases_map:   {case_id: TestCase}
        results_dir: BenchmarkRunner 的 results_dir（sandbox 存档位置）
        """
        reports = []
        for history in histories:
            if history.final_success:
                continue

            case = cases_map.get(history.case_id)
            if not case:
                continue

            # 找最后一轮的 sandbox_dir（从 results_dir 推导）
            sandbox_dir = self._find_sandbox_dir(history.case_id, results_dir)
            if sandbox_dir:
                report = self.check(case, sandbox_dir)
                if report.has_issues:
                    logger.info(f"[{history.case_id}] 发现环境问题:\n{report.summary()}")
                reports.append(report)

        return reports

    def _find_sandbox_dir(self, case_id: str, results_dir: Optional[str]) -> Optional[Path]:
        """从 results_dir 中找到对应 case_id 的最新 sandbox 目录。"""
        if not results_dir:
            return None
        rd = Path(results_dir)
        if not rd.exists():
            return None
        # sandbox 目录命名格式：{case_id}_{uuid8}/
        matches = sorted(
            [d for d in rd.iterdir() if d.is_dir() and d.name.startswith(case_id)],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        return matches[0] if matches else None

    def save_report(self, reports: list[EnvCheckReport], output_file: str):
        """将检查报告写入 JSON 文件。"""
        data = [r.to_dict() for r in reports]
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"环境检查报告已写入 {output_file}")
