# Translator V2 Codex-Local Architecture

Updated: 2026-04-11

## Summary

`codex_local` is an execution mode for `translator_v2`.

It exists for the case where the user wants to keep the current
`Discord/OpenClaw` operating style but does not want the translator itself to
depend on direct model API calls for the main translation step.

The key idea is simple:

- `translator_v2` keeps ownership of ingest, segmentation, ledger, validation,
  repair planning, assembly, reporting, packaging, and delivery
- Codex becomes a local worker that reads bounded translation tasks from disk
  and writes bounded results back to disk
- OpenClaw keeps ownership of command entry, progress notifications, and final
  file delivery

This is an internal architecture change, not a user-facing workflow rewrite.

## What Stays the Same

The outer UX should remain effectively unchanged.

Typical user experience:

1. a Discord command starts the run
2. OpenClaw launches the local translation pipeline
3. progress and phase messages are sent during the run
4. completion or failure is announced automatically
5. result files and reports are packaged and delivered

From the user's perspective, the translator still behaves like
"ask in Discord, wait, receive the finished files."

## What Changes Internally

In API mode today, the translation step looks like this:

`translator_v2 -> provider adapter -> remote model API`

In `codex_local` mode, it becomes:

`translator_v2 -> task queue -> Codex worker -> task result -> validator`

That means:

- the pipeline no longer treats translation as a direct function call
- translation generation becomes an asynchronous work queue
- validation and repair stay deterministic and remain the control plane
- the worker is not allowed to bypass ledger or assembly rules

## Design Goals

- preserve the current `Discord/OpenClaw` UX
- support book-length runs without embedding a model backend in the translator
- keep `provable coverage` and hard validation as the primary safety contract
- avoid recursive or self-referential agent orchestration
- allow a future hybrid setup where `api` and `codex_local` share one ledger
  model but use different generation backends

## Non-goals

- making Codex behave like an unrestricted batch API
- letting worker outputs skip validation because they came from a "better"
  model
- hiding unresolved failures behind partial delivery
- pushing OpenClaw into the role of the translation engine itself

## Execution Modes

`translator_v2` should support two generation modes:

- `api`
  - current direct provider path
- `codex_local`
  - local queue-based generation path

Mode selection should be explicit in config and persisted in the run manifest.

Suggested config field:

```yaml
generation_mode: codex_local
```

## Topology

```text
Discord
  -> OpenClaw command/session
    -> scripts/run_with_notify.sh
      -> translator_v2 orchestrator
        -> ingest / segment / ledger
        -> codex queue
          -> Codex worker(s)
            -> result files
        -> validate / repair / assemble / report / finalize
      -> OpenClaw completion notification
    -> Discord file delivery
```

## Roles and Ownership

### OpenClaw

Owns:

- command entry
- long-run supervision
- progress notifications
- completion/failure notifications
- optional final file delivery to Discord

Does not own:

- segment ledger mutation
- translation acceptance logic
- final assembly rules

### Translator V2 Orchestrator

Owns:

- canonicalization and segmentation
- run manifest and ledger persistence
- claim epoch issuance and stale-result rejection
- task creation and repair planning
- validation and acceptance rules
- blocked-vs-deliverable decision
- final assembly, reports, and packaging

Does not own:

- direct generation in `codex_local` mode

### Codex Worker

Owns only:

- claiming a pending translation or repair task
- reading the bounded task payload
- producing wrapped output for the requested segment IDs
- writing a result artifact and optional worker notes

Does not own:

- final output files
- ledger status transitions other than task-local metadata
- repair policy
- progress notifications
- direct Discord or OpenClaw calls

This boundary is important. The worker is a producer, not the control plane.

## Run Lifecycle

### 1. Command Entry

The user starts a run from Discord. OpenClaw launches the existing local shell
wrapper and pipeline entry point.

### 2. Setup

`translator_v2` creates:

- run manifest
- normalized source hash
- segment ledger
- initial queue directories
- progress event metadata

### 3. Task Emission

The orchestrator emits bounded task files. A task should stay within one
chapter and contain a small ordered batch of segment IDs.

Phase-1 recommendation:

- batch only segments from the same chapter
- preserve source order inside the task
- keep task size small enough that a worker can complete it reliably in one
  interaction

### 4. Worker Translation

One or more Codex workers claim tasks and write results using the strict segment
wrapper contract already defined by `translator_v2`.

### 5. Validation

The orchestrator validates each returned segment.

- accepted segments are marked `ACCEPTED`
- failed segments are marked `FAILED_PARSE` or `FAILED_VALIDATION`
- unresolved segments re-enter the queue as repair tasks
- exhausted repair budget blocks the run
- late results from stale claims are quarantined and never ingested

### 6. Assembly and Finalization

Only when every ledger segment is accepted:

- the document is assembled in source order
- reports are written
- packaging/finalization runs
- OpenClaw emits completion and delivery notifications

## Queue Strategy

The queue must be file-based and run-local.

Why:

- it matches the current repo's local-tool operating model
- it is easy for Codex to inspect and manipulate
- it survives restarts
- it is auditable after failures

The queue is not just a convenience layer. It is the contract boundary between
control plane and worker plane.

## Task Granularity

The task unit is not "translate the whole book."

Recommended unit:

- one ordered batch of segment IDs from one chapter

Each task includes:

- run metadata
- chapter ID
- requested segment IDs
- source text for each segment
- limited neighboring context
- glossary/protected-span hints
- exact output schema instructions
- attempt count and repair reason

This keeps the worker focused while allowing the orchestrator to preserve hard
coverage guarantees.

## Validation and Repair Policy

`codex_local` does not change the validation rules from `translator_v2`.

That is the most important design choice in this extension.

- worker output is still untrusted until validated
- repair remains issue-driven, not score-driven
- only failed segment IDs are requeued
- assembly is still blocked by any unresolved critical issue
- a result is ingestible only if its claim epoch matches the currently active
  task claim for that segment lineage

Codex may improve generation quality, but it does not replace machine-checkable
validation.

## Durable Provenance

`codex_local` should not make backend attribution depend on queue files alone.

For every accepted segment, the ledger should persist:

- `generation_mode`
- `backend_label`
  - example: `codex_local`
- `worker_id` or provider/model identity
- `accepted_task_id`
- `accepted_task_epoch`

Reports must derive backend attribution from accepted ledger records, not from
best-effort reconstruction of old queue artifacts.

## Progress and Notifications

The current OpenClaw-facing behavior should stay in place.

Reused pieces:

- [progress.py](/home/hoonsw/dev/translator/progress.py:1)
- [run_with_notify.sh](/home/hoonsw/dev/translator/scripts/run_with_notify.sh:1)

Additional progress events should be added for:

- queue creation
- waiting for worker capacity
- task completion counts
- repair backlog counts
- blocked-run summaries

This means the Discord side still sees useful progress even though generation is
queue-driven.

## Failure Model

The run is `BLOCKED`, not "partially done," when any of the following is true:

- a required task never returns within timeout budget
- a result artifact is malformed
- validation fails beyond repair budget
- ledger and queue state diverge
- a result arrives for an expired claim epoch
- final assembly prerequisites are not satisfied

The system may emit a blocked work package for debugging, but it must not send a
deliverable translation file as if the run succeeded.

## No Recursive Agent Loops

The design must explicitly avoid the following anti-pattern:

- OpenClaw starts the translator
- the translator asks OpenClaw to spawn Codex again inside the same run
- the worker then re-enters the same orchestrator path

That creates opaque recursion and brittle ownership.

Instead:

- one outer run owns the session
- workers consume files from a queue contract
- worker instructions forbid calling the translator entry point recursively
- worker results are plain artifacts, not nested pipeline launches

## Migration Recommendation

Adopt `codex_local` as a new execution mode, not as a hard replacement for
`api`.

Recommended path:

1. keep the current `api` mode intact
2. add `codex_local` behind an explicit mode flag
3. validate both modes against the same ledger semantics
4. make the OpenClaw/Discord wrapper choose the generation mode by config or
   command option

## Success Criteria

The design is successful only if all of the following are true:

- the Discord/OpenClaw UX is materially unchanged
- `codex_local` shares the same acceptance contract as `api`
- queue artifacts are enough to resume and debug a run
- blocked runs fail loudly and precisely
- a finished run still ends with packaged files and automatic completion
  notification
