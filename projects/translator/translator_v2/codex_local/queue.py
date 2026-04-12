from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def task_artifact_dir(output_path: str, run_id: str) -> Path:
    base = Path(output_path).with_suffix(Path(output_path).suffix + ".v2.run")
    return base / run_id


@dataclass
class CodexTaskRecord:
    task_id: str
    run_id: str
    generation_mode: str
    backend_label: str
    work_type: str
    chapter_id: str
    segment_ids: List[str]
    attempt: int
    task_epoch: int
    created_at: str
    context: Dict
    segments: List[Dict]
    instructions: Dict
    repair_reason: Optional[str]
    system_prompt: str
    user_prompt: str
    status: str = "pending"
    worker_id: str = ""
    claim_id: str = ""
    claimed_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "CodexTaskRecord":
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass
class CodexResultRecord:
    task_id: str
    run_id: str
    status: str
    generation_mode: str
    backend_label: str
    worker_id: str
    task_epoch: int
    claim_id: str
    completed_at: str
    raw_output_path: str
    notes: str = ""
    result_schema_version: str = "segment_wrappers_v1"
    provider_name: str = ""
    model_name: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "CodexResultRecord":
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


class CodexTaskQueue:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.base = self.run_dir / "codex_queue"
        self.pending = self.base / "pending"
        self.in_progress = self.base / "in_progress"
        self.completed = self.base / "completed"
        self.failed = self.base / "failed"
        self.abandoned = self.base / "abandoned"
        self.quarantined = self.base / "quarantined"
        for directory in (
            self.run_dir,
            self.base,
            self.pending,
            self.in_progress,
            self.completed,
            self.failed,
            self.abandoned,
            self.quarantined,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def _task_filename(self, task_id: str, task_epoch: int) -> str:
        return f"{_safe_name(task_id)}.e{task_epoch}.json"

    def _heartbeat_filename(self, task_id: str, task_epoch: int) -> str:
        return f"{_safe_name(task_id)}.e{task_epoch}.heartbeat.json"

    def _result_filename(self, task_id: str, task_epoch: int, claim_id: str) -> str:
        return f"{_safe_name(task_id)}.e{task_epoch}.{_safe_name(claim_id)}.result.json"

    def _raw_output_filename(self, task_id: str, task_epoch: int, claim_id: str) -> str:
        return f"{_safe_name(task_id)}.e{task_epoch}.{_safe_name(claim_id)}.txt"

    def _load_json(self, path: Path) -> Dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_task_path(self, state_dir: Path, task_id: str, task_epoch: int) -> Optional[Path]:
        path = state_dir / self._task_filename(task_id, task_epoch)
        return path if path.exists() else None

    def enqueue(self, task: CodexTaskRecord) -> Path:
        task.status = "pending"
        path = self.pending / self._task_filename(task.task_id, task.task_epoch)
        self._write_json(path, task.to_dict())
        return path

    def list_pending(self) -> List[CodexTaskRecord]:
        items = []
        for path in sorted(self.pending.glob("*.json")):
            items.append(CodexTaskRecord.from_dict(self._load_json(path)))
        return items

    def claim_next(self, worker_id: str) -> Optional[CodexTaskRecord]:
        for path in sorted(self.pending.glob("*.json")):
            target = self.in_progress / path.name
            try:
                path.rename(target)
            except FileNotFoundError:
                continue
            payload = self._load_json(target)
            payload["status"] = "in_progress"
            payload["worker_id"] = worker_id
            payload["claim_id"] = uuid4().hex
            payload["claimed_at"] = now_iso()
            self._write_json(target, payload)
            heartbeat = {
                "worker_id": worker_id,
                "claim_id": payload["claim_id"],
                "updated_at": payload["claimed_at"],
                "phase": "claimed",
            }
            self._write_json(
                self.in_progress / self._heartbeat_filename(payload["task_id"], payload["task_epoch"]),
                heartbeat,
            )
            return CodexTaskRecord.from_dict(payload)
        return None

    def load_in_progress(self, task_id: str, task_epoch: int) -> Optional[CodexTaskRecord]:
        path = self._find_task_path(self.in_progress, task_id, task_epoch)
        if path is None:
            return None
        return CodexTaskRecord.from_dict(self._load_json(path))

    def heartbeat(self, task_id: str, task_epoch: int, claim_id: str, worker_id: str, phase: str) -> None:
        heartbeat_path = self.in_progress / self._heartbeat_filename(task_id, task_epoch)
        payload = {
            "worker_id": worker_id,
            "claim_id": claim_id,
            "updated_at": now_iso(),
            "phase": phase,
        }
        self._write_json(heartbeat_path, payload)

    def read_heartbeat(self, task_id: str, task_epoch: int) -> Optional[Dict]:
        heartbeat_path = self.in_progress / self._heartbeat_filename(task_id, task_epoch)
        if not heartbeat_path.exists():
            return None
        return self._load_json(heartbeat_path)

    def complete_claim(
        self,
        *,
        task_id: str,
        task_epoch: int,
        claim_id: str,
        worker_id: str,
        raw_output_text: str,
        notes: str = "",
        backend_label: str = "codex_local",
        provider_name: str = "",
        model_name: str = "",
    ) -> CodexResultRecord:
        in_progress_path = self._find_task_path(self.in_progress, task_id, task_epoch)
        abandoned_path = self._find_task_path(self.abandoned, task_id, task_epoch)
        source_path = in_progress_path or abandoned_path
        if source_path is None:
            raise FileNotFoundError(f"task not found for {task_id}@{task_epoch}")
        task_payload = self._load_json(source_path)
        if task_payload.get("claim_id") != claim_id:
            raise ValueError("claim_id does not match current in-progress claim")
        if in_progress_path is not None:
            task_payload["status"] = "completed"
            completed_task_path = self.completed / in_progress_path.name
            in_progress_path.rename(completed_task_path)
            self._write_json(completed_task_path, task_payload)
            heartbeat_path = self.in_progress / self._heartbeat_filename(task_id, task_epoch)
            if heartbeat_path.exists():
                heartbeat_path.unlink()

        raw_output_path = self.completed / self._raw_output_filename(task_id, task_epoch, claim_id)
        raw_output_path.write_text(raw_output_text, encoding="utf-8")
        result = CodexResultRecord(
            task_id=task_id,
            run_id=task_payload["run_id"],
            status="completed",
            generation_mode=task_payload.get("generation_mode", "codex_local"),
            backend_label=backend_label,
            worker_id=worker_id,
            task_epoch=task_epoch,
            claim_id=claim_id,
            completed_at=now_iso(),
            raw_output_path=str(raw_output_path),
            notes=notes,
            provider_name=provider_name,
            model_name=model_name,
        )
        self._write_json(
            self.completed / self._result_filename(task_id, task_epoch, claim_id),
            result.to_dict(),
        )
        return result

    def fail_claim(
        self,
        *,
        task_id: str,
        task_epoch: int,
        claim_id: str,
        worker_id: str,
        error: str,
    ) -> None:
        in_progress_path = self._find_task_path(self.in_progress, task_id, task_epoch)
        if in_progress_path is None:
            raise FileNotFoundError(f"in-progress task not found for {task_id}@{task_epoch}")
        task_payload = self._load_json(in_progress_path)
        if task_payload.get("claim_id") != claim_id:
            raise ValueError("claim_id does not match current in-progress claim")
        task_payload["status"] = "failed"
        task_payload["worker_id"] = worker_id
        task_payload["error"] = error
        failed_path = self.failed / in_progress_path.name
        in_progress_path.rename(failed_path)
        self._write_json(failed_path, task_payload)
        heartbeat_path = self.in_progress / self._heartbeat_filename(task_id, task_epoch)
        if heartbeat_path.exists():
            heartbeat_path.unlink()

    def mark_stale(self, task_id: str, task_epoch: int, reason: str) -> CodexTaskRecord:
        in_progress_path = self._find_task_path(self.in_progress, task_id, task_epoch)
        if in_progress_path is None:
            raise FileNotFoundError(f"in-progress task not found for {task_id}@{task_epoch}")
        payload = self._load_json(in_progress_path)
        payload["status"] = "abandoned"
        payload["abandoned_reason"] = reason
        payload["abandoned_at"] = now_iso()
        abandoned_path = self.abandoned / in_progress_path.name
        in_progress_path.rename(abandoned_path)
        self._write_json(abandoned_path, payload)
        heartbeat_path = self.in_progress / self._heartbeat_filename(task_id, task_epoch)
        if heartbeat_path.exists():
            heartbeat_path.unlink()
        return CodexTaskRecord.from_dict(payload)

    def requeue_with_new_epoch(self, task: CodexTaskRecord, repair_reason: Optional[str] = None) -> CodexTaskRecord:
        next_task = CodexTaskRecord(
            task_id=task.task_id,
            run_id=task.run_id,
            generation_mode=task.generation_mode,
            backend_label=task.backend_label,
            work_type=task.work_type,
            chapter_id=task.chapter_id,
            segment_ids=list(task.segment_ids),
            attempt=task.attempt + 1,
            task_epoch=task.task_epoch + 1,
            created_at=now_iso(),
            context=dict(task.context),
            segments=list(task.segments),
            instructions=dict(task.instructions),
            repair_reason=repair_reason if repair_reason is not None else task.repair_reason,
            system_prompt=task.system_prompt,
            user_prompt=task.user_prompt,
            status="pending",
        )
        self.enqueue(next_task)
        return next_task

    def list_completed_results(self, task_id: str, task_epoch: int) -> List[CodexResultRecord]:
        pattern = f"{_safe_name(task_id)}.e{task_epoch}.*.result.json"
        items = []
        for path in sorted(self.completed.glob(pattern)):
            items.append(CodexResultRecord.from_dict(self._load_json(path)))
        return items

    def list_all_completed_results(self, task_id: str) -> List[CodexResultRecord]:
        pattern = f"{_safe_name(task_id)}.e*.result.json"
        items = []
        for path in sorted(self.completed.glob(pattern)):
            items.append(CodexResultRecord.from_dict(self._load_json(path)))
        return items

    def load_failed(self, task_id: str, task_epoch: int) -> Optional[Dict]:
        path = self._find_task_path(self.failed, task_id, task_epoch)
        if path is None:
            return None
        return self._load_json(path)

    def quarantine_result(self, result: CodexResultRecord, reason: str) -> None:
        result_path = self.completed / self._result_filename(
            result.task_id,
            result.task_epoch,
            result.claim_id,
        )
        raw_output_path = Path(result.raw_output_path)
        quarantine_result_path = self.quarantined / result_path.name
        quarantine_raw_output_path = self.quarantined / raw_output_path.name
        if result_path.exists():
            payload = self._load_json(result_path)
            payload["status"] = "quarantined"
            payload["quarantine_reason"] = reason
            result_path.rename(quarantine_result_path)
            self._write_json(quarantine_result_path, payload)
        if raw_output_path.exists():
            raw_output_path.rename(quarantine_raw_output_path)
