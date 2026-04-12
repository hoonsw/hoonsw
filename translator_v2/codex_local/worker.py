from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from translator_v2.adapters.providers import build_provider
from translator_v2.codex_local.queue import CodexTaskQueue
from translator_v2.core.models import V2Config


class ProviderBackedCodexWorker:
    """Test and bootstrap worker that processes queue tasks via a provider.

    This is primarily for validation and local bootstrapping. The production
    `codex_local` design still treats the queue boundary as authoritative.
    """

    def __init__(self, provider, worker_id: str = "codex-local-inline"):
        self.provider = provider
        self.worker_id = worker_id

    async def process_next(self, queue: CodexTaskQueue) -> bool:
        task = queue.claim_next(self.worker_id)
        if task is None:
            return False
        queue.heartbeat(task.task_id, task.task_epoch, task.claim_id, self.worker_id, "generating")
        response = await self.provider.generate(task.system_prompt, task.user_prompt)
        queue.complete_claim(
            task_id=task.task_id,
            task_epoch=task.task_epoch,
            claim_id=task.claim_id,
            worker_id=self.worker_id,
            raw_output_text=response.text,
            backend_label=task.backend_label,
            provider_name=response.provider,
            model_name=response.model,
        )
        return True


def render_codex_exec_prompt(task: dict) -> str:
    return (
        "You are processing a translator_v2 codex_local queue task.\n\n"
        "[SYSTEM INSTRUCTIONS]\n"
        f"{task['system_prompt'].strip()}\n\n"
        "[TASK INPUT]\n"
        f"{task['user_prompt'].strip()}\n\n"
        "[OUTPUT REQUIREMENT]\n"
        "Return only the wrapped translation output required by the task. "
        "Do not add any commentary outside the wrappers."
    )


def resolve_codex_executable() -> str:
    env_override = os.environ.get("CODEX_BIN")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.exists():
            return str(candidate)

    discovered = shutil.which("codex")
    if discovered:
        return discovered

    home = Path.home()
    direct_candidates = [
        home / ".local/bin/codex",
        home / ".npm-global/bin/codex",
    ]
    for candidate in direct_candidates:
        if candidate.exists():
            return str(candidate.resolve())

    glob_patterns = [
        home / ".vscode-server/extensions/openai.chatgpt-*/bin/linux-x86_64/codex",
        home / ".nvm/versions/node/*/bin/codex",
    ]
    glob_matches: list[Path] = []
    for pattern in glob_patterns:
        glob_matches.extend(Path(match) for match in glob.glob(str(pattern)))
    existing = [path.resolve() for path in glob_matches if path.exists()]
    if existing:
        latest = max(existing, key=lambda path: path.stat().st_mtime)
        return str(latest)

    return "codex"


def build_codex_exec_command(
    *,
    model: str | None,
    reasoning_effort: str | None,
    cd: str | None,
    output_file: str,
) -> list[str]:
    cmd = [resolve_codex_executable(), "exec", "--skip-git-repo-check", "--output-last-message", output_file]
    if cd:
        cmd += ["--cd", cd]
    if model:
        cmd += ["--model", model]
    if reasoning_effort:
        cmd += ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    cmd += ["--ephemeral"]
    cmd += ["--sandbox", "workspace-write"]
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
    cmd += ["--add-dir", str(codex_home)]
    return cmd


class CodexCliWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        cd: str | None = None,
    ):
        self.worker_id = worker_id
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.cd = cd

    def process_next(self, queue: CodexTaskQueue) -> bool:
        task = queue.claim_next(self.worker_id)
        if task is None:
            return False

        output_dir = Path(queue.run_dir) / "codex_queue" / "worker_exec"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{task.task_id}.e{task.task_epoch}.{task.claim_id}.last.txt"
        exec_log_file = output_dir / f"{task.task_id}.e{task.task_epoch}.{task.claim_id}.exec.log"
        prompt = render_codex_exec_prompt(task.to_dict())
        cmd = build_codex_exec_command(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            cd=self.cd,
            output_file=str(output_file),
        )
        queue.heartbeat(task.task_id, task.task_epoch, task.claim_id, self.worker_id, "codex_exec")
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as exc:
            queue.fail_claim(
                task_id=task.task_id,
                task_epoch=task.task_epoch,
                claim_id=task.claim_id,
                worker_id=self.worker_id,
                error=f"codex_exec_exception: {exc}",
            )
            return True

        combined_output = "\n".join(
            part.strip()
            for part in (proc.stdout or "", proc.stderr or "")
            if part and part.strip()
        ).strip()
        if combined_output:
            exec_log_file.write_text(combined_output, encoding="utf-8")

        if proc.returncode != 0:
            queue.fail_claim(
                task_id=task.task_id,
                task_epoch=task.task_epoch,
                claim_id=task.claim_id,
                worker_id=self.worker_id,
                error=f"codex_exit_{proc.returncode}: {combined_output[-2000:] if combined_output else 'no output captured'}",
            )
            return True

        if not output_file.exists():
            queue.fail_claim(
                task_id=task.task_id,
                task_epoch=task.task_epoch,
                claim_id=task.claim_id,
                worker_id=self.worker_id,
                error="codex_exec_missing_output_file",
            )
            return True

        queue.complete_claim(
            task_id=task.task_id,
            task_epoch=task.task_epoch,
            claim_id=task.claim_id,
            worker_id=self.worker_id,
            raw_output_text=output_file.read_text(encoding="utf-8"),
            notes=(proc.stderr or "").strip()[:1000],
            backend_label="codex_local",
            provider_name="codex",
            model_name=self.model or "",
        )
        return True


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run_once(args: argparse.Namespace) -> int:
    queue = CodexTaskQueue(args.run_dir)
    config = V2Config(
        provider_name=args.provider,
        model=args.model,
    )
    provider = build_provider(config)
    worker = ProviderBackedCodexWorker(provider, worker_id=args.worker_id)
    processed = await worker.process_next(queue)
    if not processed:
        print("no pending tasks")
        return 1
    print("processed one task")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex-local queue worker utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim = subparsers.add_parser("claim", help="Claim the next pending task and print it as JSON")
    claim.add_argument("--run-dir", required=True)
    claim.add_argument("--worker-id", required=True)

    complete = subparsers.add_parser("complete", help="Complete a claimed task using a raw output file")
    complete.add_argument("--run-dir", required=True)
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--task-epoch", type=int, required=True)
    complete.add_argument("--claim-id", required=True)
    complete.add_argument("--worker-id", required=True)
    complete.add_argument("--output-file", required=True)
    complete.add_argument("--backend-label", default="codex_local")
    complete.add_argument("--provider-name", default="")
    complete.add_argument("--model-name", default="")
    complete.add_argument("--notes", default="")

    fail = subparsers.add_parser("fail", help="Mark a claimed task as failed")
    fail.add_argument("--run-dir", required=True)
    fail.add_argument("--task-id", required=True)
    fail.add_argument("--task-epoch", type=int, required=True)
    fail.add_argument("--claim-id", required=True)
    fail.add_argument("--worker-id", required=True)
    fail.add_argument("--error", required=True)

    once = subparsers.add_parser("once", help="Process one task via a direct provider for testing/bootstrap")
    once.add_argument("--run-dir", required=True)
    once.add_argument("--worker-id", default="codex-local-inline")
    once.add_argument("--provider", default="openai")
    once.add_argument("--model", default="gpt-5.2-chat-latest")

    exec_once = subparsers.add_parser("exec-once", help="Process one task via codex exec")
    exec_once.add_argument("--run-dir", required=True)
    exec_once.add_argument("--worker-id", default="codex-cli-worker")
    exec_once.add_argument("--model", default=None)
    exec_once.add_argument("--reasoning-effort", default=None)
    exec_once.add_argument("--cd", default=None)

    exec_loop = subparsers.add_parser("exec-loop", help="Poll and process codex_local tasks via codex exec")
    exec_loop.add_argument("--run-dir", required=True)
    exec_loop.add_argument("--worker-id", default="codex-cli-worker")
    exec_loop.add_argument("--model", default=None)
    exec_loop.add_argument("--reasoning-effort", default=None)
    exec_loop.add_argument("--cd", default=None)
    exec_loop.add_argument("--poll-interval", type=float, default=0.5)
    exec_loop.add_argument("--max-idle-seconds", type=float, default=15.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    queue = CodexTaskQueue(args.run_dir)

    if args.command == "claim":
        task = queue.claim_next(args.worker_id)
        if task is None:
            print("no pending tasks")
            return 1
        _print_json(task.to_dict())
        return 0

    if args.command == "complete":
        raw_output = Path(args.output_file).read_text(encoding="utf-8")
        result = queue.complete_claim(
            task_id=args.task_id,
            task_epoch=args.task_epoch,
            claim_id=args.claim_id,
            worker_id=args.worker_id,
            raw_output_text=raw_output,
            notes=args.notes,
            backend_label=args.backend_label,
            provider_name=args.provider_name,
            model_name=args.model_name,
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "fail":
        queue.fail_claim(
            task_id=args.task_id,
            task_epoch=args.task_epoch,
            claim_id=args.claim_id,
            worker_id=args.worker_id,
            error=args.error,
        )
        print("task failed")
        return 0

    if args.command == "once":
        return asyncio.run(_run_once(args))

    if args.command == "exec-once":
        worker = CodexCliWorker(
            worker_id=args.worker_id,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            cd=args.cd,
        )
        processed = worker.process_next(queue)
        if not processed:
            print("no pending tasks")
            return 1
        print("processed one task")
        return 0

    if args.command == "exec-loop":
        worker = CodexCliWorker(
            worker_id=args.worker_id,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            cd=args.cd,
        )
        idle_started = time.time()
        while True:
            processed = worker.process_next(queue)
            if processed:
                idle_started = time.time()
                continue
            if time.time() - idle_started >= args.max_idle_seconds:
                print("idle timeout reached")
                return 0
            time.sleep(args.poll_interval)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
