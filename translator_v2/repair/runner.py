from __future__ import annotations

from typing import Dict, List

from translator_v2.core.enums import SegmentStatus
from translator_v2.core.models import DocumentLedger, RepairAttempt, SegmentRecord, V2Config
from translator_v2.repair.planner import choose_repair_strategy
from translator_v2.segment.segmenter import split_segment_for_repair
from translator_v2.translate.generator import generate_segment_batch
from translator_v2.validate.coverage import build_batch_parse_issues
from translator_v2.validate.runner import validate_segment_output


def _original_segment_id(ledger: DocumentLedger, segment: SegmentRecord) -> str:
    segment_map = ledger.segment_map()
    current = segment
    while current.parent_segment_id:
        current = segment_map[current.parent_segment_id]
    return current.segment_id


def _root_repair_count(ledger: DocumentLedger, segment: SegmentRecord) -> int:
    segment_map = ledger.segment_map()
    root_id = _original_segment_id(ledger, segment)
    count = 0
    for attempt in ledger.repair_attempts:
        attempted_segment = segment_map.get(attempt.segment_id)
        if attempted_segment is None:
            continue
        if _original_segment_id(ledger, attempted_segment) == root_id and attempt.strategy != "block":
            count += 1
    return count


def _failure_hint_map(segment: SegmentRecord) -> Dict[str, str]:
    hint = "\n".join(
        f"- {issue.get('issue_type', 'unknown')}: {issue.get('observed', '')}".strip()
        for issue in segment.validation_errors
    ).strip()
    return {segment.segment_id: hint} if hint else {}


def append_repair_attempt(
    ledger: DocumentLedger,
    segment: SegmentRecord,
    strategy: str,
    result_status: str,
    issues: List[str],
) -> None:
    ledger.repair_attempts.append(
        RepairAttempt(
            segment_id=segment.segment_id,
            attempt=segment.attempt_count,
            strategy=strategy,
            source_hash=segment.source_hash,
            result_status=result_status,
            issues=issues,
        )
    )


async def _translate_single_segment(
    *,
    ledger: DocumentLedger,
    segment: SegmentRecord,
    provider,
    codex_backend,
    rules_text: str,
    glossary_payload: Dict,
    config: V2Config,
    batch_sequence: int,
    count_as_repair: bool,
) -> bool:
    if count_as_repair:
        segment.repair_count += 1
    segment.attempt_count += 1
    segment.status = SegmentStatus.TRANSLATING.value
    batch_id = f"repair-{batch_sequence:04d}"
    failure_hints = _failure_hint_map(segment) if count_as_repair else None
    if config.generation_mode == "codex_local":
        result = await codex_backend.generate_batch(
            segments=[segment],
            rules_text=rules_text,
            glossary_payload=glossary_payload,
            config=config,
            batch_id=batch_id,
            failure_hints=failure_hints,
            work_type="repair_batch",
            repair_reason=";".join(issue.get("issue_type", "unknown") for issue in segment.validation_errors),
        )
    else:
        result = await generate_segment_batch(
            provider=provider,
            segments=[segment],
            rules_text=rules_text,
            glossary_payload=glossary_payload,
            config=config,
            batch_id=batch_id,
            failure_hints=failure_hints,
        )
    segment.provider = result.provider
    segment.model = result.model
    segment.batch_id = batch_id
    segment.generation_mode = config.generation_mode
    segment.backend_label = result.backend_label or config.generation_mode
    segment.worker_id = result.worker_id or None
    segment.clear_issues()

    if not result.parse_result.get("ok"):
        for issue in build_batch_parse_issues(segment.segment_id, result.parse_result):
            segment.add_issue(issue)
        segment.status = SegmentStatus.FAILED_PARSE.value
        if count_as_repair:
            append_repair_attempt(
                ledger,
                segment,
                strategy="local_retry_parse",
                result_status=segment.status,
                issues=[issue.get("issue_type", "unknown") for issue in segment.validation_errors],
            )
        return False

    translated_text = result.parse_result["units"].get(segment.segment_id, "")
    validation = validate_segment_output(
        segment=segment,
        translated_text=translated_text,
        config=config,
        glossary_payload=glossary_payload,
    )
    segment.translated_text = translated_text.strip()
    segment.warnings = validation.warnings
    if validation.ok:
        segment.status = SegmentStatus.ACCEPTED.value
        segment.accepted_task_id = result.task_id or result.batch_id or None
        segment.accepted_task_epoch = result.task_epoch or None
        segment.accepted_claim_id = result.claim_id or None
        if count_as_repair:
            append_repair_attempt(
                ledger,
                segment,
                strategy="local_retry",
                result_status=segment.status,
                issues=[],
            )
        return True

    for issue in validation.issues:
        segment.add_issue(issue)
    segment.status = SegmentStatus.FAILED_VALIDATION.value
    if count_as_repair:
        append_repair_attempt(
            ledger,
            segment,
            strategy="local_retry",
            result_status=segment.status,
            issues=[issue.issue_type for issue in validation.issues],
        )
    return False


async def repair_segment(
    *,
    ledger: DocumentLedger,
    segment: SegmentRecord,
    provider,
    codex_backend,
    rules_text: str,
    glossary_payload: Dict,
    config: V2Config,
    batch_sequence: int,
) -> int:
    while segment.status in {
        SegmentStatus.FAILED_PARSE.value,
        SegmentStatus.FAILED_VALIDATION.value,
        SegmentStatus.PENDING.value,
    }:
        if segment.status == SegmentStatus.PENDING.value and segment.attempt_count == 0:
            ok = await _translate_single_segment(
                ledger=ledger,
                segment=segment,
                provider=provider,
                codex_backend=codex_backend,
                rules_text=rules_text,
                glossary_payload=glossary_payload,
                config=config,
                batch_sequence=batch_sequence,
                count_as_repair=False,
            )
            batch_sequence += 1
            if ok:
                break
            continue

        if _root_repair_count(ledger, segment) >= config.max_total_repairs_per_segment:
            segment.status = SegmentStatus.BLOCKED.value
            append_repair_attempt(
                ledger,
                segment,
                strategy="block",
                result_status=segment.status,
                issues=["root_repair_budget_exhausted"],
            )
            break

        decision = choose_repair_strategy(segment, config)
        if decision.strategy == "block":
            segment.status = SegmentStatus.BLOCKED.value
            append_repair_attempt(
                ledger,
                segment,
                strategy="block",
                result_status=segment.status,
                issues=[issue.get("issue_type", "unknown") for issue in segment.validation_errors],
            )
            break

        if decision.strategy == "split":
            children = split_segment_for_repair(segment, config.source_lang, config)
            if not children:
                segment.status = SegmentStatus.BLOCKED.value
                append_repair_attempt(
                    ledger,
                    segment,
                    strategy="split_blocked",
                    result_status=segment.status,
                    issues=["segment_not_splittable"],
                )
                break
            segment.repair_count += 1
            segment.status = SegmentStatus.SUPERSEDED_BY_CHILDREN.value
            segment.child_segment_ids = [child.segment_id for child in children]
            ledger.segments.extend(children)
            ledger.document.segment_count = len(ledger.segments)
            append_repair_attempt(
                ledger,
                segment,
                strategy="split",
                result_status=segment.status,
                issues=[child.segment_id for child in children],
            )
            for child in children:
                batch_sequence += 1
                child.status = SegmentStatus.PENDING.value
                batch_sequence = await repair_segment(
                    ledger=ledger,
                    segment=child,
                    provider=provider,
                    codex_backend=codex_backend,
                    rules_text=rules_text,
                    glossary_payload=glossary_payload,
                    config=config,
                    batch_sequence=batch_sequence,
                )
            break

        ok = await _translate_single_segment(
            ledger=ledger,
            segment=segment,
            provider=provider,
            codex_backend=codex_backend,
            rules_text=rules_text,
            glossary_payload=glossary_payload,
            config=config,
            batch_sequence=batch_sequence,
            count_as_repair=True,
        )
        batch_sequence += 1
        if ok:
            break

    return batch_sequence
