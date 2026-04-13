from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Dict, List, Optional

from quality_gate import detect_source_residue_in_chunk
from translator_v3.core.models import TranslationUnit, V3Config
from translator_v3.orchestrate import run_pipeline


DEFAULT_MANIFEST = Path(__file__).resolve().parent / "fixtures" / "quality" / "cases.json"
DEFAULT_REPORT = Path(__file__).resolve().parent / "fixtures" / "quality" / "baseline_results.active_v3.codex_local.json"
OMISSION_SIGNALS = {"adequacy_review_failed", "coverage_drop", "severe_undertranslation"}


@dataclass
class QualityFixtureCase:
    case_id: str
    label: str
    input_path: str
    source_lang: str
    failure_mode: str
    expected_detection_signals: List[str] = field(default_factory=list)
    min_char_ratio: Optional[float] = None
    origin_run: str = ""
    origin_unit_id: str = ""
    notes: str = ""


@dataclass
class QualityFixtureResult:
    case_id: str
    label: str
    failure_mode: str
    source_lang: str
    status: str
    elapsed_ms: int
    artifact_dir: str
    translated_output_path: str
    total_units: int
    blocked_units: int
    issue_types: List[str]
    expected_detection_signals: List[str]
    expected_signals_detected: bool
    residue_issue_count: int
    adequacy_issue_count: int
    posthoc_residue_hits: int
    low_char_ratio: bool
    min_char_ratio: Optional[float]
    source_chars: int
    translated_chars: int
    char_ratio: float
    translated_preview: str
    max_body_attempt_count: int
    body_unit_outcomes: List[Dict[str, Any]]
    origin_run: str
    origin_unit_id: str
    notes: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["char_ratio"] = round(self.char_ratio, 4)
        return payload


def load_quality_cases(manifest_path: str | Path | None = None) -> List[QualityFixtureCase]:
    path = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST
    payload = json.loads(path.read_text(encoding="utf-8"))
    base_dir = path.parent
    cases: list[QualityFixtureCase] = []
    for item in payload["cases"]:
        input_path = item["input_path"]
        if not Path(input_path).is_absolute():
            input_path = str((base_dir / input_path).resolve())
        cases.append(
            QualityFixtureCase(
                case_id=item["case_id"],
                label=item["label"],
                input_path=input_path,
                source_lang=item.get("source_lang", "ja"),
                failure_mode=item["failure_mode"],
                expected_detection_signals=list(item.get("expected_detection_signals", [])),
                min_char_ratio=item.get("min_char_ratio"),
                origin_run=item.get("origin_run", ""),
                origin_unit_id=item.get("origin_unit_id", ""),
                notes=item.get("notes", ""),
            )
        )
    return cases


def _load_units(artifact_dir: str) -> List[TranslationUnit]:
    path = Path(artifact_dir) / "translation_units.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [TranslationUnit.from_dict(item) for item in payload]


def _strip_segment_wrapper(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith("[[SEGMENT ") and not line.startswith("[[END SEGMENT ")]
    return "\n".join(lines).strip()


def _body_units(units: List[TranslationUnit]) -> List[TranslationUnit]:
    return [unit for unit in units if unit.unit_class == "body_paragraph"]


def _flatten_issue_types(units: List[TranslationUnit]) -> List[str]:
    issue_types: List[str] = []
    for unit in units:
        for issue in unit.validation_errors:
            issue_type = issue.get("issue_type")
            if issue_type:
                issue_types.append(issue_type)
    return issue_types


def _posthoc_residue_hits(units: List[TranslationUnit]) -> int:
    hits = 0
    for unit in units:
        cleaned = _strip_segment_wrapper(unit.translated_text)
        result = detect_source_residue_in_chunk(
            cleaned,
            source_lang=unit.source_lang,
            source_text=unit.source_text,
        )
        if result.get("severity") != "none":
            hits += 1
    return hits


def _translated_preview(units: List[TranslationUnit], limit: int = 160) -> str:
    cleaned = " ".join(_strip_segment_wrapper(unit.translated_text) for unit in units).strip()
    return cleaned[:limit]


async def _run_case(
    case: QualityFixtureCase,
    *,
    backend: str,
    mock_backend_fixture_path: Optional[str],
    model: str,
    reasoning_effort: str,
    artifact_root: str,
    output_root: str,
) -> QualityFixtureResult:
    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{case.case_id}_ko.txt"
    config = V3Config(
        source_lang=case.source_lang,
        target_lang="ko",
        run_root=artifact_root,
        backend_name=backend,
        mock_backend_fixture_path=mock_backend_fixture_path,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    started = perf_counter()
    result = await run_pipeline(case.input_path, str(output_path), config)
    elapsed_ms = int((perf_counter() - started) * 1000)
    units = _body_units(_load_units(result.artifact_dir))
    issue_types = _flatten_issue_types(units)
    source_chars = sum(len(unit.source_text.strip()) for unit in units)
    translated_chars = sum(len(_strip_segment_wrapper(unit.translated_text)) for unit in units)
    char_ratio = translated_chars / max(1, source_chars)
    residue_issue_count = sum(1 for issue in issue_types if issue == "source_residue")
    adequacy_issue_count = sum(1 for issue in issue_types if issue in OMISSION_SIGNALS)
    posthoc_hits = _posthoc_residue_hits(units)
    low_char_ratio = case.min_char_ratio is not None and char_ratio < case.min_char_ratio
    expected_signals_detected = any(issue in set(case.expected_detection_signals) for issue in issue_types)
    body_unit_outcomes = [
        {
            "unit_id": unit.unit_id,
            "status": unit.status,
            "attempt_count": unit.attempt_count,
            "compression_retry_telemetry": unit.telemetry.get("compression_repair_retry", {}),
        }
        for unit in units
    ]

    return QualityFixtureResult(
        case_id=case.case_id,
        label=case.label,
        failure_mode=case.failure_mode,
        source_lang=case.source_lang,
        status=result.status,
        elapsed_ms=elapsed_ms,
        artifact_dir=result.artifact_dir,
        translated_output_path=result.translated_output_path,
        total_units=result.total_units,
        blocked_units=result.blocked_units,
        issue_types=sorted(set(issue_types)),
        expected_detection_signals=case.expected_detection_signals,
        expected_signals_detected=expected_signals_detected,
        residue_issue_count=residue_issue_count,
        adequacy_issue_count=adequacy_issue_count,
        posthoc_residue_hits=posthoc_hits,
        low_char_ratio=low_char_ratio,
        min_char_ratio=case.min_char_ratio,
        source_chars=source_chars,
        translated_chars=translated_chars,
        char_ratio=char_ratio,
        translated_preview=_translated_preview(units),
        max_body_attempt_count=max((unit.attempt_count for unit in units), default=0),
        body_unit_outcomes=body_unit_outcomes,
        origin_run=case.origin_run,
        origin_unit_id=case.origin_unit_id,
        notes=case.notes,
    )


async def _run_all(
    cases: List[QualityFixtureCase],
    *,
    backend: str,
    mock_backend_fixture_path: Optional[str],
    model: str,
    reasoning_effort: str,
    artifact_root: str,
    output_root: str,
) -> List[QualityFixtureResult]:
    results: list[QualityFixtureResult] = []
    for case in cases:
        results.append(
            await _run_case(
                case,
                backend=backend,
                mock_backend_fixture_path=mock_backend_fixture_path,
                model=model,
                reasoning_effort=reasoning_effort,
                artifact_root=artifact_root,
                output_root=output_root,
            )
        )
    return results


def _category_detected(result: QualityFixtureResult) -> bool:
    if result.failure_mode == "source_language_residue":
        return result.residue_issue_count > 0 or result.posthoc_residue_hits > 0
    if result.failure_mode == "silent_omission":
        return result.adequacy_issue_count > 0
    if result.failure_mode == "over_compression":
        return result.adequacy_issue_count > 0 or result.low_char_ratio
    return result.expected_signals_detected


def build_report(
    *,
    manifest_path: str,
    backend: str,
    mock_backend_fixture_path: Optional[str],
    model: str,
    reasoning_effort: str,
    artifact_root: str,
    output_root: str,
    results: List[QualityFixtureResult],
) -> Dict[str, Any]:
    detected_cases = sum(1 for result in results if _category_detected(result))
    blocked_cases = sum(1 for result in results if result.status == "BLOCKED")
    summary = {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_path": str(Path(manifest_path).resolve()),
        "backend": backend,
        "mock_backend_fixture_path": str(Path(mock_backend_fixture_path).resolve()) if mock_backend_fixture_path else "",
        "model": model,
        "reasoning_effort": reasoning_effort,
        "artifact_root": str(Path(artifact_root).resolve()),
        "output_root": str(Path(output_root).resolve()),
        "case_count": len(results),
        "blocked_case_count": blocked_cases,
        "detected_case_count": detected_cases,
        "detection_rate": round(detected_cases / max(1, len(results)), 4),
        "average_char_ratio": round(mean(result.char_ratio for result in results), 4),
        "minimum_char_ratio": round(min((result.char_ratio for result in results), default=0.0), 4),
        "by_failure_mode": {},
    }
    for failure_mode in sorted({result.failure_mode for result in results}):
        subset = [result for result in results if result.failure_mode == failure_mode]
        summary["by_failure_mode"][failure_mode] = {
            "case_count": len(subset),
            "blocked_case_count": sum(1 for result in subset if result.status == "BLOCKED"),
            "detected_case_count": sum(1 for result in subset if _category_detected(result)),
            "detection_rate": round(
                sum(1 for result in subset if _category_detected(result)) / max(1, len(subset)),
                4,
            ),
            "average_char_ratio": round(mean(result.char_ratio for result in subset), 4),
        }
    return {
        "summary": summary,
        "results": [result.to_dict() for result in results],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run curated quality fixtures through translator_v3.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--case-id", action="append", default=[], help="Run only the named fixture case_id. Repeatable.")
    parser.add_argument("--backend", choices=["provider_api", "codex_local", "echo", "fixture_script"], default="codex_local")
    parser.add_argument("--mock-backend-fixture", default="", help="JSON script used only with --backend fixture_script.")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--artifact-root", default="runs_v3/quality_fixtures")
    parser.add_argument("--output-root", default="output/quality_fixtures")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT))
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cases = load_quality_cases(args.manifest)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.case_id in selected]
        if not cases:
            raise SystemExit(f"No quality fixture matched case ids: {sorted(selected)}")
    results = asyncio.run(
        _run_all(
            cases,
            backend=args.backend,
            mock_backend_fixture_path=args.mock_backend_fixture,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            artifact_root=args.artifact_root,
            output_root=args.output_root,
        )
    )
    report = build_report(
        manifest_path=args.manifest,
        backend=args.backend,
        mock_backend_fixture_path=args.mock_backend_fixture,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        artifact_root=args.artifact_root,
        output_root=args.output_root,
        results=results,
    )
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
