# Translator V3 Design

This directory contains the benchmark-driven redesign package for
`translator_v3`.

Documents:

- [architecture.md](./architecture.md): benchmark findings, target
  architecture, subsystem boundaries, and reuse/replace decisions
- [phase-plan.md](./phase-plan.md): phased rollout plan, benchmark harness, and
  migration guardrails
- [critical-review.md](./critical-review.md): adversarial review findings and
  the changes accepted after critique
- [quality-path.md](./quality-path.md): active v3 runtime quality path, with
  exact notes on shared v2 prompt and residue logic still on the live path
- [quality-fixtures-baseline.md](./quality-fixtures-baseline.md): curated
  milestone-2 fixture metrics and the current active-path baseline

Working entrypoints:

- Inspect/build a document graph:
  `python3 -m translator_v3.cli inspect <input.pdf|input.txt> --source-lang en`
- Run end-to-end with the Codex backend:
  `python3 -m translator_v3.cli run <input> -o <output> --backend codex_local --model gpt-5.4 --reasoning-effort high`
- Run through the generic operational wrapper:
  `./scripts/run_job_runner_codex.sh <input> [output]`
- The explicit v3 wrapper also remains available:
  `./scripts/run_job_runner_v3_codex.sh <input> [output]`
- Presets are supported on the v3 operational path as well:
  `./scripts/run_job_runner_codex.sh <input.pdf|input.txt> [output.txt] --preset en_nonfiction`

Design intent:

- `translator_v3` is not a thin continuation of the current
  `txt -> segment -> translate -> repair` core.
- The new baseline is a document translation system, not a text translation
  script with added OCR heuristics.
- Phase 1 scope is intentionally narrow:
  - book-length `PDF` and `TXT` only
  - one canonical translated text output
  - one review bundle
  - no layout-aware PDF, bilingual export, or EPUB/container ingestion yet
- The redesign is benchmarked against established open-source stacks:
  `OCRmyPDF`, `Marker`, `Docling`, `PDFMathTranslate`, and translation-engine
  backends such as `LibreTranslate` / `argos-translate-files` / `Bergamot`.
- The outer operating model remains compatible with the current
  `OpenClaw/Discord -> run -> progress -> delivery` workflow, but the internal
  control plane is rebuilt around document understanding, extractor arbitration,
  block-level routing, and observable progress.

Current implementation caveat:

- The current `benchmark` slice validates input classification,
  document-graph ingest, and external extractor execution when a supported
  local tool is available.
- It does not yet prove extractor quality superiority across benchmark corpora,
  nor end-to-end translation quality.
- The practical default wrapper now points only at `translator_v3`.
- Legacy top-level CLIs were retired from the supported surface.
- `translate.py` remains in-repo as a shared helper module for review/report/
  finalize code, not as a supported translation entrypoint.
- The current runtime-enabled external extractor is `docling`; `marker` and
  `ocrmypdf` are still partial / future-facing integration targets.
- The operational v3 path is practical-default, but parts of the translation
  prompt/backend layer still reuse proven v2 building blocks rather than a
  fully independent backend stack.
