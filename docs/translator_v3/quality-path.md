# Translator V3 Quality Path

Updated: 2026-04-13

This document traces the active long-book translation quality path that runs in
production by default and points out exactly where `translator_v3` still reuses
`translator_v2` quality logic.

## Active Entry Path

Operational default:

```bash
python3 job_runner.py input.txt -o output.txt --v3-backend codex_local
```

The live call chain is:

`job_runner.py`
-> `translator_v3.cli`
-> `translator_v3/orchestrate/pipeline.py`
-> `translator_v3/ingest/spine.py`
-> `translator_v3/plan/planner.py`
-> `translator_v3/backends/base.py`
-> `translator_v3/validate/runner.py`
-> `translator_v3/assemble/reconstructor.py`

`job_runner.py` is the operational wrapper. By default, it builds:

```text
python -m translator_v3.cli run ... --backend <provider_api|codex_local>
```

That is the current supported execution path. Retired `run_pipeline.py` and
`web.py` shims, plus helper tools such as `review.py` and `retry_engine.py`,
are not directly on the active v3 path.

## Stage 1: Runtime Selection

Files:

- `job_runner.py`
- `translator_v3/cli.py`
- `translator_v3/core/models.py`

What happens:

- `job_runner.py` always selects `translator_v3.cli run`.
- `translator_v3/cli.py` resolves presets and builds `V3Config`.
- The effective defaults matter for quality behavior:
  - `backend_name`
  - `max_unit_chars=900`
  - `max_retry_per_unit=1`
  - `adequacy_review_mode="all_body_units"`
  - `adequacy_review_ratio_threshold=0.6`

These defaults control how aggressively v3 splits, retries, and rechecks output.

## Stage 2: Ingest And Document Graph

Files:

- `translator_v3/ingest/classifier.py`
- `translator_v3/ingest/spine.py`
- `translator_v3/extractors/arbitration.py`
- `translator_v3/extractors/execution.py`

What happens:

- Input is classified as `clean_text`, `pdf_born_digital`, `pdf_scanned`, or
  `unknown_noisy_text`.
- `build_document_graph()` chooses an extraction path, executes it if needed,
  and writes telemetry-oriented graph artifacts.
- The graph is normalized into document, page, chapter, and block nodes.
- Block nodes currently default to `unit_class="body_paragraph"` unless later
  specialized.

Quality implication:

- Any extraction loss or chapter/block segmentation error becomes upstream
  context loss for translation and cannot be recovered reliably by later retry.

## Stage 3: Translation Unit Planning

Files:

- `translator_v3/plan/planner.py`

What happens:

- Graph blocks are turned into `TranslationUnit`s.
- Body paragraphs are split by sentence first, then by hard character limits.
- `max_unit_chars` is adjusted modestly by preset boundary hints such as
  `scene` or `argument`.

Quality implication:

- Long paragraphs can be broken into units with no overlap and no continuity
  check across split boundaries.
- Hard splitting of oversized sentences creates the cleanest place for silent
  omission or semantic compression to hide.

## Stage 4: Translation Backend

Files:

- `translator_v3/backends/base.py`

What happens:

- `build_backend()` picks `echo`, `provider_api`, or `codex_local`.
- Each unit is translated through `translate_unit()`.
- For `provider_api` and `codex_local`, v3 does not own a distinct prompt stack
  yet. It adapts the v3 unit into a temporary v2 `SegmentRecord`.

Shared v2 logic still used here:

- `translator_v2/translate/prompt_builder.py`
- `translator_v2/codex_local/worker.py`
- `translator_v2/adapters/providers.py`
- `translator_v2/core/models.V2Config`

Concretely:

- v3 reuses `build_batch_system_prompt()` and `build_batch_user_prompt()`.
- v3 reuses the Codex exec wrapper and prompt envelope for `codex_local`.
- The active v3 prompt contract is therefore still a v2 segment-wrapper prompt,
  even though v3’s control plane is graph/unit based.

Quality implication:

- Prompt instructions still say “do not omit” and “do not leave source
  language,” but they are not yet expressed as a v3-specific completeness
  contract.
- Failure hints are appended on retry, but retries still go through the same
  reused prompt builder.

## Stage 5: Unit Validation

Files:

- `translator_v3/validate/runner.py`
- `readability_gate.py`

What happens:

- Every translated unit is validated before acceptance.
- v3 adds its own completeness checks such as:
  - `coverage_drop`
  - `severe_undertranslation`
  - `heading_number_mismatch`
- v3 also invokes shared validators for exact-copy, numbers, residue, and
  readability.

Shared v2 logic still used here:

- `translator_v2/validate/exact_copy.py`
- `translator_v2/validate/numbers.py`
- `translator_v2/validate/residue.py`

Shared legacy logic still used indirectly:

- `quality_gate.py`

Concretely:

- `translator_v2.validate.residue.validate_source_residue()` calls
  `quality_gate.detect_source_residue_in_chunk()`.
- That means active v3 residue detection still depends on the legacy residue
  heuristics in `quality_gate.py`.
- `quality_gate.evaluate_quality_gate()` is not the main v3 blocker, but its
  residue detection helper is still on the live path.

Quality implication:

- v3’s blocking logic is partly modernized and partly inherited.
- English residue detection remains narrower than Japanese residue detection
  because the underlying helper in `quality_gate.py` is language-specific and
  asymmetric.

## Stage 6: Adequacy Review And Retry

Files:

- `translator_v3/orchestrate/pipeline.py`
- `translator_v3/backends/base.py`

What happens:

- After structural validation passes, v3 may run `review_unit_adequacy()`.
- The adequacy reviewer compares source and translation and returns pass/fail.
- If validation or adequacy review fails, the unit is retried with a failure
  hint.
- Retry remains local to the unit. When attempts exceed
  `max_retry_per_unit`, the unit becomes `BLOCKED`.

Important distinction from legacy behavior:

- v3 does not call `translator_v2/repair/runner.py`.
- v3 currently has no split-repair tree, parent/child repair lineage, or
  strategy planner comparable to v2.

Quality implication:

- The active retry model is shallow and unit-local.
- Persistent residue or omission can survive if the first retry does not change
  model behavior enough.

## Stage 7: Assembly And Artifacts

Files:

- `translator_v3/assemble/reconstructor.py`
- `translator_v3/core/io.py`

What happens:

- Accepted and blocked units are assembled into preview and final outputs.
- Review artifacts are written to `translation_units.json`,
  `translated.preview.txt`, `review_bundle.json`, and the graph/telemetry files.
- If every unit is accepted, the final requested output path is written.
- If any unit is blocked, v3 writes `translated.blocked.txt` inside the run
  artifact directory.

Quality implication:

- The final quality status is effectively unit acceptance across the document,
  not a separate post-hoc global quality gate.
- The review bundle is the main artifact for diagnosing where residue,
  omission, or compression escaped validation.

## What Is On The Active Path vs Not

On the active v3 path:

- `job_runner.py`
- `translator_v3/cli.py`
- `translator_v3/ingest/*`
- `translator_v3/plan/planner.py`
- `translator_v3/backends/base.py`
- `translator_v3/validate/runner.py`
- `readability_gate.py`
- `translator_v2/translate/prompt_builder.py`
- `translator_v2/validate/exact_copy.py`
- `translator_v2/validate/numbers.py`
- `translator_v2/validate/residue.py`
- `quality_gate.detect_source_residue_in_chunk()`

Not directly on the active v3 path:

- `review.py`
- `retry_engine.py`
- `run_pipeline.py` legacy path
- `quality_gate.evaluate_quality_gate()`
- `translator_v2/repair/runner.py`

These remain useful as references, fallback tooling, or legacy compatibility,
but they are not the main runtime path for the default long-book workflow.
