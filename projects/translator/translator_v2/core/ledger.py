from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .enums import SegmentStatus
from .models import DocumentLedger


def ledger_path(output_path: str) -> Path:
    return Path(output_path).with_suffix(Path(output_path).suffix + ".v2.ledger.json")


def blocked_workfile_path(output_path: str) -> Path:
    return Path(output_path).with_suffix(Path(output_path).suffix + ".v2.blocked.txt")


def preview_path(output_path: str) -> Path:
    return Path(output_path).with_suffix(Path(output_path).suffix + ".v2.preview.txt")


def report_path(output_path: str) -> Path:
    return Path(output_path).with_suffix(Path(output_path).suffix + ".v2.report.json")


def write_live_preview(output_path: str, ledger: DocumentLedger) -> str:
    path = preview_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    pieces: list[str] = []
    unresolved_run = 0
    leaves = [
        segment
        for segment in sorted(ledger.segments, key=lambda item: item.sort_key())
        if not segment.child_segment_ids
    ]

    def flush_unresolved() -> None:
        nonlocal unresolved_run
        if unresolved_run <= 0:
            return
        if pieces and not pieces[-1].endswith("\n\n"):
            pieces.append("\n\n")
        pieces.append(f"[... {unresolved_run}개 미해결 세그먼트 생략 ...]\n\n")
        unresolved_run = 0

    for segment in leaves:
        if segment.status == SegmentStatus.ACCEPTED.value:
            flush_unresolved()
            pieces.append(segment.translated_text)
            if segment.separator_after:
                pieces.append(segment.separator_after)
            continue
        if segment.status != SegmentStatus.SUPERSEDED_BY_CHILDREN.value:
            unresolved_run += 1

    flush_unresolved()
    rendered = "".join(pieces).rstrip()
    path.write_text((rendered + "\n") if rendered else "", encoding="utf-8")
    return str(path)


def save_ledger(output_path: str, ledger: DocumentLedger) -> None:
    path = ledger_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(ledger.to_dict(), handle, ensure_ascii=False, indent=2)
    write_live_preview(output_path, ledger)


def load_ledger(output_path: str) -> Optional[DocumentLedger]:
    path = ledger_path(output_path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return DocumentLedger.from_dict(payload)
