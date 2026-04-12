#!/usr/bin/env python3

import os
import sys
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from quality_gate import evaluate_quality_gate
from readability_gate import detect_readability_bulk, detect_readability_in_chunk


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
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
            errors.append((name, str(exc)))

    return decorator


print("=" * 60)
print("readability_gate tests")
print("=" * 60)


@test("detects spaced hangul as critical readability issue")
def _():
    result = detect_readability_in_chunk("이 번 역 문 은 아 주 부 자 연 스 럽 다", target_lang="ko", chunk_num=1)
    assert result["severity"] == "critical"
    assert any(item["type"] == "spaced_hangul" for item in result["findings"])


@test("detects fragmented short-line translation as critical readability issue")
def _():
    text = "\n".join([
        "짧은 줄",
        "또 짧은 줄",
        "계속 짧은 줄",
        "계속 이어짐",
        "또 짧다",
        "여전히 짧다",
        "마지막 짧은 줄",
    ])
    result = detect_readability_in_chunk(text * 4, target_lang="ko", chunk_num=1)
    assert result["severity"] == "critical"
    assert any(item["type"] == "fragmented_lines" for item in result["findings"])


@test("quality gate blocks deliverable on critical readability issues")
def _():
    readability = detect_readability_bulk(["이 번 역 문 은 아 주 부 자 연 스 럽 다"], target_lang="ko")
    verdict = evaluate_quality_gate(
        total_chunks=1,
        problem_chunks=[],
        residue_results=[],
        readability_results=readability,
        source_lang="en",
        target_lang="ko",
    )
    assert verdict.status == "reviewed"
    assert not verdict.is_deliverable
    assert verdict.chunks_with_readability_issues == 1


print("\n" + "=" * 60)
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if errors:
    print("\nErrors:")
    for name, error in errors:
        print(f"  - {name}: {error}")
    raise SystemExit(1)

print("\nAll readability_gate tests passed.")
