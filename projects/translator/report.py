#!/usr/bin/env python3
"""
Post-editing / human-friendly report generator.

Phase 3 feature: generates structured reports for human reviewers to
efficiently post-edit the translation output.

Report contents (per spec section 14):
- Final translation text
- Chunk metadata summary
- Review summary
- Problem chunk list with severity
- Source/translation parallel view
- Glossary candidate list
- Style notes
- Priority review points

Output formats:
- Human-readable text (.report.txt)
- Structured JSON (.report.json)
- Side-by-side HTML (.report.html)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from translate import (
    TranslationConfig, split_into_chunks, count_sentences,
    load_translation_artifacts, load_glossary,
)
from review import (
    structural_review_chunk, load_glossary as load_glossary_for_review,
    check_glossary_consistency,
)
from retry_engine import score_chunk


def detect_glossary_variant_issues(text: str, glossary: dict) -> List[dict]:
    """Detect inconsistent name/term variants that should have been normalized.

    Looks for source terms / aliases still appearing in the translated output.
    """
    issues = []
    if not glossary or not text:
        return issues

    for source_term, value in glossary.items():
        if isinstance(value, dict):
            translation = value.get('translation', '').strip()
            aliases = value.get('aliases', []) or []
        else:
            translation = str(value).strip()
            aliases = []
        if not translation:
            continue

        leftovers = []
        for candidate in [source_term, *aliases]:
            if candidate and candidate != translation and candidate in text:
                leftovers.append(candidate)
        if leftovers:
            issues.append({
                'source_term': source_term,
                'approved_translation': translation,
                'leftover_variants': leftovers,
            })
    return issues


# ── Report data ─────────────────────────────────────────────

class TranslationReport:
    """Collected data for a post-editing report."""

    def __init__(self):
        self.input_path: str = ""
        self.output_path: str = ""
        self.generated_at: str = ""
        self.preset_name: str = ""

        # Overview
        self.total_chunks: int = 0
        self.source_sentences: int = 0
        self.target_sentences: int = 0
        self.source_chars: int = 0
        self.target_chars: int = 0

        # Chunks with details
        self.chunk_details: List[dict] = []

        # Problems
        self.problem_chunks: List[dict] = []
        self.priority_review_points: List[str] = []

        # Glossary
        self.glossary_consistency: List[dict] = []
        self.glossary_variant_issues: List[dict] = []

        # Style notes
        self.style_notes: List[str] = []

        # Pipeline summary
        self.pipeline_summary: dict = {}

        # Quality gate (phase 4)
        self.quality_status: str = ""  # draft / reviewed / deliverable
        self.quality_blocking_reasons: List[str] = []
        self.quality_warnings: List[str] = []
        self.residue_chunks: List[dict] = []

    def to_dict(self) -> dict:
        result = {
            "input_path": self.input_path,
            "output_path": self.output_path,
            "generated_at": self.generated_at,
            "preset": self.preset_name,
            "overview": {
                "total_chunks": self.total_chunks,
                "source_sentences": self.source_sentences,
                "target_sentences": self.target_sentences,
                "source_chars": self.source_chars,
                "target_chars": self.target_chars,
            },
            "chunk_details": self.chunk_details,
            "problem_chunks": self.problem_chunks,
            "priority_review_points": self.priority_review_points,
            "glossary_consistency": self.glossary_consistency,
            "glossary_variant_issues": self.glossary_variant_issues,
            "style_notes": self.style_notes,
            "pipeline_summary": self.pipeline_summary,
        }
        if self.quality_status:
            result["quality"] = {
                "status": self.quality_status,
                "blocking_reasons": self.quality_blocking_reasons,
                "warnings": self.quality_warnings,
                "residue_chunks": self.residue_chunks,
            }
        return result


# ── Report generation ───────────────────────────────────────

def generate_report(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    pipeline_summary: dict = None,
) -> TranslationReport:
    """Generate a comprehensive post-editing report.

    Args:
        input_path: Source text file
        output_path: Translation output file
        config: Translation config
        pipeline_summary: Optional pipeline run summary

    Returns:
        TranslationReport with all data populated.
    """
    report = TranslationReport()
    report.input_path = str(Path(input_path).resolve())
    report.output_path = str(Path(output_path).resolve())
    report.generated_at = datetime.now().isoformat(timespec='seconds')
    report.preset_name = config.preset.name if config.preset else ""
    report.pipeline_summary = pipeline_summary or {}

    # Load source and translation
    with open(input_path, 'r', encoding='utf-8') as f:
        source_text = f.read()

    artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)
    records = artifacts.get('records', [])

    source_chunks = split_into_chunks(source_text, config)
    report.total_chunks = len(source_chunks)
    report.source_chars = len(source_text)
    report.source_sentences = count_sentences(source_text, config.source_lang)

    translated_text = ""
    if Path(output_path).exists():
        with open(output_path, 'r', encoding='utf-8') as f:
            translated_text = f.read()
    report.target_chars = len(translated_text)
    report.target_sentences = count_sentences(translated_text, config.target_lang)

    # Per-chunk analysis
    preset = config.preset
    threshold_pct = preset.review_threshold_pct if preset else 0.3
    threshold_abs = preset.review_threshold_abs if preset else 5

    for i in range(min(len(source_chunks), len(records))):
        _, source_chunk = source_chunks[i]
        trans_text = records[i].get('translated_text', '')

        review = structural_review_chunk(
            i + 1, source_chunk, trans_text,
            config.source_lang, config.target_lang,
            threshold_pct=threshold_pct,
            threshold_abs=threshold_abs,
        )
        quality_score = score_chunk(review)

        detail = {
            "chunk_num": i + 1,
            "source_sentences": review['source_sentences'],
            "target_sentences": review['target_sentences'],
            "sentence_diff": review['sentence_diff'],
            "quality_score": round(quality_score, 2),
            "is_problem": review['is_problem'],
            "problems": review.get('problems', []),
            "source_preview": source_chunk[:100] + "..." if len(source_chunk) > 100 else source_chunk,
            "target_preview": trans_text[:100] + "..." if len(trans_text) > 100 else trans_text,
        }
        report.chunk_details.append(detail)

        if review['is_problem']:
            severity = "HIGH" if quality_score < 0.4 else "MEDIUM" if quality_score < 0.6 else "LOW"
            problem = {
                "chunk_num": i + 1,
                "severity": severity,
                "quality_score": round(quality_score, 2),
                "problems": review.get('problems', []),
                "number_issues": review.get('number_issues', []),
                "meta_issues": review.get('meta_issues', []),
                "source_preview": source_chunk[:200],
                "target_preview": trans_text[:200],
            }
            report.problem_chunks.append(problem)

    # Priority review points
    high_problems = [p for p in report.problem_chunks if p['severity'] == "HIGH"]
    medium_problems = [p for p in report.problem_chunks if p['severity'] == "MEDIUM"]

    if high_problems:
        chunks_str = ", ".join(str(p['chunk_num']) for p in high_problems)
        report.priority_review_points.append(
            f"HIGH priority: {len(high_problems)} chunks need immediate review: {chunks_str}"
        )
    if medium_problems:
        chunks_str = ", ".join(str(p['chunk_num']) for p in medium_problems)
        report.priority_review_points.append(
            f"MEDIUM priority: {len(medium_problems)} chunks should be checked: {chunks_str}"
        )

    # Sentence count drift
    sentence_diff = report.target_sentences - report.source_sentences
    if abs(sentence_diff) > 20:
        report.priority_review_points.append(
            f"Overall sentence count drift: {sentence_diff:+d} "
            f"(source: {report.source_sentences}, target: {report.target_sentences})"
        )

    # Glossary consistency
    glossary = load_glossary_for_review("glossary.json")
    if glossary and translated_text:
        consistency = check_glossary_consistency(source_text, translated_text, glossary)
        for src_term, src_count, tgt_term, tgt_count, status in consistency:
            entry = {
                "source_term": src_term,
                "target_term": tgt_term,
                "source_count": src_count,
                "target_count": tgt_count,
                "status": "ok" if src_count == tgt_count else "mismatch",
            }
            report.glossary_consistency.append(entry)
            if src_count != tgt_count:
                report.priority_review_points.append(
                    f"Glossary mismatch: '{src_term}' appears {src_count}x "
                    f"but '{tgt_term}' appears {tgt_count}x"
                )

        variant_issues = detect_glossary_variant_issues(translated_text, glossary)
        report.glossary_variant_issues = variant_issues
        for issue in variant_issues:
            leftovers = ", ".join(issue['leftover_variants'])
            report.priority_review_points.append(
                f"Variant normalization needed: '{issue['source_term']}' should be '{issue['approved_translation']}' (found: {leftovers})"
            )

    # Style notes from preset
    if preset:
        report.style_notes.append(f"Genre preset: {preset.display_name}")
        if preset.extra_rules:
            report.style_notes.append(f"Style rule: {preset.extra_rules}")

    return report


# ── Output formatters ───────────────────────────────────────

def write_text_report(report: TranslationReport, path: str) -> None:
    """Write a human-readable text report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  번역 후검수 리포트 (Post-Editing Report)")
    lines.append("=" * 70)
    lines.append(f"생성 시각: {report.generated_at}")
    lines.append(f"원문: {report.input_path}")
    lines.append(f"번역: {report.output_path}")
    if report.preset_name:
        lines.append(f"프리셋: {report.preset_name}")
    lines.append("")

    # Overview
    lines.append("[개요 (Overview)]")
    lines.append(f"  총 청크: {report.total_chunks}")
    lines.append(f"  원문 문장 수: {report.source_sentences}")
    lines.append(f"  번역 문장 수: {report.target_sentences}")
    diff = report.target_sentences - report.source_sentences
    lines.append(f"  문장 수 차이: {diff:+d}")
    lines.append(f"  원문 글자 수: {report.source_chars:,}")
    lines.append(f"  번역 글자 수: {report.target_chars:,}")
    lines.append("")

    # Priority review points
    if report.priority_review_points:
        lines.append("[우선 검토 포인트 (Priority Review)]")
        for point in report.priority_review_points:
            lines.append(f"  * {point}")
        lines.append("")

    # Problem chunks
    if report.problem_chunks:
        lines.append(f"[문제 청크 ({len(report.problem_chunks)}개)]")
        lines.append(f"{'청크':>6}  {'심각도':>8}  {'점수':>6}  문제 유형")
        lines.append("-" * 60)
        for p in sorted(report.problem_chunks, key=lambda x: x['quality_score']):
            problems_str = ", ".join(p['problems'])
            lines.append(f"{p['chunk_num']:>6}  {p['severity']:>8}  {p['quality_score']:>6.2f}  {problems_str}")
        lines.append("")
    else:
        lines.append("[문제 청크: 없음]")
        lines.append("")

    # Glossary consistency
    mismatches = [g for g in report.glossary_consistency if g['status'] == 'mismatch']
    if mismatches:
        lines.append(f"[용어 일관성 문제 ({len(mismatches)}개)]")
        for g in mismatches:
            lines.append(
                f"  {g['source_term']} → {g['target_term']}: "
                f"원문 {g['source_count']}회 / 번역 {g['target_count']}회"
            )
        lines.append("")

    if report.glossary_variant_issues:
        lines.append(f"[표기 흔들림 후보 ({len(report.glossary_variant_issues)}개)]")
        for issue in report.glossary_variant_issues:
            leftovers = ", ".join(issue['leftover_variants'])
            lines.append(
                f"  {issue['source_term']} → {issue['approved_translation']} (잔류 표기: {leftovers})"
            )
        lines.append("")

    # Style notes
    if report.style_notes:
        lines.append("[문체 메모 (Style Notes)]")
        for note in report.style_notes:
            lines.append(f"  - {note}")
        lines.append("")

    # Chunk overview table
    lines.append("[청크별 요약]")
    lines.append(f"{'청크':>6}  {'원문':>6}  {'번역':>6}  {'차이':>6}  {'점수':>6}  상태")
    lines.append("-" * 60)
    for d in report.chunk_details:
        if d['is_problem']:
            status = ", ".join(d['problems'])
        elif abs(d['sentence_diff']) > 2:
            status = "~"
        else:
            status = "OK"
        lines.append(
            f"{d['chunk_num']:>6}  {d['source_sentences']:>6}  "
            f"{d['target_sentences']:>6}  {d['sentence_diff']:>+6}  "
            f"{d['quality_score']:>6.2f}  {status}"
        )
    lines.append("-" * 60)

    # Quality gate status
    if report.quality_status:
        lines.append("")
        status_labels = {
            "draft": "DRAFT (초안)",
            "reviewed": "REVIEWED (검토완료)",
            "deliverable": "DELIVERABLE (납품가능)",
        }
        lines.append(f"[품질 상태 (Quality Status)]")
        lines.append(f"  상태: {status_labels.get(report.quality_status, report.quality_status)}")
        if report.quality_blocking_reasons:
            lines.append(f"  차단 사유 ({len(report.quality_blocking_reasons)}건):")
            for reason in report.quality_blocking_reasons:
                lines.append(f"    [BLOCK] {reason}")
        if report.quality_warnings:
            lines.append(f"  경고 ({len(report.quality_warnings)}건):")
            for warning in report.quality_warnings:
                lines.append(f"    [WARN] {warning}")
        if report.residue_chunks:
            lines.append(f"  원문 잔류 (Source Residue): {len(report.residue_chunks)}개 청크")
            for rc in report.residue_chunks[:5]:
                chunk_num = rc.get('chunk_num', '?')
                severity = rc.get('severity', '?')
                passages = rc.get('passages', [])
                sample = passages[0]['text'][:50] if passages else ''
                lines.append(f"    청크 {chunk_num} [{severity}]: {sample}...")
            if len(report.residue_chunks) > 5:
                lines.append(f"    ... 외 {len(report.residue_chunks) - 5}개")

    # Pipeline summary
    if report.pipeline_summary:
        lines.append("")
        lines.append("[파이프라인 요약]")
        ps = report.pipeline_summary
        if 'total_chunks' in ps:
            lines.append(f"  처리 청크: {ps['total_chunks']}")
        if 'initial_problems' in ps:
            lines.append(f"  초기 문제: {len(ps['initial_problems'])}개")
        if ps.get('retry_result'):
            rr = ps['retry_result']
            lines.append(f"  재시도 개선: {rr.get('improvement_count', 0)}개")
            lines.append(f"  잔여 문제: {len(rr.get('still_problematic', []))}개")

    lines.append("")
    lines.append("=" * 70)
    lines.append("이 리포트는 자동 생성되었습니다. 문제 청크를 우선 검토해주세요.")
    lines.append("=" * 70)

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def write_json_report(report: TranslationReport, path: str) -> None:
    """Write structured JSON report."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)


def write_html_report(report: TranslationReport, path: str,
                      source_chunks: list = None,
                      translated_records: list = None) -> None:
    """Write a side-by-side HTML report for human review.

    Shows source and translation in parallel, with problem chunks highlighted.
    """
    problem_nums = {p['chunk_num'] for p in report.problem_chunks}

    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="ko"><head><meta charset="utf-8">',
        "<title>번역 후검수 리포트</title>",
        "<style>",
        "body { font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif; margin: 20px; background: #f8f9fa; }",
        "h1 { color: #333; }",
        ".summary { background: #fff; padding: 15px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }",
        ".chunk-pair { display: flex; gap: 20px; margin-bottom: 15px; }",
        ".chunk-pair > div { flex: 1; padding: 12px; border-radius: 6px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }",
        ".chunk-pair.problem > div { border-left: 4px solid #e74c3c; }",
        ".chunk-pair.ok > div { border-left: 4px solid #27ae60; }",
        ".chunk-num { font-weight: bold; color: #666; margin-bottom: 6px; font-size: 0.85em; }",
        ".source { background: #fefefe; }",
        ".target { background: #f9f9ff; }",
        ".severity-HIGH { color: #e74c3c; font-weight: bold; }",
        ".severity-MEDIUM { color: #f39c12; font-weight: bold; }",
        ".severity-LOW { color: #3498db; }",
        ".problems-tag { font-size: 0.8em; color: #888; }",
        "table { border-collapse: collapse; width: 100%; margin-top: 10px; }",
        "th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }",
        "th { background: #f2f2f2; }",
        ".priority { background: #fff3cd; padding: 10px; border-radius: 6px; margin-bottom: 15px; }",
        "</style></head><body>",
    ]

    html_parts.append(f"<h1>번역 후검수 리포트</h1>")
    html_parts.append(f'<div class="summary">')
    html_parts.append(f"<p><strong>생성:</strong> {report.generated_at}</p>")
    html_parts.append(f"<p><strong>원문:</strong> {Path(report.input_path).name}</p>")
    html_parts.append(f"<p><strong>총 청크:</strong> {report.total_chunks} / "
                       f"문제 청크: {len(report.problem_chunks)}</p>")
    html_parts.append(f"<p><strong>원문 문장:</strong> {report.source_sentences} / "
                       f"번역 문장: {report.target_sentences} "
                       f"(차이: {report.target_sentences - report.source_sentences:+d})</p>")
    html_parts.append("</div>")

    # Priority review
    if report.priority_review_points:
        html_parts.append('<div class="priority">')
        html_parts.append("<strong>우선 검토 포인트:</strong><ul>")
        for point in report.priority_review_points:
            html_parts.append(f"<li>{_html_escape(point)}</li>")
        html_parts.append("</ul></div>")

    # Problem chunks table
    if report.problem_chunks:
        html_parts.append(f"<h2>문제 청크 ({len(report.problem_chunks)}개)</h2>")
        html_parts.append("<table><tr><th>청크</th><th>심각도</th><th>점수</th><th>문제 유형</th></tr>")
        for p in sorted(report.problem_chunks, key=lambda x: x['quality_score']):
            sev_class = f"severity-{p['severity']}"
            html_parts.append(
                f'<tr><td>{p["chunk_num"]}</td>'
                f'<td class="{sev_class}">{p["severity"]}</td>'
                f'<td>{p["quality_score"]:.2f}</td>'
                f'<td>{", ".join(p["problems"])}</td></tr>'
            )
        html_parts.append("</table>")

    # Side-by-side view
    if source_chunks and translated_records:
        html_parts.append("<h2>원문/번역 대조 보기</h2>")
        for i in range(min(len(source_chunks), len(translated_records))):
            chunk_num = i + 1
            _, source_text = source_chunks[i]
            trans_text = translated_records[i].get('translated_text', '') if isinstance(translated_records[i], dict) else str(translated_records[i])

            is_problem = chunk_num in problem_nums
            pair_class = "problem" if is_problem else "ok"

            # Find severity if problem
            severity_html = ""
            if is_problem:
                for p in report.problem_chunks:
                    if p['chunk_num'] == chunk_num:
                        severity_html = f' <span class="severity-{p["severity"]}">[{p["severity"]}]</span>'
                        break

            html_parts.append(f'<div class="chunk-pair {pair_class}">')
            html_parts.append(f'<div class="source"><div class="chunk-num">원문 청크 {chunk_num}{severity_html}</div>')
            html_parts.append(f'<div>{_html_escape(source_text)}</div></div>')
            html_parts.append(f'<div class="target"><div class="chunk-num">번역 청크 {chunk_num}</div>')
            html_parts.append(f'<div>{_html_escape(trans_text)}</div></div>')
            html_parts.append("</div>")

    html_parts.append("</body></html>")

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html_parts))


def _html_escape(text: str) -> str:
    """Basic HTML escaping."""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\n', '<br>\n'))


# ── Convenience function ────────────────────────────────────

def generate_all_reports(
    input_path: str,
    output_path: str,
    config: TranslationConfig,
    pipeline_summary: dict = None,
    report_dir: str = None,
) -> Dict[str, str]:
    """Generate all report formats.

    Args:
        input_path: Source text file
        output_path: Translation output file
        config: Translation config
        pipeline_summary: Optional pipeline summary
        report_dir: Directory for report files (defaults to same dir as output)

    Returns:
        Dict mapping format names to file paths.
    """
    report = generate_report(input_path, output_path, config, pipeline_summary)

    if report_dir is None:
        report_dir = str(Path(output_path).parent)
    Path(report_dir).mkdir(parents=True, exist_ok=True)

    stem = Path(output_path).stem
    paths = {}

    # Text report
    txt_path = str(Path(report_dir) / f"{stem}.report.txt")
    write_text_report(report, txt_path)
    paths['text'] = txt_path

    # JSON report
    json_path = str(Path(report_dir) / f"{stem}.report.json")
    write_json_report(report, json_path)
    paths['json'] = json_path

    # HTML report (with side-by-side view)
    html_path = str(Path(report_dir) / f"{stem}.report.html")
    with open(input_path, 'r', encoding='utf-8') as f:
        source_text = f.read()
    source_chunks = split_into_chunks(source_text, config)
    artifacts = load_translation_artifacts(output_path, input_path=input_path, config=config)
    records = artifacts.get('records', [])
    write_html_report(report, html_path, source_chunks=source_chunks, translated_records=records)
    paths['html'] = html_path

    return paths
