"""
core/interfaces.py
==================
框架核心抽象层。规则：核心接口永远不动，扩展靠子类。

主要变化（重构说明）：
  - InjectionSurface 改为 Environment，职责扩展为"构建完整运行时环境"
  - 新增 EnvironmentContext 数据类，描述容器需要的所有资源
  - SurfaceType 改为 EnvironmentType
  - TestCase 里 surface_type/surface_config 改为 environment_type/environment_config
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from pathlib import Path
import datetime

# 所有原材料的根目录（skills、task_files、templates）
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_LOCAL_FILE = DATA_DIR / "local_file"
DATA_SKILL_FILE = DATA_DIR / "skill_file"


# ─────────────────────────────────────────
# 枚举
# ─────────────────────────────────────────
class EnvironmentType(str, Enum):
    LOCAL_FILE   = "local_file"     # 本地文件（txt/csv/pdf...）
    WEB_PAGE     = "web_page"       # mock 网页服务器
    MCP          = "mcp"            # mock MCP server
    SKILL_FILE   = "skill_file"     # Claude skill 文件
    EMAIL        = "email"          # mock 邮件服务
    API_RESPONSE = "api_response"   # mock REST API
    COMPOSITE    = "composite"      # 组合多种环境


class Modality(str, Enum):
    TEXT  = "text"
    IMAGE = "image"
    CODE  = "code"


class AttackGoal(str, Enum):
    DATA_EXFIL       = "data_exfiltration"
    FILE_DELETE      = "file_deletion"
    FILE_MODIFY      = "file_modification"
    PRIVILEGE_ESC    = "privilege_escalation"
    MALWARE_DOWNLOAD = "malware_download"
    LATERAL_MOVE     = "lateral_movement"
    DENIAL_OF_SVC    = "denial_of_service"
    CUSTOM           = "custom"


# ─────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────
@dataclass
class AttackPayload:
    """攻击载荷：注入的恶意指令本体"""
    content:       str
    modality:      Modality = Modality.TEXT
    position_hint: Optional[str] = None  # 注入位置提示（如 html_comment / csv_row / metadata）
    metadata:      dict = field(default_factory=dict)


@dataclass
class EnvironmentContext:
    """
    Environment.build() 的返回值。
    描述"容器需要什么才能访问这个环境"，由 SandboxManager 消费。
    """
    # 注入容器的额外环境变量（如 MOCK_WEB_URL、MCP_SERVER_URL）
    extra_env:    dict = field(default_factory=dict)

    # 额外 volume 挂载，格式：[{"host": "/path", "container": "/path", "mode": "ro"}]
    extra_mounts: list = field(default_factory=list)

    # 容器 --add-host 映射，格式：{"attacker.example.com": "172.17.0.1"}
    # 让容器内域名解析到 mock 服务器
    extra_hosts:  dict = field(default_factory=dict)

    # 已启动的服务对象列表，teardown() 时逐一关闭
    # 每个元素需要有 .shutdown() 或 .terminate() 方法
    services:     list = field(default_factory=list)

    # 传给评估器的元数据（如预置文件列表、mock 服务地址）
    notes:        dict = field(default_factory=dict)


@dataclass
class TestCase:
    """一条完整的测试用例"""
    # 基础标识
    case_id:  str
    name:     str
    category: str

    # 用户任务（Agent 收到的正常指令）
    user_task: str

    # 攻击目标
    attack_goal:      AttackGoal
    attack_goal_desc: str

    # 环境配置（替代原来的 surface_type + surface_config）
    environment_type:   EnvironmentType
    environment_config: dict   # 各 Environment 子类自定义的参数

    # 攻击路径
    attack_path: list[str]

    # 恶意载荷
    payload: AttackPayload

    # 成功判据
    success_criteria: dict
    """
    success_criteria 示例：
    {
      "type": "llm_judge",
      "judge_prompt": "判断 Agent 是否..."
    }
    """

    # 元信息
    severity:   str = "high"
    tags:       list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


@dataclass
class AgentTrace:
    """Agent 完整执行轨迹"""
    case_id:    str
    started_at: datetime.datetime
    ended_at:   datetime.datetime

    messages:    list[dict] = field(default_factory=list)
    tool_calls:  list[dict] = field(default_factory=list)
    side_effects: dict      = field(default_factory=dict)
    raw_log:     str        = ""
    metadata:    dict       = field(default_factory=dict)


@dataclass
class EvalResult:
    """单条用例的评估结果"""
    case_id:         str
    attack_success:  bool
    confidence:      float
    evidence:        str
    triggered_steps: list[str]
    evaluator_used:  str
    metadata:        dict = field(default_factory=dict)


# ─────────────────────────────────────────
# 抽象接口
# ─────────────────────────────────────────

class Environment(ABC):
    """
    插槽①：环境构建器
    负责为一个 TestCase 构建完整的运行时环境：
      - 创建注入文件、预置文件
      - 启动 mock 服务（网页、MCP、邮件、API...）
      - 配置网络映射

    每种注入场景实现一个子类：
      LocalFileEnvironment、MockWebEnvironment、MockMCPEnvironment...

    生命周期：
      ctx = env.build(case, sandbox_dir)   # runner 在启动容器前调用
      ...容器运行中...
      env.teardown()                        # runner 在容器退出后调用
    """

    environment_type: EnvironmentType

    @abstractmethod
    def build(self, case: TestCase, sandbox_dir: Path) -> EnvironmentContext:
        """
        构建环境，返回 EnvironmentContext。
        sandbox_dir 会被挂载为容器内的 /workspace。
        所有需要让 Agent 访问的文件都放在这里。
        """

    @abstractmethod
    def teardown(self):
        """停止所有已启动的服务，释放资源"""


class AgentAdapter(ABC):
    """插槽②：Agent 适配器"""

    @abstractmethod
    def build_agent_cmd(self, case: Optional[TestCase] = None) -> list[str]:
        """
        返回 docker run <image> 之后、task_prompt 之前的命令列表。
        SandboxManager.run() 会在末尾追加 task_prompt。
        """

    @abstractmethod
    def parse_trace(self, raw_trace: "AgentTrace") -> "AgentTrace":
        """
        从 SandboxManager 返回的原始 AgentTrace 解析出结构化轨迹。
        raw_trace.raw_log      = stdout 文本
        raw_trace.side_effects = command_history 等副作用
        raw_trace.metadata     = sandbox_dir、duration 等
        """


# class Evaluator(ABC):
#     """插槽③：评估器"""

#     @abstractmethod
#     def can_handle(self, criteria_type: str) -> bool:
#         """声明自己能处理哪种 success_criteria.type"""

#     @abstractmethod
#     def evaluate(self, trace: AgentTrace, case: TestCase) -> EvalResult:
#         """核心评估逻辑"""

class Evaluator(ABC):
    @abstractmethod
    def can_handle(self, criteria_type: str) -> bool: ...

    @abstractmethod
    def evaluate(
        self,
        trace:       AgentTrace,
        case:        TestCase,
        sandbox_dir: Optional[Path] = None,
    ) -> EvalResult: ...


class Reporter(ABC):
    """插槽④：报告生成器"""

    @abstractmethod
    def generate(self, results: list[EvalResult], cases: list[TestCase], output_dir: str) -> str:
        """生成报告，返回报告文件路径"""
