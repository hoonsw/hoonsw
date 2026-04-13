# AGENTS

Current default runtime is `translator_v3 + codex_local`. Prefer that path unless you are debugging legacy behavior.

## Run the translator

Primary path:

```bash
python3 job_runner.py input.txt -o output.txt --v3 --v3-backend codex_local
```

Direct v3 CLI:

```bash
python3 -m translator_v3.cli run input.txt -o output.txt --backend codex_local --model gpt-5.4 --reasoning-effort high
```

Legacy/v2 path:

```bash
python3 -m translator_v2.cli input.txt -o output.txt --generation-mode codex_local --codex-worker-mode external
```

## Run tests

Use the repo's script-style tests:

```bash
python3 test_translator_v3.py
python3 test_translator_v2.py
python3 test_job_runner.py
python3 test_readability_gate.py
```

Add targeted `test_phase*.py` or other `test_*.py` runs when touching legacy pipeline behavior.

## Where things live

- Translation, current path: `translator_v3/orchestrate/pipeline.py`, `translator_v3/backends/`, `translator_v3/plan/`, `translator_v3/assemble/`
- Review/validation, current path: `translator_v3/validate/runner.py`
- Retry/repair, current path: retry loop inside `translator_v3/orchestrate/pipeline.py`
- Retry/repair, structured legacy path: `translator_v2/repair/runner.py`
- Legacy review tool: `review.py`
- Legacy retry helper: `retry_engine.py`
- Legacy quality gate: `quality_gate.py`
- Readability gate shared by validators: `readability_gate.py`

## What "done" means for quality fixes

- The reproduced failure is covered by a test or a minimal fixture.
- Relevant tests pass, especially `test_translator_v3.py`, `test_translator_v2.py`, and `test_readability_gate.py` when applicable.
- The fix removes source-language residue or omission without weakening number/exact-copy/readability checks.
- The active path ends unblocked: for v3, units finish `ACCEPTED` with no `BLOCKED` output; for legacy gate work, the result is not blocked by `quality_gate.py`.

## Critic Rules

- Default critic target is the active `translator_v3` path. Treat edits to `translator_v2/`, `quality_gate.py`, `review.py`, `retry_engine.py`, `translate.py`, or other legacy paths as scope drift unless the task explicitly requires them.
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
