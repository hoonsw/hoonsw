# Translator V2 Phase Plan

Updated: 2026-04-11

## Strategy

Build `translator_v2` as a new package beside the legacy system.
Do not refactor the old pipeline in place.

## Phase 0: Skeleton and Compatibility

Goal:

- create the package structure and route a minimal CLI through it

Deliverables:

- `translator_v2/` package skeleton
- compatibility shim from legacy CLI to v2 entry point
- base enums, record types, and ledger persistence helpers
- artifact version contract for legacy outputs vs v2 ledgers

Exit criteria:

- a dry run can ingest a source file, segment it, and persist an empty ledger
- artifact versions are explicit and non-interchangeable

## Phase 1: Coverage Backbone

Goal:

- prove source-to-target coverage at segment level

Scope:

- deterministic ingestion
- deterministic segmentation
- structured per-segment generation
- hard validation
- local repair loop
- resumability by hash and config fingerprint
- minimal report/manifest
- frozen regression corpus and corruption-fixture harness

Explicit exclusions:

- OCR/layout parity
- RAG / translation memory / style memory / entity memory
- chapter parallelism
- multimodel routing
- human review gating
- sentence-count primary gating

Exit criteria:

- sample book runs produce no deliverable when any segment is missing
- source residue is caught before assembly
- exact-copy output is caught before assembly
- local segment repair works without whole-chapter reruns
- renamed input files can resume if canonical content and version keys match
- malformed batch wrappers never leak partial invalid assemblies

## Phase 2: Stability and Consistency

Goal:

- make accepted output more robust without changing the core ledger model

Scope:

- chapter-level consistency diagnostics
- better residue exception handling for titles and proper nouns
- stronger blocked-run artifacts for debugging clustered failures
- controlled cleanup rules for final assembly

Exit criteria:

- repaired segments do not regress neighboring separators or ordering
- blocked runs expose exact unresolved segment IDs and issue history

## Phase 3: Narrow Memory

Goal:

- improve quality without weakening the coverage contract

Scope:

- glossary retrieval
- validated prior-translation reuse
- narrow chapter memory
- explicit allowlists for kept terms

Rules:

- retrieval must only reference validated records
- retrieval cannot bypass validation

Exit criteria:

- quality improves on repeated terminology without introducing mapping drift

## Phase 4: Scale

Goal:

- improve throughput once correctness is stable

Scope:

- chapter parallelism
- bounded concurrency
- improved progress reporting

Exit criteria:

- parallel runs preserve identical ledger semantics
- acceptance contract remains unchanged under concurrency

## Phase 5: Optional Extensions

Scope:

- richer reports
- human post-edit hooks
- OCR and layout reconstruction
- optional multimodel escalation

These are intentionally later because they do not solve the primary omission and
residue failure mode.

## Rollout Recommendation

1. Keep legacy translator for current production use during development.
2. Implement v2 on a controlled sample set first.
3. Compare legacy vs v2 on previously problematic books.
4. Promote v2 only after Phase 1 and Phase 2 exit criteria are met.

## Legacy Artifact Contract

Legacy and v2 artifacts must coexist without ambiguity.

- legacy sidecars keep their existing names and semantics
- v2 ledgers are stored under a distinct versioned manifest path
- shims may emit both legacy-facing summaries and v2-native ledgers during
  migration, but resume always uses the v2 ledger only
- reports must state which pipeline version produced them

## Success Metrics

- zero silent missing segments in accepted runs
- zero critical source-residue segments in accepted runs
- zero exact-copy segments in accepted runs
- resumed runs reuse accepted work only when ledger inputs match exactly
- blocked runs always point to exact unresolved segment IDs
- regression fixtures stay green across segmenter and prompt changes unless the
  version key is explicitly bumped

