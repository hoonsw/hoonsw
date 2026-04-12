#!/usr/bin/env python3
"""
Regression tests for translate.py entrypoint configuration behavior.
"""

from __future__ import annotations

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


def main() -> None:
    print("=" * 60)
    print("translate.py entrypoint tests")
    print("=" * 60)
    tests = [
        ("translation config exposes provider_name", test_translation_config_exposes_provider_name),
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
    print("\nAll translate.py entrypoint tests passed.")


if __name__ == "__main__":
    main()
