from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

from translator_v2.adapters.providers import build_provider
from translator_v2.assemble.reconstructor import (
    assemble_deliverable_text,
    can_assemble_deliverable,
    write_blocked_workfile,
)
from translator_v2.codex_local import CodexLocalBackend, ProviderBackedCodexWorker, task_artifact_dir
from translator_v2.core.enums import DocumentStatus, SegmentStatus
from translator_v2.core.ledger import load_ledger, save_ledger
from translator_v2.core.models import DocumentLedger, DocumentRecord, SegmentRecord, V2Config
from translator_v2.ingest.loader import load_document, load_rules_and_glossary
from translator_v2.ingest.normalize import config_fingerprint, sha256_text
from translator_v2.repair.runner import repair_segment
from translator_v2.report.manifest import save_manifest
from translator_v2.segment.segmenter import build_initial_ledger_records
from translator_v2.translate.generator import generate_segment_batch
from translator_v2.validate.coverage import build_batch_parse_issues
from translator_v2.validate.runner import validate_segment_output


@dataclass
class PipelineResult:
    status: str
    output_path: str
    ledger_path: str
    report_path: str
    blocked_workfile: str = ""
    accepted_segments: int = 0
    total_segments: int = 0
    resumed: bool = False


def load_v2_config(config_path: str = "config.yaml", overrides: Optional[Dict] = None) -> V2Config:
    overrides = overrides or {}
    payload: Dict = {}
    path = Path(config_path)
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    providers_cfg = payload.get("providers", {})
    translation_cfg = providers_cfg.get("translation", {})
    config = V2Config(
        source_lang=payload.get("source_lang", "ja"),
        target_lang=payload.get("target_lang", "ko"),
        provider_name=translation_cfg.get("provider", payload.get("provider", "openai")),
        model=translation_cfg.get("model", payload.get("model", "gpt-5.2-chat-latest")),
        generation_mode=payload.get("generation_mode", payload.get("v2", {}).get("generation_mode", "api")),
        codex_worker_mode=payload.get("v2", {}).get("codex_worker_mode", "bootstrap"),
        max_turns=payload.get("max_turns", 1),
        batch_size=payload.get("batch_size", 3),
        max_segment_chars=payload.get("v2", {}).get("max_segment_chars", 900),
        max_segment_sentences=payload.get("v2", {}).get("max_segment_sentences", 6),
        hard_max_segment_chars=payload.get("v2", {}).get("hard_max_segment_chars", 1200),
        local_retry_max=payload.get("v2", {}).get("local_retry_max", 2),
        split_retry_max=payload.get("v2", {}).get("split_retry_max", 1),
        max_total_repairs_per_segment=payload.get("v2", {}).get("max_total_repairs_per_segment", 3),
        ingest_version=payload.get("v2", {}).get("ingest_version", "ingest-v2"),
        config_path=config_path,
        rules_path=payload.get("rules_path", "rules.md"),
        glossary_path=payload.get("glossary_path", "glossary.json"),
        force_ocr=payload.get("force_ocr", False),
        repair_batch_fallback_to_single=payload.get("v2", {}).get("repair_batch_fallback_to_single", True),
        protected_regexes=payload.get("v2", {}).get("protected_regexes", []),
        codex_poll_interval_seconds=payload.get("v2", {}).get("codex_poll_interval_seconds", 0.25),
        codex_claim_timeout_seconds=payload.get("v2", {}).get("codex_claim_timeout_seconds", 30.0),
        codex_result_timeout_seconds=payload.get("v2", {}).get("codex_result_timeout_seconds", 900.0),
        codex_stale_requeue_max=payload.get("v2", {}).get("codex_stale_requeue_max", 1),
    )

    for key, value in overrides.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)
    return config


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _fresh_run_id(previous_run_id: Optional[str] = None) -> str:
    candidate = _run_id()
    if candidate != previous_run_id:
        return candidate
    return f"{candidate}-r"


def _matching_resume(ledger: DocumentLedger, resume_keys: Dict[str, str]) -> bool:
    document = ledger.document
    return all(
        [
            document.input_sha256 == resume_keys["input_sha256"],
            document.normalized_source_sha256 == resume_keys["normalized_source_sha256"],
            document.config_fingerprint == resume_keys["config_fingerprint"],
            document.ingest_version == resume_keys["ingest_version"],
            document.canonicalizer_version == resume_keys["canonicalizer_version"],
            document.segmenter_version == resume_keys["segmenter_version"],
            document.prompt_schema_version == resume_keys["prompt_schema_version"],
        ]
    )


def _recount_document(ledger: DocumentLedger) -> None:
    ledger.document.segment_count = len(ledger.segments)
    ledger.document.accepted_segment_count = len(
        [segment for segment in ledger.segments if segment.status == SegmentStatus.ACCEPTED.value]
    )


def _starting_batch_sequence(ledger: DocumentLedger) -> int:
    highest = 0
    for segment in ledger.segments:
        if not segment.batch_id:
            continue
        match = re.match(r"^(?:batch|repair)-(\d+)$", segment.batch_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _has_unfinished_leaf_segments(ledger: DocumentLedger) -> bool:
    return any(
        not segment.child_segment_ids and segment.status != SegmentStatus.ACCEPTED.value
        for segment in ledger.segments
    )


def _reset_interrupted_leaf_segments(ledger: DocumentLedger) -> bool:
    changed = False
    for segment in ledger.segments:
        if segment.child_segment_ids:
            continue
        if segment.status in {
            SegmentStatus.TRANSLATING.value,
            SegmentStatus.DRAFTED.value,
        }:
            segment.status = SegmentStatus.PENDING.value
            changed = True
    return changed


def _refresh_codex_local_run_context(output_path: str, ledger: DocumentLedger, config: V2Config) -> bool:
    if config.generation_mode != "codex_local":
        return False
    if not _has_unfinished_leaf_segments(ledger):
        return False
    previous_run_id = ledger.document.run_id
    next_run_id = _fresh_run_id(previous_run_id)
    ledger.document.run_id = next_run_id
    ledger.document.generation_mode = config.generation_mode
    ledger.document.artifact_dir = str(task_artifact_dir(output_path, next_run_id))
    return ledger.document.run_id != previous_run_id


def _leaf_segments(ledger: DocumentLedger, statuses: Tuple[str, ...]) -> list[SegmentRecord]:
    return [
        segment
        for segment in sorted(ledger.segments, key=lambda item: item.sort_key())
        if not segment.child_segment_ids and segment.status in statuses
    ]


def _build_context_map(ledger: DocumentLedger, segments: list[SegmentRecord]) -> Dict[str, str]:
    accepted = sorted(
        [
            segment
            for segment in ledger.segments
            if segment.status == SegmentStatus.ACCEPTED.value and not segment.child_segment_ids
        ],
        key=lambda item: item.sort_key(),
    )
    context_map: Dict[str, str] = {}
    for segment in segments:
        previous = [item for item in accepted if item.sort_key() < segment.sort_key()]
        if previous:
            context_map[segment.segment_id] = previous[-1].translated_text[-400:]
    return context_map


def _deliverable_text_matches_ledger(output_path: str, ledger: DocumentLedger) -> bool:
    if not can_assemble_deliverable(ledger):
        return False
    expected = assemble_deliverable_text(ledger)
    path = Path(output_path)
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8") == expected


def _restore_deliverable_from_ledger(output_path: str, ledger: DocumentLedger) -> None:
    expected = assemble_deliverable_text(ledger)
    Path(output_path).write_text(expected, encoding="utf-8")


def _apply_result_metadata(segment: SegmentRecord, result, generation_mode: str, accepted: bool) -> None:
    segment.provider = result.provider
    segment.model = result.model
    segment.generation_mode = generation_mode
    segment.backend_label = result.backend_label or generation_mode
    segment.worker_id = result.worker_id or None
    if accepted:
        segment.accepted_task_id = result.task_id or result.batch_id or None
        segment.accepted_task_epoch = result.task_epoch or None
        segment.accepted_claim_id = result.claim_id or None


def _new_ledger(
    input_path: str,
    output_path: str,
    config: V2Config,
    loaded_document=None,
) -> Tuple[DocumentLedger, str, Dict]:
    loaded_document = loaded_document or load_document(input_path, config)
    rules_text, glossary_payload = load_rules_and_glossary(config)
    fingerprint = config_fingerprint(config, rules_text, glossary_payload)
    chapters, segments = build_initial_ledger_records(loaded_document, config)
    document_run_id = _run_id()
    document = DocumentRecord(
        version=1,
        run_id=document_run_id,
        input_path=loaded_document.input_path,
        input_sha256=loaded_document.input_sha256,
        normalized_source_sha256=loaded_document.normalized_source_sha256,
        source_lang=config.source_lang,
        target_lang=config.target_lang,
        status=DocumentStatus.SEGMENTED.value,
        config_fingerprint=fingerprint,
        canonicalizer_version=config.canonicalizer_version,
        segmenter_version=config.segmenter_version,
        prompt_schema_version=config.prompt_schema_version,
        chapter_ids=[chapter.chapter_id for chapter in chapters],
        segment_count=len(segments),
        accepted_segment_count=0,
        output_path=str(Path(output_path).resolve()),
        generation_mode=config.generation_mode,
        ingest_version=config.ingest_version,
        ingest_state=loaded_document.ingest_state,
        ingest_analysis=dict(loaded_document.ingest_analysis),
        artifact_dir=str(task_artifact_dir(output_path, document_run_id)),
        rules_sha256=sha256_text(rules_text),
        glossary_sha256=sha256_text(json.dumps(glossary_payload, ensure_ascii=False, sort_keys=True)),
        warnings=list(loaded_document.warnings),
    )
    return DocumentLedger(document=document, chapters=chapters, segments=segments), rules_text, glossary_payload


async def run_pipeline(
    *,
    input_path: str,
    output_path: str,
    config: V2Config,
    provider=None,
    dry_run: bool = False,
    resume: bool = True,
) -> PipelineResult:
    output_path = str(Path(output_path).resolve())
    loaded_document = load_document(input_path, config)
    if loaded_document.source_lang != config.source_lang:
        config = replace(config, source_lang=loaded_document.source_lang)
    rules_text, glossary_payload = load_rules_and_glossary(config)
    fingerprint = config_fingerprint(config, rules_text, glossary_payload)
    resume_keys = {
        "input_sha256": loaded_document.input_sha256,
        "normalized_source_sha256": loaded_document.normalized_source_sha256,
        "config_fingerprint": fingerprint,
        "ingest_version": config.ingest_version,
        "canonicalizer_version": config.canonicalizer_version,
        "segmenter_version": config.segmenter_version,
        "prompt_schema_version": config.prompt_schema_version,
    }

    resumed = False
    ledger = load_ledger(output_path) if resume else None
    if ledger and not _matching_resume(ledger, resume_keys):
        ledger = None
    if ledger:
        resumed = True
        rules_text, glossary_payload = load_rules_and_glossary(config)
        changed = False
        if _reset_interrupted_leaf_segments(ledger):
            changed = True
        if _refresh_codex_local_run_context(output_path, ledger, config):
            changed = True
        if changed:
            save_ledger(output_path, ledger)
    else:
        ledger, rules_text, glossary_payload = _new_ledger(
            input_path,
            output_path,
            config,
            loaded_document=loaded_document,
        )
        save_ledger(output_path, ledger)

    if dry_run:
        _recount_document(ledger)
        ledger.document.status = DocumentStatus.SEGMENTED.value
        save_ledger(output_path, ledger)
        report_file = save_manifest(output_path, ledger)
        return PipelineResult(
            status=ledger.document.status,
            output_path=output_path,
            ledger_path=str(Path(output_path).with_suffix(Path(output_path).suffix + ".v2.ledger.json")),
            report_path=report_file,
            accepted_segments=ledger.document.accepted_segment_count,
            total_segments=ledger.document.segment_count,
            resumed=resumed,
        )

    codex_backend = None
    if config.generation_mode == "api":
        provider = provider or build_provider(config)
    elif config.generation_mode == "codex_local":
        worker = None
        if provider is not None:
            worker = provider if hasattr(provider, "process_next") else ProviderBackedCodexWorker(provider)
        elif config.codex_worker_mode == "bootstrap":
            worker = ProviderBackedCodexWorker(
                build_provider(config),
                worker_id="codex-local-bootstrap",
            )
        codex_backend = CodexLocalBackend(
            output_path=output_path,
            ledger=ledger,
            config=config,
            worker=worker,
        )
        if worker is None:
            print(
                f"[codex_local] waiting for an external worker. "
                f"Run dir: {ledger.document.artifact_dir}"
            )
    else:
        raise ValueError(f"Unsupported translator_v2 generation_mode: {config.generation_mode}")

    if ledger.document.status == DocumentStatus.DELIVERABLE.value and can_assemble_deliverable(ledger):
        if not _deliverable_text_matches_ledger(output_path, ledger):
            _restore_deliverable_from_ledger(output_path, ledger)
        report_file = save_manifest(output_path, ledger)
        return PipelineResult(
            status=ledger.document.status,
            output_path=output_path,
            ledger_path=str(Path(output_path).with_suffix(Path(output_path).suffix + ".v2.ledger.json")),
            report_path=report_file,
            accepted_segments=ledger.document.accepted_segment_count,
            total_segments=ledger.document.segment_count,
            resumed=True,
        )

    ledger.document.status = DocumentStatus.TRANSLATING.value
    save_ledger(output_path, ledger)

    batch_sequence = _starting_batch_sequence(ledger)
    while True:
        pending = _leaf_segments(
            ledger,
            (
                SegmentStatus.PENDING.value,
                SegmentStatus.FAILED_PARSE.value,
                SegmentStatus.FAILED_VALIDATION.value,
            ),
        )
        if not pending:
            break

        fresh_batch = [segment for segment in pending if segment.attempt_count == 0][: config.batch_size]
        if fresh_batch:
            context_map = _build_context_map(ledger, fresh_batch)
            batch_id = f"batch-{batch_sequence:04d}"
            for segment in fresh_batch:
                segment.status = SegmentStatus.TRANSLATING.value
                segment.attempt_count += 1
                segment.batch_id = batch_id
            if config.generation_mode == "codex_local":
                result = await codex_backend.generate_batch(
                    segments=fresh_batch,
                    rules_text=rules_text,
                    glossary_payload=glossary_payload,
                    config=config,
                    batch_id=batch_id,
                    context_map=context_map,
                    work_type="translate_batch",
                )
            else:
                result = await generate_segment_batch(
                    provider=provider,
                    segments=fresh_batch,
                    rules_text=rules_text,
                    glossary_payload=glossary_payload,
                    config=config,
                    batch_id=batch_id,
                    context_map=context_map,
                )
            if not result.parse_result.get("ok"):
                for segment in fresh_batch:
                    _apply_result_metadata(segment, result, config.generation_mode, accepted=False)
                    segment.clear_issues()
                    for issue in build_batch_parse_issues(segment.segment_id, result.parse_result):
                        segment.add_issue(issue)
                    segment.status = SegmentStatus.FAILED_PARSE.value
            else:
                for segment in fresh_batch:
                    translated_text = result.parse_result["units"].get(segment.segment_id, "")
                    _apply_result_metadata(segment, result, config.generation_mode, accepted=False)
                    segment.clear_issues()
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
                        _apply_result_metadata(segment, result, config.generation_mode, accepted=True)
                    else:
                        for issue in validation.issues:
                            segment.add_issue(issue)
                        segment.status = SegmentStatus.FAILED_VALIDATION.value
            batch_sequence += 1
            save_ledger(output_path, ledger)

        failed = [
            segment
            for segment in _leaf_segments(
                ledger,
                (
                    SegmentStatus.PENDING.value,
                    SegmentStatus.FAILED_PARSE.value,
                    SegmentStatus.FAILED_VALIDATION.value,
                ),
            )
            if segment.status != SegmentStatus.PENDING.value or segment.attempt_count > 0
        ]
        if not failed:
            continue
        for segment in failed:
            batch_sequence = await repair_segment(
                ledger=ledger,
                segment=segment,
                provider=provider,
                codex_backend=codex_backend,
                rules_text=rules_text,
                glossary_payload=glossary_payload,
                config=config,
                batch_sequence=batch_sequence,
            )
            save_ledger(output_path, ledger)

    _recount_document(ledger)
    blocked_workfile = ""
    if can_assemble_deliverable(ledger):
        ledger.document.status = DocumentStatus.ASSEMBLED.value
        Path(output_path).write_text(assemble_deliverable_text(ledger), encoding="utf-8")
        ledger.document.status = DocumentStatus.DELIVERABLE.value
        ledger.blocked_manifest = {}
    else:
        ledger.document.status = DocumentStatus.BLOCKED.value
        blocked_workfile = write_blocked_workfile(output_path, ledger)
        ledger.blocked_manifest = {
            "blocked_workfile": blocked_workfile,
            "unresolved_leaf_segments": [
                {
                    "segment_id": segment.segment_id,
                    "status": segment.status,
                    "issues": segment.validation_errors,
                }
                for segment in sorted(ledger.segments, key=lambda item: item.sort_key())
                if not segment.child_segment_ids and segment.status != SegmentStatus.ACCEPTED.value
            ],
        }

    save_ledger(output_path, ledger)
    report_file = save_manifest(output_path, ledger)
    return PipelineResult(
        status=ledger.document.status,
        output_path=output_path,
        ledger_path=str(Path(output_path).with_suffix(Path(output_path).suffix + ".v2.ledger.json")),
        report_path=report_file,
        blocked_workfile=blocked_workfile,
        accepted_segments=ledger.document.accepted_segment_count,
        total_segments=ledger.document.segment_count,
        resumed=resumed,
    )
