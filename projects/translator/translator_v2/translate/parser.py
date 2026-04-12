from __future__ import annotations

import re
from typing import Dict, Iterable, List


_WRAPPER_RE = re.compile(
    r"\[\[SEGMENT\s+([A-Za-z0-9._-]+)\]\]\s*(.*?)\s*\[\[END SEGMENT\s+\1\]\]",
    re.DOTALL,
)


def parse_segmented_response(response_text: str, expected_ids: Iterable[str]) -> dict:
    expected = list(expected_ids)
    expected_set = set(expected)
    text = response_text.strip()
    matches = list(_WRAPPER_RE.finditer(text))

    mapping: Dict[str, str] = {}
    duplicate_ids: List[str] = []
    unexpected_ids: List[str] = []
    observed_order: List[str] = []

    for match in matches:
        segment_id = match.group(1)
        content = match.group(2).strip()
        observed_order.append(segment_id)
        if segment_id in mapping:
            duplicate_ids.append(segment_id)
            continue
        mapping[segment_id] = content
        if segment_id not in expected_set:
            unexpected_ids.append(segment_id)

    missing_ids = [segment_id for segment_id in expected if segment_id not in mapping]
    extraneous_parts: List[str] = []
    last_end = 0
    for match in matches:
        prefix = text[last_end:match.start()]
        if prefix.strip():
            extraneous_parts.append(prefix.strip())
        last_end = match.end()
    suffix = text[last_end:]
    if suffix.strip():
        extraneous_parts.append(suffix.strip())

    parse_error = ""
    if not matches:
        parse_error = "No structured segment wrappers were returned."
    elif missing_ids:
        parse_error = f"Missing segment wrappers: {missing_ids}"
    elif duplicate_ids:
        parse_error = f"Duplicate segment wrappers: {sorted(set(duplicate_ids))}"
    elif unexpected_ids:
        parse_error = f"Unexpected segment wrappers: {sorted(set(unexpected_ids))}"
    elif observed_order != expected:
        parse_error = f"Reordered segment wrappers: observed {observed_order}, expected {expected}"
    elif extraneous_parts:
        parse_error = "Extraneous text outside wrappers."

    return {
        "ok": not parse_error,
        "units": mapping,
        "missing_ids": missing_ids,
        "duplicate_ids": sorted(set(duplicate_ids)),
        "unexpected_ids": sorted(set(unexpected_ids)),
        "observed_order": observed_order,
        "extraneous_text": extraneous_parts,
        "parse_error": parse_error,
    }
