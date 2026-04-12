from __future__ import annotations

import json
from pathlib import Path
from typing import Dict
from collections import Counter

from translator_v2.core.ledger import report_path
from translator_v2.core.models import DocumentLedger


def build_manifest(ledger: DocumentLedger) -> Dict:
    accepted_segments = [
        {
            "segment_id": segment.segment_id,
            "generation_mode": segment.generation_mode,
            "backend_label": segment.backend_label,
            "worker_id": segment.worker_id,
            "provider": segment.provider,
            "model": segment.model,
            "accepted_task_id": segment.accepted_task_id,
            "accepted_task_epoch": segment.accepted_task_epoch,
            "accepted_claim_id": segment.accepted_claim_id,
        }
        for segment in ledger.leaf_segments()
    ]
    unresolved = [
        {
            "segment_id": segment.segment_id,
            "status": segment.status,
            "issues": segment.validation_errors,
        }
        for segment in sorted(ledger.segments, key=lambda item: item.sort_key())
        if not segment.child_segment_ids and segment.status != "ACCEPTED"
    ]
    accepted_backend_counts = Counter(
        (segment.backend_label or segment.provider or "unknown")
        for segment in ledger.leaf_segments()
    )
    return {
        "pipeline_version": "translator_v2",
        "document": ledger.document.to_dict(),
        "repair_attempt_count": len(ledger.repair_attempts),
        "accepted_leaf_segments": len(ledger.leaf_segments()),
        "accepted_backend_counts": dict(sorted(accepted_backend_counts.items())),
        "accepted_segment_provenance": accepted_segments,
        "unresolved_leaf_segments": unresolved,
    }


def save_manifest(output_path: str, ledger: DocumentLedger) -> str:
    path = report_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(build_manifest(ledger), handle, ensure_ascii=False, indent=2)
    return str(path)
