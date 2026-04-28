from __future__ import annotations

import re
from typing import Dict, List

from translator_v3.core.models import TranslationUnit


_SINGLE_SEGMENT_WRAPPER_RE = re.compile(
    r"^\s*\[\[SEGMENT\s+[A-Za-z0-9._-]+\]\]\s*(.*?)\s*\[\[END SEGMENT\s+[A-Za-z0-9._-]+\]\]\s*$",
    re.DOTALL,
)


def _sort_key(unit: TranslationUnit) -> tuple[int, int, str]:
    return (unit.node_reading_order, unit.unit_index_within_node, unit.unit_id)


def _renderable_text(text: str) -> str:
    stripped = text.strip()
    match = _SINGLE_SEGMENT_WRAPPER_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def assemble_output(units: List[TranslationUnit]) -> str:
    pieces: list[str] = []
    current_node_id: str | None = None
    current_parts: list[str] = []
    current_unit_class: str | None = None

    def flush() -> None:
        nonlocal current_node_id, current_parts, current_unit_class
        if not current_parts:
            current_node_id = None
            current_unit_class = None
            return
        if current_unit_class == "heading":
            pieces.append(" ".join(part for part in current_parts if part).strip())
        else:
            pieces.append(" ".join(part for part in current_parts if part).strip())
        pieces.append("\n\n")
        current_node_id = None
        current_parts = []
        current_unit_class = None

    for unit in sorted(units, key=_sort_key):
        if current_node_id is not None and unit.node_id != current_node_id:
            flush()

        if current_node_id is None:
            current_node_id = unit.node_id
            current_unit_class = unit.unit_class

        if unit.status == "ACCEPTED":
            current_parts.append(_renderable_text(unit.translated_text))
        elif unit.status == "BLOCKED":
            current_parts.append("[... 미해결 번역 단위 생략 ...]")

    flush()
    rendered = "".join(pieces).strip()
    return (rendered + "\n") if rendered else ""


def build_review_bundle(units: List[TranslationUnit]) -> Dict[str, object]:
    return {
        "units": [unit.to_dict() for unit in sorted(units, key=_sort_key)],
        "summary": {
            "total_units": len(units),
            "accepted_units": sum(1 for unit in units if unit.status == "ACCEPTED"),
            "blocked_units": sum(1 for unit in units if unit.status == "BLOCKED"),
            "warning_units": sum(1 for unit in units if unit.warnings),
        },
    }
