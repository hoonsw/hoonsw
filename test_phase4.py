#!/usr/bin/env python3
"""
Phase 4 validation tests.

Tests the new modules without requiring API access.
Focuses on: Japanese residue detection, quality gate evaluation,
output status levels, delivery blocking, enhanced progress tracking,
and report/manifest quality fields.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure we're in the right directory
os.chdir(Path(__file__).parent)

passed = 0
failed = 0
errors = []


def test(name):
    global passed, failed
    def decorator(func):
        global passed, failed
        try:
            func()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
            errors.append((name, str(e)))
    return decorator


print("=" * 60)
print("Phase 4 validation tests")
print("=" * 60)


# ── Japanese residue detection ──────────────────────────────

print("\n[quality_gate: residue detection]")
from quality_gate import (
    count_hiragana, count_katakana,
    find_japanese_passages, detect_residue_in_chunk,
    detect_residue_bulk, detect_source_residue_in_chunk,
    find_english_passages, detect_english_residue_in_chunk,
    evaluate_quality_gate, format_verdict_summary,
    QualityVerdict,
    STATUS_DRAFT, STATUS_REVIEWED, STATUS_DELIVERABLE,
)

@test("count_hiragana detects hiragana characters")
def _():
    assert count_hiragana("これはテストです") == 5  # こ れ は で す
    assert count_hiragana("한국어만 있는 텍스트") == 0
    assert count_hiragana("") == 0

@test("count_katakana detects katakana characters")
def _():
    assert count_katakana("テスト") == 3
    assert count_katakana("한국어") == 0

@test("find_japanese_passages finds untranslated sentence")
def _():
    text = "이것은 한국어 번역입니다. これは翻訳されていない文章です。그리고 더 한국어."
    passages = find_japanese_passages(text)
    assert len(passages) > 0, "Should find Japanese passage"
    found_types = {p['type'] for p in passages}
    assert 'sentence' in found_types or 'passage' in found_types, \
        f"Should find sentence or passage, got: {found_types}"

@test("find_japanese_passages ignores pure Korean text")
def _():
    text = "이것은 완전한 한국어 문장입니다. 일본어가 전혀 없습니다. 모든 것이 올바르게 번역되었습니다."
    passages = find_japanese_passages(text)
    assert len(passages) == 0, f"Should find no passages in pure Korean, got {len(passages)}"

@test("find_japanese_passages detects hiragana particles")
def _():
    text = "번역 결과물에 のために라는 표현이 남아있습니다"
    passages = find_japanese_passages(text)
    assert len(passages) > 0, "Should detect hiragana particles"

@test("find_japanese_passages handles mixed passage")
def _():
    text = "第一章の内容は次のとおりです"  # Full Japanese passage
    passages = find_japanese_passages(text)
    assert len(passages) > 0, "Should detect Japanese passage with kanji+hiragana"

@test("detect_residue_in_chunk severity: none for clean Korean")
def _():
    text = "이것은 깨끗한 한국어 번역입니다. 문제가 없습니다."
    result = detect_residue_in_chunk(text, chunk_num=1)
    assert result['severity'] == 'none', f"Expected 'none', got '{result['severity']}'"

@test("detect_residue_in_chunk severity: critical for untranslated sentence")
def _():
    text = "한국어 부분. これは翻訳されていない文章です。더 많은 한국어."
    result = detect_residue_in_chunk(text, chunk_num=1)
    assert result['severity'] == 'critical', f"Expected 'critical', got '{result['severity']}'"
    assert result['critical_count'] > 0

@test("detect_residue_in_chunk tracks chunk_num")
def _():
    text = "テスト文です。"
    result = detect_residue_in_chunk(text, chunk_num=42)
    assert result['chunk_num'] == 42

@test("detect_residue_bulk only checks ja source lang")
def _():
    chunks = ["これは日本語です。", "Another test."]
    # English source lang should skip residue check
    results = detect_residue_bulk(chunks, source_lang="en")
    assert len(results) == 0, "Should not check residue for non-ja source"

@test("find_english_passages detects long copied English passage")
def _():
    text = "이 문단은 번역되었지만 This is a full English sentence left behind in output. 가 남아 있습니다."
    passages = find_english_passages(
        text,
        source_text="This is a full English sentence left behind in output.",
    )
    assert len(passages) >= 1, "Should detect suspicious English passage"

@test("detect_english_residue_in_chunk marks copied English as critical")
def _():
    text = "한국어 문장. This is a full English sentence left behind in output."
    result = detect_english_residue_in_chunk(
        text,
        source_text="This is a full English sentence left behind in output.",
    )
    assert result["severity"] == "critical"
    assert result["critical_count"] >= 1

@test("detect_source_residue_in_chunk dispatches by source language")
def _():
    result = detect_source_residue_in_chunk(
        "한국어 문장. This is a full English sentence left behind in output.",
        source_lang="en",
        source_text="This is a full English sentence left behind in output.",
    )
    assert result["severity"] == "critical"

@test("detect_residue_bulk uses source chunks for English workflows")
def _():
    chunks = ["한국어 문장. This is a full English sentence left behind in output."]
    source_chunks = ["This is a full English sentence left behind in output."]
    results = detect_residue_bulk(chunks, source_lang="en", source_chunks=source_chunks)
    assert len(results) == 1
    assert results[0]["severity"] == "critical"

@test("detect_residue_bulk finds residue across chunks")
def _():
    chunks = [
        "이것은 깨끗한 번역입니다.",
        "이 부분에 これは残った日本語です。 남아있습니다.",
        "또 다른 깨끗한 번역.",
    ]
    results = detect_residue_bulk(chunks, source_lang="ja")
    assert len(results) >= 1, f"Should find residue in at least 1 chunk, got {len(results)}"
    # The residue should be in chunk 2
    chunk_nums = [r['chunk_num'] for r in results]
    assert 2 in chunk_nums, f"Chunk 2 should have residue, got chunks: {chunk_nums}"


# ── Quality gate evaluation ─────────────────────────────────

print("\n[quality_gate: evaluation]")

@test("STATUS constants are correct strings")
def _():
    assert STATUS_DRAFT == "draft"
    assert STATUS_REVIEWED == "reviewed"
    assert STATUS_DELIVERABLE == "deliverable"

@test("evaluate_quality_gate: clean output is deliverable")
def _():
    verdict = evaluate_quality_gate(
        total_chunks=10,
        problem_chunks=[1, 3],
        retry_result={'still_problematic': [], 'improvement_count': 2},
        residue_results=[],
        source_lang="ja",
    )
    assert verdict.status == STATUS_DELIVERABLE
    assert verdict.is_deliverable
    assert not verdict.is_blocked
    assert len(verdict.blocking_reasons) == 0

@test("evaluate_quality_gate: critical residue blocks delivery")
def _():
    residue = [{
        'chunk_num': 5,
        'severity': 'critical',
        'hiragana_count': 20,
        'hiragana_ratio': 0.15,
        'passages': [{'text': 'これは翻訳されていない', 'type': 'sentence'}],
        'critical_count': 1,
        'minor_count': 0,
    }]
    verdict = evaluate_quality_gate(
        total_chunks=10,
        problem_chunks=[],
        residue_results=residue,
        source_lang="ja",
    )
    assert verdict.status == STATUS_REVIEWED, f"Expected 'reviewed', got '{verdict.status}'"
    assert verdict.is_blocked
    assert any("residue" in r.lower() for r in verdict.blocking_reasons)

@test("evaluate_quality_gate: high problem ratio blocks delivery")
def _():
    verdict = evaluate_quality_gate(
        total_chunks=10,
        problem_chunks=[1, 2, 3, 4, 5],
        retry_result={'still_problematic': [1, 2, 3, 4, 5], 'improvement_count': 0},
        residue_results=[],
    )
    assert verdict.is_blocked
    assert any("problem ratio" in r.lower() for r in verdict.blocking_reasons)

@test("evaluate_quality_gate: empty pipeline is draft")
def _():
    verdict = evaluate_quality_gate(total_chunks=0, problem_chunks=[])
    assert verdict.status == STATUS_DRAFT

@test("evaluate_quality_gate: strict mode blocks on warnings")
def _():
    residue = [{
        'chunk_num': 3,
        'severity': 'warning',
        'hiragana_count': 5,
        'hiragana_ratio': 0.03,
        'passages': [{'text': 'テスト', 'type': 'particle_cluster'}],
        'critical_count': 0,
        'minor_count': 3,
    }]
    # Non-strict: warning should not block
    verdict_normal = evaluate_quality_gate(
        total_chunks=10, problem_chunks=[], residue_results=residue, strict=False,
    )
    assert verdict_normal.is_deliverable

    # Strict: warning should block
    verdict_strict = evaluate_quality_gate(
        total_chunks=10, problem_chunks=[], residue_results=residue, strict=True,
    )
    assert not verdict_strict.is_deliverable

@test("QualityVerdict.to_dict has all required fields")
def _():
    v = QualityVerdict()
    v.status = STATUS_REVIEWED
    v.blocking_reasons = ["test reason"]
    d = v.to_dict()
    assert 'status' in d
    assert 'is_deliverable' in d
    assert 'blocking_reasons' in d
    assert 'warnings' in d
    assert 'residue_score' in d
    assert 'chunks_with_residue' in d

@test("format_verdict_summary produces readable output")
def _():
    v = QualityVerdict()
    v.status = STATUS_REVIEWED
    v.blocking_reasons = ["Critical residue in chunk 5"]
    v.warnings = ["Minor residue in chunk 8"]
    v.chunks_with_residue = 2
    v.total_chunks_checked = 10
    summary = format_verdict_summary(v)
    assert "REVIEWED" in summary
    assert "BLOCK" in summary
    assert "WARN" in summary


# ── Enhanced progress tracker ──────────────────────────────

print("\n[progress: enhanced tracking]")
from progress import ProgressTracker

@test("tracker has PIPELINE_STAGES defined")
def _():
    stages = ProgressTracker.PIPELINE_STAGES
    assert len(stages) >= 8
    stage_names = [s[0] for s in stages]
    assert "translation" in stage_names
    assert "quality_gate" in stage_names
    assert "finalize" in stage_names

@test("tracker phase includes stage numbering")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.phase("structural_review")
    events = tracker.get_events()
    phase_events = [e for e in events if e['type'] == 'phase']
    assert len(phase_events) >= 1
    # Phase message should contain numbering like [1/3]
    assert "[" in phase_events[0]['message']

@test("tracker get_status includes new fields")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(20)
    tracker.phase("translation")
    tracker.update_chunk(10, 20)
    status = tracker.get_status()
    assert 'overall_progress_pct' in status
    assert 'active_stages' in status
    assert 'completed_stages' in status
    assert 'quality_status' in status
    assert status['overall_progress_pct'] >= 0

@test("tracker set_quality_status works")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.set_quality_status("deliverable")
    status = tracker.get_status()
    assert status['quality_status'] == "deliverable"

@test("tracker complete shows quality status")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.set_quality_status("reviewed")
    tracker.complete(summary={'quality_verdict': {'status': 'reviewed'}})
    events = tracker.get_events()
    complete_events = [e for e in events if e['type'] == 'complete']
    assert len(complete_events) == 1
    assert "REVIEWED" in complete_events[0]['message']

@test("tracker tracks completed stages")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.phase("translation")
    tracker.phase("structural_review")
    tracker.phase("quality_gate")
    status = tracker.get_status()
    assert "translation" in status['completed_stages']
    assert "structural_review" in status['completed_stages']
    assert status['phase'] == "quality_gate"


# ── Report quality fields ──────────────────────────────────

print("\n[report: quality fields]")
from report import TranslationReport, write_text_report, write_json_report

@test("TranslationReport has quality fields")
def _():
    report = TranslationReport()
    assert hasattr(report, 'quality_status')
    assert hasattr(report, 'quality_blocking_reasons')
    assert hasattr(report, 'quality_warnings')
    assert hasattr(report, 'residue_chunks')

@test("TranslationReport to_dict includes quality when set")
def _():
    report = TranslationReport()
    report.quality_status = "reviewed"
    report.quality_blocking_reasons = ["test block"]
    d = report.to_dict()
    assert 'quality' in d
    assert d['quality']['status'] == "reviewed"
    assert d['quality']['blocking_reasons'] == ["test block"]

@test("TranslationReport to_dict omits quality when empty")
def _():
    report = TranslationReport()
    d = report.to_dict()
    assert 'quality' not in d

@test("write_text_report includes quality status section")
def _():
    report = TranslationReport()
    report.input_path = "/test/input.txt"
    report.output_path = "/test/output.txt"
    report.generated_at = "2026-03-22T12:00:00"
    report.total_chunks = 5
    report.source_sentences = 50
    report.target_sentences = 52
    report.source_chars = 2000
    report.target_chars = 2500
    report.quality_status = "reviewed"
    report.quality_blocking_reasons = ["Critical residue in chunk 3"]
    report.quality_warnings = ["Minor residue in chunk 7"]
    report.residue_chunks = [
        {'chunk_num': 3, 'severity': 'critical',
         'passages': [{'text': 'これはテスト', 'type': 'sentence'}]}
    ]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        path = f.name
    try:
        write_text_report(report, path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "품질 상태" in content or "Quality Status" in content
        assert "REVIEWED" in content
        assert "BLOCK" in content
        assert "WARN" in content
    finally:
        os.unlink(path)


# ── Finalize quality gate integration ──────────────────────

print("\n[finalize: quality gate]")
from finalize import FinalizeResult

@test("FinalizeResult has quality fields")
def _():
    result = FinalizeResult()
    assert hasattr(result, 'quality_verdict')
    assert hasattr(result, 'delivery_blocked')
    assert hasattr(result, 'delivery_block_reason')

@test("FinalizeResult summary includes quality when set")
def _():
    result = FinalizeResult()
    result.run_id = "test-run"
    result.quality_verdict = QualityVerdict()
    result.quality_verdict.status = "reviewed"
    result.quality_verdict.blocking_reasons = ["test"]
    result.delivery_blocked = True
    result.delivery_block_reason = "blocked reason"
    s = result.summary()
    assert s.get('quality_status') == "reviewed"
    assert s.get('delivery_blocked') is True
    assert 'quality_verdict' in s


# ── Pipeline integration (CLI) ──────────────────────────────

print("\n[pipeline: quality gate integration]")

@test("run_pipeline.py imports quality_gate")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         "from run_pipeline import run_pipeline; "
         "from quality_gate import evaluate_quality_gate, detect_residue_bulk"],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Import failed: {result.stderr}"

@test("run_pipeline.py --help still works")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--finalize" in result.stdout
    assert "--report" in result.stdout


# ── Backward compatibility ──────────────────────────────────

print("\n[backward compatibility]")

@test("phase 2 imports still work after phase 4 changes")
def _():
    from run_pipeline import run_pipeline
    from retry_engine import retry_problem_chunks, RetryResult, score_chunk
    from review import structural_review_chunk, run_structural_review, PROBLEM_TYPES
    from presets import get_preset, list_presets, PRESETS
    from progress import ProgressTracker, notify_openclaw

@test("phase 3 imports still work after phase 4 changes")
def _():
    from parallel import detect_chapters, translate_parallel, reassemble_records, Chapter
    from subagent_hooks import SubagentTask, SubagentResult, SubagentTaskQueue
    from report import generate_report, write_text_report, write_json_report, write_html_report
    from finalize import finalize_output, cleanup_translation_text, FinalizeResult

@test("review.py PROBLEM_TYPES unchanged")
def _():
    from review import PROBLEM_TYPES
    expected = [
        "ocr_noise", "meta_text_leak", "over_split", "under_translate",
        "source_residue", "hallucination", "name_inconsistency",
        "tone_drift", "structure_mismatch"
    ]
    assert PROBLEM_TYPES == expected

@test("existing tracker events still work")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.phase("translation")
    tracker.update_chunk(5, 10)
    tracker.report_problems([1, 3])
    tracker.complete()
    events = tracker.get_events()
    assert len(events) >= 4
    assert events[0]['type'] == 'start'

@test("run_pipeline.py --list-presets still works")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--list-presets"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "ja_novel" in result.stdout


# ── Edge cases ────────────────────────────────────────────

print("\n[edge cases]")

@test("residue detection handles empty string")
def _():
    result = detect_residue_in_chunk("", chunk_num=1)
    assert result['severity'] == 'none'

@test("residue detection handles only-katakana annotation")
def _():
    # Katakana in parentheses is often an annotation, not residue
    text = "이 단어는 (カタカナ) 표기입니다."
    result = detect_residue_in_chunk(text, chunk_num=1)
    # Should not be critical (it's an annotation pattern)
    assert result['severity'] != 'critical', \
        f"Annotation katakana should not be critical, got {result['severity']}"

@test("quality gate with no retry result still works")
def _():
    verdict = evaluate_quality_gate(
        total_chunks=10,
        problem_chunks=[1, 2],
        retry_result=None,
        residue_results=[],
    )
    # 2/10 = 20% which is > 10%, should block
    assert verdict.is_blocked
    assert any("problem ratio" in r.lower() for r in verdict.blocking_reasons)

@test("quality gate with minor residue only gives deliverable")
def _():
    residue = [{
        'chunk_num': 3,
        'severity': 'minor',
        'hiragana_count': 2,
        'hiragana_ratio': 0.005,
        'passages': [{'text': 'の', 'type': 'particle_cluster'}],
        'critical_count': 0,
        'minor_count': 1,
    }]
    verdict = evaluate_quality_gate(
        total_chunks=10,
        problem_chunks=[],
        retry_result={'still_problematic': [], 'improvement_count': 0},
        residue_results=residue,
    )
    assert verdict.is_deliverable, \
        f"Minor-only residue should be deliverable, got: {verdict.status}"


# ── Summary ──────────────────────────────────────────────────

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, err in errors:
        print(f"  - {name}: {err}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
