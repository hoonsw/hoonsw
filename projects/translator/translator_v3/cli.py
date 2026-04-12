#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from presets import get_preset
from translator_v3.benchmarks import default_manifest_path, load_cases, run_benchmark_suite
from translator_v3.core.io import write_graph_bundle
from translator_v3.core.models import V3Config
from translator_v3.ingest.spine import build_document_graph
from translator_v3.orchestrate import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "translator_v3 tooling. Current benchmark coverage reaches "
            "document-graph ingest and external extractor execution when a "
            "supported tool is locally available; it still does not prove "
            "end-to-end translation quality."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="classify input and build a v3 document graph")
    inspect_parser.add_argument("input_path")
    inspect_parser.add_argument("--source-lang", default="ja")
    inspect_parser.add_argument("--target-lang", default="ko")
    inspect_parser.add_argument("--run-root", default="runs_v3")
    inspect_parser.add_argument("--run-id")

    run_parser = sub.add_parser("run", help="run translator_v3 vertical slice")
    run_parser.add_argument("input_path")
    run_parser.add_argument("-o", "--output-path", required=True)
    run_parser.add_argument("--source-lang", default="ja")
    run_parser.add_argument("--target-lang", default="ko")
    run_parser.add_argument("--preset")
    run_parser.add_argument("--run-root", default="runs_v3")
    run_parser.add_argument(
        "--backend",
        required=True,
        choices=["echo", "provider_api", "codex_local"],
        help="`echo` is test-only; `provider_api` and `codex_local` are real translation backends.",
    )
    run_parser.add_argument("--provider", default="openai")
    run_parser.add_argument("--model", default="gpt-5.2-chat-latest")
    run_parser.add_argument("--reasoning-effort", default="high")

    bench_parser = sub.add_parser(
        "benchmark",
        help="run the pretranslation benchmark harness (not an end-to-end translation-quality gate)",
    )
    bench_parser.add_argument("--manifest", default=str(default_manifest_path()))
    bench_parser.add_argument("--source-lang", default="ja")
    bench_parser.add_argument("--target-lang", default="ko")
    bench_parser.add_argument("--preset")
    bench_parser.add_argument("--artifact-root", default="runs_v3/benchmarks")
    bench_parser.add_argument("--report-path", default="runs_v3/benchmarks/benchmark_report.json")
    return parser


def _resolve_preset(name: str | None):
    if not name:
        return None
    preset = get_preset(name)
    if preset is None:
        raise SystemExit(f"Unknown preset: {name}")
    return preset


def _resolve_source_lang(source_lang: str, preset_name: str | None) -> str:
    if source_lang != "ja" or not preset_name:
        return source_lang
    if preset_name.startswith("en_"):
        return "en"
    return source_lang


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        preset = _resolve_preset(None)
        config = V3Config(
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            run_root=args.run_root,
            preset_name=preset.name if preset else None,
            preset_display_name=preset.display_name if preset else None,
            preset_prompt_modifier=preset.prompt_modifier if preset else "",
            preset_extra_rules=preset.extra_rules if preset else "",
            preset_chunk_boundary_hints=preset.chunk_boundary_hints if preset else "paragraph",
        )
        graph = build_document_graph(args.input_path, config, run_id=args.run_id)
        paths = write_graph_bundle(
            graph,
            normalized_source_text=graph.analysis.get("normalized_text", "") or graph.analysis.get("processed_text", "") or "",
        )
        print(
            json.dumps(
                {
                    "run_id": graph.run_id,
                    "artifact_dir": graph.artifact_dir,
                    "input_class": graph.input_class,
                    "extraction_plan": graph.extraction_plan,
                    "planned_extractor": graph.extraction_trace.planned_extractor if graph.extraction_trace else None,
                    "resolved_owner": graph.extraction_trace.resolved_owner if graph.extraction_trace else None,
                    "integration_status": graph.extraction_trace.integration_status if graph.extraction_trace else None,
                    "external_execution_attempted": (
                        graph.extraction_trace.external_execution_attempted if graph.extraction_trace else None
                    ),
                    "external_execution_succeeded": (
                        graph.extraction_trace.external_execution_succeeded if graph.extraction_trace else None
                    ),
                    "effective_source_path": graph.analysis.get("effective_source_path"),
                    "paths": paths,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "benchmark":
        preset = _resolve_preset(args.preset)
        cases = load_cases(args.manifest)
        config = V3Config(
            source_lang=_resolve_source_lang(args.source_lang, preset.name if preset else None),
            target_lang=args.target_lang,
            run_root=args.artifact_root,
            preset_name=preset.name if preset else None,
            preset_display_name=preset.display_name if preset else None,
            preset_prompt_modifier=preset.prompt_modifier if preset else "",
            preset_extra_rules=preset.extra_rules if preset else "",
            preset_chunk_boundary_hints=preset.chunk_boundary_hints if preset else "paragraph",
        )
        report = run_benchmark_suite(
            cases=cases,
            config=config,
            artifact_root=args.artifact_root,
            report_path=args.report_path,
            manifest_path=args.manifest,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "run":
        preset = _resolve_preset(args.preset)
        config = V3Config(
            source_lang=_resolve_source_lang(args.source_lang, preset.name if preset else None),
            target_lang=args.target_lang,
            preset_name=preset.name if preset else None,
            preset_display_name=preset.display_name if preset else None,
            preset_prompt_modifier=preset.prompt_modifier if preset else "",
            preset_extra_rules=preset.extra_rules if preset else "",
            preset_chunk_boundary_hints=preset.chunk_boundary_hints if preset else "paragraph",
            run_root=args.run_root,
            backend_name=args.backend,
            provider_name=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        result = asyncio.run(run_pipeline(args.input_path, args.output_path, config))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.status == "DELIVERABLE":
            return 0
        if result.status == "BLOCKED":
            return 3
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
