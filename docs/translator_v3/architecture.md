# Translator V3 Benchmark-Driven Architecture

Updated: 2026-04-12

## Summary

`translator_v3` should be designed as a document translation pipeline, not as a
better retry loop around raw text.

The current system is strongest when it receives already-clean prose. It becomes
slow and unstable when it is asked to recover document structure, OCR cleanup,
reading order, and translation quality inside one serial repair loop.

The benchmark conclusion is straightforward:

- use `OCRmyPDF` for scan cleanup and OCR normalization
- use `Marker` and `Docling` through an arbitration layer instead of hard
  coding one universal winner
- use pluggable translation engines behind a stable block-translation interface
- use a custom assembly, QC, progress, and delivery layer that fits this repo's
  `OpenClaw + Synology + Discord` operating model

This is a rebuild of the core translation path, not an incremental patch set on
top of `translator_v2`.

## Phase 1 Scope Freeze

`translator_v3` should launch with a narrow scope.

Included:

- book-length `PDF`
- book-length `TXT`, including OCR/PDF-derived text dumps
- one canonical translated text output
- one review bundle with aligned source/target artifacts

Explicitly deferred:

- `EPUB` and office-document containers
- layout-preserving PDF output
- bilingual PDF products
- image-only manga/comic pipelines
- multi-product export trees in the first rollout

This scope freeze is intentional. The redesign should remain a translation
system first.

## Why V2 Is Not Enough

The current `translator_v2` design fixed real problems, but it still centers
the wrong primitive.

Today the system still tends to do this:

`normalized text -> paragraph-ish segments -> translation -> repair -> split -> repair`

That is workable for good source text. It is not the right core for book-length
OCR/PDF jobs because:

- segmentation still begins from text that has already lost document structure
- validation fires on mechanical OCR artifacts and front-matter metadata
- repair becomes the dominant throughput cost
- progress reporting is derived from queue side effects rather than from an
  explicit document-state model
- users cannot reliably tell whether the job is translating the book, repairing
  OCR noise, or stalled in a local failure loop

`translator_v3` should replace the central primitive with:

`document graph -> block classes -> translation units -> class-aware QC -> assembly`

## Benchmark Findings

### OCRmyPDF

Useful patterns to copy:

- OCR cleanup as a separate front-end stage
- conservative switches such as rotate, deskew, background cleanup, redo-ocr,
  skip-text, and force-ocr
- strong operational behavior: progress visibility, cleanup on failure, and
  deterministic artifact production

Do not copy:

- making aggressive cleanup the default for every file
- treating OCR as the translation engine's problem

Primary role in `translator_v3`:

- preprocess scanned or poor-quality PDFs into clean, searchable intermediate
  documents

References:

- <https://ocrmypdf.readthedocs.io/en/latest/cookbook.html>
- <https://ocrmypdf.readthedocs.io/en/stable/advanced.html>

### Marker

Useful patterns to copy:

- primary focus on PDF/image document extraction, not just OCR text
- layout detection, reading-order recovery, header/footer cleanup, and
  structured outputs
- optional LLM repair at the extraction stage rather than in the final
  translation stage
- batch processing model that is aimed at large document collections

Do not copy:

- reimplementing its page understanding stack inside this repo
- locking the whole system to `Marker-first` without benchmark evidence

Primary role in `translator_v3`:

- one extraction candidate in an arbitration layer for long-form books and
  PDF-derived content

Reference:

- <https://github.com/datalab-to/marker>

### Docling

Useful patterns to copy:

- explicit document tree model
- confidence-aware parsing and batch conversion
- partial-success handling
- structured export formats that preserve the distinction between pages,
  blocks, tables, captions, and text flows

Do not copy:

- wrapping Docling only as a format converter while discarding its confidence
  signals
- treating Docling as “just a fallback parser”

Primary role in `translator_v3`:

- one extraction candidate, structured QC layer, and confidence-bearing
  benchmark peer to Marker

References:

- <https://github.com/docling-project/docling>
- <https://docling-project.github.io/docling/concepts/architecture/>
- <https://docling-project.github.io/docling/concepts/confidence_scores/>

### PDFMathTranslate

Useful patterns to copy:

- delivery modes that preserve layout or produce bilingual outputs
- explicit distinction between parsing, translation, and merge
- output forms aimed at real document consumption, not just plain text dumps

Do not copy:

- using it as the universal ingest layer for all books
- promoting layout-aware PDF output into phase 1

Primary role in `translator_v3`:

- later-phase export mode for layout-sensitive documents, especially if
  bilingual PDF output matters

Reference:

- <https://github.com/PDFMathTranslate/PDFMathTranslate>

### LibreTranslate / argos-translate-files / Bergamot

Useful patterns to copy:

- keeping translation engines modular
- separating document handling from MT backends
- allowing lighter-weight offline engines where high-end LLM output is not
  required

Do not copy:

- treating these projects as document understanding systems
- forcing high-cost LLM translation on every block where bulk MT is enough

Primary role in `translator_v3`:

- interchangeable translation backends behind one translation-unit contract

References:

- <https://docs.libretranslate.com/>
- <https://github.com/LibreTranslate/argos-translate-files>
- <https://firefox-source-docs.mozilla.org/toolkit/components/translations/resources/03_bergamot.html>

## Design Goals

- treat PDFs, OCR output, and clean text as different input classes
- preserve `OpenClaw/Discord` UX compatibility
- make progress observable from first principles instead of inferring it from
  queue side effects
- prevent OCR cleanup, translation, and delivery from becoming one opaque loop
- reduce repair to an exception path instead of the main throughput path
- support one readable text output and one review bundle in phase 1

## Non-Goals

- rebuilding Marker or Docling in-house
- forcing every input through the same extraction engine
- making partial success look like a finished book
- preserving every `translator_v2` internal interface
- promising layout-perfect PDF export in phase 1

## Topology

```text
OpenClaw / Discord
  -> job launcher
    -> translator_v3 orchestrator
      -> input classifier
      -> OCR/document cleanup stage
      -> extraction arbitration
      -> canonical document graph
      -> translation planner
      -> translation backend(s)
      -> class-aware QC + targeted repair
      -> preview + telemetry
      -> assembly + review bundle + delivery
```

## Input Classes

Every job should be classified before translation starts.

Input classes:

- `clean_text`
- `pdf_born_digital`
- `pdf_scanned`
- `unknown_noisy_text`

The mistake in the current system is not just bad OCR. It is that too many
inputs collapse into the same text-first route.

## Core Artifact: Canonical Document Graph

The primary internal representation should not be a giant normalized string.

It should be a canonical document graph with stable IDs at several levels:

- document
- chapter or section
- page
- block
- span
- translation unit

Minimum node metadata:

- `node_id`
- `parent_id`
- `node_type`
- `page_index`
- `source_text`
- `source_hash`
- `bbox` or layout metadata when available
- `confidence`
- `class_label`
- `reading_order`

This graph is the control plane.

Translation units are derived from it. They are not the source of truth.

## Stage Design

### Stage 0. Input Classifier

Input router chooses the primary path:

- clean text path
- born-digital PDF path
- scan/OCR path
- noisy text path

Classifier evidence should be stored in the run manifest.

### Stage 1. OCR / Cleanup

Recommended policy:

- `pdf_scanned`: `OCRmyPDF` first
- `pdf_born_digital`: extract existing text and compare against OCR fallback
- `unknown_noisy_text`: lightweight repair only, unless source can be traced
  back to PDF assets

Outputs:

- cleaned derivative artifact
- OCR metadata
- page-level quality hints

### Stage 2. Extraction Arbitration

Candidate engines:

- `Marker`
- `Docling`

Policy:

- choose an initial extractor by input class
- score each page or block with extractor confidence and structural sanity checks
- reroute only pages that violate thresholds or extraction invariants
- write exactly one authoritative graph node set per page
- preserve losing extractor artifacts only as debug attachments, not as live
  competing graph state

Required arbitration contract:

- one canonical graph writer
- one ownership decision per page
- deterministic tie-break rules
- cache keys for extractor outputs
- max rerun counts per page and per document

Outputs:

- document blocks with reading order
- detected headings, lists, captions, tables, footnotes, headers, and footers
- page-to-block mapping
- extraction confidence

### Stage 3. Translation Planning

Translation should be planned from semantic blocks, not arbitrary text slices.

Unit classes:

- `body_paragraph`
- `heading`
- `front_matter`
- `caption`
- `footnote`
- `table_cell`
- `bibliography_or_catalog`
- `index_like`

Planner responsibilities:

- merge or split blocks into cost-safe translation units
- preserve unit-to-block traceability
- attach class-aware validation policy
- generate preview-safe ordering metadata

### Stage 4. Translation Backends

Translation backend interface should be pluggable.

Supported backend families:

- `codex_local`
- `api_llm`
- `mt_engine`

Default routing policy:

- `mt_engine` is the default bulk path for clean or high-confidence prose
- `api_llm` or `codex_local` are escalation paths for low-confidence,
  high-value, or stylistically sensitive blocks
- backend choice must be recorded per translation unit

The translation interface should be narrow:

- input: ordered translation units plus context bundle
- output: translated units with exact unit IDs

It should not own:

- OCR cleanup
- validation decisions
- delivery packaging

### Stage 5. QC and Targeted Repair

Validation should become class-aware.

Examples:

- `missing_number` is severe for body text, lighter for catalog metadata
- `source_residue` is severe for prose, but may be tolerated for song titles,
  proper nouns, discography entries, or quoted matter
- readability checks should be strong for body text and softer for tables or
  indexes

Repair policy should change from recursive depth-first repair to:

- primary forward pass
- bounded repair queue
- class-aware repair budgets
- optional human-review queue for pathological units

This keeps translation moving forward instead of letting one bad block dominate
wall-clock time.

### Stage 6. Assembly

Phase 1 assembly products:

- plain translated text
- review bundle with source/target block alignment

The main product should be a readable translation, not just a proof that every
segment was touched.

### Stage 7. Telemetry and Preview

Telemetry must be first-class.

Required state:

- pages total / processed
- blocks total / translated / unresolved / blocked
- extraction confidence distribution
- reroute rate and reroute reasons
- OCR-repair rate
- adequacy / coverage review outcome
- repair queue depth
- last successful worker activity
- current backend worker count
- preview path

Progress should come from ledgered document state, not stdout scraping.

## Reuse vs Replace

### Reuse

Keep and adapt:

- `OpenClaw/Discord` entry and notification workflow
- `job_runner.py` as the outer orchestration shell
- Synology delivery and share-link layer
- glossary and packaging/report ideas where still useful
- `codex_local` worker idea as one translation backend

### Replace

Retire as the primary core:

- raw text as the only canonical source representation
- paragraph-first segmentation as the central planning primitive
- recursive repair as the default throughput mechanism
- stdout-based progress as the main observability layer

### Keep Only as Compatibility Layers

- `translator_v2` should remain runnable for existing scripts
- old outputs and ledgers must remain readable
- migration should be package-level, not file-format-destructive

## Recommended Baseline Stack

For book-length OCR/PDF jobs, the recommended phase-1 baseline is:

1. `OCRmyPDF` for scanned PDFs when applicable
2. extractor arbitration between `Marker` and `Docling`
3. `mt_engine` as the default bulk path when extraction confidence is high
4. `codex_local` or `api_llm` for escalation cases
5. class-aware QC and bounded repair
6. preview, report, delivery, and Synology sharing through this repo's own
   orchestration layer

That means the repo should stop trying to be its own OCR/document parser for the
hard cases.

## Risks

- external-tool integration raises environment complexity
- extraction outputs from Marker and Docling will not always agree
- document graph design is a one-time schema decision with migration cost
- output expectations must be reset: plain text jobs and layout-preserving jobs
  are different products

These risks are acceptable. The current alternative is to keep paying the same
complexity cost invisibly through slow repair churn and unreliable observability.

## Decision

Build `translator_v3` as a benchmark-driven document pipeline.

Do not continue growing `translator_v2` into an ad hoc document parser.

Use best-of-breed open-source tools for OCR and document understanding, and
restrict in-repo custom logic to the parts that are genuinely specific to this
workflow:

- translation-unit planning
- class-aware QC and repair policy
- preview/progress/report UX
- OpenClaw/Discord operations
- Synology delivery packaging

Avoid growing the phase-1 target into a general document platform. The first
ship target is a better long-book translator, not a universal ingest product.
