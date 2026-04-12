from __future__ import annotations

from typing import List

from translator_v2.core.models import DocumentLedger


def build_review_report(ledger: DocumentLedger) -> str:
    lines: List[str] = []
    lines.append(f"run_id: {ledger.document.run_id}")
    lines.append(f"status: {ledger.document.status}")
    lines.append(f"accepted_segments: {ledger.document.accepted_segment_count}/{ledger.document.segment_count}")

    unresolved = [
        segment for segment in sorted(ledger.segments, key=lambda item: item.sort_key())
        if not segment.child_segment_ids and segment.status != "ACCEPTED"
    ]
    if unresolved:
        lines.append("unresolved_segments:")
        for segment in unresolved[:20]:
            lines.append(f"- {segment.segment_id}: {segment.status}")
            for issue in segment.validation_errors[:3]:
                lines.append(f"  * {issue.get('issue_type', 'unknown')}: {issue.get('observed', '')}")
    else:
        lines.append("unresolved_segments: none")

    return "\n".join(lines)
