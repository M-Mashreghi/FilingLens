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

### Amendment & restatement lineage (Task 3.2)

- `filinglens.lineage.amendments` (`lineage/1`): `link_amendment` uses an explicit `amends_accession`, else the unique earlier original with the same base form and report period (confidence 0.95). Multiple candidates or none leave the amendment unlinked with ranked candidates — no guessing.
- `LineageEngine.period(base, as_of)` returns original, amendments public at `as_of` and the `effective` version; before an amendment's `available_at` the original state is returned.
- `restatement_signals(as_of)` surfaces 8-K Item 4.02 non-reliance filings and 10-K/A / 10-Q/A amendments with neutral wording.
- Amendment-vs-original (`amendment_only_changes`, by block fingerprint) and effective-vs-prior-period (comparator policy) are separate lenses.

### Section alignment (Task 3.3)

- `filinglens.diff.alignment.align_documents` (`alignment/1`) aligns sections by SEC item key only; blocks are never matched across different items.
- Within a section: identical fingerprints → `identical`; identical after year/date/number normalization (`boilerplate_key`) → `modified` with similarity 1.0; otherwise greedy same-kind similarity (max of token Jaccard and sequence ratio on normalized text, threshold 0.55) → `modified`; leftovers → `added` / `removed`.
- Reorders are flagged `moved` (pairs outside one longest increasing subsequence). A match whose runner-up is within 0.03 similarity is `ambiguous` with confidence ≤ 0.5. Missing/ambiguous sections are carried into notes and statuses.

### Text & table diff (Task 3.4)

- `filinglens.diff.text_diff.diff_documents` (`diff/1`) converts an alignment into `Change`s (`added` / `removed` / `modified`) with stable ids `chg-<sha256[:16]>` and `EvidenceRef`s (snapshot id, block id, locator, exact text) for both sides.
- Modified paragraphs carry sentence-level edits (abbreviation-aware splitter + sequence matching); unchanged sentences are not reported.
- Tables are diffed by row label (first non-numeric cell): added/removed rows and per-column cell changes. Tables with duplicate row labels are marked `supported=False` with a reason instead of producing a guessed cell diff.
- `SectionStat` reports old/new chars, length delta, word-level change magnitude (1 − sequence ratio) and change counts.

### Change detectors (Task 3.5)

- `filinglens.diff.detectors` (`detectors/1`) emits `ChangeSignal`s with topics, a language direction (`new`, `removed`, `strengthened`, `softened`, `unknown`), a reason and the old/new evidence of the source change.
- Risk Factors: every substantive added/removed/modified block becomes a candidate; direction for modifications compares intensifier vs softener counts in the changed sentences and returns `unknown` for no or mixed signals.
- MD&A/liquidity topics (demand, pricing, costs, margins, capex, liquidity, debt, customer/supplier concentration) and 8-K/exhibit topics (earnings, guidance, financing, acquisition/disposition, material agreement) come from versioned lexicons; 8-K signals carry primary and exhibit evidence locators.
- Formatting-only edits (same alphanumeric content) and pure year roll-forwards are suppressed by detectors (the diff engine still records them).

### Research priority features (Task 3.6)

- `filinglens.diff.priority_features.build_features` (`priority-features/1`) turns each `ChangeSignal` into a `FeatureVector` whose features are listed with range and meaning in `FEATURE_SPECS`: section importance, novelty, change magnitude, topic breadth, direction score, amendment/restatement flags, exhibit weight, cross-section recurrence, related XBRL signal, parser confidence, alignment confidence and evidence quality.
- All values are in [0, 1]; a feature that cannot be computed is `None` (missing is not zero). Vectors are deterministic and carry explanations. Features rank analyst attention only and never encode legal materiality.

### XBRL fact model & direct iXBRL extraction (Tasks 4.1–4.2)

- `filinglens.domain.xbrl.XbrlFact` is shared by both paths and keeps `source` (`companyfacts` vs `ixbrl`), prefix/concept/namespace, standard-vs-custom status (prefix outside the standard taxonomy set), unit, Decimal value or text, period (instant vs duration), decimals/scale/sign, sorted `(axis, member)` dimensions, context id, hidden flag, locator and a content-hash `fact_id`.
- `filinglens.xbrl.ixbrl.extract_ixbrl` (`ixbrl-extractor/1`, namespace-aware lxml, no network/entity resolution) reads `xbrli:context` (period, explicit and typed members) and `xbrli:unit` (incl. divide units such as `USD/shares`), then every `ix:nonFraction` / `ix:nonNumeric`. Scale and `sign="-"` are applied; `ixt:fixed-zero` / dashes are zero; `xsi:nil` is `None` (missing, not zero). Facts inside `ix:hidden` keep `hidden=True`. Locators are lxml element paths (`tree.find(locator)`).
- Dimensional facts stay separate from entity-wide facts; unknown contexts/units or unparseable numbers are reported in `errors`, never silently dropped.

### XBRL normalization (Task 4.3)

- `filinglens.xbrl.normalize.FactSet` (`xbrl-normalizer/1`) keys facts by (qname, normalized unit, period start/end, dimensions) and classifies periods (`instant`, `quarter`, `half_year_ytd`, `nine_months_ytd`, `annual`, `other_duration`) with fiscal-quarter metadata from the issuer FYE.
- Units are normalized (`iso4217:USD` → `USD`, `xbrli:shares` → `shares`, divide units kept as `A/B`) while the original unit is retained.
- Duplicate policy `duplicates/consistent-within-coarsest-decimals`: duplicates that agree after rounding to the coarsest declared decimals resolve to the most precise fact; disagreeing duplicates are `conflict` with no value. Several units for one key are a `conflict` unless a unit is requested.
- Lookups never collapse dimensions (entity-wide lookups only see dimensionless facts) and return `missing` with `value=None` for nil/absent facts — never zero.

### Taxonomy mapping & drift (Task 4.4)

- `filinglens.xbrl.taxonomy` (`concept-map/1`): `taxonomy_version()` reads prefix/year from the fact namespace. `STANDARD_RULES` map standard concepts to canonical metrics (revenue, cost of revenue, gross profit, operating income, net income, operating cash flow, capex, cash, debt, receivables, inventory, current assets/liabilities, shares, diluted EPS) with rule ids, priorities and optional taxonomy-version validity (e.g. `SalesRevenueNet` only through 2018).
- Custom extension concepts map only through an issuer-scoped `CustomMappingRule` that must carry evidence; dimensional facts never map to entity-wide metrics.
- `detect_drift` reports taxonomy version changes per prefix and changes in the concepts used for a metric between two filings.

### Financial trend engine (Task 4.5)

- `filinglens.analytics.trends` (`trend-engine/1`): `CanonicalStore` resolves canonical metric values from entity-wide facts via `ConceptMapper` (lowest rule priority wins) and `FactSet` duplicate policy, optionally restricted to `eligible_accessions` (filings public at `as_of`).
- `TrendEngine.compute(PeriodPair)` returns `MetricResult`s with formula, inputs (value, period, unit, fact ids, accession) and version: revenue/net income/OCF/receivables/inventory/diluted-share growth, gross and operating margins and deltas (gross profit may be derived as revenue − cost of revenue, recorded as `derived:`), OCF, capex, FCF, cash and long-term-debt change, working capital, current ratio and net debt.
- Conventions: Decimal arithmetic, ratios quantized to 1e-6; growth = (cur − prior)/|prior| (undefined when prior = 0); margin deltas are fraction differences; capex is the positive payment and FCF = OCF − capex; balance-sheet values are instants at period ends; mismatched units are refused; missing inputs give `insufficient_data` with the missing names.

### Accounting / disclosure watch signals (Task 4.6)

- `filinglens.analytics.watch_signals` (`watch-rules/1`) documents each rule's formula and threshold in `RULES`: receivables vs revenue growth (> 0.10), inventory vs revenue growth (> 0.10), OCF vs net income growth (< −0.20), cash decline with higher long-term debt, dilution (> 3%), large revenue change (|growth| > 50%), accounting-policy/critical-estimate text changes (with diff change ids) and restatement-related disclosures/amendments (with accessions).
- Signals are `flagged`, `not_flagged` or `unknown` (insufficient data — never treated as not flagged). Descriptions are neutral follow-up prompts; a test bans accusatory wording.

### Narrative ↔ numeric reconciliation (Task 4.7)

- `filinglens.analytics.reconciliation.reconcile` (`reconciliation/1`) maps MD&A topics to canonical metrics with a polarity (`TOPIC_METRICS`: demand → revenue growth, pricing/margins → margin deltas, costs → gross-margin delta inverted, liquidity → cash change, debt → long-term-debt change).
- Narrative direction comes from up/down lexicons in the new text of changed blocks; numeric direction from the deterministic metric sign with small neutral bands. Results are `aligned`, `tension`, `mixed` or `insufficient_data` (unmapped topic, unavailable metrics, or indeterminate narrative) and name the driving metrics.
- Each result keeps the narrative evidence refs and the original `MetricResult`s (fact lineage); nothing is recalculated.

### Retrieval index (Task 5.1)

- `filinglens.retrieval.index.RetrievalIndex` (`retrieval/1`) chunks each document by section then block (max 1,500 chars, never across sections or documents). Chunks keep accession, filename, doc type, section key, block ids, locators, snapshot hash and `available_at`.
- `RetrievalFilter` requires a non-empty accession set and an `as_of`; optional section/document/doc-type filters. Filters are applied before scoring and re-checked on results, so bounded queries cannot return out-of-filter evidence.
- Scoring is a deterministic BM25 baseline computed over the filtered candidates only; embedding scorers can be swapped in through the `Scorer` protocol (no vector database in v1).

### Structured LLM client (Task 5.2)

- `filinglens.llm.client.StructuredLLMClient` builds a bounded prompt (`filing-change-analyst/1`) from `EvidenceItem`s wrapped in `<evidence>` blocks (closing tags in content are escaped; the system prompt declares evidence untrusted) and requires JSON matching the pydantic `FindingsEnvelope` (`finding-schema/1`: finding type, document/section, direction, severity, confidence, summary, why it matters, evidence ids, open questions, interpretation status; extra fields forbidden).
- Invalid JSON, schema violations or provider exceptions return `status=failed` with no findings (fail closed, never partial).
- Every call records provider, model, prompt version, schema version, request id, token usage and cost. `FakeProvider` is the deterministic offline provider used in mandatory CI; paid providers implement `LLMProvider` and stay optional.

### Analyst workflow (Task 5.3)

- `filinglens.llm.workflow.build_evidence` turns change signals (old/new text), OK metrics (value, formula, accessions, engine version), flagged watch signals, reconciliations and lineage notes into bounded, id-addressed `EvidenceItem`s (default cap 60).
- `FilingChangeWorkflow` (`analyst-workflow/1`) picks a form-specific task (10-K, 10-Q, 8-K, amendment), calls the structured client and reviews each finding: basis (`source_fact` vs `deterministic_calculation`; the model text is interpretation), unknown evidence ids flagged, no valid evidence → `not_supported`, numbers absent from cited evidence → `uncertain` + `numeric_unverified`, uncited "management said/expects" claims → `not_supported`.
- An LLM failure returns `failed` with no findings while keeping the deterministic evidence list; no evidence means no model call.

### Evidence validation & Research Priority (Task 5.4)

- `filinglens.llm.validation.validate` (`evidence-validator/1`) blocks a finding when cited evidence has no provenance binding, points to a snapshot outside the run manifest, a wrong accession, a section different from the finding's item, or a calculation without (in-run) source fact ids; also when the interpretation is `not_supported`, numbers are unverified, a management claim is uncited, or an optional `Reviewer` model rejects it.
- Opposite directions for the same finding type and section are flagged as contradictions (reason code `contradiction_flagged`).
- `priority_score` (`research-priority/1`) is a weighted mean of the Task 3.6 features (missing features renormalized away) scaled by model confidence, 0–100, with top-contributor reason codes. It is labelled as an analyst-attention ranking, not legal materiality, and blocked findings get no score.

### Cited report & bounded Q&A (Task 5.6)

- `filinglens.reporting.report.render_report` (`report/1`) renders a deterministic Markdown report from `ReportInputs`: metadata, executive summary, top ranked findings, Risk Factors, MD&A/liquidity, 8-K events/exhibits, amendment/restatement notes, XBRL signals, narrative–numeric tensions, open questions, methodology (disclaimer, blocked-finding count, component versions, snapshots) and an evidence appendix. Only accepted findings appear; any uncited or dangling citation raises `ReportIntegrityError`.
- `ReportService` stores inputs per run (`report_inputs`, migration 5) so `regenerate(run_id)` reproduces the identical report; `finalize` requires an approving `ReviewDecision` and creates a new report version.
- `filinglens.reporting.qa.answer_question` (`bounded-qa/1`) retrieves under the run's accession filter and `as_of`; no relevant evidence → `not_supported` without a model call. Answers must cite retrieved chunk ids only and may not contain numbers absent from the cited text.

### API & dashboard (Tasks 6.1–6.2)

- `filinglens.bootstrap.Container` is the composition root (SQLite repositories, run/metadata/report services, optional issuer reference data). It lives outside the application layer so inner layers never import infrastructure.
- FastAPI (`docs/API.md`) and Streamlit are thin: controllers and `filinglens.ui.viewmodels` only shape data from the container. The dashboard covers issuer search with explicit ambiguity choice, filing timeline with form filters, run creation/status (Failed / NeedsInput / BlockedBySource highlighted with messages) and findings with evidence drill-down (missing evidence is shown, not hidden). Tests run the Streamlit script headless with `AppTest` and enforce that UI modules import no parsing, analytics, XBRL, infrastructure or HTTP code.

### Comparison workspace (Task 6.3)

- `filinglens.ui.workspace.build_workspace` (`workspace/1`) shapes already-computed artifacts — `DiffResult`, `EightKParseResult`, `PeriodLineage`, `MetricResult`s and findings — into panels: current vs comparator metadata, per-section tabs with added/removed/modified counts and magnitude, side-by-side old/new text with exact locators and sentence edits, table cell/row changes, 8-K items with resolved/missing exhibits, amendment lineage (original, amendments, effective), XBRL metric formula/fact lineage, and finding → evidence → section/change navigation. It never recomputes anything.
- `src/filinglens/ui/pages/comparison.py` renders a prepared workspace from session state (headless `AppTest` covered).

### Watchlists & new-filing workflow (Task 6.4)

- `Watchlist` (cik, forms, `since`) selects filings whose `available_at` is after `since` (no backfill flood on creation). Stored in `watchlists`; per-accession state in `filing_reservations` (migration 6) with status (`reserved` / `enqueued` / `failed`), run id, attempts, last error and `available_at`.
- `WatchlistPoller.poll_once` (`watchlist-poller/1`) fetches submissions through the governed adapter, ingests idempotently, atomically reserves each new accession (`INSERT OR IGNORE` inside an immediate transaction — concurrent pollers on separate connections cannot both win), then creates exactly one run with a deterministic id `wl-<sha256(watchlist|accession)>`. Run-creation failures mark the reservation `failed` and are retried on the next poll; a crash between run creation and marking is healed because the deterministic run id is reused. Source outages reserve nothing.

### Alerts & triage (Task 6.5)

- `filinglens.application.alerts.AlertService` (`alerts/1`) raises `new_filing`, `high_priority_finding` (accepted findings at or above a research-priority threshold), `restatement_signal` and operational (`source_failure`, `parser_failure`, `model_failure`) alerts. Dedupe keys (`new_filing:<acc>`, `finding:<run>:<type>:<section>`, `restatement:<acc>`, `op:<kind>:<run|error>`) suppress duplicates; research alerts must link to a run and `deep_link()` points at the run/finding (operational alerts carry the error).
- `triage_queue()` lists unacknowledged alerts by priority then age; `acknowledge()` updates state.
- Optional `HttpWebhookNotifier` (`infrastructure/notify/webhook.py`, refuses SEC hosts) is called after the alert is stored; delivery failures are recorded in `deliveries` and never roll back alert or analysis state.

### Export & artifact packaging (Task 6.6)

- `filinglens.reporting.export` (`export-schema/1`) produces Markdown (the report), versioned JSON validated by the pydantic `ExportDocument` (run, provenance: snapshot ids, component/calculation/prompt/model versions, report version, inputs hash, blocked-finding count; findings with resolved citation locators; research-priority note), a CSV findings table with citation locators, and escaped printable HTML.
- Exports refuse any finding without resolvable citations, so no model-only claim can leave the system. `package_artifacts` writes all formats plus `manifest.json` with sha256 per file; exports are deterministic.

### End-to-end pipeline (Task 7.1)

- `filinglens.application.pipeline.AnalysisPipeline` (`pipeline/1`) wires every stage with injected adapters: submissions → comparator selection (recorded in the manifest) → package manifests → governed retrieval into the raw store → normalization, 10-K/10-Q sections or 8-K events → iXBRL extraction → alignment/diff → detectors, trend engine, watch rules, reconciliation, priority features → LLM workflow → evidence validation → persisted findings/evidence → staged report → optional reviewer finalization (`Completed`).
- Source problems move the run to `BlockedBySource`, missing structure or rejected model output to `NeedsInput`, an unpublished focal filing to `Failed`; each raises an operational alert and produces no report.
- `tests/e2e` drives the whole chain offline (mock SEC transport serving a synthetic issuer's submissions, index pages, iXBRL 10-Ks, a 10-K/A, 8-Ks with exhibits; scripted LLM): happy 10-K, 8-K + exhibit, amendment lineage, source failure, malformed model output, unsupported finding blocked, point-in-time exclusion and report reproducibility, with provenance assertions (snapshot hashes re-verified from the raw store).

### Observability (Task 7.2)

- `filinglens.observability` (`telemetry/1`): `log_context(...)` binds `correlation_id`, `run_id`, `accession`, `cik` and stage to every record through a `ContextFilter` installed by `configure_logging`; JSON logs scrub secrets (key/value patterns, bearer tokens, `sk-` keys, sensitive extra fields).
- `Telemetry.stage()` records per-stage counts, errors and timings; errors are categorized (`source`, `parser`, `alignment`, `model`, `validation`, `internal`). The pipeline logs every run transition and `run stopped` with category and error, so a failed run is traceable end-to-end by `run_id`/`correlation_id`.
- API probes: `/healthz` (liveness), `/ready` (database reachable at the latest schema, raw store writable; 503 otherwise) and `/health/detailed` (schema version, runs by state, pending alerts, telemetry, SEC client request/cache/rate-limit metrics when wired; never secrets).

### Backup, restore & drill (Task 7.5)

- `filinglens.infrastructure.storage.backup` (`backup/1`): consistent SQLite copy via the online backup API, raw objects copied and re-hashed, manifest with database hash, per-object hashes, schema version and completed runs; `verify_backup`, `restore_backup` (empty targets only, hash-checked) and `prune_backups` retention.
- `filinglens.application.restore_drill.restore_drill` reopens every completed run after restore: manifest verification, snapshot re-hash from the restored raw store, evidence → manifest snapshot checks and byte-identical report regeneration from stored inputs. The e2e test restores a completed pipeline run and requires the drill to pass; corrupted or missing objects are detected. Procedures are in `docs/RUNBOOK.md` §16.
