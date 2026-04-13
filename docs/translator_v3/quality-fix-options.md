# Translator V3 Quality Fix Options

Updated: 2026-04-13

This document is milestone 3: a ranked design-only proposal for the active
`translator_v3 + codex_local` path, using the current fixture baseline.

## Baseline-Driven Priorities

Current baseline reference:

- `docs/translator_v3/quality-fixtures-baseline.md`
- `translator_v3/benchmarks/fixtures/quality/baseline_results.active_v3.codex_local.json`

Priority order from the baseline:

1. `en_compression_truncated_predicate`
   This is the only failure that reproduced cleanly and remained blocked on the
   active path. It is the primary target.
2. `en_residue_repeat_listens`
   The case still blocked, but on adequacy review rather than residue. Residue
   itself did not reproduce.
3. `en_omission_flynt_surprised`
   The earlier omission did not reproduce.
4. `ja_residue_valis_titles`
   The earlier Japanese-title residue did not reproduce.

That means milestone 4 should optimize first for the reproducible
compression/truncation failure and should treat residue and omission work as
secondary until their fixtures become more reliable.

## Ranked Options

### 1. Add a v3-only completeness addendum to the translation prompt contract

Why it ranks first:

- It directly targets the one reproducible failure.
- It is the smallest likely behavior-changing diff on the active path.
- It can be limited to v3 without modifying shared v2 prompt behavior.

What it would do:

- Add explicit instructions for body-paragraph units that forbid incomplete
  Korean predicates, hanging ellipses, or clause fragments that simply mirror a
  damaged source edge.
- Require the model to preserve every recoverable semantic role in truncated or
  malformed source text, even when the source starts or ends mid-clause.
- Require the retry prompt to restate that a blocked truncation/compression
  issue must return a syntactically complete Korean output, not a graceful
  fragment.

Expected benefit:

- Highest expected gain on the reproducible compression case.
- Low-latency path to changing generation behavior rather than only detection.
- May improve retry success on adequacy-blocked units without touching planner
  or validation thresholds.

Likely risk:

- Could make output slightly more verbose or more interpretive at damaged
  boundaries.
- If phrased badly, could encourage hallucinated completion rather than careful
  recovery.

Files affected:

- `translator_v3/backends/base.py`

Attempt now or defer:

- Attempt now.

### 2. Tighten adequacy review prompts for truncated-clause detection

Why it ranks second:

- The reproducible compression case is already being caught by adequacy review.
- Tightening the review prompt is a small diff, but it improves blocking
  reliability more than translation quality itself.

What it would do:

- Make the adequacy reviewer fail explicitly on dangling predicates,
  unfinished clause recovery, and syntactically incomplete Korean output.
- Tell the reviewer not to treat fragmentary fluency as acceptable compression.

Expected benefit:

- Improves consistency of blocking on compression/truncation failures.
- Small and isolated change.

Likely risk:

- Mostly detection gain, not repair gain.
- Can increase false positives on quotations, headings, or intentionally
  fragmentary prose if the review contract is too blunt.

Files affected:

- `translator_v3/backends/base.py`

Attempt now or defer:

- Defer unless milestone 4 is explicitly about stronger blocking rather than
  successful repair.

### 3. Increase retry specificity for adequacy/compression failures

Why it ranks third:

- Retry behavior is shallow, and the reproducible case fails after only one
  retry.
- This is still fairly small if limited to failure-hint formatting, but it
  touches orchestration rather than just prompt text.

What it would do:

- Distinguish adequacy/compression retries from generic retries.
- For `adequacy_review_failed`, include a stronger repair instruction such as:
  preserve all recoverable meaning, avoid ellipsis, and return a complete
  Korean predicate.
- Optionally allow one extra retry only for adequacy/compression failures.

Expected benefit:

- Better chance that a unit already caught by adequacy review will recover on
  the second pass.
- More targeted than globally raising retry counts.

Likely risk:

- More latency and cost on difficult units.
- A global retry-count change could magnify churn on non-reproducible cases.

Files affected:

- `translator_v3/orchestrate/pipeline.py`
- `translator_v3/core/models.py`
- possibly `translator_v3/backends/base.py`

Attempt now or defer:

- Defer until after the prompt-contract change is measured.

### 4. Adjust planner boundary policy for damaged or marginal source blocks

Why it ranks fourth:

- Planner boundaries are a plausible cause of compression and omission in
  general, but the current reproducible case is already a single short unit.
- This is higher complexity and less directly tied to the one reliable fixture.

What it would do:

- Detect suspiciously truncated source starts or ends and annotate the unit.
- Avoid hard sentence splits around damaged boundaries.
- Potentially add local overlap or boundary metadata for retry-time use.

Expected benefit:

- Helps broader long-form robustness where boundary loss causes semantic
  compression or omission.
- Good medium-term architecture fit.

Likely risk:

- More code complexity.
- Can change unit counts and benchmark comparability.
- Harder to isolate from unrelated behavior shifts.

Files affected:

- `translator_v3/plan/planner.py`
- possibly `translator_v3/core/models.py`
- possibly `translator_v3/validate/runner.py`

Attempt now or defer:

- Defer.

### 5. Expand prompt contract completeness in the shared v2 prompt builder

Why it ranks fifth:

- It is potentially high leverage because the active v3 translation prompt still
  reuses v2 infrastructure.
- But it has wider blast radius than a v3-only addendum.

What it would do:

- Strengthen the existing segment prompt with explicit completeness and
  anti-truncation language for all segment generations.

Expected benefit:

- Could improve both v3 and any active v2 compatibility path.

Likely risk:

- Touches shared prompt behavior across multiple code paths.
- Harder to attribute outcome changes cleanly.

Files affected:

- `translator_v2/translate/prompt_builder.py`

Attempt now or defer:

- Defer.

### 6. Tighten adequacy review trigger policy

Why it ranks sixth:

- The current omission fixture did not reproduce, and the reproducible
  compression fixture is already triggering adequacy review.
- Better trigger coverage is valuable, but not the highest-yield next change.

What it would do:

- Run adequacy review more aggressively for low-char-ratio units, malformed
  source boundaries, and title-heavy residue-risk blocks.
- Potentially review all body units that contain line-break hyphenation or
  suspicious punctuation loss.

Expected benefit:

- Better coverage for non-reproducible omission or residue failures once those
  cases are hardened.

Likely risk:

- More cost and latency.
- Higher false-positive rate if review prompts remain permissive or noisy.

Files affected:

- `translator_v3/orchestrate/pipeline.py`
- `translator_v3/core/models.py`

Attempt now or defer:

- Defer.

### 7. Expand residue detection coverage, especially English-title carryover

Why it ranks seventh:

- Residue is a real concern, but the current fixture baseline does not
  reproduce it reliably enough to make this the first implementation target.
- The English residue case currently blocks via adequacy rather than residue.

What it would do:

- Strengthen English residue heuristics around title-heavy carryover,
  untranslated multiword spans, and mixed-script title handling.
- Possibly add title-aware exceptions and title-aware blocking rules instead of
  using one generic English-passage heuristic.

Expected benefit:

- Better direct residue labeling when English source phrases survive in Korean
  output.
- Better observability of the residue class rather than indirect adequacy
  failure.

Likely risk:

- False positives on preserved proper nouns, album titles, bibliographic data,
  and intentionally untranslated tokens.
- This is detection work, not repair work.

Files affected:

- `quality_gate.py`
- `translator_v2/validate/residue.py`
- possibly `translator_v3/validate/runner.py`

Attempt now or defer:

- Defer until residue fixtures become more reliable.

## Option Summary

Attempt now:

- v3-only completeness addendum to the translation prompt contract

Defer:

- adequacy review prompt tightening
- retry-policy changes
- planner boundary policy changes
- shared v2 prompt-builder edits
- adequacy review trigger expansion
- residue detection expansion

## Recommendation For Milestone 4

Implement exactly one target:

- `translator_v3/backends/base.py`: add a v3-only completeness addendum to the
  translation prompt contract used by `ProviderApiBackend.translate_unit()` and
  `CodexLocalBackend.translate_unit()`, explicitly forbidding incomplete Korean
  predicates, dangling ellipses, and compression that drops recoverable meaning
  in truncated or malformed source clauses.

Why this is the recommended single target:

- smallest expected diff with the highest direct gain on the only reproducible
  failure
- active-path only, so the blast radius stays narrower than editing the shared
  v2 prompt builder
- changes generation behavior rather than only making the blocker harsher
- measurable immediately against
  `en_compression_truncated_predicate.active_v3.codex_local.json`
