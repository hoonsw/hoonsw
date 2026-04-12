#!/usr/bin/env python3

import os
import sys
import tempfile
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from input_pipeline import InputState, process_input
from normalizer import TextNormalizer


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
print("input_pipeline tests")
print("=" * 60)


@test("clean prose txt stays clean_txt")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clean.txt"
        path.write_text(
            "This is a clean paragraph of prose.\n\n"
            "It has normal sentence boundaries and no page artifacts.\n",
            encoding="utf-8",
        )
        result = process_input(str(path), source_lang="en")
        assert result.state == InputState.CLEAN_TXT
        assert result.analysis["selected_state"] == InputState.CLEAN_TXT.value


@test("cp932 txt decodes successfully for japanese ingest")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.txt"
        path.write_bytes("これはテストです。\n".encode("cp932"))
        result = process_input(str(path), source_lang="ja")
        assert result.state == InputState.CLEAN_TXT
        assert "これはテストです。" in result.text
        assert any("cp932" in warning for warning in result.warnings)


@test("english-dominant txt auto-corrects source language")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "english.txt"
        path.write_text(
            "This is an English manuscript paragraph with enough latin text to trigger detection.\n"
            "It should switch away from a Japanese configuration.\n",
            encoding="utf-8",
        )
        result = process_input(str(path), source_lang="ja")
        assert result.analysis["effective_source_lang"] == "en"
        assert any("Auto-corrected source_lang from ja to en" in warning for warning in result.warnings)


@test("pdf-like txt is routed through pdf normalization")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pdfish.txt"
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
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = process_input(str(path), source_lang="en")
        assert result.state == InputState.TXT_PDF_NORMALIZED
        assert "PDF-like layout artifacts" in " ".join(result.warnings)
        assert "\f" not in result.text
        assert "inter-\nwoven" not in result.text


@test("ocr-like txt is routed through ocr normalization")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ocrish.txt"
        path.write_text(
            "T H I S I S A T I T L E\n"
            "\ufffd\ufffd\ufffd\ufffd\ufffd\n"
            "---\n"
            "T H I S L I N E W A S B A D L Y R E A D B Y O C R\n"
            "A N O T H E R H E A D E R\n"
            "\ufffd\ufffd\ufffd\ufffd\ufffd\n"
            "----\n"
            "***\n"
            "T H I S L I N E W A S B A D L Y R E A D B Y O C R A G A I N\n",
            encoding="utf-8",
        )
        result = process_input(str(path), source_lang="en")
        assert result.state == InputState.TXT_OCR_NORMALIZED
        assert "OCR-like layout artifacts" in " ".join(result.warnings)
        assert "\ufffd" not in result.text


@test("japanese ocr-like txt is routed through ocr normalization")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ocrish_ja.txt"
        path.write_text(
            "こ れ は 見 出 し で す\n"
            "\ufffd\ufffd\ufffd\ufffd\n"
            "---\n"
            "こ れ は O C R で 乱 れ た 行 で す\n"
            "あ た ら し い 小 見 出 し\n"
            "\ufffd\ufffd\ufffd\ufffd\n"
            "***\n"
            "こ れ も O C R で 乱 れ た 行 で す\n",
            encoding="utf-8",
        )
        result = process_input(str(path), source_lang="ja")
        assert result.state == InputState.TXT_OCR_NORMALIZED
        assert "OCR-like layout artifacts" in " ".join(result.warnings)


@test("pdf normalization does not strip repeated short sentences with punctuation")
def _():
    normalizer = TextNormalizer("en")
    text = (
        "Refrain line.\n"
        "Body text continues here with a complete sentence.\n\n"
    ) * 5
    cleaned = normalizer.normalize_pdf_text(text)
    assert "Refrain line." in cleaned


print("\n" + "=" * 60)
print(f"Passed: {passed}")
print(f"Failed: {failed}")

if errors:
    print("\nErrors:")
    for name, error in errors:
        print(f"  - {name}: {error}")
    raise SystemExit(1)

print("\nAll input_pipeline tests passed.")
