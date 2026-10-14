# FilingLens Operational Runbook

## 1. Health

Implemented endpoints (see also *Diagnostics* below):

- `/healthz` — process alive
- `/ready` — DB/storage/config usable
- `/health/detailed` — component diagnostics without secrets

## 2. SEC Outage / 403 / 429 / Timeout / 5xx

Runs hit by an SEC outage stop in `BlockedBySource` with a `source_failure` alert; resume them (`BlockedBySource → Collecting`) once SEC recovers instead of re-running from scratch.


1. locate run/accession and request telemetry;
2. verify configured descriptive SEC User-Agent/contact;
3. inspect current governor rate/concurrency and cache hit rate;
4. do not tight-loop or add bypass behavior;
5. reduce concurrency/rate if needed;
6. respect backoff and retry only bounded transient cases;
7. for large historical work, switch to official bulk bootstrap where suitable;
8. record persistent external outage separately from parser/data defects.

## 3. Wrong Issuer / CIK

Stop analysis, show ambiguity, inspect mapping source/version, require explicit selection if unresolved, then add a resolver regression case. Never silently guess.

## 4. Filing Package / Primary Document Error

If an exhibit was mistaken for primary or an important exhibit was missed:

1. preserve the original package manifest;
2. inspect sequence/type/filename/description;
3. fix classification policy;
4. add the accession package as a regression fixture/minimized case;
5. create a corrected run rather than mutating completed research.

## 5. Raw Snapshot Hash Mismatch

Never overwrite. Retain retrieval metadata, compare raw bytes/source URLs, identify whether content/endpoint/path changed, and create explicit new snapshot/retrieval relation if warranted.

## 6. 10-K / 10-Q Parser Failure / Parser Drift

Parser drift (new filing layouts breaking boundaries) shows up as `ambiguous`/`missing` sections, `NeedsInput` runs with `parser_failure` alerts and a drop in `section_success_rate` in `scripts/run_eval.py`.


Reproduce against exact raw snapshot, inspect boundaries, add regression fixture, prefer general structural fix over issuer-specific hack, and return explicit ambiguity if still uncertain.

## 7. 8-K / Exhibit Parser Failure

Check item boundaries, exhibit-reference table, package manifest and exhibit availability. Do not infer event detail from an exhibit that was not retrieved. Add fixture coverage.

## 8. Wrong Comparator

Invalidate the affected run, record focal/comparator accessions and fiscal metadata, fix comparator policy, add regression test, and create a new corrected run.

## 9. Amendment / Restatement Lineage Incident

If original/amended history is mixed:

1. freeze the affected run;
2. verify accepted/public times;
3. rebuild original→amendment lineage;
4. verify historical `as_of` view excludes later amendment;
5. separately analyze amendment-vs-base and period-vs-period changes;
6. never overwrite historical raw evidence.

## 10. XBRL Mismatch

Inspect taxonomy/version, concept, custom-vs-standard status, context, dimensions, unit, period, scale/decimals/sign and accession. Compare aggregate `companyfacts` with direct filing iXBRL when relevant. Never patch report prose to hide a numeric bug.

## 11. Taxonomy-Version Regression

When a new SEC-supported taxonomy breaks mappings:

1. identify namespace/version change;
2. preserve old mapping behavior for historical runs;
3. add version-specific mapping/fixture;
4. rerun evaluation;
5. do not silently reinterpret old reports.

## 12. LLM Malformed / Unsupported Output (LLM failures)

The pipeline moves the run to `NeedsInput` with a `model_failure` alert; no partial findings are stored.


Use bounded retry if policy allows. Schema failure becomes structured failure/uncertain. Never publish free-form provider output as an approved finding.

## 13. Citation / Evidence Failure

A material unsupported factual claim blocks finalization. Verify evidence id → snapshot → accession → item/document → locator; repair retrieval/analysis/composition or remove/downgrade the claim.

## 14. Point-in-Time Incident

Mark run invalid, record `as_of` and offending source availability time, add regression, fix filter, and create a corrected run. Never rewrite audit history.

## 15. Watchlist Duplicate / Missed Filing

For duplicates, inspect accession reservation/idempotency/concurrency. For missed filings, inspect submissions metadata, form filters and scheduler state; backfill by accession safely.

## 16. Backup / Restore

Implemented in `filinglens.infrastructure.storage.backup` (`backup/1`) and `filinglens.application.restore_drill`.

Backup (can run while the API is up; SQLite online backup API):

```python
from datetime import UTC, datetime
from pathlib import Path
from filinglens.infrastructure.storage.backup import create_backup, prune_backups

create_backup(
    Path("/app/data/filinglens.db"),
    Path("/app/data/raw"),
    Path("/backups/2026-10-14"),
    created_at=datetime.now(UTC),
)
prune_backups(Path("/backups"), keep=14)
```

The backup manifest records the database hash, every raw object hash, schema version and completed run ids; raw objects are re-hashed during backup.

Restore:

1. stop writers (API, UI, poller) and preserve the current state for forensics;
2. `verify_backup(dir)` — every hash must match;
3. `restore_backup(dir, db_path, raw_dir)` into empty targets (it refuses to overwrite);
4. run the **restore drill**: `restore_drill(repos.runs, repos.snapshots, repos.evidence, FileSystemRawStore(raw_dir), ReportService(repos.reports, repos.report_inputs))` — it must report `passed`: every completed run's manifest verifies, every snapshot re-hashes from the restored raw store, every evidence span cites a manifest snapshot, and each finalized report regenerates byte-for-byte from its stored inputs;
5. start services and check `/ready`.

Retention: keep at least the last 14 daily backups; raw snapshots pinned by completed runs must never be purged (`SnapshotRetentionService`).

## 17. Release-Day Checklist

- CI green
- SEC governor tests green
- parser + 8-K/exhibit fixtures green
- amendment lineage tests green
- point-in-time tests green
- XBRL custom/dimension/taxonomy fixtures green
- evidence/reviewer tests green
- golden evaluation compared to baseline
- no secrets in repo/image
- SEC user-agent configured
- health/readiness green
- backup/restore verified
- release qualification record stored


## Diagnostics (implemented)

- Liveness: `GET /healthz`. Readiness: `GET /ready` (database schema current, raw store writable). Detail: `GET /health/detailed` (runs by state, pending alerts, stage telemetry, SEC request/cache/429/403 counters).
- Trace a run: filter JSON logs (`FILINGLENS_LOG_JSON=true`) by `run_id` or `correlation_id`; `run stopped` records carry `error_category` (`source`, `parser`, `model`, ...) and the error text. Operational alerts with the same run id appear in the triage queue.
