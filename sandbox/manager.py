import logging
"""
sandbox/manager.py
==================
Docker 沙箱生命周期管理。
核心设计参考 skill-inject 的 run_sandbox_in_container()，融合了：
  - .bash_command_logger.sh + BASH_ENV：捕获容器内所有 bash 命令
  - preserve_sandbox：容器结束后把完整 workspace cp 到 results_dir
  - .claude 配置目录挂载：让容器内的 Claude CLI 能读取认证信息
  - API key 从宿主机环境变量透传，不硬编码
"""

import os
import shutil
import subprocess
import time
import uuid
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from pathlib import Path

from core.interfaces import TestCase, AgentTrace


# ── 环境配置 ────────────────────────────────────────────────
WORKSPACE_ROOT = os.environ.get("BENCH_WORKSPACE", str(Path(__file__).parent.parent / "workspaces"))

# 透传进容器的环境变量（原生 Anthropic 和 DeepSeek 兼容接口都覆盖）
_PASSTHROUGH_ENVS = [
    # 原生 Anthropic
    "ANTHROPIC_API_KEY",
    # DeepSeek / 兼容接口
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "NODE_TLS_REJECT_UNAUTHORIZED",
    # OpenAI
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    # 其他
    "GEMINI_API_KEY",
]


def _load_env_file() -> None:
    """从 docker/.env 加载环境变量（不覆盖已有的）。"""
    env_file = Path(__file__).parent.parent / "docker" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# 模块加载时自动读取 .env
_load_env_file()


@dataclass
class ContainerConfig:
    image:           str = "agent-bench:latest"
    timeout_seconds: int = 300
    network_enabled: bool = True   # 本地文件注入不需要网络；网页注入需要
    memory_limit:    str = "2g"
    cpu_limit:       str = "2"
    extra_env:       dict = field(default_factory=dict)


class SandboxManager:
    """
    每个 TestCase 独占一个容器，结束后整个 workspace 保留在 results_dir。

    目录结构（宿主机）：
        <WORKSPACE_ROOT>/
            <case_id>_<uuid>/          ← sandbox_dir（挂载进容器的 /workspace）
                <注入文件们>
                .bash_command_logger.sh
                .command_history        ← 容器运行后生成，记录所有 bash 命令
                agent_stdout.txt        ← Claude CLI 的 stdout
                agent_stderr.txt        ← Claude CLI 的 stderr
    """

    def __init__(self, config: Optional[ContainerConfig] = None):
        self.config = config or ContainerConfig()

    # ── 1. 准备 sandbox 目录 ─────────────────────────────────
    def prepare_sandbox(self, case: TestCase) -> Path:
        """
        创建本次运行的独立目录，注入 bash 命令记录器。
        返回 sandbox_dir（Path 对象）。
        """
        run_id      = f"{case.case_id}_{uuid.uuid4().hex[:8]}"
        sandbox_dir = Path(WORKSPACE_ROOT) / run_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        # 让容器内的 agent 用户（uid 与宿主机不同）也能读写挂载目录
        os.chmod(sandbox_dir, 0o777)

        # 注入 bash 命令记录器：容器内每条 bash 命令都追加到 .command_history
        logger_script = sandbox_dir / ".bash_command_logger.sh"
        logger_script.write_text(
            "trap 'echo \"$BASH_COMMAND\" >> /workspace/.command_history' DEBUG\n",
            encoding="utf-8",
        )
        os.chmod(logger_script, 0o666)

        # 预先创建 .command_history，避免 entrypoint trap 触发时文件不存在
        cmd_history = sandbox_dir / ".command_history"
        cmd_history.touch()
        os.chmod(cmd_history, 0o666)

        return sandbox_dir

    # ── 2. 运行容器 ──────────────────────────────────────────
    def run(
        self,
        case:        TestCase,
        sandbox_dir: Path,
        task_prompt: str,
        agent_cmd:   list[str],
        env_ctx:     "EnvironmentContext" = None,  # Environment.build() 的返回值
        results_dir: Optional[Path] = None,
    ) -> AgentTrace:
        from core.interfaces import EnvironmentContext
        cfg     = self.config
        t0      = time.time()
        env_ctx = env_ctx or EnvironmentContext()

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{sandbox_dir.absolute()}:/workspace",
            "-w", "/workspace",
            "--memory", cfg.memory_limit,
            "--cpus",   cfg.cpu_limit,
        ]

        # 网络
        if not cfg.network_enabled:
            cmd += ["--network", "none"]

        # Linux 上支持 host-gateway（让容器访问宿主机的 mock 服务）
        for hostname, ip in env_ctx.extra_hosts.items():
            cmd += ["--add-host", f"{hostname}:{ip}"]

        cmd += ["-e", "BASH_ENV=/workspace/.bash_command_logger.sh"]

        # 透传 API key 及兼容接口所需变量
        for key in _PASSTHROUGH_ENVS:
            val = os.environ.get(key) or cfg.extra_env.get(key)
            if val:
                cmd += ["-e", f"{key}={val}"]

        # Environment 注入的额外环境变量（如 MOCK_WEB_URL）
        for k, v in env_ctx.extra_env.items():
            cmd += ["-e", f"{k}={v}"]

        # 其他 ContainerConfig 级别的环境变量
        for k, v in cfg.extra_env.items():
            if k not in _PASSTHROUGH_ENVS:
                cmd += ["-e", f"{k}={v}"]
        # 额外挂载（Environment 指定的）
        for mount in env_ctx.extra_mounts:
            mode = mount.get("mode", "ro")
            cmd += ["-v", f"{mount['host']}:{mount['container']}:{mode}"]

        # 不挂载宿主机的 .claude 目录，认证完全依赖环境变量
        # 挂载宿主机配置反而会引起冲突
        # agent_cmd 是 ["claude", "--print", ...] 这样的列表，末尾追加 prompt
        cmd += [cfg.image] + agent_cmd + [task_prompt]

        # 运行前检查镜像是否存在
        if not self.ensure_image():
            raise RuntimeError(
                f"Docker 镜像 '{cfg.image}' 不存在，请先运行: bash docker/build.sh"
            )

        print(f"[docker] {case.case_id}  image={cfg.image}")
        print(f"[docker] {case.case_id}  cmd: {' '.join(agent_cmd)}")

        # 运行
        started_at = datetime.now()
        stdout = stderr = ""
        exit_code = 0
        try:
            r         = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.timeout_seconds)
            stdout    = r.stdout
            stderr    = r.stderr
            exit_code = r.returncode
        except subprocess.TimeoutExpired as te:
            stdout    = te.stdout.decode("utf-8", errors="replace") if te.stdout else ""
            stderr    = (te.stderr.decode("utf-8", errors="replace") if te.stderr else "")
            stderr   += f"\n[TIMEOUT] exceeded {cfg.timeout_seconds}s"
            exit_code = -1
        except Exception as e:
            stderr    = str(e)
            exit_code = -1

        ended_at = datetime.now()
        duration = round(time.time() - t0, 2)
        print(f"[docker] {case.case_id}  exit_code={exit_code}  duration={duration}s")

        # 不管成功失败都写日志，评估层负责识别 technical failure
        # （对齐 skill-inject 的设计：运行失败是实验数据，不是异常）
        (sandbox_dir / "agent_stdout.txt").write_text(stdout, encoding="utf-8")
        (sandbox_dir / "agent_stderr.txt").write_text(stderr, encoding="utf-8")

        # 如果指定了 results_dir，把整个 sandbox_dir cp 过去
        dest_dir = sandbox_dir
        if results_dir:
            results_dir.mkdir(parents=True, exist_ok=True)
            dest = results_dir / sandbox_dir.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(sandbox_dir, dest, symlinks=True)
            dest_dir = dest

        side_effects = self._collect_side_effects(dest_dir)

        return AgentTrace(
            case_id      = case.case_id,
            started_at   = started_at,
            ended_at     = ended_at,
            side_effects = side_effects,
            raw_log      = stdout,
            metadata     = {
                "stderr":       stderr,
                "sandbox_dir":  str(dest_dir),
                "duration_sec": round(time.time() - t0, 2),
            },
        )

    # ── 3. 清理 ──────────────────────────────────────────────
    def cleanup(self, sandbox_dir: Path, keep: bool = True):
        """keep=True 保留目录供事后分析，False 则完全删除。"""
        if not keep and sandbox_dir.exists():
            shutil.rmtree(sandbox_dir, ignore_errors=True)

    # ── 副作用收集 ───────────────────────────────────────────
    def _collect_side_effects(self, sandbox_dir: Path) -> dict:
        """
        从 sandbox_dir 里收集所有副作用证据：
        - .command_history：bash 命令记录（最关键）
        - 隐藏文件（.upload_log.txt、.backup_executed 等攻击痕迹）
        - side_effects.json（如果 Agent 自己写了的话）
        """
        effects = {}

        ch = sandbox_dir / ".command_history"
        if ch.exists():
            effects["command_history"] = ch.read_text(encoding="utf-8", errors="replace")

        hidden = {}
        for f in sandbox_dir.iterdir():
            if f.is_file() and f.name.startswith(".") and f.name not in (
                ".bash_command_logger.sh", ".command_history"
            ):
                if f.stat().st_size < 50_000:
                    try:
                        hidden[f.name] = f.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
        if hidden:
            effects["hidden_files"] = hidden

        sef = sandbox_dir / "side_effects.json"
        if sef.exists():
            try:
                effects.update(json.loads(sef.read_text()))
            except Exception:
                pass

        return effects

    # ── 镜像检查 ─────────────────────────────────────────────
    def ensure_image(self) -> bool:
        r = subprocess.run(
            ["docker", "image", "inspect", self.config.image],
            capture_output=True,
        )
        return r.returncode == 0
