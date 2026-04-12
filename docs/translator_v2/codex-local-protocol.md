# Translator V2 Codex-Local Protocol

Updated: 2026-04-11

## Purpose

This document defines the queue and artifact contract between
`translator_v2` and Codex workers in `codex_local` mode.

The goal is to keep the worker interface narrow, auditable, and restart-safe.

## Queue Layout

Each run owns a private queue directory.

Suggested layout:

```text
runs/<run_id>/
  manifest.json
  ledger/
    document.json
    chapters/
    segments/
  codex_queue/
    pending/
    in_progress/
    completed/
    failed/
    abandoned/
  artifacts/
    draft/
    reports/
    final/
```

`codex_queue/` is separate from the ledger. Queue state reflects worker traffic.
The ledger remains the source of truth for document state.

## Work Types

Phase 1 needs only two work types:

- `translate_batch`
- `repair_batch`

Optional later types:

- `consistency_review`
- `style_pass`

These later types must not become a prerequisite for coverage correctness.

## Task Record

```json
{
  "task_id": "run123.ch003.batch002.try1",
  "run_id": "run123",
  "generation_mode": "codex_local",
  "backend_label": "codex_local",
  "work_type": "translate_batch",
  "chapter_id": "ch003",
  "segment_ids": ["ch003.s014", "ch003.s015", "ch003.s016"],
  "attempt": 1,
  "task_epoch": 1,
  "created_at": "2026-04-11T22:10:00+09:00",
  "context": {
    "preceding_accepted_tail": "...",
    "following_source_head": "...",
    "glossary_hints": [],
    "protected_spans": []
  },
  "segments": [
    {
      "segment_id": "ch003.s014",
      "source_hash": "...",
      "source_text": "...",
      "separator_after": "\n\n"
    }
  ],
  "instructions": {
    "output_schema": "segment_wrappers_v1",
    "forbid_extra_prose": true,
    "target_language": "ko"
  },
  "repair_reason": null,
  "status": "pending"
}
```

## Result Record

```json
{
  "task_id": "run123.ch003.batch002.try1",
  "run_id": "run123",
  "status": "completed",
  "generation_mode": "codex_local",
  "backend_label": "codex_local",
  "worker_id": "codex-local-1",
  "task_epoch": 1,
  "claim_id": "claim-01",
  "completed_at": "2026-04-11T22:13:52+09:00",
  "raw_output_path": "codex_queue/completed/run123.ch003.batch002.try1.txt",
  "notes": "",
  "result_schema_version": "segment_wrappers_v1"
}
```

The actual translation text should live in a sibling text artifact rather than
inside the JSON result body. That keeps result records readable and avoids large
JSON blobs.

## Worker Claim Semantics

Claiming must be atomic at the file level.

Allowed pattern:

1. move `pending/<task>.json` to `in_progress/<task>.json`
2. append worker metadata, `claim_id`, and current `task_epoch`
3. write result artifacts only under the same run queue

The worker must never:

- edit ledger files directly
- overwrite final outputs
- mutate another worker's claimed task
- requeue tasks by itself

Requeueing belongs to the orchestrator only.

## Claim Epochs and Late Results

Every emitted task carries a monotonic `task_epoch`.

Rules:

- the first emission for a task lineage starts at `task_epoch = 1`
- any stale-task re-emission increments `task_epoch`
- every result must echo both `task_id` and `task_epoch`
- every claim writes a unique `claim_id`
- the orchestrator accepts a result only if `task_id`, `task_epoch`, and
  `claim_id` match the currently active claim record

If a stale worker writes a late result after the task was abandoned and
re-emitted, that result is quarantined as a stale artifact and never ingested
into the ledger.

## Heartbeats and Staleness

To support long tasks, each claimed task may have a lightweight heartbeat file.

Suggested artifact:

```text
codex_queue/in_progress/<task_id>.heartbeat.json
```

Fields:

- `worker_id`
- `updated_at`
- `phase`

If no heartbeat or completion is observed within the configured timeout window,
the orchestrator may mark the task stale and move it to `abandoned/` before
re-emitting a replacement task.

The replacement task must use a higher `task_epoch` so any late result from the
abandoned claim is mechanically rejectable.

## Worker Output Contract

Workers must return only strict wrapper output.

Example:

```text
[[SEGMENT ch003.s014]]
...
[[END SEGMENT ch003.s014]]

[[SEGMENT ch003.s015]]
...
[[END SEGMENT ch003.s015]]
```

Rules:

- segment IDs must match the requested set exactly
- order must remain the same as requested
- extra explanation outside wrappers is invalid
- if a worker cannot safely finish, it should fail the task rather than improvise

## Failure Semantics

There are two different failure classes:

### Worker-level failure

Examples:

- worker crashes
- worker times out
- output file missing
- malformed wrapper output
- result arrives with stale `task_epoch` or mismatched `claim_id`

These fail the task, not the whole document, unless repair budget or stale-task
budget is exhausted.

### Document-level failure

Examples:

- unresolved critical validation errors remain
- too many repair attempts for one segment lineage
- ledger and queue states become irreconcilable

These block the run.

## Accepted Segment Provenance

Queue artifacts may be pruned later, so accepted backend provenance must be
copied into the segment ledger.

Each accepted segment should persist:

- `generation_mode`
- `backend_label`
- `worker_id`
- `accepted_task_id`
- `accepted_task_epoch`
- `accepted_claim_id`

In hybrid mode, reports must read backend provenance from these ledger fields,
not from queue directories.

## Segment Lineage

If repair splits a segment, the queue protocol must carry the child IDs
explicitly.

Example:

- parent: `ch003.s014`
- children: `ch003.s014.a`, `ch003.s014.b`

The worker does not invent lineage. It only consumes the IDs already specified
by the orchestrator.

## Review Tasks

If a later phase adds review tasks, they must remain advisory until they produce
machine-actionable repair tasks.

Allowed:

- "segment `x` has probable honorific inconsistency"

Not allowed:

- "overall translation seems fine, ship it"

This keeps review subordinate to the ledger and validation model.

## OpenClaw Boundary

The queue protocol is intentionally filesystem-only.

That means:

- OpenClaw does not need to parse translation payloads
- worker tasks can be debugged from disk after a failure
- Discord integration stays focused on progress, completion, and delivery

## Security and Safety Rules

- queue paths must stay under the translator workspace
- worker instructions must forbid reading unrelated secrets by default
- result ingestion must treat all worker output as untrusted until parsed and
  validated
- completion notification must come from the orchestrator, not from workers

## Resume Rules

On restart:

1. reload manifest and ledger
2. inspect queue directories
3. reconcile `in_progress`, active claims, and heartbeat freshness
4. keep accepted segments
5. re-emit only unresolved tasks

Resume logic keys off ledger state first, queue state second.

## Minimal Phase-1 Operating Rule

Phase 1 should support one run, one queue, and one or more independent Codex
workers with no shared mutable state beyond atomic task-claim moves.

If that invariant breaks, the design has become more complex than necessary for
the first release.
