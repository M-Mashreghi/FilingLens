# FilingLens Architecture

FilingLens is a modular SEC filing intelligence system that detects and explains economically relevant disclosure changes while preserving exact evidence and point-in-time reproducibility.

## High-Level Overview

```text
                        ┌──────────────────────┐
                        │ Streamlit Dashboard  │
                        └──────────┬───────────┘
                                   │
                        ┌──────────▼───────────┐
                        │      FastAPI API     │
                        └──────────┬───────────┘
                                   │
                     ┌─────────────▼─────────────┐
                     │ Research Workflow / Runs  │
                     └─────────────┬─────────────┘
                                   │
       ┌───────────────────────────┼───────────────────────────┐
       ▼                           ▼                           ▼
┌──────────────┐          ┌──────────────────┐       ┌──────────────────┐
│ SEC Gateway  │          │ Diff / XBRL /    │       │ LLM Intelligence │
│ + Rate Guard │          │ Lineage Analytics│       │ + Reviewer       │
└──────┬───────┘          └────────┬─────────┘       └────────┬─────────┘
       │                            │                          │
       ▼                            └────────────┬─────────────┘
┌────────────────┐                              ▼
│ Filing Package │                     ┌───────────────────┐
│ + Raw Snapshots│                     │ Evidence + Report │
└──────┬─────────┘                     └───────────────────┘
       ▼
┌────────────────┐
│ SQLite Metadata│
└────────────────┘
```

## 1. Core Identity Model

### Issuer

Canonical identity is CIK-centric. Tickers/exchanges are aliases that may change.

### Filing / Submission Package

An accession number identifies a submission package. A package contains:

- primary filing document
- exhibits/supporting documents
- complete submission text
- XBRL/iXBRL artifacts
- images/other attachments

Minimum filing metadata includes CIK, accession, form, filing date, report period, accepted/public timestamp where available, primary document name, amendment flag/lineage, and SEC URLs.

### FilingDocument

Each document inside a package retains sequence/type/filename/description/size/URL plus raw snapshot identity.

## 2. Immutable Provenance

Raw snapshots are immutable and content-addressed/hash-verified. A refetch is a retrieval event; it does not silently mutate the bytes used by a completed research run.

A completed run records:

- exact source snapshot ids/hashes
- focal/comparator accessions
- `as_of`
- parser/diff/calculation versions
- model/prompt/schema versions
- final finding/report version

Corrections create a new run linked to the prior run.

## 3. SEC Access Layer

All SEC traffic flows through one infrastructure gateway with:

- descriptive User-Agent/contact
- configurable fair-access governor
- bounded concurrency
- timeout/backoff
- shared cache
- request telemetry
- optional official bulk-data bootstrap

The UI/browser does not call SEC directly.

See `SEC_DATA_CONTRACT.md`.

## 4. Parsing Layer

The normalizer produces a document model with original/normalized text, stable block ids and source locators.

Dedicated parsers cover:

- 10-K sections/items
- 10-Q parts/items
- 8-K items and exhibit references

Parser uncertainty is explicit. Hidden iXBRL facts are not merged into visible narrative.

## 5. Filing Lineage & Comparator Policy

Default baselines:

- 10-K → prior comparable 10-K
- 10-Q → same fiscal quarter prior year; optional sequential-quarter lens
- 8-K → event-specific context only when meaningful

Amendments are separate filings. The lineage engine can compare:

1. amendment vs original base filing;
2. effective focal filing vs prior comparable period.

Historical runs must not apply an amendment filed after `as_of`.

## 6. Deterministic Change Engine

Before an LLM sees a filing, deterministic code produces:

- section/block alignment
- paragraph/sentence additions/removals/changes
- supported table diffs
- section change magnitude
- exact old/new evidence locators
- rule/topic features for high-value sections/exhibits

The LLM interprets these objects; it does not replace them.

## 7. XBRL Dual-Path Architecture

### Aggregate standardized path

SEC `companyfacts` supports efficient standardized entity-wide trend extraction.

### Filing-specific path

Direct iXBRL extraction preserves:

- custom concepts
- dimensions/axes/members
- hidden/visible status
- filing context ids
- unit/scale/decimals/sign
- exact accession/locator

The normalized fact model keeps both paths distinguishable and reconciles only through explicit rules.

## 8. Taxonomy / Canonical Metric Layer

Canonical metrics (e.g. revenue, cash, debt, capex) are mapped using versioned rules. Taxonomy namespace/version is stored. Custom concepts never silently inherit a standard meaning.

## 9. Deterministic Financial Analytics

Python computes supported growth, margin, cash-flow, leverage, liquidity, working-capital and share-count changes. Every result references source facts and transformation version.

Accounting/disclosure watch signals are neutral research flags, not allegations.

## 10. Retrieval / LLM Intelligence

Retrieval is filtered by accession/package/document/item and `as_of`. Model inputs are bounded evidence plus deterministic change/financial objects.

Findings distinguish:

- source fact
- deterministic calculation
- model interpretation
- open question / unknown

## 11. Evidence Review & Research Priority

Every material finding must pass evidence binding. Wrong-accession citations or unsupported claims block report finalization.

Research Priority Score combines transparent deterministic features and evidence quality. It is not legal materiality.

## 12. Product Layer

FastAPI exposes application services. Streamlit provides issuer search, timelines, comparison workspace, XBRL/finding drill-down, watchlists and alerts. Exports retain run/accession/hash/version metadata.

## 13. Architectural Invariants

1. Accession/package identity is explicit.
2. Raw evidence is immutable.
3. Point-in-time filtering uses public availability, not date-only shortcuts.
4. Amendments never overwrite originals.
5. SEC traffic has one governed gateway.
6. Deterministic math/diffs are separate from LLM interpretation.
7. Filing-specific iXBRL exists alongside aggregate `companyfacts`.
8. Every material finding is evidence-backed.
9. UI remains thin.
10. New infrastructure is justified by a real task, not by fashion.

## 14. Implementation Notes (kept in sync with completed tasks)

### Persistence (Task 1.2)

- Domain entities (`filinglens.domain.models`) are frozen dataclasses with UTC-aware timestamps; naive datetimes are rejected.
- Repository interfaces live in `filinglens.application.ports`; `filinglens.infrastructure.storage.sqlite` implements them. Domain/application code never imports SQLite, HTTP, FastAPI or Streamlit (enforced by an import test).
- Each table stores indexed identity columns plus the full entity as a JSON payload.
- Migrations are forward-only, numbered, append-only and recorded in `schema_migrations`.
- Accession identity: re-adding an accession with identical content is a no-op; `None` fields may be filled by later sources; any conflicting non-null value raises `ConflictError`. A filing discovered under a second CIK (co-registrant) is linked through `filing_issuers`, never duplicated.
- Source snapshots are content-addressed by sha256 and never overwritten.

### Issuer resolution (Task 1.3)

- `filinglens.application.issuer_resolver.IssuerResolver` resolves CIK, ticker, `EXCHANGE:TICKER`, normalized legal name and aliases against a versioned `ReferenceDataset`.
- Outcomes are `resolved`, `ambiguous` (ranked candidates) or `not_found`. Fuzzy name matches are never auto-resolved; a ticker or name mapping to several CIKs is ambiguous. Share classes of one CIK resolve to that CIK.
- Every resolution carries the reference `source`, `source_version` (sha256 of the raw file) and `retrieved_at`; tickers are stored as `SecurityIdentifier` observations, never as durable identity.
- `filinglens.infrastructure.sec.reference` parses SEC `company_tickers_exchange.json` / `company_tickers.json`; network access is injected (the governed SEC client in live mode). `ReferenceDataCache` applies a TTL and records loaded versions.
- `filinglens.domain.identifiers.cik10()` produces zero-padded 10-digit CIKs for API paths; `parse_cik()` keeps the numeric form.

### Filing identity & provenance (Task 1.4)

- `AccessionNumber` parses dashed/no-dash forms; every entity that carries an accession stores the canonical dashed form and repositories normalize lookup keys.
- Archive URL builders and `parse_archive_url` round-trip `(cik, accession, filename)`.
- `Filing.available_at` follows the `availability/1` policy documented in `SEC_DATA_CONTRACT.md` §6; `filter_as_of` applies it.
- Amendments are distinct accessions with `is_amendment` and an optional `amends_accession` (never self-referential); an attempt to re-label an existing accession is a `ConflictError`.
- `SourceSnapshot.from_bytes` captures sha256, size, content type, retrieval time and adapter/version; `verify()` re-checks bytes. Snapshot ids are never overwritten.

### Run lifecycle (Task 1.5)

- `filinglens.domain.run_lifecycle.TRANSITIONS` is the authoritative state machine: the work chain `Created → Resolving → Collecting → Parsing → Aligning → Analyzing → Reviewing → Completed`, with `NeedsInput` (resume into any work state), `BlockedBySource` (resume to Resolving/Collecting), `Reviewing → Analyzing` (reviewer send-back) and `Failed` (requires an error). `Completed` and `Failed` are terminal.
- `RunManifest` (table `run_manifests`) records `as_of`, focal/comparator accessions and reason, snapshot ids, XBRL fact ids, component versions (`parser`, `diff`, `calculation`, `comparator_policy`, `availability_policy`; plus `llm_provider`, `llm_model`, `prompt`, `output_schema` when an LLM is used) and a config fingerprint.
- `RunService.transition(..., COMPLETED)` is refused unless `verify_manifest` finds every snapshot in the store, every evidence span citing a recorded snapshot and all required versions present.
- Completed runs are immutable (service and repository guards). `RunService.supersede` creates a new run linked through `supersedes_run_id`.

### Raw snapshots & document retrieval (Task 2.4)

- `FileSystemRawStore` (under `FILINGLENS_RAW_DIR`) is content-addressed (`sha256/<aa>/<digest>`), write-once (atomic temp-file rename, read-only file mode), verifies hashes on read and refuses to replace a corrupt object.
- `DocumentRetriever` fetches the manifest's unique primary document plus exhibits selected by a versioned `ExhibitPolicy` (per-form document-type prefixes, `max_exhibits`, `max_bytes`, allowed content types). Exhibits are never promoted to primary; if no unique primary exists (or it fails) the complete submission text is used as an explicit, flagged fallback when configured.
- A `SourceSnapshot` row is written only after bytes are stored and re-verified; failed, oversized or disallowed-type retrievals produce a `failed`/`skipped` outcome and no snapshot.

### Normalization (Task 2.5)

- `filinglens.parsing.normalizer.normalize_html` (`normalizer/1`, lxml) emits ordered `Block`s (`heading`, `paragraph`, `list_item`, `table`) with stable ids `blk-<index>-<sha256(kind|text)[:8]>`, an XPath locator (`#runN` suffix for loose text runs between block children), source line, whitespace-normalized `text` and raw `original_text`.
- Scripts/styles/head/comments, link-only "Table of Contents" back-references and bare page numbers are dropped.
- Content under `display:none`, `ix:header` or `ix:hidden` never becomes narrative; tagged hidden facts are returned as `HiddenFact`s. Visible `ix:*` wrappers are unwrapped.
- Tables keep normalized rows: empty spacer cells removed, `$`/`(` merged into the following cell and `)`/`%` into the preceding one.

### 10-K / 10-Q sections (Task 2.6)

- `filinglens.parsing.sections.parse_sections` (`section-parser/1`) works on normalized blocks. Candidates are short, non-table blocks starting with `Item N[A-C]`, `Part I–IV` or `Signatures`; cross-references inside prose never qualify.
- Table-of-contents candidates (tiny bodies followed by a later candidate for the same key) are dropped with a diagnostic. Every section ends at the next chosen item, part marker or signatures heading, so sections cannot swallow later items.
- Status is `ok`, `ambiguous` (competing substantial headings, out-of-canonical-order, empty body; confidence ≤ 0.4) or `missing` (required item not found). 10-Q items are keyed `I-n` / `II-n`; when PART markers are absent the part is inferred from item-number restarts and confidence is reduced.
- `SectionParseResult.analyst("risk_factors" | "mdna" | "market_risk" | "legal_proceedings" | "financial_statements" | "business")` maps analyst names to form-specific items.

### 8-K events (Task 2.7)

- `filinglens.parsing.eight_k.parse_eight_k` (`eight-k-parser/1`) turns `Item X.YY` headings into `EightKEvent`s (text, evidence locators, category derived only from the SEC item number, e.g. 2.02 → `earnings`, 4.02 → `non_reliance`). Events stop at the next item or `SIGNATURES`.
- Exhibit references come from the Item 9.01 exhibit table and `Exhibit NN.N` mentions; they resolve to package `EX-*` documents by numeric exhibit number (`99.1` = `EX-99.01`). Unresolved references are `missing` with a diagnostic; `101`/`104` cover-page XBRL entries are ignored.
- Normalized exhibit documents (e.g. EX-99.1 earnings releases) can be passed in so their block locators become event evidence. The submissions `items` field is cross-checked and mismatches are reported.

### Snapshot dedup, cache & retention (Task 2.8)

- Snapshot identity is the sha256 of the bytes; repeat retrieval of identical bytes reuses the first-captured snapshot (its `retrieved_at` is the first capture).
- Every retrieval (retrieved/failed/skipped, network or cache) is a `RetrievalAttempt` in `retrieval_attempts` (migration 4). Changed bytes for the same document create a new snapshot; `snapshot_versions(accession, filename)` lists them in first-seen order and the old bytes are never overwritten.
- HTTP cache TTL/validators only decide whether the network is used; `from_cache` is recorded on the attempt, never on the snapshot.
- `SnapshotRetentionService` (`retention-policy/1`) pins every snapshot referenced by a completed run's manifest or evidence; purging a pinned snapshot raises `ImmutabilityError`, and `missing_pinned()` reports any pinned snapshot whose raw bytes are absent. Unpinned raw bytes may be purged; metadata rows remain and an identical refetch re-attaches.

### Comparator selection (Task 3.1)

- `filinglens.lineage.comparator.select_comparator` (`comparator-policy/1`): 10-K → latest earlier fiscal-year 10-K (gap 350–380 days = confidence 1.0; irregular or missing years lower confidence with notes); 10-Q → same fiscal quarter of the prior year (fiscal quarter from issuer FYE, 52/53-week aware) or the optional sequential lens (Q1 has none: fiscal Q4 lives in the 10-K); 8-K → previous earnings 8-K only for Item 2.02, otherwise explicitly no baseline.
- Eligibility is `available_at <= as_of`; a focal filing not yet public raises. Excluded candidates and reasons are returned.
- Amendments: `AmendmentPolicy.ORIGINAL` (default) compares against the original filing of the chosen period; `LATEST_AVAILABLE` uses the newest amendment public by `as_of` (flagged as possibly partial). A focal amendment is compared to the prior period like its base form.
- `application.comparison.record_comparator` persists comparator accession, reason (mode, policy, notes) and policy version into the run manifest.
