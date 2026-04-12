#!/usr/bin/env python3
"""
Translation review tool.
Compares source and translated text for quality assurance.

Review is split into two clear layers:
1. Structural review — rule-based checks (sentence counts, glossary, numbers, etc.)
2. AI review — semantic quality check via LLM

Problem types (for classification):
- ocr_noise: OCR artifacts leaked into translation
- meta_text_leak: meta/instruction text leaked into translation
- over_split: one sentence split into too many
- under_translate: content omitted
- hallucination: content added that isn't in source
- name_inconsistency: proper noun translation inconsistent
- tone_drift: register/tone changed unexpectedly
- structure_mismatch: paragraph/sentence structure diverges significantly
"""

import asyncio
import argparse
import re
import json
from io import StringIO
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import yaml

try:
    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
except ImportError:
    print("Error: claude-agent-sdk not installed. Run: pip install claude-agent-sdk")
    exit(1)

from translate import (
    split_into_chunks,
    TranslationConfig,
    load_translation_artifacts,
    count_sentences,
    append_audit_log,
)

from providers import get_provider_from_config, BaseProvider


# ── Problem type constants ───────────────────────────────────

PROBLEM_TYPES = [
    "ocr_noise",
    "meta_text_leak",
    "over_split",
    "under_translate",
    "source_residue",
    "hallucination",
    "name_inconsistency",
    "tone_drift",
    "structure_mismatch",
]


class ReviewWriter:
    """Captures review output for both terminal display and file writing."""

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path
        self.buffer = StringIO()
        self.structured = {
            "source_sentences": 0,
            "target_sentences": 0,
            "glossary_issues": [],
            "structural_problems": [],
            "ai_issues": [],
            "ai_problem_chunks": [],
            "retranslate_command": "",
            "alignment": {},
        }

    def print(self, *args, **kwargs):
        print(*args, **kwargs)
        end = kwargs.get('end', '\n')
        if end == '\r':
            return
        text = " ".join(str(a) for a in args)
        self.buffer.write(text + end)

    def save(self):
        if not self.file_path:
            return
        with open(self.file_path, 'w', encoding='utf-8') as f:
            f.write(self.buffer.getvalue())
        json_path = str(Path(self.file_path).with_suffix('.json'))
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(self.structured, f, ensure_ascii=False, indent=2)


class ReviewConfig:
    """Configuration for review"""
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        self.source_lang = config.get('source_lang', 'ja')
        self.target_lang = config.get('target_lang', 'ko')
        self.model = config.get('model', 'claude-sonnet-4-6')
        self.max_turns = config.get('max_turns', 1)
        self.chunk_size = config.get('chunk_size', 2000)
        self.overlap_sentences = config.get('overlap_sentences', 3)
        self._raw_config = config

    def get_review_provider(self) -> BaseProvider:
        """Get the review provider from config."""
        return get_provider_from_config(self._raw_config, role='review')


def load_glossary(glossary_path: str = "glossary.json") -> Dict[str, str]:
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        result = {}
        for k, v in raw.items():
            result[k] = v.get("translation", "") if isinstance(v, dict) else v
        return result
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def count_term_occurrences(text: str, term: str) -> int:
    pattern = r'\b' + re.escape(term) + r'\b'
    matches = re.findall(pattern, text, re.IGNORECASE)
    return len(matches)


def check_glossary_consistency(source_text: str, translated_text: str, glossary: Dict[str, str]) -> List[Tuple[str, int, str, int, str]]:
    results = []
    for source_term, target_term in glossary.items():
        source_count = count_term_occurrences(source_text, source_term)
        target_count = count_term_occurrences(translated_text, target_term)
        if source_count == 0:
            continue
        status = "✓" if source_count == target_count else f"⚠️ ({source_count - target_count:+d})"
        results.append((source_term, source_count, target_term, target_count, status))
    return results


def build_translation_config_for_review(config: ReviewConfig) -> TranslationConfig:
    tc = TranslationConfig.__new__(TranslationConfig)
    tc.chunk_size = config.chunk_size
    tc.overlap_sentences = config.overlap_sentences
    tc.source_lang = config.source_lang
    tc.target_lang = config.target_lang
    tc.model = config.model
    tc.max_turns = config.max_turns
    return tc


def load_aligned_chunks(source_path: str, translated_path: str, config: ReviewConfig) -> dict:
    with open(source_path, 'r', encoding='utf-8') as f:
        source_text = f.read()

    with open(translated_path, 'r', encoding='utf-8') as f:
        translated_text = f.read()

    tc = build_translation_config_for_review(config)
    source_chunks = split_into_chunks(source_text, tc)
    artifacts = load_translation_artifacts(translated_path, input_path=source_path, config=tc)

    translation_chunks = []
    if artifacts['ok']:
        translation_chunks = [c.get('translated_text', '').strip() for c in artifacts['records']]
    else:
        translation_chunks = [c.strip() for c in translated_text.split('\n\n\n') if c.strip()]

    return {
        'source_text': source_text,
        'translated_text': translated_text,
        'source_chunks': source_chunks,
        'translation_chunks': translation_chunks,
        'artifacts': artifacts,
    }


# ═══════════════════════════════════════════════════════════════
#  STRUCTURAL REVIEW
# ═══════════════════════════════════════════════════════════════

def _check_number_preservation(source: str, translated: str) -> List[str]:
    """Check if numbers in source text are preserved in translation."""
    source_numbers = set(re.findall(r'\d+(?:\.\d+)?', source))
    target_numbers = set(re.findall(r'\d+(?:\.\d+)?', translated))
    missing = source_numbers - target_numbers
    issues = []
    for num in missing:
        # Skip single digits (too common, too many false positives)
        if len(num) >= 2:
            issues.append(f"Number '{num}' in source not found in translation")
    return issues


def _check_bracket_balance(text: str) -> List[str]:
    """Check bracket/quotation balance."""
    pairs = [('(', ')'), ('「', '」'), ('『', '』'), ('<', '>'), ('【', '】')]
    issues = []
    for open_b, close_b in pairs:
        open_count = text.count(open_b)
        close_count = text.count(close_b)
        if open_count != close_count:
            issues.append(f"Unbalanced {open_b}{close_b}: {open_count} open, {close_count} close")
    return issues


def _check_meta_text_leak(translated: str) -> List[str]:
    """Check for meta-text or instruction leaking into translation."""
    patterns = [
        r'(번역|translate|translation)\s*(완료|done|note|주)',
        r'\[.*?(note|주석|comment|번역자).*?\]',
        r'<.*?(translator|번역).*?>',
        r'(I\'ll|Let me|Here is|다음은).*?(translat|번역)',
    ]
    issues = []
    for pat in patterns:
        if re.search(pat, translated, re.IGNORECASE):
            issues.append(f"Possible meta-text leak: pattern '{pat}' matched")
    return issues


def structural_review_chunk(
    chunk_num: int,
    source_chunk: str,
    translated_chunk: str,
    source_lang: str,
    target_lang: str,
    threshold_pct: float = 0.3,
    threshold_abs: int = 5,
    chunk_record: Optional[dict] = None,
) -> dict:
    """Run structural review on a single chunk pair.

    Returns a dict with review results and problem classification.
    """
    src_sentences = count_sentences(source_chunk, source_lang)
    tgt_sentences = count_sentences(translated_chunk, target_lang)
    diff = tgt_sentences - src_sentences
    limit = max(threshold_abs, int(src_sentences * threshold_pct))
    under_limit = diff < -limit
    over_limit = diff > max(limit * 2, threshold_abs * 2)

    # Number preservation
    number_issues = _check_number_preservation(source_chunk, translated_chunk)

    # Bracket balance in translation
    bracket_issues = _check_bracket_balance(translated_chunk)

    # Meta-text leak detection
    meta_issues = _check_meta_text_leak(translated_chunk)

    validation = (chunk_record or {}).get('validation', {}) if chunk_record else {}
    coverage_issues = []
    residue_issues = []
    if validation:
        if validation.get('missing_units'):
            coverage_issues.append(f"Missing units: {validation['missing_units']}")
        if validation.get('empty_units'):
            coverage_issues.append(f"Empty units: {validation['empty_units']}")
        if validation.get('suspiciously_short_units'):
            coverage_issues.append(
                f"Suspiciously short units: {validation['suspiciously_short_units']}"
            )
        if validation.get('exact_copy_units'):
            residue_issues.append(
                f"Source copied without translation: {validation['exact_copy_units']}"
            )
        if validation.get('residue_units'):
            residue_issues.append(
                f"Critical source residue units: {[r['unit'] for r in validation['residue_units']]}"
            )

    # Classify problems
    problems = []
    if under_limit:
        problems.append("under_translate")
        problems.append("structure_mismatch")
    elif over_limit:
        problems.append("over_split")
        if diff > max(limit * 3, threshold_abs * 3):
            problems.append("structure_mismatch")

    if number_issues:
        problems.append("under_translate")
    if meta_issues:
        problems.append("meta_text_leak")
    if bracket_issues:
        problems.append("structure_mismatch")
    if coverage_issues:
        problems.append("under_translate")
    if residue_issues:
        problems.append("source_residue")
        problems.append("under_translate")

    return {
        "chunk_num": chunk_num,
        "source_sentences": src_sentences,
        "target_sentences": tgt_sentences,
        "sentence_diff": diff,
        "is_problem": bool(problems),
        "problems": list(set(problems)),
        "number_issues": number_issues,
        "bracket_issues": bracket_issues,
        "meta_issues": meta_issues,
        "coverage_issues": coverage_issues,
        "residue_issues": residue_issues,
    }


def run_structural_review(
    aligned: dict,
    config: ReviewConfig,
    writer: ReviewWriter,
    source_path: str = "",
    translated_path: str = "",
    threshold_pct: float = 0.3,
    threshold_abs: int = 5,
) -> List[int]:
    """Run structural review on all aligned chunks.

    Returns list of problem chunk numbers.
    """
    source_chunks = aligned['source_chunks']
    trans_chunks = aligned['translation_chunks']
    artifacts = aligned['artifacts']

    total_source = len(source_chunks)
    total_trans = len(trans_chunks)

    writer.print(f"\n[구조 리뷰 (Structural Review)]")
    writer.print(f"원문 청크 수: {total_source} / 번역문 청크 수: {total_trans}")
    writer.print(f"정렬 상태: {artifacts['reason']}")

    writer.structured['alignment'] = {
        'source_chunk_count': total_source,
        'translation_chunk_count': total_trans,
        'mode': artifacts['reason'],
        'metadata_ok': artifacts['ok'],
    }

    if not artifacts['ok']:
        writer.print("⚠️  메타데이터 기반 정렬이 아님. selective retranslation 신뢰도가 낮을 수 있음.")

    if total_source != total_trans:
        writer.print("⚠️  청크 수 불일치! 비교 가능한 범위만 분석합니다.")

    compare_count = min(total_source, total_trans)
    problem_chunks = []
    all_chunk_results = []

    writer.print(f"\n{'청크':>6}  {'원문':>6}  {'번역':>6}  {'차이':>6}  상태")
    writer.print("-" * 60)

    for i in range(compare_count):
        _, source_chunk = source_chunks[i]
        trans_chunk = trans_chunks[i]
        chunk_record = None
        if artifacts['ok'] and i < len(artifacts.get('records', [])):
            chunk_record = artifacts['records'][i]

        result = structural_review_chunk(
            chunk_num=i + 1,
            source_chunk=source_chunk,
            translated_chunk=trans_chunk,
            source_lang=config.source_lang,
            target_lang=config.target_lang,
            threshold_pct=threshold_pct,
            threshold_abs=threshold_abs,
            chunk_record=chunk_record,
        )
        all_chunk_results.append(result)

        if result['is_problem']:
            status = "⚠️  " + ", ".join(result['problems'])
            problem_chunks.append(i + 1)
        elif abs(result['sentence_diff']) > 2:
            status = "△"
        else:
            status = "✓"

        extra = ""
        if result['number_issues']:
            extra += f" [숫자 누락: {len(result['number_issues'])}]"
        if result['meta_issues']:
            extra += " [메타텍스트]"
        if result['bracket_issues']:
            extra += " [괄호 불균형]"
        if result['coverage_issues']:
            extra += f" [커버리지: {len(result['coverage_issues'])}]"
        if result['residue_issues']:
            extra += f" [원문잔류: {len(result['residue_issues'])}]"

        writer.print(f"{i+1:>6}  {result['source_sentences']:>6}  {result['target_sentences']:>6}  {result['sentence_diff']:>+6}  {status}{extra}")

    if total_trans < total_source:
        for i in range(total_trans, total_source):
            writer.print(f"{i+1:>6}  {'?':>6}  {'없음':>6}  {'':>6}  ⚠️  번역 누락")
            problem_chunks.append(i + 1)
    elif total_trans > total_source:
        for i in range(total_source, total_trans):
            writer.print(f"{'?':>6}  {'없음':>6}  {'?':>6}  {'':>6}  ⚠️  초과 청크 {i+1}")

    writer.print("-" * 60)
    if problem_chunks:
        chunks_str = ",".join(str(c) for c in problem_chunks)
        writer.print(f"\n⚠️  문제 청크 {len(problem_chunks)}개: {chunks_str}")
        if artifacts['ok']:
            writer.print(f"\n💡 재번역 명령어:")
            writer.print(f"   python3 translate.py {source_path} -o {translated_path} --only-chunks {chunks_str}")
            writer.structured['retranslate_command'] = f"python3 translate.py {source_path} -o {translated_path} --only-chunks {chunks_str}"
        else:
            writer.print("\n💡 현재 산출물은 메타데이터 정합성이 없어 selective retranslation 비권장")
        writer.structured['structural_problems'] = all_chunk_results
        # Keep backward-compatible field
        writer.structured['problem_chunks'] = problem_chunks
    else:
        writer.print("\n✅ 구조 리뷰: 모든 청크 정상")

    return problem_chunks


# ═══════════════════════════════════════════════════════════════
#  AI REVIEW
# ═══════════════════════════════════════════════════════════════

async def ai_review_chunk(source_chunk: str, translated_chunk: str, config: ReviewConfig, chunk_num: int) -> str:
    """AI-powered semantic review of a single chunk.

    Uses the review provider from config (defaults to Claude).
    """
    system_prompt = """당신은 번역 품질 검토자입니다.
원문과 번역문을 비교하여 다음 사항을 확인하세요:
1. 누락: 원문의 내용이 번역에서 빠졌는지
2. 추가: 번역에서 원문에 없는 내용이 추가되었는지
3. 의미 왜곡: 의미가 크게 변경되거나 왜곡된 부분
4. 화자/시점 혼동: 화자나 시점이 바뀐 부분
5. 대사 톤 흔들림: 어체/말투가 원문에 충실하고 일관되는지
6. 문체 붕괴: 전체적인 문체가 급격히 달라진 부분

문제가 없으면 "OK"라고만 답하세요.
문제가 있으면 구체적으로 지적해주세요. 각 문제 앞에 유형 태그를 붙이세요:
[누락] [추가] [왜곡] [시점] [톤] [문체]"""

    user_prompt = f"""[원문]
{source_chunk}

[번역문]
{translated_chunk}"""

    try:
        provider = config.get_review_provider()
        response = await provider.generate(system_prompt, user_prompt)
        result = response.text

        if result.upper() != "OK" and result:
            return f"청크 {chunk_num}: {result}"
        return ""
    except Exception as e:
        return f"청크 {chunk_num}: ❌ 검증 중 오류 - {str(e)}"


async def run_ai_review(
    aligned: dict,
    config: ReviewConfig,
    writer: ReviewWriter,
    source_path: str = "",
    translated_path: str = "",
    target_chunks: List[int] = None,
) -> List[int]:
    """Run AI review on chunks.

    Args:
        aligned: Aligned chunk data
        config: Review configuration
        writer: Output writer
        source_path: For retranslation command generation
        translated_path: For retranslation command generation
        target_chunks: If specified, only review these chunk numbers.
                       If None, reviews all chunks.

    Returns:
        List of problem chunk numbers identified by AI.
    """
    writer.print("\n[AI 의미 리뷰 (AI Semantic Review)]")
    writer.print("분석 중...")

    compare_count = min(len(aligned['source_chunks']), len(aligned['translation_chunks']))
    issues = []
    ai_problem_chunks = []

    if target_chunks:
        indices = [c - 1 for c in target_chunks if 0 < c <= compare_count]
    else:
        indices = list(range(compare_count))

    total = len(indices)
    for progress, i in enumerate(indices, 1):
        print(f"  청크 {i+1}/{compare_count} 검증 중... ({progress}/{total})", end='\r')
        _, source_chunk = aligned['source_chunks'][i]
        trans_chunk = aligned['translation_chunks'][i]
        result = await ai_review_chunk(source_chunk, trans_chunk, config, i + 1)
        if result:
            issues.append(result)
            ai_problem_chunks.append(i + 1)
    print(" " * 60, end='\r')

    if issues:
        writer.print(f"발견된 문제: {len(issues)}개")
        for issue in issues:
            writer.print(f"- {issue}")
        writer.structured['ai_issues'] = issues
        writer.structured['ai_problem_chunks'] = ai_problem_chunks
        if aligned['artifacts']['ok'] and ai_problem_chunks:
            chunks_str = ",".join(str(c) for c in ai_problem_chunks)
            writer.print(f"\n💡 AI 검증 기반 재번역 명령어:")
            writer.print(f"   python3 translate.py {source_path} -o {translated_path} --only-chunks {chunks_str}")
    else:
        writer.print("AI 리뷰: 문제 없음 (✓)")

    return ai_problem_chunks


# ═══════════════════════════════════════════════════════════════
#  COMBINED REVIEW ENTRY POINT
# ═══════════════════════════════════════════════════════════════

async def review_files(source_path: str, translated_path: str, config: ReviewConfig, use_ai: bool = False, detailed: bool = False, writer: Optional[ReviewWriter] = None) -> None:
    if writer is None:
        writer = ReviewWriter()

    writer.print("📖 Reading files...")
    aligned = load_aligned_chunks(source_path, translated_path, config)
    source_text = aligned['source_text']
    translated_text = aligned['translated_text']

    writer.print("\n" + "="*60)
    writer.print("📋 번역 검증 리포트")
    writer.print("="*60)

    source_count = count_sentences(source_text, config.source_lang)
    target_count = count_sentences(translated_text, config.target_lang)
    diff = target_count - source_count

    writer.structured['source_sentences'] = source_count
    writer.structured['target_sentences'] = target_count

    writer.print(f"\n원문 문장 수: {source_count}")
    writer.print(f"번역문 문장 수: {target_count}")
    if diff == 0:
        writer.print("차이: 0 (✓)")
    elif abs(diff) <= 5:
        writer.print(f"차이: {diff:+d} (⚠️ 약간의 차이)")
    else:
        writer.print(f"차이: {diff:+d} (⚠️⚠️ 주의 필요 - 누락 또는 추가 가능성)")

    glossary = load_glossary("glossary.json")
    writer.print("\n[용어 일관성]")
    if glossary:
        consistency_results = check_glossary_consistency(source_text, translated_text, glossary)
        if consistency_results:
            for source_term, source_cnt, target_term, target_cnt, status in consistency_results:
                writer.print(f'- {source_term}: 원문 {source_cnt}회 / 번역 "{target_term}" {target_cnt}회 ({status})')
                if "⚠️" in status:
                    writer.structured['glossary_issues'].append({
                        'source_term': source_term,
                        'expected_count': source_cnt,
                        'target_term': target_term,
                        'actual_count': target_cnt,
                    })
        else:
            writer.print("- 검증할 용어가 원문에 없음")
    else:
        writer.print("- glossary.json이 비어있음 (용어 검증 건너뜀)")

    # Structural review (replaces old detailed_chunk_analysis)
    structural_problem_chunks = []
    if detailed:
        structural_problem_chunks = run_structural_review(
            aligned, config, writer, source_path, translated_path
        )

    # AI review
    if use_ai:
        # If structural review found problems, focus AI review on those +
        # a sample of others. If no structural review, review all.
        target_for_ai = None
        if structural_problem_chunks:
            target_for_ai = structural_problem_chunks  # Focus on flagged chunks
        await run_ai_review(
            aligned, config, writer, source_path, translated_path,
            target_chunks=target_for_ai,
        )

    writer.print("\n" + "="*60)

    append_audit_log('review', {
        'source_path': str(Path(source_path).resolve()),
        'translated_path': str(Path(translated_path).resolve()),
        'source_sentences': source_count,
        'target_sentences': target_count,
        'sentence_diff': diff,
        'alignment_mode': aligned['artifacts']['reason'],
        'metadata_ok': aligned['artifacts']['ok'],
        'problem_chunks': writer.structured.get('problem_chunks', []),
        'ai_problem_chunks': writer.structured.get('ai_problem_chunks', []),
    })


# ── Legacy-compatible function name ──────────────────────────
# Keep for web.py backward compatibility
detailed_chunk_analysis = run_structural_review


def main():
    parser = argparse.ArgumentParser(description="Review translation quality")
    parser.add_argument("source", help="Source text file path")
    parser.add_argument("translated", help="Translated text file path")
    parser.add_argument("--detailed", action="store_true", help="Show per-chunk structural analysis")
    parser.add_argument("--ai", action="store_true", help="Enable AI-powered semantic review (slower)")
    parser.add_argument("--config", default="config.yaml", help="Config file path (default: config.yaml)")
    parser.add_argument("-o", "--output", default=None, help="Save review report to file")

    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"❌ Error: Source file not found: {args.source}")
        exit(1)
    if not Path(args.translated).exists():
        print(f"❌ Error: Translated file not found: {args.translated}")
        exit(1)

    try:
        config = ReviewConfig(args.config)
    except FileNotFoundError:
        print(f"❌ Error: Config file not found: {args.config}")
        exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        exit(1)

    writer = ReviewWriter(file_path=args.output)
    try:
        asyncio.run(review_files(args.source, args.translated, config, args.ai, args.detailed, writer=writer))
        writer.save()
        if args.output:
            json_path = Path(args.output).with_suffix('.json')
            print(f"\n📄 리포트 저장: {args.output}")
            print(f"📊 구조화 데이터 저장: {json_path}")
    except KeyboardInterrupt:
        writer.save()
        print("\n\n⏸️  Review interrupted by user")
        exit(0)
    except Exception as e:
        print(f"\n❌ Error during review: {e}")
        exit(1)


if __name__ == "__main__":
    main()
