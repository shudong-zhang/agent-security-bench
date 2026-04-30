# Loki

Loki is an agent security testing harness.

The repository name remains `loki`, while the Python package is now `loki_harness` to avoid the old nested `loki/loki` shape.

## Goal

Loki's primary workflow is:

```text
submit a structured TestTask
-> run an end-to-end agent security test
-> collect trace, evidence, verdict, and training-ready trajectories
-> distill reusable knowledge and skills
```

This project is not a legacy benchmark runner, a generic coding assistant, or a LangGraph workflow. The short-term engineering goal is to build a Hermes-grade agent substrate, then layer security testing capabilities on top.

## Current Package Layout

```text
loki_harness/
  cli.py
  domain/
  orchestrator/
  runtime/core/
  sources/
  targets/
  trace_store/
  verification/
  exporters/
  knowledge/
docs/
  agent_handoff.md
  rearchitecture_phase1.md
```

## Commands

```bash
python -m loki_harness blueprint
python -m loki_harness scaffold-run --task-name "Agent security test"
python -m loki_harness run-task --task-file runs/<run>/task.json
python -m loki_harness export-run --run-dir runs/<run>
python -m loki_harness export-corpus --runs-root runs --output exports/corpus.jsonl
python -m loki_harness export-training-views --runs-root runs --output-dir exports/views
```

`main.py` remains a thin compatibility entrypoint:

```bash
python main.py blueprint
```

## Runtime Direction

The runtime core is owned by Loki but intentionally follows Hermes as the substrate baseline:

- reusable tool-calling loop
- registry-driven tools
- subagent delegation
- MCP support
- skills lifecycle
- prompt/context discipline
- complete trace capture

The harness treats prompt injection and tool misuse as attack signals to observe and label, not as instructions for Loki to block before the target agent is tested.

## Handoff

New agents should read [docs/agent_handoff.md](/home/shudong/workspace/agent/loki/docs/agent_handoff.md) first. It describes the current goal, architecture, finished work, remaining gaps, and development rules.
