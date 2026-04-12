# Translator V3 Critical Review

Updated: 2026-04-12

This document records the adversarial review of the benchmark-driven redesign.

## Reviewer Stance

The review was intentionally skeptical.

Primary concerns raised before review:

- hidden complexity from stacking OCR, extraction, translation, QC, and export
- throughput risk from too many per-page or per-block passes
- migration risk from abandoning existing resumability and artifacts
- rebuilding Marker, Docling, OCRmyPDF, or PDFMathTranslate badly
- creating a large document-processing product with translation added as an
  afterthought
- over-weighting LLM translation where bulk MT would do better on throughput
- hard-coding Marker-first routing without benchmark evidence

## Findings

### 1. The redesign is justified, but only if the repo stops reimplementing document understanding

Accepted.

The architecture explicitly treats `OCRmyPDF`, `Marker`, and `Docling` as
external engines and rejects an in-house replacement effort.

### 2. The design must not hide complexity inside “smart repair”

Accepted.

The architecture makes repair a bounded secondary queue, not the main
translation path.

### 3. Progress must not depend on stdout heuristics

Accepted.

The architecture requires first-class telemetry from document state.

### 4. The redesign must still be a translation system

Accepted, with scope cuts.

The architecture centers the canonical document graph only as the control plane
that produces translation units, QC policy, preview, and final delivery.
Translation remains the main product.

The phase-1 scope is frozen to:

- PDF/TXT books only
- plain translated text
- one review bundle

Layout-preserving PDF, bilingual exports, and broad container ingestion are now
explicitly deferred.

### 5. Extractor choice must be benchmarked, not ideological

Accepted.

The architecture no longer fixes `Marker` as a universal first choice. It now
requires extractor arbitration, deterministic page ownership, reroute budgets,
and cache keys.

### 6. The benchmark plan must measure adequacy, not just readability

Accepted.

The benchmark suite requires omission/adequacy, terminology consistency,
alignment acceptance, reroute rate, OCR-repair rate, and a holdout slice.

### 7. The migration contract must be explicit, not aspirational

Accepted.

The phase plan requires versioned manifest/report/preview/telemetry schemas and
OpenClaw-facing compatibility fields before implementation starts.

## Result

The redesign is acceptable as a next-generation architecture because it is not
trying to rebuild every benchmarked project inside this repo.

The current design remains disciplined in scope:

- external tools own OCR and extraction
- this repo owns translation planning, QC policy, observability, and delivery
- migration is benchmark-gated instead of aspirational

Concrete rejection criteria for implementation review:

- reject any phase-1 plan that expands back to EPUB/layout-PDF/bilingual export
- reject any plan that lacks deterministic extractor arbitration
- reject benchmark harnesses that do not include adequacy/omission metrics
- reject progress designs that depend on stdout scraping
- reject backend plans that force high-cost LLM translation on every block

Remaining watchpoints for implementation:

- dependency and environment complexity
- adapter maintenance when upstream tools change
- choosing a stable document graph schema early enough
- preventing benchmark drift or cherry-picked success cases
