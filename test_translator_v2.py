#!/usr/bin/env python3
"""
translator_v2 Phase 1 validation tests.

Runs without external API access by using scripted fake providers.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from providers import ProviderResponse
from translator_v2.core.ledger import write_live_preview
from translator_v2.core.models import SegmentRecord, V2Config
from translator_v2.codex_local.queue import CodexTaskQueue, CodexTaskRecord, task_artifact_dir
from translator_v2.codex_local.worker import build_codex_exec_command, render_codex_exec_prompt
from translator_v2.ingest.normalize import config_fingerprint
import translator_v2.orchestrate.pipeline as v2_pipeline
from translator_v2.orchestrate.pipeline import run_pipeline
from translator_v2.segment.segmenter import split_segment_for_repair
from translator_v2.translate.parser import parse_segmented_response
from translator_v2.validate.numbers import validate_numbers


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


class ScriptedProvider:
    def __init__(self, batch_scripts=None, single_scripts=None):
        self.batch_scripts = {
            tuple(key): list(value)
            for key, value in (batch_scripts or {}).items()
        }
        self.single_scripts = {
            key: list(value)
            for key, value in (single_scripts or {}).items()
        }
        self.calls = []

    async def generate(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        ids = tuple(re.findall(r"\[\[SEGMENT\s+([^\]]+)\]\]", user_prompt))
        self.calls.append(ids)
        if ids in self.batch_scripts and self.batch_scripts[ids]:
            text = self.batch_scripts[ids].pop(0)
        elif len(ids) == 1 and ids[0] in self.single_scripts and self.single_scripts[ids[0]]:
            text = self.single_scripts[ids[0]].pop(0)
        else:
            rendered = []
            for segment_id in ids:
                rendered.append(
                    f"[[SEGMENT {segment_id}]]\n임시 번역: {segment_id}\n[[END SEGMENT {segment_id}]]"
                )
            text = "\n\n".join(rendered)
        return ProviderResponse(text=text, model="fake-model", provider="fake")


class FailingQueueWorker:
    def __init__(self, error: str):
        self.error = error
        self.calls = 0

    async def process_next(self, queue) -> bool:
        task = queue.claim_next("failing-queue-worker")
        if task is None:
            return False
        self.calls += 1
        queue.fail_claim(
            task_id=task.task_id,
            task_epoch=task.task_epoch,
            claim_id=task.claim_id,
            worker_id="failing-queue-worker",
            error=self.error,
        )
        return True


def make_temp_config(tmpdir: Path, **overrides) -> V2Config:
    rules = tmpdir / "rules.md"
    glossary = tmpdir / "glossary.json"
    rules.write_text("", encoding="utf-8")
    glossary.write_text("{}", encoding="utf-8")
    config = V2Config(
        source_lang="en",
        target_lang="ko",
        provider_name="fake",
        model="fake-model",
        batch_size=2,
        config_path=str(tmpdir / "config.yaml"),
        rules_path=str(rules),
        glossary_path=str(glossary),
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


print("=" * 60)
print("translator_v2 Phase 1 validation tests")
print("=" * 60)


print("\n[parser]")


@test("strict parser rejects extraneous text outside wrappers")
def _():
    parsed = parse_segmented_response(
        "Here you go.\n[[SEGMENT ch001.s001]]\n번역\n[[END SEGMENT ch001.s001]]",
        ["ch001.s001"],
    )
    assert not parsed["ok"]
    assert parsed["parse_error"]
    assert parsed["extraneous_text"]


@test("strict parser rejects reordered wrappers")
def _():
    parsed = parse_segmented_response(
        "[[SEGMENT ch001.s002]]\n둘째\n[[END SEGMENT ch001.s002]]\n\n"
        "[[SEGMENT ch001.s001]]\n첫째\n[[END SEGMENT ch001.s001]]",
        ["ch001.s001", "ch001.s002"],
    )
    assert not parsed["ok"]
    assert "Reordered" in parsed["parse_error"]


@test("codex exec command carries model and reasoning overrides")
def _():
    cmd = build_codex_exec_command(
        model="gpt-5.4",
        reasoning_effort="high",
        cd="/home/hoonsw/dev/translator",
        output_file="/tmp/out.txt",
    )
    assert "--model" in cmd
    assert "gpt-5.4" in cmd
    assert 'model_reasoning_effort="high"' in cmd
    assert "--output-last-message" in cmd
    assert "--ephemeral" in cmd


@test("codex exec command respects CODEX_BIN override")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        fake_codex = Path(tmp) / "codex"
        fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_codex.chmod(0o755)
        previous = os.environ.get("CODEX_BIN")
        os.environ["CODEX_BIN"] = str(fake_codex)
        try:
            cmd = build_codex_exec_command(
                model="gpt-5.4",
                reasoning_effort="high",
                cd="/home/hoonsw/dev/translator",
                output_file="/tmp/out.txt",
            )
        finally:
            if previous is None:
                os.environ.pop("CODEX_BIN", None)
            else:
                os.environ["CODEX_BIN"] = previous
        assert cmd[0] == str(fake_codex)


@test("codex exec prompt wraps system and task blocks")
def _():
    prompt = render_codex_exec_prompt(
        {
            "system_prompt": "SYSTEM RULES",
            "user_prompt": "[[SEGMENT ch001.s001]]",
        }
    )
    assert "[SYSTEM INSTRUCTIONS]" in prompt
    assert "[TASK INPUT]" in prompt
    assert "SYSTEM RULES" in prompt
    assert "[[SEGMENT ch001.s001]]" in prompt


print("\n[validators / segment split]")


@test("number validator flags missing multi-digit tokens")
def _():
    issues = validate_numbers(
        "ch001.s001",
        source_text="There were 42 apples and 7 pears.",
        translated_text="사과는 많았고 배도 있었다.",
    )
    issue_types = {item.issue_type for item in issues}
    assert "missing_number" in issue_types
    assert any(item.severity == "critical" for item in issues)


@test("split repair preserves parent trailing separator on final child")
def _():
    segment = SegmentRecord(
        segment_id="ch001.s001",
        parent_segment_id=None,
        child_segment_ids=[],
        chapter_id="ch001",
        source_order_index=1,
        source_start_offset=0,
        source_end_offset=90,
        source_text="Sentence one. Sentence two. Sentence three.",
        source_hash="dummy",
        separator_after="\n\n",
    )
    config = V2Config(source_lang="en", max_segment_chars=20, hard_max_segment_chars=20)
    children = split_segment_for_repair(segment, "en", config)
    assert len(children) >= 2
    assert children[-1].separator_after == "\n\n"
    assert all(child.separator_after == " " for child in children[:-1])


@test("split repair preserves newline separator between child units")
def _():
    segment = SegmentRecord(
        segment_id="ch001.s001",
        parent_segment_id=None,
        child_segment_ids=[],
        chapter_id="ch001",
        source_order_index=1,
        source_start_offset=0,
        source_end_offset=18,
        source_text="Line one.\nLine two.",
        source_hash="dummy",
        separator_after="",
    )
    config = V2Config(source_lang="en", max_segment_chars=120, hard_max_segment_chars=120)
    children = split_segment_for_repair(segment, "en", config)
    assert len(children) == 2
    assert children[0].separator_after == "\n"


@test("split repair does not invent spaces for no-whitespace boundaries")
def _():
    segment = SegmentRecord(
        segment_id="ch001.s001",
        parent_segment_id=None,
        child_segment_ids=[],
        chapter_id="ch001",
        source_order_index=1,
        source_start_offset=0,
        source_end_offset=160,
        source_text="A" * 160,
        source_hash="dummy",
        separator_after="",
    )
    config = V2Config(source_lang="en", max_segment_chars=80, hard_max_segment_chars=80)
    children = split_segment_for_repair(segment, "en", config)
    assert len(children) == 2
    assert children[0].separator_after == ""


@test("config fingerprint changes when protected regex policy changes")
def _():
    base = V2Config(protected_regexes=[])
    changed = V2Config(protected_regexes=[r"OpenAI-[A-Z]+"])
    first = config_fingerprint(base, "", {})
    second = config_fingerprint(changed, "", {})
    assert first != second


@test("config fingerprint changes when ingest policy version changes")
def _():
    base = V2Config(ingest_version="ingest-v2")
    changed = V2Config(ingest_version="ingest-v3")
    first = config_fingerprint(base, "", {})
    second = config_fingerprint(changed, "", {})
    assert first != second


@test("v2 loader auto-corrects english-dominant source language")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text(
                "This is an English manuscript paragraph with enough latin text to trigger detection.\n"
                "It should switch away from a Japanese configuration.\n",
                encoding="utf-8",
            )
            config = make_temp_config(tmpdir, source_lang="ja")
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                dry_run=True,
            )
            ledger = json.loads(Path(result.ledger_path).read_text(encoding="utf-8"))
            assert ledger["document"]["source_lang"] == "en"
            assert ledger["document"]["ingest_analysis"]["effective_source_lang"] == "en"

    asyncio.run(scenario())


print("\n[pipeline]")


@test("pipeline repairs failed sibling locally and delivers")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text(
                "First paragraph with 42 apples.\n\nSecond paragraph ends here.\n",
                encoding="utf-8",
            )
            config = make_temp_config(tmpdir, batch_size=2, local_retry_max=2, split_retry_max=1)
            provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001", "ch001.s002"): [
                        "[[SEGMENT ch001.s001]]\n첫 번째 문단에는 사과 42개가 있다.\n[[END SEGMENT ch001.s001]]\n\n"
                        "[[SEGMENT ch001.s002]]\nSecond paragraph ends here.\n[[END SEGMENT ch001.s002]]"
                    ]
                },
                single_scripts={
                    "ch001.s002": [
                        "[[SEGMENT ch001.s002]]\n두 번째 문단은 여기서 끝난다.\n[[END SEGMENT ch001.s002]]"
                    ]
                },
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            text = output.read_text(encoding="utf-8")
            assert result.status == "DELIVERABLE"
            assert "사과 42개" in text
            assert "두 번째 문단은 여기서 끝난다." in text
            assert provider.calls == [
                ("ch001.s001", "ch001.s002"),
                ("ch001.s002",),
            ]

    asyncio.run(scenario())


@test("pipeline blocks unresolved exact-copy output")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("This sentence must be translated.\n", encoding="utf-8")
            config = make_temp_config(
                tmpdir,
                batch_size=1,
                local_retry_max=1,
                split_retry_max=0,
                max_total_repairs_per_segment=1,
            )
            provider = ScriptedProvider(
                single_scripts={
                    "ch001.s001": [
                        "[[SEGMENT ch001.s001]]\nThis sentence must be translated.\n[[END SEGMENT ch001.s001]]",
                        "[[SEGMENT ch001.s001]]\nThis sentence must be translated.\n[[END SEGMENT ch001.s001]]",
                    ]
                }
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            blocked_text = Path(result.blocked_workfile).read_text(encoding="utf-8")
            assert result.status == "BLOCKED"
            assert "[BLOCKED:ch001.s001]" in blocked_text
            assert provider.calls == [("ch001.s001",), ("ch001.s001",)]

    asyncio.run(scenario())


@test("resume skips provider calls when deliverable ledger already exists")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n\nBeta.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=2)
            first_provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001", "ch001.s002"): [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]\n\n"
                        "[[SEGMENT ch001.s002]]\n베타.\n[[END SEGMENT ch001.s002]]"
                    ]
                }
            )
            output = tmpdir / "out.txt"
            first_result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=first_provider,
            )
            second_provider = ScriptedProvider()
            second_result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=second_provider,
            )
            assert first_result.status == "DELIVERABLE"
            assert second_result.status == "DELIVERABLE"
            assert second_result.resumed is True
            assert len(first_provider.calls) == 1
            assert len(second_provider.calls) == 0

    asyncio.run(scenario())


@test("resume restores tampered deliverable from ledger without provider call")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n\nBeta.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=2)
            first_provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001", "ch001.s002"): [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]\n\n"
                        "[[SEGMENT ch001.s002]]\n베타.\n[[END SEGMENT ch001.s002]]"
                    ]
                }
            )
            output = tmpdir / "out.txt"
            await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=first_provider,
            )
            output.write_text("tampered output\n", encoding="utf-8")

            second_provider = ScriptedProvider()
            second_result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=second_provider,
            )
            restored = output.read_text(encoding="utf-8")
            assert second_result.status == "DELIVERABLE"
            assert restored == "알파.\n\n베타.\n"
            assert len(second_provider.calls) == 0

    asyncio.run(scenario())


@test("resume requeues interrupted translating segment and avoids batch id collisions")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=1)
            output = tmpdir / "out.txt"
            initial = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                dry_run=True,
            )
            ledger_path = Path(initial.ledger_path)
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            payload["segments"][0]["status"] = "TRANSLATING"
            payload["segments"][0]["attempt_count"] = 1
            payload["segments"][0]["batch_id"] = "batch-0007"
            ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            provider = ScriptedProvider(
                single_scripts={
                    "ch001.s001": [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]"
                    ]
                }
            )
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            repaired = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert result.status == "DELIVERABLE"
            assert repaired["segments"][0]["batch_id"] == "repair-0008"
            assert output.read_text(encoding="utf-8") == "알파.\n"
            assert provider.calls == [("ch001.s001",)]

    asyncio.run(scenario())


@test("codex_local resume rotates artifact dir for unfinished work")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n", encoding="utf-8")
            config = make_temp_config(
                tmpdir,
                batch_size=1,
                generation_mode="codex_local",
                codex_claim_timeout_seconds=0.5,
                codex_result_timeout_seconds=0.5,
                codex_poll_interval_seconds=0.01,
            )
            output = tmpdir / "out.txt"
            initial = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                dry_run=True,
            )
            ledger_path = Path(initial.ledger_path)
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            payload["document"]["run_id"] = "run-old"
            payload["document"]["generation_mode"] = "codex_local"
            payload["document"]["artifact_dir"] = str(task_artifact_dir(str(output), "run-old"))
            payload["document"]["status"] = "TRANSLATING"
            payload["segments"][0]["status"] = "FAILED_VALIDATION"
            payload["segments"][0]["attempt_count"] = 1
            ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            provider = ScriptedProvider(
                single_scripts={
                    "ch001.s001": [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]"
                    ]
                }
            )
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            refreshed = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert result.status == "DELIVERABLE"
            assert refreshed["document"]["run_id"] != "run-old"
            assert refreshed["document"]["artifact_dir"] != str(task_artifact_dir(str(output), "run-old"))
            assert refreshed["document"]["artifact_dir"].endswith(refreshed["document"]["run_id"])

    asyncio.run(scenario())


@test("dry-run ledger records ingest state for pdf-like txt")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            lines = []
            for idx in range(1, 16):
                lines.extend(
                    [
                        f"The recording archive {idx} remained inter-",
                        f"woven with aesthetic debate section {idx}, and the argument con-",
                        f"tinued across hard-wrapped lines without a real paragraph break {idx}.",
                    ]
                )
                if idx in {5, 10}:
                    lines.append("\f")
            src.write_text("\n".join(lines) + "\n", encoding="utf-8")
            config = make_temp_config(tmpdir, source_lang="en")
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                dry_run=True,
            )
            ledger = json.loads(Path(result.ledger_path).read_text(encoding="utf-8"))
            assert ledger["document"]["ingest_state"] == "txt_pdf_normalized"
            assert ledger["document"]["ingest_version"] == config.ingest_version

    asyncio.run(scenario())


@test("pipeline retries readability failure and then delivers")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("This paragraph should become natural Korean prose after repair.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=1, local_retry_max=1, split_retry_max=0)
            provider = ScriptedProvider(
                single_scripts={
                    "ch001.s001": [
                        "[[SEGMENT ch001.s001]]\n이 번 역 문 은 아 주 부 자 연 스 럽 다.\n[[END SEGMENT ch001.s001]]",
                        "[[SEGMENT ch001.s001]]\n이 번역문은 훨씬 자연스럽다.\n[[END SEGMENT ch001.s001]]",
                    ]
                }
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            assert result.status == "DELIVERABLE"
            assert "이 번 역 문" not in output.read_text(encoding="utf-8")
            assert provider.calls == [("ch001.s001",), ("ch001.s001",)]

    asyncio.run(scenario())


@test("split descendants share original repair budget")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text(
                "Sentence one is long enough. Sentence two is also long enough.\n",
                encoding="utf-8",
            )
            config = make_temp_config(
                tmpdir,
                batch_size=1,
                local_retry_max=0,
                split_retry_max=1,
                max_total_repairs_per_segment=1,
                max_segment_chars=120,
                hard_max_segment_chars=120,
            )
            provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001",): [
                        "[[SEGMENT ch001.s001]]\nSentence one is long enough. Sentence two is also long enough.\n[[END SEGMENT ch001.s001]]"
                    ]
                },
                single_scripts={
                    "ch001.s001.a": [
                        "[[SEGMENT ch001.s001.a]]\n첫 번째 문장은 충분히 길다.\n[[END SEGMENT ch001.s001.a]]"
                    ],
                    "ch001.s001.b": [
                        "[[SEGMENT ch001.s001.b]]\nSentence two is also long enough.\n[[END SEGMENT ch001.s001.b]]"
                    ],
                },
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            blocked_text = Path(result.blocked_workfile).read_text(encoding="utf-8")
            assert result.status == "BLOCKED"
            assert "[BLOCKED:ch001.s001.b]" in blocked_text
            assert provider.calls == [
                ("ch001.s001",),
                ("ch001.s001.a",),
                ("ch001.s001.b",),
            ]

    asyncio.run(scenario())


@test("codex_local pipeline delivers and persists accepted provenance")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n\nBeta.\n", encoding="utf-8")
            config = make_temp_config(
                tmpdir,
                batch_size=2,
                generation_mode="codex_local",
                codex_claim_timeout_seconds=0.5,
                codex_result_timeout_seconds=0.5,
                codex_poll_interval_seconds=0.01,
            )
            provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001", "ch001.s002"): [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]\n\n"
                        "[[SEGMENT ch001.s002]]\n베타.\n[[END SEGMENT ch001.s002]]"
                    ]
                }
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            ledger = json.loads(Path(result.ledger_path).read_text(encoding="utf-8"))
            leaves = [segment for segment in ledger["segments"] if segment["status"] == "ACCEPTED"]
            assert result.status == "DELIVERABLE"
            assert output.read_text(encoding="utf-8") == "알파.\n\n베타.\n"
            assert len(leaves) == 2
            assert all(segment["generation_mode"] == "codex_local" for segment in leaves)
            assert all(segment["backend_label"] == "codex_local" for segment in leaves)
            assert all(segment["accepted_task_epoch"] == 1 for segment in leaves)
            assert all(segment["accepted_claim_id"] for segment in leaves)

    asyncio.run(scenario())


@test("live preview writes accepted text with unresolved gap markers")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n\nBeta.\n\nGamma.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=2)
            provider = ScriptedProvider(
                batch_scripts={
                    ("ch001.s001", "ch001.s002"): [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]\n\n"
                        "[[SEGMENT ch001.s002]]\n베타.\n[[END SEGMENT ch001.s002]]"
                    ]
                }
            )
            output = tmpdir / "out.txt"
            initial = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                dry_run=True,
            )
            ledger = v2_pipeline.load_ledger(str(output))
            assert ledger is not None
            leaves = [segment for segment in ledger.segments if not segment.child_segment_ids]
            leaves[0].status = "ACCEPTED"
            leaves[0].translated_text = "알파."
            leaves[1].status = "ACCEPTED"
            leaves[1].translated_text = "베타."
            preview_file = Path(write_live_preview(str(output), ledger))
            preview_text = preview_file.read_text(encoding="utf-8")
            assert "알파." in preview_text
            assert "베타." in preview_text
            assert "미해결 세그먼트 생략" in preview_text

    asyncio.run(scenario())


@test("codex_local bootstrap mode is operational without injected worker")
def _():
    async def scenario():
        original_build_provider = v2_pipeline.build_provider
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmpdir = Path(tmp)
                src = tmpdir / "book.txt"
                src.write_text("Alpha.\n", encoding="utf-8")
                config = make_temp_config(
                    tmpdir,
                    batch_size=1,
                    generation_mode="codex_local",
                    codex_worker_mode="bootstrap",
                    provider_name="fake",
                    model="fake-model",
                    codex_claim_timeout_seconds=0.5,
                    codex_result_timeout_seconds=0.5,
                    codex_poll_interval_seconds=0.01,
                )
                scripted = ScriptedProvider(
                    single_scripts={
                        "ch001.s001": [
                            "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]"
                        ]
                    }
                )
                v2_pipeline.build_provider = lambda cfg: scripted
                output = tmpdir / "out.txt"
                result = await run_pipeline(
                    input_path=str(src),
                    output_path=str(output),
                    config=config,
                )
                assert result.status == "DELIVERABLE"
                assert scripted.calls == [("ch001.s001",)]
        finally:
            v2_pipeline.build_provider = original_build_provider

    asyncio.run(scenario())


@test("codex_local surfaces failed worker claims with the actual error")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n", encoding="utf-8")
            config = make_temp_config(
                tmpdir,
                batch_size=1,
                generation_mode="codex_local",
                codex_claim_timeout_seconds=0.5,
                codex_result_timeout_seconds=0.5,
                codex_poll_interval_seconds=0.01,
            )
            output = tmpdir / "out.txt"
            worker = FailingQueueWorker("codex_exec_exception: [Errno 2] No such file or directory: 'codex'")
            try:
                await run_pipeline(
                    input_path=str(src),
                    output_path=str(output),
                    config=config,
                    provider=worker,
                )
                raise AssertionError("run_pipeline should have raised")
            except RuntimeError as exc:
                assert "failed after claim" in str(exc)
                assert "No such file or directory: 'codex'" in str(exc)
                assert worker.calls == 1

    asyncio.run(scenario())


@test("codex queue quarantines stale results after requeue")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        run_dir = task_artifact_dir(str(tmpdir / "out.txt"), "run123")
        queue = CodexTaskQueue(run_dir)
        task = CodexTaskRecord(
            task_id="batch-0001",
            run_id="run123",
            generation_mode="codex_local",
            backend_label="codex_local",
            work_type="translate_batch",
            chapter_id="ch001",
            segment_ids=["ch001.s001"],
            attempt=1,
            task_epoch=1,
            created_at="2026-04-11T00:00:00+09:00",
            context={},
            segments=[{
                "segment_id": "ch001.s001",
                "source_hash": "abc",
                "source_text": "Alpha.",
                "separator_after": "\n",
            }],
            instructions={"output_schema": "prompt-v1"},
            repair_reason=None,
            system_prompt="system",
            user_prompt="user",
        )
        queue.enqueue(task)
        claimed = queue.claim_next("worker-a")
        assert claimed is not None
        abandoned = queue.mark_stale("batch-0001", 1, reason="timeout")
        replacement = queue.requeue_with_new_epoch(abandoned)
        assert replacement.task_epoch == 2
        claimed_new = queue.claim_next("worker-b")
        assert claimed_new is not None
        queue.complete_claim(
            task_id="batch-0001",
            task_epoch=1,
            claim_id=claimed.claim_id,
            worker_id="worker-a",
            raw_output_text="[[SEGMENT ch001.s001]]\n오래된 결과\n[[END SEGMENT ch001.s001]]",
            provider_name="fake",
            model_name="fake-model",
        )
        stale_results = queue.list_completed_results("batch-0001", 1)
        assert len(stale_results) == 1
        queue.quarantine_result(stale_results[0], "stale_task_epoch")
        quarantined = list((run_dir / "codex_queue" / "quarantined").glob("*.result.json"))
        assert len(quarantined) == 1
        assert not queue.list_completed_results("batch-0001", 1)


@test("manifest carries per-segment provenance for accepted leaves")
def _():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "book.txt"
            src.write_text("Alpha.\n", encoding="utf-8")
            config = make_temp_config(tmpdir, batch_size=1)
            provider = ScriptedProvider(
                single_scripts={
                    "ch001.s001": [
                        "[[SEGMENT ch001.s001]]\n알파.\n[[END SEGMENT ch001.s001]]"
                    ]
                }
            )
            output = tmpdir / "out.txt"
            result = await run_pipeline(
                input_path=str(src),
                output_path=str(output),
                config=config,
                provider=provider,
            )
            manifest = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
            accepted = manifest["accepted_segment_provenance"]
            assert len(accepted) == 1
            assert accepted[0]["segment_id"] == "ch001.s001"
            assert accepted[0]["provider"] == "fake"
            assert accepted[0]["model"] == "fake-model"

    asyncio.run(scenario())


print("\n" + "=" * 60)
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if errors:
    print("\nErrors:")
    for name, error in errors:
        print(f"  - {name}: {error}")
    raise SystemExit(1)

print("\nAll translator_v2 tests passed.")
