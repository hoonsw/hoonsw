#!/usr/bin/env python3
"""
Run the legacy translator and translator_v2 side-by-side on the same input.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from translate import TranslationConfig, translate_file
from translator_v2.orchestrate.pipeline import load_v2_config, run_pipeline as run_v2_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare legacy translator and translator_v2")
    parser.add_argument("input", help="Input text or PDF path")
    parser.add_argument("--config", default="config.yaml", help="Shared config file")
    parser.add_argument("--output-dir", default="compare_outputs", help="Directory for side-by-side outputs")
    parser.add_argument("--dry-run", action="store_true", help="Only run planning/segmentation where supported")
    parser.add_argument("--skip-legacy", action="store_true", help="Skip legacy run")
    parser.add_argument("--skip-v2", action="store_true", help="Skip translator_v2 run")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    legacy_output = output_dir / f"{stem}.legacy.txt"
    v2_output = output_dir / f"{stem}.v2.txt"

    if not args.skip_legacy:
        legacy_config = TranslationConfig(args.config)
        await translate_file(
            str(input_path),
            str(legacy_output),
            legacy_config,
            dry_run=args.dry_run,
        )
        print(f"legacy_output: {legacy_output}")

    if not args.skip_v2:
        v2_config = load_v2_config(args.config)
        result = await run_v2_pipeline(
            input_path=str(input_path),
            output_path=str(v2_output),
            config=v2_config,
            dry_run=args.dry_run,
        )
        print(f"v2_output: {v2_output}")
        print(f"v2_status: {result.status}")
        print(f"v2_ledger: {result.ledger_path}")

    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
