from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class V3Config:
    source_lang: str = "ja"
    target_lang: str = "ko"
    preset_name: Optional[str] = None
    preset_display_name: Optional[str] = None
    preset_prompt_modifier: str = ""
    preset_extra_rules: str = ""
    preset_chunk_boundary_hints: str = "paragraph"
    run_root: str = "runs_v3"
    classifier_version: str = "classifier-v1"
    graph_builder_version: str = "graph-v1"
    benchmark_version: str = "benchmark-v1"
    manifest_version: str = "manifest-v1"
    telemetry_version: str = "telemetry-v1"
    min_chapter_size: int = 500
    page_break_token: str = "\f"
    provider_name: str = "openai"
    model: str = "gpt-5.2-chat-latest"
    backend_name: str = "provider_api"
    mock_backend_fixture_path: Optional[str] = None
    reasoning_effort: str = "high"
    codex_timeout_seconds: int = 1800
    max_turns: int = 1
    batch_size: int = 3
    max_unit_chars: int = 900
    max_retry_per_unit: int = 1
    adequacy_review_mode: str = "all_body_units"
    adequacy_review_min_chars: int = 120
    adequacy_review_ratio_threshold: float = 0.6


@dataclass
class ClassifiedInput:
    input_path: str
    file_type: str
    input_class: str
    ingest_state: str
    configured_source_lang: str
    source_lang: str
    target_lang: str
    processed_text: str
    raw_text: Optional[str]
    warnings: List[str] = field(default_factory=list)
    analysis: Dict[str, Any] = field(default_factory=dict)
    planned_extractors: List[str] = field(default_factory=list)
    selected_spine: str = "text_spine"
    ocr_required: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentNode:
    node_id: str
    parent_id: Optional[str]
    node_type: str
    source_text: str
    start_offset: int
    end_offset: int
    reading_order: int
    page_index: Optional[int] = None
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentNode":
        return cls(**data)


@dataclass
class V3Telemetry:
    phase: str
    progress_mode: str
    pages_total: int
    pages_processed: int
    chapters_total: int
    blocks_total: int
    unresolved_blocks: int
    blocked_blocks: int
    low_confidence_pages: int
    warnings_count: int
    reroute_count: int
    ocr_repair_count: int
    adequacy_review_status: str
    preview_path: str
    last_activity_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "V3Telemetry":
        return cls(**data)


@dataclass
class ExtractorCapability:
    name: str
    kind: str
    available: bool
    module_path: Optional[str] = None
    cli_path: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractorCapability":
        return cls(**data)


@dataclass
class ExtractionCandidate:
    name: str
    role: str
    available: bool
    selected: bool
    reason: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionCandidate":
        return cls(**data)


@dataclass
class ExtractionTrace:
    strategy: str
    planned_extractor: str
    resolved_owner: str
    fallback_reason: str
    execution_mode: str
    integration_status: str
    external_execution_attempted: bool
    external_execution_succeeded: bool
    ownership_mode: str
    reroute_budget_per_document: int
    reroute_budget_per_page: int
    cache_key: str
    preprocessors: List[str] = field(default_factory=list)
    candidates: List[ExtractionCandidate] = field(default_factory=list)
    probes: List[ExtractorCapability] = field(default_factory=list)
    page_owners: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "planned_extractor": self.planned_extractor,
            "resolved_owner": self.resolved_owner,
            "fallback_reason": self.fallback_reason,
            "execution_mode": self.execution_mode,
            "integration_status": self.integration_status,
            "external_execution_attempted": self.external_execution_attempted,
            "external_execution_succeeded": self.external_execution_succeeded,
            "ownership_mode": self.ownership_mode,
            "reroute_budget_per_document": self.reroute_budget_per_document,
            "reroute_budget_per_page": self.reroute_budget_per_page,
            "cache_key": self.cache_key,
            "preprocessors": self.preprocessors,
            "candidates": [item.to_dict() for item in self.candidates],
            "probes": [item.to_dict() for item in self.probes],
            "page_owners": self.page_owners,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionTrace":
        return cls(
            strategy=data["strategy"],
            planned_extractor=data["planned_extractor"],
            resolved_owner=data["resolved_owner"],
            fallback_reason=data.get("fallback_reason", ""),
            execution_mode=data["execution_mode"],
            integration_status=data.get("integration_status", "preflight_only"),
            external_execution_attempted=data.get("external_execution_attempted", False),
            external_execution_succeeded=data.get("external_execution_succeeded", False),
            ownership_mode=data["ownership_mode"],
            reroute_budget_per_document=data["reroute_budget_per_document"],
            reroute_budget_per_page=data["reroute_budget_per_page"],
            cache_key=data["cache_key"],
            preprocessors=list(data.get("preprocessors", [])),
            candidates=[ExtractionCandidate.from_dict(item) for item in data.get("candidates", [])],
            probes=[ExtractorCapability.from_dict(item) for item in data.get("probes", [])],
            page_owners=dict(data.get("page_owners", {})),
            notes=list(data.get("notes", [])),
        )


@dataclass
class DocumentGraph:
    version: int
    run_id: str
    artifact_dir: str
    input_path: str
    input_class: str
    ingest_state: str
    configured_source_lang: str
    source_lang: str
    target_lang: str
    selected_spine: str
    planned_extractors: List[str]
    extraction_plan: str
    extraction_trace: Optional[ExtractionTrace] = None
    warnings: List[str] = field(default_factory=list)
    analysis: Dict[str, Any] = field(default_factory=dict)
    nodes: List[DocumentNode] = field(default_factory=list)
    telemetry: Optional[V3Telemetry] = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "artifact_dir": self.artifact_dir,
            "input_path": self.input_path,
            "input_class": self.input_class,
            "ingest_state": self.ingest_state,
            "configured_source_lang": self.configured_source_lang,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "selected_spine": self.selected_spine,
            "planned_extractors": self.planned_extractors,
            "extraction_plan": self.extraction_plan,
            "extraction_trace": self.extraction_trace.to_dict() if self.extraction_trace else None,
            "warnings": self.warnings,
            "analysis": self.analysis,
            "nodes": [node.to_dict() for node in self.nodes],
            "telemetry": self.telemetry.to_dict() if self.telemetry else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentGraph":
        return cls(
            version=data["version"],
            run_id=data["run_id"],
            artifact_dir=data["artifact_dir"],
            input_path=data["input_path"],
            input_class=data["input_class"],
            ingest_state=data["ingest_state"],
            configured_source_lang=data["configured_source_lang"],
            source_lang=data["source_lang"],
            target_lang=data["target_lang"],
            selected_spine=data["selected_spine"],
            planned_extractors=list(data.get("planned_extractors", [])),
            extraction_plan=data["extraction_plan"],
            extraction_trace=ExtractionTrace.from_dict(data["extraction_trace"])
            if data.get("extraction_trace")
            else None,
            warnings=list(data.get("warnings", [])),
            analysis=dict(data.get("analysis", {})),
            nodes=[DocumentNode.from_dict(item) for item in data.get("nodes", [])],
            telemetry=V3Telemetry.from_dict(data["telemetry"]) if data.get("telemetry") else None,
        )


@dataclass
class TranslationUnit:
    unit_id: str
    node_id: str
    chapter_id: str
    source_order_index: int
    node_reading_order: int
    unit_index_within_node: int
    unit_class: str
    source_text: str
    source_lang: str
    target_lang: str
    status: str = "PENDING"
    translated_text: str = ""
    attempt_count: int = 0
    warnings: List[str] = field(default_factory=list)
    validation_errors: List[Dict[str, Any]] = field(default_factory=list)
    telemetry: Dict[str, Any] = field(default_factory=dict)
    backend_name: str = ""
    provider_name: str = ""
    model_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TranslationUnit":
        return cls(**data)


@dataclass
class V3RunResult:
    run_id: str
    status: str
    input_path: str
    output_path: str
    artifact_dir: str
    accepted_units: int
    blocked_units: int
    total_units: int
    preview_path: str
    review_bundle_path: str
    translated_output_path: str
    final_output_written: bool
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UnitValidationResult:
    ok: bool
    issues: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class BenchmarkCase:
    case_id: str
    label: str
    input_path: str
    source_lang: str
    tags: List[str] = field(default_factory=list)
    expected_input_class: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, base_dir: Optional[Path] = None) -> "BenchmarkCase":
        input_path = data["input_path"]
        if base_dir is not None and not Path(input_path).is_absolute():
            input_path = str((base_dir / input_path).resolve())
        return cls(
            case_id=data["case_id"],
            label=data["label"],
            input_path=input_path,
            source_lang=data.get("source_lang", "ja"),
            tags=list(data.get("tags", [])),
            expected_input_class=data.get("expected_input_class"),
            notes=data.get("notes", ""),
        )


@dataclass
class BenchmarkCaseResult:
    case_id: str
    label: str
    input_path: str
    input_class: str
    ingest_state: str
    source_lang: str
    planned_extractor: str
    resolved_owner: str
    integration_status: str
    external_execution_attempted: bool
    external_execution_succeeded: bool
    pages_total: int
    chapters_total: int
    blocks_total: int
    warnings_count: int
    elapsed_ms: int
    expected_input_class: Optional[str] = None
    expected_match: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BenchmarkSuiteReport:
    version: str
    manifest_path: str
    case_results: List[BenchmarkCaseResult]
    summary: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "manifest_path": self.manifest_path,
            "case_results": [result.to_dict() for result in self.case_results],
            "summary": self.summary,
        }
