#!/usr/bin/env python3
"""
Quality gate module for translation pipeline.

Provides:
1. Japanese source residue detection in Korean output (ja→ko workflows)
2. Output status levels: draft / reviewed / deliverable
3. Delivery quality gate: blocks outputs with critical issues from being
   marked as deliverable

Integrated at the pipeline level between review/retry and finalize.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

from readability_gate import detect_readability_bulk

# ── Output status levels ────────────────────────────────────

STATUS_DRAFT = "draft"
STATUS_REVIEWED = "reviewed"
STATUS_DELIVERABLE = "deliverable"

VALID_STATUSES = {STATUS_DRAFT, STATUS_REVIEWED, STATUS_DELIVERABLE}


@dataclass
class QualityVerdict:
    """Result of the quality gate evaluation."""

    status: str = STATUS_DRAFT  # draft / reviewed / deliverable
    blocking_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    residue_chunks: List[dict] = field(default_factory=list)
    residue_score: float = 0.0  # 0.0 = no residue, 1.0 = all residue
    total_chunks_checked: int = 0
    chunks_with_residue: int = 0
    readability_chunks: List[dict] = field(default_factory=list)
    chunks_with_readability_issues: int = 0

    @property
    def is_deliverable(self) -> bool:
        return self.status == STATUS_DELIVERABLE

    @property
    def is_blocked(self) -> bool:
        return len(self.blocking_reasons) > 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "is_deliverable": self.is_deliverable,
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
            "residue_score": round(self.residue_score, 4),
            "total_chunks_checked": self.total_chunks_checked,
            "chunks_with_residue": self.chunks_with_residue,
            "residue_chunks": self.residue_chunks,
            "chunks_with_readability_issues": self.chunks_with_readability_issues,
            "readability_chunks": self.readability_chunks,
        }


# ── Japanese residue detection ──────────────────────────────

# Hiragana: U+3040–U+309F
# Katakana: U+30A0–U+30FF (shared with Korean transliterations — handled below)
# CJK common: U+4E00–U+9FFF (shared between JA/KO/ZH — need context)
# Katakana special: ー (U+30FC) is common in both JA and KO loan words

# Characters that are uniquely Japanese (not used in Korean text)
_HIRAGANA_RE = re.compile(r'[\u3040-\u309F]')
_KATAKANA_RE = re.compile(r'[\u30A0-\u30FF]')

# A "passage" of Japanese: 3+ consecutive Japanese-script characters
# (hiragana/katakana), possibly with CJK kanji interspersed
_JA_PASSAGE_RE = re.compile(
    r'[\u3040-\u309F\u30A0-\u30FF]'          # starts with kana
    r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3000-\u303F\s、。！？「」『』（）]*'
    r'[\u3040-\u309F\u30A0-\u30FF]',          # ends with kana
    re.UNICODE,
)

# Sentence-level Japanese: hiragana particles/endings that prove Japanese
# (Korean doesn't use hiragana at all)
_JA_PARTICLE_RE = re.compile(
    r'[\u3040-\u309F]{2,}',  # 2+ hiragana in a row
    re.UNICODE,
)

# Full Japanese sentence: contains hiragana (the most reliable indicator)
_JA_SENTENCE_RE = re.compile(
    r'[^\n。！？]*[\u3040-\u309F]+[^\n。！？]*[。！？\n]',
    re.UNICODE,
)

# Common false positives: single katakana words that are also used in Korean
# (borrowed terms written in katakana parenthetical annotations)
_ANNOTATION_RE = re.compile(r'\([\u30A0-\u30FF\u3040-\u309F]+\)')

_EN_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_EN_PASSAGE_RE = re.compile(
    r'(?:\b[A-Za-z]+(?:[-\'][A-Za-z]+)*\b(?:[\s,;:"“”\'()\[\]{}<>/\\-]+|$)){4,}',
    re.UNICODE,
)
_EN_COMMON_WORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'in',
    'into', 'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'their',
    'there', 'this', 'to', 'was', 'were', 'with', 'you', 'your',
}


def count_hiragana(text: str) -> int:
    """Count hiragana characters in text. Hiragana is uniquely Japanese."""
    return len(_HIRAGANA_RE.findall(text))


def count_katakana(text: str) -> int:
    """Count katakana characters in text."""
    return len(_KATAKANA_RE.findall(text))


def find_japanese_passages(text: str) -> List[dict]:
    """Find Japanese passages/segments in text.

    Returns list of dicts with:
      - text: the matched Japanese passage
      - start: character offset
      - end: character offset
      - hiragana_count: number of hiragana chars (most reliable indicator)
      - type: 'passage' | 'sentence' | 'particle_cluster'
    """
    results = []
    seen_spans = set()

    # Find full Japanese sentences (most critical: entire untranslated sentences)
    for m in _JA_SENTENCE_RE.finditer(text):
        span_text = m.group().strip()
        if count_hiragana(span_text) >= 2:  # at least 2 hiragana = definitely Japanese
            span_key = (m.start(), m.end())
            if span_key not in seen_spans:
                seen_spans.add(span_key)
                results.append({
                    "text": span_text,
                    "start": m.start(),
                    "end": m.end(),
                    "hiragana_count": count_hiragana(span_text),
                    "type": "sentence",
                })

    # Find Japanese passages (fragments that didn't match as full sentences)
    for m in _JA_PASSAGE_RE.finditer(text):
        span_text = m.group().strip()
        if len(span_text) < 3:
            continue
        # Skip if inside an annotation like (カタカナ)
        if _ANNOTATION_RE.search(text[max(0, m.start()-1):m.end()+1]):
            continue
        span_key = (m.start(), m.end())
        if span_key not in seen_spans:
            # Check it's not already covered by a sentence match
            overlaps = any(
                s <= m.start() and m.end() <= e
                for (s, e) in seen_spans
            )
            if not overlaps:
                seen_spans.add(span_key)
                results.append({
                    "text": span_text,
                    "start": m.start(),
                    "end": m.end(),
                    "hiragana_count": count_hiragana(span_text),
                    "type": "passage",
                })

    # Find hiragana particle clusters (e.g., のために, ではない)
    for m in _JA_PARTICLE_RE.finditer(text):
        span_text = m.group()
        if len(span_text) < 2:
            continue
        span_key = (m.start(), m.end())
        overlaps = any(
            s <= m.start() and m.end() <= e
            for (s, e) in seen_spans
        )
        if not overlaps:
            seen_spans.add(span_key)
            results.append({
                "text": span_text,
                "start": m.start(),
                "end": m.end(),
                "hiragana_count": len(span_text),
                "type": "particle_cluster",
            })

    return sorted(results, key=lambda r: r["start"])


def detect_residue_in_chunk(
    translated_text: str,
    chunk_num: int = 0,
) -> dict:
    """Detect Japanese source residue in a single translated chunk.

    Returns:
        dict with residue details for this chunk.
    """
    passages = find_japanese_passages(translated_text)

    # Classify severity
    total_hiragana = count_hiragana(translated_text)
    total_chars = max(len(translated_text), 1)
    hiragana_ratio = total_hiragana / total_chars

    # Separate critical (sentences/passages) from minor (particle clusters)
    critical = [p for p in passages if p["type"] in ("sentence", "passage")]
    minor = [p for p in passages if p["type"] == "particle_cluster"]

    severity = "none"
    if critical:
        severity = "critical"
    elif len(minor) >= 3 or hiragana_ratio > 0.02:
        severity = "warning"
    elif minor:
        severity = "minor"

    return {
        "chunk_num": chunk_num,
        "severity": severity,
        "hiragana_count": total_hiragana,
        "hiragana_ratio": round(hiragana_ratio, 4),
        "passages": [{"text": p["text"], "type": p["type"]} for p in passages],
        "critical_count": len(critical),
        "minor_count": len(minor),
    }


def _normalize_english_text(text: str) -> str:
    return " ".join(w.lower() for w in _EN_WORD_RE.findall(text))


def find_english_passages(text: str, source_text: Optional[str] = None) -> List[dict]:
    """Find suspicious English passages left in translated text.

    This intentionally stays conservative:
    - short proper nouns are ignored
    - title-like phrases are downgraded unless they are long
    - when source_text is available, we only keep passages that substantially
      overlap with the source, so citations or accidental English additions do
      not dominate the signal
    """
    results = []
    normalized_source = _normalize_english_text(source_text or "")
    source_words = set(_EN_WORD_RE.findall((source_text or "").lower()))

    for match in _EN_PASSAGE_RE.finditer(text):
        span_text = match.group().strip()
        words = _EN_WORD_RE.findall(span_text)
        if len(words) < 4:
            continue

        joined_len = len(" ".join(words))
        if joined_len < 18:
            continue

        lower_words = [w.lower() for w in words]
        common_count = sum(1 for w in lower_words if w in _EN_COMMON_WORDS)
        title_like = all(w[:1].isupper() or w.isupper() for w in words if w[:1].isalpha())

        if common_count == 0 and len(words) < 6:
            continue

        overlap_ratio = 0.0
        if source_words:
            overlap_ratio = sum(1 for w in lower_words if w in source_words) / len(lower_words)
        candidate_norm = " ".join(lower_words)
        if normalized_source:
            if candidate_norm not in normalized_source and overlap_ratio < 0.6:
                continue

        kind = "sentence" if len(words) >= 6 or common_count >= 2 else "passage"
        results.append({
            "text": span_text,
            "start": match.start(),
            "end": match.end(),
            "word_count": len(words),
            "common_word_count": common_count,
            "type": kind,
            "title_like": title_like,
        })

    return results


def detect_english_residue_in_chunk(
    translated_text: str,
    chunk_num: int = 0,
    source_text: Optional[str] = None,
) -> dict:
    passages = find_english_passages(translated_text, source_text=source_text)
    critical = [
        p for p in passages
        if p["type"] == "sentence" and not p.get("title_like")
    ]
    minor = [p for p in passages if p not in critical]

    severity = "none"
    if critical:
        severity = "critical"
    elif len(minor) >= 2:
        severity = "warning"
    elif minor:
        severity = "minor"

    return {
        "chunk_num": chunk_num,
        "severity": severity,
        "english_word_count": sum(p["word_count"] for p in passages),
        "passages": [{"text": p["text"], "type": p["type"]} for p in passages],
        "critical_count": len(critical),
        "minor_count": len(minor),
    }


def detect_source_residue_in_chunk(
    translated_text: str,
    source_lang: str = "ja",
    chunk_num: int = 0,
    source_text: Optional[str] = None,
) -> dict:
    """Detect source-language residue in a translated chunk."""
    if source_lang == "ja":
        return detect_residue_in_chunk(translated_text, chunk_num=chunk_num)
    if source_lang == "en":
        return detect_english_residue_in_chunk(
            translated_text,
            chunk_num=chunk_num,
            source_text=source_text,
        )

    return {
        "chunk_num": chunk_num,
        "severity": "none",
        "passages": [],
        "critical_count": 0,
        "minor_count": 0,
    }


def detect_residue_bulk(
    chunks: List[str],
    source_lang: str = "ja",
    source_chunks: Optional[List[str]] = None,
) -> List[dict]:
    """Detect Japanese residue across all translated chunks.

    Args:
        chunks: list of translated text strings
        source_lang: source language. 'ja' and 'en' are supported.
        source_chunks: optional aligned source chunks. Required for useful
            English residue detection.

    Returns:
        List of residue results per chunk (only chunks with residue).
    """
    if source_lang not in {"ja", "en"}:
        return []
    if source_lang == "en" and not source_chunks:
        return []

    results = []
    for i, chunk_text in enumerate(chunks):
        source_text = source_chunks[i] if source_chunks and i < len(source_chunks) else None
        result = detect_source_residue_in_chunk(
            chunk_text,
            source_lang=source_lang,
            chunk_num=i + 1,
            source_text=source_text,
        )
        if result["severity"] != "none":
            results.append(result)
    return results


# ── Quality gate evaluation ─────────────────────────────────

def evaluate_quality_gate(
    total_chunks: int,
    problem_chunks: List[int],
    retry_result: Optional[dict] = None,
    residue_results: Optional[List[dict]] = None,
    source_lang: str = "ja",
    readability_results: Optional[List[dict]] = None,
    target_lang: str = "ko",
    strict: bool = False,
) -> QualityVerdict:
    """Evaluate the quality gate and determine output status.

    Rules:
    - draft: initial translation, not yet reviewed
    - reviewed: passed structural review + retry, but may have residual issues
    - deliverable: passed all quality gates with no critical blockers

    Blocking conditions (prevent deliverable status):
    1. Any chunk with critical Japanese residue (untranslated sentences)
    2. More than 10% of chunks still problematic after retry
    3. In strict mode: any residue warnings also block

    Args:
        total_chunks: total number of chunks
        problem_chunks: chunk numbers flagged by structural review
        retry_result: retry summary dict (from RetryResult.summary())
        residue_results: output of detect_residue_bulk()
        source_lang: source language code
        strict: if True, warnings also block deliverable status

    Returns:
        QualityVerdict with status and reasons.
    """
    verdict = QualityVerdict()
    verdict.total_chunks_checked = total_chunks

    if total_chunks == 0:
        verdict.status = STATUS_DRAFT
        return verdict

    # Start with reviewed (we got past structural review)
    verdict.status = STATUS_REVIEWED

    # Check retry results
    still_problematic = []
    if retry_result:
        still_problematic = retry_result.get("still_problematic", [])

    # Problem ratio check
    problematic_after_retry = len(still_problematic) if retry_result else len(problem_chunks)
    problem_ratio = problematic_after_retry / total_chunks

    if problem_ratio > 0.10:
        verdict.blocking_reasons.append(
            f"High problem ratio: {problematic_after_retry}/{total_chunks} "
            f"chunks ({problem_ratio:.0%}) still problematic after retry"
        )

    source_lang_label = {
        "ja": "Japanese",
        "en": "English",
    }.get(source_lang, "Source-language")

    # Source residue check
    if residue_results:
        critical_residue = [r for r in residue_results if r["severity"] == "critical"]
        warning_residue = [r for r in residue_results if r["severity"] == "warning"]
        minor_residue = [r for r in residue_results if r["severity"] == "minor"]

        verdict.residue_chunks = residue_results
        verdict.chunks_with_residue = len(residue_results)

        # Compute overall residue score
        total_critical = sum(r.get("critical_count", 0) for r in residue_results)
        total_hiragana = sum(r.get("hiragana_count", 0) for r in residue_results)
        # Weighted score: critical passages weigh more
        verdict.residue_score = min(1.0, (total_critical * 0.1 + total_hiragana * 0.001))

        if critical_residue:
            chunk_nums = [r["chunk_num"] for r in critical_residue]
            passages_sample = []
            for r in critical_residue[:3]:  # show first 3
                for p in r.get("passages", [])[:2]:
                    passages_sample.append(p["text"][:60])

            verdict.blocking_reasons.append(
                f"Critical {source_lang_label} residue in {len(critical_residue)} chunk(s): "
                f"{chunk_nums}. "
                f"Examples: {passages_sample}"
            )

        if warning_residue:
            chunk_nums = [r["chunk_num"] for r in warning_residue]
            msg = f"{source_lang_label} residue warnings in {len(warning_residue)} chunk(s): {chunk_nums}"
            if strict:
                verdict.blocking_reasons.append(msg)
            else:
                verdict.warnings.append(msg)

        if minor_residue:
            chunk_nums = [r["chunk_num"] for r in minor_residue]
            verdict.warnings.append(
                f"Minor {source_lang_label} residue in {len(minor_residue)} chunk(s): {chunk_nums}"
            )

    if readability_results:
        critical_readability = [r for r in readability_results if r.get("severity") == "critical"]
        warning_readability = [r for r in readability_results if r.get("severity") == "warning"]
        verdict.readability_chunks = readability_results
        verdict.chunks_with_readability_issues = len(readability_results)

        if critical_readability:
            chunk_nums = [r["chunk_num"] for r in critical_readability]
            examples = []
            for item in critical_readability[:3]:
                for finding in item.get("findings", [])[:2]:
                    examples.extend(finding.get("examples", [])[:1])
            verdict.blocking_reasons.append(
                f"Critical readability issues in {len(critical_readability)} chunk(s): "
                f"{chunk_nums}. Examples: {examples}"
            )

        if warning_readability:
            chunk_nums = [r["chunk_num"] for r in warning_readability]
            msg = (
                f"Readability warnings in {len(warning_readability)} chunk(s): {chunk_nums}"
            )
            if strict:
                verdict.blocking_reasons.append(msg)
            else:
                verdict.warnings.append(msg)

    # Determine final status
    if verdict.blocking_reasons:
        verdict.status = STATUS_REVIEWED  # blocked from deliverable
    else:
        verdict.status = STATUS_DELIVERABLE

    return verdict


def format_verdict_summary(verdict: QualityVerdict) -> str:
    """Format a human-readable summary of the quality verdict."""
    lines = []
    status_labels = {
        STATUS_DRAFT: "DRAFT (초안)",
        STATUS_REVIEWED: "REVIEWED (검토완료)",
        STATUS_DELIVERABLE: "DELIVERABLE (납품가능)",
    }
    lines.append(f"Output status: {status_labels.get(verdict.status, verdict.status)}")

    if verdict.blocking_reasons:
        lines.append(f"BLOCKED — {len(verdict.blocking_reasons)} reason(s):")
        for reason in verdict.blocking_reasons:
            lines.append(f"  [BLOCK] {reason}")

    if verdict.warnings:
        lines.append(f"Warnings ({len(verdict.warnings)}):")
        for warning in verdict.warnings:
            lines.append(f"  [WARN] {warning}")

    if verdict.chunks_with_residue > 0:
        lines.append(
            f"Residue: {verdict.chunks_with_residue}/{verdict.total_chunks_checked} "
            f"chunks affected (score: {verdict.residue_score:.3f})"
        )
    if verdict.chunks_with_readability_issues > 0:
        lines.append(
            f"Readability: {verdict.chunks_with_readability_issues}/{verdict.total_chunks_checked} "
            f"chunks affected"
        )

    return "\n".join(lines)
