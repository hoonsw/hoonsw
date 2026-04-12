#!/usr/bin/env python3
"""
Phase 3 validation tests.

Tests the new modules without requiring API access.
Focuses on: parallelization (chapter detection, reassembly), subagent hooks
(task queue, task creation, merge), report generation, finalize stage.
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
print("Phase 3 validation tests")
print("=" * 60)


# ── Chapter detection (parallel.py) ────────────────────────

print("\n[parallel: chapter detection]")
from parallel import detect_chapters, Chapter, reassemble_records, ParallelResult

@test("detect_chapters finds Japanese chapters")
def _():
    text = """第一章 始まり

これは第一章の内容です。長い文章がここに入ります。
もう少し内容を追加して、最低文字数を超えるようにします。
さらにもう少し追加します。これで十分でしょう。
この章はテストのために作られました。内容は重要ではありません。
ただし、最低500文字は必要です。

このパラグラフは第一章の続きです。
もう少し内容を追加して、500文字を超えるようにしましょう。
テスト用のダミーテキストをここに追加します。
日本語のテスト文章です。これはテストです。
もう少しだけ追加します。

第二章 展開

これは第二章の内容です。別の話題に移ります。
ここにも長い文章が入ります。テスト用の文章です。
もう少し追加して最低文字数を超えるようにします。
さらにもう少し追加します。もう少しだけ追加します。
テスト用のダミーテキストをここに追加します。

このパラグラフは第二章の続きです。
もう少し内容を追加して、500文字を超えるようにしましょう。
日本語のテスト文章です。テストテスト。
もう少しだけ追加して終わりにします。"""

    chapters = detect_chapters(text, "ja", min_chapter_size=100)
    assert len(chapters) == 2, f"Expected 2 chapters, got {len(chapters)}"
    assert "第一章" in chapters[0].title
    assert "第二章" in chapters[1].title


@test("detect_chapters finds English chapters")
def _():
    text = """Chapter 1 Introduction

This is the first chapter content with enough text to pass the minimum size.
We need at least 500 characters for a proper chapter detection test.
Let me add more content here to ensure we exceed the threshold requirement.
This chapter discusses the basic introduction and setup of the topic.
More content follows to pad out the chapter to the required length.

Additional paragraph for the first chapter to make it long enough.
Testing the chapter detection functionality with English text content.
We need a good amount of text to properly test the detection logic.

Chapter 2 Development

This is the second chapter with different content about development.
We also need sufficient text here to ensure proper chapter detection.
Adding more content to exceed the minimum chapter size threshold.
This chapter covers the development phase of the project in detail.
More sentences to pad the chapter out to the required character count.

Another paragraph for chapter two with additional test content here.
Ensuring the chapter is long enough for proper detection and testing."""

    chapters = detect_chapters(text, "en", min_chapter_size=100)
    assert len(chapters) == 2, f"Expected 2 chapters, got {len(chapters)}"
    assert "Chapter 1" in chapters[0].title
    assert "Chapter 2" in chapters[1].title


@test("detect_chapters returns full doc when no chapters")
def _():
    text = "Just a plain paragraph without any chapter headings at all."
    chapters = detect_chapters(text, "ja")
    assert len(chapters) == 1
    assert chapters[0].title == "(full document)"


@test("detect_chapters indexes are sequential")
def _():
    text = """第一章 A

テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容

第二章 B

テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容

第三章 C

テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容
テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容テスト内容"""

    chapters = detect_chapters(text, "ja", min_chapter_size=100)
    assert len(chapters) == 3
    for i, ch in enumerate(chapters):
        assert ch.index == i, f"Chapter {i} has index {ch.index}"


@test("reassemble_records produces flat ordered list")
def _():
    result = ParallelResult(
        chapters=[
            Chapter(index=0, title="Ch1", text="text1"),
            Chapter(index=1, title="Ch2", text="text2"),
        ],
        chapter_translations={0: "trans1", 1: "trans2"},
        chapter_chunks={
            0: [{"index": 1, "translated_text": "a"}, {"index": 2, "translated_text": "b"}],
            1: [{"index": 1, "translated_text": "c"}],
        },
    )
    records = reassemble_records(result)
    assert len(records) == 3
    assert records[0]['index'] == 1
    assert records[0]['chapter_index'] == 0
    assert records[2]['index'] == 3
    assert records[2]['chapter_index'] == 1


@test("ParallelResult.full_translation assembles in order")
def _():
    result = ParallelResult(
        chapters=[
            Chapter(index=0, title="Ch1", text=""),
            Chapter(index=1, title="Ch2", text=""),
        ],
        chapter_translations={1: "second", 0: "first"},
        chapter_chunks={},
    )
    text = result.full_translation
    assert text.index("first") < text.index("second")


# ── Subagent hooks ──────────────────────────────────────────

print("\n[subagent_hooks: task queue]")
from subagent_hooks import (
    SubagentTask, SubagentResult, SubagentTaskQueue,
    create_review_tasks, create_rework_tasks, merge_rework_results,
)

@test("task serialization roundtrip")
def _():
    task = SubagentTask(
        task_id="test-review-1",
        task_type="review",
        chunk_num=5,
        source_text="原文",
        translated_text="번역",
        problems=["under_translate"],
    )
    d = task.to_dict()
    task2 = SubagentTask.from_dict(d)
    assert task2.task_id == "test-review-1"
    assert task2.chunk_num == 5
    assert task2.problems == ["under_translate"]


@test("result serialization roundtrip")
def _():
    result = SubagentResult(
        task_id="test-review-1",
        status="completed",
        result="looks good",
        improved_translation="개선된 번역",
    )
    d = result.to_dict()
    result2 = SubagentResult.from_dict(d)
    assert result2.task_id == "test-review-1"
    assert result2.improved_translation == "개선된 번역"


@test("task queue enqueue/claim/complete cycle")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = SubagentTaskQueue(tmpdir)
        task = SubagentTask(
            task_id="q-test-1", task_type="review",
            chunk_num=1, source_text="src",
        )
        queue.enqueue(task)

        summary = queue.status_summary()
        assert summary['pending'] == 1

        claimed = queue.claim("q-test-1")
        assert claimed is not None
        assert claimed.task_id == "q-test-1"
        assert queue.status_summary()['pending'] == 0
        assert queue.status_summary()['in_progress'] == 1

        result = SubagentResult(task_id="q-test-1", result="ok")
        queue.complete("q-test-1", result)
        assert queue.status_summary()['completed'] == 1
        assert queue.status_summary()['in_progress'] == 0


@test("task queue batch enqueue")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = SubagentTaskQueue(tmpdir)
        tasks = [
            SubagentTask(task_id=f"batch-{i}", task_type="review",
                         chunk_num=i, source_text=f"src-{i}")
            for i in range(5)
        ]
        paths = queue.enqueue_batch(tasks)
        assert len(paths) == 5
        assert queue.status_summary()['pending'] == 5


@test("task queue fail moves task correctly")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = SubagentTaskQueue(tmpdir)
        task = SubagentTask(task_id="fail-test", task_type="rework",
                            chunk_num=1, source_text="src")
        queue.enqueue(task)
        queue.claim("fail-test")
        queue.fail("fail-test", "timeout")
        assert queue.status_summary()['failed'] == 1
        assert queue.status_summary()['in_progress'] == 0


@test("task queue list_pending and list_completed")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = SubagentTaskQueue(tmpdir)
        for i in range(3):
            task = SubagentTask(task_id=f"list-{i}", task_type="review",
                                chunk_num=i, source_text=f"src-{i}")
            queue.enqueue(task)

        pending = queue.list_pending()
        assert len(pending) == 3

        queue.claim("list-0")
        queue.complete("list-0", SubagentResult(task_id="list-0", result="ok"))
        completed = queue.list_completed()
        assert len(completed) == 1


@test("create_review_tasks builds correct tasks")
def _():
    from translate import TranslationConfig
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("chunk_size: 2000\nsource_lang: ja\ntarget_lang: ko\nmodel: claude-sonnet-4-6\n")
        f.flush()
        config_path = f.name
    try:
        config = TranslationConfig(config_path)
        source_chunks = [("ctx", "原文1"), ("ctx", "原文2"), ("ctx", "原文3")]
        trans_chunks = ["번역1", "번역2", "번역3"]
        tasks = create_review_tasks(source_chunks, trans_chunks, [1, 3], config)
        assert len(tasks) == 2
        assert tasks[0].chunk_num == 1
        assert tasks[1].chunk_num == 3
        assert tasks[0].task_type == "review"
    finally:
        os.unlink(config_path)


@test("merge_rework_results updates records")
def _():
    records = [
        {"index": 1, "translated_text": "old1"},
        {"index": 2, "translated_text": "old2"},
        {"index": 3, "translated_text": "old3"},
    ]
    results = {
        "rework-chunk-2": SubagentResult(
            task_id="rework-chunk-2",
            improved_translation="new2",
        ),
    }
    updated = merge_rework_results(records, results)
    assert updated == [2]
    assert records[1]['translated_text'] == "new2"
    assert records[0]['translated_text'] == "old1"  # unchanged


# ── Report generation ───────────────────────────────────────

print("\n[report: generation]")
from report import (
    TranslationReport, generate_report,
    write_text_report, write_json_report, write_html_report,
)

@test("TranslationReport to_dict has required fields")
def _():
    report = TranslationReport()
    report.input_path = "/test/input.txt"
    report.total_chunks = 10
    d = report.to_dict()
    assert "input_path" in d
    assert "overview" in d
    assert d["overview"]["total_chunks"] == 10


@test("write_text_report creates readable file")
def _():
    report = TranslationReport()
    report.input_path = "/test/input.txt"
    report.output_path = "/test/output.txt"
    report.generated_at = "2026-03-21T12:00:00"
    report.total_chunks = 10
    report.source_sentences = 100
    report.target_sentences = 105
    report.source_chars = 5000
    report.target_chars = 6000
    report.problem_chunks = [
        {"chunk_num": 3, "severity": "HIGH", "quality_score": 0.3, "problems": ["under_translate"],
         "number_issues": [], "meta_issues": [], "source_preview": "...", "target_preview": "..."},
    ]
    report.priority_review_points = ["HIGH priority: 1 chunks need review: 3"]
    report.chunk_details = [
        {"chunk_num": i+1, "source_sentences": 10, "target_sentences": 10+i,
         "sentence_diff": i, "quality_score": 0.9, "is_problem": i==2, "problems": ["under_translate"] if i==2 else []}
        for i in range(10)
    ]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        path = f.name
    try:
        write_text_report(report, path)
        with open(path, 'r') as f:
            content = f.read()
        assert "번역 후검수 리포트" in content
        assert "청크" in content
        assert "HIGH" in content
    finally:
        os.unlink(path)


@test("write_json_report creates valid JSON")
def _():
    report = TranslationReport()
    report.input_path = "/test/input.txt"
    report.total_chunks = 5

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        path = f.name
    try:
        write_json_report(report, path)
        with open(path, 'r') as f:
            data = json.load(f)
        assert data['input_path'] == "/test/input.txt"
        assert data['overview']['total_chunks'] == 5
    finally:
        os.unlink(path)


@test("write_html_report creates HTML file")
def _():
    report = TranslationReport()
    report.input_path = "/test/input.txt"
    report.output_path = "/test/output.txt"
    report.generated_at = "2026-03-21T12:00:00"
    report.total_chunks = 2
    report.source_sentences = 20
    report.target_sentences = 22
    report.problem_chunks = []
    report.priority_review_points = []

    source_chunks = [("", "原文テスト1"), ("", "原文テスト2")]
    trans_records = [
        {"translated_text": "번역 테스트 1"},
        {"translated_text": "번역 테스트 2"},
    ]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
        path = f.name
    try:
        write_html_report(report, path, source_chunks=source_chunks, translated_records=trans_records)
        with open(path, 'r') as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "번역 후검수 리포트" in content
        assert "원문 청크" in content
    finally:
        os.unlink(path)


# ── Finalize stage ──────────────────────────────────────────

print("\n[finalize: text cleanup]")
from finalize import cleanup_translation_text, FinalizeResult

@test("cleanup removes trailing whitespace")
def _():
    text = "line one   \nline two  \n"
    result = cleanup_translation_text(text)
    assert "   \n" not in result
    assert "  \n" not in result


@test("cleanup collapses excessive blank lines")
def _():
    text = "para one\n\n\n\n\n\npara two"
    result = cleanup_translation_text(text)
    # Should have at most 3 consecutive newlines (2 blank lines)
    assert "\n\n\n\n" not in result
    assert "para one" in result
    assert "para two" in result


@test("cleanup ensures trailing newline")
def _():
    text = "some text"
    result = cleanup_translation_text(text)
    assert result.endswith('\n')


@test("cleanup handles already clean text")
def _():
    text = "clean text here\n"
    result = cleanup_translation_text(text)
    assert result == text


@test("FinalizeResult summary has required fields")
def _():
    result = FinalizeResult()
    result.run_id = "test-run"
    result.run_dir = "/tmp/runs/test"
    s = result.summary()
    assert s['run_id'] == "test-run"
    assert 'reports' in s


# ── Integration: generate_report with real files ────────────

print("\n[report: integration]")

@test("generate_report with sample files")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create sample input
        input_path = os.path.join(tmpdir, "input.txt")
        with open(input_path, 'w', encoding='utf-8') as f:
            f.write("これはテスト文です。二番目の文章です。三番目の文です。\n\nもう一つの段落です。")

        # Create sample output with metadata
        output_path = os.path.join(tmpdir, "output.txt")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("이것은 테스트 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다.\n\n또 하나의 문단입니다.")

        # Create minimal config
        config_path = os.path.join(tmpdir, "config.yaml")
        with open(config_path, 'w') as f:
            f.write("chunk_size: 2000\nsource_lang: ja\ntarget_lang: ko\nmodel: claude-sonnet-4-6\n")

        from translate import TranslationConfig
        config = TranslationConfig(config_path)
        report = generate_report(input_path, output_path, config)

        assert report.total_chunks > 0
        assert report.source_sentences > 0
        assert report.target_sentences > 0


# ── Pipeline integration ────────────────────────────────────

print("\n[pipeline: CLI flags]")

@test("run_pipeline.py has --parallel flag")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert "--parallel" in result.stdout


@test("run_pipeline.py has --report flag")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert "--report" in result.stdout


@test("run_pipeline.py has --finalize flag")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert "--finalize" in result.stdout


@test("run_pipeline.py has --dispatch-subagents flag")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert "--dispatch-subagents" in result.stdout


@test("run_pipeline.py has --deliver-to flag")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--help"],
        capture_output=True, text=True
    )
    assert "--deliver-to" in result.stdout


@test("finalize.py CLI help works")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "finalize.py", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--deliver-to" in result.stdout
    assert "--input" in result.stdout


# ── Backward compatibility ──────────────────────────────────

print("\n[backward compatibility]")

@test("phase 2 imports still work")
def _():
    from run_pipeline import run_pipeline
    from retry_engine import retry_problem_chunks, RetryResult, score_chunk
    from review import structural_review_chunk, run_structural_review, PROBLEM_TYPES
    from presets import get_preset, list_presets, PRESETS
    from progress import ProgressTracker, notify_openclaw


@test("phase 3 imports work")
def _():
    from parallel import detect_chapters, translate_parallel, reassemble_records, Chapter
    from subagent_hooks import SubagentTask, SubagentResult, SubagentTaskQueue
    from subagent_hooks import create_review_tasks, create_rework_tasks, merge_rework_results
    from report import generate_report, write_text_report, write_json_report, write_html_report
    from finalize import finalize_output, cleanup_translation_text, FinalizeResult


@test("run_pipeline.py --list-presets still works")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--list-presets"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "ja_novel" in result.stdout


@test("review.py PROBLEM_TYPES unchanged")
def _():
    from review import PROBLEM_TYPES
    expected = [
        "ocr_noise", "meta_text_leak", "over_split", "under_translate",
        "source_residue", "hallucination", "name_inconsistency",
        "tone_drift", "structure_mismatch"
    ]
    assert PROBLEM_TYPES == expected


# ── Summary ──────────────────────────────────────────────────

print("\n" + "=" * 60)
print(f"Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, err in errors:
        print(f"  - {name}: {err}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
