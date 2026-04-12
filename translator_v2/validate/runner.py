from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Dict, List

from readability_gate import detect_readability_in_chunk
from translator_v2.core.enums import IssueSeverity
from translator_v2.core.models import SegmentRecord, V2Config, ValidationIssue
from translator_v2.validate.exact_copy import validate_exact_copy
from translator_v2.validate.format import validate_basic_format
from translator_v2.validate.numbers import validate_numbers
from translator_v2.validate.residue import validate_source_residue


@dataclass
class ValidationResult:
    ok: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _source_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _is_suspiciously_short(segment: SegmentRecord, translated_text: str, config: V2Config) -> bool:
    source_len = len(segment.source_text.strip())
    target_len = len(translated_text.strip())
    if source_len < 80:
        return False
    if len(translated_text.strip()) >= max(12, int(source_len * 0.12)):
        return False
    return True


def validate_segment_output(
    segment: SegmentRecord,
    translated_text: str,
    config: V2Config,
    glossary_payload: Dict,
) -> ValidationResult:
    issues: List[ValidationIssue] = []
    warnings: List[str] = []

    if _source_hash(segment.source_text) != segment.source_hash:
        issues.append(
            ValidationIssue(
                issue_type="source_hash_mismatch",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment.segment_id,
                expected=segment.source_hash,
                observed=_source_hash(segment.source_text),
                action="block_segment",
            )
        )

    if not translated_text.strip():
        issues.append(
            ValidationIssue(
                issue_type="empty_output",
                severity=IssueSeverity.CRITICAL.value,
                segment_id=segment.segment_id,
                expected="non-empty translated text",
                observed="empty output",
                action="retry_segment",
            )
        )
        return ValidationResult(ok=False, issues=issues, warnings=warnings)

    issues.extend(validate_exact_copy(segment.segment_id, segment.source_text, translated_text))
    issues.extend(validate_numbers(segment.segment_id, segment.source_text, translated_text))
    format_issues, format_warnings = validate_basic_format(segment.segment_id, translated_text)
    issues.extend(format_issues)
    warnings.extend(format_warnings)
    residue_issues, residue_warnings = validate_source_residue(
        segment_id=segment.segment_id,
        translated_text=translated_text,
        source_lang=config.source_lang,
        source_text=segment.source_text,
        glossary_payload=glossary_payload,
        protected_regexes=config.protected_regexes,
    )
    issues.extend(residue_issues)
    warnings.extend(residue_warnings)
    readability = detect_readability_in_chunk(
        translated_text,
        target_lang=config.target_lang,
    )
    if readability.get("severity") == "critical":
        for finding in readability.get("findings", [])[:3]:
            if finding.get("severity") != "critical":
                continue
            issues.append(
                ValidationIssue(
                    issue_type=finding.get("type", "readability_issue"),
                    severity=IssueSeverity.REPAIR_TRIGGER.value,
                    segment_id=segment.segment_id,
                    expected="readable Korean output without fragmented or syllable-spaced text",
                    observed=", ".join(finding.get("examples", [])[:2]) or "readability failure",
                    action="retry_segment",
                )
            )
    elif readability.get("severity") == "warning":
        for finding in readability.get("findings", []):
            warnings.append(f"readability warning: {finding.get('type')}")

    if _is_suspiciously_short(segment, translated_text, config):
        issues.append(
            ValidationIssue(
                issue_type="suspiciously_short",
                severity=IssueSeverity.REPAIR_TRIGGER.value,
                segment_id=segment.segment_id,
                expected="translation length roughly aligned with source scope",
                observed=f"source={len(segment.source_text.strip())}, target={len(translated_text.strip())}",
                action="retry_segment",
            )
        )

    critical_like = {
        IssueSeverity.CRITICAL.value,
        IssueSeverity.REPAIR_TRIGGER.value,
    }
    ok = not any(issue.severity in critical_like for issue in issues)
    return ValidationResult(ok=ok, issues=issues, warnings=warnings)
