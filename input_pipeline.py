#!/usr/bin/env python3
"""
Input pipeline: handles TXT and PDF inputs, routing through appropriate
extraction and normalization paths.

Input states:
- clean_txt: plain text file, used directly
- pdf_text_extracted: text extracted from PDF via text layer
- ocr_raw: raw OCR output (when PDF text extraction fails)
- ocr_cleaned: OCR output after normalization/cleanup
"""

import re
from enum import Enum
from pathlib import Path
from typing import Optional

from normalizer import TextNormalizer


class InputState(Enum):
    CLEAN_TXT = "clean_txt"
    TXT_PDF_NORMALIZED = "txt_pdf_normalized"
    TXT_OCR_NORMALIZED = "txt_ocr_normalized"
    PDF_TEXT_EXTRACTED = "pdf_text_extracted"
    OCR_RAW = "ocr_raw"
    OCR_CLEANED = "ocr_cleaned"


class InputResult:
    """Result of input pipeline processing."""
    def __init__(self, text: str, state: InputState, source_path: str,
                 warnings: list = None, raw_text: str = None, analysis: dict = None):
        self.text = text
        self.state = state
        self.source_path = source_path
        self.warnings = warnings or []
        self.raw_text = raw_text  # pre-normalization text for comparison logging
        self.analysis = analysis or {}


def _detect_file_type(path: str) -> str:
    """Detect file type from extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".txt", ".text", ".md"):
        return "txt"
    # Default to txt for unknown extensions
    return "txt"


def _read_text_file(path: str) -> tuple[str, Optional[str]]:
    encodings = ("utf-8", "utf-8-sig", "cp932", "shift_jis", "utf-16")
    last_error = None
    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as handle:
                return handle.read(), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
        except LookupError as exc:
            last_error = exc
    raise UnicodeDecodeError(
        "text",
        b"",
        0,
        1,
        f"Unable to decode text file with supported encodings: {last_error}",
    )


def _extract_pdf_text(path: str) -> Optional[str]:
    """Extract text from PDF using text layer (pdfplumber or PyMuPDF).

    Returns extracted text or None if extraction fails or quality is too low.
    """
    # Try PyMuPDF (fitz) first — fast and reliable
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        text = "\n\n".join(pages)
        if _assess_text_quality(text) >= 0.5:
            return text
        return None
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: pdfplumber
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
        text = "\n\n".join(pages)
        if _assess_text_quality(text) >= 0.5:
            return text
        return None
    except ImportError:
        pass
    except Exception:
        pass

    return None


def _assess_text_quality(text: str) -> float:
    """Assess quality of extracted text (0.0 = garbage, 1.0 = perfect).

    Heuristic scoring based on:
    - Ratio of printable characters
    - Average line length
    - Presence of actual sentences
    """
    if not text or len(text.strip()) < 10:
        return 0.0

    lines = text.strip().split('\n')
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return 0.0

    # Check ratio of printable/meaningful characters
    total_chars = len(text)
    # Count alphanumeric + CJK + common punctuation
    meaningful = sum(1 for c in text if c.isalnum() or c in '。、！？.,!? \n\t')
    char_ratio = meaningful / total_chars if total_chars > 0 else 0

    # Average line length (very short lines suggest OCR artifacts)
    avg_len = sum(len(l) for l in non_empty) / len(non_empty)
    len_score = min(avg_len / 20.0, 1.0)  # normalize: 20+ chars/line = 1.0

    # Combined score
    return (char_ratio * 0.6) + (len_score * 0.4)


def _collect_text_metrics(text: str) -> dict:
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = normalized.split('\n')
    non_empty = [line.strip() for line in lines if line.strip()]
    avg_non_empty_len = (
        sum(len(line) for line in non_empty) / len(non_empty)
        if non_empty
        else 0.0
    )
    short_line_ratio = (
        sum(1 for line in non_empty if len(line) < 50) / len(non_empty)
        if non_empty
        else 0.0
    )
    line_counts: dict[str, int] = {}
    for line in non_empty:
        if len(line) <= 80:
            line_counts[line] = line_counts.get(line, 0) + 1
    repeated_short_line_occurrences = sum(
        count for count in line_counts.values() if count >= 3
    )
    spaced_letter_line_count = 0
    spaced_cjk_line_count = 0
    symbol_noise_line_count = 0
    for line in non_empty:
        tokens = line.split()
        short_upper_tokens = [token for token in tokens if re.fullmatch(r"[A-Z0-9&]{1,2}", token)]
        short_cjk_tokens = [
            token
            for token in tokens
            if re.fullmatch(r"[\u3040-\u30ff\u4e00-\u9fff]{1,2}", token)
        ]
        if len(tokens) >= 6 and len(short_upper_tokens) / max(1, len(tokens)) >= 0.7:
            spaced_letter_line_count += 1
        if len(tokens) >= 6 and len(short_cjk_tokens) / max(1, len(tokens)) >= 0.7:
            spaced_cjk_line_count += 1
        if len(line) <= 12 and not any(char.isalnum() for char in line):
            symbol_noise_line_count += 1

    latin_char_count = sum(
        1 for char in normalized if ("A" <= char <= "Z") or ("a" <= char <= "z")
    )
    cjk_char_count = sum(
        1
        for char in normalized
        if ("\u3040" <= char <= "\u30ff") or ("\u4e00" <= char <= "\u9fff")
    )

    return {
        "char_count": len(normalized),
        "non_empty_line_count": len(non_empty),
        "avg_non_empty_line_length": round(avg_non_empty_len, 2),
        "short_line_ratio": round(short_line_ratio, 4),
        "form_feed_count": normalized.count('\f'),
        "soft_hyphen_count": normalized.count('\u00ad'),
        "replacement_char_count": normalized.count('\ufffd'),
        "hyphenated_linebreak_count": len(re.findall(r"[A-Za-z]{2,}(?:-|\u00ad)\n[a-z]{2,}", normalized)),
        "repeated_short_line_occurrences": repeated_short_line_occurrences,
        "spaced_letter_line_count": spaced_letter_line_count,
        "spaced_cjk_line_count": spaced_cjk_line_count,
        "symbol_noise_line_count": symbol_noise_line_count,
        "latin_char_count": latin_char_count,
        "cjk_char_count": cjk_char_count,
    }


def _bounded_ratio(value: float, full_scale: float) -> float:
    if full_scale <= 0:
        return 0.0
    return max(0.0, min(value / full_scale, 1.0))


def _pdfish_signal_score(metrics: dict) -> float:
    score = 0.0
    score += 0.30 * _bounded_ratio(metrics["form_feed_count"], 2)
    score += 0.25 * _bounded_ratio(metrics["hyphenated_linebreak_count"], 25)
    score += 0.15 * _bounded_ratio(metrics["soft_hyphen_count"], 15)
    score += 0.15 * _bounded_ratio(metrics["repeated_short_line_occurrences"], 8)
    score += 0.15 * _bounded_ratio(max(metrics["short_line_ratio"] - 0.22, 0.0), 0.25)
    return round(min(score, 1.0), 4)


def _ocrish_signal_score(metrics: dict) -> float:
    score = 0.0
    score += 0.25 * _bounded_ratio(metrics["replacement_char_count"], 4)
    score += 0.20 * _bounded_ratio(metrics["symbol_noise_line_count"], 8)
    score += 0.15 * _bounded_ratio(metrics["spaced_letter_line_count"], 6)
    score += 0.15 * _bounded_ratio(metrics["spaced_cjk_line_count"], 6)
    score += 0.15 * _bounded_ratio(max(metrics["short_line_ratio"] - 0.30, 0.0), 0.25)
    score += 0.10 * _bounded_ratio(metrics["form_feed_count"], 2)
    return round(min(score, 1.0), 4)


def _layout_artifact_score(metrics: dict) -> float:
    score = 0.0
    score += 0.25 * _bounded_ratio(metrics["form_feed_count"], 2)
    score += 0.20 * _bounded_ratio(metrics["hyphenated_linebreak_count"], 20)
    score += 0.10 * _bounded_ratio(metrics["soft_hyphen_count"], 15)
    score += 0.10 * _bounded_ratio(metrics["repeated_short_line_occurrences"], 8)
    score += 0.10 * _bounded_ratio(metrics["spaced_letter_line_count"], 6)
    score += 0.05 * _bounded_ratio(metrics["spaced_cjk_line_count"], 6)
    score += 0.10 * _bounded_ratio(metrics["symbol_noise_line_count"], 8)
    score += 0.05 * _bounded_ratio(max(metrics["short_line_ratio"] - 0.18, 0.0), 0.30)
    score += 0.05 * _bounded_ratio(max(90.0 - metrics["avg_non_empty_line_length"], 0.0), 60.0)
    return round(min(score, 1.0), 4)


def _content_retention_ratio(baseline_text: str, candidate_text: str) -> float:
    baseline_len = max(1, len(baseline_text.strip()))
    return len(candidate_text.strip()) / baseline_len


def _candidate_summary(text: str) -> dict:
    metrics = _collect_text_metrics(text)
    return {
        "metrics": metrics,
        "quality_score": round(_assess_text_quality(text), 4),
        "layout_artifact_score": _layout_artifact_score(metrics),
    }


def _detect_dominant_source_lang(metrics: dict, configured_source_lang: str) -> tuple[str, str]:
    latin_chars = metrics.get("latin_char_count", 0)
    cjk_chars = metrics.get("cjk_char_count", 0)
    total_chars = max(1, metrics.get("char_count", 0))

    detected = configured_source_lang
    english_like = latin_chars >= max(40, cjk_chars * 4) and (latin_chars / total_chars) >= 0.20
    japanese_like = cjk_chars >= max(20, int(latin_chars * 1.2)) and (cjk_chars / total_chars) >= 0.15

    if english_like and not japanese_like:
        detected = "en"
    elif japanese_like and not english_like:
        detected = "ja"

    effective = detected if detected in {"ja", "en"} else configured_source_lang
    return detected, effective


def _choose_txt_processing(text: str, source_lang: str) -> tuple[InputState, str, list, dict]:
    raw_metrics = _collect_text_metrics(text)
    detected_source_lang, effective_source_lang = _detect_dominant_source_lang(raw_metrics, source_lang)
    normalizer = TextNormalizer(effective_source_lang)
    clean_text = normalizer.normalize_txt(text)
    clean_summary = _candidate_summary(clean_text)
    pdfish_score = _pdfish_signal_score(raw_metrics)
    ocrish_score = _ocrish_signal_score(raw_metrics)
    warnings: list[str] = []
    analysis = {
        "configured_source_lang": source_lang,
        "detected_source_lang": detected_source_lang,
        "effective_source_lang": effective_source_lang,
        "raw_metrics": raw_metrics,
        "pdfish_score": pdfish_score,
        "ocrish_score": ocrish_score,
        "clean_candidate": clean_summary,
    }

    if effective_source_lang != source_lang:
        warnings.append(
            f"Auto-corrected source_lang from {source_lang} to {effective_source_lang} based on input script dominance."
        )
    elif source_lang == "ja" and raw_metrics["latin_char_count"] > 2000 and raw_metrics["cjk_char_count"] < 50:
        warnings.append(
            "Input looks English-dominant while source_lang=ja. Consider source_lang=en or an English preset."
        )
    if source_lang == "en" and raw_metrics["cjk_char_count"] > 2000 and raw_metrics["latin_char_count"] < 500:
        warnings.append(
            "Input looks CJK-dominant while source_lang=en. Consider source_lang=ja for Japanese-source workflows."
        )

    if not (pdfish_score >= 0.55 or ocrish_score >= 0.60):
        analysis["selected_state"] = InputState.CLEAN_TXT.value
        return InputState.CLEAN_TXT, clean_text, warnings, analysis

    pdf_text = normalizer.normalize_pdf_text(text)
    pdf_summary = _candidate_summary(pdf_text)
    pdf_summary["content_retention_ratio"] = round(_content_retention_ratio(clean_text, pdf_text), 4)
    analysis["pdf_candidate"] = pdf_summary

    ocr_text = normalizer.normalize_ocr(text)
    ocr_summary = _candidate_summary(ocr_text)
    ocr_summary["content_retention_ratio"] = round(_content_retention_ratio(clean_text, ocr_text), 4)
    analysis["ocr_candidate"] = ocr_summary

    clean_artifact = clean_summary["layout_artifact_score"]
    clean_quality = clean_summary["quality_score"]

    def is_better(summary: dict, *, min_retention: float = 0.82, min_artifact_delta: float = 0.12) -> bool:
        return (
            summary["content_retention_ratio"] >= min_retention
            and summary["quality_score"] >= clean_quality - 0.08
            and summary["layout_artifact_score"] <= clean_artifact - min_artifact_delta
        )

    ocr_min_retention = 0.60 if source_lang == "ja" else 0.75

    choose_ocr = (
        ocrish_score >= pdfish_score + 0.10
        and is_better(ocr_summary, min_retention=ocr_min_retention, min_artifact_delta=0.05)
        and (
            not is_better(pdf_summary)
            or ocr_summary["layout_artifact_score"] <= pdf_summary["layout_artifact_score"] + 0.01
        )
    )
    if choose_ocr:
        warnings.append("Detected OCR-like layout artifacts in .txt input; applied OCR normalization.")
        analysis["selected_state"] = InputState.TXT_OCR_NORMALIZED.value
        return InputState.TXT_OCR_NORMALIZED, ocr_text, warnings, analysis

    if is_better(pdf_summary):
        warnings.append("Detected PDF-like layout artifacts in .txt input; applied PDF-text normalization.")
        analysis["selected_state"] = InputState.TXT_PDF_NORMALIZED.value
        return InputState.TXT_PDF_NORMALIZED, pdf_text, warnings, analysis

    analysis["selected_state"] = InputState.CLEAN_TXT.value
    return InputState.CLEAN_TXT, clean_text, warnings, analysis


def _run_ocr(path: str) -> Optional[str]:
    """Run OCR on a PDF file.

    TODO: Implement full OCR pipeline. Current placeholder uses
    Tesseract via pdf2image if available.
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(path)
        pages = []
        for img in images:
            text = pytesseract.image_to_string(img, lang='jpn+eng')
            pages.append(text)
        return "\n\n".join(pages)
    except ImportError:
        return None
    except Exception:
        return None


def process_input(path: str, source_lang: str = "ja",
                  force_ocr: bool = False) -> InputResult:
    """Process an input file through the appropriate pipeline.

    Args:
        path: Path to input file (TXT or PDF)
        source_lang: Source language code (ja, en)
        force_ocr: If True, skip text extraction and go straight to OCR for PDFs

    Returns:
        InputResult with processed text and metadata
    """
    path = str(Path(path).resolve())
    file_type = _detect_file_type(path)
    warnings = []

    if file_type == "txt":
        text, encoding_used = _read_text_file(path)
        state, cleaned, txt_warnings, analysis = _choose_txt_processing(text, source_lang)
        if encoding_used and encoding_used not in {"utf-8", "utf-8-sig"}:
            txt_warnings = [f"Decoded TXT input using {encoding_used}."] + list(txt_warnings)
        return InputResult(
            text=cleaned,
            state=state,
            source_path=path,
            warnings=txt_warnings,
            raw_text=text if cleaned != text else None,
            analysis=analysis,
        )

    if file_type == "pdf":
        raw_text = None
        state = None

        if not force_ocr:
            # Step 1: Try text extraction
            extracted = _extract_pdf_text(path)
            if extracted:
                raw_text = extracted
                state = InputState.PDF_TEXT_EXTRACTED
            else:
                warnings.append("PDF text extraction failed or low quality, attempting OCR")

        if raw_text is None:
            # Step 2: OCR
            ocr_text = _run_ocr(path)
            if ocr_text:
                raw_text = ocr_text
                state = InputState.OCR_RAW
            else:
                raise RuntimeError(
                    f"Cannot extract text from PDF: {path}. "
                    "Install PyMuPDF (pip install pymupdf) for text extraction, "
                    "or Tesseract + pdf2image for OCR."
                )

        raw_metrics = _collect_text_metrics(raw_text)
        detected_source_lang, effective_source_lang = _detect_dominant_source_lang(raw_metrics, source_lang)
        if effective_source_lang != source_lang:
            warnings.append(
                f"Auto-corrected source_lang from {source_lang} to {effective_source_lang} based on input script dominance."
            )
        normalizer = TextNormalizer(effective_source_lang)
        if state == InputState.OCR_RAW:
            cleaned = normalizer.normalize_ocr(raw_text)
            state = InputState.OCR_CLEANED
        else:
            cleaned = normalizer.normalize_pdf_text(raw_text)

        return InputResult(
            text=cleaned,
            state=state,
            source_path=path,
            warnings=warnings,
            raw_text=raw_text if cleaned != raw_text else None,
            analysis={
                "configured_source_lang": source_lang,
                "detected_source_lang": detected_source_lang,
                "effective_source_lang": effective_source_lang,
                "selected_state": state.value,
                "raw_metrics": raw_metrics,
                "cleaned_metrics": _collect_text_metrics(cleaned),
            },
        )

    # Fallback: treat as plain text
    text, encoding_used = _read_text_file(path)
    return InputResult(
        text=text,
        state=InputState.CLEAN_TXT,
        source_path=path,
        warnings=[f"Decoded TXT input using {encoding_used}."] if encoding_used and encoding_used not in {"utf-8", "utf-8-sig"} else [],
        analysis={"selected_state": InputState.CLEAN_TXT.value},
    )
