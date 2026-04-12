#!/usr/bin/env python3
"""
Auto-retry engine for problem chunks.

Implements the phase 2 retry strategy:
1. Translate all chunks (phase 1 behavior, in translate.py)
2. Run structural review to identify problem chunks
3. Auto-retry problem chunks with enhanced prompts
4. If still problematic and chunk is large, auto-split and re-translate sub-chunks
5. Re-review, assess improvement
6. Stop when: improvement is marginal, max retries reached, or all chunks pass

This module is designed to be called after initial translation, or integrated
into an end-to-end pipeline. It preserves all phase 1 abstractions.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Optional

from translate import (
    TranslationConfig, translate_chunk_detailed,
    update_record_from_translation_result, load_rules, load_glossary,
    split_into_chunks, split_into_sentences,
    save_translation_artifacts,
    load_translation_artifacts, append_audit_log,
)
from review import (
    ReviewConfig, structural_review_chunk,
    ai_review_chunk,
)
from presets import GenrePreset, get_preset


# ── Chunk quality scoring ────────────────────────────────────

def score_chunk(review_result: dict) -> float:
    """Score a chunk from structural review result.

    Returns a float from 0.0 (terrible) to 1.0 (perfect).
    Used to decide whether to retry.
    """
    score = 1.0

    # Sentence mismatch penalty
    src = review_result.get('source_sentences', 0)
    tgt = review_result.get('target_sentences', 0)
    if src > 0:
        ratio = abs(tgt - src) / src
        if ratio > 1.0:
            score -= 0.18
        elif ratio > 0.6:
            score -= 0.1
        elif ratio > 0.3:
            score -= 0.04

    # Problem type penalties
    problems = review_result.get('problems', [])
    problem_weights = {
        'under_translate': 0.28,
        'source_residue': 0.4,
        'hallucination': 0.35,
        'meta_text_leak': 0.3,
        'structure_mismatch': 0.08,
        'over_split': 0.04,
        'tone_drift': 0.1,
        'name_inconsistency': 0.1,
        'ocr_noise': 0.2,
    }
    for problem in problems:
        score -= problem_weights.get(problem, 0.1)

    # Additional issue penalties
    if review_result.get('number_issues'):
        score -= 0.1 * min(len(review_result['number_issues']), 3)
    if review_result.get('meta_issues'):
        score -= 0.2
    if review_result.get('bracket_issues'):
        score -= 0.05
    if review_result.get('coverage_issues'):
        score -= 0.15
    if review_result.get('residue_issues'):
        score -= 0.25

    return max(0.0, min(1.0, score))


# ── Sub-chunk splitting ──────────────────────────────────────

def split_chunk_for_retry(
    chunk_text: str,
    source_lang: str,
    max_sub_size: int = 1000,
) -> List[str]:
    """Split a large chunk into smaller sub-chunks for retry.

    Used when a chunk consistently fails quality checks and may be
    too large/complex for a single translation call.
    """
    if len(chunk_text) <= max_sub_size:
        return [chunk_text]

    sentences = split_into_sentences(chunk_text, source_lang)
    if len(sentences) <= 1:
        return [chunk_text]

    sub_chunks = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_sub_size:
            sub_chunks.append(current)
            current = sentence
        else:
            current = sentence if not current else current + " " + sentence
    if current:
        sub_chunks.append(current)

    return sub_chunks


# ── Retry engine ─────────────────────────────────────────────

class RetryResult:
    """Result of a retry pass."""

    def __init__(self):
        self.retried_chunks: List[int] = []
        self.improved_chunks: List[int] = []
        self.split_chunks: List[int] = []
        self.still_problematic: List[int] = []
        self.scores_before: Dict[int, float] = {}
        self.scores_after: Dict[int, float] = {}

    def summary(self) -> dict:
        return {
            'retried': self.retried_chunks,
            'improved': self.improved_chunks,
            'split_retried': self.split_chunks,
            'still_problematic': self.still_problematic,
            'scores_before': self.scores_before,
            'scores_after': self.scores_after,
            'improvement_count': len(self.improved_chunks),
        }


async def retry_problem_chunks(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    problem_chunks: List[int],
    preset: Optional[GenrePreset] = None,
    max_retries: int = 2,
    score_threshold: float = 0.6,
    enable_split: bool = True,
    progress_callback=None,
) -> RetryResult:
    """Auto-retry problem chunks with optional split strategy.

    Args:
        input_path: Source text file path
        output_path: Translation output file path
        config: Translation config
        problem_chunks: Chunk numbers to retry (1-indexed)
        preset: Genre preset (overrides thresholds if provided)
        max_retries: Maximum retry attempts per chunk
        score_threshold: Minimum quality score to accept
        enable_split: Whether to try splitting large failing chunks
        progress_callback: async callable(event, data) for progress reporting

    Returns:
        RetryResult with details of what was retried and outcomes.
    """
    if preset:
        max_retries = preset.max_auto_retries
        score_threshold = preset.retry_score_threshold
        enable_split = preset.auto_split_on_retry

    result = RetryResult()
    rules = load_rules("rules.md")
    glossary_text, annotate_terms = load_glossary("glossary.json")

    # Load current artifacts
    artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)
    if not artifacts['ok']:
        if progress_callback:
            await progress_callback("error", "Cannot retry: metadata not valid")
        return result

    records = artifacts['records']

    # Re-read source to get chunks
    with open(input_path, 'r', encoding='utf-8') as f:
        source_text = f.read()
    chunks = split_into_chunks(source_text, config)

    if progress_callback:
        await progress_callback("retry_start", {
            'problem_chunks': problem_chunks,
            'max_retries': max_retries,
            'score_threshold': score_threshold,
        })

    for chunk_num in problem_chunks:
        idx = chunk_num - 1
        if idx < 0 or idx >= len(chunks):
            continue

        context, chunk_text = chunks[idx]
        original_translation = records[idx].get('translated_text', '')

        # Score before retry
        review_before = structural_review_chunk(
            chunk_num, chunk_text, original_translation,
            config.source_lang, config.target_lang,
            chunk_record=records[idx],
        )
        score_before = score_chunk(review_before)
        result.scores_before[chunk_num] = score_before

        if score_before >= score_threshold:
            # Already acceptable
            result.scores_after[chunk_num] = score_before
            continue

        result.retried_chunks.append(chunk_num)

        if progress_callback:
            await progress_callback("retry_chunk", {
                'chunk_num': chunk_num,
                'score_before': round(score_before, 2),
                'attempt': 1,
            })

        # Build enhanced prompt context from review issues
        retry_hint = _build_retry_hint(review_before, preset)

        # Attempt retries
        best_translation_result = {
            'text': original_translation,
            'segments': records[idx].get('segments', []),
            'validation': records[idx].get('validation', {}),
        }
        best_score = score_before

        for attempt in range(1, max_retries + 1):
            try:
                # Compute already_annotated for this chunk
                already_annotated = set()
                if annotate_terms:
                    for prev_idx in range(idx):
                        for term in annotate_terms:
                            if term in records[prev_idx].get('source_text', ''):
                                already_annotated.add(term)

                # Add retry hint to rules
                enhanced_rules = rules
                if retry_hint:
                    enhanced_rules = rules + "\n\n" + retry_hint
                if preset and preset.extra_rules:
                    enhanced_rules = enhanced_rules + "\n\n" + preset.extra_rules

                translation_result = await translate_chunk_detailed(
                    chunk_text, context, enhanced_rules, glossary_text, config,
                    annotate_terms=annotate_terms,
                    already_annotated=already_annotated,
                )
                translated = translation_result['text']

                # Score the new translation
                review_after = structural_review_chunk(
                    chunk_num, chunk_text, translated,
                    config.source_lang, config.target_lang,
                    chunk_record={'validation': translation_result.get('validation', {})},
                )
                new_score = score_chunk(review_after)

                if new_score > best_score:
                    best_translation_result = translation_result
                    best_score = new_score

                if progress_callback:
                    await progress_callback("retry_attempt_done", {
                        'chunk_num': chunk_num,
                        'attempt': attempt,
                        'score': round(new_score, 2),
                        'improved': new_score > score_before,
                    })

                if new_score >= score_threshold:
                    break

            except Exception as e:
                if progress_callback:
                    await progress_callback("retry_error", {
                        'chunk_num': chunk_num,
                        'attempt': attempt,
                        'error': str(e),
                    })

        # If still below threshold and chunk is large, try split strategy
        if best_score < score_threshold and enable_split and len(chunk_text) > 800:
            if progress_callback:
                await progress_callback("split_retry", {
                    'chunk_num': chunk_num,
                    'chunk_size': len(chunk_text),
                })

            split_translation = await _retry_with_split(
                chunk_text, context, rules, glossary_text, config,
                preset=preset,
                annotate_terms=annotate_terms,
                already_annotated=already_annotated if annotate_terms else set(),
            )

            if split_translation:
                split_review = structural_review_chunk(
                    chunk_num, chunk_text, split_translation['text'],
                    config.source_lang, config.target_lang,
                    chunk_record={'validation': split_translation.get('validation', {})},
                )
                split_score = score_chunk(split_review)

                if split_score > best_score:
                    best_translation_result = split_translation
                    best_score = split_score
                    result.split_chunks.append(chunk_num)

                    if progress_callback:
                        await progress_callback("split_result", {
                            'chunk_num': chunk_num,
                            'score': round(split_score, 2),
                            'improved': True,
                        })

        # Update record if improved
        result.scores_after[chunk_num] = best_score
        if best_score > score_before:
            update_record_from_translation_result(records[idx], best_translation_result)
            result.improved_chunks.append(chunk_num)
        else:
            result.still_problematic.append(chunk_num)

    # Save updated artifacts
    save_translation_artifacts(output_path, records, input_path, config)

    if progress_callback:
        await progress_callback("retry_done", result.summary())

    append_audit_log('retry', {
        'input_path': str(Path(input_path).resolve()),
        'output_path': str(Path(output_path).resolve()),
        **result.summary(),
    })

    return result


async def _retry_with_split(
    chunk_text: str,
    context: str,
    rules: str,
    glossary_text: str,
    config: TranslationConfig,
    preset: Optional[GenrePreset] = None,
    annotate_terms: list = None,
    already_annotated: set = None,
) -> Optional[dict]:
    """Try translating a chunk by splitting it into sub-chunks.

    Returns the concatenated translation plus merged validation, or None on failure.
    """
    sub_chunks = split_chunk_for_retry(chunk_text, config.source_lang)
    if len(sub_chunks) <= 1:
        return None  # No point splitting if it's already small

    enhanced_rules = rules
    if preset and preset.extra_rules:
        enhanced_rules = rules + "\n\n" + preset.extra_rules

    translations = []
    all_segments = []
    validation_issues = []
    invalid_sub_chunks = []
    sub_context = context

    for i, sub_chunk in enumerate(sub_chunks):
        try:
            translation_result = await translate_chunk_detailed(
                sub_chunk, sub_context, enhanced_rules, glossary_text, config,
                annotate_terms=annotate_terms,
                already_annotated=already_annotated,
            )
            translations.append(translation_result['text'])
            all_segments.extend(translation_result.get('segments', []))
            if not translation_result.get('validation', {}).get('ok', True):
                invalid_sub_chunks.append(i + 1)
                validation_issues.extend(translation_result['validation'].get('issues', []))
            # Use end of translated sub-chunk as context for next
            sub_context = sub_chunk[-500:] if len(sub_chunk) > 500 else sub_chunk
        except Exception:
            return None  # Bail on any sub-chunk failure

    return {
        'text': "\n\n".join(translations),
        'segments': all_segments,
        'validation': {
            'ok': not invalid_sub_chunks,
            'issues': validation_issues,
            'invalid_sub_chunks': invalid_sub_chunks,
            'invalid_units': invalid_sub_chunks,
        },
    }


def _build_retry_hint(review_result: dict, preset: Optional[GenrePreset] = None) -> str:
    """Build a retry hint from review issues to guide the retranslation."""
    hints = []

    problems = review_result.get('problems', [])
    if 'under_translate' in problems:
        hints.append("이전 번역에서 원문의 일부가 누락되었습니다. 모든 문장을 빠짐없이 번역하세요.")
    if 'source_residue' in problems:
        hints.append("이전 번역에서 원문 언어가 그대로 남았습니다. 번역해야 할 문장은 모두 한국어로 옮기세요.")
    if 'over_split' in problems:
        hints.append("이전 번역에서 문장이 과도하게 분할되었습니다. 원문의 문장 경계를 유지하세요.")
    if 'meta_text_leak' in problems:
        hints.append("이전 번역에 메타텍스트/지시문이 포함되었습니다. 번역문만 출력하세요.")
    if 'structure_mismatch' in problems:
        hints.append("이전 번역의 구조가 원문과 크게 달랐습니다. 문장 수와 구조를 맞추세요.")
    if 'hallucination' in problems:
        hints.append("이전 번역에 원문에 없는 내용이 추가되었습니다. 원문에 충실하세요.")

    if review_result.get('number_issues'):
        hints.append("숫자가 누락되었습니다. 원문의 모든 숫자를 보존하세요.")
    if review_result.get('bracket_issues'):
        hints.append("괄호/인용부호 균형이 맞지 않습니다. 정확히 맞추세요.")
    if review_result.get('coverage_issues'):
        hints.extend(review_result['coverage_issues'])
    if review_result.get('residue_issues'):
        hints.extend(review_result['residue_issues'])

    if preset and preset.prompt_modifier:
        hints.append(preset.prompt_modifier)

    if not hints:
        return ""

    return "[재번역 주의사항]\n" + "\n".join(f"- {h}" for h in hints)


# ── End-to-end pipeline helper ───────────────────────────────

async def translate_with_auto_retry(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    preset_name: Optional[str] = None,
    run_ai_review: bool = False,
    progress_callback=None,
) -> dict:
    """Run full translation with automatic retry on problem chunks.

    This is the phase 2 end-to-end pipeline:
    1. Translate all chunks (delegates to translate.translate_file)
    2. Structural review
    3. Auto-retry problem chunks
    4. Optionally run AI review on remaining problems
    5. Return summary

    Returns dict with translation and retry results.
    """
    from translate import translate_file

    preset = get_preset(preset_name) if preset_name else None

    # Step 1: Initial translation
    if progress_callback:
        await progress_callback("phase", "initial_translation")
    await translate_file(input_path, output_path, config)

    # Step 2: Structural review to find problems
    if progress_callback:
        await progress_callback("phase", "structural_review")

    review_config = ReviewConfig.__new__(ReviewConfig)
    review_config.source_lang = config.source_lang
    review_config.target_lang = config.target_lang
    review_config.model = config.model
    review_config.max_turns = config.max_turns
    review_config.chunk_size = config.chunk_size
    review_config.overlap_sentences = config.overlap_sentences
    review_config._raw_config = config._raw_config

    # Load aligned chunks for review
    with open(input_path, 'r', encoding='utf-8') as f:
        source_text = f.read()
    source_chunks = split_into_chunks(source_text, config)

    artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)
    if not artifacts['ok']:
        return {'error': f"Translation artifacts invalid: {artifacts['reason']}"}

    records = artifacts['records']
    trans_chunks = [r.get('translated_text', '').strip() for r in records]

    threshold_pct = preset.review_threshold_pct if preset else 0.3
    threshold_abs = preset.review_threshold_abs if preset else 5

    problem_chunks = []
    for i in range(min(len(source_chunks), len(trans_chunks))):
        _, source_chunk = source_chunks[i]
        result = structural_review_chunk(
            i + 1, source_chunk, trans_chunks[i],
            config.source_lang, config.target_lang,
            threshold_pct=threshold_pct,
            threshold_abs=threshold_abs,
        )
        if result['is_problem']:
            problem_chunks.append(i + 1)

    if progress_callback:
        await progress_callback("review_done", {
            'total_chunks': len(source_chunks),
            'problem_chunks': problem_chunks,
        })

    # Step 3: Auto-retry
    retry_result = None
    if problem_chunks:
        if progress_callback:
            await progress_callback("phase", "auto_retry")

        retry_result = await retry_problem_chunks(
            input_path, output_path, config,
            problem_chunks=problem_chunks,
            preset=preset,
            progress_callback=progress_callback,
        )

    # Step 4: Optional AI review on remaining problems
    ai_problems = []
    if run_ai_review and retry_result and retry_result.still_problematic:
        if progress_callback:
            await progress_callback("phase", "ai_review")
        # Re-load after retry
        artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)
        records = artifacts['records']
        for chunk_num in retry_result.still_problematic:
            idx = chunk_num - 1
            _, source_chunk = source_chunks[idx]
            trans_chunk = records[idx].get('translated_text', '')
            ai_result = await ai_review_chunk(source_chunk, trans_chunk, review_config, chunk_num)
            if ai_result:
                ai_problems.append(chunk_num)

    summary = {
        'total_chunks': len(source_chunks),
        'initial_problems': problem_chunks,
        'retry_result': retry_result.summary() if retry_result else None,
        'ai_flagged': ai_problems,
        'status': 'completed',
    }

    if progress_callback:
        await progress_callback("pipeline_done", summary)

    return summary
