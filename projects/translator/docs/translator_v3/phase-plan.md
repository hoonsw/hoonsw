# Translator V3 Phase Plan

Updated: 2026-04-12

## Rollout Principle

`translator_v3` should be introduced as a parallel package.

Do not mutate `translator_v2` into `translator_v3` in place. The migration risk
is too high, and the benchmarked design changes too many core assumptions.

## Phase 0. Benchmark Harness

Goal:

- create a reproducible benchmark set before implementation work expands

Required benchmark slices:

- clean prose txt
- born-digital PDF
- scanned PDF
- OCR-noisy text copied from PDF
- long book with front matter, bibliography, and captions
- negative cases with tables / index-like pages / catalog pages
- mixed-language pages
- footnote-heavy pages
- broken reading-order samples
- caption-heavy pages

Per-run measurements:

- extraction time
- translation time
- pages per minute
- blocks per minute
- repair rate
- reroute rate
- OCR-repair rate
- blocked-unit rate
- preview lag
- adequacy / omission score
- terminology consistency score
- alignment acceptance score
- final readability review outcome

Exit criteria:

- at least one stable benchmark corpus checked into a repeatable harness
- baseline `translator_v2` metrics captured for comparison
- a hidden holdout slice is reserved for regression checks

## Phase 1. Document Ingest Spine

Goal:

- implement `translator_v3` package skeleton and document graph storage

Deliverables:

- input classifier
- run manifest v3
- canonical document graph schema
- artifact directories for extracted docs and page-level metadata
- telemetry schema independent of stdout scraping

Exit criteria:

- a book can be classified and represented as a document graph without any
  translation backend attached

## Phase 2. OCR / Extraction Integration

Goal:

- integrate external document tools instead of reimplementing them

Deliverables:

- `OCRmyPDF` adapter
- `Marker` adapter
- `Docling` adapter
- extractor arbitration policy with deterministic page ownership
- stored confidence and fallback traces

Exit criteria:

- the benchmark corpus can be converted into document graphs through the new
  adapters
- low-confidence pages can be rerouted without restarting the whole run
- reroute budgets and cache keys are enforced

## Phase 3. Translation Planning and Backends

Goal:

- translate semantic units derived from the document graph

Deliverables:

- translation-unit planner
- backend interface
- `mt_engine` backend adapter
- `codex_local` backend adapter
- `api_llm` backend adapter

Exit criteria:

- a document graph can be translated end-to-end without using recursive
  paragraph repair as the core control flow
- backend routing is recorded per translation unit

## Phase 4. Class-Aware QC and Repair

Goal:

- make repair bounded and class-aware

Deliverables:

- validation policy by unit class
- bounded repair queue
- review-needed queue for pathological units
- quality reports tied to block classes and confidence

Exit criteria:

- long-book runs show repair as an exception path rather than the dominant task
  volume

## Phase 5. Assembly and Delivery Products

Goal:

- produce final artifacts that match real usage

Deliverables:

- plain text output
- review bundle with aligned source/target blocks
- Synology-ready delivery bundle

Exit criteria:

- the same run can produce a readable text output and a review/debug bundle

Deferred to later phases:

- markdown/html export trees
- layout-preserving PDF export
- bilingual PDF packages

## Phase 6. OpenClaw Operations

Goal:

- preserve the existing user-facing operating model

Deliverables:

- progress derived from v3 telemetry
- live preview updates
- completion/failure notifications
- share-link delivery metadata

Exit criteria:

- Discord/OpenClaw users can start, inspect, and receive results without caring
  whether the backend is `v2` or `v3`

## Migration Rules

- keep `translator_v2` runnable until `v3` beats it on the benchmark suite
- do not silently switch default routing until benchmark gates pass
- preserve legacy artifact readers where possible
- prefer one-way migration: `v3` may read `v2` artifacts for comparison, but
  `v2` should not be modified to understand `v3` internals
- version `manifest`, `report`, `preview`, and telemetry schemas from day one
- define OpenClaw-facing compatibility fields before implementation starts

## Success Criteria

`translator_v3` is ready to become the preferred long-book pipeline when it
beats `translator_v2` on the benchmark set in all of the following:

- better readability on OCR/PDF-derived books
- better adequacy and lower omission rate
- lower repair-task ratio
- more trustworthy progress reporting
- faster wall-clock completion for long noisy books
- clearer delivery artifacts for review and debugging
