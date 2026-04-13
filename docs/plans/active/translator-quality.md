# Translator Quality Plan

## Goal

Explain why long-book translation still shows source-language residue, silent omission, and over-compression on the active path, then define a bounded investigation plan before changing logic.

## Status Update

- Milestone 1 is complete.
- Milestone 2 is complete.
- Milestone 3 is complete.
- Added `docs/translator_v3/quality-path.md` to trace the live v3 quality path
  end to end.
- Added a link to that trace document from `docs/translator_v3/README.md`.
- Added a curated fixture set under
  `translator_v3/benchmarks/fixtures/quality/`.
- Added the evaluation harness
  `translator_v3/benchmarks/quality_eval.py`.
- Added baseline documentation in
  `docs/translator_v3/quality-fixtures-baseline.md`.
- Recorded current baseline results in
  `translator_v3/benchmarks/fixtures/quality/baseline_results.active_v3.codex_local.json`
  and per-case `*.active_v3.codex_local.json` reports.
- Added ranked milestone-3 fix options in
  `docs/translator_v3/quality-fix-options.md`.
- No translation logic changed in milestone 2.
- No translation logic changed in milestone 3.
- Implemented the first milestone-4 fix only:
  a v3-only completeness addendum in `translator_v3/backends/base.py`.
- Implemented the next smallest milestone-4 fix only:
  a targeted first-retry compression repair hint in
  `translator_v3/orchestrate/pipeline.py`.
- Implemented the next smallest milestone-4 fix after that:
  a bounded extra post-repair retry for compression-like body failures in
  `translator_v3/orchestrate/pipeline.py`.
- Added focused regression coverage for that retry hint in
  `test_translator_v3.py`.
- Added focused regression coverage for the extra post-repair retry path in
  `test_translator_v3.py`.
- Added a deterministic mocked benchmark path for the existing quality fixture
  harness:
  `translator_v3/benchmarks/quality_eval.py` can now run with a local
  `fixture_script` backend from `translator_v3/backends/base.py`.
- Added two scripted compression benchmark fixtures under
  `translator_v3/benchmarks/fixtures/quality/` that force the existing
  `en_compression_truncated_predicate` case to reach attempt 3 and either
  recover or remain blocked.
- Added focused regression coverage for those deterministic benchmark paths in
  `test_translator_v3.py`.
- Added minimal compression-repair telemetry on the active v3 path:
  `TranslationUnit.telemetry.compression_repair_retry`.
- Added repo-local critic-loop automation for future quality work:
  critic rules in `AGENTS.md`,
  the skill `skills/translator_critic/SKILL.md`,
  and the deterministic validator `scripts/translator_critic.py`.
- Added wrapper-based enforcement for that critic loop:
  `scripts/run_codex_with_translator_critic.sh`.
- Why this changed:
  the compression fixture was the only reliably reproducible active-path
  failure, and milestone 3 ranked a v3-only prompt addendum as the smallest
  highest-leverage change.
- Why this repair path was chosen:
  `translator_v3/orchestrate/pipeline.py` already owns retry state and
  failure-hint injection, so it was the smallest v3-only place to add one
  specialized second-pass repair instruction without changing the legacy v2
  prompt builder or broadening normal retry behavior.
- Exact files changed for this fix:
  `translator_v3/orchestrate/pipeline.py`,
  `test_translator_v3.py`
- Exact files changed for this benchmark/proof step:
  `translator_v3/core/models.py`,
  `translator_v3/backends/base.py`,
  `translator_v3/benchmarks/quality_eval.py`,
  `translator_v3/benchmarks/fixtures/quality/compression_attempt3_recovered.fixture.json`,
  `translator_v3/benchmarks/fixtures/quality/compression_attempt3_blocked.fixture.json`,
  `test_translator_v3.py`
- Added telemetry fields:
  `classified_compression_like`,
  `specialized_hint_used`,
  `retry_attempt_number`,
  `post_repair_status`
- Validation results:
  the current five-run validation on 2026-04-13 produced
  4 first-attempt acceptances,
  1 acceptance after the specialized repair retry,
  0 acceptances after the new extra retry,
  and 0 blocked results.
  The deterministic mocked benchmark on 2026-04-13 also forced attempt 3
  twice on the same compression fixture:
  once to `DELIVERABLE`,
  once to `BLOCKED`.
  `python3 test_translator_v3.py` passed with focused coverage for the new
  third-attempt path and the deterministic benchmark path,
  and `python3 test_readability_gate.py` passed.
- Compression case outcome:
  this five-run sample had no blocked outputs, but it also did not exercise
  the new extra retry in live fixture runs.
  The new deterministic benchmark path does exercise that branch, but only
  through a scripted local backend rather than a live model run.
- Remaining semantic-adequacy status:
  the focused regression proves that a body unit can now recover on attempt 3
  after a second compression-like adequacy failure, but this live five-run
  sample does not prove that branch materially changed the fixture's
  distribution because no live run reached attempt 3.
  The deterministic benchmark does prove the branch can change final status,
  but only as control-flow proof under mocked retry outputs.
- Remaining work after this one-fix stop:
  if the compression case relapses again, the next investigation step should
  capture a reproducible live or semi-live retry-path fixture that reaches
  attempt 3 without the scripted backend so the added retry can be measured
  outside this mocked benchmark.
- What still requires human judgment:
  whether a mixed repeated-run distribution is good enough to claim a real
  user-visible improvement,
  whether semantic adequacy is truly fixed rather than merely unblocked,
  and whether the chosen validation set is strong enough for the stated
  claim.

## Critic Loop Automation

- Added critic rules to `AGENTS.md` so future Codex turns keep the same
  manual-review bar around scope drift, active-path-only discipline,
  validation specificity, and reproducibility caveats.
- Added the repo-local skill `skills/translator_critic/SKILL.md`.
  It is intentionally small and focused on review, not implementation.
- Added the deterministic validator entrypoint
  `python3 scripts/translator_critic.py --plan docs/plans/active/translator-quality.md`.
  This is the repo-local equivalent of a Stop hook for the current review
  loop.
- Added the wrapper
  `scripts/run_codex_with_translator_critic.sh`.
  It is the current enforcement path: run Codex work through that wrapper and
  the critic is executed automatically afterward.
- Codex CLI one-command invocation:
  `./scripts/run_codex_with_translator_critic.sh -- "Read AGENTS.md and docs/plans/active/translator-quality.md first. <your task here>"`
- The validator is intentionally limited.
  It can detect obvious legacy-path drift and missing caveat language, but it
  does not decide semantic adequacy and it does not prove that a claimed fix
  is truly sufficient for end users.
- What is now automatic:
  running `translator_critic` after Codex work,
  non-zero process failure when the critic reports `FAIL`,
  and a repo-local one-command entrypoint for the critic loop.
- What is still manual:
  choosing the right task-specific test commands,
  judging whether mixed repeated-run evidence is sufficient,
  and deciding whether semantic adequacy is truly good enough to claim a fix.

## Milestone 4 Validation

Exact commands run after the extra post-repair retry addition:

```bash
python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path /tmp/en_compression_run01.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path /tmp/en_compression_run02.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path /tmp/en_compression_run03.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path /tmp/en_compression_run04.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path /tmp/en_compression_run05.json

python3 test_translator_v3.py
python3 test_readability_gate.py
```

Observed results after the extra post-repair retry addition and five repeated runs:

- Telemetry now lands per body unit at
  `TranslationUnit.telemetry.compression_repair_retry`.
- Run breakdown across `/tmp/en_compression_run01.json` to
  `/tmp/en_compression_run05.json`:
  4 accepted on first attempt,
  1 accepted after specialized repair retry,
  0 accepted after the new extra retry,
  0 remained blocked.
- Representative specialized-retry artifact:
  `runs_v3/quality_fixtures/20260413-233602-en_compression_truncated_predicate`
  accepted on attempt 2 with
  `classified_compression_like=true`,
  `specialized_hint_used=true`,
  `retry_attempt_number=2`,
  `post_repair_status=ACCEPTED`.
- Representative first-attempt artifact:
  `runs_v3/quality_fixtures/20260413-233334-en_compression_truncated_predicate`
  accepted on attempt 1 with
  `classified_compression_like=false`,
  `specialized_hint_used=false`,
  `post_repair_status=not_used`.
- No live artifact in this five-run sample reached the new extra retry.
  At this stage the live branch was not observed outside focused tests.
- `python3 test_translator_v3.py`: passed
- `python3 test_readability_gate.py`: passed

## Milestone 4 Deterministic Attempt-3 Benchmark

Exact commands run for the deterministic benchmark/proof step on 2026-04-13:

```bash
python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend fixture_script \
  --mock-backend-fixture translator_v3/benchmarks/fixtures/quality/compression_attempt3_recovered.fixture.json \
  --artifact-root /tmp/translator_attempt3_recovered_runs \
  --output-root /tmp/translator_attempt3_recovered_output \
  --report-path /tmp/translator_attempt3_recovered_report.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend fixture_script \
  --mock-backend-fixture translator_v3/benchmarks/fixtures/quality/compression_attempt3_blocked.fixture.json \
  --artifact-root /tmp/translator_attempt3_blocked_runs \
  --output-root /tmp/translator_attempt3_blocked_output \
  --report-path /tmp/translator_attempt3_blocked_report.json

python3 test_translator_v3.py
python3 test_readability_gate.py
```

How attempt 3 was forced:

- The benchmark reused the existing
  `en_compression_truncated_predicate` body fixture from
  `translator_v3/benchmarks/fixtures/quality/cases.json`.
- `translator_v3.benchmarks.quality_eval` now accepts a local
  `fixture_script` backend that returns scripted body translations and
  scripted adequacy-review verdicts per attempt.
- The first scripted body output is compression-like and fails adequacy review.
- The second scripted body output is the first specialized repair retry and
  still fails adequacy review.
- Because the second attempt is still a compression-like adequacy failure on a
  `body_paragraph` unit with `specialized_hint_used=true`, the active v3 retry
  loop takes the bounded extra retry and reaches attempt 3.

Observed deterministic benchmark results:

- `/tmp/translator_attempt3_recovered_report.json`:
  attempt 3 was reached on the body unit with
  `max_body_attempt_count=3`,
  `retry_attempt_number=2`,
  and final
  `post_repair_status=ACCEPTED`.
  The overall case finished `DELIVERABLE`.
- `/tmp/translator_attempt3_blocked_report.json`:
  attempt 3 was reached on the same body fixture with
  `max_body_attempt_count=3`,
  `retry_attempt_number=2`,
  and final
  `post_repair_status=BLOCKED`.
  The overall case finished `BLOCKED`.

What this proves:

- Attempt 3 is now deterministically reachable on the active v3 compression
  path without broad live reruns.
- Attempt 3 can materially help:
  in the recovered scripted case it changes the final status from the
  would-have-been post-attempt-2 block to `DELIVERABLE`.
- Attempt 3 does not guarantee recovery:
  in the blocked scripted case the extra retry runs and the final status
  remains `BLOCKED`.
- This is mocked-path proof, not live-frequency proof.
  It validates control flow and outcome sensitivity, but it does not show how
  often the live model reaches or benefits from attempt 3.

Recommendation after this benchmark step:

- The extra retry should stay for now.
  The deterministic benchmark proves it can rescue a compression-like body
  unit, so removing it would drop a real recovery path.
- Claims about material improvement should still be limited.
  The current evidence supports branch reachability and possible benefit, not
  measured live impact on the fixture distribution.

## Milestone 4 Repeated-Run Compression Analysis

The repeated-run compression fixture still shows mixed outcomes, but the
telemetry and artifacts now make the remaining instability narrower and more
concrete than the original baseline.

Representative comparison set:

- First-attempt success:
  `/tmp/en_compression_run02.json`
  with artifact
  `runs_v3/quality_fixtures/20260413-223549-en_compression_truncated_predicate`
  ended `DELIVERABLE` on attempt 1 with
  `classified_compression_like=false`,
  `specialized_hint_used=false`,
  `post_repair_status=not_used`.
  The accepted Korean output preserved both the
  “near and dear to their heart and their practice” relation and the
  coordinated predicate
  “흥미롭고도 교육적이다.”
- Specialized-repair success:
  `/tmp/en_compression_run03.json`
  with artifact
  `runs_v3/quality_fixtures/20260413-223704-en_compression_truncated_predicate`
  ended `DELIVERABLE` on attempt 2 with
  `classified_compression_like=true`,
  `specialized_hint_used=true`,
  `retry_attempt_number=2`,
  `post_repair_status=ACCEPTED`.
  This shows the compression-like classifier and the specialized repair path
  can recover the unit when the first pass compresses too aggressively.
- Still-blocked after specialized repair:
  `/tmp/en_compression_run01.json`
  with artifact
  `runs_v3/quality_fixtures/20260413-223308-en_compression_truncated_predicate`
  ended `BLOCKED` on attempt 2 with the same retry telemetry:
  `classified_compression_like=true`,
  `specialized_hint_used=true`,
  `retry_attempt_number=2`,
  `post_repair_status=BLOCKED`.
  The final adequacy-review failure was:
  the retry output produced a fluent Korean sentence, but it still dropped the
  extra coordinated descriptor before “and educational” and kept only the
  educational meaning.

Concrete difference in failure mode:

- The remaining blocked run is no longer best described as a failure to detect
  compression or a pure incomplete-predicate collapse.
- It is a narrower semantic-adequacy miss after the repair path already fired:
  the model rebuilt a complete sentence, but it still omitted one recoverable
  piece of meaning from the damaged clause.
- That matters because the active path is now unstable at the recovery step,
  not at the classification step.

Most likely cause of the remaining instability:

- More likely `3) retry policy depth` than
  `1) compression-like failure classification`,
  `2) repair hint wording`,
  or
  `4) planner/unit boundary effects`.
- Why not `1)`:
  the blocked representative run was correctly tagged
  `classified_compression_like=true` and the specialized repair hint was used.
- Why not primarily `2)`:
  the same repair-hint path already produced one retry-path acceptance, and the
  blocked run also carried the adequacy-review explanation into the retry.
  The evidence does not show a missed or malformed hint so much as a
  one-chance repair path that sometimes lands and sometimes misses.
- Why not primarily `4)`:
  the damaged unit boundary is clearly a contributing pressure, because the
  fixture starts mid-word (`ing and educational ...`), but the repeated-run
  evidence shows that the current unit boundary can still succeed on attempt 1
  or attempt 2.
  The unstable branch is specifically the single specialized retry budget after
  a correct compression-like classification.

Recommended smallest next code change:

- In `translator_v3/orchestrate/pipeline.py`, allow exactly one additional
  retry only when all of the following are true:
  `specialized_hint_used=true`,
  the unit is a body paragraph,
  and the post-repair failure is still compression-like
  (`adequacy_review_failed` or `severe_undertranslation`).
- Status:
  implemented on 2026-04-13 with focused regression coverage in
  `test_translator_v3.py`.
- This is the smallest next change because it keeps the active-path scope in
  the existing retry owner, preserves the current classifier and hint wording,
  and directly targets the observed instability:
  one first-attempt win,
  one win on specialized retry,
  and one miss after the only specialized retry was exhausted.
- This is still only a bounded hypothesis.
  Human judgment is required afterward to decide whether an extra retry really
  fixes user-visible adequacy or merely increases stochastic recoveries on this
  one damaged-clause fixture.

## Milestone 2 Commands

Exact active-path evaluation commands used for the recorded baseline:

```bash
python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_residue_repeat_listens \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path translator_v3/benchmarks/fixtures/quality/en_residue_repeat_listens.active_v3.codex_local.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id ja_residue_valis_titles \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path translator_v3/benchmarks/fixtures/quality/ja_residue_valis_titles.active_v3.codex_local.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_omission_flynt_surprised \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path translator_v3/benchmarks/fixtures/quality/en_omission_flynt_surprised.active_v3.codex_local.json

python3 -m translator_v3.benchmarks.quality_eval \
  --manifest translator_v3/benchmarks/fixtures/quality/cases.json \
  --case-id en_compression_truncated_predicate \
  --backend codex_local \
  --model gpt-5.4 \
  --reasoning-effort high \
  --artifact-root runs_v3/quality_fixtures \
  --output-root output/quality_fixtures \
  --report-path translator_v3/benchmarks/fixtures/quality/en_compression_truncated_predicate.active_v3.codex_local.json
```

## 1) Active Execution Path

- Primary runtime: `job_runner.py`
- Current default command: `python3 job_runner.py input.txt -o output.txt --v3 --v3-backend codex_local`
- Command path:
  `job_runner.py`
  -> `translator_v3.cli`
  -> `translator_v3/orchestrate/pipeline.py`
  -> `translator_v3/ingest/spine.py`
  -> `translator_v3/plan/planner.py`
  -> `translator_v3/backends/base.py`
  -> shared prompt builder in `translator_v2/translate/prompt_builder.py`
  -> `translator_v3/validate/runner.py`
  -> optional adequacy review in `translator_v3/backends/base.py`
  -> retry loop in `translator_v3/orchestrate/pipeline.py`
  -> `translator_v3/assemble/reconstructor.py`

## 2) Likely Root Causes

- Prompt path mismatch: v3 uses a v2 batch-style prompt builder for single-unit translation. The prompt says “do not omit,” but it does not force sentence-by-sentence coverage or explicit completeness accounting. Files: `translator_v3/backends/base.py`, `translator_v2/translate/prompt_builder.py`.
- Planner-induced context loss: long paragraphs are split mostly by sentence length and then by hard character cuts, with no overlap or carry-forward of unresolved meaning. This can cause local omission or compressed paraphrase at boundaries. File: `translator_v3/plan/planner.py`.
- Validation is too coarse for silent omission: current checks rely mainly on sentence count, rough length ratio, exact-copy, numbers, readability, and residue. Meaning-preserving-looking compression can pass even when important details are dropped. File: `translator_v3/validate/runner.py`.
- Adequacy review is lenient or not targeted enough: the reviewer explicitly allows “minor compression,” and v3 only escalates based on heuristics such as unit class, length, and ratio. Some bad outputs will never get a stronger semantic check. Files: `translator_v3/orchestrate/pipeline.py`, `translator_v3/backends/base.py`, `translator_v3/core/models.py`.
- Retry depth is shallow on v3: `max_retry_per_unit` defaults to `1`, and retries mainly resend the same unit with a failure hint. v3 does not yet use the more structured repair tree that exists in v2, so persistent residue/omission patterns can survive. Files: `translator_v3/core/models.py`, `translator_v3/orchestrate/pipeline.py`, `translator_v2/repair/runner.py`.
- Residue detection coverage is incomplete across source languages: v3 inherits residue validation from v2, which depends on `quality_gate.py` heuristics. Japanese residue detection is much richer than English residue detection, so partial English carryover can slip through. Files: `translator_v2/validate/residue.py`, `quality_gate.py`.

## 3) Exact Files To Inspect

- Active orchestration: `job_runner.py`
- v3 entrypoint: `translator_v3/cli.py`
- v3 main loop and retry policy: `translator_v3/orchestrate/pipeline.py`
- v3 config defaults: `translator_v3/core/models.py`
- v3 planner and split behavior: `translator_v3/plan/planner.py`
- v3 backend translation and adequacy review: `translator_v3/backends/base.py`
- shared translation prompt builder: `translator_v2/translate/prompt_builder.py`
- v3 validation rules: `translator_v3/validate/runner.py`
- shared residue validator: `translator_v2/validate/residue.py`
- shared residue heuristics and quality gate: `quality_gate.py`
- legacy repair reference: `translator_v2/repair/runner.py`
- current regression coverage: `test_translator_v3.py`, `test_translator_v2.py`, `test_readability_gate.py`

## 4) How To Measure Improvement

- Add a focused long-form fixture set with known failure cases:
  source-language residue,
  silent omission,
  aggressive compression that drops material content.
- Track unit-level metrics from v3 artifacts:
  accepted vs blocked units,
  retry count,
  adequacy review triggered count,
  coverage-related validation issues,
  residue-related validation issues.
- Add outcome metrics:
  residue incidence per 100 units,
  omission/compression failure rate on curated fixtures,
  blocked-run rate,
  false-positive rate from stronger validation.
- Use existing script tests as the baseline and extend with targeted cases in `test_translator_v3.py`; keep `test_translator_v2.py` and `test_readability_gate.py` green to avoid regressions in shared validators.
- “Improved” means the curated failures are caught or repaired on the active v3 path without increasing spurious blocks on clean fixtures.

## 5) Milestones

1. Complete: trace and document the live v3 quality path end to end, including where v2 prompt and residue logic are still reused.
2. Complete: build a reproducible fixture set and baseline metrics for residue, omission, and compression failures on long-form units.
3. Complete: tighten detection design on paper first: prompt contract, planner boundary policy, adequacy review trigger policy, retry policy, and residue coverage options.
4. In progress: implemented only the first recommended v3-only prompt-contract fix and validated it. The compression case improved in syntactic completeness but remains blocked on semantic adequacy.
