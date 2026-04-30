"""CLI for the rebuilt Loki harness.

Commands:

- `blueprint`    print the re-architecture document path
- `scaffold-run` create a run skeleton using the new schema and trace store
"""

import argparse
import logging
from pathlib import Path
import json

from loki_harness.domain import TargetProfile, TestTask, new_id
from loki_harness.exporters import RunTrainingExporter
from loki_harness.orchestrator import SecurityHarnessEngine
from loki_harness.orchestrator.task_runner import TaskRunner

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [loki] %(levelname)s %(message)s",
)


def cmd_blueprint(_args):
    doc = Path(__file__).resolve().parent.parent / "docs" / "rearchitecture_phase1.md"
    print(doc)


def cmd_scaffold_run(args):
    target = TargetProfile(
        target_id=args.target_id,
        target_type=args.target_type,
        display_name=args.target_name,
        access_level=args.access_level,
        source_availability=args.source_availability,
    )
    task = TestTask(
        task_id=new_id("task"),
        name=args.task_name,
        target=target,
        description=args.description,
        allowed_actions=args.allow or [],
        forbidden_actions=args.forbid or [],
        success_criteria={"summary": args.success_criteria},
        reporting_requirements={"format": "json"},
        run_budget={"time_budget": args.time_budget, "run_budget": args.run_budget},
        metadata={
            "target_adapter": args.target_adapter,
            "target_model": args.target_model,
            "runtime_provider": args.runtime_provider,
            "runtime_max_turns": args.runtime_max_turns,
            "runtime_temperature": args.runtime_temperature,
            "runtime_max_tokens": args.runtime_max_tokens,
            "source_materializer": {
                "enabled": args.seed_sources,
                "kind": args.source_type,
                "profile": args.source_profile,
                "bundle_dir": args.source_bundle_dir,
            } if args.seed_sources else {},
            "source_spec": {
                "source_type": args.source_type,
                "origin": args.source_origin,
                "carrier_format": args.source_format,
                "trust_boundary": args.source_trust_boundary,
                "description": args.source_description,
            } if args.seed_sources else {},
            "source_content": {
                "objective": args.source_objective,
                "tone": args.source_tone,
            } if args.seed_sources else {},
            "sink_spec": [
                {
                    "sink_type": sink_type,
                    "component": "target_runtime",
                    "sensitivity": "high",
                    "description": f"Configured candidate sink: {sink_type}",
                }
                for sink_type in args.sink_type
            ] if args.seed_sources and args.sink_type else [],
        },
    )
    engine = SecurityHarnessEngine(runs_root=args.output)
    run, _store = engine.create_run(task)
    print(f"run_id={run.run_id}")
    print(f"trace={run.trace_path}")
    print(f"evidence_dir={run.evidence_dir}")


def _load_task_from_file(task_file: str) -> TestTask:
    data = json.loads(Path(task_file).read_text(encoding="utf-8"))
    target_data = data["target"]
    return TestTask(
        task_id=data["task_id"],
        name=data["name"],
        target=TargetProfile(
            target_id=target_data["target_id"],
            target_type=target_data["target_type"],
            display_name=target_data["display_name"],
            access_level=target_data["access_level"],
            source_availability=target_data["source_availability"],
            metadata=target_data.get("metadata", {}),
        ),
        description=data["description"],
        allowed_actions=data.get("allowed_actions", []),
        forbidden_actions=data.get("forbidden_actions", []),
        success_criteria=data.get("success_criteria", {}),
        reporting_requirements=data.get("reporting_requirements", {}),
        run_budget=data.get("run_budget", {}),
        metadata=data.get("metadata", {}),
    )


def cmd_run_task(args):
    task = _load_task_from_file(args.task_file)
    runner = TaskRunner(runs_root=args.output, knowledge_root=args.knowledge)
    result = runner.run_task(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_export_run(args):
    exporter = RunTrainingExporter()
    record = exporter.export_run(args.run_dir)
    if args.output:
        Path(args.output).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(args.output)
        return
    print(json.dumps(record, ensure_ascii=False, indent=2))


def cmd_export_corpus(args):
    exporter = RunTrainingExporter()
    records = exporter.export_runs(args.runs_root)
    if args.output:
        exporter.write_jsonl(args.output, records)
        print(args.output)
        return
    print(json.dumps(records, ensure_ascii=False, indent=2))


def cmd_export_training_views(args):
    exporter = RunTrainingExporter()
    views = exporter.export_training_views(args.runs_root)
    if args.output_dir:
        exporter.write_training_views(args.output_dir, views)
        print(args.output_dir)
        return
    print(json.dumps(views, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "loki",
        description = "Loki security harness platform",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = """
示例：
  python -m loki_harness blueprint
  python -m loki_harness scaffold-run --task-name "OpenClaw black-box security test"
  python -m loki_harness run-task --task-file runs/<run>/task.json
  python -m loki_harness export-run --run-dir runs/<run>
  python -m loki_harness export-corpus --runs-root runs --output exports/corpus.jsonl
  python -m loki_harness export-training-views --runs-root runs --output-dir exports/views
        """,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p_blueprint = sub.add_parser("blueprint", help="print the phase-1 architecture document path")
    p_blueprint.set_defaults(func=cmd_blueprint)

    p_scaffold = sub.add_parser("scaffold-run", help="create a run skeleton using the new schema")
    p_scaffold.add_argument("--output", default="runs", help="run root directory")
    p_scaffold.add_argument("--task-name", default="Agent security test task")
    p_scaffold.add_argument("--description", default="Phase-1 scaffolded Loki test run")
    p_scaffold.add_argument("--target-id", default="target_local")
    p_scaffold.add_argument("--target-type", default="repo")
    p_scaffold.add_argument("--target-name", default="Local target")
    p_scaffold.add_argument("--access-level", default="white_box")
    p_scaffold.add_argument("--source-availability", default="source+docs")
    p_scaffold.add_argument("--target-adapter", default="claude_code")
    p_scaffold.add_argument("--target-model", default=None)
    p_scaffold.add_argument("--runtime-provider", default="replay")
    p_scaffold.add_argument("--runtime-max-turns", type=int, default=8)
    p_scaffold.add_argument("--runtime-temperature", type=float, default=0.2)
    p_scaffold.add_argument("--runtime-max-tokens", type=int, default=None)
    p_scaffold.add_argument("--time-budget", default="1h")
    p_scaffold.add_argument("--run-budget", type=int, default=1)
    p_scaffold.add_argument("--success-criteria", default="Produce trace, evidence, and verdict")
    p_scaffold.add_argument("--seed-sources", action="store_true", help="seed attacker-controlled sources into the task workspace")
    p_scaffold.add_argument("--source-type", default="workspace_bootstrap")
    p_scaffold.add_argument("--source-origin", default="attacker_controlled_content")
    p_scaffold.add_argument("--source-format", default="markdown")
    p_scaffold.add_argument("--source-trust-boundary", default="crosses_into_agent_context")
    p_scaffold.add_argument("--source-description", default="Seeded adversarial source for source-to-sink testing")
    p_scaffold.add_argument("--source-profile", default="workspace_bundle")
    p_scaffold.add_argument("--source-bundle-dir", default="source_inputs")
    p_scaffold.add_argument("--source-objective", default="诱导目标偏离原始任务并暴露安全弱点")
    p_scaffold.add_argument("--source-tone", default="plausible_but_high_pressure")
    p_scaffold.add_argument("--sink-type", action="append", default=[], help="candidate sink type; can be repeated")
    p_scaffold.add_argument("--allow", nargs="*")
    p_scaffold.add_argument("--forbid", nargs="*")
    p_scaffold.set_defaults(func=cmd_scaffold_run)

    p_run_task = sub.add_parser("run-task", help="run a task file through the new harness flow")
    p_run_task.add_argument("--task-file", required=True, help="path to task.json")
    p_run_task.add_argument("--output", default="runs", help="run root directory")
    p_run_task.add_argument("--knowledge", default="knowledge", help="knowledge store root")
    p_run_task.set_defaults(func=cmd_run_task)

    p_export_run = sub.add_parser("export-run", help="export one run into a training-oriented JSON record")
    p_export_run.add_argument("--run-dir", required=True, help="path to one run directory")
    p_export_run.add_argument("--output", default=None, help="optional output json file")
    p_export_run.set_defaults(func=cmd_export_run)

    p_export_corpus = sub.add_parser("export-corpus", help="export all finished runs into JSONL")
    p_export_corpus.add_argument("--runs-root", default="runs", help="root directory that contains run_* folders")
    p_export_corpus.add_argument("--output", default=None, help="optional jsonl output file")
    p_export_corpus.set_defaults(func=cmd_export_corpus)

    p_export_views = sub.add_parser("export-training-views", help="export SFT/tool-use/reward-model training views")
    p_export_views.add_argument("--runs-root", default="runs", help="root directory that contains run_* folders")
    p_export_views.add_argument("--output-dir", default=None, help="optional directory for per-view jsonl files")
    p_export_views.set_defaults(func=cmd_export_training_views)

    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
