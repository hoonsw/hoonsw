# Translator V2 Data Model

Updated: 2026-04-11

## 1. Deterministic Segmentation Rules

Segmentation must be stable across reruns for the same normalized source text.

### Input to segmentation

- Segmentation always runs on `normalized_source_text`.
- Hashes are computed from normalized text, not raw file bytes alone.
- If normalization rules change, the run is not resumable with old segment
  ledgers.
- Canonicalization runs before chapter detection and before segmentation.
- `canonicalizer_version` is persisted on the document ledger.

### Canonicalization spec

Phase-1 canonicalization is exact and versioned:

1. decode text as UTF-8 input from the ingest layer
2. normalize line endings to `\n`
3. apply Unicode `NFC`
4. replace tabs with a single ASCII space
5. strip trailing spaces on each line
6. collapse runs of 3 or more blank lines to exactly 2 blank lines
7. preserve all remaining punctuation, quotes, headings, page numbers, and
   foreign text literally

Phase 1 intentionally does not remove headers or footers heuristically.

### Chapter detection

- Chapter boundaries are detected first when headings are present.
- If no chapter structure is detected, the whole document is one chapter.
- Chapter detection is deterministic and never changes during repair.

### Segment boundaries

- Primary strategy: paragraph-first.
- Paragraph split rule: two or more consecutive newlines.
- Oversized paragraph fallback: split into sentence groups using language-aware
  punctuation.
- Oversized sentence fallback: split by length-preserving text windows at safe
  whitespace boundaries.
- Oversized paragraph threshold:
  - split when paragraph length exceeds `900` chars
  - or paragraph sentence count exceeds `6`
- Sentence-group target size:
  - aim for `350-900` chars
  - hard cap `1200` chars
- Language-aware sentence punctuation:
  - Japanese: `。！？`
  - English/Korean prose: `.?!`

### Stability rules

- Segmentation may not change during retries.
- A retry works on existing segment IDs only.
- Further splitting during repair creates child segment IDs that remain scoped
  under the original segment, for example `ch003.s017.a` and `ch003.s017.b`.
- Parent-child relationships are persisted in the ledger.
- Each segment persists `source_order_index`, `source_start_offset`, and
  `source_end_offset`.

### Size defaults

- `target_segment_chars_default`: 350 to 900 chars
- `hard_max_segment_chars`: 1200 chars
- These are config values, but changing them invalidates resumability for an
  existing run.

## 2. Ledger Schema

## DocumentRecord

```json
{
  "version": 1,
  "run_id": "20260411-...",
  "input_path": "...",
  "input_sha256": "...",
  "normalized_source_sha256": "...",
  "source_lang": "ja",
  "target_lang": "ko",
  "status": "INGESTED",
  "config_fingerprint": "...",
  "canonicalizer_version": "canon-v1",
  "segmenter_version": "segmenter-v1",
  "prompt_schema_version": "prompt-v1",
  "chapter_ids": ["ch001", "ch002"],
  "segment_count": 418,
  "accepted_segment_count": 0
}
```

## ChapterRecord

```json
{
  "chapter_id": "ch001",
  "title": "Chapter 1",
  "start_offset": 0,
  "end_offset": 18234,
  "source_order_index": 1,
  "segment_ids": ["ch001.s001", "ch001.s002"],
  "status": "SEGMENTED"
}
```

## SegmentRecord

```json
{
  "segment_id": "ch001.s014",
  "parent_segment_id": null,
  "child_segment_ids": [],
  "chapter_id": "ch001",
  "source_order_index": 14,
  "source_start_offset": 8134,
  "source_end_offset": 8761,
  "source_text": "...",
  "source_hash": "...",
  "separator_after": "\\n\\n",
  "status": "PENDING",
  "translated_text": "",
  "attempt_count": 0,
  "repair_count": 0,
  "validation_errors": [],
  "warnings": [],
  "provider": null,
  "model": null
}
```

## ValidationIssue

```json
{
  "issue_type": "source_residue",
  "severity": "critical",
  "segment_id": "ch001.s014",
  "span": "これは...",
  "expected": "fully translated Korean output",
  "observed": "Japanese passage remained",
  "action": "retry_segment"
}
```

## RepairAttempt

```json
{
  "segment_id": "ch001.s014",
  "attempt": 2,
  "strategy": "local_retry",
  "source_hash": "...",
  "result_status": "FAILED_VALIDATION",
  "issues": ["source_residue", "missing_number"]
}
```

## 3. Segment Status Enum

- `PENDING`
- `TRANSLATING`
- `DRAFTED`
- `FAILED_PARSE`
- `FAILED_VALIDATION`
- `SUPERSEDED_BY_CHILDREN`
- `ACCEPTED`
- `BLOCKED`

Only `ACCEPTED` segments are eligible for assembly.

## 4. Structured Output Schema

The generation layer requests exact segment wrappers.

For a single segment:

```text
[[SEGMENT ch001.s014]]
...translated text...
[[END SEGMENT ch001.s014]]
```

For a repair split:

```text
[[SEGMENT ch001.s014.a]]
...
[[END SEGMENT ch001.s014.a]]

[[SEGMENT ch001.s014.b]]
...
[[END SEGMENT ch001.s014.b]]
```

Rules:

- segment IDs must match exactly
- wrappers must be exact
- extra prose outside wrappers is a parse failure
- reordered or duplicate IDs are parse failures
- parse failure triggers local repair retry
- a multi-segment batch is either parse-valid as a whole or rejected as a whole

JSON is intentionally avoided for v1 because long multiline literary output is
more likely to break strict JSON formatting than stable wrapper markers.

## 5. Validation Definitions

### Coverage

Pass only if:

- every requested segment ID appears exactly once
- no unexpected segment ID appears
- no requested segment is missing

Severity:

- `critical`

### Emptiness

Pass only if normalized target text is non-empty.

Severity:

- `critical`

### Exact-copy

Pass only if normalized target text is not effectively identical to normalized
source text.

Heuristic:

- identical normalized string is a hard fail
- near-identical long string is also a hard fail

Severity:

- `critical`

### Source residue

Pass only if no critical source-language residue is detected.

Rules:

- Japanese source: hiragana/kana-based residue detection plus passage logic
- English source: suspicious long English passage detection with source overlap
- protected-span exceptions are applied before residue checks

### Protected-span precedence

Protected spans are evaluated in this order:

1. glossary entries with `mode=keep`
2. URLs, emails, file paths, ISBN/DOI-like identifiers
3. explicit project allowlist regexes
4. short foreign tokens of length `<= 3` are warnings only

Quoted titles, citations, proper nouns, scientific Latin names, and footnote
markers are not automatically protected unless matched by one of the rules
above.

Severity:

- `critical` for sentence/passage-scale residue
- `warning` for benign short leftovers

### Numbers

Pass only if required numeric tokens from the source are preserved, except for
configured exemptions.

Severity:

- `critical` for multi-digit omissions
- `warning` for ambiguous single-digit cases

### Basic format

V1 scope only:

- balanced major brackets/quotes
- no leftover wrapper markers in output
- no obvious meta commentary like "translation:" or "here is the translation"
- no broken child-parent separator merge after split repair

Severity:

- `critical` for wrapper or meta-text leakage
- `warning` for softer punctuation anomalies

### Suspiciously short output

This does not automatically block by itself, but it triggers repair unless
another validator already blocks the segment.

Severity:

- `repair-trigger`

## 6. Repair Policy

### Retry budget

- default max local retries: `2`
- default max split retries: `1`
- default max total repair attempts per original segment: `3`

### Strategy order

1. local retry with same segment ID
2. local retry with strengthened failure hints
3. split into child segments if still failing and source is oversized
4. if unresolved after budget, mark original segment `BLOCKED`

### Hard rules

- retries never mutate unrelated accepted segments
- segmentation for unrelated segments never changes during repair
- if a parent segment is split, all child segments must pass before the parent
  can be considered resolved
- if a batch is parse-invalid, none of its siblings are accepted from that batch
- if a batch is parse-valid, accepted siblings remain accepted while only failed
  siblings re-enter repair
- split-child separator rule:
  - intermediate children receive only repair-local join separators
  - the final child inherits the original parent `separator_after`
  - the parent `separator_after` is never duplicated across multiple children

## 7. Assembly Rules

- Assembly order follows source chapter order, then segment order.
- `separator_after` is preserved from the segmentation phase.
- Assembly never normalizes away paragraph boundaries beyond agreed cleanup.
- Assembly uses accepted child segments in place of a split parent segment.
- If any segment is not `ACCEPTED`, document assembly to deliverable output is
  forbidden.
- For split parents, internal child joins are local to the repair result, and
  only the final child emits the original parent trailing separator.

## 8. Resumability Contract

A run may resume only if all of the following match:

- `input_sha256`
- `normalized_source_sha256`
- `config_fingerprint`
- `canonicalizer_version`
- `segmenter_version`
- `prompt_schema_version`

If any of those change:

- old accepted segments remain historical artifacts
- they are not reused as accepted results for the new run
- the run starts with a fresh ledger

`input_path` is advisory metadata, not a hard resume key. Renaming or moving a
file must not invalidate resumability if the canonical content and version keys
still match.

## 9. Logging and Replayability

At minimum, each run must persist:

- document record
- chapter records
- segment records
- validation issues
- repair attempts
- final manifest
- blocked workfile manifest when deliverable assembly is refused

These artifacts must be sufficient to answer:

- which segment failed
- why it failed
- how many times it was retried
- which strategy resolved it
- whether the final document is reproducible

## 10. Regression Harness Contract

Phase 1 is not accepted without a frozen regression corpus.

The harness must include:

- synthetic corruption fixtures
  - missing wrapper
  - duplicate segment
  - empty segment
  - exact-copy segment
  - critical residue passage
  - missing multi-digit number
- at least one previously problematic Japanese book sample
- at least one previously problematic English book sample
- replay tests proving resumed runs preserve accepted segments only under
  matching ledger keys

Phase-1 design is considered valid only if these fixtures fail or pass exactly
as intended.
