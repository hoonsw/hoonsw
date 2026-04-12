from __future__ import annotations

import re
from typing import List, Tuple

from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import ValidationIssue


_META_PATTERNS = [
    re.compile(r"^\s*(translation|translated text|here is the translation)\s*[:：-]", re.IGNORECASE),
    re.compile(r"^\s*(번역|번역문)\s*[:：-]"),
]


def validate_basic_format(segment_id: str, translated_text: str) -> Tuple[List[ValidationIssue], List[str]]:
    issues: List[ValidationIssue] = []
    warnings: List[str] = []
    text = translated_text.strip()

    if "[[SEGMENT" in text or "[[END SEGMENT" in text:
        issues.append(
            ValidationIssue(
                issue_type="wrapper_leak",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment_id,
                observed="wrapper marker leaked into translated text",
                expected="plain translated text only",
                action="retry_segment",
            )
        )

    for pattern in _META_PATTERNS:
        if pattern.search(text):
            issues.append(
                ValidationIssue(
                    issue_type="meta_text_leak",
                    severity=IssueSeverity.CRITICAL.value,
                    segment_id=segment_id,
                    observed=text[:80],
                    expected="translation only",
                    action="retry_segment",
                )
            )
            break

    bracket_pairs = [("(", ")"), ("[", "]"), ("{", "}"), ("「", "」"), ("『", "』")]
    for opening, closing in bracket_pairs:
        if text.count(opening) != text.count(closing):
            warnings.append(f"unbalanced bracket pair: {opening}{closing}")

    quote_chars = ['"', "“", "”", "'"]
    for quote in quote_chars:
        if text.count(quote) % 2 == 1:
            warnings.append(f"odd quote count: {quote}")

    return issues, warnings
