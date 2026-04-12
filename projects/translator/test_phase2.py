#!/usr/bin/env python3
"""
Phase 2 validation tests.

Tests the new modules without requiring API access.
Focuses on: presets, retry scoring, chunk splitting, glossary workflow,
progress tracker, and config integration.
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
print("Phase 2 validation tests")
print("=" * 60)

# ── Presets ───────────────────────────────────────────────────

print("\n[presets]")
from presets import get_preset, list_presets, PRESETS, GenrePreset

@test("all 6 presets exist")
def _():
    expected = {"ja_novel", "ja_essay", "ja_philosophy", "en_fiction", "en_nonfiction", "en_philosophy"}
    assert set(PRESETS.keys()) == expected, f"Got: {set(PRESETS.keys())}"

@test("get_preset returns correct type")
def _():
    p = get_preset("ja_novel")
    assert isinstance(p, GenrePreset)
    assert p.name == "ja_novel"

@test("get_preset returns None for unknown")
def _():
    assert get_preset("nonexistent") is None

@test("list_presets returns dicts with required keys")
def _():
    presets = list_presets()
    assert len(presets) == 6
    for p in presets:
        assert "name" in p
        assert "display_name" in p
        assert "description" in p

@test("preset has prompt_modifier")
def _():
    p = get_preset("ja_novel")
    assert p.prompt_modifier, "ja_novel should have prompt_modifier"
    assert "대사" in p.prompt_modifier

@test("preset review thresholds differ by genre")
def _():
    novel = get_preset("ja_novel")
    philosophy = get_preset("ja_philosophy")
    assert philosophy.review_threshold_pct < novel.review_threshold_pct, \
        "Philosophy should be stricter than novel"


# ── Retry engine: scoring ────────────────────────────────────

print("\n[retry_engine: scoring]")
from retry_engine import score_chunk, split_chunk_for_retry

@test("perfect chunk scores 1.0")
def _():
    result = {
        "source_sentences": 5,
        "target_sentences": 5,
        "sentence_diff": 0,
        "is_problem": False,
        "problems": [],
        "number_issues": [],
        "bracket_issues": [],
        "meta_issues": [],
    }
    assert score_chunk(result) == 1.0

@test("bad chunk scores low")
def _():
    result = {
        "source_sentences": 10,
        "target_sentences": 3,
        "sentence_diff": -7,
        "is_problem": True,
        "problems": ["under_translate", "structure_mismatch"],
        "number_issues": ["Number '42' missing"],
        "bracket_issues": [],
        "meta_issues": [],
    }
    score = score_chunk(result)
    assert score < 0.5, f"Score {score} should be < 0.5 for bad chunk"

@test("meta_text_leak penalizes score")
def _():
    clean = {
        "source_sentences": 5, "target_sentences": 5, "sentence_diff": 0,
        "is_problem": False, "problems": [],
        "number_issues": [], "bracket_issues": [], "meta_issues": [],
    }
    dirty = {
        **clean,
        "is_problem": True, "problems": ["meta_text_leak"],
        "meta_issues": ["pattern matched"],
    }
    assert score_chunk(dirty) < score_chunk(clean)


# ── Retry engine: chunk splitting ────────────────────────────

print("\n[retry_engine: split]")

@test("short chunk not split")
def _():
    text = "短い文。もう一つ。"
    result = split_chunk_for_retry(text, "ja", max_sub_size=1000)
    assert len(result) == 1

@test("long chunk splits into sub-chunks")
def _():
    # Create a long text
    sentences = ["これはテスト文です。"] * 50
    text = "".join(sentences)
    result = split_chunk_for_retry(text, "ja", max_sub_size=200)
    assert len(result) > 1, f"Expected >1 sub-chunks, got {len(result)}"

@test("split preserves all content")
def _():
    sentences = ["Sentence one. ", "Sentence two. ", "Sentence three. "] * 10
    text = "".join(sentences)
    result = split_chunk_for_retry(text, "en", max_sub_size=100)
    recombined = " ".join(result)
    # All original sentences should be present
    assert "Sentence one" in recombined
    assert "Sentence three" in recombined


# ── Translation structure / coverage ───────────────────────

print("\n[translate: structure]")
from translate import (
    count_sentences, split_chunk_into_translation_units,
    parse_structured_translation_response, validate_translated_units,
    TranslationUnit,
)

def make_config(source_lang="en", target_lang="ko", chunk_size=2000):
    cfg = object.__new__(type("Cfg", (), {}))
    cfg.source_lang = source_lang
    cfg.target_lang = target_lang
    cfg.chunk_size = chunk_size
    return cfg

@test("count_sentences for Korean does not overcount blank lines")
def _():
    text = "첫 문장입니다.\n\n둘째 문장입니다."
    assert count_sentences(text, "ko") == 2

@test("split_chunk_into_translation_units preserves paragraph grouping")
def _():
    cfg = make_config(source_lang="en", target_lang="ko", chunk_size=200)
    text = "First paragraph sentence one. First paragraph sentence two.\n\nSecond paragraph."
    units = split_chunk_into_translation_units(text, cfg)
    assert len(units) >= 2
    assert units[0].separator_after in {" ", "\n\n"}

@test("parse_structured_translation_response detects missing units")
def _():
    parsed = parse_structured_translation_response(
        "[[SEGMENT 1]]\n첫 번째\n[[END SEGMENT 1]]",
        expected_count=2,
    )
    assert parsed["missing_units"] == [2]
    assert parsed["parse_error"]

@test("validate_translated_units flags copied source text")
def _():
    cfg = make_config(source_lang="en", target_lang="ko", chunk_size=200)
    units = [TranslationUnit(index=1, source_text="This sentence must be translated.")]
    result = validate_translated_units(
        units,
        ["This sentence must be translated."],
        cfg,
        parse_result={"missing_units": [], "duplicate_units": [], "parse_error": ""},
    )
    assert not result["ok"]
    assert result["exact_copy_units"] == [1]


# ── Glossary workflow ────────────────────────────────────────

print("\n[glossary_workflow]")
from glossary_workflow import (
    GlossaryWorkflow, normalize_entry, minimize_entry,
    STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_MODIFIED,
)

@test("normalize_entry handles string value")
def _():
    entry = normalize_entry("test", "테스트")
    assert entry["translation"] == "테스트"
    assert entry["status"] == STATUS_APPROVED  # legacy = approved
    assert entry["mode"] == "translate"

@test("normalize_entry handles dict value")
def _():
    entry = normalize_entry("test", {
        "translation": "테스트",
        "category": "concept",
        "annotate": True,
    })
    assert entry["translation"] == "테스트"
    assert entry["category"] == "concept"
    assert entry["annotate"] is True

@test("minimize_entry strips empty fields")
def _():
    full = {
        "translation": "foo",
        "category": "",
        "mode": "translate",
        "annotate": False,
        "aliases": [],
        "first_mention_note": "",
        "note": "",
        "status": STATUS_APPROVED,
        "approved_at": None,
    }
    result = minimize_entry(full)
    assert result == {"translation": "foo"}, f"Got: {result}"

@test("workflow import/approve/sync cycle")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create draft
        draft_path = os.path.join(tmpdir, "draft.json")
        with open(draft_path, 'w') as f:
            json.dump({
                "テスト": {"translation": "테스트", "category": "concept"},
                "サンプル": {"translation": "샘플", "category": "concept"},
            }, f)

        wf_path = os.path.join(tmpdir, "workflow.json")
        gl_path = os.path.join(tmpdir, "glossary.json")
        # Create empty glossary
        with open(gl_path, 'w') as f:
            json.dump({}, f)

        wf = GlossaryWorkflow(wf_path, gl_path)

        # Import
        added = wf.import_draft(draft_path)
        assert added == 2

        # Check pending
        pending = wf.get_pending()
        assert len(pending) == 2

        # Approve one
        wf.approve("テスト")
        pending = wf.get_pending()
        assert len(pending) == 1

        # Reject one
        wf.reject("サンプル")
        pending = wf.get_pending()
        assert len(pending) == 0

        # Sync to glossary
        synced = wf.sync_to_glossary()
        assert synced == 1  # Only approved one

        with open(gl_path, 'r') as f:
            glossary = json.load(f)
        assert "テスト" in glossary
        assert "サンプル" not in glossary

@test("workflow approve_all_pending")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        draft_path = os.path.join(tmpdir, "draft.json")
        with open(draft_path, 'w') as f:
            json.dump({"A": "a", "B": "b", "C": "c"}, f)

        wf_path = os.path.join(tmpdir, "workflow.json")
        gl_path = os.path.join(tmpdir, "glossary.json")
        with open(gl_path, 'w') as f:
            json.dump({}, f)

        wf = GlossaryWorkflow(wf_path, gl_path)
        wf.import_draft(draft_path)
        count = wf.approve_all_pending()
        assert count == 3

@test("workflow summary counts are correct")
def _():
    with tempfile.TemporaryDirectory() as tmpdir:
        draft_path = os.path.join(tmpdir, "draft.json")
        with open(draft_path, 'w') as f:
            json.dump({"A": "a", "B": "b", "C": "c"}, f)

        wf_path = os.path.join(tmpdir, "workflow.json")
        gl_path = os.path.join(tmpdir, "glossary.json")
        with open(gl_path, 'w') as f:
            json.dump({}, f)

        wf = GlossaryWorkflow(wf_path, gl_path)
        wf.import_draft(draft_path)
        wf.approve("A")
        wf.reject("B")

        s = wf.summary()
        assert s['total'] == 3
        assert s['approved'] == 1
        assert s['rejected'] == 1
        assert s['pending'] == 1


# ── Progress tracker ─────────────────────────────────────────

print("\n[progress]")
from progress import ProgressTracker

@test("tracker records events")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(10)
    tracker.phase("translation")
    tracker.update_chunk(5, 10)
    tracker.complete()

    events = tracker.get_events()
    assert len(events) >= 3
    assert events[0]['type'] == 'start'

@test("tracker status shows correct state")
def _():
    tracker = ProgressTracker(input_path="test.txt", notify=False)
    tracker.start(20)
    tracker.update_chunk(10, 20)
    status = tracker.get_status()
    assert status['total_chunks'] == 20
    assert status['completed_chunks'] == 10
    assert status['phase'] == 'translation'

@test("tracker log file works")
def _():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        log_path = f.name

    try:
        tracker = ProgressTracker(input_path="test.txt", notify=False, log_path=log_path)
        tracker.start(5)
        tracker.complete()

        with open(log_path, 'r') as f:
            lines = f.readlines()
        assert len(lines) >= 2
        event = json.loads(lines[0])
        assert 'type' in event
        assert 'timestamp' in event
    finally:
        os.unlink(log_path)


# ── Config integration ───────────────────────────────────────

print("\n[config integration]")

@test("TranslationConfig loads preset from config")
def _():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("chunk_size: 2000\nsource_lang: ja\ntarget_lang: ko\nmodel: claude-sonnet-4-6\npreset: ja_novel\n")
        f.flush()
        config_path = f.name
    try:
        from translate import TranslationConfig
        config = TranslationConfig(config_path)
        assert config.preset is not None
        assert config.preset.name == "ja_novel"
    finally:
        os.unlink(config_path)

@test("TranslationConfig preset_override wins over config")
def _():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("chunk_size: 2000\nsource_lang: ja\ntarget_lang: ko\nmodel: claude-sonnet-4-6\npreset: ja_novel\n")
        f.flush()
        config_path = f.name
    try:
        from translate import TranslationConfig
        config = TranslationConfig(config_path, preset_override="ja_philosophy")
        assert config.preset.name == "ja_philosophy"
    finally:
        os.unlink(config_path)

@test("TranslationConfig retry settings load")
def _():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("""
chunk_size: 2000
source_lang: ja
target_lang: ko
model: claude-sonnet-4-6
retry:
  enabled: true
  max_retries: 3
  score_threshold: 0.7
  auto_split: false
""")
        f.flush()
        config_path = f.name
    try:
        from translate import TranslationConfig
        config = TranslationConfig(config_path)
        assert config.retry_enabled is True
        assert config.retry_max == 3
        assert config.retry_score_threshold == 0.7
        assert config.retry_auto_split is False
    finally:
        os.unlink(config_path)

@test("TranslationConfig no preset is None")
def _():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("chunk_size: 2000\nsource_lang: ja\ntarget_lang: ko\nmodel: claude-sonnet-4-6\n")
        f.flush()
        config_path = f.name
    try:
        from translate import TranslationConfig
        config = TranslationConfig(config_path)
        assert config.preset is None
    finally:
        os.unlink(config_path)


# ── CLI argument checks ──────────────────────────────────────

print("\n[CLI]")

@test("translate.py --preset flag exists")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "translate.py", "--help"],
        capture_output=True, text=True
    )
    assert "--preset" in result.stdout

@test("translate.py --auto-retry flag exists")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "translate.py", "--help"],
        capture_output=True, text=True
    )
    assert "--auto-retry" in result.stdout

@test("run_pipeline.py --list-presets works")
def _():
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_pipeline.py", "--list-presets"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "ja_novel" in result.stdout
    assert "en_philosophy" in result.stdout

@test("glossary_workflow.py status works with empty state")
def _():
    import subprocess
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [sys.executable, "glossary_workflow.py", "status",
             "--workflow", os.path.join(tmpdir, "wf.json"),
             "--glossary", os.path.join(tmpdir, "gl.json")],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Total: 0" in result.stdout


# ── Existing module compatibility ────────────────────────────

print("\n[backward compatibility]")

@test("existing translate.py imports work")
def _():
    from translate import (
        TranslationConfig, split_into_chunks, count_sentences,
        split_into_sentences, build_chunk_records, load_translation_artifacts,
        save_translation_artifacts, file_sha256, output_meta_path
    )

@test("existing review.py imports work")
def _():
    from review import (
        ReviewConfig, ReviewWriter, structural_review_chunk,
        run_structural_review, PROBLEM_TYPES
    )

@test("existing providers.py imports work")
def _():
    from providers import (
        BaseProvider, ClaudeProvider, create_provider,
        get_provider_from_config, ProviderResponse
    )

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
