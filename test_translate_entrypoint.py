#!/usr/bin/env python3
"""
Regression tests for the shared translate.py helper module and retired entrypoints.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from translate import TranslationConfig


def test_translation_config_exposes_provider_name() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "source_lang: ja",
                    "target_lang: ko",
                    "model: gpt-5.2-chat-latest",
                    "providers:",
                    "  translation:",
                    "    provider: openai",
                    "    model: gpt-5.2-chat-latest",
                ]
            ),
            encoding="utf-8",
        )
        config = TranslationConfig(str(config_path))
        assert config.provider_name == "openai", config.provider_name


def test_translate_cli_prints_retirement_message() -> None:
    result = subprocess.run(
        [sys.executable, "translate.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent,
    )
    assert result.returncode == 2
    assert "translate.py CLI was retired" in result.stdout
    assert "job_runner.py" in result.stdout


def test_run_pipeline_cli_prints_retirement_message() -> None:
    result = subprocess.run(
        [sys.executable, "run_pipeline.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "run_pipeline.py was retired" in output
    assert "translator_v3.cli" in output


def test_web_cli_prints_retirement_message() -> None:
    result = subprocess.run(
        [sys.executable, "web.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "web.py was retired" in output
    assert "job_runner" in output or "translator_v3" in output


def main() -> None:
    print("=" * 60)
    print("translate.py helper / retired entrypoint tests")
    print("=" * 60)
    tests = [
        ("translation config exposes provider_name", test_translation_config_exposes_provider_name),
        ("translate.py CLI prints retirement message", test_translate_cli_prints_retirement_message),
        ("run_pipeline.py CLI prints retirement message", test_run_pipeline_cli_prints_retirement_message),
        ("web.py CLI prints retirement message", test_web_cli_prints_retirement_message),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            failed += 1
    print("\n" + "=" * 60)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    if failed:
        raise SystemExit(1)
    print("\nAll translate.py helper / retired entrypoint tests passed.")


if __name__ == "__main__":
    main()
