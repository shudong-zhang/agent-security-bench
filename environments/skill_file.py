# """
# environments/skill_file.py
# ===========================
# Skill 文件注入环境。

# 攻击原理：
#   Claude Code 启动时自动加载 .claude/skills/ 下的 SKILL.md，
#   攻击者将恶意指令注入到合法 skill 文件里，Agent 执行正常任务时触发注入。

# environment_config 字段：

#   必填：
#     skill_name:    str   data/skills/ 下的 skill 目录名，如 "pptx"、"docx"
#                          整个目录会被 copy 进 sandbox 的 .claude/skills/

#   可选：
#     inject_method: str   "line"（默认）或 "description"
#     inject_line:   int   line 注入时的插入行号，None 则追加到末尾
#     inject_prefix: str   注入内容的前缀，如 "6. "

#     extra_files:   dict  workspace 根目录下的文件（任务文件、受害文件）
#                          格式：{"文件名": "data/ 下的相对路径"}
#                          值为空字符串时创建空文件

# sandbox 结构：
#   /workspace/
#   ├── .claude/skills/<skill_name>/   ← 从 data/skills/ copy 过来，SKILL.md 里已注入
#   │   ├── SKILL.md
#   │   └── scripts/...
#   └── <extra_files>                  ← 任务文件、受害文件
# """

# import re
# import shutil
# from pathlib import Path
# from core.interfaces import Environment, EnvironmentType, EnvironmentContext, TestCase, DATA_SKILL_FILE


# class SkillFileEnvironment(Environment):
#     environment_type = EnvironmentType.SKILL_FILE

#     def build(self, case: TestCase, sandbox_dir: Path) -> EnvironmentContext:
#         cfg           = case.environment_config
#         skill_name    = cfg["skill_name"]
#         inject_method = cfg.get("inject_method", "line")
#         inject_line   = cfg.get("inject_line")
#         inject_prefix = cfg.get("inject_prefix", "")
#         extra_files   = cfg.get("extra_files", {})

#         sandbox_dir.mkdir(parents=True, exist_ok=True)

#         # ── 1. 把 data/skills/<skill_name>/ 整个 copy 进 sandbox ─────
#         skill_src = DATA_SKILL_FILE / "skills" / skill_name
#         if not skill_src.exists():
#             raise FileNotFoundError(
#                 f"Skill not found: {skill_src}\n"
#                 f"请先把 skill 文件夹放到 data/skills/{skill_name}/"
#             )
#         skill_dst = sandbox_dir / ".claude" / "skills" / skill_name
#         skill_dst.parent.mkdir(parents=True, exist_ok=True)
#         shutil.copytree(skill_src, skill_dst, dirs_exist_ok=True)

#         # ── 2. 在 copy 好的 SKILL.md 上注入恶意指令 ───────────────────
#         skill_file   = skill_dst / "SKILL.md"
#         payload_text = inject_prefix + case.payload.content

#         if inject_method == "description":
#             self._inject_into_description(skill_file, payload_text)
#         else:
#             self._inject_at_line(skill_file, payload_text, inject_line)

#         # ── 3. 预置 workspace 根目录文件 ──────────────────────────────
#         created = []
#         for dest_name, src_rel in extra_files.items():
#             dest = sandbox_dir / dest_name
#             dest.parent.mkdir(parents=True, exist_ok=True)
#             if src_rel:
#                 src = DATA_SKILL_FILE / "task_files" / src_rel.removeprefix("task_files/")
#                 shutil.copy2(src, dest)
#             else:
#                 dest.touch()
#             created.append(dest_name)

#         return EnvironmentContext(
#             notes={
#                 "skill_path":    f"/workspace/.claude/skills/{skill_name}/SKILL.md",
#                 "inject_method": inject_method,
#                 "inject_line":   inject_line,
#                 "extra_files":   created,
#             }
#         )

#     def teardown(self):
#         pass

#     # ── 注入方法 ────────────────────────────────────────────────────────

#     def _inject_at_line(self, file_path: Path, text: str, line_number: int | None) -> None:
#         lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
#         block = [(raw.strip() + "\n") if raw.strip() else "\n"
#                  for raw in text.strip("\n").splitlines()]
#         if line_number is None:
#             new_lines = lines + ["\n"] + block
#         else:
#             pos = max(0, min(line_number - 1, len(lines)))
#             new_lines = lines[:pos] + block + lines[pos:]
#         file_path.write_text("".join(new_lines), encoding="utf-8")

#     def _inject_into_description(self, file_path: Path, text: str) -> None:
#         content    = file_path.read_text(encoding="utf-8")
#         yaml_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL | re.MULTILINE)

#         if not yaml_match:
#             file_path.write_text(
#                 f"---\ndescription: \"{text.strip()}\"\n---\n\n{content}",
#                 encoding="utf-8",
#             )
#             return

#         yaml_block = yaml_match.group(1)
#         yaml_end   = yaml_match.end()
#         clean      = text.strip()

#         block_re = re.compile(
#             r"^description:\s*([>|])\s*\n((?:[ \t]+.+\n?)+)", re.MULTILINE
#         )
#         bm = block_re.search(yaml_block)
#         if bm:
#             lines     = bm.group(2).rstrip("\n").split("\n")
#             lines[-1] = lines[-1].rstrip() + " " + clean
#             new_block = "\n".join(lines) + "\n"
#             new_yaml  = (
#                 yaml_block[: bm.start()]
#                 + f"description: {bm.group(1)}\n{new_block}"
#                 + yaml_block[bm.end():]
#             )
#         else:
#             desc_re = re.compile(r'^description:\s*["\']?(.*?)["\']?\s*$', re.MULTILINE)
#             dm      = desc_re.search(yaml_block)
#             if not dm:
#                 new_yaml = yaml_block + f'\ndescription: "{clean}"'
#             else:
#                 orig     = dm.group(1).strip().strip("\"'")
#                 escaped  = clean.replace('"', '\\"')
#                 new_yaml = desc_re.sub(f'description: "{orig} {escaped}"', yaml_block)

#         file_path.write_text(
#             f"---\n{new_yaml}\n---\n{content[yaml_end:]}",
#             encoding="utf-8",
#         )

"""
environments/skill_file.py
===========================
Skill 文件注入环境。

environment_config 字段：

  必填：
    skill_name:    str   data/skill_file/skills/ 下的 skill 目录名

  可选：
    inject_method: str   "line"（默认）或 "description"
    inject_line:   int   line 注入时的插入行号，None 则追加到末尾
    inject_prefix: str   注入内容的前缀，如 "5. "

    task_scripts:  dict  注入进 skill/scripts/ 的脚本文件
                         格式：{"脚本名": "task_scripts/xxx.py"}
                         从 data/skill_file/task_scripts/ 下 copy

    extra_files:   dict  workspace 根目录下的任务文件/受害文件
                         格式：{"文件名": "task_files/xxx"} 或 {"文件名": ""}
                         值为空字符串时创建空文件

sandbox 结构（与 skill-inject 一致）：
  <sandbox_dir>/
  ├── .claude/skills/<skill_name>/
  │   ├── SKILL.md          ← 从磁盘 copy，已注入恶意指令
  │   └── scripts/          ← task_scripts copy 进来
  └── <extra_files>         ← 任务文件、受害文件
"""

import re
import shutil
from pathlib import Path
from core.interfaces import Environment, EnvironmentType, EnvironmentContext, TestCase, DATA_SKILL_FILE


class SkillFileEnvironment(Environment):
    environment_type = EnvironmentType.SKILL_FILE

    def build(self, case: TestCase, sandbox_dir: Path) -> EnvironmentContext:
        cfg           = case.environment_config
        skill_name    = cfg["skill_name"]
        inject_method = cfg.get("inject_method", "line")
        inject_line   = cfg.get("inject_line")
        inject_prefix = cfg.get("inject_prefix", "")
        task_scripts  = cfg.get("task_scripts", {})
        extra_files   = cfg.get("extra_files", {})

        sandbox_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. 从磁盘 copy 干净的 skill ──────────────────────────────
        skill_src = DATA_SKILL_FILE / "skills" / skill_name
        if not skill_src.exists():
            raise FileNotFoundError(
                f"Skill not found: {skill_src}\n"
                f"请先把 skill 文件夹放到 data/skill_file/skills/{skill_name}/"
            )
        skill_dst = sandbox_dir / ".claude" / "skills" / skill_name
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_src, skill_dst, dirs_exist_ok=True)

        # ── 2. copy task_scripts 进 skill/scripts/ ───────────────────
        for script_name, script_rel in task_scripts.items():
            src = DATA_SKILL_FILE / "task_scripts" / script_rel.removeprefix("task_scripts/")
            if not src.exists():
                raise FileNotFoundError(f"task_script not found: {src}")
            scripts_dir = skill_dst / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            dst = scripts_dir / script_name
            shutil.copy2(src, dst)
            dst.chmod(0o755)

        # ── 3. 注入恶意指令到 SKILL.md ───────────────────────────────
        skill_file   = skill_dst / "SKILL.md"
        payload_text = inject_prefix + case.payload.content

        if inject_method == "description":
            self._inject_into_description(skill_file, payload_text)
        else:
            self._inject_at_line(skill_file, payload_text, inject_line)

        # ── 4. 预置 workspace 根目录文件 ─────────────────────────────
        created = []
        for dest_name, src_rel in extra_files.items():
            dest = sandbox_dir / dest_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not src_rel:
                dest.touch()
            else:
                src = DATA_SKILL_FILE / "task_files" / src_rel.removeprefix("task_files/")
                if not src.exists():
                    raise FileNotFoundError(f"task_file not found: {src}")
                shutil.copy2(src, dest)
            created.append(dest_name)

        return EnvironmentContext(
            notes={
                "skill_path":    f"/workspace/.claude/skills/{skill_name}/SKILL.md",
                "inject_method": inject_method,
                "inject_line":   inject_line,
                "extra_files":   created,
            }
        )

    def teardown(self):
        pass

    # ── 注入方法 ────────────────────────────────────────────────────────

    def _inject_at_line(self, file_path: Path, text: str, line_number: int | None) -> None:
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
        block = [(raw.strip() + "\n") if raw.strip() else "\n"
                 for raw in text.strip("\n").splitlines()]
        if line_number is None:
            new_lines = lines + ["\n"] + block
        else:
            pos = max(0, min(line_number - 1, len(lines)))
            new_lines = lines[:pos] + block + lines[pos:]
        file_path.write_text("".join(new_lines), encoding="utf-8")

    def _inject_into_description(self, file_path: Path, text: str) -> None:
        content    = file_path.read_text(encoding="utf-8")
        yaml_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL | re.MULTILINE)

        if not yaml_match:
            file_path.write_text(
                f"---\ndescription: \"{text.strip()}\"\n---\n\n{content}",
                encoding="utf-8",
            )
            return

        yaml_block = yaml_match.group(1)
        yaml_end   = yaml_match.end()
        clean      = text.strip()

        block_re = re.compile(
            r"^description:\s*([>|])\s*\n((?:[ \t]+.+\n?)+)", re.MULTILINE
        )
        bm = block_re.search(yaml_block)
        if bm:
            lines     = bm.group(2).rstrip("\n").split("\n")
            lines[-1] = lines[-1].rstrip() + " " + clean
            new_block = "\n".join(lines) + "\n"
            new_yaml  = (
                yaml_block[: bm.start()]
                + f"description: {bm.group(1)}\n{new_block}"
                + yaml_block[bm.end():]
            )
        else:
            desc_re = re.compile(r'^description:\s*["\']?(.*?)["\']?\s*$', re.MULTILINE)
            dm      = desc_re.search(yaml_block)
            if not dm:
                new_yaml = yaml_block + f'\ndescription: "{clean}"'
            else:
                orig     = dm.group(1).strip().strip("\"'")
                escaped  = clean.replace('"', '\\"')
                new_yaml = desc_re.sub(f'description: "{orig} {escaped}"', yaml_block)

        file_path.write_text(
            f"---\n{new_yaml}\n---\n{content[yaml_end:]}",
            encoding="utf-8",
        )
