# Translator V2 Design

This directory contains the reviewed design package for `translator_v2`.

Documents:

- [architecture.md](./architecture.md): system goals, pipeline shape, module boundaries, acceptance contract, and reuse/replace decisions
- [data-model.md](./data-model.md): deterministic segmentation rules, ledger schema, validation schema, repair policy, assembly rules, and resumability contract
- [phase-plan.md](./phase-plan.md): phased implementation plan, milestones, exit criteria, and rollout strategy
- [critical-review.md](./critical-review.md): adversarial review findings and how the final design resolves them
- [codex-local-architecture.md](./codex-local-architecture.md): Codex-operated translation mode that preserves the Discord/OpenClaw UX while moving generation out of direct API calls
- [codex-local-protocol.md](./codex-local-protocol.md): queue layout, task/result schema, worker boundaries, and failure semantics for Codex-local execution
- [codex-local-phase-plan.md](./codex-local-phase-plan.md): phased migration plan from API mode to Codex-local mode
- [codex-local-critical-review.md](./codex-local-critical-review.md): adversarial review findings and the changes applied to the Codex-local design
- [ocr-input-design.md](./ocr-input-design.md): ingest-only hardening plan for OCR/PDF-derived `.txt` inputs
- [ocr-input-critical-review.md](./ocr-input-critical-review.md): adversarial review findings and the accepted guardrails for OCR/PDF-ish TXT handling

Design intent:

- `translator_v2` is a new parallel package, not an in-place extension of the
  current `translate -> review -> retry` pipeline.
- The first version optimizes for provable coverage and local repair, not for
  feature parity with enterprise translation products.
- The design was revised after an adversarial review to add explicit contracts
  for canonicalization, versioning, validation precedence, batch semantics, and
  legacy coexistence.
- The Codex-local extension keeps the same outer operating model
  (`Discord/OpenClaw command -> progress -> completion notification -> files`)
  while moving translation generation to a local Codex worker queue.
