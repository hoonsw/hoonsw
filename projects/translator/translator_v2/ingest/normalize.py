from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from translator_v2.core.models import V2Config


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonicalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\t", " ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + ("\n" if text.strip() else "")


def config_fingerprint(config: V2Config, rules_text: str, glossary_payload: dict) -> str:
    payload = {
        "source_lang": config.source_lang,
        "target_lang": config.target_lang,
        "provider_name": config.provider_name,
        "model": config.model,
        "generation_mode": config.generation_mode,
        "codex_worker_mode": config.codex_worker_mode,
        "max_turns": config.max_turns,
        "batch_size": config.batch_size,
        "max_segment_chars": config.max_segment_chars,
        "max_segment_sentences": config.max_segment_sentences,
        "hard_max_segment_chars": config.hard_max_segment_chars,
        "local_retry_max": config.local_retry_max,
        "split_retry_max": config.split_retry_max,
        "max_total_repairs_per_segment": config.max_total_repairs_per_segment,
        "ingest_version": config.ingest_version,
        "protected_regexes": list(config.protected_regexes),
        "canonicalizer_version": config.canonicalizer_version,
        "segmenter_version": config.segmenter_version,
        "prompt_schema_version": config.prompt_schema_version,
        "rules_sha256": sha256_text(rules_text),
        "glossary_sha256": sha256_text(json.dumps(glossary_payload, ensure_ascii=False, sort_keys=True)),
    }
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))
