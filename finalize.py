#!/usr/bin/env python3
"""
Finalize stage for final output cleanup and packaging.

Phase 3 feature: takes the pipeline output and produces a clean,
publication-ready package with all artifacts organized.

Finalize steps:
1. Clean up translation text (trailing whitespace, double blank lines, etc.)
2. Generate post-editing reports (text, JSON, HTML)
3. Package everything into a run directory
4. Write a manifest with all file paths and checksums
5. Optionally copy to a delivery directory

Usage:
    python3 finalize.py output/sample_ko.txt --input input/sample.txt
    python3 finalize.py output/sample_ko.txt --input input/sample.txt --deliver-to /path/to/delivery

Or from Python:
    from finalize import finalize_output
    result = finalize_output(input_path, output_path, config)
"""

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from translate import TranslationConfig, load_translation_artifacts, split_into_chunks, load_glossary
from report import generate_all_reports, generate_report
from run_manager import RunManager
from quality_gate import (
    QualityVerdict, STATUS_DRAFT, STATUS_REVIEWED, STATUS_DELIVERABLE,
    format_verdict_summary,
)


# ── Text cleanup ────────────────────────────────────────────

def _normalize_punctuation_ko(text: str) -> str:
    """Normalize Japanese-style punctuation toward a cleaner Korean output style.

    Current policy:
    - Convert Japanese dialogue/book-title brackets to common Korean/ASCII-friendly quotes
    - Normalize fullwidth spaces and common Japanese punctuation marks
    - Reduce excessive ellipsis variation
    """
    replacements = {
        '\u3000': ' ',   # ideographic space
        '，': ',',
        '、': ',',
        '｡': '.',
        '。': '.',
        '！': '!',
        '？': '?',
        '（': '(',
        '）': ')',
        '［': '[',
        '］': ']',
        '｛': '{',
        '｝': '}',
        '〈': '<',
        '〉': '>',
        '「': '"',
        '」': '"',
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    # 『』 handling: prefer < > when the content looks like a title/term,
    # otherwise keep conservatively as nested quotes.
    def repl_double_kakko(m):
        inner = m.group(1).strip()
        title_like = False
        # short-ish title/term heuristic: no terminal punctuation, not too long
        if inner and len(inner) <= 40 and not re.search(r'[\.!\?]$', inner):
            title_like = True
        if re.search(r'(이론|개념|구조|세계|욕망|주체|타자|에크리튀르|기호|상징계|상상계|작품|편|장|권)$', inner):
            title_like = True
        if title_like:
            return f'<{inner}>'
        return f'“{inner}”'

    text = re.sub(r'『\s*(.*?)\s*』', repl_double_kakko, text)

    # normalize ellipsis-like runs
    text = text.replace('......', '……')
    text = re.sub(r'…{3,}', '……', text)
    text = re.sub(r'\.\.\.+', '……', text)

    # remove odd spaces before punctuation
    text = re.sub(r'\s+([,\.!\?])', r'\1', text)
    # single space after sentence punctuation when text is inline
    text = re.sub(r'([\.!\?])(?=[^\s\n\]\)"”』」])', r'\1 ', text)
    return text


def _apply_glossary_normalization(text: str, glossary: dict) -> str:
    """Apply glossary-approved alias normalization after translation.

    If a glossary entry has aliases, normalize all aliases to the approved translation.
    Also normalize the source term itself when mode is transliterate/translate and a
    clear approved translation is present.
    """
    if not glossary:
        return text

    rules = []
    for source_term, value in glossary.items():
        if isinstance(value, dict):
            translation = value.get('translation', '').strip()
            aliases = value.get('aliases', []) or []
            mode = value.get('mode', 'translate')
        else:
            translation = str(value).strip()
            aliases = []
            mode = 'translate'

        if not translation:
            continue

        candidates = []
        if mode in ('translate', 'transliterate', 'keep'):
            candidates.append(source_term)
        candidates.extend([a for a in aliases if a])

        for cand in candidates:
            if cand and cand != translation:
                rules.append((cand, translation))

    # longest first so more specific aliases win
    for src, dst in sorted(rules, key=lambda x: len(x[0]), reverse=True):
        text = text.replace(src, dst)
    return text


def cleanup_translation_text(text: str, glossary: Optional[dict] = None) -> str:
    """Clean up final translation text for publication.

    Applies:
    - Normalize line endings
    - Remove trailing whitespace per line
    - Conservative Korean punctuation normalization
    - Glossary alias normalization for stable approved spellings
    - Collapse 3+ consecutive blank lines to 2
    - Remove leading/trailing blank lines
    """
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Remove trailing whitespace per line
    lines = [line.rstrip() for line in text.split('\n')]
    text = '\n'.join(lines)

    text = _normalize_punctuation_ko(text)
    text = _apply_glossary_normalization(text, glossary or {})

    # Collapse excessive blank lines (3+ -> 2)
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # Strip leading/trailing whitespace
    text = text.strip()

    # Ensure file ends with newline
    if text and not text.endswith('\n'):
        text += '\n'

    return text


# ── Finalize result ────────────────────────────────────────

class FinalizeResult:
    """Result of the finalize stage."""

    def __init__(self):
        self.run_id: str = ""
        self.run_dir: str = ""
        self.cleaned_output: str = ""
        self.report_paths: Dict[str, str] = {}
        self.manifest: dict = {}
        self.delivery_dir: str = ""
        self.quality_verdict: Optional[QualityVerdict] = None
        self.delivery_blocked: bool = False
        self.delivery_block_reason: str = ""

    def summary(self) -> dict:
        result = {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "cleaned_output": self.cleaned_output,
            "reports": self.report_paths,
            "delivery_dir": self.delivery_dir,
        }
        if self.quality_verdict:
            result["quality_status"] = self.quality_verdict.status
            result["quality_verdict"] = self.quality_verdict.to_dict()
        if self.delivery_blocked:
            result["delivery_blocked"] = True
            result["delivery_block_reason"] = self.delivery_block_reason
        return result


# ── Main finalize function ──────────────────────────────────

def finalize_output(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    pipeline_summary: dict = None,
    run_id: str = None,
    deliver_to: str = None,
    clean_text: bool = True,
    quality_verdict: Optional[QualityVerdict] = None,
) -> FinalizeResult:
    """Finalize the translation output: cleanup, report, package.

    Args:
        input_path: Source text file
        output_path: Translation output file
        config: Translation config
        pipeline_summary: Optional pipeline summary dict
        run_id: Optional run ID (auto-generated if None)
        deliver_to: Optional delivery directory path
        clean_text: Whether to clean up the translation text
        quality_verdict: Optional quality gate verdict. If present and
            the output is not deliverable, delivery is blocked.

    Returns:
        FinalizeResult with all paths and metadata.
    """
    result = FinalizeResult()
    result.quality_verdict = quality_verdict

    # Step 1: Clean up translation text
    if clean_text and Path(output_path).exists():
        with open(output_path, 'r', encoding='utf-8') as f:
            raw_text = f.read()
        glossary_text, _annotate_terms = load_glossary(str(Path(__file__).parent / 'glossary.json'))
        glossary = {}
        glossary_path = Path(__file__).parent / 'glossary.json'
        if glossary_path.exists():
            with open(glossary_path, 'r', encoding='utf-8') as gf:
                glossary = json.load(gf)
        cleaned = cleanup_translation_text(raw_text, glossary=glossary)
        if cleaned != raw_text:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(cleaned)
        result.cleaned_output = output_path

    # Step 2: Create/reuse run directory
    rm = RunManager()
    if run_id:
        rm.resume_run(run_id)
    else:
        run_id = rm.create_run(input_path)
    result.run_id = run_id
    result.run_dir = str(rm.run_dir)

    # Step 3: Copy main artifacts to run directory
    output_stem = Path(output_path).name
    run_output = rm.get_run_path(output_stem)
    shutil.copy2(output_path, run_output)

    # Copy sidecar metadata if exists
    meta_path = Path(output_path).with_suffix(Path(output_path).suffix + '.chunks.json')
    if meta_path.exists():
        shutil.copy2(meta_path, rm.get_run_path(meta_path.name))

    # Step 4: Generate reports
    report_dir = str(rm.run_dir)
    report_paths = generate_all_reports(
        input_path, output_path, config,
        pipeline_summary=pipeline_summary,
        report_dir=report_dir,
    )
    result.report_paths = report_paths

    # Step 5: Write manifest (now includes quality status)
    manifest = _build_manifest(
        input_path, output_path, run_id,
        report_paths, rm.run_dir,
        quality_verdict=quality_verdict,
    )
    manifest_path = rm.get_run_path("manifest.json")
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    result.manifest = manifest

    # Determine finalize status based on quality verdict
    finalize_status = "finalized"
    extra_meta = {
        "finalized_at": datetime.now().isoformat(timespec='seconds'),
        "report_paths": report_paths,
        "manifest_path": str(manifest_path),
    }
    if quality_verdict:
        extra_meta["quality_status"] = quality_verdict.status
        if quality_verdict.blocking_reasons:
            extra_meta["blocking_reasons"] = quality_verdict.blocking_reasons

    rm.update_status(finalize_status, extra_meta)

    # Step 6: Optional delivery — blocked if quality gate fails
    if deliver_to:
        if quality_verdict and quality_verdict.is_blocked:
            result.delivery_blocked = True
            result.delivery_block_reason = (
                f"Delivery blocked: output status is '{quality_verdict.status}', "
                f"not 'deliverable'. "
                f"Reasons: {'; '.join(quality_verdict.blocking_reasons)}"
            )
            print(f"  DELIVERY BLOCKED: {result.delivery_block_reason}")
        else:
            delivery_dir = Path(deliver_to)
            delivery_dir.mkdir(parents=True, exist_ok=True)

            # Copy all run directory contents to delivery
            for item in rm.run_dir.iterdir():
                dest = delivery_dir / item.name
                if item.is_file():
                    shutil.copy2(item, dest)

            result.delivery_dir = str(delivery_dir)

    return result


def _build_manifest(
    input_path: str,
    output_path: str,
    run_id: str,
    report_paths: Dict[str, str],
    run_dir: Path,
    quality_verdict: Optional[QualityVerdict] = None,
) -> dict:
    """Build a manifest listing all files with checksums."""
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec='seconds'),
        "input": {
            "path": str(Path(input_path).resolve()),
            "sha256": _file_sha256(input_path),
        },
        "output": {
            "path": str(Path(output_path).resolve()),
            "sha256": _file_sha256(output_path),
        },
        "reports": {},
        "files": [],
    }

    # Quality gate section
    if quality_verdict:
        manifest["quality"] = {
            "status": quality_verdict.status,
            "is_deliverable": quality_verdict.is_deliverable,
            "blocking_reasons": quality_verdict.blocking_reasons,
            "warnings": quality_verdict.warnings,
            "residue_score": quality_verdict.residue_score,
            "chunks_with_residue": quality_verdict.chunks_with_residue,
        }
    else:
        manifest["quality"] = {
            "status": STATUS_DRAFT,
            "is_deliverable": False,
            "blocking_reasons": ["Quality gate not evaluated"],
            "warnings": [],
        }

    for fmt, path in report_paths.items():
        manifest["reports"][fmt] = {
            "path": path,
            "sha256": _file_sha256(path),
        }

    # List all files in run directory
    if run_dir and run_dir.exists():
        for item in sorted(run_dir.iterdir()):
            if item.is_file():
                manifest["files"].append({
                    "name": item.name,
                    "size": item.stat().st_size,
                    "sha256": _file_sha256(str(item)),
                })

    return manifest


def _file_sha256(path: str) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return ""


# ── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Finalize translation output: cleanup, report, package"
    )
    parser.add_argument("output", help="Translation output file path")
    parser.add_argument("--input", required=True, help="Source input file path")
    parser.add_argument("--config", default="config.yaml", help="Config file")
    parser.add_argument("--deliver-to", default=None,
                        help="Copy finalized package to this directory")
    parser.add_argument("--run-id", default=None,
                        help="Existing run ID to finalize into")
    parser.add_argument("--no-clean", action="store_true",
                        help="Skip text cleanup")
    parser.add_argument("--summary-json", default=None,
                        help="Path to pipeline summary JSON file")

    args = parser.parse_args()

    if not Path(args.output).exists():
        print(f"Error: Output file not found: {args.output}")
        exit(1)
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}")
        exit(1)

    try:
        config = TranslationConfig(args.config)
    except Exception as e:
        print(f"Error loading config: {e}")
        exit(1)

    pipeline_summary = None
    if args.summary_json and Path(args.summary_json).exists():
        with open(args.summary_json, 'r', encoding='utf-8') as f:
            pipeline_summary = json.load(f)

    print(f"Finalizing: {args.output}")
    result = finalize_output(
        input_path=args.input,
        output_path=args.output,
        config=config,
        pipeline_summary=pipeline_summary,
        run_id=args.run_id,
        deliver_to=args.deliver_to,
        clean_text=not args.no_clean,
    )

    print(f"Run ID: {result.run_id}")
    print(f"Run dir: {result.run_dir}")
    print(f"Reports:")
    for fmt, path in result.report_paths.items():
        print(f"  {fmt}: {path}")
    if result.delivery_dir:
        print(f"Delivered to: {result.delivery_dir}")
    print("Finalization complete.")


if __name__ == "__main__":
    main()
