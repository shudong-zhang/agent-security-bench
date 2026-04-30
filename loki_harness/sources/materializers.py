"""Materialize attacker-controlled sources into concrete repo-visible inputs."""

from __future__ import annotations

import json

from loki_harness.domain import AttackPath, SourceSpec, TestTask, new_id
from loki_harness.sources.types import PreparedSourceInput


class WorkspaceSourceMaterializer:
    """Materialize a source into workspace files, keeping 'surface' as just one carrier."""

    def render(
        self,
        *,
        task: TestTask,
        path: AttackPath,
        source: SourceSpec,
        content: str,
        rationale: str,
    ) -> PreparedSourceInput:
        materializer = task.metadata.get("source_materializer", {}) or {}
        bundle_dir = str(materializer.get("bundle_dir") or "source_inputs")
        profile = str(materializer.get("profile") or "workspace_bundle")
        entrypoint = f"{bundle_dir}/index.html"
        operator_notes = f"{bundle_dir}/bootstrap/OPERATOR_NOTES.md"
        source_doc = f"{bundle_dir}/source.md"
        manifest = f"{bundle_dir}/source_manifest.json"

        files = {
            source_doc: content,
            operator_notes: self._render_operator_notes(task, path, source, rationale),
            manifest: json.dumps(
                {
                    "source_id": source.source_id,
                    "profile": profile,
                    "source_type": source.source_type,
                    "entrypoint": entrypoint,
                    "origin": source.origin,
                    "carrier_format": source.carrier_format,
                    "manifest_id": new_id("source_manifest"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            entrypoint: self._render_index(task, path, source, content, profile),
        }
        return PreparedSourceInput(
            source=source,
            content=content,
            rationale=rationale,
            materialized_files=files,
            metadata={
                "profile": profile,
                "entrypoint": entrypoint,
                "bootstrap_file": operator_notes,
                "source_file": source_doc,
                "manifest_file": manifest,
            },
        )

    @staticmethod
    def _render_operator_notes(task: TestTask, path: AttackPath, source: SourceSpec, rationale: str) -> str:
        return (
            "# Operator Notes\n\n"
            f"- Task: {task.name}\n"
            f"- Selected path: {path.title}\n"
            f"- Source type: {source.source_type}\n"
            f"- Trust boundary: {source.trust_boundary}\n"
            f"- Purpose: observe whether the source propagates into sensitive sinks.\n"
            f"- Rationale: {rationale}\n"
        )

    @staticmethod
    def _render_index(task: TestTask, path: AttackPath, source: SourceSpec, content: str, profile: str) -> str:
        escaped = _html_escape(content).replace("\n", "<br/>\n")
        return (
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\" />\n"
            f"  <title>{_html_escape(task.target.display_name)} Source Input</title>\n"
            "  <style>\n"
            "    :root { --bg: #f2ecdf; --paper: #fffdf7; --ink: #1f2328; --accent: #9a3412; --line: #ddcfbc; }\n"
            "    body { margin: 0; background: radial-gradient(circle at top, #f8f1e4, var(--bg)); color: var(--ink); font-family: Georgia, serif; }\n"
            "    main { max-width: 900px; margin: 42px auto; background: var(--paper); border: 1px solid var(--line); padding: 30px; box-shadow: 0 14px 40px rgba(40,24,10,0.08); }\n"
            "    .eyebrow { color: var(--accent); text-transform: uppercase; letter-spacing: 0.08em; font-size: 12px; }\n"
            "    h1 { margin-bottom: 8px; }\n"
            "    .meta { color: #6b6254; margin-bottom: 20px; }\n"
            "    article { line-height: 1.7; }\n"
            "  </style>\n"
            "</head>\n"
            "<body>\n"
            "  <main>\n"
            "    <div class=\"eyebrow\">Loki Source Materializer</div>\n"
            f"    <h1>{_html_escape(source.source_type)}</h1>\n"
            f"    <div class=\"meta\">profile={_html_escape(profile)} | path={_html_escape(path.title)}</div>\n"
            f"    <p>{_html_escape(task.description)}</p>\n"
            f"    <article>{escaped}</article>\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
