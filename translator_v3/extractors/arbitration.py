from __future__ import annotations

import hashlib
import json
from typing import Dict, List

from translator_v3.core.models import ClassifiedInput, ExtractionCandidate, ExtractionTrace, V3Config
from translator_v3.extractors.adapters import available_capabilities


def _capability_map() -> Dict[str, object]:
    return {item.name: item for item in available_capabilities()}


def _candidate_score(input_class: str, name: str) -> float:
    if input_class == "pdf_scanned":
        return {"marker": 0.94, "docling": 0.89, "text_spine": 0.45}.get(name, 0.0)
    if input_class == "pdf_born_digital":
        return {"docling": 0.93, "marker": 0.90, "text_spine": 0.55}.get(name, 0.0)
    if input_class == "unknown_noisy_text":
        return {"marker": 0.88, "docling": 0.84, "text_spine": 0.60}.get(name, 0.0)
    return {"text_spine": 0.99}.get(name, 0.0)


def _preferred_order(input_class: str) -> List[str]:
    if input_class == "pdf_scanned":
        return ["marker", "docling", "text_spine"]
    if input_class == "pdf_born_digital":
        return ["docling", "marker", "text_spine"]
    if input_class == "unknown_noisy_text":
        return ["marker", "docling", "text_spine"]
    return ["text_spine"]


def _runtime_supported(name: str) -> bool:
    return name in {"docling", "text_spine"}


def build_extraction_trace(classified: ClassifiedInput, config: V3Config) -> ExtractionTrace:
    capability_map = _capability_map()
    candidates: list[ExtractionCandidate] = []
    preprocessors: list[str] = []
    notes: list[str] = []

    if classified.input_class == "pdf_scanned":
        cap = capability_map["ocrmypdf"]
        if cap.available:
            preprocessors.append("ocrmypdf")
        else:
            notes.append("ocrmypdf unavailable; scanned-PDF preprocessing will fall back to existing OCR/text layer inputs.")

    order = _preferred_order(classified.input_class)
    planned_extractor = "text_spine"
    resolved_owner = "text_spine"
    fallback_reason = ""
    execution_mode = "text_spine"
    integration_status = "preflight_only"

    for name in order:
        available = name == "text_spine" or capability_map.get(name).available
        if name == "text_spine":
            reason = "always_available_baseline"
        elif not available:
            reason = "tool_unavailable"
        elif not _runtime_supported(name):
            reason = "capability_available_but_runtime_not_enabled"
        else:
            reason = "capability_available"
        score = _candidate_score(classified.input_class, name)
        candidates.append(
            ExtractionCandidate(
                name=name,
                role="fallback" if name == "text_spine" else "extractor",
                available=available,
                selected=False,
                reason=reason,
                score=score,
            )
        )

    for candidate in candidates:
        if candidate.name == "text_spine":
            continue
        if candidate.available and _runtime_supported(candidate.name):
            planned_extractor = candidate.name
            fallback_reason = ""
            execution_mode = "external_candidate_detected"
            break

    if planned_extractor != "text_spine":
        resolved_owner = "text_spine"
        execution_mode = "planned_external_with_text_spine_fallback"
        fallback_reason = (
            f"{planned_extractor} selected by arbitration. Graph ownership remains text_spine until "
            "external extractor execution completes and the extracted text is accepted."
        )
    else:
        resolved_owner = "text_spine"
        if classified.input_class != "clean_text":
            fallback_reason = "No runtime-enabled external extractor available; using text_spine fallback."
            for candidate in candidates:
                if candidate.available and candidate.name != "text_spine" and not _runtime_supported(candidate.name):
                    notes.append(f"{candidate.name} is installed but not yet wired for runtime execution in translator_v3.")

    selected_name = planned_extractor if planned_extractor != "text_spine" else resolved_owner
    for candidate in candidates:
        if candidate.name == selected_name:
            candidate.selected = True

    probes = list(capability_map.values())
    cache_payload = {
        "input_class": classified.input_class,
        "ingest_state": classified.ingest_state,
        "planned_extractors": classified.planned_extractors,
        "capabilities": [probe.to_dict() for probe in probes],
        "classifier_version": config.classifier_version,
        "graph_builder_version": config.graph_builder_version,
    }
    cache_key = hashlib.sha256(
        json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return ExtractionTrace(
        strategy="single_owner_per_page",
        planned_extractor=planned_extractor,
        resolved_owner=resolved_owner,
        fallback_reason=fallback_reason,
        execution_mode=execution_mode,
        integration_status=integration_status,
        external_execution_attempted=False,
        external_execution_succeeded=False,
        ownership_mode="single_owner_per_page",
        reroute_budget_per_document=25,
        reroute_budget_per_page=1,
        cache_key=cache_key,
        preprocessors=preprocessors,
        candidates=candidates,
        probes=probes,
        page_owners={},
        notes=notes,
    )
