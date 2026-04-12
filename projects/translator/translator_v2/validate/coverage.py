from __future__ import annotations

from typing import List

from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import ValidationIssue


def build_batch_parse_issues(segment_id: str, parse_result: dict) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if parse_result.get("parse_error"):
        issues.append(
            ValidationIssue(
                issue_type="parse_failure",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment_id,
                expected="exact segment wrapper mapping",
                observed=parse_result["parse_error"],
                action="retry_segment",
            )
        )
    if segment_id in parse_result.get("missing_ids", []):
        issues.append(
            ValidationIssue(
                issue_type="missing_segment",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment_id,
                expected=segment_id,
                observed="segment missing from response",
                action="retry_segment",
            )
        )
    if segment_id in parse_result.get("duplicate_ids", []):
        issues.append(
            ValidationIssue(
                issue_type="duplicate_segment",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment_id,
                expected="single wrapper",
                observed="duplicate wrapper for segment",
                action="retry_segment",
            )
        )
    return issues
