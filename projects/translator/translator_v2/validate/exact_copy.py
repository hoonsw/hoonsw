from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List

from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import ValidationIssue


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def validate_exact_copy(segment_id: str, source_text: str, translated_text: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    normalized_source = _normalize(source_text)
    normalized_target = _normalize(translated_text)
    if not normalized_source or not normalized_target:
        return issues

    if normalized_source == normalized_target:
        issues.append(
            ValidationIssue(
                issue_type="exact_copy",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment_id,
                expected="translated Korean output",
                observed="target text matches source",
                action="retry_segment",
            )
        )
        return issues

    if len(normalized_source) >= 80:
        similarity = SequenceMatcher(None, normalized_source, normalized_target).ratio()
        if similarity >= 0.96:
            issues.append(
                ValidationIssue(
                    issue_type="near_exact_copy",
                    severity=IssueSeverity.CRITICAL.value,
                    segment_id=segment_id,
                    expected="meaningful translation change",
                    observed=f"similarity={similarity:.3f}",
                    action="retry_segment",
                )
            )
    return issues
