#!/usr/bin/env python3
"""
Genre presets for the translation workbench.

Each preset adjusts:
- sentence_split_policy: how aggressively to split sentences
- dialogue_priority: how much to prioritize preserving dialogue flow
- style_consistency: strength of style consistency enforcement
- explanation_ban: how strictly to forbid adding explanations
- review_tolerance: tolerance for structural review deviations
- retry_sensitivity: how aggressively to flag chunks for retry
- extra_rules: additional rules appended to the system prompt

Presets are loaded into TranslationConfig and affect:
1. Chunking hints (via chunk_boundary_hints)
2. Translation prompt modifiers (via prompt_modifier)
3. Review thresholds (via review_threshold_pct, review_threshold_abs)
4. Retry thresholds (via retry_score_threshold, max_retries)
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class GenrePreset:
    """A genre preset that tunes translation/review/retry behavior."""

    name: str
    display_name: str
    description: str

    # Chunking
    chunk_boundary_hints: str = "paragraph"  # paragraph | scene | argument

    # Translation prompt modifier (appended to system prompt)
    prompt_modifier: str = ""

    # Review thresholds
    review_threshold_pct: float = 0.3   # % sentence count difference tolerance
    review_threshold_abs: int = 5       # absolute sentence count tolerance

    # Retry
    retry_score_threshold: float = 0.6  # minimum quality score to skip retry
    max_auto_retries: int = 2           # max retry attempts per chunk
    auto_split_on_retry: bool = True    # split large chunks on retry

    # Extra rules text (appended to translation rules)
    extra_rules: str = ""


# ── Preset definitions ───────────────────────────────────────

JA_NOVEL = GenrePreset(
    name="ja_novel",
    display_name="일본어 소설",
    description="Japanese novel/fiction. Preserves narrative rhythm, dialogue flow, and character voice.",
    chunk_boundary_hints="scene",
    prompt_modifier="""[장르: 소설]
- 대사와 독백의 흐름을 끊지 마세요. 같은 대화 씬의 대사들은 한 청크에 유지하세요.
- 원문의 서술 호흡을 존중하세요. 짧은 문장이 반복되면 그대로, 긴 문장이면 그대로.
- 등장인물의 말투/어체를 일관되게 유지하세요.
- 문장 분할은 정말 필요할 때만 하세요.""",
    review_threshold_pct=0.25,
    review_threshold_abs=3,
    retry_score_threshold=0.55,
    max_auto_retries=2,
    extra_rules="소설 번역: 문장 경계를 가능한 원문과 1:1로 유지. 대사 톤 일관성 최우선.",
)

JA_ESSAY = GenrePreset(
    name="ja_essay",
    display_name="일본어 에세이",
    description="Japanese essay/personal writing. Preserves author's personal tone and reflective rhythm.",
    chunk_boundary_hints="paragraph",
    prompt_modifier="""[장르: 에세이]
- 저자의 개인적인 어조와 사색적 리듬을 유지하세요.
- 문장 구조를 최대한 유지하되, 가독성이 크게 떨어지면 최소한으로 조정하세요.
- 원문의 모호성은 보존하세요. 저자가 의도적으로 열어둔 문장은 그대로.""",
    review_threshold_pct=0.3,
    review_threshold_abs=4,
    retry_score_threshold=0.6,
    max_auto_retries=2,
    extra_rules="에세이 번역: 저자의 개인적 목소리를 살리는 것이 최우선.",
)

JA_PHILOSOPHY = GenrePreset(
    name="ja_philosophy",
    display_name="일본어 철학서",
    description="Japanese philosophy/theory. Preserves logical structure, technical terms, argument flow.",
    chunk_boundary_hints="argument",
    prompt_modifier="""[장르: 철학/이론서]
- 논리 구조와 논증 흐름을 정확히 유지하세요.
- 전문 용어는 일관되게 번역하세요. glossary를 반드시 따르세요.
- 원문의 복잡한 문장 구조가 논리적 의미를 담고 있다면 유지하세요.
- 각주, 인용, 참조 표현을 정확히 보존하세요.""",
    review_threshold_pct=0.2,
    review_threshold_abs=3,
    retry_score_threshold=0.5,
    max_auto_retries=3,
    extra_rules="철학서 번역: 용어 통일과 논증 구조 보존이 최우선. 문장 분할 최소화.",
)

EN_FICTION = GenrePreset(
    name="en_fiction",
    display_name="영어 소설",
    description="English fiction. Allows more flexible sentence restructuring for natural Korean.",
    chunk_boundary_hints="scene",
    prompt_modifier="""[장르: 영어 소설]
- 영어 긴 문장은 한국어에서 적절히 분할해도 됩니다.
- 대사의 톤과 등장인물의 말투를 일관되게 유지하세요.
- 서술 리듬을 최대한 살리되, 가독성을 위한 조정은 허용합니다.""",
    review_threshold_pct=0.35,
    review_threshold_abs=5,
    retry_score_threshold=0.6,
    max_auto_retries=2,
    extra_rules="영어 소설 번역: 대사 톤 일관성 유지. 긴 문장 적절히 분할 허용.",
)

EN_NONFICTION = GenrePreset(
    name="en_nonfiction",
    display_name="영어 논픽션",
    description="English nonfiction. Emphasizes clarity, logical structure, and accurate information.",
    chunk_boundary_hints="paragraph",
    prompt_modifier="""[장르: 영어 논픽션]
- 논리 구조를 명확히 유지하세요.
- 긴 문장은 적절히 분할하되, 논리적 연결을 유지하세요.
- 수치, 통계, 인용은 정확히 보존하세요.
- 전문 용어는 glossary를 따르세요.""",
    review_threshold_pct=0.3,
    review_threshold_abs=5,
    retry_score_threshold=0.6,
    max_auto_retries=2,
    extra_rules="논픽션 번역: 정보 정확도와 논리 구조 보존 우선. 가독성 개선 허용.",
)

EN_PHILOSOPHY = GenrePreset(
    name="en_philosophy",
    display_name="영어 철학서",
    description="English philosophy/theory. Strict term consistency, preserve argument structure.",
    chunk_boundary_hints="argument",
    prompt_modifier="""[장르: 영어 철학/이론서]
- 논증 구조를 정확히 보존하세요.
- 전문 용어는 반드시 glossary를 따르세요.
- 복잡한 문장은 논리가 보존되는 범위에서만 분할하세요.
- 각주, 인용, 참조를 정확히 유지하세요.""",
    review_threshold_pct=0.2,
    review_threshold_abs=3,
    retry_score_threshold=0.5,
    max_auto_retries=3,
    extra_rules="영어 철학서 번역: 용어 통일, 논증 구조 보존 최우선.",
)


# ── Registry ─────────────────────────────────────────────────

PRESETS: Dict[str, GenrePreset] = {
    "ja_novel": JA_NOVEL,
    "ja_essay": JA_ESSAY,
    "ja_philosophy": JA_PHILOSOPHY,
    "en_fiction": EN_FICTION,
    "en_nonfiction": EN_NONFICTION,
    "en_philosophy": EN_PHILOSOPHY,
}


def get_preset(name: str) -> Optional[GenrePreset]:
    """Get a preset by name. Returns None if not found."""
    return PRESETS.get(name)


def list_presets() -> list:
    """List all available presets as dicts."""
    return [
        {
            "name": p.name,
            "display_name": p.display_name,
            "description": p.description,
        }
        for p in PRESETS.values()
    ]
