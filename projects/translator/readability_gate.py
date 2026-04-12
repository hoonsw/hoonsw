#!/usr/bin/env python3
"""
Readability-oriented heuristics for translated Korean output.

These checks intentionally focus on high-signal structural failures rather than
subjective style quality.
"""

import re
from typing import List, Optional


_HANGUL_CHAR_RE = re.compile(r"[가-힣]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[A-Za-z가-힣]\))\s+")


def _find_spaced_hangul_sequences(text: str) -> List[str]:
    findings: List[str] = []
    for line in text.splitlines():
        tokens = line.strip().split()
        streak: List[str] = []
        for token in tokens:
            if re.fullmatch(r"[가-힣]", token):
                streak.append(token)
            else:
                if len(streak) >= 4:
                    findings.append(" ".join(streak))
                streak = []
        if len(streak) >= 4:
            findings.append(" ".join(streak))
    return findings


def _fragmented_line_summary(text: str) -> Optional[dict]:
    body = text.strip()
    if len(body) < 140:
        return None
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in raw_lines if not _LIST_LINE_RE.match(line)]
    if len(lines) < 6:
        return None
    short_lines = [line for line in lines if len(line) < 18]
    if len(short_lines) < 4:
        return None
    ratio = len(short_lines) / len(lines)
    if ratio < 0.6:
        return None
    return {
        "line_count": len(lines),
        "short_line_count": len(short_lines),
        "ratio": round(ratio, 4),
        "examples": short_lines[:3],
    }


def _low_hangul_ratio_summary(text: str, target_lang: str) -> Optional[dict]:
    if target_lang != "ko":
        return None
    body = text.strip()
    if len(body) < 80:
        return None
    hangul_count = len(_HANGUL_CHAR_RE.findall(body))
    latin_words = _LATIN_WORD_RE.findall(body)
    if len(latin_words) < 6:
        return None
    alpha_count = sum(1 for char in body if char.isalpha())
    if alpha_count == 0:
        return None
    hangul_ratio = hangul_count / alpha_count
    if hangul_ratio >= 0.2:
        return None
    return {
        "hangul_count": hangul_count,
        "latin_word_count": len(latin_words),
        "hangul_ratio": round(hangul_ratio, 4),
        "example_words": latin_words[:8],
    }


def detect_readability_in_chunk(
    translated_text: str,
    *,
    target_lang: str = "ko",
    chunk_num: int = 0,
) -> dict:
    findings = []

    spaced_hangul = _find_spaced_hangul_sequences(translated_text)
    if spaced_hangul:
        findings.append({
            "type": "spaced_hangul",
            "severity": "critical",
            "examples": spaced_hangul[:3],
        })

    fragmented = _fragmented_line_summary(translated_text)
    if fragmented:
        findings.append({
            "type": "fragmented_lines",
            "severity": "critical",
            "examples": fragmented["examples"],
            "line_count": fragmented["line_count"],
            "short_line_count": fragmented["short_line_count"],
            "ratio": fragmented["ratio"],
        })

    low_hangul = _low_hangul_ratio_summary(translated_text, target_lang=target_lang)
    if low_hangul:
        findings.append({
            "type": "low_hangul_ratio",
            "severity": "warning",
            "examples": low_hangul["example_words"],
            "hangul_ratio": low_hangul["hangul_ratio"],
            "latin_word_count": low_hangul["latin_word_count"],
        })

    severity = "none"
    if any(item["severity"] == "critical" for item in findings):
        severity = "critical"
    elif findings:
        severity = "warning"

    return {
        "chunk_num": chunk_num,
        "severity": severity,
        "findings": findings,
    }


def detect_readability_bulk(
    chunks: List[str],
    *,
    target_lang: str = "ko",
) -> List[dict]:
    results = []
    for index, chunk in enumerate(chunks, start=1):
        result = detect_readability_in_chunk(chunk, target_lang=target_lang, chunk_num=index)
        if result["severity"] != "none":
            results.append(result)
    return results
