"""
agents/claude_adapter.py
=========================
Claude Code CLI 适配器。

容器里运行的命令形如：
    claude --print --dangerously-skip-permissions \
           --output-format text --max-turns 20 \
           "<task_prompt>"

AgentTrace.raw_log 就是 Claude CLI 的 stdout，包含思考过程和工具调用记录。
parse_trace() 从这段文本里提取结构化的工具调用列表。
"""

import re
from pathlib import Path
from datetime import datetime

from typing import Optional

from core.interfaces import AgentAdapter, AgentTrace, TestCase


class ClaudeAgentAdapter(AgentAdapter):
    """
    environment_config里可以指定：
        model:     str   使用的 Claude 模型，默认 claude-sonnet-4-5
        max_turns: int   最大对话轮数，默认 20
    """

    def __init__(
        self,
        model:     str = "claude-sonnet-4-5",
        max_turns: int = 20,
    ):
        self.model     = model
        self.max_turns = max_turns

    # ── 生成容器内的 agent 命令（不含 prompt，prompt 由 SandboxManager 追加）──
    def build_agent_cmd(self, case: Optional[TestCase] = None) -> list[str]:
        """
        返回 docker run <image> 之后、prompt 之前的部分。
        SandboxManager.run() 会在末尾追加 task_prompt。
        """
        model = self.model
        if case and case.environment_config.get("model"):
            model = case.environment_config["model"]

        max_turns = self.max_turns
        if case and case.environment_config.get("max_turns"):
            max_turns = case.environment_config["max_turns"]

        return [
            "claude",
            "--print",
            "--verbose",                    # 打印每个工具调用到 stdout
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--max-turns", str(max_turns),
            "--model", model,
        ]

    # ── 解析 Claude CLI 输出，提取工具调用 ─────────────────────
    def parse_trace(self, raw_trace: AgentTrace) -> AgentTrace:
        """
        从 SandboxManager 的原始 AgentTrace 解析出结构化轨迹。
        同时利用 .command_history 补充 bash 命令记录。
        """
        raw_log    = raw_trace.raw_log
        tool_calls = self._extract_tool_calls(raw_log)

        # 从 .command_history 补充工具调用（兜底：捕获 Claude 没打印的命令）
        cmd_history = raw_trace.side_effects.get("command_history", "")
        if cmd_history:
            tool_calls += self._extract_from_command_history(cmd_history)

        messages = self._extract_messages(raw_log)

        raw_trace.tool_calls = tool_calls
        raw_trace.messages   = messages
        return raw_trace

    # ── 内部解析工具 ─────────────────────────────────────────
    def _extract_tool_calls(self, text: str) -> list[dict]:
        """
        提取所有工具调用。支持两种格式：
        1. <tool_use>JSON</tool_use> 标签格式
        2. ToolUse(name=...) 文本格式（verbose 模式下）
        """
        import json as _json
        calls = []

        # 格式1：<tool_use>...</tool_use>
        for m in re.finditer(r"<tool_use>\s*(\{.*?\})\s*</tool_use>", text, re.DOTALL):
            try:
                data = _json.loads(m.group(1))
                calls.append({
                    "name":      data.get("name", "unknown"),
                    "arguments": data.get("input", data.get("arguments", {})),
                })
            except Exception:
                pass

        # 格式2：⚙ Tool: BashTool  Input: {"command": "..."}  (verbose text 模式)
        for m in re.finditer(
            r"(?:Tool:|ToolUse\(name=)['\"]?(\w+)['\"]?.*?(?:Input:|input=)(\{.*?\})",
            text, re.DOTALL
        ):
            try:
                data = _json.loads(m.group(2))
                calls.append({"name": m.group(1), "arguments": data})
            except Exception:
                pass

        # 格式3：直接搜索常见危险命令模式（兜底，用于 command_history 分析）
        # 这里只记录，实际危险命令检测在 ToolCallEvaluator 里做
        return calls

    def _extract_from_command_history(self, history: str) -> list[dict]:
        """从 .command_history 提取 bash 命令，补充为工具调用记录。"""
        calls = []
        for line in history.splitlines():
            line = line.strip()
            if line and not line.startswith("trap"):
                calls.append({"name": "bash", "arguments": {"command": line}, "source": "command_history"})
        return calls

    def _extract_messages(self, text: str) -> list[dict]:
        """把整个输出拆成简单的消息列表，方便 LLM judge 阅读。"""
        # 按工具调用边界切分，简单处理
        parts = re.split(r"(<tool_use>|</tool_result>)", text)
        messages = []
        for part in parts:
            part = part.strip()
            if part and part not in ("<tool_use>", "</tool_result>"):
                messages.append({"role": "assistant", "content": part[:500]})
        return messages
