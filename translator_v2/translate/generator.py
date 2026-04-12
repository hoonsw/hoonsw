from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from providers import BaseProvider, ProviderResponse
from translator_v2.core.models import SegmentRecord, V2Config
from translator_v2.translate.parser import parse_segmented_response
from translator_v2.translate.prompt_builder import (
    build_batch_system_prompt,
    build_batch_user_prompt,
)


@dataclass
class BatchGenerationResult:
    batch_id: str
    response_text: str
    provider: str
    model: str
    parse_result: dict
    generation_mode: str = "api"
    backend_label: str = ""
    worker_id: str = ""
    task_id: str = ""
    task_epoch: int = 0
    claim_id: str = ""


async def generate_segment_batch(
    provider: BaseProvider,
    segments: List[SegmentRecord],
    rules_text: str,
    glossary_payload: Dict,
    config: V2Config,
    batch_id: str,
    failure_hints: Optional[Dict[str, str]] = None,
    context_map: Optional[Dict[str, str]] = None,
) -> BatchGenerationResult:
    system_prompt = build_batch_system_prompt(
        segments=segments,
        rules_text=rules_text,
        glossary_payload=glossary_payload,
        config=config,
        failure_hints=failure_hints,
    )
    user_prompt = build_batch_user_prompt(segments=segments, context_map=context_map)

    response: ProviderResponse = await provider.generate(system_prompt, user_prompt)
    parse_result = parse_segmented_response(
        response_text=response.text,
        expected_ids=[segment.segment_id for segment in segments],
    )
    return BatchGenerationResult(
        batch_id=batch_id,
        response_text=response.text,
        provider=response.provider,
        model=response.model,
        parse_result=parse_result,
        generation_mode="api",
        backend_label=response.provider,
    )
