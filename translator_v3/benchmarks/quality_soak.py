from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from translator_v3.benchmarks.quality_eval import DEFAULT_MANIFEST


RUN_STATUSES = {
    "completed_deliverable",
    "completed_blocked",
    "timed_out",
    "stalled_before_adequacy_review",
}


@dataclass
class SoakRunResult:
    run_index: int
    status: str
    elapsed_ms: int
    timed_out: bool
    process_exit_code: Optional[int]
    report_path: str
    artifact_root: str
    artifact_dir: str
    output_root: str
    final_pipeline_status: str
    telemetry_phase: str
    adequacy_review_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_artifact_dir(artifact_root: Path) -> Optional[Path]:
    candidates = [path for path in artifact_root.iterdir() if path.is_dir()] if artifact_root.exists() else []
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _classify_completed_status(status: str) -> str:
    if status == "DELIVERABLE":
        return "completed_deliverable"
    if status == "BLOCKED":
        return "completed_blocked"
    raise ValueError(f"unsupported pipeline status: {status}")


def classify_run_result(report_path: Path, artifact_root: Path, timed_out: bool, process_exit_code: Optional[int]) -> SoakRunResult:
    payload_status = ""
    telemetry_phase = ""
    adequacy_review_status = ""
    artifact_dir = ""

    if report_path.exists():
        report = _read_json(report_path)
        result = report["results"][0]
        payload_status = result["status"]
        artifact_dir = result["artifact_dir"]
    else:
        latest_artifact = _latest_artifact_dir(artifact_root)
        if latest_artifact is not None:
            artifact_dir = str(latest_artifact)
            run_result_path = latest_artifact / "run_result.json"
            telemetry_path = latest_artifact / "telemetry.json"
            if run_result_path.exists():
                payload_status = _read_json(run_result_path).get("status", "")
            if telemetry_path.exists():
                telemetry = _read_json(telemetry_path)
                telemetry_phase = telemetry.get("phase", "")
                adequacy_review_status = telemetry.get("adequacy_review_status", "")

    if payload_status:
        status = _classify_completed_status(payload_status)
    elif timed_out and telemetry_phase == "translating" and adequacy_review_status == "not_run":
        status = "stalled_before_adequacy_review"
    else:
        status = "timed_out"

    return SoakRunResult(
        run_index=0,
        status=status,
        elapsed_ms=0,
        timed_out=timed_out,
        process_exit_code=process_exit_code,
        report_path=str(report_path),
        artifact_root=str(artifact_root),
        artifact_dir=artifact_dir,
        output_root="",
        final_pipeline_status=payload_status,
        telemetry_phase=telemetry_phase,
        adequacy_review_status=adequacy_review_status,
    )


def run_soak(
    *,
    manifest: str,
    case_id: str,
    backend: str,
    mock_backend_fixture: str,
    model: str,
    reasoning_effort: str,
    run_root: str,
    run_count: int,
    timeout_seconds: int,
) -> Dict[str, Any]:
    root = Path(run_root).resolve()
    artifact_parent = root / "artifacts"
    output_parent = root / "output"
    report_parent = root / "reports"
    status_log_path = root / "status.jsonl"
    summary_path = root / "summary.json"
    for path in (artifact_parent, output_parent, report_parent):
        path.mkdir(parents=True, exist_ok=True)

    run_records: List[SoakRunResult] = []
    status_log_path.write_text("", encoding="utf-8")

    for run_index in range(1, run_count + 1):
        report_path = report_parent / f"run{run_index:02d}.json"
        artifact_root = artifact_parent / f"run{run_index:02d}"
        output_root = output_parent / f"run{run_index:02d}"
        artifact_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "translator_v3.benchmarks.quality_eval",
            "--manifest",
            manifest,
            "--case-id",
            case_id,
            "--backend",
            backend,
            "--model",
            model,
            "--reasoning-effort",
            reasoning_effort,
            "--artifact-root",
            str(artifact_root),
            "--output-root",
            str(output_root),
            "--report-path",
            str(report_path),
        ]
        if mock_backend_fixture:
            cmd.extend(["--mock-backend-fixture", mock_backend_fixture])

        started = perf_counter()
        timed_out = False
        process_exit_code: Optional[int] = None
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(Path(__file__).resolve().parents[2]),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            process_exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True

        record = classify_run_result(report_path, artifact_root, timed_out, process_exit_code)
        record.run_index = run_index
        record.elapsed_ms = int((perf_counter() - started) * 1000)
        record.output_root = str(output_root)
        run_records.append(record)
        with status_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        print(
            f"run {run_index:02d}: status={record.status} "
            f"pipeline={record.final_pipeline_status or 'n/a'} "
            f"elapsed_ms={record.elapsed_ms}"
        )

    counts = {status: sum(1 for record in run_records if record.status == status) for status in sorted(RUN_STATUSES)}
    summary = {
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "manifest_path": str(Path(manifest).resolve()),
        "case_id": case_id,
        "backend": backend,
        "mock_backend_fixture_path": str(Path(mock_backend_fixture).resolve()) if mock_backend_fixture else "",
        "model": model,
        "reasoning_effort": reasoning_effort,
        "run_root": str(root),
        "run_count": run_count,
        "timeout_seconds": timeout_seconds,
        "status_counts": counts,
        "status_log_path": str(status_log_path),
        "report_root": str(report_parent),
        "results": [record.to_dict() for record in run_records],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeated quality fixture probes with per-run timeout and status logging.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--backend", choices=["provider_api", "codex_local", "echo", "fixture_script"], default="codex_local")
    parser.add_argument("--mock-backend-fixture", default="")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--run-count", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    run_soak(
        manifest=args.manifest,
        case_id=args.case_id,
        backend=args.backend,
        mock_backend_fixture=args.mock_backend_fixture,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        run_root=args.run_root,
        run_count=args.run_count,
        timeout_seconds=args.timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
