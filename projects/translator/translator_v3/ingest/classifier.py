from __future__ import annotations

from pathlib import Path

from input_pipeline import InputState, process_input

from translator_v3.core.models import ClassifiedInput, V3Config


def _detect_file_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    return "txt"


def classify_input(path: str, config: V3Config) -> ClassifiedInput:
    # Phase 0/1 note: v3 still uses the proven v2 input pipeline as a
    # transitional adapter. This dependency is intentionally surfaced in
    # analysis so later phases can replace it cleanly.
    resolved = str(Path(path).resolve())
    result = process_input(resolved, source_lang=config.source_lang, force_ocr=False)
    file_type = _detect_file_type(resolved)
    effective_source_lang = result.analysis.get("effective_source_lang", config.source_lang)

    input_class = "clean_text"
    planned_extractors = ["text_spine"]
    selected_spine = "text_spine"
    ocr_required = False

    if file_type == "pdf":
        planned_extractors = ["marker", "docling"]
        if result.state == InputState.PDF_TEXT_EXTRACTED:
            input_class = "pdf_born_digital"
            selected_spine = "pdf_text_spine"
        else:
            input_class = "pdf_scanned"
            selected_spine = "ocr_text_spine"
            ocr_required = True
    elif result.state in {InputState.TXT_PDF_NORMALIZED, InputState.TXT_OCR_NORMALIZED}:
        input_class = "unknown_noisy_text"
        selected_spine = "normalized_text_spine"
    elif file_type == "txt":
        raw_metrics = result.analysis.get("raw_metrics", {})
        pdfish_score = result.analysis.get("pdfish_score", 0.0)
        ocrish_score = result.analysis.get("ocrish_score", 0.0)
        strong_pdfish_txt = (
            pdfish_score >= 0.35
            or raw_metrics.get("hyphenated_linebreak_count", 0) >= 18
            or (
                raw_metrics.get("form_feed_count", 0) >= 1
                and raw_metrics.get("short_line_ratio", 0.0) >= 0.30
            )
        )
        strong_ocrish_txt = ocrish_score >= 0.45
        if strong_pdfish_txt or strong_ocrish_txt:
            input_class = "unknown_noisy_text"
            selected_spine = "normalized_text_spine"
            planned_extractors = ["text_spine", "marker", "docling"]

    analysis = dict(result.analysis)
    analysis.update(
        {
            "file_type": file_type,
            "input_class": input_class,
            "selected_spine": selected_spine,
            "planned_extractors": planned_extractors,
            "ocr_required": ocr_required,
            "classifier_version": config.classifier_version,
            "transitional_adapters": ["input_pipeline.process_input"],
        }
    )

    return ClassifiedInput(
        input_path=resolved,
        file_type=file_type,
        input_class=input_class,
        ingest_state=result.state.value,
        configured_source_lang=config.source_lang,
        source_lang=effective_source_lang,
        target_lang=config.target_lang,
        processed_text=result.text,
        raw_text=result.raw_text,
        warnings=list(result.warnings),
        analysis=analysis,
        planned_extractors=planned_extractors,
        selected_spine=selected_spine,
        ocr_required=ocr_required,
    )
