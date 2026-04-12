# Translator V2 Critical Review

Updated: 2026-04-11

This file records the adversarial review findings applied to the final design.

## Reviewer Findings

The critical reviewer raised five main issues:

1. resumability was not fully implementable because version keys and segment
   identity fields were missing
2. segmentation and canonicalization were too underspecified
3. validation exception precedence for protected spans was not explicit
4. batch parse and repair semantics were not closed
5. legacy/v2 artifact coexistence was not explicitly defined

## Resolutions Applied

### 1. Resumability and Version Keys

Added to the design:

- `canonicalizer_version`
- `segmenter_version`
- `prompt_schema_version`
- `source_order_index`
- `source_start_offset`
- `source_end_offset`
- child-parent lineage fields

Also changed:

- `input_path` is now advisory metadata, not a hard resume key

Result:

- a moved or renamed file can still resume if canonical content and version keys
  match

### 2. Canonicalization and Segmentation

Added an exact phase-1 canonicalization contract:

- newline normalization
- Unicode `NFC`
- tab replacement
- trailing-space stripping
- blank-line collapse
- no heuristic header/footer stripping in v1

Added deterministic segmentation thresholds:

- paragraph-first
- split oversized paragraphs above 900 chars or 6 sentences
- target segment range 350 to 900 chars
- hard cap 1200 chars

### 3. Validation Precedence

Added explicit protected-span precedence:

1. glossary `mode=keep`
2. URLs, emails, file paths, identifiers
3. explicit allowlist regexes
4. short foreign tokens become warnings only

Also clarified what is not implicitly protected:

- proper nouns
- quoted titles
- citations
- Latin names
- footnotes

These require explicit allowlisting to avoid hiding real untranslated spans.

### 4. Batch and Repair Semantics

Added exact rules:

- batch parse is all-or-nothing
- parse-valid batches may preserve accepted siblings
- parse-invalid batches preserve none of that batch
- split repairs create child IDs
- parent becomes `SUPERSEDED_BY_CHILDREN`
- assembly consumes children in source order

### 5. Legacy Migration Boundary

Added the artifact coexistence contract:

- legacy sidecars and v2 ledgers are version-distinct
- they are not interchangeable resume sources
- migration shims may emit both summaries, but only v2 ledgers resume v2

## Final Review Outcome

Reviewer verdict before revision:

- directionally sound
- not final until the above contracts were added

Final design rule after revision:

- validation remains a hard gate
- no best-effort deliverable
- no silent fallback to partial alignment
- no final assembly with unresolved segments
- no review-only safety claims without machine-actionable validation
