from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .enums import DocumentStatus, IssueSeverity, SegmentStatus


@dataclass
class V2Config:
    source_lang: str = "ja"
    target_lang: str = "ko"
    provider_name: str = "openai"
    model: str = "gpt-5.2-chat-latest"
    generation_mode: str = "api"
    codex_worker_mode: str = "bootstrap"
    max_turns: int = 1
    batch_size: int = 3
    max_segment_chars: int = 900
    max_segment_sentences: int = 6
    hard_max_segment_chars: int = 1200
    local_retry_max: int = 2
    split_retry_max: int = 1
    max_total_repairs_per_segment: int = 3
    ingest_version: str = "ingest-v2"
    canonicalizer_version: str = "canon-v1"
    segmenter_version: str = "segmenter-v1"
    prompt_schema_version: str = "prompt-v1"
    config_path: str = "config.yaml"
    rules_path: str = "rules.md"
    glossary_path: str = "glossary.json"
    force_ocr: bool = False
    repair_batch_fallback_to_single: bool = True
    protected_regexes: List[str] = field(default_factory=list)
    codex_poll_interval_seconds: float = 0.25
    codex_claim_timeout_seconds: float = 30.0
    codex_result_timeout_seconds: float = 900.0
    codex_stale_requeue_max: int = 1


@dataclass
class LoadedDocument:
    input_path: str
    input_sha256: str
    normalized_source_text: str
    normalized_source_sha256: str
    source_lang: str
    target_lang: str
    ingest_state: str = "clean_txt"
    ingest_analysis: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ChapterRecord:
    chapter_id: str
    title: str
    start_offset: int
    end_offset: int
    source_order_index: int
    segment_ids: List[str] = field(default_factory=list)
    status: str = DocumentStatus.SEGMENTED.value

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChapterRecord":
        return cls(**data)


@dataclass
class ValidationIssue:
    issue_type: str
    severity: str
    segment_id: str
    span: str = ""
    expected: str = ""
    observed: str = ""
    action: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ValidationIssue":
        return cls(**data)


@dataclass
class RepairAttempt:
    segment_id: str
    attempt: int
    strategy: str
    source_hash: str
    result_status: str
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RepairAttempt":
        return cls(**data)


@dataclass
class SegmentRecord:
    segment_id: str
    parent_segment_id: Optional[str]
    child_segment_ids: List[str]
    chapter_id: str
    source_order_index: int
    source_start_offset: int
    source_end_offset: int
    source_text: str
    source_hash: str
    separator_after: str
    status: str = SegmentStatus.PENDING.value
    translated_text: str = ""
    attempt_count: int = 0
    repair_count: int = 0
    validation_errors: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    batch_id: Optional[str] = None
    child_order_index: Optional[int] = None
    generation_mode: Optional[str] = None
    backend_label: Optional[str] = None
    worker_id: Optional[str] = None
    accepted_task_id: Optional[str] = None
    accepted_task_epoch: Optional[int] = None
    accepted_claim_id: Optional[str] = None

    def add_issue(self, issue: ValidationIssue) -> None:
        self.validation_errors.append(issue.to_dict())

    def clear_issues(self) -> None:
        self.validation_errors = []
        self.warnings = []

    def is_leaf_for_assembly(self) -> bool:
        return self.status == SegmentStatus.ACCEPTED.value

    def sort_key(self) -> tuple:
        return (
            self.source_order_index,
            self.child_order_index if self.child_order_index is not None else 0,
            self.segment_id,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SegmentRecord":
        return cls(**data)


@dataclass
class DocumentRecord:
    version: int
    run_id: str
    input_path: str
    input_sha256: str
    normalized_source_sha256: str
    source_lang: str
    target_lang: str
    status: str
    config_fingerprint: str
    canonicalizer_version: str
    segmenter_version: str
    prompt_schema_version: str
    chapter_ids: List[str] = field(default_factory=list)
    segment_count: int = 0
    accepted_segment_count: int = 0
    output_path: str = ""
    generation_mode: str = "api"
    ingest_version: str = "ingest-v1"
    ingest_state: str = "clean_txt"
    ingest_analysis: Dict[str, Any] = field(default_factory=dict)
    artifact_dir: str = ""
    rules_sha256: str = ""
    glossary_sha256: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentRecord":
        return cls(**data)


@dataclass
class DocumentLedger:
    document: DocumentRecord
    chapters: List[ChapterRecord]
    segments: List[SegmentRecord]
    repair_attempts: List[RepairAttempt] = field(default_factory=list)
    blocked_manifest: Dict[str, Any] = field(default_factory=dict)

    def chapter_map(self) -> Dict[str, ChapterRecord]:
        return {chapter.chapter_id: chapter for chapter in self.chapters}

    def segment_map(self) -> Dict[str, SegmentRecord]:
        return {segment.segment_id: segment for segment in self.segments}

    def leaf_segments(self) -> List[SegmentRecord]:
        return [
            segment
            for segment in sorted(self.segments, key=lambda item: item.sort_key())
            if segment.status == SegmentStatus.ACCEPTED.value
        ]

    def to_dict(self) -> dict:
        return {
            "document": self.document.to_dict(),
            "chapters": [chapter.to_dict() for chapter in self.chapters],
            "segments": [segment.to_dict() for segment in self.segments],
            "repair_attempts": [attempt.to_dict() for attempt in self.repair_attempts],
            "blocked_manifest": self.blocked_manifest,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentLedger":
        return cls(
            document=DocumentRecord.from_dict(data["document"]),
            chapters=[ChapterRecord.from_dict(item) for item in data.get("chapters", [])],
            segments=[SegmentRecord.from_dict(item) for item in data.get("segments", [])],
            repair_attempts=[
                RepairAttempt.from_dict(item) for item in data.get("repair_attempts", [])
            ],
            blocked_manifest=data.get("blocked_manifest", {}),
        )


def resolve_path(path: str) -> str:
    return str(Path(path).resolve())
