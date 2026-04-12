from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from translator_v2.orchestrate.pipeline import load_v2_config, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="translator_v2 Phase-1 pipeline")
    parser.add_argument("input_path", help="Source TXT/PDF path")
    parser.add_argument("-o", "--output", required=True, help="Output translation path")
    parser.add_argument("--config", default="config.yaml", help="Config YAML path")
    parser.add_argument("--rules", default=None, help="Override rules.md path")
    parser.add_argument("--glossary", default=None, help="Override glossary.json path")
    parser.add_argument("--provider", default=None, help="Override provider name")
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--generation-mode", default=None, choices=("api", "codex_local"),
                        help="Override generation backend for translator_v2")
    parser.add_argument("--codex-worker-mode", default=None, choices=("bootstrap", "external"),
                        help="How codex_local should source worker execution")
    parser.add_argument("--source-lang", default=None, help="Override source language")
    parser.add_argument("--target-lang", default=None, help="Override target language")
    parser.add_argument("--dry-run", action="store_true", help="Only ingest/segment and persist ledger")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing v2 ledger")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    overrides = {
        "rules_path": args.rules,
        "glossary_path": args.glossary,
        "provider_name": args.provider,
        "model": args.model,
        "generation_mode": args.generation_mode,
        "codex_worker_mode": args.codex_worker_mode,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
    }
    config = load_v2_config(args.config, overrides=overrides)
    result = await run_pipeline(
        input_path=args.input_path,
        output_path=args.output,
        config=config,
        dry_run=args.dry_run,
        resume=not args.fresh,
    )

    print(f"status: {result.status}")
    print(f"output: {result.output_path}")
    print(f"ledger: {result.ledger_path}")
    print(f"report: {result.report_path}")
    if result.blocked_workfile:
        print(f"blocked_workfile: {result.blocked_workfile}")
    print(f"accepted_segments: {result.accepted_segments}/{result.total_segments}")
    return 0 if result.status != "BLOCKED" else 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
