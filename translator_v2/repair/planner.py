from __future__ import annotations

from dataclasses import dataclass

from translator_v2.core.models import SegmentRecord, V2Config
from translator_v2.segment.segmenter import split_into_sentences
@dataclass
class RepairDecision:
    strategy: str
    reason: str


def choose_repair_strategy(segment: SegmentRecord, config: V2Config) -> RepairDecision:
    if segment.repair_count >= config.max_total_repairs_per_segment:
        return RepairDecision("block", "repair budget exhausted")

    if segment.repair_count < config.local_retry_max:
        return RepairDecision("local_retry", "local retry budget available")

    can_split = (
        segment.parent_segment_id is None
        and not segment.child_segment_ids
        and config.split_retry_max > 0
        and (
            len(segment.source_text) > config.max_segment_chars
            or len(split_into_sentences(segment.source_text, config.source_lang)) > 1
        )
    )
    if can_split:
        return RepairDecision("split", "local retries exhausted and segment is splittable")

    return RepairDecision("block", "no remaining repair strategy")
