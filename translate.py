#!/usr/bin/env python3
"""
Long-form text translation tool for Japanese/English to Korean translation.

Supports multiple providers (OpenAI/Claude/Gemini) via providers.py abstraction.
"""

import asyncio
import argparse
import re
import json
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from providers import get_provider_from_config, BaseProvider
from presets import GenrePreset, get_preset


class TranslationConfig:
    """Configuration for translation"""
    def __init__(self, config_path: str = "config.yaml", preset_override: str = None):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        self.chunk_size = config.get('chunk_size', 2000)
        self.overlap_sentences = config.get('overlap_sentences', 3)
        self.source_lang = config.get('source_lang', 'ja')
        self.target_lang = config.get('target_lang', 'ko')
        self.model = config.get('model', 'gpt-5.2-chat-latest')
        self.max_turns = config.get('max_turns', 1)
        self._raw_config = config
        providers_cfg = config.get('providers', {})
        translation_cfg = providers_cfg.get('translation', {})
        self.provider_name = translation_cfg.get('provider', config.get('provider', 'openai'))

        # Genre preset
        preset_name = preset_override or config.get('preset')
        self.preset: GenrePreset = get_preset(preset_name) if preset_name else None

        # Retry config
        retry_cfg = config.get('retry', {})
        self.retry_enabled = retry_cfg.get('enabled', False)
        self.retry_max = retry_cfg.get('max_retries', 2)
        self.retry_score_threshold = retry_cfg.get('score_threshold', 0.6)
        self.retry_auto_split = retry_cfg.get('auto_split', True)

        # Progress config
        progress_cfg = config.get('progress', {})
        self.progress_notify = progress_cfg.get('notify', False)
        self.progress_interval = progress_cfg.get('report_interval', 900)

    def get_translation_provider(self) -> BaseProvider:
        """Get the translation provider from config."""
        return get_provider_from_config(self._raw_config, role='translation')


def load_rules(rules_path: str = "rules.md") -> str:
    """Load translation rules from markdown file"""
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ""


def load_glossary(glossary_path: str = "glossary.json") -> tuple:
    """Load glossary and format it as text.
    Returns (glossary_text, annotate_terms) where annotate_terms is a list of
    terms that should have their original form annotated on first occurrence.
    """
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            glossary = json.load(f)
        if not glossary:
            return "없음", []

        lines = []
        annotate_terms = []
        for k, v in glossary.items():
            if isinstance(v, dict):
                translation = v.get("translation", "")
                lines.append(f"- {k} → {translation}")
                if v.get("annotate", False):
                    annotate_terms.append(k)
            else:
                lines.append(f"- {k} → {v}")

        return "\n".join(lines), annotate_terms
    except (FileNotFoundError, json.JSONDecodeError):
        return "없음", []


def count_sentences(text: str, lang: str) -> int:
    """Count sentences in text based on language-aware punctuation.

    Newlines are not treated as sentence boundaries by default because Korean
    translations often contain many line breaks, which made structural review
    massively over-count sentences and trigger noisy retries.
    """
    return len(split_into_sentences(text, lang))


def split_into_sentences(text: str, lang: str) -> List[str]:
    """Split text into sentences.

    Falls back to non-empty lines, then to the full text, when punctuation-based
    splitting is not informative enough.
    """
    normalized = text.replace('\r\n', '\n').strip()
    if not normalized:
        return []

    if lang == 'ja':
        parts = re.split(r'(?<=[。！？])\s*', normalized)
    else:
        parts = re.split(r'(?<=[.!?])\s*', normalized)

    sentences = [p.strip() for p in parts if p.strip()]
    if len(sentences) > 1:
        return sentences

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines

    return [normalized]


def split_into_chunks(text: str, config: TranslationConfig) -> List[Tuple[str, str]]:
    """
    Split text into chunks with context overlap.
    Returns list of (context, chunk) tuples.
    """
    paragraphs = text.split('\n\n')
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks = []
    current_chunk = ""
    previous_sentences = []

    def save_current_chunk():
        nonlocal current_chunk, previous_sentences
        if current_chunk:
            context = "\n".join(previous_sentences[-config.overlap_sentences:]) if previous_sentences else ""
            chunks.append((context, current_chunk))
            chunk_sentences = split_into_sentences(current_chunk, config.source_lang)
            previous_sentences.extend(chunk_sentences)
            current_chunk = ""

    def split_large_paragraph(paragraph: str) -> List[str]:
        sentences = split_into_sentences(paragraph, config.source_lang)
        sub_chunks = []
        current_sub = ""

        for sentence in sentences:
            if current_sub and len(current_sub) + len(sentence) + 1 > config.chunk_size:
                sub_chunks.append(current_sub)
                current_sub = sentence
            else:
                current_sub = sentence if not current_sub else current_sub + " " + sentence

        if current_sub:
            sub_chunks.append(current_sub)
        return sub_chunks

    for paragraph in paragraphs:
        para_len = len(paragraph)
        if para_len > config.chunk_size:
            save_current_chunk()
            for sub_chunk in split_large_paragraph(paragraph):
                current_chunk = sub_chunk
                save_current_chunk()
        elif current_chunk and len(current_chunk) + len(paragraph) + 2 > config.chunk_size:
            save_current_chunk()
            current_chunk = paragraph
        else:
            current_chunk = paragraph if not current_chunk else current_chunk + "\n\n" + paragraph

    save_current_chunk()
    return chunks


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def output_meta_path(output_path: str) -> Path:
    return Path(output_path).with_suffix(Path(output_path).suffix + '.chunks.json')


def audit_log_path(kind: str) -> Path:
    log_dir = Path(__file__).parent / 'logs'
    log_dir.mkdir(exist_ok=True)
    return log_dir / f'{kind}_audit.jsonl'


def append_audit_log(kind: str, payload: dict) -> None:
    payload = dict(payload)
    payload['ts'] = datetime.now().isoformat(timespec='seconds')
    with open(audit_log_path(kind), 'a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


def build_chunk_records(chunks: List[Tuple[str, str]]) -> List[dict]:
    records = []
    for idx, (context, source_text) in enumerate(chunks, start=1):
        records.append({
            'index': idx,
            'context': context,
            'source_text': source_text,
            'source_hash': hashlib.sha256(source_text.encode('utf-8')).hexdigest(),
            'translated_text': '',
            'segments': [],
            'validation': {},
        })
    return records


def update_record_from_translation_result(record: dict, result: Dict[str, Any]) -> None:
    """Persist a structured translation result into a chunk record."""
    record['translated_text'] = result.get('text', '')
    record['segments'] = result.get('segments', [])
    record['validation'] = result.get('validation', {})


def save_translation_artifacts(output_path: str, records: List[dict], input_path: str, config: TranslationConfig) -> None:
    output_text = "\n\n\n".join(record.get('translated_text', '').strip() for record in records if record.get('translated_text', '').strip())
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)

    meta = {
        'version': 2,
        'input_path': str(Path(input_path).resolve()),
        'input_sha256': file_sha256(input_path),
        'config': {
            'chunk_size': config.chunk_size,
            'overlap_sentences': config.overlap_sentences,
            'source_lang': config.source_lang,
            'target_lang': config.target_lang,
            'model': config.model,
            'max_turns': config.max_turns,
        },
        'chunk_count': len(records),
        'chunks': records,
    }
    with open(output_meta_path(output_path), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_translation_artifacts(output_path: str, input_path: Optional[str] = None, config: Optional[TranslationConfig] = None) -> dict:
    meta_path = output_meta_path(output_path)
    result = {
        'ok': False,
        'reason': 'no-metadata',
        'records': [],
        'meta': None,
    }

    if not meta_path.exists():
        if Path(output_path).exists():
            with open(output_path, 'r', encoding='utf-8') as f:
                text = f.read()
            result.update({
                'reason': 'legacy-text-only',
                'records': [
                    {'index': i + 1, 'translated_text': c.strip()}
                    for i, c in enumerate(text.split('\n\n\n')) if c.strip()
                ]
            })
        return result

    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    result['meta'] = meta
    result['records'] = meta.get('chunks', [])

    if input_path:
        expected_path = str(Path(input_path).resolve())
        if meta.get('input_path') != expected_path:
            result['reason'] = 'input-path-mismatch'
            return result
        if meta.get('input_sha256') != file_sha256(input_path):
            result['reason'] = 'input-hash-mismatch'
            return result

    if config:
        cfg = meta.get('config', {})
        expected_cfg = {
            'chunk_size': config.chunk_size,
            'overlap_sentences': config.overlap_sentences,
            'source_lang': config.source_lang,
            'target_lang': config.target_lang,
        }
        for k, v in expected_cfg.items():
            if cfg.get(k) != v:
                result['reason'] = f'config-mismatch:{k}'
                return result

    result['ok'] = True
    result['reason'] = 'ok'
    return result


@dataclass
class TranslationUnit:
    index: int
    source_text: str
    separator_after: str = ""


def _split_text_by_length(text: str, max_chars: int) -> List[str]:
    """Fallback splitter for long text without reliable sentence boundaries."""
    remaining = text.strip()
    if not remaining:
        return []
    if len(remaining) <= max_chars:
        return [remaining]

    pieces = []
    while len(remaining) > max_chars:
        split_at = remaining.rfind(' ', 0, max_chars)
        if split_at < max_chars // 2:
            split_at = max_chars
        piece = remaining[:split_at].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[split_at:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _split_paragraph_into_units(paragraph: str, source_lang: str, max_chars: int) -> List[str]:
    paragraph = paragraph.strip()
    if not paragraph:
        return []
    if len(paragraph) <= max_chars:
        return [paragraph]

    sentences = split_into_sentences(paragraph, source_lang)
    if len(sentences) <= 1:
        return _split_text_by_length(paragraph, max_chars)

    units = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                units.append(current.strip())
                current = ""
            units.extend(_split_text_by_length(sentence, max_chars))
            continue

        if current and len(current) + len(sentence) + 1 > max_chars:
            units.append(current.strip())
            current = sentence
        else:
            current = sentence if not current else current + " " + sentence

    if current:
        units.append(current.strip())

    return units or _split_text_by_length(paragraph, max_chars)


def split_chunk_into_translation_units(
    chunk_text: str,
    config: TranslationConfig,
    max_unit_chars: Optional[int] = None,
) -> List[TranslationUnit]:
    """Split a chunk into smaller translation units with stable ordering."""
    if max_unit_chars is None:
        max_unit_chars = max(350, min(900, int(config.chunk_size * 0.45)))

    paragraphs = [p.strip() for p in chunk_text.split('\n\n') if p.strip()]
    if not paragraphs:
        paragraphs = [chunk_text.strip()] if chunk_text.strip() else []

    units: List[TranslationUnit] = []
    for para_idx, paragraph in enumerate(paragraphs):
        sub_units = _split_paragraph_into_units(paragraph, config.source_lang, max_unit_chars)
        for sub_idx, sub_unit in enumerate(sub_units):
            if sub_idx < len(sub_units) - 1:
                separator_after = " "
            elif para_idx < len(paragraphs) - 1:
                separator_after = "\n\n"
            else:
                separator_after = ""
            units.append(TranslationUnit(
                index=len(units) + 1,
                source_text=sub_unit,
                separator_after=separator_after,
            ))

    return units


def _build_annotation_section(annotate_terms: Optional[list], already_annotated: Optional[set]) -> str:
    if not annotate_terms:
        return ""
    pending = [t for t in annotate_terms if t not in (already_annotated or set())]
    if not pending:
        return ""
    return f"""
[원어 병기 규칙]
다음 용어가 이 구간에서 처음 등장하면, 번역어 뒤에 괄호로 원어를 병기하세요.
이미 앞에서 병기된 용어는 번역어만 쓰세요.
대상 용어: {', '.join(pending)}
예시: 짐 오루크(Jim O'Rourke)
"""


def _build_preset_section(config: TranslationConfig) -> str:
    if config.preset and config.preset.prompt_modifier:
        return f"\n{config.preset.prompt_modifier}\n"
    return ""


def _build_segmented_system_prompt(
    rules: str,
    glossary: str,
    config: TranslationConfig,
    annotate_terms: Optional[list] = None,
    already_annotated: Optional[set] = None,
    failure_hint: str = "",
) -> str:
    annotation_section = _build_annotation_section(annotate_terms, already_annotated)
    preset_section = _build_preset_section(config)
    failure_section = f"\n[직전 검증에서 잡힌 문제]\n{failure_hint}\n" if failure_hint else ""

    return f"""당신은 전문 번역가입니다. 아래 번역 원칙을 반드시 따르세요.

[번역 원칙]
{rules}
{preset_section}
[고유명사 사전]
{glossary}
{annotation_section}
{failure_section}
규칙:
- 원문의 모든 문장을 빠짐없이 번역하세요. 절대로 생략하지 마세요.
- 원문에 없는 내용을 추가하지 마세요.
- 각 번역 단위를 번호 그대로 모두 출력하세요. 단위를 합치거나 건너뛰지 마세요.
- 원문의 문장/문단 구조를 최대한 보존하세요.
- 번역문 안에 원어를 길게 남겨두지 마세요. 번역해야 할 문장은 모두 한국어로 옮기세요.
- 출력 형식은 아래 마커만 사용하세요. 설명, 주석, 메타 코멘트 금지.
- 도구(tool)를 사용하지 마세요. 텍스트 응답만 하세요.

[출력 형식]
[[SEGMENT 1]]
첫 번째 단위의 번역
[[END SEGMENT 1]]

[[SEGMENT 2]]
두 번째 단위의 번역
[[END SEGMENT 2]]
"""


def _build_segmented_user_prompt(
    units: List[TranslationUnit],
    context: str,
) -> str:
    context_section = f"""[이전 문맥 (번역 불필요, 참고용)]
{context}

""" if context else ""

    body = []
    for unit in units:
        body.append(f"[[SEGMENT {unit.index}]]\n{unit.source_text}\n[[END SEGMENT {unit.index}]]")

    return f"""다음 텍스트를 한국어로 번역하세요.

{context_section}[번역 대상 단위]

{chr(10).join(body)}"""


def _build_single_unit_prompts(
    unit: TranslationUnit,
    context: str,
    rules: str,
    glossary: str,
    config: TranslationConfig,
    annotate_terms: Optional[list] = None,
    already_annotated: Optional[set] = None,
    failure_hint: str = "",
) -> Tuple[str, str]:
    annotation_section = _build_annotation_section(annotate_terms, already_annotated)
    preset_section = _build_preset_section(config)
    failure_section = f"\n[직전 검증에서 잡힌 문제]\n{failure_hint}\n" if failure_hint else ""
    system_prompt = f"""당신은 전문 번역가입니다. 아래 번역 원칙을 반드시 따르세요.

[번역 원칙]
{rules}
{preset_section}
[고유명사 사전]
{glossary}
{annotation_section}
{failure_section}
규칙:
- 반드시 아래 단 하나의 번역 단위만 한국어로 번역하세요.
- 원문의 내용을 빠짐없이 번역하고, 요약하거나 생략하지 마세요.
- 번역문만 출력하세요. 번호, 설명, 메타 코멘트 금지.
- 원문의 문장이 그대로 길게 남아 있으면 안 됩니다.
- 도구(tool)를 사용하지 마세요. 텍스트 응답만 하세요."""

    context_section = f"""[이전 문맥 (번역 불필요, 참고용)]
{context}

""" if context else ""

    user_prompt = f"""다음 단위만 한국어로 번역하세요.

{context_section}[번역 대상 단위]
{unit.source_text}"""

    return system_prompt, user_prompt


_SEGMENT_RE = re.compile(
    r'\[\[SEGMENT\s+(\d+)\]\]\s*(.*?)\s*\[\[END SEGMENT\s+\1\]\]',
    re.DOTALL,
)


def parse_structured_translation_response(response_text: str, expected_count: int) -> dict:
    """Parse the segmented translation format emitted by the model."""
    text = response_text.strip()
    if expected_count == 1 and "[[SEGMENT" not in text:
        return {
            'units': [text],
            'missing_units': [],
            'duplicate_units': [],
            'parse_error': '',
        }

    matches = list(_SEGMENT_RE.finditer(text))
    mapping: Dict[int, str] = {}
    duplicate_units = []
    for match in matches:
        idx = int(match.group(1))
        content = match.group(2).strip()
        if idx in mapping:
            duplicate_units.append(idx)
            continue
        mapping[idx] = content

    units = [mapping.get(i, '') for i in range(1, expected_count + 1)]
    missing_units = [i for i in range(1, expected_count + 1) if i not in mapping]
    parse_error = ''
    if not matches:
        parse_error = "Structured segment markers were not returned."
    elif missing_units:
        parse_error = f"Missing translated segments: {missing_units}"
    elif duplicate_units:
        parse_error = f"Duplicate translated segments: {sorted(set(duplicate_units))}"

    return {
        'units': units,
        'missing_units': missing_units,
        'duplicate_units': sorted(set(duplicate_units)),
        'parse_error': parse_error,
    }


def _normalize_for_comparison(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip().lower()


def _is_suspiciously_short(source_text: str, translated_text: str, config: TranslationConfig) -> bool:
    source_len = len(source_text.strip())
    target_len = len(translated_text.strip())
    source_sentences = count_sentences(source_text, config.source_lang)

    if source_len < 80 or source_sentences <= 1:
        return False

    return target_len < max(12, int(source_len * 0.12))


def validate_translated_units(
    units: List[TranslationUnit],
    translated_units: List[str],
    config: TranslationConfig,
    parse_result: Optional[dict] = None,
) -> dict:
    """Validate structured unit translations for completeness and source residue."""
    from quality_gate import detect_source_residue_in_chunk

    missing_units = list(parse_result.get('missing_units', [])) if parse_result else []
    duplicate_units = list(parse_result.get('duplicate_units', [])) if parse_result else []
    empty_units = []
    exact_copy_units = []
    residue_units = []
    suspiciously_short_units = []

    for unit, translated in zip(units, translated_units):
        cleaned = translated.strip()
        if not cleaned:
            empty_units.append(unit.index)
            continue

        if len(_normalize_for_comparison(unit.source_text)) >= 20 and (
            _normalize_for_comparison(unit.source_text) == _normalize_for_comparison(cleaned)
        ):
            exact_copy_units.append(unit.index)

        residue = detect_source_residue_in_chunk(
            cleaned,
            source_lang=config.source_lang,
            chunk_num=unit.index,
            source_text=unit.source_text,
        )
        if residue.get('severity') == 'critical':
            residue_units.append({
                'unit': unit.index,
                'severity': residue.get('severity'),
                'passages': residue.get('passages', []),
            })

        if _is_suspiciously_short(unit.source_text, cleaned, config):
            suspiciously_short_units.append(unit.index)

    issues = []
    if parse_result and parse_result.get('parse_error'):
        issues.append(parse_result['parse_error'])
    if missing_units:
        issues.append(f"Missing units: {missing_units}")
    if duplicate_units:
        issues.append(f"Duplicate units: {duplicate_units}")
    if empty_units:
        issues.append(f"Empty units: {empty_units}")
    if exact_copy_units:
        issues.append(f"Source copied without translation: {exact_copy_units}")
    if residue_units:
        issues.append(f"Critical source residue remained in units: {[r['unit'] for r in residue_units]}")
    if suspiciously_short_units:
        issues.append(f"Suspiciously short translations: {suspiciously_short_units}")

    invalid_units = sorted(set(
        missing_units +
        duplicate_units +
        empty_units +
        exact_copy_units +
        [r['unit'] for r in residue_units] +
        suspiciously_short_units
    ))

    return {
        'ok': not invalid_units,
        'issues': issues,
        'missing_units': missing_units,
        'duplicate_units': duplicate_units,
        'empty_units': empty_units,
        'exact_copy_units': exact_copy_units,
        'residue_units': residue_units,
        'suspiciously_short_units': suspiciously_short_units,
        'invalid_units': invalid_units,
        'unit_count': len(units),
        'translated_unit_count': len([t for t in translated_units if t.strip()]),
    }


def _join_translated_units(units: List[TranslationUnit], translated_units: List[str]) -> str:
    pieces = []
    for unit, translated in zip(units, translated_units):
        text = translated.strip()
        if not text:
            continue
        pieces.append(text)
        if unit.separator_after:
            pieces.append(unit.separator_after)
    return "".join(pieces).strip()


def _build_segment_payload(
    units: List[TranslationUnit],
    translated_units: List[str],
) -> List[dict]:
    segments = []
    for unit, translated in zip(units, translated_units):
        segments.append({
            'index': unit.index,
            'source_text': unit.source_text,
            'translated_text': translated.strip(),
            'separator_after': unit.separator_after,
        })
    return segments


def _summarize_validation_issues(validation: dict) -> str:
    details = []
    if validation.get('missing_units'):
        details.append(f"- 누락된 단위: {validation['missing_units']}")
    if validation.get('empty_units'):
        details.append(f"- 비어 있는 단위: {validation['empty_units']}")
    if validation.get('exact_copy_units'):
        details.append(f"- 원문이 그대로 남은 단위: {validation['exact_copy_units']}")
    if validation.get('residue_units'):
        details.append(
            f"- 원어 잔류가 남은 단위: {[item['unit'] for item in validation['residue_units']]}"
        )
    if validation.get('suspiciously_short_units'):
        details.append(f"- 지나치게 짧은 번역 단위: {validation['suspiciously_short_units']}")
    if not details and validation.get('issues'):
        details.extend(f"- {issue}" for issue in validation['issues'])
    return "\n".join(details)


async def _translate_single_unit(
    unit: TranslationUnit,
    context: str,
    rules: str,
    glossary: str,
    config: TranslationConfig,
    provider: BaseProvider,
    annotate_terms: Optional[list] = None,
    already_annotated: Optional[set] = None,
    failure_hint: str = "",
    max_retries: int = 2,
) -> str:
    system_prompt, user_prompt = _build_single_unit_prompts(
        unit, context, rules, glossary, config,
        annotate_terms=annotate_terms,
        already_annotated=already_annotated,
        failure_hint=failure_hint,
    )
    last_error = None
    for _ in range(max_retries):
        try:
            response = await provider.generate(system_prompt, user_prompt)
            return response.text.strip()
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise Exception(f"Single-unit translation failed: {last_error}")


async def translate_chunk_detailed(
    chunk_text: str,
    context: str,
    rules: str,
    glossary: str,
    config: TranslationConfig,
    annotate_terms: list = None,
    already_annotated: set = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Translate a chunk with structured unit coverage and validation."""
    provider = config.get_translation_provider()
    units = split_chunk_into_translation_units(chunk_text, config)
    if not units:
        return {
            'text': '',
            'segments': [],
            'validation': {'ok': True, 'issues': [], 'invalid_units': [], 'unit_count': 0},
            'attempts': 0,
        }

    best_result: Optional[Dict[str, Any]] = None
    best_invalid_count = None
    failure_hint = ""

    for attempt in range(max_retries):
        try:
            system_prompt = _build_segmented_system_prompt(
                rules, glossary, config,
                annotate_terms=annotate_terms,
                already_annotated=already_annotated,
                failure_hint=failure_hint,
            )
            user_prompt = _build_segmented_user_prompt(units, context)
            response = await provider.generate(system_prompt, user_prompt)

            parse_result = parse_structured_translation_response(response.text, len(units))
            translated_units = list(parse_result['units'])
            if len(translated_units) < len(units):
                translated_units.extend([''] * (len(units) - len(translated_units)))

            validation = validate_translated_units(units, translated_units, config, parse_result=parse_result)

            # If only a small subset failed, repair those units individually instead of
            # paying for another full-chunk pass.
            if validation['invalid_units'] and len(validation['invalid_units']) <= max(3, len(units) // 2):
                repaired_units = list(translated_units)
                for unit_index in validation['invalid_units']:
                    repaired_units[unit_index - 1] = await _translate_single_unit(
                        units[unit_index - 1],
                        context,
                        rules,
                        glossary,
                        config,
                        provider=provider,
                        annotate_terms=annotate_terms,
                        already_annotated=already_annotated,
                        failure_hint=failure_hint or _summarize_validation_issues(validation),
                    )
                repaired_validation = validate_translated_units(units, repaired_units, config)
                if len(repaired_validation['invalid_units']) <= len(validation['invalid_units']):
                    translated_units = repaired_units
                    validation = repaired_validation

            result = {
                'text': _join_translated_units(units, translated_units),
                'segments': _build_segment_payload(units, translated_units),
                'validation': validation,
                'attempts': attempt + 1,
            }

            invalid_count = len(validation['invalid_units'])
            if best_result is None or invalid_count < best_invalid_count:
                best_result = result
                best_invalid_count = invalid_count

            if validation['ok']:
                return result

            failure_hint = _summarize_validation_issues(validation)

        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"  ⚠️  Error on attempt {attempt + 1}: {str(e)}")
                print(f"  ⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                if best_result is None:
                    raise Exception(f"Failed after {max_retries} attempts: {str(e)}")

    if best_result is not None:
        if best_result['validation'].get('invalid_units'):
            print(
                "  ⚠️  Structured validation still found issues in units "
                f"{best_result['validation']['invalid_units']}; returning best repaired result"
            )
        return best_result

    raise Exception(f"Failed after {max_retries} attempts: no translation result")


async def translate_chunk(
    chunk_text: str,
    context: str,
    rules: str,
    glossary: str,
    config: TranslationConfig,
    annotate_terms: list = None,
    already_annotated: set = None,
    max_retries: int = 3
) -> str:
    """Backward-compatible wrapper returning only the translated text."""
    result = await translate_chunk_detailed(
        chunk_text,
        context,
        rules,
        glossary,
        config,
        annotate_terms=annotate_terms,
        already_annotated=already_annotated,
        max_retries=max_retries,
    )
    return result['text']


async def translate_file(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    dry_run: bool = False,
    only_chunks: Optional[List[int]] = None
) -> None:
    """Main translation function.

    Supports both TXT and PDF input. PDF files are processed through
    the input pipeline (text extraction / OCR / normalization).
    """

    rules = load_rules("rules.md")
    glossary, annotate_terms = load_glossary("glossary.json")

    print(f"📖 Reading input file: {input_path}")

    try:
        from input_pipeline import process_input
        result = process_input(input_path, source_lang=config.source_lang)
        text = result.text
        print(f"📄 Input processed (state: {result.state.value})")
        effective_source_lang = result.analysis.get("effective_source_lang", config.source_lang)
        if effective_source_lang != config.source_lang:
            print(f"  ⚠️  source_lang auto-corrected: {config.source_lang} -> {effective_source_lang}")
            config.source_lang = effective_source_lang
        if result.warnings:
            for w in result.warnings:
                print(f"  ⚠️  {w}")
    except Exception as e:
        print(f"❌ Input processing failed: {e}")
        print("   Falling back to plain text read...")
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()

    print(f"✂️  Splitting text into chunks...")
    chunks = split_into_chunks(text, config)
    total_chunks = len(chunks)
    print(f"📊 Total chunks: {total_chunks}")

    if dry_run:
        print("\n🔍 Dry run mode - Chunk analysis:")
        print("="*60)
        total_chars = 0
        total_sentences = 0
        for i, (context, chunk) in enumerate(chunks, start=1):
            chunk_chars = len(chunk)
            chunk_sentences = count_sentences(chunk, config.source_lang)
            context_sentences = count_sentences(context, config.source_lang) if context else 0
            total_chars += chunk_chars
            total_sentences += chunk_sentences
            print(f"\n청크 {i}/{total_chunks}:")
            print(f"  - 문자 수: {chunk_chars:,}")
            print(f"  - 문장 수: {chunk_sentences}")
            if context:
                print(f"  - 컨텍스트 문장 수: {context_sentences}")
        print("\n" + "="*60)
        print(f"총 문자 수: {total_chars:,}")
        print(f"총 문장 수: {total_sentences}")
        print(f"평균 청크 크기: {total_chars // total_chunks:,} 문자")
        print(f"평균 청크 문장 수: {total_sentences // total_chunks} 문장")
        print("\n✅ Dry run 완료 (실제 번역은 수행되지 않음)")
        append_audit_log('translate', {
            'mode': 'dry-run',
            'input_path': str(Path(input_path).resolve()),
            'total_chunks': total_chunks,
        })
        return

    records = build_chunk_records(chunks)
    artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)

    if artifacts['ok']:
        existing_records = artifacts['records']
        if len(existing_records) == len(records):
            for i, rec in enumerate(existing_records):
                if rec.get('source_hash') == records[i]['source_hash']:
                    records[i]['translated_text'] = rec.get('translated_text', '')
                    records[i]['segments'] = rec.get('segments', [])
                    records[i]['validation'] = rec.get('validation', {})
    elif artifacts['reason'] == 'legacy-text-only' and not only_chunks:
        legacy = artifacts['records']
        for i, rec in enumerate(legacy[:len(records)]):
            records[i]['translated_text'] = rec.get('translated_text', '')

    if only_chunks:
        if not Path(output_path).exists():
            print(f"❌ Error: Output file not found: {output_path}")
            print("   --only-chunks requires an existing translation file to patch.")
            return

        if not artifacts['ok']:
            print(f"❌ Error: selective retranslation requires valid metadata ({artifacts['reason']})")
            print("   기존 산출물이 메타데이터 기반이 아니므로 안전하게 패치할 수 없습니다.")
            print("   전체 재번역을 한 번 수행해 메타데이터를 생성한 뒤 다시 시도하세요.")
            return

        invalid = [c for c in only_chunks if c < 1 or c > total_chunks]
        if invalid:
            print(f"❌ Error: Invalid chunk numbers: {invalid} (valid: 1-{total_chunks})")
            return

        warnings = []
        failures = []
        target_count = len(only_chunks)

        for progress, chunk_num in enumerate(only_chunks, start=1):
            idx = chunk_num - 1
            context, chunk = chunks[idx]
            print(f"\n[{progress}/{target_count}] 청크 {chunk_num} 재번역 중...")
            try:
                already_annotated = set()
                if annotate_terms:
                    for prev_idx in range(idx):
                        prev_source = records[prev_idx]['source_text']
                        for term in annotate_terms:
                            if term in prev_source:
                                already_annotated.add(term)

                translation_result = await translate_chunk_detailed(
                    chunk, context, rules, glossary, config,
                    annotate_terms=annotate_terms,
                    already_annotated=already_annotated
                )
                translated = translation_result['text']
                update_record_from_translation_result(records[idx], translation_result)
                save_translation_artifacts(output_path, records, input_path, config)

                source_count = count_sentences(chunk, config.source_lang)
                target_cnt = count_sentences(translated, config.target_lang)
                diff = abs(source_count - target_cnt)
                if diff == 0:
                    print(f"  ✓ 원문 {source_count}문장 / 번역 {target_cnt}문장")
                elif diff <= 2:
                    print(f"  ⚠️  원문 {source_count}문장 / 번역 {target_cnt}문장 (차이: {diff})")
                    warnings.append((chunk_num, source_count, target_cnt))
                else:
                    print(f"  ⚠️⚠️  원문 {source_count}문장 / 번역 {target_cnt}문장 (차이: {diff})")
                    warnings.append((chunk_num, source_count, target_cnt))
            except Exception as e:
                print(f"  ❌ Error: {str(e)}")
                failures.append((chunk_num, str(e)))

        print("\n" + "="*60)
        print("📋 재번역 완료 요약")
        print("="*60)
        print(f"대상 청크: {only_chunks}")
        print(f"성공: {target_count - len(failures)}")
        print(f"실패: {len(failures)}")
        if warnings:
            print("\n⚠️  문장 수 차이:")
            for chunk_num, sc, tc in warnings:
                print(f"  - 청크 {chunk_num}: 원문 {sc} / 번역 {tc}")
        if failures:
            print("\n❌ 실패:")
            for chunk_num, error in failures:
                print(f"  - 청크 {chunk_num}: {error}")
        print(f"\n✅ 저장 완료: {output_path}")
        append_audit_log('translate', {
            'mode': 'only-chunks',
            'input_path': str(Path(input_path).resolve()),
            'output_path': str(Path(output_path).resolve()),
            'target_chunks': only_chunks,
            'success_count': target_count - len(failures),
            'failure_count': len(failures),
        })
        return

    start_chunk = 0
    while start_chunk < len(records) and records[start_chunk].get('translated_text', '').strip():
        start_chunk += 1
    if start_chunk > 0:
        print(f"♻️  Resuming from chunk {start_chunk + 1} (found {start_chunk} completed chunks)")

    warnings = []
    failures = []
    already_annotated = set()
    for prev_idx in range(start_chunk):
        for term in annotate_terms:
            if term in records[prev_idx]['source_text']:
                already_annotated.add(term)

    for i, (context, chunk) in enumerate(chunks[start_chunk:], start=start_chunk):
        chunk_num = i + 1
        print(f"\n[{chunk_num}/{total_chunks}] 번역 중...")
        try:
            translation_result = await translate_chunk_detailed(
                chunk, context, rules, glossary, config,
                annotate_terms=annotate_terms,
                already_annotated=already_annotated
            )
            translated = translation_result['text']
            update_record_from_translation_result(records[i], translation_result)
            save_translation_artifacts(output_path, records, input_path, config)

            if annotate_terms:
                for term in annotate_terms:
                    if term in chunk:
                        already_annotated.add(term)

            source_count = count_sentences(chunk, config.source_lang)
            target_count = count_sentences(translated, config.target_lang)
            diff = abs(source_count - target_count)
            if diff == 0:
                print(f"✓ 원문 {source_count}문장 / 번역 {target_count}문장")
            elif diff <= 2:
                print(f"⚠️  원문 {source_count}문장 / 번역 {target_count}문장 (차이: {diff})")
                warnings.append((chunk_num, source_count, target_count))
            else:
                print(f"⚠️⚠️  원문 {source_count}문장 / 번역 {target_count}문장 (차이: {diff} - 주의 필요!)")
                warnings.append((chunk_num, source_count, target_count))
        except Exception as e:
            print(f"❌ Error in chunk {chunk_num}: {str(e)}")
            failures.append((chunk_num, str(e)))

    print("\n" + "="*60)
    print("📋 번역 완료 요약")
    print("="*60)
    print(f"총 청크 수: {total_chunks}")
    print(f"성공: {total_chunks - len(failures)}")
    print(f"실패: {len(failures)}")
    if warnings:
        print(f"\n⚠️  문장 수 차이가 있는 청크: {len(warnings)}개")
        for chunk_num, source_count, target_count in warnings[:10]:
            print(f"  - 청크 {chunk_num}: 원문 {source_count} / 번역 {target_count}")
        if len(warnings) > 10:
            print(f"  ... 외 {len(warnings) - 10}개")
    if failures:
        print(f"\n❌ 실패한 청크: {len(failures)}개")
        for chunk_num, error in failures:
            print(f"  - 청크 {chunk_num}: {error}")
    print(f"\n✅ 번역 결과 저장: {output_path}")

    append_audit_log('translate', {
        'mode': 'full',
        'input_path': str(Path(input_path).resolve()),
        'output_path': str(Path(output_path).resolve()),
        'total_chunks': total_chunks,
        'warning_chunks': len(warnings),
        'failure_chunks': len(failures),
        'meta_path': str(output_meta_path(output_path).resolve()),
    })


def main():
    parser = argparse.ArgumentParser(description="Translate long-form text using configurable AI providers")
    parser.add_argument("input", help="Input text file path")
    parser.add_argument("-o", "--output", help="Output text file path (not required for --dry-run)")
    parser.add_argument("--config", default="config.yaml", help="Config file path (default: config.yaml)")
    parser.add_argument("--v2", action="store_true", help="Run translator_v2 instead of the legacy chunk pipeline")
    parser.add_argument("--legacy", action="store_true", help="Force the legacy chunk pipeline")
    parser.add_argument("--generation-mode", choices=("api", "codex_local"), default=None,
                        help="Override translator_v2 generation backend")
    parser.add_argument("--codex-worker-mode", choices=("bootstrap", "external"), default=None,
                        help="How codex_local should source worker execution")
    parser.add_argument("--dry-run", action="store_true", help="Show chunk analysis without translating")
    parser.add_argument("--only-chunks", type=str, default=None,
                        help="Retranslate only specified chunks (comma-separated, e.g. 2,5,8)")
    parser.add_argument("--preset", type=str, default=None,
                        help="Genre preset (ja_novel, ja_essay, ja_philosophy, en_fiction, en_nonfiction, en_philosophy)")
    parser.add_argument("--auto-retry", action="store_true",
                        help="Run auto-retry on problem chunks after translation")

    args = parser.parse_args()

    if not args.dry_run and not args.output:
        parser.error("the following arguments are required: -o/--output (unless using --dry-run)")
    if args.v2 and args.legacy:
        parser.error("--v2 and --legacy cannot be used together")

    if not Path(args.input).exists():
        print(f"❌ Error: Input file not found: {args.input}")
        exit(1)

    try:
        config = TranslationConfig(args.config, preset_override=args.preset)
    except FileNotFoundError:
        print(f"❌ Error: Config file not found: {args.config}")
        exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        exit(1)

    if config.preset:
        print(f"📎 Preset: {config.preset.display_name} ({config.preset.name})")

    only_chunks = None
    if args.only_chunks:
        try:
            only_chunks = [int(x.strip()) for x in args.only_chunks.split(",")]
        except ValueError:
            print(f"❌ Error: --only-chunks must be comma-separated integers (e.g. 2,5,8)")
            exit(1)

    pipeline_version = (
        "v2" if args.v2 else
        "legacy" if args.legacy else
        config._raw_config.get("pipeline_version", "legacy")
    )

    try:
        output_path = args.output if args.output else "dummy.txt"

        if pipeline_version == "v2":
            if only_chunks or args.auto_retry:
                if args.v2:
                    if only_chunks:
                        print("❌ Error: --only-chunks is not supported by translator_v2 yet")
                    else:
                        print("❌ Error: --auto-retry is a legacy-only option; translator_v2 already performs local repair")
                    exit(1)
                print("↪ Falling back to legacy translate.py because legacy-only options were requested.")
                pipeline_version = "legacy"

        if pipeline_version == "v2":
            if only_chunks:
                print("❌ Error: --only-chunks is not supported by translator_v2 yet")
                exit(1)
            if args.auto_retry:
                print("❌ Error: --auto-retry is a legacy-only option; translator_v2 already performs local repair")
                exit(1)

            from translator_v2.orchestrate.pipeline import load_v2_config, run_pipeline as run_v2_pipeline

            v2_config = load_v2_config(
                args.config,
                overrides={
                    "source_lang": config.source_lang,
                    "target_lang": config.target_lang,
                    "provider_name": config.provider_name,
                    "model": config.model,
                    "generation_mode": args.generation_mode,
                    "codex_worker_mode": args.codex_worker_mode,
                },
            )
            result = asyncio.run(run_v2_pipeline(
                input_path=args.input,
                output_path=output_path,
                config=v2_config,
                dry_run=args.dry_run,
            ))
            print(f"\n📦 translator_v2 status: {result.status}")
            print(f"   ledger: {result.ledger_path}")
            print(f"   report: {result.report_path}")
            if result.blocked_workfile:
                print(f"   blocked_workfile: {result.blocked_workfile}")
            return

        if args.auto_retry and not args.dry_run and not only_chunks:
            from retry_engine import translate_with_auto_retry
            result = asyncio.run(translate_with_auto_retry(
                args.input, output_path, config,
                preset_name=args.preset or config._raw_config.get('preset'),
            ))
            if result.get('retry_result'):
                rr = result['retry_result']
                print(f"\n📊 Auto-retry summary: {rr['improvement_count']} improved, "
                      f"{len(rr.get('still_problematic', []))} still need review")
        else:
            asyncio.run(translate_file(args.input, output_path, config, args.dry_run, only_chunks))
    except KeyboardInterrupt:
        print("\n\n⏸️  Translation interrupted by user")
        print("   Run the same command again to resume from where you left off")
        exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
