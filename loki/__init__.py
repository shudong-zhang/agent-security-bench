"""
loki/
=====
Loki 扩展模块：在 agent-security-bench 核心框架之上，提供：
  - 自动化测试用例生成（generate_test_cases）
  - 迭代编排引擎（orchestrator）
  - Payload 自动优化（payload_optimizer）
  - 环境状态修复（env_repair）
  - 多 Agent 结果适配（mobile_adapter）
  - 结果可视化 Dashboard（dashboard/）

使用原则：
  - 所有新功能放 loki/，继承 core/interfaces.py 里的抽象类
  - core/ 代码不修改
"""
