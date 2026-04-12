# Translator V2 Architecture

Updated: 2026-04-11

## Summary

`translator_v2` is a document translation pipeline for book-length `JA/EN -> KO`
translation. The core design goal is not "better prompt quality." It is
`provable coverage`: every source segment must be translated exactly once, or
the run must fail loudly with a machine-readable reason.

The final deliverable is never a partial assembly of "good" fragments. The
document can be marked final only when every segment has passed hard validation
and the full source order has been reconstructed.

## Goals

- Guarantee 1:1 source-to-target segment coverage.
- Block missing, duplicated, empty, copied, or residue-containing segments from
  entering the final document.
- Localize failures to specific segment IDs so repair is targeted and cheap.
- Preserve validated work across interruptions and reruns with deterministic
  hashing and segment ledgers.
- Support book-length input without relying on a single freeform model call.

## Non-goals for V1

- OCR/layout parity with enterprise document translation products
- RAG-heavy retrieval or broad translation memory systems
- Multimodel debate or agent councils during translation
- Human review as a primary control plane
- Whole-book or whole-chapter blind retranslation loops

## Core Principles

### 1. Segment Contract First

Translation happens over stable, addressable segments. The model is never asked
to "just translate this whole chunk" without a machine-checkable mapping.

### 2. Validation Is a Hard Gate

Validation is not a report at the end. It decides whether a segment is accepted,
repaired, or blocks the run.

### 3. Repair Is Local

Failures are repaired at segment scope first. Large-scale retries are a last
resort, not the default response.

### 4. Assembly Happens Only After Full Validation

The final document is reconstructed in source order only when every segment in
the ledger has cleared hard validation.

## High-level Pipeline

1. `INGESTED`
   - Source is loaded, normalized, and hashed.
2. `SEGMENTED`
   - Stable segment IDs and source hashes are created.
3. `TRANSLATING`
   - A bounded batch of segments is sent for structured generation.
4. `VALIDATING`
   - Coverage, residue, emptiness, exact-copy, numbers, and basic format checks
     run on every returned segment.
5. `TRANSLATING` loop
   - Only failed segments are retried or split further.
6. `ASSEMBLED`
   - All validated segments are merged in original order and with preserved
     structural separators.
7. `DELIVERABLE`
   - Final acceptance state.
8. `BLOCKED`
   - Terminal failure state for ledger mismatch, unresolved critical issues, or
     exhausted repair budget.

Repair is not a separate top-level stage. It is a controlled loop from
`VALIDATING` back to `TRANSLATING` for unresolved segment IDs.

## Canonicalization Boundary

Before segmentation, source text passes through a deterministic canonicalization
step. This step is versioned and becomes part of the resumability key.

Phase-1 rules:

- normalize newline style to `\n`
- apply Unicode `NFC`
- strip trailing whitespace on each line
- replace tabs with a single space
- collapse 3 or more consecutive blank lines to 2 blank lines
- do not rewrite quotes, punctuation, titles, or chapter labels
- do not heuristically strip headers or footers by default

Header/footer stripping is excluded from Phase 1 unless the ingest layer emits
explicit extractor metadata for removable page furniture.

## Module Boundaries

```text
translator_v2/
  cli.py
  orchestrate/
    pipeline.py
  core/
    models.py
    enums.py
    ledger.py
  ingest/
    loader.py
    normalize.py
  segment/
    chapter_detector.py
    segmenter.py
  translate/
    prompt_builder.py
    generator.py
    parser.py
  validate/
    coverage.py
    residue.py
    exact_copy.py
    numbers.py
    format.py
  repair/
    planner.py
    runner.py
  assemble/
    reconstructor.py
  report/
    manifest.py
    review_report.py
  adapters/
    providers.py
```

### Ownership

- `orchestrate` owns flow control and state transitions.
- `core` owns data types and ledger persistence rules.
- `ingest` owns deterministic normalization only.
- `segment` owns stable boundary generation and segment IDs.
- `translate` owns prompt construction, model calls, and strict parsing.
- `validate` owns hard acceptance rules.
- `repair` owns retry budgets, split policy, and escalation decisions.
- `assemble` owns exact reconstruction of the original order and separators.
- `report` owns human-readable and machine-readable artifacts.
- `adapters` wraps provider-specific integrations.

## Strict Acceptance Contract

The document may advance to `ASSEMBLED` only if every segment satisfies all of
the following:

- has a stable `segment_id`
- has a matching `source_hash`
- has exactly one parsed output mapped to that ID
- has non-empty translated text
- is not an exact copy of the source text
- has no critical source-language residue
- has no missing required numeric tokens
- has no unparseable wrapper or malformed structured output
- has ledger fields consistent with source order and lineage metadata

The document may advance to `DELIVERABLE` only if:

- all segments are accepted
- no segment remains in `FAILED_VALIDATION`
- no segment exhausted repair budget unresolved
- ledger hashes, counts, and ordering are consistent
- assembly cleanup rules were applied without violating preserved separator rules

Anything else is `BLOCKED`, not "best effort."

## How Final Assembly Works

Assembly does not mean "merge the successful parts and ignore the failures."
It means:

1. confirm that all segments are accepted
2. sort accepted segments by original source order
3. reinsert preserved separators and paragraph breaks
4. emit one complete book-shaped output

If even one segment is unresolved, no final output is emitted as deliverable.
At most, the system may persist a blocked workfile for debugging.

## Batch and Repair Semantics

Phase 1 allows bounded multi-segment generation calls, but semantics are strict.

- A generation batch is valid only if every requested segment ID appears exactly
  once, in exact wrappers, with no extra prose outside wrappers.
- If wrapper parsing fails at batch scope, the entire batch is rejected and all
  batch members return to retry planning.
- If wrapper parsing succeeds, segment validation is per segment.
- Accepted siblings from a valid batch may be preserved while failed siblings
  alone are repaired.
- Split repairs create child segment IDs under a single parent. The parent is
  marked `SUPERSEDED_BY_CHILDREN`, and assembly consumes accepted children in
  source order.

## Reuse vs Replace

### Reuse

- [providers.py](/home/hoonsw/dev/translator/providers.py:1)
  - Keep as the provider boundary with minimal adaptation.
- [input_pipeline.py](/home/hoonsw/dev/translator/input_pipeline.py:1)
  - Reuse ingestion and normalization ideas.
- [glossary_workflow.py](/home/hoonsw/dev/translator/glossary_workflow.py:1)
  - Keep glossary persistence and approval flow.
- [quality_gate.py](/home/hoonsw/dev/translator/quality_gate.py:1)
  - Reuse residue detection ideas inside the new validation layer.
- [report.py](/home/hoonsw/dev/translator/report.py:1)
  - Reuse report formatting concepts.
- [finalize.py](/home/hoonsw/dev/translator/finalize.py:1)
  - Reuse final packaging and cleanup ideas selectively.

### Replace

- [translate.py](/home/hoonsw/dev/translator/translate.py:1)
  - Keep only thin compatibility helpers if needed.
- [review.py](/home/hoonsw/dev/translator/review.py:1)
  - No longer the control plane.
- [retry_engine.py](/home/hoonsw/dev/translator/retry_engine.py:1)
  - Replace score-driven retries with issue-driven repair.
- [run_pipeline.py](/home/hoonsw/dev/translator/run_pipeline.py:1)
  - Replace orchestration with a state-machine pipeline.

## Compatibility Strategy

- Keep current entry points working temporarily.
- Introduce `translator_v2/cli.py` as the new implementation target.
- Existing top-level scripts may become shims that dispatch to either legacy or
  v2 paths during migration.

## Legacy Coexistence Contract

`translator_v2` artifacts must not pretend to be legacy artifacts.

- v2 stores a separate versioned ledger and manifest format.
- legacy `.chunks.json` and v2 ledgers are not interchangeable resume sources.
- legacy reports may coexist in the same run directory only if clearly marked by
  pipeline version.
- shims may read legacy inputs, but only v2 ledgers can resume v2 runs.
