# OCR/PDF-ish TXT Critical Review

This review captures the main objections raised during design review and the accepted mitigations.

## Blockers Raised

### 1. Ingest split across entry points

Risk:
- `.txt` repair in one path and raw text in another would break alignment and review consistency.

Mitigation:
- use [`process_input()`](/home/hoonsw/dev/translator/input_pipeline.py) as the authoritative ingest path for both v2 and legacy `translate.py`
- persist the chosen ingest state in ledger metadata

### 2. Cleanup can be destructive on clean prose

Risk:
- header/footer stripping and line merging can damage lists, dialogue, poetry, citations, and mixed-language text.

Mitigation:
- conservative raw-signal thresholds
- candidate comparison against `normalize_txt()`
- reject cleanup candidates that lose too much content or fail to materially reduce layout artifacts

### 3. Separator and heading semantics can collapse

Risk:
- aggressive repair can flatten line structure before segmentation.

Mitigation:
- keep the initial implementation ingest-only
- avoid new separator logic in segmenter for this phase
- add regression tests for heading/list-like text and keep cleanup conservative

### 4. Versioning/resume drift

Risk:
- changing cleanup policy without fingerprinting it would make resumed ledgers unreliable.

Mitigation:
- add `ingest_version` to v2 config and fingerprinting
- persist ingest metadata in the ledger

## Explicitly Deferred

- automatic source-language override
- new readability gate
- full document-layout reconstruction

Those are valuable, but they are larger than the safe first fix and would add more regression surface than this patch should carry.
