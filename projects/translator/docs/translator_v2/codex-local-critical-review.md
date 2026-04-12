# Translator V2 Codex-Local Critical Review

Updated: 2026-04-11

This file records the adversarial review findings applied to the
`codex_local` design package.

## Reviewer Findings

The critical reviewer raised these main risks in the initial draft:

1. the queue/ledger ownership boundary was directionally right but not explicit
   enough to prevent accidental worker authority creep
2. preserving the outer Discord/OpenClaw UX was stated as a goal, but the
   design did not make completion notification ownership explicit
3. the design needed a clearer anti-recursion rule so OpenClaw would not become
   both orchestrator and worker launcher inside the same run
4. stale-task handling needed a documented place in the contract
5. hybrid `api`/`codex_local` coexistence needed an auditability rule
6. stale workers could return late results with no durable claim-epoch contract
7. backend provenance was not durable enough if queue artifacts were later pruned

## Resolutions Applied

### 1. Narrow Worker Authority

Added explicit worker prohibitions:

- workers may not edit ledger files
- workers may not overwrite final outputs
- workers may not requeue tasks directly
- workers only emit task-local artifacts

Result:

- the orchestrator remains the only control plane

### 2. Completion Ownership

Added an explicit rule:

- completion and failure notifications come from the orchestrator and existing
  OpenClaw wrappers, never from individual workers

Result:

- the outer UX remains stable even when worker composition changes

### 3. Anti-Recursion Rule

Added a dedicated architecture section:

- one outer run owns the session
- workers consume queue files only
- workers must not recursively call the translator entry point

Result:

- the design avoids opaque nested agent loops

### 4. Stale-Task Contract

Added:

- heartbeat artifact guidance
- timeout-based staleness detection
- `abandoned/` queue state before re-emission

Result:

- long-running or interrupted workers no longer sit in an undefined state

### 5. Hybrid Auditability

Added:

- `generation_mode` persistence in run metadata
- explicit no-silent-fallback rule
- per-run backend visibility in reports

Result:

- mixed-mode support can exist later without hiding who produced accepted output

### 6. Claim Epoch Contract

Added:

- monotonic `task_epoch` on emitted tasks
- unique `claim_id` on claims
- result ingestion rule requiring exact `task_id + task_epoch + claim_id` match
- stale-result quarantine on mismatch

Result:

- late results from abandoned workers are mechanically rejectable

### 7. Durable Backend Provenance

Added:

- accepted segment provenance fields in the ledger
- report rule that backend attribution must come from ledger records, not queue
  reconstruction

Result:

- mixed-mode support can exist later without hiding who produced accepted output

## Final Review Outcome

Reviewer verdict before revision:

- strong direction
- not final until worker authority, notification ownership, and stale-task
  handling were locked down

Reviewer verdict after revision:

- finalizable as a design baseline
- safe to use as the implementation target for a first Codex-local execution
  mode

## Final Design Rule

`codex_local` changes the generation backend, not the safety model.

That means:

- the ledger remains authoritative
- validation remains a hard gate
- workers remain producers, not controllers
- OpenClaw remains the outer operating surface
