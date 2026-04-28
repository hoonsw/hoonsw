#!/usr/bin/env python3

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from translator_v3.benchmarks import default_manifest_path, load_cases, run_benchmark_suite
import translator_v3.benchmarks.quality_eval as quality_eval
import translator_v3.benchmarks.quality_soak as quality_soak
from translator_v3.core.io import load_graph, write_graph_bundle
from translator_v3.core.models import ClassifiedInput, TranslationUnit, V3Config
from translator_v3.extractors.adapters import available_capabilities
from translator_v3.extractors.arbitration import build_extraction_trace
from translator_v3.extractors.execution import ExtractionExecutionResult
from translator_v3.ingest.classifier import classify_input
from translator_v3.ingest.spine import build_document_graph
import translator_v3.ingest.spine as v3_spine
from translator_v3.assemble import assemble_output
from translator_v3.orchestrate import run_pipeline
import translator_v3.orchestrate.pipeline as v3_pipeline
from translator_v3.plan import build_translation_units
from translator_v3.validate import validate_unit_output


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


def _run_quality_fixture_case(case_id: str, fixture_name: str):
    cases = quality_eval.load_quality_cases(
        Path("translator_v3/benchmarks/fixtures/quality/cases.json")
    )
    case = next(case for case in cases if case.case_id == case_id)
    with tempfile.TemporaryDirectory() as tmp:
        return asyncio.run(
            quality_eval._run_case(
                case,
                backend="fixture_script",
                mock_backend_fixture_path=str(
                    Path("translator_v3/benchmarks/fixtures/quality") / fixture_name
                ),
                model="fixture-script",
                reasoning_effort="high",
                artifact_root=tmp,
                output_root=tmp,
            )
        )


print("=" * 60)
print("translator_v3 Phase 0/1 tests")
print("=" * 60)


@test("classifier maps clean txt to clean_text")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clean.txt"
        path.write_text("This is clean prose.\n\nIt should stay simple.\n", encoding="utf-8")
        result = classify_input(str(path), V3Config(source_lang="en"))
        assert result.input_class == "clean_text"
        assert result.selected_spine == "text_spine"


@test("classifier maps pdf-ish txt to unknown_noisy_text")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pdfish.txt"
        path.write_text(
            "The archive was first pub-\n"
            "lished in 2014 and con-\n"
            "tinued across hard-wrapped lines.\n\f\n",
            encoding="utf-8",
        )
        result = classify_input(str(path), V3Config(source_lang="en"))
        assert result.input_class == "unknown_noisy_text"
        assert result.selected_spine == "normalized_text_spine"


@test("document graph emits chapter and block nodes")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.txt"
        path.write_text(
            "CHAPTER 1\n\n"
            "First paragraph.\n\n"
            "Second paragraph.\n\n"
            "CHAPTER 2\n\n"
            "Third paragraph.\n",
            encoding="utf-8",
        )
        graph = build_document_graph(str(path), V3Config(source_lang="en"), run_id="test-run")
        node_types = [node.node_type for node in graph.nodes]
        assert "document" in node_types
        assert "chapter" in node_types
        assert "block" in node_types
        assert graph.telemetry is not None
        assert graph.telemetry.blocks_total >= 3
        assert graph.extraction_trace is not None
        assert graph.extraction_trace.ownership_mode == "single_owner_per_page"
        assert graph.extraction_trace.integration_status == "not_needed"


@test("graph bundle round-trips through disk")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.txt"
        path.write_text("Simple text.\n", encoding="utf-8")
        config = V3Config(source_lang="en", run_root=tmp)
        graph = build_document_graph(str(path), config, run_id="roundtrip")
        paths = write_graph_bundle(graph, normalized_source_text="Simple text.\n")
        loaded = load_graph(paths["document_graph"])
        assert loaded.run_id == "roundtrip"
        assert loaded.input_path == str(path.resolve())
        assert Path(paths["extraction_plan"]).exists()
        assert Path(paths["extractor_probes"]).exists()
        assert Path(paths["page_ownership"]).exists()


@test("adapter probes return ocrmypdf marker docling entries")
def _():
    probes = available_capabilities()
    names = {probe.name for probe in probes}
    assert names == {"ocrmypdf", "marker", "docling"}


@test("pdf-scanned arbitration remains preflight-only before execution")
def _():
    classified = classify_input(
        "/home/hoonsw/dev/translator/translator_v3/benchmarks/fixtures/pdfish_en.txt",
        V3Config(source_lang="en"),
    )
    trace = build_extraction_trace(classified, V3Config(source_lang="en"))
    assert trace.ownership_mode == "single_owner_per_page"
    assert trace.candidates[-1].name == "text_spine"
    assert trace.integration_status == "preflight_only"
    assert trace.external_execution_attempted is False
    assert trace.planned_extractor in {"docling", "text_spine"}


@test("default benchmark manifest loads")
def _():
    cases = load_cases(default_manifest_path())
    assert len(cases) >= 5
    assert any(case.case_id == "pdfish_en" for case in cases)


@test("benchmark suite writes report and matches expected classes")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        cases = load_cases(default_manifest_path())
        report = run_benchmark_suite(
            cases=cases,
            config=V3Config(source_lang="en", run_root=tmp),
            artifact_root=tmp,
            report_path=str(report_path),
        )
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["summary"]["case_count"] == len(cases)
        assert payload["summary"]["all_expected_classes_matched"] is True
        assert payload["summary"]["gate_status"] == "pretranslation_non_blocking"
        assert report.summary["all_expected_classes_matched"] is True


@test("v3 run pipeline produces translated output, preview, and review bundle")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text(
            "CHAPTER 1\n\n"
            "First paragraph.\n\n"
            "Second paragraph.\n",
            encoding="utf-8",
        )
        result = asyncio.run(
            run_pipeline(
                str(input_path),
                str(output_path),
                V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="echo"),
            )
        )
        assert result.total_units >= 3
        assert Path(result.translated_output_path).exists()
        assert Path(result.preview_path).exists()
        assert Path(result.review_bundle_path).exists()
        assert result.status == "DELIVERABLE"
        assert result.final_output_written is True
        assert "테스트용 번역 결과" in output_path.read_text(encoding="utf-8")


@test("document graph records successful external extraction execution")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "book.pdf"
        path.write_bytes(b"%PDF-1.4\n%fake\n")

        original_execute = v3_spine.execute_extraction
        original_classify = v3_spine.classify_input

        def fake_classify(path, config):
            return ClassifiedInput(
                input_path=str(Path(path).resolve()),
                file_type="pdf",
                input_class="pdf_born_digital",
                ingest_state="pdf_text_extracted",
                configured_source_lang=config.source_lang,
                source_lang=config.source_lang,
                target_lang=config.target_lang,
                processed_text="Fallback text should not win.",
                raw_text="Fallback text should not win.",
                warnings=[],
                analysis={"transitional_adapters": []},
                planned_extractors=["docling"],
                selected_spine="pdf_text_spine",
                ocr_required=False,
            )

        def fake_execute(classified, trace, artifact_dir):
            return ExtractionExecutionResult(
                extractor_name="docling",
                source_path=classified.input_path,
                executed=True,
                succeeded=True,
                text="CHAPTER 1\n\nRecovered paragraph.\n",
                raw_text="CHAPTER 1\n\nRecovered paragraph.\n",
                page_count=3,
                artifact_paths={"extracted_text": str(Path(artifact_dir) / "extraction" / "docling_extracted.md")},
                notes=["fake docling success"],
            )

        v3_spine.classify_input = fake_classify
        v3_spine.execute_extraction = fake_execute
        try:
            graph = build_document_graph(str(path), V3Config(source_lang="en", run_root=tmp), run_id="extract-ok")
        finally:
            v3_spine.classify_input = original_classify
            v3_spine.execute_extraction = original_execute

        assert graph.extraction_trace is not None
        assert graph.extraction_trace.integration_status == "executed"
        assert graph.extraction_trace.resolved_owner == "docling"
        assert graph.analysis["phase2_slice_status"] == "external_extractor_executed"
        assert graph.analysis["external_extraction_succeeded"] is True
        assert graph.analysis["normalized_text"].startswith("CHAPTER 1")


@test("planner splits oversized blocks instead of truncating")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "long.txt"
        long_sentence = "This sentence is intentionally long and repetitive for planner testing. " * 30
        input_path.write_text(f"CHAPTER 1\n\n{long_sentence}\n", encoding="utf-8")
        graph = build_document_graph(str(input_path), V3Config(source_lang="en"), run_id="split-test")
        units = build_translation_units(graph, V3Config(source_lang="en", max_unit_chars=200))
        body_units = [unit for unit in units if unit.unit_class == "body_paragraph"]
        assert len(body_units) >= 2
        reconstructed = " ".join(unit.source_text for unit in body_units)
        assert "planner testing" in reconstructed
        assert len(reconstructed) >= 500


@test("planner splits multi-sentence body paragraphs into sentence-aligned units")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "body.txt"
        input_path.write_text(
            "CHAPTER 1\n\n"
            "First sentence introduces the setting. Second sentence adds the main claim. Third sentence closes the paragraph.\n",
            encoding="utf-8",
        )
        graph = build_document_graph(str(input_path), V3Config(source_lang="en"), run_id="sentence-split")
        body_units = [
            u
            for u in build_translation_units(graph, V3Config(source_lang="en", max_unit_chars=500))
            if u.unit_class == "body_paragraph" and "sentence" in u.source_text.lower()
        ]
        assert len(body_units) == 3
        assert all(u.node_id == body_units[0].node_id for u in body_units)
        assert body_units[0].source_text.endswith(".")


@test("argument preset allows larger unit chunks than paragraph default")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "argument.txt"
        long_sentence = "This sentence carries argumentative structure and should stay together. " * 30
        input_path.write_text(f"CHAPTER 1\n\n{long_sentence}\n", encoding="utf-8")
        graph = build_document_graph(str(input_path), V3Config(source_lang="en"), run_id="argument-test")
        base_units = [u for u in build_translation_units(graph, V3Config(source_lang="en", max_unit_chars=120)) if u.unit_class == "body_paragraph"]
        argument_units = [
            u
            for u in build_translation_units(
                graph,
                V3Config(
                    source_lang="en",
                    max_unit_chars=120,
                    preset_name="en_philosophy",
                    preset_chunk_boundary_hints="argument",
                ),
            )
            if u.unit_class == "body_paragraph"
        ]
        assert len(argument_units) <= len(base_units)


@test("body omission triggers completeness validator")
def _():
    unit = TranslationUnit(
        unit_id="u1",
        node_id="block.001",
        chapter_id="chapter.001",
        source_order_index=1,
        node_reading_order=1,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text="First sentence explains the premise. Second sentence adds a key detail. Third sentence closes the point.",
        source_lang="en",
        target_lang="ko",
    )
    validation = validate_unit_output(unit, "첫 문장만 번역했다.", V3Config(source_lang="en", target_lang="ko"))
    assert validation.ok is False
    issue_types = {issue["issue_type"] for issue in validation.issues}
    assert "coverage_drop" in issue_types


@test("wrapper markers are stripped from assembled output")
def _():
    units = [
        TranslationUnit(
            unit_id="u1",
            node_id="block.001",
            chapter_id="chapter.001",
            source_order_index=1,
            node_reading_order=1,
            unit_index_within_node=1,
            unit_class="body_paragraph",
            source_text="Source paragraph.",
            source_lang="en",
            target_lang="ko",
            status="ACCEPTED",
            translated_text="[[SEGMENT u1]]\n정리된 번역문\n[[END SEGMENT u1]]",
        )
    ]
    rendered = assemble_output(units)
    assert "[[SEGMENT" not in rendered
    assert "[[END SEGMENT" not in rendered
    assert rendered.strip() == "정리된 번역문"


@test("wrapper markers in translated text are rejected by validator")
def _():
    unit = TranslationUnit(
        unit_id="u1",
        node_id="block.001",
        chapter_id="chapter.001",
        source_order_index=1,
        node_reading_order=1,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text="Source paragraph.",
        source_lang="en",
        target_lang="ko",
    )
    validation = validate_unit_output(
        unit,
        "[[SEGMENT u1]]\n정리된 번역문\n[[END SEGMENT u1]]",
        V3Config(source_lang="en", target_lang="ko"),
    )
    assert validation.ok is False
    issue_types = {issue["issue_type"] for issue in validation.issues}
    assert "wrapper_leak" in issue_types


@test("reference-style page citation does not block when digits are preserved in Korean form")
def _():
    unit = TranslationUnit(
        unit_id="chapter.ch001.block.b0050.u01",
        node_id="chapter.ch001.block.b0050",
        chapter_id="chapter.ch001",
        source_order_index=52,
        node_reading_order=52,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text="（17）William Gibson, Neuromancer, Harper Collins Publishers, 1995, p.12.",
        source_lang="ja",
        target_lang="ko",
    )
    validation = validate_unit_output(
        unit,
        "(17) 윌리엄 깁슨, 『뉴로맨서』, 하퍼 콜린스 출판사, 1995년, 12쪽.",
        V3Config(source_lang="ja", target_lang="ko", backend_name="provider_api"),
    )
    assert validation.ok is True
    critical_types = {
        issue["issue_type"] for issue in validation.issues if issue["severity"] in {"critical", "repair-trigger"}
    }
    assert "missing_number" not in critical_types
    assert any(issue["issue_type"] == "missing_number" for issue in validation.issues)


@test("unexpected Armenian or Hebrew leakage in Korean output triggers retry")
def _():
    unit = TranslationUnit(
        unit_id="chapter.ch001.block.b0030.u",
        node_id="chapter.ch001.block.b0030",
        chapter_id="chapter.ch001",
        source_order_index=32,
        node_reading_order=32,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text="ではそこでは、なにがどのように「悪魔祓い」されていたのか。",
        source_lang="ja",
        target_lang="ko",
    )
    validation = validate_unit_output(
        unit,
        "그렇다면 այնտեղ에서는 무엇이 어떻게 ‘악마 퇴치’되고 있었던 것일까.",
        V3Config(source_lang="ja", target_lang="ko", backend_name="provider_api"),
    )
    assert validation.ok is False
    issue_types = {issue["issue_type"] for issue in validation.issues}
    assert "unexpected_foreign_script" in issue_types


@test("two-sentence collapse with severe shrinkage is blocked")
def _():
    unit = TranslationUnit(
        unit_id="u2",
        node_id="block.002",
        chapter_id="chapter.001",
        source_order_index=2,
        node_reading_order=2,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text=(
            "First sentence introduces the archival setting in detail. "
            "Second sentence adds the specific claim that must not be dropped."
        ),
        source_lang="en",
        target_lang="ko",
    )
    validation = validate_unit_output(unit, "첫 문장만 옮겼다.", V3Config(source_lang="en", target_lang="ko"))
    assert validation.ok is False
    issue_types = {issue["issue_type"] for issue in validation.issues}
    assert "coverage_drop" in issue_types


@test("minor sentence count compression without severe shrinkage can pass")
def _():
    unit = TranslationUnit(
        unit_id="u3",
        node_id="block.003",
        chapter_id="chapter.001",
        source_order_index=3,
        node_reading_order=3,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text=(
            "First sentence introduces the archival setting in detail. "
            "Second sentence adds the specific claim that must not be dropped."
        ),
        source_lang="en",
        target_lang="ko",
    )
    validation = validate_unit_output(
        unit,
        "첫 문장은 기록 보관의 장면을 자세히 제시하고, 둘째 문장은 빠지면 안 되는 핵심 주장을 덧붙인다.",
        V3Config(source_lang="en", target_lang="ko"),
    )
    issue_types = {issue["issue_type"] for issue in validation.issues}
    assert "coverage_drop" not in issue_types


@test("heading number corruption remains blocking")
def _():
    unit = TranslationUnit(
        unit_id="h1",
        node_id="chapter.001",
        chapter_id="chapter.001",
        source_order_index=1,
        node_reading_order=1,
        unit_index_within_node=1,
        unit_class="heading",
        source_text="CHAPTER 1",
        source_lang="en",
        target_lang="ko",
    )
    validation = validate_unit_output(unit, "제2장", V3Config(source_lang="en", target_lang="ko"))
    assert validation.ok is False
    severities = {issue["severity"] for issue in validation.issues}
    assert "critical" in severities or "repair_trigger" in severities


@test("blocked runs do not write requested final output path")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text("This body keeps 42 numbers.\n", encoding="utf-8")
        class BadBackend:
            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                return BackendResponse(
                    text="레코드가 있었다.",
                    backend_name="bad-test",
                    provider_name="bad-test",
                    model_name="bad-test",
                )

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: BadBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="echo"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        assert result.final_output_written is False
        assert output_path.exists() is False
        assert Path(result.translated_output_path).exists()


@test("adequacy review can block fluent but incomplete long sentence output")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text(
            "This long sentence introduces the recording archive, explains the institutional context, and "
            "adds a final clause that should not disappear in translation because it materially changes the claim.\n",
            encoding="utf-8",
        )

        class AdequacyFailBackend:
            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                return BackendResponse(
                    text="기록 보관소를 소개하는 문장이다.",
                    backend_name="adequacy-fail",
                    provider_name="adequacy-fail",
                    model_name="adequacy-fail",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                return False, "final clause carrying the material claim appears omitted"

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: AdequacyFailBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        review_bundle = json.loads(Path(result.review_bundle_path).read_text(encoding="utf-8"))
        issue_types = {
            issue["issue_type"]
            for unit in review_bundle["units"]
            for issue in unit.get("validation_errors", [])
        }
        assert "adequacy_review_failed" in issue_types
        assert output_path.exists() is False


@test("adequacy review also covers shorter single-sentence body units by default")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text(
            "This sentence sounds ordinary but still carries a final qualifier that materially changes the claim.\n",
            encoding="utf-8",
        )

        class AdequacyFailBackend:
            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                return BackendResponse(
                    text="주장을 소개하는 문장이다.",
                    backend_name="adequacy-fail-short",
                    provider_name="adequacy-fail-short",
                    model_name="adequacy-fail-short",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                return False, "material qualifier appears omitted"

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: AdequacyFailBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        review_bundle = json.loads(Path(result.review_bundle_path).read_text(encoding="utf-8"))
        issue_types = {
            issue["issue_type"]
            for unit in review_bundle["units"]
            for issue in unit.get("validation_errors", [])
        }
        assert "adequacy_review_failed" in issue_types


@test("compression-like body retry gets specialized repair hint once")
def _():
    unit = TranslationUnit(
        unit_id="u4",
        node_id="block.004",
        chapter_id="chapter.001",
        source_order_index=4,
        node_reading_order=4,
        unit_index_within_node=1,
        unit_class="body_paragraph",
        source_text=(
            "When someone fails to recognize a person or musical work that you would have thought "
            "close to that person's heart and work, it is interesting and educational."
        ),
        source_lang="en",
        target_lang="ko",
        translated_text="흥미롭고 교육적이다.",
        attempt_count=1,
        validation_errors=[
            {
                "issue_type": "adequacy_review_failed",
                "observed": "material qualifier appears distorted",
            }
        ],
    )
    config = V3Config(source_lang="en", target_lang="ko")
    hint = v3_pipeline._failure_hint(unit, config)
    assert "adequacy_review_failed" in hint
    assert "[translator_v3 compression repair]" in hint
    assert "원문에서 회복 가능한 의미를 모두 보존" in hint
    assert "요약하지 말고" in hint
    telemetry = unit.telemetry["compression_repair_retry"]
    assert telemetry["classified_compression_like"] is True
    assert telemetry["specialized_hint_used"] is True
    assert telemetry["retry_attempt_number"] == 2

    unit.attempt_count = 2
    second_retry_hint = v3_pipeline._failure_hint(unit, config)
    assert "[translator_v3 compression repair]" not in second_retry_hint


@test("compression repair telemetry records accepted post-repair status")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "compression.txt"
        output_path = Path(tmp) / "compression_ko.txt"
        input_path.write_text(
            "When someone fails to recognize a person or piece of music that you would have thought near and dear to their heart and their practice, it is interesting and educational.\n",
            encoding="utf-8",
        )

        class CompressionRepairBackend:
            def __init__(self):
                self.body_calls = 0

            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                if unit.unit_class == "heading":
                    return BackendResponse(
                        text="(전체 문서)",
                        backend_name="compression-repair-test",
                        provider_name="compression-repair-test",
                        model_name="compression-repair-test",
                    )

                self.body_calls += 1
                if self.body_calls == 1:
                    assert failure_hint == ""
                    text = "흥미롭고 교육적이다."
                else:
                    assert "[translator_v3 compression repair]" in failure_hint
                    text = (
                        "누군가가 그 사람의 마음과 작업에 매우 가깝고 소중하다고 당신이 여겼을 인물이나 음악 작품을 "
                        "알아보지 못할 때, 그것은 흥미롭고도 교육적인 일이 된다."
                    )
                return BackendResponse(
                    text=text,
                    backend_name="compression-repair-test",
                    provider_name="compression-repair-test",
                    model_name="compression-repair-test",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                if "흥미롭고 교육적이다." == translated_text:
                    return False, "material qualifier appears distorted"
                return True, ""

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: CompressionRepairBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "DELIVERABLE"
        units = json.loads((Path(result.artifact_dir) / "translation_units.json").read_text(encoding="utf-8"))
        body = next(unit for unit in units if unit["unit_class"] == "body_paragraph")
        telemetry = body["telemetry"]["compression_repair_retry"]
        assert body["attempt_count"] == 2
        assert telemetry["classified_compression_like"] is True
        assert telemetry["specialized_hint_used"] is True
        assert telemetry["retry_attempt_number"] == 2
        assert telemetry["post_repair_status"] == "ACCEPTED"


@test("compression repair path allows one extra retry after post-repair adequacy failure")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "compression.txt"
        output_path = Path(tmp) / "compression_ko.txt"
        input_path.write_text(
            "When someone fails to recognize a person or piece of music that you would have thought near and dear to their heart and their practice, it is interesting and educational.\n",
            encoding="utf-8",
        )

        class ExtraCompressionRetryBackend:
            def __init__(self):
                self.body_calls = 0
                self.failure_hints = []

            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                if unit.unit_class == "heading":
                    return BackendResponse(
                        text="(전체 문서)",
                        backend_name="compression-extra-retry-test",
                        provider_name="compression-extra-retry-test",
                        model_name="compression-extra-retry-test",
                    )

                self.body_calls += 1
                self.failure_hints.append(failure_hint)
                if self.body_calls == 1:
                    assert failure_hint == ""
                    text = "흥미롭고 교육적이다."
                elif self.body_calls == 2:
                    assert "[translator_v3 compression repair]" in failure_hint
                    text = "그것은 교육적인 일이다."
                else:
                    assert "adequacy_review_failed" in failure_hint
                    assert "[translator_v3 compression repair]" not in failure_hint
                    text = (
                        "누군가가 그 사람의 마음과 작업에 매우 가깝고 소중하다고 당신이 여겼을 인물이나 음악 작품을 "
                        "알아보지 못할 때, 그것은 흥미롭고도 교육적인 일이 된다."
                    )
                return BackendResponse(
                    text=text,
                    backend_name="compression-extra-retry-test",
                    provider_name="compression-extra-retry-test",
                    model_name="compression-extra-retry-test",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                if translated_text in {"흥미롭고 교육적이다.", "그것은 교육적인 일이다."}:
                    return False, "material qualifier still missing"
                return True, ""

        backend = ExtraCompressionRetryBackend()
        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: backend
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "DELIVERABLE"
        units = json.loads((Path(result.artifact_dir) / "translation_units.json").read_text(encoding="utf-8"))
        body = next(unit for unit in units if unit["unit_class"] == "body_paragraph")
        telemetry = body["telemetry"]["compression_repair_retry"]
        assert body["attempt_count"] == 3
        assert backend.body_calls == 3
        assert telemetry["classified_compression_like"] is True
        assert telemetry["specialized_hint_used"] is True
        assert telemetry["retry_attempt_number"] == 2
        assert telemetry["post_repair_status"] == "ACCEPTED"


@test("quality benchmark fixture can deterministically reach attempt 3 and recover")
def _():
    result = _run_quality_fixture_case(
        "en_compression_truncated_predicate",
        "compression_attempt3_recovered.fixture.json",
    )
    assert result.status == "DELIVERABLE"
    assert result.max_body_attempt_count == 3
    assert len(result.body_unit_outcomes) == 1
    body = result.body_unit_outcomes[0]
    assert body["attempt_count"] == 3
    telemetry = body["compression_retry_telemetry"]
    assert telemetry["classified_compression_like"] is True
    assert telemetry["specialized_hint_used"] is True
    assert telemetry["retry_attempt_number"] == 2
    assert telemetry["post_repair_status"] == "ACCEPTED"


@test("quality benchmark fixture can deterministically reach attempt 3 and still block")
def _():
    result = _run_quality_fixture_case(
        "en_compression_truncated_predicate",
        "compression_attempt3_blocked.fixture.json",
    )
    assert result.status == "BLOCKED"
    assert result.max_body_attempt_count == 3
    assert len(result.body_unit_outcomes) == 1
    body = result.body_unit_outcomes[0]
    assert body["attempt_count"] == 3
    telemetry = body["compression_retry_telemetry"]
    assert telemetry["classified_compression_like"] is True
    assert telemetry["specialized_hint_used"] is True
    assert telemetry["retry_attempt_number"] == 2
    assert telemetry["post_repair_status"] == "BLOCKED"


@test("quality soak runner logs deliverable blocked stalled and timed-out runs")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        run_root = Path(tmp) / "soak"
        original_run = quality_soak.subprocess.run

        class FakeCompletedProcess:
            def __init__(self, returncode=0):
                self.returncode = returncode

        call_count = 0

        def fake_run(cmd, cwd, check, capture_output, text, timeout):
            nonlocal call_count
            call_count += 1
            report_path = Path(cmd[cmd.index("--report-path") + 1])
            artifact_root = Path(cmd[cmd.index("--artifact-root") + 1])
            artifact_dir = artifact_root / f"artifact-{call_count:02d}"
            artifact_dir.mkdir(parents=True, exist_ok=True)

            if call_count == 1:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(
                        {
                            "results": [
                                {
                                    "status": "DELIVERABLE",
                                    "artifact_dir": str(artifact_dir),
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return FakeCompletedProcess()

            if call_count == 2:
                (artifact_dir / "run_result.json").write_text(
                    json.dumps({"status": "BLOCKED"}),
                    encoding="utf-8",
                )
                return FakeCompletedProcess(returncode=1)

            if call_count == 3:
                (artifact_dir / "telemetry.json").write_text(
                    json.dumps({"phase": "translating", "adequacy_review_status": "not_run"}),
                    encoding="utf-8",
                )
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

            (artifact_dir / "telemetry.json").write_text(
                json.dumps({"phase": "translating", "adequacy_review_status": "selective_model_review"}),
                encoding="utf-8",
            )
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        quality_soak.subprocess.run = fake_run
        try:
            summary = quality_soak.run_soak(
                manifest="translator_v3/benchmarks/fixtures/quality/cases.json",
                case_id="en_omission_flynt_surprised",
                backend="codex_local",
                mock_backend_fixture="",
                model="gpt-5.4",
                reasoning_effort="high",
                run_root=str(run_root),
                run_count=4,
                timeout_seconds=1,
            )
        finally:
            quality_soak.subprocess.run = original_run

        counts = summary["status_counts"]
        assert counts["completed_deliverable"] == 1
        assert counts["completed_blocked"] == 1
        assert counts["stalled_before_adequacy_review"] == 1
        assert counts["timed_out"] == 1

        status_lines = (run_root / "status.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(status_lines) == 4
        statuses = [json.loads(line)["status"] for line in status_lines]
        assert statuses == [
            "completed_deliverable",
            "completed_blocked",
            "stalled_before_adequacy_review",
            "timed_out",
        ]


@test("pipeline persists await heartbeat markers around translate and reviewer calls")
def _():
    async def wait_for_heartbeat(search_root: Path, expected_stage: str, unit_class: str | None = "body_paragraph") -> dict:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            for units_path in search_root.rglob("translation_units.json"):
                try:
                    units = json.loads(units_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                for unit in units:
                    if unit_class is not None and unit["unit_class"] != unit_class:
                        continue
                    heartbeat = unit.get("telemetry", {}).get("await_heartbeat", {})
                    if heartbeat.get("stage") == expected_stage:
                        return heartbeat
            await asyncio.sleep(0.05)
        raise AssertionError(f"timed out waiting for heartbeat stage {expected_stage}")

    async def run_translate_probe(tmp: str) -> None:
        input_path = Path(tmp) / "translate_only.txt"
        output_path = Path(tmp) / "translate_only_ko.txt"
        input_path.write_text("This paragraph tests translate heartbeat persistence.\n", encoding="utf-8")

        class BlockingTranslateBackend:
            def __init__(self):
                self.release_translate = asyncio.Event()

            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                if unit.unit_class != "body_paragraph":
                    return BackendResponse(
                        text="(전체 문서)",
                        backend_name="blocking-translate",
                        provider_name="blocking-translate",
                        model_name="blocking-translate",
                    )
                await self.release_translate.wait()
                return BackendResponse(
                    text="번역 대기 상태를 기록하는 테스트 문장이다.",
                    backend_name="blocking-translate",
                    provider_name="blocking-translate",
                    model_name="blocking-translate",
                )

        backend = BlockingTranslateBackend()
        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: backend
        try:
            task = asyncio.create_task(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(
                        source_lang="en",
                        target_lang="ko",
                        run_root=tmp,
                        backend_name="echo",
                        adequacy_review_mode="off",
                    ),
                )
            )
            before = await wait_for_heartbeat(Path(tmp), "before_translate_unit", unit_class=None)
            assert before["call_name"] == "translate_unit"
            assert before["outcome"] == "in_progress"
            assert before["completed_at"] == ""
            backend.release_translate.set()
            result = await task
        finally:
            v3_pipeline.build_backend = original

        units = json.loads(Path(result.artifact_dir, "translation_units.json").read_text(encoding="utf-8"))
        body = next(unit for unit in units if unit["unit_class"] == "body_paragraph")
        heartbeat = body["telemetry"]["await_heartbeat"]
        assert heartbeat["stage"] == "after_translate_unit"
        assert heartbeat["call_name"] == "translate_unit"
        assert heartbeat["outcome"] == "completed"
        assert heartbeat["started_at"]
        assert heartbeat["completed_at"]

    async def run_review_probe(tmp: str) -> None:
        input_path = Path(tmp) / "review_only.txt"
        output_path = Path(tmp) / "review_only_ko.txt"
        input_path.write_text(
            (
                "This paragraph is intentionally long enough to trigger adequacy review and verify that "
                "the reviewer heartbeat is flushed before the awaited reviewer call returns.\n"
            ),
            encoding="utf-8",
        )

        class BlockingReviewBackend:
            def __init__(self):
                self.release_review = asyncio.Event()

            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                return BackendResponse(
                    text=(
                        "이 문장은 적절성 검토 하트비트가 검토 호출 전후에 기록되는지 확인하기 위해 "
                        "충분한 길이로 작성된 테스트 번역문이다."
                    ),
                    backend_name="blocking-review",
                    provider_name="blocking-review",
                    model_name="blocking-review",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                await self.release_review.wait()
                return True, ""

        backend = BlockingReviewBackend()
        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: backend
        try:
            task = asyncio.create_task(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
            before = await wait_for_heartbeat(Path(tmp), "before_reviewer")
            assert before["call_name"] == "review_unit_adequacy"
            assert before["outcome"] == "in_progress"
            assert before["completed_at"] == ""
            backend.release_review.set()
            result = await task
        finally:
            v3_pipeline.build_backend = original

        units = json.loads(Path(result.artifact_dir, "translation_units.json").read_text(encoding="utf-8"))
        body = next(unit for unit in units if unit["unit_class"] == "body_paragraph")
        heartbeat = body["telemetry"]["await_heartbeat"]
        assert heartbeat["stage"] == "after_reviewer"
        assert heartbeat["call_name"] == "review_unit_adequacy"
        assert heartbeat["outcome"] == "completed"
        assert heartbeat["started_at"]
        assert heartbeat["completed_at"]

    with tempfile.TemporaryDirectory() as translate_tmp:
        asyncio.run(run_translate_probe(translate_tmp))
    with tempfile.TemporaryDirectory() as review_tmp:
        asyncio.run(run_review_probe(review_tmp))


@test("adequacy review exceptions block the unit without crashing the run")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text(
            "This sentence triggers adequacy review exception handling in the orchestration layer.\n",
            encoding="utf-8",
        )

        class ReviewExplodingBackend:
            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                return BackendResponse(
                    text="검토 예외를 테스트하는 번역문이다.",
                    backend_name="review-exploding",
                    provider_name="review-exploding",
                    model_name="review-exploding",
                )

            async def review_unit_adequacy(self, unit, translated_text):
                raise RuntimeError("review backend timeout")

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: ReviewExplodingBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="provider_api"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        review_bundle = json.loads(Path(result.review_bundle_path).read_text(encoding="utf-8"))
        issue_types = {
            issue["issue_type"]
            for unit in review_bundle["units"]
            for issue in unit.get("validation_errors", [])
        }
        assert "adequacy_review_exception" in issue_types


@test("backend exceptions block the run instead of crashing the pipeline")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text("This paragraph triggers backend exception handling.\n", encoding="utf-8")

        class ExplodingBackend:
            async def translate_unit(self, unit, failure_hint=""):
                raise RuntimeError("boom from backend")

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: ExplodingBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="echo"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        assert result.final_output_written is False
        review = json.loads(Path(result.review_bundle_path).read_text(encoding="utf-8"))
        assert review["summary"]["blocked_units"] >= 1


@test("heading exact-copy output blocks deliverable")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        input_path.write_text("CHAPTER 1\n\nBody paragraph.\n", encoding="utf-8")

        class HeadingCopyBackend:
            async def translate_unit(self, unit, failure_hint=""):
                from translator_v3.backends.base import BackendResponse

                if unit.unit_class == "heading":
                    text = unit.source_text
                else:
                    text = "본문 번역 결과입니다."
                return BackendResponse(
                    text=text,
                    backend_name="bad-heading-test",
                    provider_name="bad-heading-test",
                    model_name="bad-heading-test",
                )

        original = v3_pipeline.build_backend
        v3_pipeline.build_backend = lambda config: HeadingCopyBackend()
        try:
            result = asyncio.run(
                run_pipeline(
                    str(input_path),
                    str(output_path),
                    V3Config(source_lang="en", target_lang="ko", run_root=tmp, backend_name="echo"),
                )
            )
        finally:
            v3_pipeline.build_backend = original

        assert result.status == "BLOCKED"
        preview_text = Path(result.preview_path).read_text(encoding="utf-8")
        assert "CHAPTER 1" not in preview_text


@test("split oversized block assembles back into one paragraph")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        long_text = ("This is a long sentence for assembly testing. " * 40).strip()
        input_path.write_text(f"CHAPTER 1\n\n{long_text}\n", encoding="utf-8")
        result = asyncio.run(
            run_pipeline(
                str(input_path),
                str(output_path),
                V3Config(
                    source_lang="en",
                    target_lang="ko",
                    run_root=tmp,
                    backend_name="echo",
                    max_unit_chars=120,
                ),
            )
        )
        assert result.status == "DELIVERABLE"
        rendered = output_path.read_text(encoding="utf-8")
        assert rendered.count("테스트용 번역 결과") >= 2
        assert "결과 0003.001입니다.\n\n이 단락은 테스트용 번역 결과 0004.001입니다." in rendered
        assert "결과 0004.001입니다. 이 단락은 테스트용 번역 결과 0004.002입니다." in rendered
        assert "결과 0004.001입니다.\n\n이 단락은 테스트용 번역 결과 0004.002입니다." not in rendered


@test("unit ordering stays stable when a block splits into more than 99 chunks")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "book.txt"
        output_path = Path(tmp) / "book_ko.txt"
        very_long = ("abcdefghij " * 700).strip()
        input_path.write_text(
            f"CHAPTER 1\n\n{very_long}\n\nSecond block after the long one.\n",
            encoding="utf-8",
        )
        result = asyncio.run(
            run_pipeline(
                str(input_path),
                str(output_path),
                V3Config(
                    source_lang="en",
                    target_lang="ko",
                    run_root=tmp,
                    backend_name="echo",
                    max_unit_chars=20,
                ),
            )
        )
        assert result.status == "DELIVERABLE"
        rendered = output_path.read_text(encoding="utf-8")
        assert "결과 0004.100입니다." in rendered
        assert "결과 0005.001입니다." in rendered
        assert rendered.index("결과 0004.100입니다.") < rendered.index("결과 0005.001입니다.")


print("\n" + "=" * 60)
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if errors:
    print("\nErrors:")
    for name, error in errors:
        print(f"  - {name}: {error}")
    raise SystemExit(1)

print("\nAll translator_v3 tests passed.")
