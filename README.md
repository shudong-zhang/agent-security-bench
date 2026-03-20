agent-security-bench/
├── core/
│   ├── interfaces.py      # 所有抽象类和数据模型（骨架，轻易不改）
│   ├── dataset.py         # JSON 数据集加载器
│   └── runner.py          # 主调度器
│
├── surfaces/              # 【插槽①】注入面实现
│   ├── local_file.py      # ✅ 本地文件注入
│   ├── web_page.py        # ✅ 网页内容注入
│   └── email.py           # TODO
│
├── agents/                # 【插槽②】Agent 适配器
│   └── claude_adapter.py  # ✅ Claude Code CLI
│
├── evaluators/            # 【插槽③】评估器
│   └── evaluators.py      # ✅ ToolCall / NetworkRequest / LLMJudge
│
├── sandbox/
│   └── manager.py         # ✅ Docker 沙箱生命周期管理
│
├── reports/
│   └── markdown_reporter.py  # ✅ Markdown + JSON 报告
│
├── datasets/
│   └── cases/
│       ├── 001_local_file_injection.json
│       └── 002_web_page_injection.json
│
├── docker/
│   ├── Dockerfile         # ✅ 沙箱容器镜像
│   ├── entrypoint.sh      # ✅ 容器入口
│   ├── build.sh           # ✅ 镜像构建脚本
│   └── .env.example       # ✅ 环境变量模板
│
└── main.py                # 入口，组装并运行
