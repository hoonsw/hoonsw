#!/usr/bin/env python3
"""Small regression tests for job_runner command building."""

import argparse
import os
import tempfile
from pathlib import Path

from job_runner import (
    apply_v3_result_state,
    build_pipeline_command,
    build_attachment_path,
    deliver_job_artifacts,
    extract_v2_artifact,
    resolve_delivery_dir,
)


def make_args(**overrides):
    base = {
        "input": "input/sample.txt",
        "output": "/tmp/sample_ko.txt",
        "v2": True,
        "v3": False,
        "legacy": False,
        "v3_backend": "codex_local",
        "generation_mode": "codex_local",
        "codex_worker_mode": "external",
        "codex_model": "gpt-5.4",
        "codex_reasoning_effort": "high",
        "codex_worker_id": "codex-test",
        "preset": None,
        "ai_review": False,
        "report": False,
        "finalize": False,
        "notify": False,
        "parallel": 0,
        "deliver_to": None,
        "label": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_pipeline_command_excludes_worker_only_flags():
    args = make_args()
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = build_pipeline_command(args, ["--dry-run"], Path(tmpdir))
    joined = " ".join(cmd)
    assert "--codex-model" not in joined
    assert "--codex-reasoning-effort" not in joined
    assert "--v2" in cmd
    assert "--generation-mode" in cmd
    assert "--codex-worker-mode" in cmd


def test_pipeline_command_omits_legacy_only_flags_for_v2():
    args = make_args(
        preset="en_nonfiction",
        ai_review=True,
        report=True,
        finalize=True,
        notify=True,
        parallel=2,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = build_pipeline_command(args, [], Path(tmpdir))
    joined = " ".join(cmd)
    assert "--preset" not in joined
    assert "--ai-review" not in joined
    assert "--report" not in joined
    assert "--finalize" not in joined
    assert "--notify" not in joined
    assert "--parallel" not in joined


def test_pipeline_command_keeps_legacy_flags_when_not_v2():
    args = make_args(
        v2=False,
        legacy=True,
        preset="en_nonfiction",
        ai_review=True,
        report=True,
        finalize=True,
        notify=True,
        parallel=2,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = build_pipeline_command(args, [], Path(tmpdir))
    joined = " ".join(cmd)
    assert "--preset en_nonfiction" in joined
    assert "--ai-review" in joined
    assert "--report" in joined
    assert "--finalize" in joined
    assert "--notify" in joined
    assert "--parallel 2" in joined


def test_pipeline_command_passes_preset_to_v3():
    args = make_args(
        v2=False,
        v3=True,
        legacy=False,
        generation_mode=None,
        codex_worker_mode=None,
        preset="en_nonfiction",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = build_pipeline_command(args, [], Path(tmpdir))
    joined = " ".join(cmd)
    assert "translator_v3.cli" in joined
    assert "--preset en_nonfiction" in joined


def test_extract_v2_artifact():
    assert extract_v2_artifact("  Ledger: /tmp/a.json") == ("ledger_path", "/tmp/a.json")
    assert extract_v2_artifact("  Report: /tmp/r.json") == ("report_path", "/tmp/r.json")
    assert extract_v2_artifact("  Blocked workfile: /tmp/b.txt") == ("blocked_workfile", "/tmp/b.txt")
    assert extract_v2_artifact("  Delivered to: /tmp/delivery") == ("delivery_dir", "/tmp/delivery")
    assert extract_v2_artifact("  DELIVERY BLOCKED: nope") == ("delivery_block_reason", "nope")
    assert extract_v2_artifact("noise") == (None, None)


def test_resolve_delivery_dir_prefers_env():
    old = os.environ.get("TRANSLATOR_DEFAULT_DELIVER_TO")
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["TRANSLATOR_DEFAULT_DELIVER_TO"] = tmpdir
        assert resolve_delivery_dir(None) == str(Path(tmpdir).resolve())
    if old is None:
        os.environ.pop("TRANSLATOR_DEFAULT_DELIVER_TO", None)
    else:
        os.environ["TRANSLATOR_DEFAULT_DELIVER_TO"] = old


def test_deliver_job_artifacts_copies_output_and_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        run_dir = tmp / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        output = tmp / "sample_ko.txt"
        sidecar = tmp / "sample_ko.txt.chunks.json"
        ledger = tmp / "sample_ko.txt.v2.ledger.json"
        (run_dir / "job_result.json").write_text("{}", encoding="utf-8")
        output.write_text("hello", encoding="utf-8")
        sidecar.write_text("[]", encoding="utf-8")
        ledger.write_text("{}", encoding="utf-8")

        delivered = deliver_job_artifacts(
            run_id="run-1",
            run_dir=run_dir,
            output_path=str(output),
            delivery_root=str(tmp / "delivery"),
            extra_files=[str(ledger)],
            extra_dirs=[],
            allowed_roots=[tmp],
        )
        package_dir = Path(delivered["delivery_dir"])
        assert package_dir.exists()
        assert (package_dir / "job_result.json").exists()
        assert (package_dir / "sample_ko.txt").exists()
        assert (package_dir / "sample_ko.txt.chunks.json").exists()
        assert (package_dir / "sample_ko.txt.v2.ledger.json").exists()
        assert (package_dir / "sample_ko.txt").read_bytes().startswith(b"\xef\xbb\xbf")


def test_deliver_job_artifacts_rejects_unsafe_extra_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        run_dir = tmp / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        output = tmp / "sample_ko.txt"
        safe = tmp / "safe.json"
        unsafe = Path("/etc/hosts")
        (run_dir / "job_result.json").write_text("{}", encoding="utf-8")
        output.write_text("hello", encoding="utf-8")
        safe.write_text("{}", encoding="utf-8")

        delivered = deliver_job_artifacts(
            run_id="run-1",
            run_dir=run_dir,
            output_path=str(output),
            delivery_root=str(tmp / "delivery"),
            extra_files=[str(safe), str(unsafe)],
            extra_dirs=[],
            allowed_roots=[tmp],
        )
        package_dir = Path(delivered["delivery_dir"])
        assert (package_dir / "safe.json").exists()
        assert not (package_dir / unsafe.name).exists()


def test_build_attachment_path_creates_bom_copy_for_txt():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        run_dir = tmp / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        output = tmp / "sample_ko.txt"
        output.write_text("한글 테스트\n", encoding="utf-8")

        attachment = Path(build_attachment_path(str(output), run_dir))
        assert attachment.exists()
        assert attachment.name == "sample_ko.txt"
        assert attachment.read_bytes().startswith(b"\xef\xbb\xbf")
        assert attachment.read_text(encoding="utf-8-sig") == "한글 테스트\n"


def test_apply_v3_result_state_marks_blocked_runs_as_blocked():
    state = {"state": "failed", "phase": "running", "quality_status": None}
    result = {"state": "failed", "quality_status": None}
    v3_result = {
        "status": "BLOCKED",
        "artifact_dir": "/tmp/artifacts",
        "preview_path": "/tmp/preview.txt",
        "review_bundle_path": "/tmp/review.json",
        "translated_output_path": "/tmp/translated.blocked.txt",
    }

    apply_v3_result_state(state, result, v3_result)

    assert state["state"] == "blocked"
    assert result["state"] == "blocked"
    assert state["quality_status"] == "blocked"
    assert result["quality_status"] == "blocked"


def main():
    test_pipeline_command_excludes_worker_only_flags()
    test_pipeline_command_omits_legacy_only_flags_for_v2()
    test_pipeline_command_keeps_legacy_flags_when_not_v2()
    test_pipeline_command_passes_preset_to_v3()
    test_extract_v2_artifact()
    test_resolve_delivery_dir_prefers_env()
    test_deliver_job_artifacts_copies_output_and_logs()
    test_deliver_job_artifacts_rejects_unsafe_extra_files()
    test_build_attachment_path_creates_bom_copy_for_txt()
    test_apply_v3_result_state_marks_blocked_runs_as_blocked()
    print("job_runner tests passed.")


if __name__ == "__main__":
    main()
