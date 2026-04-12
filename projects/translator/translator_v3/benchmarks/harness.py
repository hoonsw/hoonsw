from __future__ import annotations

from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Iterable, List, Optional

from translator_v3.core.io import write_benchmark_report, write_graph_bundle
from translator_v3.core.models import BenchmarkCase, BenchmarkCaseResult, BenchmarkSuiteReport, V3Config
from translator_v3.ingest.spine import build_document_graph


def _count_nodes(graph, node_type: str) -> int:
    return sum(1 for node in graph.nodes if node.node_type == node_type)


def run_benchmark_suite(
    cases: Iterable[BenchmarkCase],
    config: Optional[V3Config] = None,
    artifact_root: Optional[str] = None,
    report_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> BenchmarkSuiteReport:
    config = config or V3Config()
    case_results: List[BenchmarkCaseResult] = []
    input_class_counter: Counter[str] = Counter()

    if artifact_root:
        Path(artifact_root).mkdir(parents=True, exist_ok=True)

    for case in cases:
        case_config = V3Config(
            source_lang=case.source_lang,
            target_lang=config.target_lang,
            run_root=artifact_root or config.run_root,
            classifier_version=config.classifier_version,
            graph_builder_version=config.graph_builder_version,
            benchmark_version=config.benchmark_version,
            manifest_version=config.manifest_version,
            telemetry_version=config.telemetry_version,
            min_chapter_size=config.min_chapter_size,
            page_break_token=config.page_break_token,
        )
        started = perf_counter()
        graph = build_document_graph(case.input_path, case_config, run_id=f"bench-{case.case_id}")
        elapsed_ms = int((perf_counter() - started) * 1000)
        input_class_counter[graph.input_class] += 1
        if artifact_root:
            write_graph_bundle(graph, normalized_source_text=graph.analysis.get("normalized_text", "") or graph.analysis.get("processed_text", "") or "")
        expected_match = None
        if case.expected_input_class is not None:
            expected_match = case.expected_input_class == graph.input_class
        case_results.append(
            BenchmarkCaseResult(
                case_id=case.case_id,
                label=case.label,
                input_path=case.input_path,
                input_class=graph.input_class,
                ingest_state=graph.ingest_state,
                source_lang=graph.source_lang,
                planned_extractor=graph.extraction_trace.planned_extractor if graph.extraction_trace else "",
                resolved_owner=graph.extraction_trace.resolved_owner if graph.extraction_trace else "",
                integration_status=graph.extraction_trace.integration_status if graph.extraction_trace else "",
                external_execution_attempted=(
                    graph.extraction_trace.external_execution_attempted if graph.extraction_trace else False
                ),
                external_execution_succeeded=(
                    graph.extraction_trace.external_execution_succeeded if graph.extraction_trace else False
                ),
                pages_total=graph.telemetry.pages_total if graph.telemetry else 0,
                chapters_total=_count_nodes(graph, "chapter"),
                blocks_total=_count_nodes(graph, "block"),
                warnings_count=len(graph.warnings),
                elapsed_ms=elapsed_ms,
                expected_input_class=case.expected_input_class,
                expected_match=expected_match,
            )
        )

    summary = {
        "case_count": len(case_results),
        "input_classes": dict(input_class_counter),
        "all_expected_classes_matched": all(
            result.expected_match is not False for result in case_results
        ),
        "external_execution_attempted_cases": sum(
            1 for result in case_results if result.external_execution_attempted
        ),
        "external_execution_succeeded_cases": sum(
            1 for result in case_results if result.external_execution_succeeded
        ),
        "max_elapsed_ms": max((result.elapsed_ms for result in case_results), default=0),
        "suite_scope": "pretranslation_graph_and_extraction",
        "gate_status": "pretranslation_non_blocking",
        "gate_reason": (
            "This suite validates input classification, document-graph ingest, and external extractor execution "
            "when the local environment provides a supported tool. It still does not prove end-to-end translation "
            "quality or extraction-quality superiority across benchmark corpora."
        ),
        "required_next_slices": [
            "v2_baseline_comparison",
            "adequacy_or_alignment_checks",
            "extractor_quality_comparison",
            "discord_operational_cutover",
        ],
    }
    report = BenchmarkSuiteReport(
        version=config.benchmark_version,
        manifest_path=str(manifest_path or ""),
        case_results=case_results,
        summary=summary,
    )
    if report_path:
        write_benchmark_report(report_path, report)
    return report
