from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from input_pipeline import process_input
from translator_v2.core.models import LoadedDocument, V2Config
from translator_v2.ingest.normalize import canonicalize_text, sha256_file, sha256_text


def load_rules_and_glossary(config: V2Config) -> Tuple[str, Dict]:
    rules_text = ""
    rules_path = Path(config.rules_path)
    if rules_path.exists():
        rules_text = rules_path.read_text(encoding="utf-8")

    glossary_payload: Dict = {}
    glossary_path = Path(config.glossary_path)
    if glossary_path.exists():
        glossary_payload = json.loads(glossary_path.read_text(encoding="utf-8"))

    return rules_text, glossary_payload


def load_document(input_path: str, config: V2Config) -> LoadedDocument:
    result = process_input(input_path, source_lang=config.source_lang, force_ocr=config.force_ocr)
    normalized = canonicalize_text(result.text)
    return LoadedDocument(
        input_path=str(Path(input_path).resolve()),
        input_sha256=sha256_file(input_path),
        normalized_source_text=normalized,
        normalized_source_sha256=sha256_text(normalized),
        source_lang=result.analysis.get("effective_source_lang", config.source_lang),
        target_lang=config.target_lang,
        ingest_state=result.state.value,
        ingest_analysis=dict(result.analysis),
        warnings=list(result.warnings),
    )
