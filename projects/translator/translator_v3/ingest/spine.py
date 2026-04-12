from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Iterable, List

from translator_v2.segment.chapter_detector import detect_chapters
from translator_v3.core.io import artifact_dir as resolve_artifact_dir
from translator_v3.core.models import DocumentGraph, DocumentNode, V3Config, V3Telemetry
from translator_v3.extractors.arbitration import build_extraction_trace
from translator_v3.extractors.execution import execute_extraction
from translator_v3.ingest.classifier import classify_input


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run_id_for(path: str) -> str:
    stem = Path(path).stem or "document"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "document"
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_stem}"


def _iter_page_slices(source_text: str | None, page_break_token: str) -> List[tuple[int, int, str, str]]:
    if not source_text:
        return [(0, 0, "", "processed_source")]
    if page_break_token not in source_text:
        return [(0, len(source_text), source_text, "processed_source")]

    pages: list[tuple[int, int, str, str]] = []
    start = 0
    cursor = 0
    for chunk in source_text.split(page_break_token):
        end = start + len(chunk)
        pages.append((start, end, chunk, "raw_source"))
        cursor = end
        start = cursor + len(page_break_token)
    return pages or [(0, len(source_text), source_text, "processed_source")]


def _guess_page_index(start_offset: int, total_length: int, page_count: int) -> int:
    if page_count <= 1 or total_length <= 0:
        return 1
    fraction = max(0.0, min(start_offset / max(1, total_length), 0.999999))
    return min(page_count, int(fraction * page_count) + 1)


def _block_spans(chapter_text: str) -> Iterable[tuple[int, int, str]]:
    cursor = 0
    for match in re.finditer(r"\S(?:.*?\S)?(?=(?:\n{2,}|\Z))", chapter_text, re.S):
        start = match.start()
        end = match.end()
        if end <= cursor:
            continue
        cursor = end
        text = chapter_text[start:end].strip()
        if text:
            yield start, end, text


def _base_confidence(input_class: str) -> float:
    if input_class == "clean_text":
        return 0.98
    if input_class == "pdf_born_digital":
        return 0.92
    if input_class == "pdf_scanned":
        return 0.78
    return 0.72


def build_document_graph(input_path: str, config: V3Config, run_id: str | None = None) -> DocumentGraph:
    classified = classify_input(input_path, config)
    run_id = run_id or _run_id_for(input_path)
    artifact_dir = str(resolve_artifact_dir(config.run_root, run_id))
    graph_nodes: list[DocumentNode] = []
    base_confidence = _base_confidence(classified.input_class)
    extraction_trace = build_extraction_trace(classified, config)
    extraction_result = execute_extraction(classified, extraction_trace, artifact_dir)

    text = extraction_result.text if extraction_result.text else classified.processed_text
    raw_or_source_text = extraction_result.raw_text or classified.raw_text or text
    total_length = len(text)
    page_slices = _iter_page_slices(raw_or_source_text, config.page_break_token)
    page_count = max(1, len(page_slices))

    if extraction_result.executed:
        extraction_trace.external_execution_attempted = True
        extraction_trace.external_execution_succeeded = extraction_result.succeeded
        if extraction_result.succeeded:
            extraction_trace.resolved_owner = extraction_result.extractor_name
            extraction_trace.execution_mode = f"executed_{extraction_result.extractor_name}"
            extraction_trace.integration_status = "executed"
            extraction_trace.fallback_reason = ""
        else:
            extraction_trace.resolved_owner = "text_spine"
            extraction_trace.execution_mode = f"{extraction_result.extractor_name}_failed_fallback"
            extraction_trace.integration_status = "fallback_after_failed_execution"
            extraction_trace.fallback_reason = "; ".join(extraction_result.warnings) or "external execution failed"
    else:
        extraction_trace.resolved_owner = "text_spine"
        extraction_trace.integration_status = (
            "not_needed" if extraction_trace.planned_extractor == "text_spine" else "fallback_only"
        )
        extraction_trace.notes.extend(extraction_result.notes)
        if extraction_result.source_path != classified.input_path:
            extraction_trace.fallback_reason = (
                f"sidecar source discovered ({extraction_result.source_path}) but no supported extractor executed"
            )
    extraction_trace.notes.extend(extraction_result.notes)

    doc_id = "doc"
    graph_nodes.append(
        DocumentNode(
            node_id=doc_id,
            parent_id=None,
            node_type="document",
            source_text="",
            start_offset=0,
            end_offset=total_length,
            reading_order=0,
            page_index=None,
            confidence=base_confidence,
            metadata={
                "input_class": classified.input_class,
                "ingest_state": classified.ingest_state,
                "selected_spine": classified.selected_spine,
            },
        )
    )

    for idx, (page_start, page_end, page_text, offset_space) in enumerate(page_slices, start=1):
        page_id = f"page.p{idx:04d}"
        graph_nodes.append(
            DocumentNode(
                node_id=page_id,
                parent_id=doc_id,
                node_type="page",
                source_text=page_text,
                start_offset=page_start,
                end_offset=page_end,
                reading_order=idx,
                page_index=idx,
                confidence=base_confidence,
                metadata={
                    "estimated": classified.file_type != "pdf" and classified.raw_text is None,
                    "offset_space": offset_space,
                    "excerpt": page_text[:400],
                    "owner": extraction_trace.resolved_owner,
                },
            )
        )
        extraction_trace.page_owners[page_id] = extraction_trace.resolved_owner

    chapters = detect_chapters(text, classified.source_lang, min_chapter_size=config.min_chapter_size)
    reading_order = len(graph_nodes)
    block_count = 0
    low_confidence_pages = 0 if base_confidence >= 0.8 else page_count

    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_id = f"chapter.ch{chapter_index:03d}"
        chapter_page = _guess_page_index(chapter.start_offset, total_length, page_count)
        graph_nodes.append(
            DocumentNode(
                node_id=chapter_id,
                parent_id=doc_id,
                node_type="chapter",
                source_text=chapter.title,
                start_offset=chapter.start_offset,
                end_offset=chapter.end_offset,
                reading_order=reading_order,
                page_index=chapter_page,
                confidence=base_confidence,
                metadata={"title": chapter.title},
            )
        )
        reading_order += 1

        local_block_index = 0
        for local_start, local_end, block_text in _block_spans(chapter.text):
            local_block_index += 1
            block_count += 1
            absolute_start = chapter.start_offset + local_start
            absolute_end = chapter.start_offset + local_end
            page_index = _guess_page_index(absolute_start, total_length, page_count)
            graph_nodes.append(
                DocumentNode(
                    node_id=f"{chapter_id}.block.b{local_block_index:04d}",
                    parent_id=chapter_id,
                    node_type="block",
                    source_text=block_text,
                    start_offset=absolute_start,
                    end_offset=absolute_end,
                    reading_order=reading_order,
                    page_index=page_index,
                    confidence=base_confidence,
                    metadata={
                        "unit_class": "body_paragraph",
                        "char_count": len(block_text),
                    },
                )
            )
            reading_order += 1

    telemetry = V3Telemetry(
        phase="ingested",
        progress_mode="snapshot_artifacts",
        pages_total=page_count,
        pages_processed=page_count,
        chapters_total=len(chapters),
        blocks_total=block_count,
        unresolved_blocks=block_count,
        blocked_blocks=0,
        low_confidence_pages=low_confidence_pages,
        warnings_count=len(classified.warnings),
        reroute_count=0,
        ocr_repair_count=1 if classified.ocr_required else 0,
        adequacy_review_status="not_run",
        preview_path="",
        last_activity_at=_now_iso(),
    )

    return DocumentGraph(
        version=1,
        run_id=run_id,
        artifact_dir=artifact_dir,
        input_path=classified.input_path,
        input_class=classified.input_class,
        ingest_state=classified.ingest_state,
        configured_source_lang=classified.configured_source_lang,
        source_lang=classified.source_lang,
        target_lang=classified.target_lang,
        selected_spine=classified.selected_spine,
        planned_extractors=classified.planned_extractors,
        extraction_plan=extraction_trace.execution_mode,
        extraction_trace=extraction_trace,
        warnings=classified.warnings,
        analysis={
            **classified.analysis,
            "normalized_text": text,
            "effective_source_path": extraction_result.source_path,
            "external_extraction_executed": extraction_result.executed,
            "external_extraction_succeeded": extraction_result.succeeded,
            "external_reported_page_count": extraction_result.page_count,
            "extraction_artifact_paths": extraction_result.artifact_paths,
            "extraction_execution_warnings": extraction_result.warnings,
            "transitional_adapters": list(classified.analysis.get("transitional_adapters", []))
            + ["translator_v2.segment.chapter_detector.detect_chapters"],
            "extraction_trace": extraction_trace.to_dict(),
            "phase2_slice_status": (
                "external_extractor_executed"
                if extraction_result.succeeded and extraction_result.executed
                else ("text_spine_only" if extraction_trace.integration_status == "not_needed" else "fallback_text_spine")
            ),
        },
        nodes=graph_nodes,
        telemetry=telemetry,
    )
