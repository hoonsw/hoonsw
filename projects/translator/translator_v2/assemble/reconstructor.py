from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from translator_v2.core.enums import SegmentStatus
from translator_v2.core.ledger import blocked_workfile_path
from translator_v2.core.models import DocumentLedger, SegmentRecord


def _assembly_segments(ledger: DocumentLedger) -> Tuple[List[SegmentRecord], List[SegmentRecord]]:
    accepted = []
    unresolved = []
    for segment in sorted(ledger.segments, key=lambda item: item.sort_key()):
        if segment.child_segment_ids:
            continue
        if segment.status == SegmentStatus.ACCEPTED.value:
            accepted.append(segment)
        elif segment.status not in {SegmentStatus.SUPERSEDED_BY_CHILDREN.value}:
            unresolved.append(segment)
    return accepted, unresolved


def can_assemble_deliverable(ledger: DocumentLedger) -> bool:
    accepted, unresolved = _assembly_segments(ledger)
    if unresolved:
        return False
    return bool(accepted)


def assemble_deliverable_text(ledger: DocumentLedger) -> str:
    accepted, unresolved = _assembly_segments(ledger)
    if unresolved:
        unresolved_ids = ", ".join(item.segment_id for item in unresolved)
        raise RuntimeError(f"Cannot assemble deliverable with unresolved segments: {unresolved_ids}")

    pieces: List[str] = []
    for segment in accepted:
        pieces.append(segment.translated_text)
        if segment.separator_after:
            pieces.append(segment.separator_after)
    return "".join(pieces).rstrip() + "\n"


def write_blocked_workfile(output_path: str, ledger: DocumentLedger) -> str:
    path = blocked_workfile_path(output_path)
    accepted, unresolved = _assembly_segments(ledger)
    pieces: List[str] = []
    unresolved_ids = {segment.segment_id for segment in unresolved}
    for segment in sorted(ledger.segments, key=lambda item: item.sort_key()):
        if segment.child_segment_ids:
            continue
        if segment.segment_id in unresolved_ids:
            pieces.append(f"[BLOCKED:{segment.segment_id}] {segment.source_text}")
        elif segment.status == SegmentStatus.ACCEPTED.value:
            pieces.append(segment.translated_text)
        if segment.separator_after:
            pieces.append(segment.separator_after)
    path.write_text("".join(pieces).rstrip() + "\n", encoding="utf-8")
    return str(path)
