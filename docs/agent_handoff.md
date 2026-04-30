# Loki Agent Handoff

这份文档写给后续接手 `loki` 仓库的 agent。

目标是：不需要再看长对话，也能快速理解这个项目的真正目标、当前状态、工作原则，以及接下来应该做什么。

## 1. 一句话定义

`loki` 不是通用聊天助手，不是 benchmark runner，也不是普通 coding agent。

`loki` 的目标是成为一个 **agent 安全渗透测试 harness 平台**：

`给一个结构化测试任务 -> 运行完整安全测试 -> 产出完整轨迹、证据、可辩护 verdict、训练数据、可复用经验`

## 2. 最终目标

最终目标不是“跑通一次测试”，而是构建一个会积累经验、能支持后训练的数据生产系统。

长期目标包括：

- 对工具型 agent 做完整安全测试
- 支持 source/sink 思路下的攻击执行与验证
- 收集完整 agent 运行轨迹
- 沉淀成功 case、失败 case、skills、target profile
- 导出最匹配后训练的数据格式
- 让系统随着测试积累而越来越强

这里的“完整轨迹”不是只看最终回答，而是至少包括：

- system / user / assistant messages
- tool calls
- tool results
- target observations
- runtime events
- verifier outputs
- evidence artifacts
- parent/subagent runtime relationship
- labels for later training

## 3. 当前短期目标

当前短期目标非常明确：

**先对标 Hermes 的 agent substrate / runtime capability。**

顺序必须是：

1. 先把 Loki 做成一个足够强的 agent substrate
2. 再在上面继续叠完整安全 harness 能力

不要本末倒置。  
不要在 substrate 还不稳定的时候继续堆更多“安全分析花活”。

## 4. 关键设计原则

### 4.1 Loki 是主仓

- 主仓是 `loki`
- `loki` 是唯一 harness 平台
- 老 Loki 的历史结构不重要
- 除了仓库名，其他都可以重构

### 4.2 Hermes 是 substrate baseline

- Hermes 是底座能力参考系
- 不是整仓继承
- 但 agent runtime / tool system / MCP / subagent / prompt/context discipline 都要尽量参考 Hermes

高优先参考目录：

- `/home/shudong/.hermes/hermes-agent/environments/agent_loop.py`
- `/home/shudong/.hermes/hermes-agent/model_tools.py`
- `/home/shudong/.hermes/hermes-agent/tools/registry.py`
- `/home/shudong/.hermes/hermes-agent/tools/delegate_tool.py`
- `/home/shudong/.hermes/hermes-agent/tools/mcp_tool.py`
- `/home/shudong/.hermes/hermes-agent/tools/skills_tool.py`
- `/home/shudong/.hermes/hermes-agent/tools/skill_manager_tool.py`
- `/home/shudong/.hermes/hermes-agent/agent/prompt_builder.py`
- `/home/shudong/.hermes/hermes-agent/agent/trajectory.py`

### 4.3 不用 LangGraph

用户明确拒绝 LangGraph。  
不要引入 LangGraph，也不要按 LangGraph 心智设计系统。

### 4.4 不要改 `mobile_control`

`/home/shudong/workspace/agent/mobile_control` 里的代码可以参考、复制、提炼。

**但绝对不要直接修改那里的任何文件。**

如果要复用：

- 先复制到 `loki`
- 再在 `loki` 里重构

### 4.5 注入信号是观测，不是提前防御

这个项目是做 agent 安全渗透测试的。

因此：

- 不要让 harness 先把 prompt injection / tool misuse 这类攻击面挡掉
- 不要把“攻击指令检测”设计成 runtime guardrail

正确做法是：

- 把它设计成 attack signal / source annotation / verdict feature / training label
- 用来观测、标注、验证 agent 是否被带偏
- 而不是在 harness 层提前替目标 agent 做防御

例外：

- `skill_manage` 之类写入 Loki 自身长期知识库的能力，可以保留污染防护

## 5. 当前架构概览

当前 Loki 已有以下主干：

- `loki_harness/domain/`
  - 统一 task/run/path/verdict/schema
- `loki_harness/orchestrator/`
  - outer orchestrator / task runner / state machine
- `loki_harness/runtime/core/`
  - agent loop
  - registry
  - model_tools
  - prompt_builder
  - MCP client / manager
  - capabilities
- `loki_harness/targets/adapters/`
  - `loki_runtime`
  - `codex_cli`
  - `claude_code`
- `loki_harness/trace_store/`
  - JSONL append-only trace/evidence
- `loki_harness/verification/`
  - deterministic verifier
- `loki_harness/exporters/`
  - episode export
  - training view export
- `loki_harness/sources/`
  - source preparation pipeline

## 6. 当前已经完成的能力

以下内容已经不是空壳，是真实存在并验证过的：

### 6.1 统一 schema 和外层编排

- 统一 task/run/path/verdict/evidence/event schema
- `TaskRunner` 能完整跑一条新链路
- run state machine 已建立
- trace / evidence / episode / knowledge entry 会落盘

### 6.2 Runtime loop

- Loki 有自己的 inner runtime loop
- 支持 tool-calling loop
- 支持 fallback `<tool_call>` 解析
- 支持 tool arg coercion
- 支持 tool result persistence
- 支持 turn budget spill
- 支持一轮内并行 tool calls
- `run_coro_sync()` 用 persistent event loop 思路处理 sync/async bridge

### 6.3 Tool registry

- 已有 Hermes 风格 registry
- 支持：
  - toolset
  - check_fn
  - availability
  - filtering
  - max_result_size
  - restricted clone

### 6.4 Skills

- 已支持：
  - `skills_list`
  - `skill_view`
  - `skill_manage`
- `skill_manage` 已扩展到：
  - `create`
  - `edit`
  - `patch`
  - `delete`
  - `write_file`
  - `remove_file`

### 6.5 Subagent

subagent 已经不是 mock 了。

当前已支持：

- 真正创建子 `LokiAgentLoop`
- 独立 messages
- 受限 registry
- 独立 output dir
- child transcript artifact
- duration / tool_trace / artifact_dir 返回
- depth guard
- batch / parallel delegation

### 6.6 MCP

MCP 已经从纯 metadata mock 前进到：

- 最小真实 stdio MCP client
- run 级 `McpSessionManager`
- 长生命周期连接复用
- tool list cache
- refresh hook

### 6.7 Prompt / context discipline

当前已有：

- tool-use discipline
- execution discipline
- context management guidance
- 可插拔 compression summarizer 接口

注意：

- 这里的“注入检测”应理解为 attack signal / annotation，不是拦截逻辑

### 6.8 训练数据导出

当前已经能导出：

- `episode.json`
- `sft.jsonl`
- `tool_use.jsonl`
- `reward_model.jsonl`

并且攻击信号已进入：

- trace event
- verdict metadata
- episode labels
- reward/tool-use/sft views

## 7. 当前最重要的训练相关设计

项目的训练目标非常重要。后续 agent 不要把轨迹系统只当成 debug log。

当前系统已经开始做的正确事情：

- 收集标准化 trajectory
- 给 episode 增加 labels
- 记录 attack signals
- 记录 behavior signals
- 标记 turn_of_failure
- 标记 agent_compromised

这些都是为了后面的：

- SFT
- tool use tuning
- reward model
- failure recovery
- 安全行为建模

## 8. 当前还没完成的关键缺口

下面这些是当前最重要的未完成项。

### 8.1 MCP 还没到 Hermes 级

虽然已经有长连接 manager，但还缺：

- server 主动 notification 监听
- `tools/list_changed` 自动刷新
- 更完整的 reconnect / backoff
- sampling handler
- 更完整 transport 生命周期管理

### 8.2 Context compression 还只是第一版

已经有：

- deterministic 压缩
- summarizer hook

但还缺：

- 真正基于 context pressure / token budget 的策略
- 更智能的摘要质量
- 与 provider/model metadata 联动
- 更成熟的“压旧保新”保真逻辑

### 8.3 Prompt / context system 还不够成熟

还缺：

- 更系统的 context source management
- memory/session guidance（如果以后 Loki 要支持）
- 更强的 cache preservation
- 更清晰的 runtime role layering

### 8.4 Agent substrate 还没完全追平 Hermes

还缺一些成熟度：

- runtime cancellation / timeout 治理
- backpressure / long-running behavior
- 更成熟的 provider/runtime abstraction
- 更完整的 tool auto-registration 生态

### 8.5 安全 harness 上层能力还没继续深入

因为当前还在优先补 substrate。

后面还需要继续做：

- source/sink-centered verification
- 更强 path ranking
- 更完整黑盒/半白盒攻击执行
- skill extraction from successful/failed runs
- training corpus refinement

## 9. 当前状态判断

如果只看“最终愿景”，项目还远没完成。

粗略判断：

- 长期目标完成度：大约 35% 到 45%
- 短期目标“对标 Hermes 底座能力”：大约 60% 左右，但还没完成

所以当前最正确的策略仍然是：

**继续补 substrate，不要急着堆更多高层安全功能。**

## 10. 后续 agent 的工作优先级

如果你是后续接手的 agent，优先级建议如下：

### P0

- 把 MCP 的 notification / auto-refresh 做真
- 继续提高 subagent 的稳定性和父子 runtime 关系表达
- 把 context compression 升级成真正可用的 summarizer/compressor

### P1

- 强化 prompt/context system
- provider/runtime loop 的 timeout / cancellation / stability 治理
- tool auto-registration / discovery 做成熟

### P2

- 回到安全 harness 上层：
  - source/sink verifier
  - path ranking
  - attack execution refinement
  - skill extraction
  - training export refinement

## 11. 工作方式要求

后续 agent 在这个仓库里工作时，请遵守：

- 只改 `loki` 仓库内的文件
- 不要修改 `mobile_control`
- 不要为了“看起来安全”而提前防掉被测 agent 的攻击面
- 遇到 “注入/恶意指令/命令诱导” 时，优先把它建模成 attack signal / evidence / label
- 优先参考 Hermes 的已验证能力，而不是重新拍脑袋发明一套
- 不要被旧 Loki 的 benchmark 结构绑住

## 12. 你应该先看的文件

如果你要继续开发，建议先读：

- [rearchitecture_phase1.md](/home/shudong/workspace/agent/loki/docs/rearchitecture_phase1.md)
- [models.py](/home/shudong/workspace/agent/loki/loki_harness/domain/models.py)
- [task_runner.py](/home/shudong/workspace/agent/loki/loki_harness/orchestrator/task_runner.py)
- [agent_loop.py](/home/shudong/workspace/agent/loki/loki_harness/runtime/core/agent_loop.py)
- [registry.py](/home/shudong/workspace/agent/loki/loki_harness/runtime/core/registry.py)
- [model_tools.py](/home/shudong/workspace/agent/loki/loki_harness/runtime/core/model_tools.py)
- [capabilities.py](/home/shudong/workspace/agent/loki/loki_harness/runtime/core/capabilities.py)
- [mcp_client.py](/home/shudong/workspace/agent/loki/loki_harness/runtime/core/mcp_client.py)
- [loki_runtime.py](/home/shudong/workspace/agent/loki/loki_harness/targets/adapters/loki_runtime.py)
- [episode.py](/home/shudong/workspace/agent/loki/loki_harness/exporters/episode.py)
- [training_export.py](/home/shudong/workspace/agent/loki/loki_harness/exporters/training_export.py)

以及 Hermes 对应参考实现：

- `/home/shudong/.hermes/hermes-agent/environments/agent_loop.py`
- `/home/shudong/.hermes/hermes-agent/model_tools.py`
- `/home/shudong/.hermes/hermes-agent/tools/registry.py`
- `/home/shudong/.hermes/hermes-agent/tools/delegate_tool.py`
- `/home/shudong/.hermes/hermes-agent/tools/mcp_tool.py`
- `/home/shudong/.hermes/hermes-agent/agent/prompt_builder.py`

## 13. 最后一条

这个项目的真正北极星不是“把一个 agent loop 做出来”，而是：

**把 Loki 做成一个能持续产出高质量 agent 安全测试轨迹、证据、verdict、skills 和训练数据的系统。**

如果某个改动让这个目标更清晰、更稳定、更可训练，那大概率是对的。  
如果某个改动只是让系统“看起来更像助手产品”，那大概率是在跑偏。
