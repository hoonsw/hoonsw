from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Optional

from translator_v2.adapters.providers import build_provider
from translator_v2.codex_local.worker import build_codex_exec_command, render_codex_exec_prompt
from translator_v2.core.models import V2Config
from translator_v2.translate.prompt_builder import build_batch_system_prompt, build_batch_user_prompt
from translator_v3.core.models import TranslationUnit, V3Config


@dataclass
class BackendResponse:
    text: str
    backend_name: str
    provider_name: str
    model_name: str


class V3Backend(ABC):
    def __init__(self, config: V3Config):
        self.config = config
        self.supports_parallel = False

    @abstractmethod
    async def translate_unit(self, unit: TranslationUnit, failure_hint: str = "") -> BackendResponse:
        ...

    async def review_unit_adequacy(self, unit: TranslationUnit, translated_text: str) -> tuple[bool, str]:
        return True, ""


class EchoBackend(V3Backend):
    def __init__(self, config: V3Config):
        super().__init__(config)
        self.supports_parallel = True

    async def translate_unit(self, unit: TranslationUnit, failure_hint: str = "") -> BackendResponse:
        if unit.unit_class == "heading":
            numbers = re.findall(r"\d+", unit.source_text)
            if numbers:
                text = f"제목 {' '.join(numbers)}"
            else:
                text = f"제목 {unit.node_reading_order:04d}.{unit.unit_index_within_node:03d}"
        else:
            text = (
                "이 단락은 테스트용 번역 결과 "
                f"{unit.node_reading_order:04d}.{unit.unit_index_within_node:03d}입니다."
            )
        return BackendResponse(
            text=text,
            backend_name="echo",
            provider_name="echo",
            model_name="echo",
        )


class ProviderApiBackend(V3Backend):
    def __init__(self, config: V3Config):
        super().__init__(config)
        self.provider = build_provider(
            V2Config(
                source_lang=config.source_lang,
                target_lang=config.target_lang,
                provider_name=config.provider_name,
                model=config.model,
                max_turns=config.max_turns,
            )
        )

    async def translate_unit(self, unit: TranslationUnit, failure_hint: str = "") -> BackendResponse:
        from translator_v2.core.models import SegmentRecord

        segment = SegmentRecord(
            segment_id=unit.unit_id,
            parent_segment_id=None,
            child_segment_ids=[],
            chapter_id=unit.chapter_id,
            source_order_index=unit.source_order_index,
            source_start_offset=0,
            source_end_offset=len(unit.source_text),
            source_text=unit.source_text,
            source_hash="",
            separator_after="\n\n",
        )
        v2_config = V2Config(
            source_lang=unit.source_lang,
            target_lang=unit.target_lang,
            provider_name=self.config.provider_name,
            model=self.config.model,
            max_turns=self.config.max_turns,
        )
        failure_hints = {segment.segment_id: failure_hint} if failure_hint else None
        rules_text = _build_rules_text(self.config)
        system_prompt = build_batch_system_prompt(
            segments=[segment],
            rules_text=rules_text,
            glossary_payload={},
            config=v2_config,
            failure_hints=failure_hints,
        )
        user_prompt = build_batch_user_prompt([segment])
        response = await self.provider.generate(system_prompt, user_prompt)
        return BackendResponse(
            text=response.text.strip(),
            backend_name="provider_api",
            provider_name=response.provider,
            model_name=response.model,
        )

    async def review_unit_adequacy(self, unit: TranslationUnit, translated_text: str) -> tuple[bool, str]:
        system_prompt, user_prompt = _build_adequacy_review_prompts(unit, translated_text)
        response = await self.provider.generate(system_prompt, user_prompt)
        return _parse_adequacy_review(response.text)


class CodexLocalBackend(V3Backend):
    def __init__(self, config: V3Config):
        super().__init__(config)
        self.supports_parallel = True

    async def translate_unit(self, unit: TranslationUnit, failure_hint: str = "") -> BackendResponse:
        from translator_v2.core.models import SegmentRecord

        segment = SegmentRecord(
            segment_id=unit.unit_id,
            parent_segment_id=None,
            child_segment_ids=[],
            chapter_id=unit.chapter_id,
            source_order_index=unit.source_order_index,
            source_start_offset=0,
            source_end_offset=len(unit.source_text),
            source_text=unit.source_text,
            source_hash="",
            separator_after="\n\n",
        )
        v2_config = V2Config(
            source_lang=unit.source_lang,
            target_lang=unit.target_lang,
            provider_name="codex",
            model=self.config.model,
            max_turns=self.config.max_turns,
        )
        failure_hints = {segment.segment_id: failure_hint} if failure_hint else None
        rules_text = _build_rules_text(self.config)
        system_prompt = build_batch_system_prompt(
            segments=[segment],
            rules_text=rules_text,
            glossary_payload={},
            config=v2_config,
            failure_hints=failure_hints,
        )
        user_prompt = build_batch_user_prompt([segment])
        prompt = render_codex_exec_prompt(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        with tempfile.TemporaryDirectory(prefix="translator_v3_codex_") as tmp:
            output_file = Path(tmp) / "last_message.txt"
            cmd = build_codex_exec_command(
                model=self.config.model,
                reasoning_effort=self.config.reasoning_effort,
                cd=str(Path(__file__).resolve().parents[2]),
                output_file=str(output_file),
            )
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.config.codex_timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"codex exec timed out after {self.config.codex_timeout_seconds}s"
                ) from exc
            if proc.returncode != 0:
                combined = "\n".join(
                    part.strip()
                    for part in (proc.stdout or "", proc.stderr or "")
                    if part and part.strip()
                )
                raise RuntimeError(
                    f"codex exec failed with exit code {proc.returncode}: {combined[-2000:] if combined else 'no output'}"
                )
            if not output_file.exists():
                raise RuntimeError("codex exec completed without output file")
            return BackendResponse(
                text=output_file.read_text(encoding="utf-8").strip(),
                backend_name="codex_local",
                provider_name="codex",
                model_name=self.config.model,
            )

    async def review_unit_adequacy(self, unit: TranslationUnit, translated_text: str) -> tuple[bool, str]:
        system_prompt, user_prompt = _build_adequacy_review_prompts(unit, translated_text)
        prompt = render_codex_exec_prompt(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        with tempfile.TemporaryDirectory(prefix="translator_v3_codex_review_") as tmp:
            output_file = Path(tmp) / "last_message.txt"
            cmd = build_codex_exec_command(
                model=self.config.model,
                reasoning_effort=self.config.reasoning_effort,
                cd=str(Path(__file__).resolve().parents[2]),
                output_file=str(output_file),
            )
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.config.codex_timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"codex adequacy review timed out after {self.config.codex_timeout_seconds}s"
                ) from exc
            if proc.returncode != 0:
                combined = "\n".join(
                    part.strip()
                    for part in (proc.stdout or "", proc.stderr or "")
                    if part and part.strip()
                )
                raise RuntimeError(
                    f"codex adequacy review failed with exit code {proc.returncode}: "
                    f"{combined[-2000:] if combined else 'no output'}"
                )
            if not output_file.exists():
                raise RuntimeError("codex adequacy review completed without output file")
            return _parse_adequacy_review(output_file.read_text(encoding="utf-8"))


def build_backend(config: V3Config) -> V3Backend:
    if config.backend_name == "echo":
        return EchoBackend(config)
    if config.backend_name == "provider_api":
        return ProviderApiBackend(config)
    if config.backend_name == "codex_local":
        return CodexLocalBackend(config)
    raise ValueError(f"Unknown v3 backend: {config.backend_name}")


def _build_rules_text(config: V3Config) -> str:
    sections: list[str] = []
    if config.preset_prompt_modifier.strip():
        sections.append(config.preset_prompt_modifier.strip())
    if config.preset_extra_rules.strip():
        sections.append(config.preset_extra_rules.strip())
    return "\n\n".join(sections)


def _build_adequacy_review_prompts(unit: TranslationUnit, translated_text: str) -> tuple[str, str]:
    system_prompt = (
        "You are a strict translation adequacy reviewer. Compare the source and the translation. "
        "Fail if any materially important source content appears omitted, distorted, or hallucinated. "
        "Minor compression and natural Korean sentence merging are acceptable when the meaning is preserved. "
        "Return JSON only: {\"verdict\":\"pass\"|\"fail\",\"reason\":\"...\"}."
    )
    user_prompt = (
        f"UNIT_CLASS: {unit.unit_class}\n"
        f"SOURCE_LANG: {unit.source_lang}\n"
        f"TARGET_LANG: {unit.target_lang}\n\n"
        f"SOURCE:\n{unit.source_text}\n\n"
        f"TRANSLATION:\n{translated_text}\n"
    )
    return system_prompt, user_prompt


def _parse_adequacy_review(raw_text: str) -> tuple[bool, str]:
    text = raw_text.strip()
    if not text:
        return False, "adequacy reviewer returned empty output"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            lowered = text.lower()
            if '"verdict":"pass"' in lowered or "verdict: pass" in lowered:
                return True, ""
            if '"verdict":"fail"' in lowered or "verdict: fail" in lowered:
                return False, text[-500:]
            return False, f"unparseable adequacy review response: {text[-500:]}"
        payload = json.loads(match.group(0))

    verdict = str(payload.get("verdict", "")).strip().lower()
    reason = str(payload.get("reason", "")).strip()
    if verdict == "pass":
        return True, reason
    if verdict == "fail":
        return False, reason or "adequacy review failed"
    return False, reason or f"unexpected adequacy verdict: {verdict or 'missing'}"
