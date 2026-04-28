# AGENTS

Current supported runtime is `translator_v3 + codex_local`.
`job_runner.py` is the main operational entrypoint.
`translate.py` remains as a shared helper module for review/report/finalize code, but its CLI is retired.
`run_pipeline.py` and `web.py` are retired shims that now only point users at the v3 path.

## Run the translator

Primary path:

```bash
python3 job_runner.py input.txt -o output.txt --v3-backend codex_local
```

Direct v3 CLI:

```bash
python3 -m translator_v3.cli run input.txt -o output.txt --backend codex_local --model gpt-5.4 --reasoning-effort high
```

## Run tests

Use the repo's script-style tests:

```bash
python3 test_translator_v3.py
python3 test_job_runner.py
python3 test_translate_entrypoint.py
python3 test_input_pipeline.py
python3 test_readability_gate.py
```

Add targeted `test_*.py` runs when touching shared helper code.

## Where things live

- Translation, current path: `translator_v3/orchestrate/pipeline.py`, `translator_v3/backends/`, `translator_v3/plan/`, `translator_v3/assemble/`
- Review/validation, current path: `translator_v3/validate/runner.py`
- Retry/repair, current path: retry loop inside `translator_v3/orchestrate/pipeline.py`
- Shared helper/config surface: `translate.py`, `review.py`, `report.py`, `finalize.py`, `extract_glossary.py`
- Internal shared legacy components still reused by v3: `translator_v2/translate/`, `translator_v2/validate/`, `translator_v2/codex_local/`
- Readability gate shared by validators: `readability_gate.py`

## What "done" means for quality fixes

- The reproduced failure is covered by a test or a minimal fixture.
- Relevant tests pass, especially `test_translator_v3.py`, `test_job_runner.py`, `test_translate_entrypoint.py`, and `test_readability_gate.py` when applicable.
- The fix removes source-language residue or omission without weakening number/exact-copy/readability checks.
- The active path ends unblocked: units finish `ACCEPTED` with no `BLOCKED` output unless the task explicitly targets blocked-run handling.

## Critic Rules

- Default critic target is the active `translator_v3` path. Treat edits outside `translator_v3/`, `job_runner.py`, active docs, and shared helper modules as scope drift unless the task explicitly requires them.
- Do not claim improvement from one green run when the plan or artifacts show mixed outcomes, attempt-1 wins, or unstable repeated-run results.
- Validation claims should name the exact commands run and the concrete result, not just say “passed” or “improved”.
- Reproducibility caveats stay in the write-up: if a fix is covered only by a focused regression test, or if the live fixture accepted on attempt 1, say so explicitly.
- Human judgment is still required for semantic adequacy, whether a fixture really proves the user-facing problem is fixed, and whether the chosen validation is sufficient for the claimed change.

## Critic Loop

- Skill: use `$translator_critic` or mention `translator_critic` when you want a scoped review of active-path discipline, validation coverage, reproducibility caveats, and proof strength.
- Repo-local skill file: `skills/translator_critic/SKILL.md`
- Deterministic validator entrypoint:
  `python3 scripts/translator_critic.py --plan docs/plans/active/translator-quality.md`
- One-command Codex CLI workflow:
  `./scripts/run_codex_with_translator_critic.sh -- "Read AGENTS.md and docs/plans/active/translator-quality.md first. <your task here>"`
- The wrapper runs `codex exec` from the repo root and then runs
  `translator_critic` automatically.
- Manual steps still remain:
  run the relevant repo tests for the touched area and make the final
  judgment on semantic adequacy or proof strength.
