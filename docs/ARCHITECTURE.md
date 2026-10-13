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
