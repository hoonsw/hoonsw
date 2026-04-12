from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from translator_v3.core.models import ClassifiedInput, ExtractionTrace
from translator_v3.extractors.adapters import available_capabilities


@dataclass
class ExtractionExecutionResult:
    extractor_name: str
    source_path: str
    executed: bool
    succeeded: bool
    text: str
    raw_text: Optional[str] = None
    page_count: Optional[int] = None
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _capability_available(name: str) -> bool:
    return any(item.name == name and item.available for item in available_capabilities())


def _discover_sidecar_pdf(input_path: str) -> Optional[str]:
    path = Path(input_path)
    if path.suffix.lower() == ".pdf":
        return str(path)
    for candidate in (path.with_suffix(".pdf"), path.with_suffix(".PDF")):
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _best_source_path(classified: ClassifiedInput) -> tuple[str, List[str]]:
    notes: list[str] = []
    if classified.file_type == "txt" and classified.input_class == "unknown_noisy_text":
        sidecar_pdf = _discover_sidecar_pdf(classified.input_path)
        if sidecar_pdf:
            notes.append(f"discovered sidecar pdf for noisy text input: {sidecar_pdf}")
            return sidecar_pdf, notes
    return classified.input_path, notes


def _docling_page_count(document: object, result: object) -> Optional[int]:
    for candidate in (getattr(document, "pages", None), getattr(result, "pages", None)):
        if candidate is None:
            continue
        try:
            return len(candidate)
        except TypeError:
            continue
    return None


def _run_docling(source_path: str, artifact_dir: str, input_class: str) -> ExtractionExecutionResult:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    out_dir = Path(artifact_dir) / "extraction"
    out_dir.mkdir(parents=True, exist_ok=True)
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = input_class == "pdf_scanned"
    pipeline_options.do_table_structure = False
    pipeline_options.do_picture_classification = False
    pipeline_options.do_picture_description = False
    pipeline_options.do_code_enrichment = False
    pipeline_options.do_formula_enrichment = False
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )
    result = converter.convert(source_path)
    document = result.document
    if hasattr(document, "export_to_markdown"):
        extracted_text = document.export_to_markdown()
        suffix = ".md"
    elif hasattr(document, "export_to_text"):
        extracted_text = document.export_to_text()
        suffix = ".txt"
    else:
        extracted_text = str(document)
        suffix = ".txt"
    success = bool(extracted_text.strip())
    output_path = out_dir / f"docling_extracted{suffix}"
    output_path.write_text(extracted_text if extracted_text.endswith("\n") or not extracted_text else extracted_text + "\n", encoding="utf-8")
    return ExtractionExecutionResult(
        extractor_name="docling",
        source_path=source_path,
        executed=True,
        succeeded=success,
        text=extracted_text,
        raw_text=extracted_text,
        page_count=_docling_page_count(document, result),
        artifact_paths={"extracted_text": str(output_path)},
        notes=["docling execution completed"],
    )


def execute_extraction(
    classified: ClassifiedInput,
    trace: ExtractionTrace,
    artifact_dir: str,
) -> ExtractionExecutionResult:
    source_path, notes = _best_source_path(classified)

    candidate_order: list[str] = []
    if trace.planned_extractor and trace.planned_extractor != "text_spine":
        candidate_order.append(trace.planned_extractor)
    candidate_order.extend(
        candidate.name
        for candidate in trace.candidates
        if candidate.name not in candidate_order and candidate.name != "text_spine"
    )

    runtime_supported = {
        "docling": _run_docling if _capability_available("docling") else None,
    }

    for candidate in candidate_order:
        runner = runtime_supported.get(candidate)
        if runner is None:
            continue
        if Path(source_path).suffix.lower() != ".pdf":
            continue
        try:
            result = runner(source_path, artifact_dir, classified.input_class)
        except Exception as exc:  # pragma: no cover - runtime environment dependent
            return ExtractionExecutionResult(
                extractor_name=candidate,
                source_path=source_path,
                executed=True,
                succeeded=False,
                text=classified.processed_text,
                raw_text=classified.raw_text,
                warnings=[f"{candidate} execution failed: {exc}"],
                notes=notes + [f"{candidate} execution raised {type(exc).__name__}"],
            )
        result.notes = notes + result.notes
        return result

    return ExtractionExecutionResult(
        extractor_name="text_spine",
        source_path=source_path,
        executed=False,
        succeeded=False,
        text=classified.processed_text,
        raw_text=classified.raw_text,
        warnings=[],
        notes=notes + ["no supported external extractor executed; using text_spine"],
    )
