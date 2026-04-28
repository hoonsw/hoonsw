from __future__ import annotations

import re
from typing import Any, Dict, List

from readability_gate import detect_readability_in_chunk
from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import SegmentRecord, V2Config
from translator_v2.validate.exact_copy import validate_exact_copy
from translator_v2.validate.numbers import validate_numbers
from translator_v2.validate.residue import validate_source_residue
from translator_v3.core.models import TranslationUnit, UnitValidationResult, V3Config


_UNEXPECTED_FOREIGN_SCRIPT_RE = re.compile(r"[\u0530-\u058F\u0590-\u05FF]+")


def _to_issue_dict(issue) -> Dict[str, Any]:
    return {
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "segment_id": issue.segment_id,
        "expected": issue.expected,
        "observed": issue.observed,
        "action": issue.action,
    }


def _validate_wrapper_leak(unit: TranslationUnit, translated_text: str) -> List[Dict[str, Any]]:
    text = translated_text.strip()
    if "[[SEGMENT" not in text and "[[END SEGMENT" not in text:
        return []
    return [
        {
            "issue_type": "wrapper_leak",
            "severity": IssueSeverity.CRITICAL.value,
            "segment_id": unit.unit_id,
            "expected": "plain translated text only",
            "observed": "segment wrapper markers leaked into translated text",
            "action": "retry_unit",
        }
    ]


def _is_reference_like_body_line(unit: TranslationUnit) -> bool:
    if unit.unit_class != "body_paragraph":
        return False
    source = unit.source_text.strip()
    if "\n" in source:
        return False
    if not re.search(r"[A-Za-z]", source):
        return False
    if not re.search(r"(?:\(\d+\)|（[０-９\d]+）|, .*?, \d{4}|(?:^|[ ,])(?:p|pp|ch)\.\d)", source):
        return False
    return True


def _soften_reference_number_issues(
    unit: TranslationUnit,
    translated_text: str,
    number_issues: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not _is_reference_like_body_line(unit):
        return number_issues
    softened: List[Dict[str, Any]] = []
    for payload in number_issues:
        if (
            payload["issue_type"] == "missing_number"
            and payload["severity"] == IssueSeverity.CRITICAL.value
            and re.fullmatch(r"\d+\.", payload["expected"])
        ):
            digits = re.sub(r"\D", "", payload["expected"])
            if digits and digits in translated_text:
                payload = dict(payload)
                payload["severity"] = IssueSeverity.WARNING.value
                payload["action"] = "review_only"
        softened.append(payload)
    return softened


def _sentence_count(text: str, lang: str) -> int:
    normalized = text.strip()
    if not normalized:
        return 0
    if lang == "ja":
        parts = re.split(r"(?<=[。！？])\s*", normalized)
    else:
        parts = re.split(r"(?<=[.!?])\s+", normalized)
    sentences = [part.strip() for part in parts if part.strip()]
    if len(sentences) > 1:
        return len(sentences)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    return len(lines) if len(lines) > 1 else 1


def _validate_completeness(unit: TranslationUnit, translated_text: str, config: V3Config) -> List[Dict[str, Any]]:
    if unit.unit_class != "body_paragraph" or config.backend_name == "echo":
        return []

    source_sentences = _sentence_count(unit.source_text, unit.source_lang)
    translated_sentences = _sentence_count(translated_text, unit.target_lang)
    source_chars = len(unit.source_text.strip())
    target_chars = len(translated_text.strip())
    char_ratio = target_chars / max(1, source_chars)
    issues: list[dict[str, Any]] = []

    if source_sentences >= 3 and translated_sentences <= max(1, source_sentences // 2):
        issues.append(
            {
                "issue_type": "coverage_drop",
                "severity": IssueSeverity.REPAIR_TRIGGER.value,
                "segment_id": unit.unit_id,
                "expected": f"roughly preserve source coverage across {source_sentences} source sentences",
                "observed": f"only {translated_sentences} target sentences detected",
                "action": "retry_unit",
            }
        )

    if (
        source_sentences == 2
        and translated_sentences == 1
        and source_chars >= 80
        and char_ratio < 0.3
    ):
        issues.append(
            {
                "issue_type": "coverage_drop",
                "severity": IssueSeverity.REPAIR_TRIGGER.value,
                "segment_id": unit.unit_id,
                "expected": "preserve both source sentences or retain equivalent coverage",
                "observed": (
                    f"2 source sentences compressed to 1 target sentence "
                    f"(source_chars={source_chars}, target_chars={target_chars})"
                ),
                "action": "retry_unit",
            }
        )

    if (
        source_sentences >= 3
        and translated_sentences == source_sentences - 1
        and source_chars >= 180
        and char_ratio < 0.4
    ):
        issues.append(
            {
                "issue_type": "coverage_drop",
                "severity": IssueSeverity.REPAIR_TRIGGER.value,
                "segment_id": unit.unit_id,
                "expected": f"roughly preserve coverage across {source_sentences} source sentences",
                "observed": (
                    f"only {translated_sentences} target sentences detected "
                    f"(source_chars={source_chars}, target_chars={target_chars})"
                ),
                "action": "retry_unit",
            }
        )

    if source_chars >= 500 and target_chars < max(80, int(source_chars * 0.12)):
        issues.append(
            {
                "issue_type": "severe_undertranslation",
                "severity": IssueSeverity.REPAIR_TRIGGER.value,
                "segment_id": unit.unit_id,
                "expected": "translation length consistent with source coverage",
                "observed": f"source_chars={source_chars}, target_chars={target_chars}",
                "action": "retry_unit",
            }
        )

    return issues


def _validate_heading_numbers(unit: TranslationUnit, translated_text: str) -> List[Dict[str, Any]]:
    if unit.unit_class != "heading":
        return []
    source_numbers = re.findall(r"\d+", unit.source_text)
    if not source_numbers:
        return []
    translated_numbers = re.findall(r"\d+", translated_text)
    if source_numbers == translated_numbers:
        return []
    return [
        {
            "issue_type": "heading_number_mismatch",
            "severity": IssueSeverity.CRITICAL.value,
            "segment_id": unit.unit_id,
            "expected": f"heading numbers {source_numbers}",
            "observed": f"translated numbers {translated_numbers or 'none'}",
            "action": "retry_unit",
        }
    ]


def _validate_unexpected_foreign_script(unit: TranslationUnit, translated_text: str) -> List[Dict[str, Any]]:
    if unit.target_lang != "ko":
        return []
    hits = sorted(set(_UNEXPECTED_FOREIGN_SCRIPT_RE.findall(translated_text)))
    if not hits:
        return []
    return [
        {
            "issue_type": "unexpected_foreign_script",
            "severity": IssueSeverity.REPAIR_TRIGGER.value,
            "segment_id": unit.unit_id,
            "expected": "natural Korean output without stray Armenian/Hebrew script fragments",
            "observed": f"unexpected foreign-script fragment(s): {', '.join(hits[:3])}",
            "action": "retry_unit",
        }
    ]


def validate_unit_output(unit: TranslationUnit, translated_text: str, config: V3Config) -> UnitValidationResult:
    if not translated_text.strip():
        return UnitValidationResult(
            ok=False,
            issues=[
                {
                    "issue_type": "empty_output",
                    "severity": IssueSeverity.CRITICAL.value,
                    "segment_id": unit.unit_id,
                    "expected": "non-empty output",
                    "observed": "empty output",
                    "action": "retry_unit",
                }
            ],
        )

    segment = SegmentRecord(
        segment_id=unit.unit_id,
        parent_segment_id=None,
        child_segment_ids=[],
        chapter_id=unit.chapter_id,
        source_order_index=unit.source_order_index,
        source_start_offset=0,
        source_end_offset=len(unit.source_text),
        source_text=unit.source_text,
        source_hash="",
        separator_after="\n\n",
    )

    v2_config = V2Config(source_lang=unit.source_lang, target_lang=unit.target_lang)
    issues = []
    issues.extend(_validate_wrapper_leak(unit, translated_text))
    issues.extend(_to_issue_dict(item) for item in validate_exact_copy(unit.unit_id, unit.source_text, translated_text))

    number_issues = validate_numbers(unit.unit_id, unit.source_text, translated_text)
    number_issues = [_to_issue_dict(item) for item in number_issues]
    number_issues = _soften_reference_number_issues(unit, translated_text, number_issues)
    if unit.unit_class in {"footnote", "bibliography_or_catalog"}:
        softened = []
        for item in number_issues:
            payload = dict(item)
            if payload["severity"] == IssueSeverity.CRITICAL.value:
                payload["severity"] = IssueSeverity.WARNING.value
                payload["action"] = "review_only"
            softened.append(payload)
        issues.extend(softened)
    else:
        issues.extend(number_issues)

    issues.extend(_validate_completeness(unit, translated_text, config))
    issues.extend(_validate_heading_numbers(unit, translated_text))
    issues.extend(_validate_unexpected_foreign_script(unit, translated_text))

    residue_issues, residue_warnings = validate_source_residue(
        segment_id=unit.unit_id,
        translated_text=translated_text,
        source_lang=unit.source_lang,
        source_text=unit.source_text,
        glossary_payload={},
        protected_regexes=[],
    )
    issues.extend(_to_issue_dict(item) for item in residue_issues)

    warnings: List[str] = list(residue_warnings)
    readability = detect_readability_in_chunk(translated_text, target_lang=v2_config.target_lang)
    if readability.get("severity") == "critical":
        for finding in readability.get("findings", [])[:3]:
            if finding.get("severity") != "critical":
                continue
            issues.append(
                {
                    "issue_type": finding.get("type", "readability_issue"),
                    "severity": IssueSeverity.REPAIR_TRIGGER.value,
                    "segment_id": unit.unit_id,
                    "expected": "readable output",
                    "observed": ", ".join(finding.get("examples", [])[:2]),
                    "action": "retry_unit",
                }
            )
    elif readability.get("severity") == "warning":
        warnings.extend(f"readability warning: {finding.get('type')}" for finding in readability.get("findings", []))

    if unit.unit_class == "heading":
        warnings = [warning for warning in warnings if not warning.startswith("residue warning:")]
        if readability.get("severity") == "warning":
            warnings.append("heading readability warning")

    critical_like = {IssueSeverity.CRITICAL.value, IssueSeverity.REPAIR_TRIGGER.value}
    ok = not any(item["severity"] in critical_like for item in issues)
    return UnitValidationResult(ok=ok, issues=issues, warnings=warnings)
