from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class ChapterBoundary:
    index: int
    title: str
    text: str
    start_offset: int
    end_offset: int


_JA_CHAPTER_PATTERNS = [
    r"^第[一二三四五六七八九十百千\d]+[章節部篇回話幕]",
    r"^[一二三四五六七八九十]+\s",
    r"^\d+[\.．]\s",
    r"^[Ⅰ-Ⅹⅰ-ⅹ]+[\s\.．]",
    r"^CHAPTER\s+\d+",
    r"^PART\s+\d+",
]

_EN_CHAPTER_PATTERNS = [
    r"^CHAPTER\s+\d+",
    r"^Chapter\s+\d+",
    r"^PART\s+\d+",
    r"^Part\s+\d+",
    r"^BOOK\s+\d+",
    r"^ACT\s+\d+",
    r"^SECTION\s+\d+",
    r"^\d+\.\s+[A-Z]",
    r"^[IVX]+\.\s",
]


def detect_chapters(text: str, source_lang: str, min_chapter_size: int = 500) -> List[ChapterBoundary]:
    patterns = _JA_CHAPTER_PATTERNS if source_lang == "ja" else _EN_CHAPTER_PATTERNS
    combined = "|".join(f"({pattern})" for pattern in patterns)

    lines = text.split("\n")
    boundaries = []
    for line_index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and re.match(combined, stripped):
            offset = sum(len(item) + 1 for item in lines[:line_index])
            boundaries.append((line_index, stripped, offset))

    if not boundaries:
        return [ChapterBoundary(0, "(full document)", text, 0, len(text))]

    chapters: List[ChapterBoundary] = []
    for idx, (line_num, title, start_offset) in enumerate(boundaries):
        if idx + 1 < len(boundaries):
            next_line_num = boundaries[idx + 1][0]
            chapter_lines = lines[line_num:next_line_num]
            end_offset = boundaries[idx + 1][2]
        else:
            chapter_lines = lines[line_num:]
            end_offset = len(text)

        chapter_text = "\n".join(chapter_lines)
        if len(chapter_text.strip()) >= min_chapter_size:
            chapters.append(
                ChapterBoundary(len(chapters), title[:80], chapter_text, start_offset, end_offset)
            )
        elif chapters:
            chapters[-1].text += "\n" + chapter_text
            chapters[-1].end_offset = end_offset

    if boundaries and boundaries[0][0] > 0:
        preamble = "\n".join(lines[:boundaries[0][0]])
        if preamble.strip() and len(preamble.strip()) >= min_chapter_size:
            chapters.insert(0, ChapterBoundary(-1, "(preamble)", preamble, 0, boundaries[0][2]))
        elif preamble.strip() and chapters:
            chapters[0].text = preamble + "\n" + chapters[0].text
            chapters[0].start_offset = 0

    if not chapters:
        return [ChapterBoundary(0, "(full document)", text, 0, len(text))]

    for idx, chapter in enumerate(chapters):
        chapter.index = idx
    return chapters

