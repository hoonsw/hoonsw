---
name: translator_critic
description: Review translator-repo changes for scope drift, active-path-only discipline, validation completeness, reproducibility caveats, and whether a claimed improvement is actually proven. Use after v3 quality, retry, validation, docs, or benchmark changes before finalizing.
---

# Translator Critic

Use this skill after making a translator change when the final answer needs a stricter audit.

## What to review

- Scope drift: did the change stay inside the requested area, or did it spill into legacy or unrelated paths?
- Active-path-only discipline: if the task is about current behavior, did the diff stay on `translator_v3` unless the request explicitly allowed more?
- Validation completeness: were the relevant commands actually run for the touched files, and are the results stated concretely?
- Reproducibility caveats: does the write-up admit when a fixture passed on attempt 1, when a retry path was only proven by a focused test, or when repeated runs are mixed?
- Proof strength: does the claimed “improvement” match the evidence, or is the result only partial, unstable, or not yet proven?

## How to run it

1. Read `AGENTS.md`.
2. Read the active plan file, usually `docs/plans/active/translator-quality.md`.
3. Run the deterministic validator:
   `python3 scripts/translator_critic.py --plan docs/plans/active/translator-quality.md`
4. Inspect the diff and artifacts yourself.
5. Report findings first. Use `FAIL`, `WARN`, or `PASS`.

## Output shape

- `FAIL`: scope drift, legacy-path edits without approval, or claimed validation that is not actually present.
- `WARN`: incomplete reproducibility caveats, mixed evidence, or improvement claims that are stronger than the artifacts justify.
- `PASS`: scoped change, concrete validation, and claims that match the observed evidence.

## Limits

- This skill does not replace human judgment on translation adequacy.
- This skill should not broaden scope on its own.
- If the validator passes but the artifacts still look weak, say so directly.
