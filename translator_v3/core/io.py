from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .models import BenchmarkSuiteReport, DocumentGraph


def artifact_dir(run_root: str, run_id: str) -> Path:
    return Path(run_root).resolve() / run_id


def graph_path(root: str | Path) -> Path:
    return Path(root) / "document_graph.json"


def manifest_path(root: str | Path) -> Path:
    return Path(root) / "manifest.json"


def telemetry_path(root: str | Path) -> Path:
    return Path(root) / "telemetry.json"


def extraction_plan_path(root: str | Path) -> Path:
    return Path(root) / "extraction_plan.json"


def extractor_probes_path(root: str | Path) -> Path:
    return Path(root) / "extractor_probes.json"


def page_ownership_path(root: str | Path) -> Path:
    return Path(root) / "page_ownership.json"


def source_text_path(root: str | Path) -> Path:
    return Path(root) / "source.normalized.txt"


def raw_text_path(root: str | Path) -> Path:
    return Path(root) / "source.raw.txt"


def benchmark_report_path(root: str | Path) -> Path:
    return Path(root) / "benchmark_report.json"


def write_graph_bundle(graph: DocumentGraph, normalized_source_text: str, raw_text: str | None = None) -> Dict[str, str]:
    root = Path(graph.artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    graph_out = graph_path(root)
    manifest_out = manifest_path(root)
    telemetry_out = telemetry_path(root)
    extraction_plan_out = extraction_plan_path(root)
    extractor_probes_out = extractor_probes_path(root)
    page_ownership_out = page_ownership_path(root)
    normalized_out = source_text_path(root)
    graph_out.write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    normalized_out.write_text(normalized_source_text, encoding="utf-8")
    manifest_payload = {
        "run_id": graph.run_id,
        "input_path": graph.input_path,
        "input_class": graph.input_class,
        "ingest_state": graph.ingest_state,
        "configured_source_lang": graph.configured_source_lang,
        "source_lang": graph.source_lang,
        "target_lang": graph.target_lang,
        "selected_spine": graph.selected_spine,
        "planned_extractors": graph.planned_extractors,
        "extraction_plan": graph.extraction_plan,
        "warnings": graph.warnings,
        "analysis": graph.analysis,
        "paths": {
            "document_graph": str(graph_out),
            "telemetry": str(telemetry_out),
            "normalized_source": str(normalized_out),
        },
    }
    if raw_text is not None:
        raw_out = raw_text_path(root)
        raw_out.write_text(raw_text, encoding="utf-8")
        manifest_payload["paths"]["raw_source"] = str(raw_out)
    manifest_out.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if graph.telemetry:
        telemetry_out.write_text(
            json.dumps(graph.telemetry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if graph.extraction_trace:
        extraction_plan_out.write_text(
            json.dumps(graph.extraction_trace.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        extractor_probes_out.write_text(
            json.dumps(
                [probe.to_dict() for probe in graph.extraction_trace.probes],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        page_ownership_out.write_text(
            json.dumps(graph.extraction_trace.page_owners, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        manifest_payload["paths"]["extraction_plan"] = str(extraction_plan_out)
        manifest_payload["paths"]["extractor_probes"] = str(extractor_probes_out)
        manifest_payload["paths"]["page_ownership"] = str(page_ownership_out)
        manifest_out.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in manifest_payload["paths"].items()}


def load_graph(path: str) -> DocumentGraph:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return DocumentGraph.from_dict(payload)


def write_benchmark_report(path: str, report: BenchmarkSuiteReport) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
