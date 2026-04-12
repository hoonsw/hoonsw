from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional

from translator_v2.codex_local.queue import CodexTaskQueue, CodexTaskRecord, task_artifact_dir
from translator_v2.core.models import DocumentLedger, SegmentRecord, V2Config
from translator_v2.translate.generator import BatchGenerationResult
from translator_v2.translate.parser import parse_segmented_response
from translator_v2.translate.prompt_builder import build_batch_system_prompt, build_batch_user_prompt

class CodexLocalBackend:
    def __init__(
        self,
        *,
        output_path: str,
        ledger: DocumentLedger,
        config: V2Config,
        worker=None,
    ):
        run_dir = ledger.document.artifact_dir or str(task_artifact_dir(output_path, ledger.document.run_id))
        self.queue = CodexTaskQueue(run_dir)
        self.config = config
        self.worker = worker
        self.run_id = ledger.document.run_id

    async def generate_batch(
        self,
        *,
        segments: List[SegmentRecord],
        rules_text: str,
        glossary_payload: Dict,
        config: V2Config,
        batch_id: str,
        failure_hints: Optional[Dict[str, str]] = None,
        context_map: Optional[Dict[str, str]] = None,
        work_type: str = "translate_batch",
        repair_reason: Optional[str] = None,
    ) -> BatchGenerationResult:
        system_prompt = build_batch_system_prompt(
            segments=segments,
            rules_text=rules_text,
            glossary_payload=glossary_payload,
            config=config,
            failure_hints=failure_hints,
        )
        user_prompt = build_batch_user_prompt(segments=segments, context_map=context_map)
        attempt = max(segment.attempt_count for segment in segments) if segments else 0
        task = CodexTaskRecord(
            task_id=batch_id,
            run_id=self.run_id,
            generation_mode="codex_local",
            backend_label="codex_local",
            work_type=work_type,
            chapter_id=segments[0].chapter_id if segments else "",
            segment_ids=[segment.segment_id for segment in segments],
            attempt=attempt,
            task_epoch=1,
            created_at=self._now(),
            context={
                "preceding_accepted_tail": self._preceding_context(context_map, segments),
                "glossary_hints": sorted(glossary_payload.keys()),
                "protected_spans": list(config.protected_regexes),
            },
            segments=[
                {
                    "segment_id": segment.segment_id,
                    "source_hash": segment.source_hash,
                    "source_text": segment.source_text,
                    "separator_after": segment.separator_after,
                }
                for segment in segments
            ],
            instructions={
                "output_schema": config.prompt_schema_version,
                "forbid_extra_prose": True,
                "target_language": config.target_lang,
            },
            repair_reason=repair_reason,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        self.queue.enqueue(task)
        return await self._wait_for_valid_result(task, expected_ids=task.segment_ids)

    async def _wait_for_valid_result(
        self,
        task: CodexTaskRecord,
        *,
        expected_ids: List[str],
    ) -> BatchGenerationResult:
        current_task = task
        active_claim_id = ""
        claim_started_at = 0.0
        loop = asyncio.get_running_loop()
        created_at = loop.time()
        stale_requeues = 0

        while True:
            if self.worker is not None:
                await self.worker.process_next(self.queue)

            in_progress = self.queue.load_in_progress(current_task.task_id, current_task.task_epoch)
            if in_progress and in_progress.claim_id:
                active_claim_id = in_progress.claim_id
                claim_started_at = claim_started_at or loop.time()

            for result in self.queue.list_all_completed_results(current_task.task_id):
                if result.task_epoch != current_task.task_epoch:
                    self.queue.quarantine_result(result, "stale_task_epoch")
                    continue
                if active_claim_id and result.claim_id != active_claim_id:
                    self.queue.quarantine_result(result, "claim_id_mismatch")
                    continue
                raw_text = Path(result.raw_output_path).read_text(encoding="utf-8")
                parse_result = parse_segmented_response(
                    response_text=raw_text,
                    expected_ids=expected_ids,
                )
                return BatchGenerationResult(
                    batch_id=current_task.task_id,
                    response_text=raw_text,
                    provider=result.provider_name or "codex_local",
                    model=result.model_name or result.worker_id or "codex-local-worker",
                    parse_result=parse_result,
                    generation_mode="codex_local",
                    backend_label=result.backend_label,
                    worker_id=result.worker_id,
                    task_id=current_task.task_id,
                    task_epoch=result.task_epoch,
                    claim_id=result.claim_id,
                )

            failed = self.queue.load_failed(current_task.task_id, current_task.task_epoch)
            if failed is not None:
                error = failed.get("error") or "unknown worker failure"
                raise RuntimeError(
                    f"codex_local task {current_task.task_id}@{current_task.task_epoch} failed after claim: {error}"
                )

            now = loop.time()
            if not active_claim_id and now - created_at > self.config.codex_claim_timeout_seconds:
                raise TimeoutError(
                    f"codex_local task {current_task.task_id} was not claimed in time. "
                    f"Run dir: {self.queue.run_dir}"
                )

            if active_claim_id and now - claim_started_at > self.config.codex_result_timeout_seconds:
                if stale_requeues >= self.config.codex_stale_requeue_max:
                    raise TimeoutError(
                        f"codex_local task {current_task.task_id}@{current_task.task_epoch} timed out after claim."
                    )
                abandoned = self.queue.mark_stale(
                    current_task.task_id,
                    current_task.task_epoch,
                    reason="result_timeout",
                )
                current_task = self.queue.requeue_with_new_epoch(
                    abandoned,
                    repair_reason="stale_result_timeout",
                )
                active_claim_id = ""
                claim_started_at = 0.0
                created_at = loop.time()
                stale_requeues += 1
                continue

            await asyncio.sleep(self.config.codex_poll_interval_seconds)

    def _preceding_context(self, context_map: Optional[Dict[str, str]], segments: List[SegmentRecord]) -> str:
        if not context_map:
            return ""
        for segment in segments:
            value = context_map.get(segment.segment_id)
            if value:
                return value
        return ""

    def _now(self) -> str:
        from translator_v2.codex_local.queue import now_iso

        return now_iso()
