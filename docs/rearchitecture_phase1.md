# Loki Re-Architecture

This document records the current architecture after the cleanup.

The repository is still named `loki`, but the Python package is `loki_harness`.

## Goal

Loki is an agent security testing harness:

```text
TestTask -> end-to-end run -> trace + evidence + verdict + training data + reusable knowledge
```

The current engineering priority is to build a Hermes-grade agent substrate first, then layer richer security testing logic on top.

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
```

Historical benchmark code, Docker sandbox scaffolding, dashboard code, generated run artifacts, reports, and old compatibility modules have been removed from the repository.

## Architecture

```text
TestTask
  -> TaskRunner / SecurityHarnessEngine
  -> HeuristicThreatAnalyzer / PathRanker
  -> execution plan
  -> TargetAdapter or LokiAgentLoop
  -> DeterministicVerifier
  -> TraceStore + EvidenceStore + KnowledgeStore
  -> RunVerdict + episode/training exports
```

## Core Runtime

The runtime core owns:

- reusable tool-calling loop
- registry-driven tools
- argument coercion
- result persistence
- turn budget handling
- fallback tool-call parsing
- subagent delegation
- MCP stdio sessions
- skills lifecycle
- prompt/context discipline

The runtime is inspired by Hermes, but Loki owns its contracts and trace schema.

## Target Adapters

Current adapters:

- `loki_runtime`: Loki-owned inner runtime for deterministic and provider-backed experiments
- `codex_cli`: executes `codex exec` and captures JSONL events
- `claude_code`: executes Claude Code CLI in a run-scoped local workspace

## Data Products

The harness should produce:

- append-only JSONL trace
- evidence artifacts
- normalized transcript
- verdict
- `episode.json`
- SFT / tool-use / reward-model export views
- knowledge entries

Attack-like source content is treated as an observation signal and training label, not as a harness-level guardrail that blocks the target behavior before testing.

## Handoff

Read [agent_handoff.md](/home/shudong/workspace/agent/loki/docs/agent_handoff.md) before continuing development.
