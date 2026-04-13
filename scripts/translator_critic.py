#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


LEGACY_PATHS = (
    "translator_v2/",
    "quality_gate.py",
    "review.py",
    "retry_engine.py",
    "translate.py",
)

ALLOWED_PREFIXES = (
    "translator_v3/",
    "docs/",
    "scripts/",
    "skills/",
    "AGENTS.md",
    "README.md",
    "test_translator_v3.py",
    "test_readability_gate.py",
    "test_job_runner.py",
)

CLAIM_WORDS = ("improved", "improvement", "resolved", "fixed", "deliverable")
EVIDENCE_WORDS = ("artifact", "artifacts", "passed", "blocked", "attempt", "runs", "distribution", "result")
CAVEAT_WORDS = ("attempt 1", "not reproduced", "mixed", "unstable", "human judgment", "still", "caveat")


def _collect_changed_paths(paths: list[str]) -> list[str]:
    if paths:
        return sorted({path for path in paths if path})

    proc = subprocess.run(
        ["git", "status", "--short"],
        text=True,
        capture_output=True,
        check=True,
    )
    changed: set[str] = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        payload = line[3:]
        if " -> " in payload:
            _, payload = payload.split(" -> ", 1)
        changed.add(payload.strip())
    return sorted(changed)


def _matches_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in prefixes)


def _status_line(status: str, label: str, detail: str) -> str:
    return f"{status:<4} {label}: {detail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic translator critic for active-path review hygiene.")
    parser.add_argument("--plan", required=True, help="Plan file to inspect for validation and caveat language.")
    parser.add_argument("--paths", nargs="*", default=[], help="Optional explicit changed paths. Defaults to git status.")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(_status_line("FAIL", "plan", f"missing plan file: {plan_path}"))
        return 1

    changed_paths = _collect_changed_paths(args.paths)
    plan_text = plan_path.read_text(encoding="utf-8")
    lowered_plan = plan_text.lower()

    failures = 0
    warnings = 0

    print("translator_critic")
    print(f"plan: {plan_path}")
    print(f"changed_paths: {len(changed_paths)}")
    for path in changed_paths:
        print(f"  - {path}")

    legacy_touches = [path for path in changed_paths if _matches_any(path, LEGACY_PATHS)]
    if legacy_touches:
        print(_status_line("FAIL", "active_path_only", f"legacy paths touched: {', '.join(legacy_touches)}"))
        failures += 1
    else:
        print(_status_line("PASS", "active_path_only", "no legacy path edits detected"))

    out_of_scope = [path for path in changed_paths if not _matches_any(path, ALLOWED_PREFIXES)]
    if out_of_scope:
        print(_status_line("WARN", "scope_drift", f"paths outside the usual critic scope: {', '.join(out_of_scope)}"))
        warnings += 1
    else:
        print(_status_line("PASS", "scope_drift", "changed files stay within the usual repo-local critic surface"))

    has_command = "python3 " in plan_text or "git diff --check" in plan_text
    has_result = any(token in lowered_plan for token in ("passed", "blocked", "deliverable", "accepted", "failed"))
    if has_command and has_result:
        print(_status_line("PASS", "validation", "plan records concrete commands and observed results"))
    else:
        print(_status_line("WARN", "validation", "plan is missing clear validation commands or concrete outcomes"))
        warnings += 1

    has_claim = any(word in lowered_plan for word in CLAIM_WORDS)
    has_evidence = any(word in lowered_plan for word in EVIDENCE_WORDS)
    has_caveat = any(word in lowered_plan for word in CAVEAT_WORDS)
    if has_claim and not has_evidence:
        print(_status_line("WARN", "proof", "plan makes improvement-style claims without obvious evidence language"))
        warnings += 1
    else:
        print(_status_line("PASS", "proof", "claim language is accompanied by evidence-oriented language"))

    if has_caveat:
        print(_status_line("PASS", "reproducibility", "plan includes caveat or instability language"))
    else:
        print(_status_line("WARN", "reproducibility", "plan does not explicitly mention caveats, mixed runs, or remaining human judgment"))
        warnings += 1

    if "human judgment" in lowered_plan:
        print(_status_line("PASS", "human_judgment", "plan explicitly reserves decisions that still need human review"))
    else:
        print(_status_line("WARN", "human_judgment", "plan does not explicitly say what still requires human judgment"))
        warnings += 1

    print(f"summary: {failures} fail, {warnings} warn")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
