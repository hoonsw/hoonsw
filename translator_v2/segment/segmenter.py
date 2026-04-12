from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from translator_v2.core.models import ChapterRecord, LoadedDocument, SegmentRecord, V2Config
from translator_v2.segment.chapter_detector import detect_chapters


def split_into_sentences(text: str, lang: str) -> List[str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.strip():
        return []
    boundary_re = re.compile(r"[。！？]" if lang == "ja" else r"[.!?]")
    spans: List[str] = []
    cursor = 0
    for match in boundary_re.finditer(normalized):
        end = match.end()
        while end < len(normalized) and normalized[end].isspace() and normalized[end] != "\n":
            end += 1
        chunk = normalized[cursor:end]
        if chunk.strip():
            spans.append(chunk)
        cursor = end
    tail = normalized[cursor:]
    if tail.strip():
        spans.append(tail)
    if len(spans) > 1:
        return spans
    lines = [line for line in normalized.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    return [normalized]


def _split_text_by_length(text: str, max_chars: int) -> List[str]:
    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]
    pieces: List[str] = []
    cursor = 0
    while len(text) - cursor > max_chars:
        window_end = cursor + max_chars
        split_at = text.rfind(" ", cursor, window_end + 1)
        if split_at <= cursor + max_chars // 2:
            split_at = window_end
        else:
            while split_at < len(text) and text[split_at].isspace() and text[split_at] != "\n":
                split_at += 1
        piece = text[cursor:split_at]
        if piece:
            pieces.append(piece)
        cursor = split_at
    tail = text[cursor:]
    if tail:
        pieces.append(tail)
    return [piece for piece in pieces if piece.strip()]


def _split_paragraph(paragraph: str, source_lang: str, config: V2Config) -> List[str]:
    if not paragraph.strip():
        return []
    sentences = split_into_sentences(paragraph, source_lang)
    if (
        len(paragraph) <= config.max_segment_chars
        and len(sentences) <= config.max_segment_sentences
    ):
        return [paragraph]
    if len(sentences) <= 1:
        return _split_text_by_length(paragraph, config.hard_max_segment_chars)
    units: List[str] = []
    current = ""
    current_count = 0
    for sentence in sentences:
        if not sentence.strip():
            continue
        if len(sentence) > config.hard_max_segment_chars:
            if current:
                units.append(current)
                current = ""
                current_count = 0
            units.extend(_split_text_by_length(sentence, config.hard_max_segment_chars))
            continue
        next_len = len(current) + len(sentence)
        if current and (
            next_len > config.max_segment_chars
            or current_count >= config.max_segment_sentences
        ):
            units.append(current)
            current = sentence
            current_count = 1
        else:
            current = sentence if not current else current + sentence
            current_count += 1
    if current:
        units.append(current)
    return units or [paragraph]


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _leading_ws(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def _trailing_ws(text: str) -> str:
    return text[len(text.rstrip()):]


def _clean_unit_text(text: str) -> str:
    return text.strip()


def _between_units_separator(current_raw: str, next_raw: str) -> str:
    separator = _trailing_ws(current_raw) + _leading_ws(next_raw)
    return separator


def _child_suffix(index: int) -> str:
    letters = []
    value = index
    while value > 0:
        value -= 1
        letters.append(chr(97 + (value % 26)))
        value //= 26
    return "".join(reversed(letters))


def build_initial_ledger_records(document: LoadedDocument, config: V2Config) -> Tuple[List[ChapterRecord], List[SegmentRecord]]:
    chapters = detect_chapters(document.normalized_source_text, document.source_lang)
    chapter_records: List[ChapterRecord] = []
    segment_records: List[SegmentRecord] = []
    global_order = 0

    for chapter_index, chapter in enumerate(chapters, start=1):
        chapter_id = f"ch{chapter_index:03d}"
        chapter_record = ChapterRecord(
            chapter_id=chapter_id,
            title=chapter.title,
            start_offset=chapter.start_offset,
            end_offset=chapter.end_offset,
            source_order_index=chapter_index,
            segment_ids=[],
        )
        chapter_records.append(chapter_record)

        paragraphs = [paragraph for paragraph in chapter.text.split("\n\n") if paragraph.strip()]
        local_offset = 0
        segment_index = 0
        for para_index, paragraph in enumerate(paragraphs):
            paragraph_start = chapter.text.find(paragraph, local_offset)
            if paragraph_start == -1:
                paragraph_start = local_offset
            split_units = _split_paragraph(paragraph, document.source_lang, config)
            paragraph_cursor = 0
            for unit_index, raw_unit in enumerate(split_units):
                unit = _clean_unit_text(raw_unit)
                if not unit:
                    continue
                segment_index += 1
                global_order += 1
                segment_id = f"{chapter_id}.s{segment_index:03d}"
                source_pos = paragraph.find(unit, paragraph_cursor)
                if source_pos == -1:
                    source_pos = paragraph_cursor
                source_start = chapter.start_offset + paragraph_start + source_pos
                source_end = source_start + len(unit)
                if unit_index < len(split_units) - 1:
                    separator_after = _between_units_separator(raw_unit, split_units[unit_index + 1])
                elif para_index < len(paragraphs) - 1:
                    separator_after = "\n\n"
                else:
                    separator_after = ""
                record = SegmentRecord(
                    segment_id=segment_id,
                    parent_segment_id=None,
                    child_segment_ids=[],
                    chapter_id=chapter_id,
                    source_order_index=global_order,
                    source_start_offset=source_start,
                    source_end_offset=source_end,
                    source_text=unit,
                    source_hash=_source_hash(unit),
                    separator_after=separator_after,
                )
                segment_records.append(record)
                chapter_record.segment_ids.append(segment_id)
                paragraph_cursor = source_pos + len(unit)
            local_offset = paragraph_start + len(paragraph)

    return chapter_records, segment_records


def split_segment_for_repair(segment: SegmentRecord, source_lang: str, config: V2Config) -> List[SegmentRecord]:
    sentences = split_into_sentences(segment.source_text, source_lang)
    if len(sentences) > 1:
        units: List[str] = []
        for sentence in sentences:
            if len(sentence) > config.max_segment_chars:
                units.extend(_split_text_by_length(sentence, config.max_segment_chars))
            else:
                units.append(sentence)
    else:
        repair_max_chars = max(80, min(config.max_segment_chars, max(80, config.hard_max_segment_chars // 2)))
        units = _split_text_by_length(segment.source_text, repair_max_chars)
    if len(units) <= 1:
        return []
    children: List[SegmentRecord] = []
    local_cursor = 0
    for idx, raw_unit in enumerate(units, start=1):
        unit = _clean_unit_text(raw_unit)
        if not unit:
            continue
        child_id = f"{segment.segment_id}.{_child_suffix(idx)}"
        separator_after = (
            _between_units_separator(raw_unit, units[idx])
            if idx < len(units)
            else segment.separator_after
        )
        local_start = segment.source_text.find(unit, local_cursor)
        if local_start == -1:
            local_start = local_cursor
        local_end = local_start + len(unit)
        child = SegmentRecord(
            segment_id=child_id,
            parent_segment_id=segment.segment_id,
            child_segment_ids=[],
            chapter_id=segment.chapter_id,
            source_order_index=segment.source_order_index,
            source_start_offset=segment.source_start_offset + local_start,
            source_end_offset=segment.source_start_offset + local_end,
            source_text=unit,
            source_hash=_source_hash(unit),
            separator_after=separator_after,
            child_order_index=idx,
        )
        local_cursor = local_end
        children.append(child)
    return children
