"""
environments/local_file.py
===========================
本地文件注入环境。

最简单的环境：只需在 sandbox_dir 里创建文件，无需启动任何服务。

environment_config 支持的字段：

  必填：
    filename:    str   注入文件名，如 "notes.txt"
    template:    str   文件内容模板，{payload} 会被替换为攻击载荷

  可选：
    extra_files: dict  需要预置的其他文件，格式 {"文件名": "内容"}
                       用于需要操作目标文件的攻击（删除、泄露、修改）
                       不涉及文件操作的攻击不需要填

示例 environment_config：
  {
    "filename": "notes.txt",
    "template": "正常内容...\n{payload}\n更多内容",
    "extra_files": {
      "secret.txt": "CONFIDENTIAL: API_KEY=sk-xxx"
    }
  }
"""

import os
from pathlib import Path
from core.interfaces import Environment, EnvironmentType, EnvironmentContext, TestCase
from typing import Optional

class LocalFileEnvironment(Environment):
    environment_type = EnvironmentType.LOCAL_FILE

    def __init__(self):
        self._sandbox_dir: Optional[Path] = None

    def build(self, case: TestCase, sandbox_dir: Path) -> EnvironmentContext:
        self._sandbox_dir = sandbox_dir
        cfg      = case.environment_config
        filename = cfg.get("filename", "document.txt")
        template = cfg.get("template", "{payload}")

        sandbox_dir.mkdir(parents=True, exist_ok=True)

        # 1. 创建注入文件
        content = template.replace("{payload}", case.payload.content)
        (sandbox_dir / filename).write_text(content, encoding="utf-8")

        # 2. 创建预置文件（可选）
        extra_files = cfg.get("extra_files", {})
        for fname, fcontent in extra_files.items():
            (sandbox_dir / fname).write_text(fcontent, encoding="utf-8")

        return EnvironmentContext(
            notes={
                "injected_file": f"/workspace/{filename}",
                "extra_files":   list(extra_files.keys()),
            }
        )

    def teardown(self):
        pass  # 无服务需要关闭，SandboxManager 统一清理文件
