# Translator V3 Quality Fixtures Baseline

Updated: 2026-04-13

This document defines the curated milestone-2 fixture metrics and records the
current baseline on the active `translator_v3 + codex_local` path.

## Fixture Set

Location:

- `translator_v3/benchmarks/fixtures/quality/cases.json`
- `translator_v3/benchmarks/fixtures/quality/*.txt`

The fixture set is intentionally small and is derived from real blocked
`translator_v3` runs:

- `en_residue_repeat_listens`
- `ja_residue_valis_titles`
- `en_omission_flynt_surprised`
- `en_compression_truncated_predicate`

Each case stores:

- failure mode
- source language
- source snippet
- origin run
- origin unit id
- expected detection signals

## Metric Definitions

Machine-readable report:

- `translator_v3/benchmarks/fixtures/quality/baseline_results.active_v3.codex_local.json`

Per-case reports:

- `translator_v3/benchmarks/fixtures/quality/*active_v3.codex_local.json`

Definitions:

- `status`: final v3 run status for the fixture, `DELIVERABLE` or `BLOCKED`
- `blocked_case_count`: how many fixtures ended with at least one blocked unit
- `issue_types`: distinct validation issue types emitted by the active v3 path
- `expected_signals_detected`: whether the case’s configured issue signals were observed directly
- `residue_issue_count`: count of `source_residue` issues emitted by the live validator
- `adequacy_issue_count`: count of omission/compression-style issues
  `adequacy_review_failed`, `coverage_drop`, `severe_undertranslation`
- `posthoc_residue_hits`: residue hits from a post-run call to
  `quality_gate.detect_source_residue_in_chunk()` on the cleaned translated text
- `char_ratio`: visible translated characters divided by source characters, with
  segment wrappers removed first
- `low_char_ratio`: whether `char_ratio` fell below the fixture-specific
  threshold, used only for fixtures tagged `over_compression`
- `detected_case_count`: category-aware detection count used for baseline summary

Category-aware detection rules:

- `source_language_residue`: detected only if the run emits `source_residue` or
  the posthoc residue pass still finds residue
- `silent_omission`: detected only if adequacy/coverage issues are emitted
- `over_compression`: detected if adequacy/coverage issues are emitted or the
  fixture-specific ratio floor is breached

## Current Baseline

Run configuration:

- backend: `codex_local`
- model: `gpt-5.4`
- reasoning effort: `high`
- artifact root: `runs_v3/quality_fixtures`
- output root: `output/quality_fixtures`

Aggregate baseline from
`baseline_results.active_v3.codex_local.json`:

- cases: `4`
- blocked cases: `2`
- detected cases: `1`
- detection rate: `0.25`
- average char ratio: `0.7255`
- minimum char ratio: `0.5095`

By failure mode:

- `source_language_residue`: `0/2` detected, `1/2` blocked, average char ratio `0.8498`
- `silent_omission`: `0/1` detected, `0/1` blocked, average char ratio `0.6280`
- `over_compression`: `1/1` detected, `1/1` blocked, average char ratio `0.5742`

Per-case baseline:

| Case | Failure Mode | Status | Detected | Char Ratio | Notes |
| --- | --- | --- | --- | ---: | --- |
| `en_residue_repeat_listens` | residue | `BLOCKED` | no | `0.5095` | Current run blocked on adequacy, not residue; the model translated the album titles instead of leaving English residue. |
| `ja_residue_valis_titles` | residue | `DELIVERABLE` | no | `1.1902` | Current run translated the Japanese titles and passed both live and posthoc residue checks. |
| `en_omission_flynt_surprised` | omission | `DELIVERABLE` | no | `0.6280` | Current run preserved the “particularly surprised” meaning, so the earlier omission did not reproduce. |
| `en_compression_truncated_predicate` | compression | `BLOCKED` | yes | `0.5742` | Current run still collapsed into an incomplete Korean predicate and was blocked by adequacy review. |

## Interpretation

This baseline is useful, but it also shows that the current failure set is only
partially reproducible on demand.

What the baseline proves now:

- the fixture inputs are stable and runnable on the active v3 path
- the evaluation harness can capture blocked/deliverable status, issue types,
  residue hits, and compression ratios
- over-compression or truncation remains reproducible on the active path

What it does not prove yet:

- that residue and omission failures are deterministic enough to serve as hard
  regressions without stronger oracles
- that the current residue heuristics catch all title-heavy residue patterns
- that char-ratio thresholds alone are sufficient for compression detection

That instability is the main handoff into milestone 3.
