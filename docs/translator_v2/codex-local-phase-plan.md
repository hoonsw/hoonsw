# Translator V2 Codex-Local Phase Plan

Updated: 2026-04-11

## Strategy

Add `codex_local` as a second generation backend for `translator_v2`.

Do not replace the ledger model, validation model, or OpenClaw-facing run
wrapper. Only replace how translation batches are produced.

## Phase 0: Contracts First

Goal:

- freeze the queue contract before implementation

Deliverables:

- `generation_mode` config contract
- queue directory layout
- task/result schema
- claim epoch and stale-result rejection contract
- worker instruction template
- timeout and stale-task policy
- accepted-segment backend provenance fields

Exit criteria:

- a dry-run can create a valid run directory and emit pending tasks without
  invoking any provider
- late results are mechanically rejectable by epoch and claim metadata

## Phase 1: Single-Worker Happy Path

Goal:

- prove that one Codex worker can complete a whole small run through the queue

Scope:

- task emission from existing segment ledger
- file-based claim/complete flow
- claim epoch validation before ingestion
- strict wrapper parsing
- existing validator path
- assembly, reporting, finalize, and notify

Exit criteria:

- a small sample book can complete end-to-end in `codex_local`
- the Discord/OpenClaw completion path still works
- any malformed worker result blocks or requeues correctly
- stale worker output cannot be mistaken for the current accepted result

## Phase 2: Repair Lane

Goal:

- support issue-driven rework without whole-run restart

Scope:

- `repair_batch` task type
- repair reasons in task metadata
- lineage-safe split repairs
- stale-task abandonment and requeue

Exit criteria:

- failed segments can be retried without touching accepted neighbors
- stale worker tasks do not corrupt ledger state

## Phase 3: Operational Quality

Goal:

- make long book runs usable from Discord/OpenClaw

Scope:

- queue backlog reporting
- worker throughput summaries
- blocked-run summaries with unresolved segment IDs
- final artifact packaging tuned for Discord delivery

Exit criteria:

- long runs report useful progress while waiting on workers
- failures are understandable from the resulting report package

## Phase 4: Multi-Worker Concurrency

Goal:

- improve throughput without changing correctness semantics

Scope:

- multiple Codex workers claiming tasks in parallel
- fairness policy across chapters
- queue reconciliation after interrupted workers

Exit criteria:

- multi-worker runs preserve identical ledger semantics
- claim races cannot produce duplicate accepted output

## Phase 5: Optional Hybrid Mode

Goal:

- allow controlled coexistence of `api` and `codex_local`

Scope:

- per-run backend selection
- provider fallback only for explicitly allowed tasks
- comparable reports across both backends

Rules:

- fallback must be explicit and auditable
- fallback must not silently occur inside a run

Exit criteria:

- a run report always shows which backend produced which accepted segments from
  ledger-persisted provenance, not queue reconstruction

## Explicit Exclusions for Early Delivery

- recursive OpenClaw-driven worker spawning
- automatic multimodel debate during translation
- style-only passes as a prerequisite for delivery
- queue transports outside the local filesystem

## Recommended Rollout

1. implement `Phase 0` and `Phase 1`
2. test on one previously problematic book excerpt
3. add `Phase 2` before any production book-length run
4. add `Phase 3` before declaring the Discord UX "production ready"
5. treat `Phase 4` and `Phase 5` as later scale work

## Success Metrics

- outer workflow remains "command -> progress -> completion -> files"
- no accepted run contains unresolved critical segments
- queue artifacts are enough to explain blocked runs
- Codex worker failure never masquerades as translation success
- the system can resume after interruption without losing accepted work
