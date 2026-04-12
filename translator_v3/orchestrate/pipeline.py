from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
import re
from typing import Dict, List

from translator_v3.assemble import assemble_output, build_review_bundle
from translator_v3.backends import build_backend
from translator_v3.core.io import telemetry_path, write_graph_bundle
from translator_v3.core.models import DocumentGraph, TranslationUnit, V3Config, V3RunResult
from translator_v3.ingest.spine import build_document_graph
from translator_v3.plan import build_translation_units
from translator_v3.validate import validate_unit_output


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _unit_paths(root: str | Path) -> Dict[str, Path]:
    root = Path(root)
    return {
        "units": root / "translation_units.json",
        "preview": root / "translated.preview.txt",
        "output": root / "translated.txt",
        "blocked": root / "translated.blocked.txt",
        "review": root / "review_bundle.json",
        "result": root / "run_result.json",
    }


def _failure_hint(unit: TranslationUnit) -> str:
    if not unit.validation_errors:
        return ""
    lines = []
    for issue in unit.validation_errors:
        issue_type = issue.get("issue_type", "unknown")
        observed = issue.get("observed", "")
        expected = issue.get("expected", "")
        if observed and expected:
            lines.append(f"{issue_type}: observed `{observed}` / expected `{expected}`")
        elif observed:
            lines.append(f"{issue_type}: {observed}")
        else:
            lines.append(issue_type)
    return "\n".join(lines)


def _sentence_count(text: str, lang: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    if lang == "ja":
        parts = [part.strip() for part in re.split(r"(?<=[。！？])\s*", stripped) if part.strip()]
    else:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", stripped) if part.strip()]
    return len(parts) if parts else 1


def _should_run_adequacy_review(unit: TranslationUnit, translated_text: str, config: V3Config) -> bool:
    if config.adequacy_review_mode == "off":
        return False
    if unit.unit_class != "body_paragraph":
        return False
    if config.adequacy_review_mode == "all_body_units":
        return True
    source_chars = len(unit.source_text.strip())
    if source_chars < config.adequacy_review_min_chars:
        return False
    translated_chars = len(translated_text.strip())
    char_ratio = translated_chars / max(1, source_chars)
    source_sentences = _sentence_count(unit.source_text, unit.source_lang)
    return source_sentences <= 1 or char_ratio < config.adequacy_review_ratio_threshold


def _write_run_artifacts(
    graph: DocumentGraph,
    units: List[TranslationUnit],
    *,
    include_graph_bundle: bool,
    include_review_bundle: bool,
) -> Dict[str, str]:
    graph_paths: dict[str, str] = {}
    if include_graph_bundle:
        graph_paths = write_graph_bundle(
            graph,
            normalized_source_text=graph.analysis.get("normalized_text", ""),
            raw_text=graph.analysis.get("raw_text") if "raw_text" in graph.analysis else None,
        )
    elif graph.telemetry:
        telemetry_out = telemetry_path(graph.artifact_dir)
        telemetry_out.write_text(
            json.dumps(graph.telemetry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        graph_paths["telemetry"] = str(telemetry_out)

    paths = _unit_paths(graph.artifact_dir)
    paths["units"].write_text(
        json.dumps([unit.to_dict() for unit in units], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    preview_text = assemble_output(units)
    paths["preview"].write_text(preview_text, encoding="utf-8")
    if include_review_bundle:
        review_bundle = build_review_bundle(units)
        paths["review"].write_text(json.dumps(review_bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    merged = {**graph_paths, "units": str(paths["units"]), "preview": str(paths["preview"]), "review": str(paths["review"])}
    return merged


def _update_telemetry(graph: DocumentGraph, units: List[TranslationUnit]) -> None:
    processed_node_ids = {unit.node_id for unit in units if unit.status != "PENDING"}
    processed_pages = {
        node.page_index
        for node in graph.nodes
        if node.node_type == "block" and node.node_id in processed_node_ids and node.page_index is not None
    }
    graph.telemetry.pages_processed = len(processed_pages)
    graph.telemetry.unresolved_blocks = sum(1 for unit in units if unit.status != "ACCEPTED")
    graph.telemetry.blocked_blocks = sum(1 for unit in units if unit.status == "BLOCKED")
    graph.telemetry.last_activity_at = _now_iso()


async def run_pipeline(input_path: str, output_path: str, config: V3Config) -> V3RunResult:
    graph = build_document_graph(input_path, config)
    backend = build_backend(config)
    units = build_translation_units(graph, config)
    graph.telemetry.phase = "translating"
    graph.telemetry.progress_mode = "snapshot_artifacts"
    graph.telemetry.preview_path = str(_unit_paths(graph.artifact_dir)["preview"])
    _write_run_artifacts(graph, units, include_graph_bundle=True, include_review_bundle=True)

    snapshot_interval = max(1, config.batch_size)
    concurrency = max(1, config.batch_size if getattr(backend, "supports_parallel", False) else 1)
    completed_units = 0
    snapshot_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)

    async def _maybe_write_snapshot(unit: TranslationUnit) -> None:
        nonlocal completed_units
        async with snapshot_lock:
            completed_units += 1
            _update_telemetry(graph, units)
            should_flush = (
                completed_units % snapshot_interval == 0
                or unit.status == "BLOCKED"
                or completed_units == len(units)
            )
            if should_flush:
                _write_run_artifacts(graph, units, include_graph_bundle=False, include_review_bundle=False)

    async def _process_unit(unit: TranslationUnit) -> None:
        attempt = 0
        async with semaphore:
            while True:
                hint = _failure_hint(unit)
                try:
                    response = await backend.translate_unit(unit, failure_hint=hint)
                except Exception as exc:
                    unit.validation_errors = [
                        {
                            "issue_type": "backend_exception",
                            "severity": "critical",
                            "segment_id": unit.unit_id,
                            "expected": "backend response",
                            "observed": str(exc),
                            "action": "retry_unit",
                        }
                    ]
                    attempt += 1
                    if attempt > config.max_retry_per_unit:
                        unit.status = "BLOCKED"
                        break
                    continue
                unit.attempt_count += 1
                unit.translated_text = response.text
                unit.backend_name = response.backend_name
                unit.provider_name = response.provider_name
                unit.model_name = response.model_name
                validation = validate_unit_output(unit, response.text, config)
                if validation.ok and _should_run_adequacy_review(unit, response.text, config):
                    reviewer = getattr(backend, "review_unit_adequacy", None)
                    if reviewer is not None:
                        try:
                            review_ok, review_reason = await reviewer(unit, response.text)
                            graph.telemetry.adequacy_review_status = "selective_model_review"
                            if not review_ok:
                                validation.issues.append(
                                    {
                                        "issue_type": "adequacy_review_failed",
                                        "severity": "repair-trigger",
                                        "segment_id": unit.unit_id,
                                        "expected": "translation should preserve materially important source content",
                                        "observed": review_reason or "adequacy reviewer flagged possible omission or distortion",
                                        "action": "retry_unit",
                                    }
                                )
                                validation.ok = False
                        except Exception as exc:
                            graph.telemetry.adequacy_review_status = "review_error"
                            validation.issues.append(
                                {
                                    "issue_type": "adequacy_review_exception",
                                    "severity": "repair-trigger",
                                    "segment_id": unit.unit_id,
                                    "expected": "adequacy review should complete successfully",
                                    "observed": str(exc),
                                    "action": "retry_unit",
                                }
                            )
                            validation.ok = False
                unit.validation_errors = validation.issues
                unit.warnings = validation.warnings
                if validation.ok:
                    unit.status = "ACCEPTED"
                    break
                attempt += 1
                if attempt > config.max_retry_per_unit:
                    unit.status = "BLOCKED"
                    break
            await _maybe_write_snapshot(unit)

    await asyncio.gather(*(_process_unit(unit) for unit in units))

    graph.telemetry.phase = "assembled"
    _update_telemetry(graph, units)
    graph.telemetry.preview_path = str(_unit_paths(graph.artifact_dir)["preview"])
    artifact_paths = _write_run_artifacts(graph, units, include_graph_bundle=False, include_review_bundle=False)
    final_text = assemble_output(units)
    paths = _unit_paths(graph.artifact_dir)

    status = "DELIVERABLE" if all(unit.status == "ACCEPTED" for unit in units) else "BLOCKED"
    graph.telemetry.phase = status.lower()
    if graph.telemetry.adequacy_review_status == "not_run":
        graph.telemetry.adequacy_review_status = "completed_without_model_review"
    _update_telemetry(graph, units)
    artifact_paths = _write_run_artifacts(graph, units, include_graph_bundle=True, include_review_bundle=True)
    final_output_written = False
    translated_output_path = str(paths["output"])
    if status == "DELIVERABLE":
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(final_text, encoding="utf-8")
        paths["output"].write_text(final_text, encoding="utf-8")
        translated_output_path = str(Path(output_path).resolve())
        final_output_written = True
    else:
        paths["blocked"].write_text(final_text, encoding="utf-8")
        translated_output_path = str(paths["blocked"])

    result = V3RunResult(
        run_id=graph.run_id,
        status=status,
        input_path=graph.input_path,
        output_path=str(Path(output_path).resolve()),
        artifact_dir=graph.artifact_dir,
        accepted_units=sum(1 for unit in units if unit.status == "ACCEPTED"),
        blocked_units=sum(1 for unit in units if unit.status == "BLOCKED"),
        total_units=len(units),
        preview_path=artifact_paths["preview"],
        review_bundle_path=artifact_paths["review"],
        translated_output_path=translated_output_path,
        final_output_written=final_output_written,
        warnings=list(graph.warnings),
    )
    paths["result"].write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
