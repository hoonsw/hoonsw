#!/usr/bin/env python3
"""Retired legacy pipeline entrypoint."""

raise SystemExit(
    "run_pipeline.py was retired. "
    "Use `python3 job_runner.py <input> -o <output> --v3-backend codex_local` "
    "or `python3 -m translator_v3.cli run <input> -o <output> --backend codex_local`."
)

import asyncio
import argparse
from pathlib import Path
import yaml

from translate import TranslationConfig, translate_file
from retry_engine import retry_problem_chunks, RetryResult
from review import (
    ReviewConfig, structural_review_chunk, run_structural_review,
    load_aligned_chunks, ReviewWriter,
)
from presets import get_preset, list_presets
from progress import ProgressTracker
from quality_gate import (
    detect_residue_bulk, evaluate_quality_gate, format_verdict_summary,
    QualityVerdict, STATUS_DRAFT, STATUS_REVIEWED, STATUS_DELIVERABLE,
)
from readability_gate import detect_readability_bulk


async def run_pipeline(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    preset_name: str = None,
    enable_retry: bool = True,
    enable_ai_review: bool = False,
    notify: bool = False,
    parallel_workers: int = 0,
    enable_report: bool = False,
    enable_finalize: bool = False,
    dispatch_subagent_tasks: bool = False,
    deliver_to: str = None,
) -> dict:
    """Run the full translation pipeline.

    Returns summary dict.
    """
    preset = get_preset(preset_name) if preset_name else config.preset

    tracker = ProgressTracker(
        input_path=input_path,
        output_path=output_path,
        notify=notify,
        report_interval=config.progress_interval,
    )

    # Step 1: Translate
    from translate import split_into_chunks, load_translation_artifacts
    from input_pipeline import process_input
    ingest_result = process_input(input_path, source_lang=config.source_lang)
    source_text = ingest_result.text
    effective_source_lang = ingest_result.analysis.get('effective_source_lang', config.source_lang)
    if effective_source_lang != config.source_lang:
        print(f"  source_lang auto-corrected: {config.source_lang} -> {effective_source_lang}")
        config.source_lang = effective_source_lang
    chunks = split_into_chunks(source_text, config)
    total_chunks = len(chunks)

    tracker.start(total_chunks, preset_name=preset.name if preset else None)

    # Choose sequential or parallel translation
    if parallel_workers > 0:
        tracker.phase("parallel_translation", f"max {parallel_workers} workers")
        try:
            await _run_parallel_translation(
                input_path, output_path, source_text, config,
                parallel_workers, tracker,
            )
        except Exception as e:
            tracker.fail(str(e))
            raise
    else:
        tracker.phase("translation")
        try:
            await translate_file(input_path, output_path, config)
        except Exception as e:
            tracker.fail(str(e))
            raise

    # Step 2: Structural review
    tracker.phase("structural_review")

    review_config = ReviewConfig.__new__(ReviewConfig)
    review_config.source_lang = config.source_lang
    review_config.target_lang = config.target_lang
    review_config.model = config.model
    review_config.max_turns = config.max_turns
    review_config.chunk_size = config.chunk_size
    review_config.overlap_sentences = config.overlap_sentences
    review_config._raw_config = config._raw_config

    aligned = load_aligned_chunks(input_path, output_path, review_config)
    writer = ReviewWriter()

    threshold_pct = preset.review_threshold_pct if preset else 0.3
    threshold_abs = preset.review_threshold_abs if preset else 5

    problem_chunks = run_structural_review(
        aligned, review_config, writer, input_path, output_path,
        threshold_pct=threshold_pct, threshold_abs=threshold_abs,
    )

    tracker.report_problems(problem_chunks)

    # Step 3: Auto-retry
    retry_result = None
    if enable_retry and problem_chunks:
        tracker.phase("auto_retry", f"{len(problem_chunks)} chunks")

        async def retry_progress(event, data):
            if event == "retry_chunk":
                print(f"  Retrying chunk {data['chunk_num']} (score: {data['score_before']})")
            elif event == "split_retry":
                print(f"  Split-retrying chunk {data['chunk_num']} ({data['chunk_size']} chars)")
            elif event == "retry_done":
                improved = data.get('improvement_count', 0)
                still = len(data.get('still_problematic', []))
                print(f"  Retry done: {improved} improved, {still} still problematic")

        retry_result = await retry_problem_chunks(
            input_path, output_path, config,
            problem_chunks=problem_chunks,
            preset=preset,
            progress_callback=retry_progress,
        )

        tracker.report_problems(
            problem_chunks,
            retried=retry_result.improved_chunks,
        )

    # Step 4: Optional AI review
    if enable_ai_review:
        tracker.phase("ai_review")
        from review import run_ai_review as _run_ai_review
        target = retry_result.still_problematic if retry_result else problem_chunks
        if target:
            aligned = load_aligned_chunks(input_path, output_path, review_config)
            await _run_ai_review(aligned, review_config, writer, input_path, output_path, target_chunks=target)

    # Step 5: Optional subagent task dispatch
    if dispatch_subagent_tasks:
        remaining = retry_result.still_problematic if retry_result else problem_chunks
        if remaining:
            tracker.phase("subagent_dispatch", f"{len(remaining)} tasks")
            _dispatch_subagent_tasks(
                input_path, output_path, config, chunks, aligned, remaining,
            )

    # Step 6: Quality gate — residue detection and status evaluation
    tracker.phase("quality_gate")

    residue_results = _run_residue_detection(output_path, config)
    readability_results = _run_readability_detection(output_path, config)
    quality_verdict = evaluate_quality_gate(
        total_chunks=total_chunks,
        problem_chunks=problem_chunks,
        retry_result=retry_result.summary() if retry_result else None,
        residue_results=residue_results,
        source_lang=config.source_lang,
        readability_results=readability_results,
        target_lang=config.target_lang,
    )

    tracker.set_quality_status(quality_verdict.status)
    print(f"  {format_verdict_summary(quality_verdict)}")

    # Build summary
    summary = {
        'total_chunks': total_chunks,
        'initial_problems': problem_chunks,
        'retry_result': retry_result.summary() if retry_result else None,
        'parallel': parallel_workers > 0,
        'quality_verdict': quality_verdict.to_dict(),
        'quality_status': quality_verdict.status,
        'readability_results': readability_results,
    }

    # Step 7: Optional report generation
    report_paths = {}
    if enable_report:
        tracker.phase("report_generation")
        report_paths = _generate_reports(
            input_path, output_path, config, summary,
            quality_verdict=quality_verdict,
            residue_results=residue_results,
            readability_results=readability_results,
        )
        summary['report_paths'] = report_paths
        print(f"  Reports generated: {', '.join(report_paths.keys())}")

    # Step 8: Optional finalize
    if enable_finalize:
        tracker.phase("finalize")
        finalize_result = _finalize_output(
            input_path, output_path, config, summary, deliver_to,
            quality_verdict=quality_verdict,
        )
        summary['finalize'] = finalize_result.summary()
        print(f"  Finalized: {finalize_result.run_id}")
        if finalize_result.delivery_blocked:
            print(f"  DELIVERY BLOCKED: {finalize_result.delivery_block_reason}")
        elif finalize_result.delivery_dir:
            print(f"  Delivered to: {finalize_result.delivery_dir}")

    tracker.complete(summary)
    return summary


async def _run_parallel_translation(
    input_path: str,
    output_path: str,
    source_text: str,
    config: TranslationConfig,
    max_workers: int,
    tracker: ProgressTracker,
) -> None:
    """Run parallel chapter-based translation."""
    from parallel import detect_chapters, translate_parallel, reassemble_records
    from translate import save_translation_artifacts

    chapters = detect_chapters(source_text, config.source_lang)
    if len(chapters) <= 1:
        # Single chapter / no chapter structure — fall back to sequential
        print(f"  No chapter structure detected, falling back to sequential translation")
        await translate_file(input_path, output_path, config)
        return

    print(f"  Detected {len(chapters)} chapters, translating with {max_workers} workers")
    for ch in chapters:
        print(f"    [{ch.index}] {ch.title} ({len(ch.text):,} chars)")

    async def par_progress(event, data):
        if event == "chapter_start":
            print(f"  Starting chapter {data['chapter']}: {data['title']}")
        elif event == "chapter_done":
            print(f"  Done chapter {data['chapter']}: {data['title']} ({data['chunks']} chunks)")
        elif event == "chapter_failed":
            print(f"  FAILED chapter {data['chapter']}: {data['error']}")

    result = await translate_parallel(
        chapters, config,
        max_workers=max_workers,
        progress_callback=par_progress,
    )

    # Reassemble and save
    records = reassemble_records(result)
    save_translation_artifacts(output_path, records, input_path, config)

    if result.failed_chapters:
        print(f"  Warning: {len(result.failed_chapters)} chapters failed")
    print(f"  Parallel translation complete: {result.total_chunks} total chunks")


def _dispatch_subagent_tasks(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    source_chunks: list,
    aligned: dict,
    problem_chunk_nums: list,
) -> None:
    """Dispatch subagent tasks for remaining problem chunks."""
    from subagent_hooks import (
        SubagentTaskQueue, create_review_tasks, create_rework_tasks,
    )

    queue = SubagentTaskQueue()
    trans_chunks = aligned.get('translation_chunks', [])

    # Create review tasks for problem chunks
    review_tasks = create_review_tasks(
        source_chunks, trans_chunks, problem_chunk_nums, config,
    )
    paths = queue.enqueue_batch(review_tasks)
    print(f"  Dispatched {len(review_tasks)} review tasks to subagent queue")
    print(f"  Task directory: {queue.base}")
    print(f"  Pending tasks: {queue.status_summary()['pending']}")


def _run_residue_detection(output_path: str, config: TranslationConfig) -> list:
    """Run source-language residue detection on translated chunks."""
    from translate import load_translation_artifacts
    artifacts = load_translation_artifacts(output_path)
    if not artifacts['ok']:
        # Fallback: read the output file and treat as single chunk
        from pathlib import Path
        if Path(output_path).exists():
            with open(output_path, 'r', encoding='utf-8') as f:
                text = f.read()
            return detect_residue_bulk([text], source_lang=config.source_lang)
        return []

    chunks = [r.get('translated_text', '') for r in artifacts['records']]
    source_chunks = [r.get('source_text', '') for r in artifacts['records']]
    return detect_residue_bulk(
        chunks,
        source_lang=config.source_lang,
        source_chunks=source_chunks,
    )


def _run_readability_detection(output_path: str, config: TranslationConfig) -> list:
    """Run readability checks on translated chunks."""
    from translate import load_translation_artifacts
    artifacts = load_translation_artifacts(output_path)
    if not artifacts['ok']:
        if Path(output_path).exists():
            with open(output_path, 'r', encoding='utf-8') as f:
                text = f.read()
            return detect_readability_bulk([text], target_lang=config.target_lang)
        return []

    chunks = [r.get('translated_text', '') for r in artifacts['records']]
    return detect_readability_bulk(chunks, target_lang=config.target_lang)


def _generate_reports(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    pipeline_summary: dict,
    quality_verdict: QualityVerdict = None,
    residue_results: list = None,
    readability_results: list = None,
) -> dict:
    """Generate post-editing reports."""
    from report import generate_all_reports, generate_report

    # Generate base report, then enrich with quality gate data
    report_paths = generate_all_reports(
        input_path, output_path, config,
        pipeline_summary=pipeline_summary,
    )

    # Patch the JSON report with quality gate info if available
    if quality_verdict and 'json' in report_paths:
        import json
        from pathlib import Path
        json_path = report_paths['json']
        if Path(json_path).exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['quality'] = {
                'status': quality_verdict.status,
                'blocking_reasons': quality_verdict.blocking_reasons,
                'warnings': quality_verdict.warnings,
                'residue_score': quality_verdict.residue_score,
                'chunks_with_residue': quality_verdict.chunks_with_residue,
                'residue_chunks': residue_results or [],
                'chunks_with_readability_issues': quality_verdict.chunks_with_readability_issues,
                'readability_chunks': readability_results or [],
            }
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    return report_paths


def _finalize_output(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    pipeline_summary: dict,
    deliver_to: str = None,
    quality_verdict: QualityVerdict = None,
):
    """Run the finalize stage."""
    from finalize import finalize_output
    return finalize_output(
        input_path, output_path, config,
        pipeline_summary=pipeline_summary,
        deliver_to=deliver_to,
        quality_verdict=quality_verdict,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Full translation pipeline (phase 2+3)"
    )
    parser.add_argument("input", nargs="?", help="Input text file path")
    parser.add_argument("-o", "--output", help="Output text file path")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--v2", action="store_true", help="Run translator_v2 instead of the legacy full pipeline")
    parser.add_argument("--legacy", action="store_true", help="Force the legacy full pipeline")
    parser.add_argument("--generation-mode", choices=("api", "codex_local"), default=None,
                        help="Override translator_v2 generation backend")
    parser.add_argument("--codex-worker-mode", choices=("bootstrap", "external"), default=None,
                        help="How codex_local should source worker execution")
    parser.add_argument("--dry-run", action="store_true",
                        help="translator_v2 only: ingest/segment and persist ledger without generation")
    parser.add_argument("--fresh", action="store_true",
                        help="translator_v2 only: ignore existing v2 ledger and start a fresh run")
    parser.add_argument("--preset", type=str, default=None,
                        help="Genre preset (ja_novel, ja_essay, ja_philosophy, en_fiction, en_nonfiction, en_philosophy)")
    parser.add_argument("--no-retry", action="store_true", help="Disable auto-retry")
    parser.add_argument("--ai-review", action="store_true", help="Enable AI review on remaining problems")
    parser.add_argument("--notify", action="store_true", help="Send OpenClaw notifications")
    parser.add_argument("--list-presets", action="store_true", help="List available presets and exit")

    # Phase 3 options
    parser.add_argument("--parallel", type=int, default=0, metavar="N",
                        help="Enable chapter-level parallel translation with N workers (0=sequential)")
    parser.add_argument("--report", action="store_true",
                        help="Generate post-editing reports (txt, json, html)")
    parser.add_argument("--finalize", action="store_true",
                        help="Run finalize stage (cleanup, package, manifest)")
    parser.add_argument("--dispatch-subagents", action="store_true",
                        help="Dispatch remaining problem chunks to subagent task queue")
    parser.add_argument("--deliver-to", type=str, default=None,
                        help="Copy finalized package to this directory")

    args = parser.parse_args()

    if args.list_presets:
        print("Available genre presets:")
        for p in list_presets():
            print(f"  {p['name']:20s} {p['display_name']} — {p['description']}")
        return
    if args.v2 and args.legacy:
        parser.error("--v2 and --legacy cannot be used together")

    if not args.input:
        parser.error("the following arguments are required: input")
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        exit(1)
    if not args.output:
        parser.error("the following arguments are required: -o/--output")

    raw_config = {}
    config_path = Path(args.config)
    if config_path.exists():
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    pipeline_version = (
        "v2" if args.v2 else
        "legacy" if args.legacy else
        raw_config.get("pipeline_version", "legacy")
    )

    incompatible_flags = [
        name for name, enabled in [
            ("--preset", bool(args.preset)),
            ("--no-retry", args.no_retry),
            ("--ai-review", args.ai_review),
            ("--notify", args.notify),
            ("--parallel", args.parallel > 0),
            ("--report", args.report),
            ("--finalize", args.finalize),
            ("--dispatch-subagents", args.dispatch_subagents),
            ("--deliver-to", bool(args.deliver_to)),
        ]
        if enabled
    ]
    if pipeline_version == "v2" and incompatible_flags:
        if args.v2:
            print(
                "translator_v2 Phase 1 does not support these run_pipeline options yet: "
                + ", ".join(incompatible_flags)
            )
            print("Use --legacy for the old full pipeline, or run without those options to use translator_v2.")
            exit(2)
        print(
            "↪ Falling back to legacy run_pipeline.py because unsupported translator_v2 options were requested: "
            + ", ".join(incompatible_flags)
        )
        pipeline_version = "legacy"

    if pipeline_version == "v2":
        from translator_v2.orchestrate.pipeline import load_v2_config, run_pipeline as run_v2_pipeline

        v2_config = load_v2_config(
            args.config,
            overrides={
                "generation_mode": args.generation_mode,
                "codex_worker_mode": args.codex_worker_mode,
            },
        )
        try:
            result = asyncio.run(run_v2_pipeline(
                input_path=args.input,
                output_path=args.output,
                config=v2_config,
                dry_run=args.dry_run,
                resume=not args.fresh,
            ))
            print("\n" + "=" * 60)
            print("translator_v2 summary:")
            print(f"  Status: {result.status}")
            print(f"  Accepted segments: {result.accepted_segments}/{result.total_segments}")
            print(f"  Ledger: {result.ledger_path}")
            print(f"  Report: {result.report_path}")
            if result.blocked_workfile:
                print(f"  Blocked workfile: {result.blocked_workfile}")
            print("=" * 60)
            return
        except KeyboardInterrupt:
            print("\nPipeline interrupted")
            exit(0)
        except Exception as e:
            print(f"\nFatal error: {e}")
            exit(1)

    try:
        config = TranslationConfig(args.config, preset_override=args.preset)
    except Exception as e:
        print(f"Error loading config: {e}")
        exit(1)

    if config.preset:
        print(f"Preset: {config.preset.display_name} ({config.preset.name})")

    try:
        result = asyncio.run(run_pipeline(
            args.input, args.output, config,
            preset_name=args.preset or config._raw_config.get('preset'),
            enable_retry=not args.no_retry,
            enable_ai_review=args.ai_review,
            notify=args.notify,
            parallel_workers=args.parallel,
            enable_report=args.report,
            enable_finalize=args.finalize,
            dispatch_subagent_tasks=args.dispatch_subagents,
            deliver_to=args.deliver_to,
        ))

        # Print final summary
        print("\n" + "=" * 60)
        print("Pipeline summary:")
        print(f"  Total chunks: {result['total_chunks']}")
        print(f"  Initial problems: {len(result['initial_problems'])}")
        if result.get('retry_result'):
            rr = result['retry_result']
            print(f"  Improved by retry: {rr['improvement_count']}")
            print(f"  Still problematic: {len(rr.get('still_problematic', []))}")
            if rr.get('split_retried'):
                print(f"  Split-retried: {len(rr['split_retried'])}")
        if result.get('parallel'):
            print(f"  Mode: parallel")
        # Quality gate
        qv = result.get('quality_verdict', {})
        if qv:
            status_labels = {
                "draft": "DRAFT",
                "reviewed": "REVIEWED",
                "deliverable": "DELIVERABLE",
            }
            print(f"  Quality status: {status_labels.get(qv.get('status', ''), qv.get('status', '?'))}")
            if qv.get('blocking_reasons'):
                for reason in qv['blocking_reasons']:
                    print(f"    [BLOCK] {reason}")
            if qv.get('chunks_with_residue'):
                print(f"  Source residue: {qv['chunks_with_residue']} chunk(s) affected")
        if result.get('report_paths'):
            print(f"  Reports: {', '.join(result['report_paths'].keys())}")
        if result.get('finalize'):
            fin = result['finalize']
            print(f"  Finalized: {fin.get('run_id', '?')}")
            if fin.get('delivery_blocked'):
                print(f"  DELIVERY BLOCKED: {fin.get('delivery_block_reason', '?')}")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\nPipeline interrupted")
        exit(0)
    except Exception as e:
        print(f"\nFatal error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
