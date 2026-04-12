#!/usr/bin/env python3
"""
Subagent-assisted review/rework architecture hooks.

Phase 3 feature: provides clean interfaces for OpenClaw or other
orchestration tools to dispatch review and rework tasks to subagents.

Architecture:
- The main pipeline identifies problem chunks and writes task files
- Subagents pick up task files, perform review/rework, write results
- The main pipeline reads results and merges them back

This module does NOT directly spawn subagents — it provides the protocol
and file-based interface for any orchestrator (OpenClaw, custom scripts).

Task file format (JSON):
    {
        "task_id": "review-chunk-5",
        "task_type": "review" | "rework",
        "chunk_num": 5,
        "source_text": "...",
        "translated_text": "...",
        "problems": ["under_translate", "structure_mismatch"],
        "context": "...",
        "config": { ... },
        "status": "pending" | "in_progress" | "completed" | "failed"
    }

Result file format (JSON):
    {
        "task_id": "review-chunk-5",
        "status": "completed",
        "result": "..." | { ... },
        "agent_id": "optional-agent-identifier",
        "completed_at": "ISO timestamp"
    }
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Callable

from translate import TranslationConfig


# ── Task definitions ────────────────────────────────────────

class SubagentTask:
    """A task to be dispatched to a subagent."""

    def __init__(
        self,
        task_id: str,
        task_type: str,
        chunk_num: int,
        source_text: str,
        translated_text: str = "",
        problems: list = None,
        context: str = "",
        config: dict = None,
        hints: str = "",
    ):
        self.task_id = task_id
        self.task_type = task_type
        self.chunk_num = chunk_num
        self.source_text = source_text
        self.translated_text = translated_text
        self.problems = problems or []
        self.context = context
        self.config = config or {}
        self.hints = hints
        self.status = "pending"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "chunk_num": self.chunk_num,
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "problems": self.problems,
            "context": self.context,
            "config": self.config,
            "hints": self.hints,
            "status": self.status,
            "created_at": datetime.now().isoformat(timespec='seconds'),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SubagentTask':
        task = cls(
            task_id=data["task_id"],
            task_type=data["task_type"],
            chunk_num=data["chunk_num"],
            source_text=data.get("source_text", ""),
            translated_text=data.get("translated_text", ""),
            problems=data.get("problems", []),
            context=data.get("context", ""),
            config=data.get("config", {}),
            hints=data.get("hints", ""),
        )
        task.status = data.get("status", "pending")
        return task


class SubagentResult:
    """Result from a subagent."""

    def __init__(
        self,
        task_id: str,
        status: str = "completed",
        result: str = "",
        review_notes: str = "",
        improved_translation: str = "",
        agent_id: str = "",
    ):
        self.task_id = task_id
        self.status = status
        self.result = result
        self.review_notes = review_notes
        self.improved_translation = improved_translation
        self.agent_id = agent_id
        self.completed_at = datetime.now().isoformat(timespec='seconds')

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "result": self.result,
            "review_notes": self.review_notes,
            "improved_translation": self.improved_translation,
            "agent_id": self.agent_id,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SubagentResult':
        r = cls(
            task_id=data["task_id"],
            status=data.get("status", "completed"),
            result=data.get("result", ""),
            review_notes=data.get("review_notes", ""),
            improved_translation=data.get("improved_translation", ""),
            agent_id=data.get("agent_id", ""),
        )
        r.completed_at = data.get("completed_at", "")
        return r


# ── Task queue (file-based) ────────────────────────────────

class SubagentTaskQueue:
    """File-based task queue for subagent dispatch.

    Directory layout:
        tasks_dir/
        ├── pending/
        │   ├── review-chunk-5.json
        │   └── rework-chunk-12.json
        ├── in_progress/
        ├── completed/
        │   └── review-chunk-5.json
        └── failed/
    """

    def __init__(self, tasks_dir: str = None):
        if tasks_dir is None:
            tasks_dir = str(Path(__file__).parent / "subagent_tasks")
        self.base = Path(tasks_dir)
        for subdir in ("pending", "in_progress", "completed", "failed"):
            (self.base / subdir).mkdir(parents=True, exist_ok=True)

    def enqueue(self, task: SubagentTask) -> Path:
        """Write a task to the pending directory."""
        task.status = "pending"
        path = self.base / "pending" / f"{task.task_id}.json"
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def enqueue_batch(self, tasks: List[SubagentTask]) -> List[Path]:
        """Write multiple tasks to the pending directory."""
        return [self.enqueue(t) for t in tasks]

    def claim(self, task_id: str) -> Optional[SubagentTask]:
        """Move a task from pending to in_progress. Returns None if not found."""
        pending = self.base / "pending" / f"{task_id}.json"
        if not pending.exists():
            return None
        in_progress = self.base / "in_progress" / f"{task_id}.json"
        pending.rename(in_progress)
        with open(in_progress, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['status'] = 'in_progress'
        with open(in_progress, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return SubagentTask.from_dict(data)

    def complete(self, task_id: str, result: SubagentResult) -> None:
        """Move a task to completed and write the result."""
        in_progress = self.base / "in_progress" / f"{task_id}.json"
        completed = self.base / "completed" / f"{task_id}.json"
        if in_progress.exists():
            in_progress.rename(completed)
        # Write result alongside task
        result_path = self.base / "completed" / f"{task_id}.result.json"
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

    def fail(self, task_id: str, error: str = "") -> None:
        """Move a task to failed."""
        in_progress = self.base / "in_progress" / f"{task_id}.json"
        failed = self.base / "failed" / f"{task_id}.json"
        if in_progress.exists():
            with open(in_progress, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['status'] = 'failed'
            data['error'] = error
            in_progress.rename(failed)
            with open(failed, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def list_pending(self) -> List[SubagentTask]:
        """List all pending tasks."""
        tasks = []
        for path in sorted((self.base / "pending").glob("*.json")):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            tasks.append(SubagentTask.from_dict(data))
        return tasks

    def list_completed(self) -> List[SubagentResult]:
        """List all completed results."""
        results = []
        for path in sorted((self.base / "completed").glob("*.result.json")):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            results.append(SubagentResult.from_dict(data))
        return results

    def get_result(self, task_id: str) -> Optional[SubagentResult]:
        """Get the result for a completed task."""
        path = self.base / "completed" / f"{task_id}.result.json"
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return SubagentResult.from_dict(data)

    def status_summary(self) -> dict:
        """Get counts of tasks in each state."""
        return {
            "pending": len(list((self.base / "pending").glob("*.json"))),
            "in_progress": len(list((self.base / "in_progress").glob("*.json"))),
            "completed": len(list((self.base / "completed").glob("*.result.json"))),
            "failed": len(list((self.base / "failed").glob("*.json"))),
        }

    def wait_for_completion(
        self,
        task_ids: List[str],
        timeout: float = 3600,
        poll_interval: float = 5.0,
    ) -> Dict[str, Optional[SubagentResult]]:
        """Wait for a set of tasks to complete (blocking).

        Args:
            task_ids: Task IDs to wait for
            timeout: Maximum wait time in seconds
            poll_interval: Seconds between checks

        Returns:
            Dict mapping task_id to SubagentResult (or None if timed out/failed)
        """
        start = time.time()
        remaining = set(task_ids)
        results = {}

        while remaining and (time.time() - start) < timeout:
            for task_id in list(remaining):
                result = self.get_result(task_id)
                if result:
                    results[task_id] = result
                    remaining.discard(task_id)
                # Check if failed
                elif (self.base / "failed" / f"{task_id}.json").exists():
                    results[task_id] = None
                    remaining.discard(task_id)

            if remaining:
                time.sleep(poll_interval)

        # Mark remaining as timed out
        for task_id in remaining:
            results[task_id] = None

        return results


# ── Helper functions ────────────────────────────────────────

def create_review_tasks(
    source_chunks: list,
    translated_chunks: list,
    problem_chunk_nums: List[int],
    config: TranslationConfig,
    run_id: str = "",
) -> List[SubagentTask]:
    """Create review tasks for problem chunks.

    Args:
        source_chunks: List of (context, source_text) tuples
        translated_chunks: List of translated text strings
        problem_chunk_nums: 1-indexed chunk numbers to review
        config: Translation config
        run_id: Optional run identifier for task naming

    Returns:
        List of SubagentTask objects ready for dispatch.
    """
    prefix = f"{run_id}-" if run_id else ""
    tasks = []

    for chunk_num in problem_chunk_nums:
        idx = chunk_num - 1
        if idx < 0 or idx >= len(source_chunks) or idx >= len(translated_chunks):
            continue

        context, source_text = source_chunks[idx]
        translated_text = translated_chunks[idx] if isinstance(translated_chunks[idx], str) else translated_chunks[idx].get('translated_text', '')

        task = SubagentTask(
            task_id=f"{prefix}review-chunk-{chunk_num}",
            task_type="review",
            chunk_num=chunk_num,
            source_text=source_text,
            translated_text=translated_text,
            context=context,
            config={
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "preset": config.preset.name if config.preset else None,
            },
        )
        tasks.append(task)

    return tasks


def create_rework_tasks(
    source_chunks: list,
    translated_chunks: list,
    problem_chunk_nums: List[int],
    review_results: Dict[int, dict],
    config: TranslationConfig,
    run_id: str = "",
) -> List[SubagentTask]:
    """Create rework (retranslation) tasks for problem chunks.

    Args:
        source_chunks: List of (context, source_text) tuples
        translated_chunks: List of translated text strings
        problem_chunk_nums: 1-indexed chunk numbers to rework
        review_results: Per-chunk review results (chunk_num -> review dict)
        config: Translation config
        run_id: Optional run identifier

    Returns:
        List of SubagentTask objects ready for dispatch.
    """
    prefix = f"{run_id}-" if run_id else ""
    tasks = []

    for chunk_num in problem_chunk_nums:
        idx = chunk_num - 1
        if idx < 0 or idx >= len(source_chunks):
            continue

        context, source_text = source_chunks[idx]
        translated_text = ""
        if idx < len(translated_chunks):
            translated_text = translated_chunks[idx] if isinstance(translated_chunks[idx], str) else translated_chunks[idx].get('translated_text', '')

        review = review_results.get(chunk_num, {})
        problems = review.get('problems', [])
        hints_parts = []
        if 'under_translate' in problems:
            hints_parts.append("Previous translation omitted content. Translate all sentences.")
        if 'over_split' in problems:
            hints_parts.append("Previous translation split sentences excessively. Preserve sentence boundaries.")
        if 'hallucination' in problems:
            hints_parts.append("Previous translation added content not in source. Stay faithful.")

        task = SubagentTask(
            task_id=f"{prefix}rework-chunk-{chunk_num}",
            task_type="rework",
            chunk_num=chunk_num,
            source_text=source_text,
            translated_text=translated_text,
            problems=problems,
            context=context,
            hints="\n".join(hints_parts),
            config={
                "source_lang": config.source_lang,
                "target_lang": config.target_lang,
                "preset": config.preset.name if config.preset else None,
            },
        )
        tasks.append(task)

    return tasks


def merge_rework_results(
    records: List[dict],
    results: Dict[str, SubagentResult],
) -> List[int]:
    """Merge rework results back into chunk records.

    Args:
        records: Flat list of chunk records (modified in place)
        results: Map of task_id -> SubagentResult

    Returns:
        List of chunk numbers that were successfully updated.
    """
    updated = []
    for task_id, result in results.items():
        if result is None or result.status != "completed":
            continue
        if not result.improved_translation:
            continue

        # Parse chunk_num from task_id: "rework-chunk-5" or "runid-rework-chunk-5"
        parts = task_id.split("-")
        try:
            chunk_num = int(parts[-1])
        except (ValueError, IndexError):
            continue

        idx = chunk_num - 1
        if 0 <= idx < len(records):
            records[idx]['translated_text'] = result.improved_translation
            updated.append(chunk_num)

    return updated
