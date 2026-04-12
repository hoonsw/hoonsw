#!/usr/bin/env python3
"""
Text normalization and OCR cleanup utilities.

Provides language-aware cleanup for:
- OCR output (aggressive cleanup for Japanese, lighter for English)
- PDF text extraction artifacts
- General TXT normalization

Design: stateless utilities grouped by cleanup phase.
"""

import re
import unicodedata


class TextNormalizer:
    """Language-aware text normalizer for translation pipeline input."""

    def __init__(self, source_lang: str = "ja"):
        self.source_lang = source_lang

    # ── Public entry points ──────────────────────────────────────

    def normalize_txt(self, text: str) -> str:
        """Light normalization for clean TXT input.
        Preserves intentional formatting while fixing encoding issues.
        """
        text = self._fix_encoding_artifacts(text)
        text = self._normalize_whitespace_light(text)
        return text.strip()

    def normalize_pdf_text(self, text: str) -> str:
        """Normalize text extracted from PDF text layer.
        Handles common PDF extraction artifacts.
        """
        text = self._fix_encoding_artifacts(text)
        text = self._remove_page_numbers(text)
        text = self._remove_headers_footers(text)
        text = self._fix_pdf_line_breaks(text)
        text = self._normalize_whitespace_medium(text)
        return text.strip()

    def normalize_ocr(self, text: str) -> str:
        """Aggressive normalization for OCR output.
        Language-specific cleanup rules applied.
        """
        text = self._fix_encoding_artifacts(text)
        text = self._remove_page_numbers(text)
        text = self._remove_headers_footers(text)

        if self.source_lang == "ja":
            text = self._cleanup_japanese_ocr(text)
        elif self.source_lang == "en":
            text = self._cleanup_english_ocr(text)

        text = self._merge_broken_lines(text)
        text = self._remove_ocr_noise(text)
        text = self._normalize_whitespace_medium(text)
        return text.strip()

    # ── Shared cleanup utilities ─────────────────────────────────

    def _fix_encoding_artifacts(self, text: str) -> str:
        """Fix common encoding/Unicode issues."""
        # Normalize Unicode forms (NFC is standard for CJK)
        text = unicodedata.normalize('NFC', text)
        # Replace common mojibake patterns
        text = text.replace('\ufffd', '')  # Unicode replacement character
        text = text.replace('\x00', '')    # Null bytes
        text = text.replace('\u00ad', '')  # soft hyphen
        text = text.replace('\f', '\n\n')  # page break marker
        # Fix common UTF-8 double-encoding artifacts
        text = text.replace('â€"', '—')   # em-dash
        text = text.replace('â€"', '–')   # en-dash
        text = text.replace('â€™', "'")   # right single quote
        text = text.replace('â€œ', '"')   # left double quote
        text = text.replace('â€\x9d', '"')  # right double quote
        return text

    def _remove_page_numbers(self, text: str) -> str:
        """Remove standalone page numbers."""
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Standalone number (1-4 digits), possibly with surrounding whitespace
            if re.match(r'^[一二三四五六七八九十百千〇\d]{1,4}$', stripped):
                continue
            # "Page N" or "p. N" patterns
            if re.match(r'^(page|p\.?)\s*\d+$', stripped, re.IGNORECASE):
                continue
            # "- N -" style
            if re.match(r'^[-–—]\s*\d+\s*[-–—]$', stripped):
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    def _remove_headers_footers(self, text: str) -> str:
        """Remove repeating headers/footers.

        Detects lines that appear frequently at page boundaries
        (near page number patterns) and removes them.
        """
        lines = text.split('\n')
        if len(lines) < 20:
            return text  # Too short to have meaningful repetition

        # Count line occurrences (stripped, non-empty, short lines likely headers)
        line_counts: dict[str, int] = {}
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) < 80:
                line_counts[stripped] = line_counts.get(stripped, 0) + 1

        repeated = set()
        for line, count in line_counts.items():
            if count >= 5 and self._looks_like_running_header_footer(line):
                repeated.add(line)
                continue
            if self.source_lang == "ja" and count >= 8 and self._looks_like_short_cjk_header_footer(line):
                repeated.add(line)

        if not repeated:
            return text

        cleaned = [line for line in lines if line.strip() not in repeated]
        return '\n'.join(cleaned)

    def _looks_like_running_header_footer(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped or len(stripped) > 60:
            return False
        if re.search(r'[.!?]["”\']?$', stripped):
            return False
        if re.search(r'[,;:]', stripped):
            return False
        if re.match(r'^(page|p\.?)\s*\d+$', stripped, re.IGNORECASE):
            return True
        if re.match(r'^[IVXLCDM\d\s\-–—]+$', stripped):
            return True

        words = re.findall(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff']+", stripped)
        if not words or len(words) > 8:
            return False

        alpha_chars = [char for char in stripped if char.isalpha()]
        upper_ratio = (
            sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
            if alpha_chars
            else 0.0
        )
        titlecase_ratio = sum(1 for word in words if word[:1].isupper()) / len(words)
        return upper_ratio >= 0.6 or titlecase_ratio >= 0.8

    def _looks_like_short_cjk_header_footer(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped or len(stripped) > 18:
            return False
        if re.search(r'[。！？、]', stripped):
            return False
        if not re.search(r'[\u3040-\u30ff\u4e00-\u9fff]', stripped):
            return False
        return True

    def _fix_pdf_line_breaks(self, text: str) -> str:
        """Fix PDF-style forced line breaks within paragraphs."""
        if self.source_lang == "ja":
            return self._fix_pdf_line_breaks_ja(text)
        return self._fix_pdf_line_breaks_en(text)

    def _fix_pdf_line_breaks_ja(self, text: str) -> str:
        """Fix line breaks in Japanese PDF text.

        Japanese text doesn't use spaces between words, so lines ending
        mid-sentence (no sentence-ending punctuation) should be joined.
        """
        lines = text.split('\n')
        merged = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                merged.append('')
                i += 1
                continue

            # If line doesn't end with sentence-ending punctuation
            # and next line is non-empty, join them
            while (i + 1 < len(lines)
                   and lines[i + 1].strip()
                   and stripped
                   and not re.search(r'[。！？」』\n]$', stripped)):
                i += 1
                stripped += lines[i].strip()

            merged.append(stripped)
            i += 1

        return '\n'.join(merged)

    def _fix_pdf_line_breaks_en(self, text: str) -> str:
        """Fix line breaks in English PDF text.

        Join lines that end without sentence-ending punctuation,
        handling hyphenated words across lines.
        """
        lines = text.split('\n')
        merged = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()

            if not stripped.strip():
                merged.append('')
                i += 1
                continue

            # Handle hyphenated line break
            while (i + 1 < len(lines)
                   and lines[i + 1].strip()
                   and stripped):
                next_stripped = lines[i + 1].strip()
                if stripped.endswith('-'):
                    # Join hyphenated word
                    stripped = stripped[:-1] + next_stripped
                elif stripped.endswith(('—', '–')):
                    break
                elif not re.search(r'[.!?:;"\')]$', stripped):
                    # Join non-sentence-ending lines
                    stripped = stripped + ' ' + next_stripped
                else:
                    break
                i += 1

            merged.append(stripped)
            i += 1

        return '\n'.join(merged)

    def _normalize_whitespace_light(self, text: str) -> str:
        """Light whitespace normalization (for clean TXT)."""
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Remove trailing whitespace per line
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Collapse 3+ consecutive blank lines to 2
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        return text

    def _normalize_whitespace_medium(self, text: str) -> str:
        """Medium whitespace normalization (for PDF/OCR)."""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Remove trailing whitespace
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Collapse 3+ blank lines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove leading/trailing blank lines within paragraphs
        text = re.sub(r'\n[ \t]+', '\n', text)
        return text

    # ── Japanese-specific OCR cleanup ────────────────────────────

    def _cleanup_japanese_ocr(self, text: str) -> str:
        """Aggressive cleanup for Japanese OCR output."""
        # Fix common Japanese OCR misrecognitions
        text = self._fix_japanese_punctuation(text)
        text = self._fix_japanese_brackets(text)
        text = self._remove_japanese_ocr_noise(text)
        text = self._fix_katakana_errors(text)
        return text

    def _fix_japanese_punctuation(self, text: str) -> str:
        """Fix OCR-garbled Japanese punctuation."""
        # Half-width to full-width punctuation
        replacements = {
            ',': '、',  # comma
            '.': '。',  # period (only when preceded by Japanese char)
            '!': '！',
            '?': '？',
            ':': '：',
            ';': '；',
            '~': '〜',
        }
        for half, full in replacements.items():
            # Only replace when surrounded by CJK characters
            text = re.sub(
                rf'(?<=[\u3000-\u9fff\uff00-\uffef]){re.escape(half)}(?=[\u3000-\u9fff\uff00-\uffef\s])',
                full, text
            )
        return text

    def _fix_japanese_brackets(self, text: str) -> str:
        """Fix bracket/quotation issues in Japanese OCR."""
        # Fix common OCR bracket substitutions
        text = text.replace('『', '「').replace('』', '」')  # Keep if intentional
        # Fix misrecognized brackets
        text = re.sub(r'(?<=[\u3000-\u9fff])「', '「', text)
        text = re.sub(r'」(?=[\u3000-\u9fff])', '」', text)
        return text

    def _remove_japanese_ocr_noise(self, text: str) -> str:
        """Remove common Japanese OCR noise characters."""
        # Remove isolated single characters that are likely noise
        # (standalone half-width alphanumeric between Japanese text)
        text = re.sub(
            r'(?<=[\u3000-\u9fff])\s*[a-zA-Z0-9]\s*(?=[\u3000-\u9fff])',
            '', text
        )
        # Remove excessive spaces between Japanese characters
        text = re.sub(r'(?<=[\u3000-\u9fff])\s+(?=[\u3000-\u9fff])', '', text)
        return text

    def _fix_katakana_errors(self, text: str) -> str:
        """Fix common katakana OCR misrecognitions."""
        # Common katakana OCR errors
        replacements = {
            'ヴ': 'ヴ',  # normalize
            '力タ': 'カタ',  # kanji "力" misread as katakana "カ"
            'ロ語': 'ロゴ',  # common misread (context-dependent, conservative)
        }
        for wrong, right in replacements.items():
            text = text.replace(wrong, right)
        return text

    # ── English-specific OCR cleanup ─────────────────────────────

    def _cleanup_english_ocr(self, text: str) -> str:
        """Lighter cleanup for English OCR output."""
        text = self._fix_english_ocr_common(text)
        text = self._fix_ligature_artifacts(text)
        return text

    def _fix_english_ocr_common(self, text: str) -> str:
        """Fix common English OCR errors."""
        # Fix common character confusions
        # l/1/I confusion is too context-dependent, skip
        # Fix double spaces
        text = re.sub(r'  +', ' ', text)
        # Fix space before punctuation
        text = re.sub(r'\s+([.,;:!?])', r'\1', text)
        # Fix missing space after punctuation
        text = re.sub(r'([.,;:!?])([A-Z])', r'\1 \2', text)
        return text

    def _fix_ligature_artifacts(self, text: str) -> str:
        """Fix Unicode ligature characters from OCR."""
        ligatures = {
            'ﬁ': 'fi',
            'ﬂ': 'fl',
            'ﬀ': 'ff',
            'ﬃ': 'ffi',
            'ﬄ': 'ffl',
            'ﬅ': 'ft',  # long s + t
            'ﬆ': 'st',
        }
        for lig, repl in ligatures.items():
            text = text.replace(lig, repl)
        return text

    # ── Shared OCR utilities ─────────────────────────────────────

    def _merge_broken_lines(self, text: str) -> str:
        """Merge lines that were broken by OCR/PDF extraction."""
        if self.source_lang == "ja":
            return self._fix_pdf_line_breaks_ja(text)
        return self._fix_pdf_line_breaks_en(text)

    def _remove_ocr_noise(self, text: str) -> str:
        """Remove common OCR noise patterns."""
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Skip lines that are just noise characters
            if stripped and len(stripped) <= 3 and not any(c.isalnum() for c in stripped):
                continue
            # Skip lines that are just dots/dashes
            if re.match(r'^[.\-–—_=•·\s]+$', stripped):
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)
