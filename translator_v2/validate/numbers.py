from __future__ import annotations

import re
from typing import List

from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import ValidationIssue


_NUMBER_RE = re.compile(r"\d[\d,./:-]*")


def _normalize_number(token: str) -> str:
    return token.replace(",", "").strip()


def validate_numbers(segment_id: str, source_text: str, translated_text: str) -> List[ValidationIssue]:
    source_tokens = {_normalize_number(token) for token in _NUMBER_RE.findall(source_text)}
    target_tokens = {_normalize_number(token) for token in _NUMBER_RE.findall(translated_text)}

    issues: List[ValidationIssue] = []
    for token in sorted(source_tokens):
        if token in target_tokens:
            continue
        severity = (
            IssueSeverity.CRITICAL.value if len(re.sub(r"\D", "", token)) >= 2 else IssueSeverity.WARNING.value
        )
        issues.append(
            ValidationIssue(
                issue_type="missing_number",
                severity=severity,
                segment_id=segment_id,
                expected=token,
                observed="missing in translated text",
                action="retry_segment" if severity == IssueSeverity.CRITICAL.value else "warn",
            )
        )
    return issues
