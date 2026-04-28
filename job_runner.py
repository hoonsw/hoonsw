#!/usr/bin/env python3
"""
Reliable foreground job runner for the translation workbench.

Purpose:
- Wrap translator_v3.cli in a stateful runner
- Persist live job state to runs/<job>/job_status.json
- Persist final summary to runs/<job>/job_result.json
- Make long-running work observable even when chat-side completion reporting is flaky

This does NOT guarantee chat delivery by itself.
It guarantees a machine-readable status/result trail that OpenClaw can query.
"""

import argparse
import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from synology_share import SynologyShareError, SynologyShareConfig, create_share_link

BASE_DIR = Path(__file__).parent
RUNS_DIR = BASE_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)
DEFAULT_DELIVERY_DIR = Path("/mnt/z/home/01. 책/translator-delivery")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_transport_text_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8")
    destination.write_text(text, encoding="utf-8-sig")
    return str(destination)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_safe_artifact_path(path_str: str, allowed_roots: list[Path]) -> bool:
    if not path_str:
        return False
    path = Path(path_str)
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(_is_within(resolved, root) for root in allowed_roots)


def detect_phase(line: str) -> Optional[str]:
    line = line.strip()
    if not line:
        return None
    phase_map = {
        "[START]": "start",
        "[PHASE]": "phase",
        "[PROGRESS]": "progress",
        "[PROBLEMS]": "problems",
        "[COMPLETE]": "complete",
        "[FAILURE]": "failure",
    }
    for prefix, phase in phase_map.items():
        if line.startswith(prefix):
            return phase
    if "Quality status:" in line:
        return "quality_gate"
    if "Finalized:" in line:
        return "finalize"
    return None


def extract_v3_result(stdout_log: Path) -> Optional[dict]:
    if not stdout_log.exists():
        return None
    try:
        text = stdout_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    candidate_starts = [idx for idx, char in enumerate(text) if char == "{"][-20:]
    for start in reversed(candidate_starts):
        snippet = text[start:].strip()
        try:
            payload = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and {"run_id", "status", "artifact_dir"} <= payload.keys():
            return payload
    return None


def apply_v3_result_state(state: dict, result: dict, v3_result: dict) -> None:
    result["v3_result"] = v3_result
    result["artifact_dir"] = v3_result.get("artifact_dir")
    result["preview_path"] = v3_result.get("preview_path")
    result["review_bundle_path"] = v3_result.get("review_bundle_path")
    result["translated_output_path"] = v3_result.get("translated_output_path")
    v3_status = v3_result.get("status")
    result["quality_status"] = "deliverable" if v3_status == "DELIVERABLE" else "blocked"
    state["quality_status"] = result["quality_status"]
    state["phase"] = v3_status.lower() if isinstance(v3_status, str) else state.get("phase", "running")
    if v3_status == "DELIVERABLE":
        state["state"] = "succeeded"
        result["state"] = "succeeded"
    elif v3_status == "BLOCKED":
        state["state"] = "blocked"
        result["state"] = "blocked"


def summarize_failure(stdout_log: Path) -> Optional[str]:
    if not stdout_log.exists():
        return None
    try:
        lines = stdout_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    fatal_prefixes = ("Fatal error:", "[FAILURE]", "Error:")
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(fatal_prefixes):
            return stripped
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def latest_v3_artifact_dir(v3_run_root: Path) -> Optional[Path]:
    if not v3_run_root.exists():
        return None
    candidates = [item for item in v3_run_root.iterdir() if item.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for candidate in candidates:
        if (candidate / "telemetry.json").exists():
            return candidate
    return candidates[0]


def extract_v3_live_state(v3_run_root: Path) -> dict[str, str]:
    artifact_dir = latest_v3_artifact_dir(v3_run_root)
    if artifact_dir is None:
        return {}
    telemetry_path = artifact_dir / "telemetry.json"
    if not telemetry_path.exists():
        return {"artifact_dir": str(artifact_dir)}
    try:
        telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    except Exception:
        return {"artifact_dir": str(artifact_dir)}
    result: dict[str, str] = {
        "artifact_dir": str(artifact_dir),
        "phase": telemetry.get("phase") or "running",
        "preview_path": telemetry.get("preview_path") or str(artifact_dir / "translated.preview.txt"),
    }
    blocked = telemetry.get("blocked_blocks")
    unresolved = telemetry.get("unresolved_blocks")
    result["v3_progress"] = f"blocked={blocked}, unresolved={unresolved}"
    if telemetry.get("phase") in {"deliverable", "blocked"}:
        result["quality_status"] = telemetry.get("phase")
    return result


def build_pipeline_command(args: argparse.Namespace, extra: list[str], run_dir: Path) -> list[str]:
    v3_run_root = run_dir / "v3_artifacts"
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "translator_v3.cli",
        "run",
        args.input,
        "-o",
        args.output,
        "--backend",
        args.v3_backend,
        "--model",
        args.codex_model or "gpt-5.4",
        "--reasoning-effort",
        args.codex_reasoning_effort or "high",
        "--run-root",
        str(v3_run_root),
    ]
    if args.preset:
        cmd += ["--preset", args.preset]
    if args.v3_backend == "provider_api":
        cmd += ["--provider", "openai"]
    cmd += extra
    return cmd


def build_attachment_path(output_path: str, run_dir: Path) -> Optional[str]:
    resolved = Path(output_path).resolve()
    if not resolved.exists():
        return None
    suffix = resolved.suffix.lower()
    if suffix == ".txt":
        attachment_copy = run_dir / resolved.name
        return write_transport_text_copy(resolved, attachment_copy)
    if suffix in {".pdf", ".docx", ".pptx", ".xlsx"}:
        return str(resolved)
    return None


def resolve_delivery_dir(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return str(Path(explicit).resolve())
    env_value = os.environ.get("TRANSLATOR_DEFAULT_DELIVER_TO")
    if env_value:
        return str(Path(env_value).resolve())
    if DEFAULT_DELIVERY_DIR.exists():
        return str(DEFAULT_DELIVERY_DIR.resolve())
    return None


def deliver_job_artifacts(
    *,
    run_id: str,
    run_dir: Path,
    output_path: str,
    delivery_root: str,
    extra_files: list[str],
    extra_dirs: list[str],
    allowed_roots: list[Path],
) -> dict[str, str]:
    delivery_root_path = Path(delivery_root).resolve()
    package_dir = delivery_root_path / run_id
    package_dir.mkdir(parents=True, exist_ok=True)

    copied_names: set[str] = set()

    def copy_if_file(path_str: str) -> Optional[str]:
        if not _is_safe_artifact_path(path_str, allowed_roots):
            return None
        path = Path(path_str)
        if not path.exists() or not path.is_file():
            return None
        dest = package_dir / path.name
        if path.suffix.lower() == ".txt":
            write_transport_text_copy(path, dest)
        else:
            try:
                shutil.copy2(path, dest)
            except OSError:
                shutil.copyfile(path, dest)
        copied_names.add(dest.name)
        return str(dest)

    def copy_if_dir(path_str: str) -> Optional[str]:
        if not _is_safe_artifact_path(path_str, allowed_roots):
            return None
        path = Path(path_str)
        if not path.exists() or not path.is_dir():
            return None
        dest = package_dir / path.name
        shutil.copytree(path, dest, dirs_exist_ok=True)
        return str(dest)

    for item in run_dir.iterdir():
        if item.is_file():
            copy_if_file(str(item))

    delivered_output = copy_if_file(output_path)
    sidecar = Path(output_path).with_suffix(Path(output_path).suffix + ".chunks.json")
    if sidecar.exists():
        copy_if_file(str(sidecar))

    for candidate in extra_files:
        if candidate:
            copy_if_file(candidate)

    copied_dirs: list[str] = []
    for candidate in extra_dirs:
        if candidate:
            copied = copy_if_dir(candidate)
            if copied:
                copied_dirs.append(copied)

    result = {
        "delivery_dir": str(package_dir),
        "delivery_root": str(delivery_root_path),
    }
    if delivered_output:
        result["delivered_output"] = delivered_output
    if copied_dirs:
        result["delivered_dirs"] = copied_dirs
    return result


def maybe_create_synology_share(result: dict[str, str]) -> Optional[dict[str, str]]:
    config = SynologyShareConfig.from_env()
    if config is None:
        return None
    share_target_mode = config.share_target
    if share_target_mode == "delivery_dir":
        local_target = result.get("delivery_dir")
    else:
        local_target = result.get("delivered_output") or result.get("delivery_dir")
    if not local_target:
        return None
    share = create_share_link(local_target, config=config)
    return {
        "share_target": share.local_path,
        "share_dsm_path": share.dsm_path,
        "share_url": share.url,
        "share_id": share.link_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stable job runner for translator_v3")
    parser.add_argument("input", help="Input file path")
    parser.add_argument("-o", "--output", required=True, help="Output file path")
    parser.add_argument("--v3-backend", choices=("provider_api", "codex_local"), default="codex_local")
    parser.add_argument("--codex-model", default=None, help="Model override for codex exec worker")
    parser.add_argument("--codex-reasoning-effort", default=None, help="Reasoning effort override for codex exec worker")
    parser.add_argument("--preset", default=None, help="Preset name")
    parser.add_argument("--deliver-to", default=None)
    parser.add_argument("--label", default=None, help="Optional run label")
    args, extra = parser.parse_known_args()

    input_name = Path(args.input).name
    label = args.label or input_name
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name(label)}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    status_path = run_dir / "job_status.json"
    result_path = run_dir / "job_result.json"
    stdout_log = run_dir / "job_stdout.log"

    cmd = build_pipeline_command(args, extra, run_dir)

    state = {
        "run_id": run_id,
        "input": str(Path(args.input).resolve()),
        "output": str(Path(args.output).resolve()),
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "state": "running",
        "phase": "starting",
        "quality_status": None,
        "command": cmd,
        "stdout_log": str(stdout_log),
    }
    write_json(status_path, state)

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    v3_run_root = run_dir / "v3_artifacts"

    with stdout_log.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

        last_update = time.time()
        assert proc.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        while True:
            events = selector.select(timeout=1.0)
            if not events:
                if v3_run_root is not None:
                    live = extract_v3_live_state(v3_run_root)
                    changed = False
                    for key, value in live.items():
                        if state.get(key) != value:
                            state[key] = value
                            changed = True
                    if changed or (time.time() - last_update >= 30):
                        state["updated_at"] = now_iso()
                        write_json(status_path, state)
                        last_update = time.time()
                if proc.poll() is not None:
                    break
                continue

            raw = proc.stdout.readline()
            if raw == "":
                if proc.poll() is not None:
                    break
                continue
            line = raw.rstrip("\n")
            print(line)
            logf.write(raw)
            logf.flush()

            phase = detect_phase(line)
            changed = False
            if phase:
                state["phase"] = phase
                changed = True
            if changed or (time.time() - last_update >= 30):
                state["updated_at"] = now_iso()
                write_json(status_path, state)
                last_update = time.time()
        selector.unregister(proc.stdout)
        code = proc.wait()

    state["updated_at"] = now_iso()
    state["finished_at"] = now_iso()
    state["exit_code"] = code
    state["state"] = "succeeded" if code == 0 else "failed"
    write_json(status_path, state)

    result = {
        "run_id": run_id,
        "input": state["input"],
        "output": state["output"],
        "state": state["state"],
        "exit_code": code,
        "phase": state.get("phase"),
        "quality_status": state.get("quality_status"),
        "status_file": str(status_path),
        "stdout_log": str(stdout_log),
        "finished_at": state["finished_at"],
    }
    v3_result = None
    v3_result = extract_v3_result(stdout_log)
    if v3_result:
        apply_v3_result_state(state, result, v3_result)
        write_json(status_path, state)
    final_exit_code = code
    if result.get("state") == "blocked":
        final_exit_code = 3
        state["exit_code"] = final_exit_code
        result["exit_code"] = final_exit_code
        write_json(status_path, state)
    failure_summary = summarize_failure(stdout_log) if state["state"] == "failed" else None
    if v3_result and v3_result.get("status") == "BLOCKED":
        blocked = v3_result.get("blocked_units")
        total = v3_result.get("total_units")
        failure_summary = f"translator_v3 blocked run: blocked_units={blocked}/{total}"
    if failure_summary:
        result["failure_summary"] = failure_summary
    if state["state"] == "succeeded":
        try:
            attachment_path = build_attachment_path(state["output"], run_dir)
        except Exception as exc:
            result["attachment_error"] = str(exc)
        else:
            if attachment_path:
                result["attachment_path"] = attachment_path
        delivery_dir = resolve_delivery_dir(args.deliver_to)
        if delivery_dir:
            extra_files: list[str] = []
            extra_dirs: list[str] = []
            if v3_result:
                extra_files.extend(
                    [
                        result.get("preview_path") or "",
                        result.get("review_bundle_path") or "",
                        result.get("translated_output_path") or "",
                    ]
                )
                extra_dirs.append(result.get("artifact_dir") or "")
            allowed_roots = [
                run_dir.resolve(),
                BASE_DIR.resolve(),
                Path(state["output"]).resolve().parent,
            ]
            try:
                delivery = deliver_job_artifacts(
                    run_id=run_id,
                    run_dir=run_dir,
                    output_path=state["output"],
                    delivery_root=delivery_dir,
                    extra_files=extra_files,
                    extra_dirs=extra_dirs,
                    allowed_roots=allowed_roots,
                )
            except Exception as exc:
                result["delivery_error"] = str(exc)
            else:
                result.update(delivery)
                try:
                    share_data = maybe_create_synology_share(result)
                except SynologyShareError as exc:
                    result["share_error"] = str(exc)
                except Exception as exc:
                    result["share_error"] = str(exc)
                else:
                    if share_data:
                        result.update(share_data)
    write_json(result_path, result)

    print(f"[JOB] run_id={run_id}")
    print(f"[JOB] state={result['state']}")
    print(f"[JOB] quality_status={result.get('quality_status')}")
    print(f"[JOB] status_file={status_path}")
    print(f"[JOB] result_file={result_path}")
    if result.get("failure_summary"):
        print(f"[JOB] failure_summary={result['failure_summary']}")
    if result.get("attachment_path"):
        print(f"[JOB] attachment_path={result['attachment_path']}")
    if result.get("delivery_dir"):
        print(f"[JOB] delivery_dir={result['delivery_dir']}")
    if result.get("delivered_output"):
        print(f"[JOB] delivered_output={result['delivered_output']}")
    if result.get("share_url"):
        print(f"[JOB] share_url={result['share_url']}")
    if result.get("share_target"):
        print(f"[JOB] share_target={result['share_target']}")
    if result.get("delivery_error"):
        print(f"[JOB] delivery_error={result['delivery_error']}")
    if result.get("share_error"):
        print(f"[JOB] share_error={result['share_error']}")
    return final_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
