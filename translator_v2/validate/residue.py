from __future__ import annotations

import re
from typing import Dict, List, Pattern, Tuple

from quality_gate import detect_source_residue_in_chunk
from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import ValidationIssue


_URL_RE = re.compile(r"https?://\S+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:/|\\)[^\s]+")
_ISBN_DOI_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?:?\s*[\d-]{9,17}|doi:\s*10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b",
    re.IGNORECASE,
)


def _keep_terms(glossary_payload: Dict) -> List[str]:
    results: List[str] = []
    for term, value in glossary_payload.items():
        if isinstance(value, dict) and value.get("mode") == "keep":
            results.append(value.get("translation") or term)
            results.append(term)
    return [term for term in results if term]


def _protected_patterns(glossary_payload: Dict, protected_regexes: List[str] | None = None) -> List[Pattern[str]]:
    patterns = [_URL_RE, _EMAIL_RE, _PATH_RE, _ISBN_DOI_RE]
    for term in _keep_terms(glossary_payload):
        patterns.append(re.compile(re.escape(term)))
    for pattern in protected_regexes or []:
        patterns.append(re.compile(pattern))
    return patterns


def scrub_protected_spans(text: str, glossary_payload: Dict, protected_regexes: List[str] | None = None) -> str:
    cleaned = text
    for pattern in _protected_patterns(glossary_payload, protected_regexes=protected_regexes):
        cleaned = pattern.sub(" ", cleaned)
    return cleaned


def validate_source_residue(
    segment_id: str,
    translated_text: str,
    source_lang: str,
    source_text: str,
    glossary_payload: Dict,
    protected_regexes: List[str] | None = None,
) -> Tuple[List[ValidationIssue], List[str]]:
    stripped = scrub_protected_spans(
        translated_text,
        glossary_payload,
        protected_regexes=protected_regexes,
    )
    result = detect_source_residue_in_chunk(
        stripped,
        source_lang=source_lang,
        source_text=source_text,
    )
    issues: List[ValidationIssue] = []
    warnings: List[str] = []

    if result.get("severity") == "critical":
        for passage in result.get("passages", [])[:3]:
            issues.append(
                ValidationIssue(
                    issue_type="source_residue",
                    severity=IssueSeverity.CRITICAL.value,
                    segment_id=segment_id,
                    span=passage.get("text", ""),
                    expected="fully translated Korean output",
                    observed="source-language passage remained",
                    action="retry_segment",
                )
            )
    elif result.get("severity") in {"warning", "minor"}:
        warnings.append(
            f"source residue warning: severity={result.get('severity')} "
            f"count={len(result.get('passages', []))}"
        )

    return issues, warnings
