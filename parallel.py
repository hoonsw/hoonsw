#!/usr/bin/env python3
"""
Chapter/part-level parallelization for translation.

Phase 3 feature: translates multiple chapters/parts concurrently while
respecting context boundaries. Only stable, independent units (chapters,
parts, acts) are parallelized — sub-chapter chunks remain sequential.

Design constraints (from spec):
- Parallel only at chapter/part level, never sub-paragraph
- No parallelization before OCR cleanup is complete
- Context must be stable before parallel dispatch
- Results are reassembled in document order

Usage:
    from parallel import detect_chapters, translate_parallel

    chapters = detect_chapters(source_text, config)
    results = await translate_parallel(chapters, config, max_workers=3)
"""

import asyncio
import re
from typing import List, Tuple, Dict
from dataclasses import dataclass, field

from translate import (
    TranslationConfig, translate_chunk_detailed, split_into_chunks,
    load_rules, load_glossary, build_chunk_records,
    update_record_from_translation_result,
)


# ── Chapter detection ────────────────────────────────────────

@dataclass
class Chapter:
    """A detected chapter/part/section in the source text."""
    index: int
    title: str
    text: str
    start_offset: int = 0
    end_offset: int = 0


# Common chapter/part heading patterns by language
_JA_CHAPTER_PATTERNS = [
    r'^第[一二三四五六七八九十百千\d]+[章節部篇回話幕]',    # 第一章, 第3節, etc.
    r'^[一二三四五六七八九十]+\s',                          # 一 Title, 二 Title
    r'^\d+[\.．]\s',                                        # 1. Title, 2. Title
    r'^[Ⅰ-Ⅹⅰ-ⅹ]+[\s\.．]',                               # Roman numerals
    r'^CHAPTER\s+\d+',                                      # English in Japanese books
    r'^PART\s+\d+',
]

_EN_CHAPTER_PATTERNS = [
    r'^CHAPTER\s+\d+',
    r'^Chapter\s+\d+',
    r'^PART\s+\d+',
    r'^Part\s+\d+',
    r'^BOOK\s+\d+',
    r'^ACT\s+\d+',
    r'^SECTION\s+\d+',
    r'^\d+\.\s+[A-Z]',                                     # 1. Title
    r'^[IVX]+\.\s',                                         # I. Title, II. Title
]


def detect_chapters(
    text: str,
    source_lang: str = "ja",
    min_chapter_size: int = 500,
) -> List[Chapter]:
    """Detect chapter/part boundaries in source text.

    Args:
        text: Full source text
        source_lang: Language code for pattern selection
        min_chapter_size: Minimum characters for a valid chapter

    Returns:
        List of Chapter objects. If no chapters detected, returns a single
        Chapter containing the entire text.
    """
    patterns = _JA_CHAPTER_PATTERNS if source_lang == "ja" else _EN_CHAPTER_PATTERNS
    combined = "|".join(f"({p})" for p in patterns)

    lines = text.split('\n')
    boundaries = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and re.match(combined, stripped):
            offset = sum(len(l) + 1 for l in lines[:i])
            boundaries.append((i, stripped, offset))

    if not boundaries:
        return [Chapter(index=0, title="(full document)", text=text,
                        start_offset=0, end_offset=len(text))]

    chapters = []
    for idx, (line_num, title, start_offset) in enumerate(boundaries):
        if idx + 1 < len(boundaries):
            next_line_num = boundaries[idx + 1][0]
            chapter_lines = lines[line_num:next_line_num]
            end_offset = boundaries[idx + 1][2]
        else:
            chapter_lines = lines[line_num:]
            end_offset = len(text)

        chapter_text = '\n'.join(chapter_lines)
        if len(chapter_text.strip()) >= min_chapter_size:
            chapters.append(Chapter(
                index=len(chapters),
                title=title[:80],
                text=chapter_text,
                start_offset=start_offset,
                end_offset=end_offset,
            ))
        elif chapters:
            # Too small — merge with previous chapter
            chapters[-1].text += '\n' + chapter_text
            chapters[-1].end_offset = end_offset

    # Handle text before first chapter heading
    if boundaries and boundaries[0][0] > 0:
        preamble = '\n'.join(lines[:boundaries[0][0]])
        if preamble.strip() and len(preamble.strip()) >= min_chapter_size:
            chapters.insert(0, Chapter(
                index=-1,  # will be re-indexed below
                title="(preamble)",
                text=preamble,
                start_offset=0,
                end_offset=boundaries[0][2],
            ))
        elif preamble.strip() and chapters:
            # Merge preamble into first chapter
            chapters[0].text = preamble + '\n' + chapters[0].text
            chapters[0].start_offset = 0

    # Re-index
    for i, ch in enumerate(chapters):
        ch.index = i

    # Fallback: if we ended up with 0 chapters (all too small)
    if not chapters:
        return [Chapter(index=0, title="(full document)", text=text,
                        start_offset=0, end_offset=len(text))]

    return chapters


# ── Parallel translation ────────────────────────────────────

@dataclass
class ParallelResult:
    """Result of parallel chapter translation."""
    chapters: List[Chapter]
    chapter_translations: Dict[int, str]
    chapter_chunks: Dict[int, List[dict]]
    total_chunks: int = 0
    warnings: List[str] = field(default_factory=list)
    failed_chapters: List[int] = field(default_factory=list)

    @property
    def full_translation(self) -> str:
        """Reassemble full translation in document order."""
        parts = []
        for ch in sorted(self.chapters, key=lambda c: c.index):
            trans = self.chapter_translations.get(ch.index, "")
            if trans:
                parts.append(trans)
        return "\n\n\n".join(parts)

    def summary(self) -> dict:
        return {
            "total_chapters": len(self.chapters),
            "translated_chapters": len(self.chapter_translations),
            "failed_chapters": self.failed_chapters,
            "total_chunks": self.total_chunks,
            "warnings": self.warnings,
        }


async def _translate_chapter(
    chapter: Chapter,
    config: TranslationConfig,
    rules: str,
    glossary_text: str,
    annotate_terms: list,
    global_context: str = "",
    progress_callback=None,
) -> Tuple[int, str, List[dict]]:
    """Translate a single chapter (all its chunks sequentially).

    Returns (chapter_index, translated_text, chunk_records).
    """
    chunks = split_into_chunks(chapter.text, config)
    records = build_chunk_records(chunks)
    already_annotated = set()

    preset_rules = rules
    if config.preset and config.preset.extra_rules:
        preset_rules = rules + "\n\n" + config.preset.extra_rules

    for i, (context, chunk_text) in enumerate(chunks):
        chunk_num = i + 1
        # For the first chunk of a chapter, prepend global context
        if i == 0 and global_context:
            context = global_context + "\n" + context if context else global_context

        try:
            translation_result = await translate_chunk_detailed(
                chunk_text, context, preset_rules, glossary_text, config,
                annotate_terms=annotate_terms,
                already_annotated=already_annotated,
            )
            update_record_from_translation_result(records[i], translation_result)

            if annotate_terms:
                for term in annotate_terms:
                    if term in chunk_text:
                        already_annotated.add(term)

            if progress_callback:
                await progress_callback("chunk_done", {
                    "chapter": chapter.index,
                    "chapter_title": chapter.title,
                    "chunk": chunk_num,
                    "total_chunks": len(chunks),
                })

        except Exception as e:
            records[i]['translated_text'] = ""
            if progress_callback:
                await progress_callback("chunk_error", {
                    "chapter": chapter.index,
                    "chunk": chunk_num,
                    "error": str(e),
                })

    translated_text = "\n\n\n".join(
        r['translated_text'].strip()
        for r in records if r.get('translated_text', '').strip()
    )

    return chapter.index, translated_text, records


async def translate_parallel(
    chapters: List[Chapter],
    config: TranslationConfig,
    max_workers: int = 3,
    progress_callback=None,
) -> ParallelResult:
    """Translate chapters in parallel with bounded concurrency.

    Args:
        chapters: Detected chapters
        config: Translation configuration
        max_workers: Maximum concurrent chapter translations
        progress_callback: async callable(event, data)

    Returns:
        ParallelResult with all translations reassembled.
    """
    rules = load_rules("rules.md")
    glossary_text, annotate_terms = load_glossary("glossary.json")

    result = ParallelResult(chapters=chapters, chapter_translations={}, chapter_chunks={})
    semaphore = asyncio.Semaphore(max_workers)

    async def _worker(chapter: Chapter, global_ctx: str):
        async with semaphore:
            if progress_callback:
                await progress_callback("chapter_start", {
                    "chapter": chapter.index,
                    "title": chapter.title,
                    "chars": len(chapter.text),
                })

            try:
                ch_idx, translation, records = await _translate_chapter(
                    chapter, config, rules, glossary_text, annotate_terms,
                    global_context=global_ctx,
                    progress_callback=progress_callback,
                )
                result.chapter_translations[ch_idx] = translation
                result.chapter_chunks[ch_idx] = records
                result.total_chunks += len(records)

                if progress_callback:
                    await progress_callback("chapter_done", {
                        "chapter": ch_idx,
                        "title": chapter.title,
                        "chunks": len(records),
                    })
            except Exception as e:
                result.failed_chapters.append(chapter.index)
                result.warnings.append(f"Chapter {chapter.index} ({chapter.title}) failed: {e}")
                if progress_callback:
                    await progress_callback("chapter_failed", {
                        "chapter": chapter.index,
                        "error": str(e),
                    })

    # Build per-chapter context: last few sentences of previous chapter
    # This provides minimal cross-chapter context for parallel workers
    global_contexts = {}
    for ch in chapters:
        if ch.index == 0:
            global_contexts[ch.index] = ""
        else:
            prev = chapters[ch.index - 1]
            # Take last 500 chars of previous chapter as context hint
            tail = prev.text[-500:] if len(prev.text) > 500 else prev.text
            global_contexts[ch.index] = tail

    # Launch all workers
    tasks = [
        _worker(ch, global_contexts.get(ch.index, ""))
        for ch in chapters
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    return result


def reassemble_records(
    parallel_result: ParallelResult,
) -> List[dict]:
    """Reassemble chunk records from parallel result into a flat list.

    Records are ordered by chapter index, then by chunk index within chapter.
    """
    all_records = []
    for ch in sorted(parallel_result.chapters, key=lambda c: c.index):
        ch_records = parallel_result.chapter_chunks.get(ch.index, [])
        for rec in ch_records:
            # Re-index for the flat list
            rec['chapter_index'] = ch.index
            rec['chapter_title'] = ch.title
            all_records.append(rec)

    # Re-number flat index
    for i, rec in enumerate(all_records):
        rec['index'] = i + 1

    return all_records
