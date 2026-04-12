from __future__ import annotations

import re
from typing import List

from translator_v3.core.models import DocumentGraph, TranslationUnit, V3Config


def _split_sentences(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    sentence_parts = re.split(r"(?<=[.!?。！？])\s+", stripped)
    sentences = [part.strip() for part in sentence_parts if part.strip()]
    return sentences or [stripped]


def _split_oversized_text(text: str, max_chars: int) -> List[str]:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return [stripped]

    sentence_parts = _split_sentences(stripped)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for part in sentence_parts:
        candidate = f"{current} {part}".strip() if current else part.strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            flush()
        if len(part.strip()) <= max_chars:
            current = part.strip()
            continue
        hard = part.strip()
        start = 0
        while start < len(hard):
            end = min(len(hard), start + max_chars)
            chunk = hard[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
    flush()
    return chunks or [stripped]


def _effective_max_chars(config: V3Config) -> int:
    hint = (config.preset_chunk_boundary_hints or "paragraph").strip().lower()
    if hint == "scene":
        return int(config.max_unit_chars * 1.2)
    if hint == "argument":
        return int(config.max_unit_chars * 1.35)
    return config.max_unit_chars


def build_translation_units(graph: DocumentGraph, config: V3Config) -> List[TranslationUnit]:
    units: list[TranslationUnit] = []
    chapter_lookup: dict[str, str] = {}
    max_chars = _effective_max_chars(config)

    for node in sorted(graph.nodes, key=lambda item: item.reading_order):
        if node.node_type == "chapter":
            chapter_lookup[node.node_id] = node.node_id
            units.append(
                TranslationUnit(
                    unit_id=f"{node.node_id}.title",
                    node_id=node.node_id,
                    chapter_id=node.node_id,
                    source_order_index=node.reading_order,
                    node_reading_order=node.reading_order,
                    unit_index_within_node=0,
                    unit_class="heading",
                    source_text=node.metadata.get("title", node.source_text),
                    source_lang=graph.source_lang,
                    target_lang=graph.target_lang,
                )
            )
        elif node.node_type == "block":
            chapter_id = node.parent_id or "chapter.unknown"
            unit_class = node.metadata.get("unit_class", "body_paragraph")
            if unit_class == "body_paragraph":
                sentence_chunks: list[str] = []
                for sentence in _split_sentences(node.source_text):
                    sentence_chunks.extend(_split_oversized_text(sentence, max_chars))
                chunks = sentence_chunks or _split_oversized_text(node.source_text, max_chars)
            else:
                chunks = _split_oversized_text(node.source_text, max_chars)
            for chunk_index, chunk_text in enumerate(chunks, start=1):
                suffix = f".u{chunk_index:02d}" if len(chunks) > 1 else ".u"
                units.append(
                    TranslationUnit(
                        unit_id=f"{node.node_id}{suffix}",
                        node_id=node.node_id,
                        chapter_id=chapter_id,
                        source_order_index=node.reading_order,
                        node_reading_order=node.reading_order,
                        unit_index_within_node=chunk_index,
                        unit_class=unit_class,
                        source_text=chunk_text,
                        source_lang=graph.source_lang,
                        target_lang=graph.target_lang,
                    )
                )
    return units
