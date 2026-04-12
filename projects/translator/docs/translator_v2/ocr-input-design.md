# OCR/PDF-ish TXT Ingest Design

## Problem

Some `.txt` inputs are not clean manuscript text. They are OCR output or text copied from PDFs, with page breaks, hard-wrapped lines, line-end hyphenation, and repeated layout artifacts. Today [`input_pipeline.py`](/home/hoonsw/dev/translator/input_pipeline.py) treats every `.txt` as `clean_txt` and routes it through [`TextNormalizer.normalize_txt()`](/home/hoonsw/dev/translator/normalizer.py:25). That freezes a broken layout into [`LoadedDocument.normalized_source_text`](/home/hoonsw/dev/translator/translator_v2/ingest/loader.py:27), after which segmentation and validation operate on the wrong structure.

## Goals

- Detect `pdf-ish` / `ocr-ish` `.txt` inputs conservatively.
- Repair layout artifacts before canonicalization and segmentation.
- Keep the decision observable in ledger metadata and resume fingerprints.
- Avoid regressions on genuinely clean prose `.txt`.

## Non-Goals

- No full document-layout parser.
- No automatic chapter reconstruction beyond existing normalization.
- No automatic source-language override in this phase.
- No readability/fluency gate in this phase.

## Design

### 1. Conservative TXT profile detection in ingest

For `.txt` inputs, add a raw-text analyzer in [`input_pipeline.py`](/home/hoonsw/dev/translator/input_pipeline.py) that computes:

- `form_feed_count`
- `soft_hyphen_count`
- `hyphenated_linebreak_count`
- `short_line_ratio`
- `avg_non_empty_line_length`
- `repeated_short_line_occurrences`
- `symbol_noise_line_count`
- `replacement_char_count`
- `latin_char_count`
- `cjk_char_count`

The analyzer derives two coarse signal scores:

- `pdfish_score`
- `ocrish_score`

The detector stays conservative:

- clean text stays `clean_txt` unless multiple strong layout-artifact signals are present
- OCR cleanup is chosen only when OCR-specific signals materially exceed PDF-like signals

### 2. Candidate-based cleanup selection

When strong layout-artifact signals are present, ingest evaluates three candidates:

- `normalize_txt()`
- `normalize_pdf_text()`
- `normalize_ocr()`

Each candidate is re-analyzed. Selection uses:

- reduced layout-artifact score
- no major drop in general text quality
- no excessive content loss

This is meant to prevent destructive cleanup from being applied to normal prose.

### 3. Input states and observability

Add explicit TXT-derived repaired states:

- `txt_pdf_normalized`
- `txt_ocr_normalized`

Persist the chosen ingest state and a compact analysis payload into:

- [`LoadedDocument`](/home/hoonsw/dev/translator/translator_v2/core/models.py)
- [`DocumentRecord`](/home/hoonsw/dev/translator/translator_v2/core/models.py)

This makes the cleanup choice visible in the ledger and report path.

### 4. Resume/versioning

Add `ingest_version` to [`V2Config`](/home/hoonsw/dev/translator/translator_v2/core/models.py) and to [`config_fingerprint()`](/home/hoonsw/dev/translator/translator_v2/ingest/normalize.py:33).

That ensures resume invalidates when TXT classification / cleanup policy changes.

### 5. Legacy parity

[`translate.py`](/home/hoonsw/dev/translator/translate.py) currently routes only `.pdf` through `process_input()`. Change it so `.txt` also goes through the same ingest path, keeping legacy behavior aligned with v2.

## Safety Rules

- Do not auto-run aggressive OCR cleanup on normal `.txt` without strong signals.
- Do not modify `canonicalize_text()`; repair belongs before canonicalization.
- Do not collapse meaningful single newlines unless the chosen cleanup path already justifies it.
- Keep raw text available through `InputResult.raw_text` when cleanup changes content.

## Test Plan

Add focused tests for:

- clean prose `.txt` stays `clean_txt`
- PDF-extracted `.txt` chooses `txt_pdf_normalized`
- OCR-noisy `.txt` chooses `txt_ocr_normalized`
- v2 dry-run ledger records `ingest_state`
- `ingest_version` changes config fingerprint
- legacy `translate.py` now uses `process_input()` for `.txt`

## Deferred Work

- input-language auto-detection / override
- OCR-aware readability gate
- specialized heading/list/verse preservation metadata
- stronger English residue blocking when alignment artifacts are missing
