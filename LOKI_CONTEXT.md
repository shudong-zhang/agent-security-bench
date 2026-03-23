
---

## 14. 现有代码基础（agent-security-bench）

仓库地址：https://github.com/shudong-zhang/agent-security-bench

### 已有、可直接用

| 文件/模块 | 内容 |
|---|---|
| `core/interfaces.py` | 核心抽象层：Environment / AgentAdapter / Evaluator / Reporter 四个插槽，数据模型 TestCase / AgentTrace / EvalResult |
| `core/runner.py` | BenchmarkRunner 主调度器，支持并行执行 |
| `core/dataset.py` | JSON 测试用例加载器 |
| `sandbox/manager.py` | Docker 沙箱生命周期管理 |
| `agents/claude_adapter.py` | Claude Code CLI 适配器 |
| `evaluators/evaluators.py` | ToolCallEvaluator / NetworkRequestEvaluator / LLMJudgeEvaluator |
| `enviroments/local_file.py` | 本地文件注入面 |
| `enviroments/skill_file.py` | Skill 文件注入面 |
| `docker/` | Dockerfile + 构建脚本 |
| `datasets/cases/` | 示例测试用例 JSON |

### 待新增（Loki 扩展部分）

| 模块 | 路径（建议） | 负责人 | 说明 |
|---|---|---|---|
| 测试用例生成 | `loki/generate_test_cases.py` | 栋博 | 读 attack_paths.json → 调 Claude API → 输出 test_cases.json |
| 迭代编排引擎 | `loki/orchestrator.py` | 栋博 | 迭代循环 + 失败路由 |
| Payload 优化 Agent | `loki/payload_optimizer.py` | 欣瑶 | 分析失败原因，重写 payload |
| 环境修复 Agent | `loki/env_repair.py` | 缘哥 | diff 期望 vs 实际环境状态 |
| 安南结果适配器 | `loki/mobile_adapter.py` | 欣瑶 | 安南结果 → 统一 JSON |
| Dashboard | `loki/dashboard/` | Claude Code 生成 | 网页前端，领导看 |

### 扩展原则

- 加新功能 = 新建文件，继承 `core/interfaces.py` 里的抽象类
- `core/interfaces.py` 轻易不动
- Loki 新增模块统一放 `loki/` 目录，与原有代码隔离

### 测试用例格式

现有格式在 `datasets/cases/` 下，新增的 Loki 测试用例（从威胁分析 skill 生成的）放 `datasets/loki_cases/`，格式参考本文档第5节。
