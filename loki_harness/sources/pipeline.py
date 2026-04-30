"""Source-centered preparation pipeline."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from loki_harness.domain import AttackPath, TestRun, TestTask
from loki_harness.sources.generator import HeuristicSourceGenerator
from loki_harness.sources.materializers import WorkspaceSourceMaterializer
from loki_harness.sources.specs import build_sink_specs, build_source_spec
from loki_harness.sources.types import PreparedSourceBundle


class SourcePreparationPipeline:
    """Prepare attacker-controlled sources and expected sinks before execution."""

    def __init__(self):
        self.generator = HeuristicSourceGenerator()
        self.materializer = WorkspaceSourceMaterializer()

    def prepare(self, task: TestTask, run: TestRun, path: AttackPath) -> PreparedSourceBundle:
        source = build_source_spec(task, path)
        sinks = build_sink_specs(task, path)
        content, rationale = self.generator.generate(task, path, source)
        prepared_source = self.materializer.render(
            task=task,
            path=path,
            source=source,
            content=content,
            rationale=rationale,
        )

        bundle_root = Path(run.evidence_dir or "runs") / "source_bundle"
        bundle_root.mkdir(parents=True, exist_ok=True)
        source_path = bundle_root / "prepared_source.json"
        source_path.write_text(json.dumps(asdict(prepared_source), ensure_ascii=False, indent=2), encoding="utf-8")

        for relative_path, file_content in prepared_source.materialized_files.items():
            output = bundle_root / relative_path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(file_content, encoding="utf-8")

        manifest_path = bundle_root / "source_manifest.json"
        manifest = {
            "source": asdict(prepared_source),
            "sinks": [asdict(sink) for sink in sinks],
            "prompt_context": {
                "source_id": prepared_source.source.source_id,
                "source_type": prepared_source.source.source_type,
                "source_origin": prepared_source.source.origin,
                "trust_boundary": prepared_source.source.trust_boundary,
                "entrypoint": prepared_source.metadata.get("entrypoint"),
                "bootstrap_file": prepared_source.metadata.get("bootstrap_file"),
                "candidate_sinks": [sink.sink_type for sink in sinks],
            },
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        workspace_files = dict(task.metadata.get("workspace_files", {}))
        workspace_files.update(prepared_source.materialized_files)
        return PreparedSourceBundle(
            source=prepared_source,
            sinks=sinks,
            workspace_files=workspace_files,
            prompt_context=manifest["prompt_context"],
            metadata={
                "bundle_dir": str(bundle_root),
                "source_path": str(source_path),
                "manifest_path": str(manifest_path),
            },
        )
