# FilingLens — Project Phases & Atomic Tasks

**Single source of truth** for implementation.

Status: `[ ]` Not Started · `[-]` In Progress · `[x]` Completed

Related: [ARCHITECTURE](./ARCHITECTURE.md) · [SEC_DATA_CONTRACT](./SEC_DATA_CONTRACT.md) · [EVALUATION](./EVALUATION.md) · [ROADMAP](./ROADMAP.md) · [PHASES](./PHASES.md) · [DIAGRAMS](./DIAGRAMS.md) · [TASKS](./TASKS.md)

## Global Definition of Done

Before any task is marked complete:

1. Scope implementation exists.
2. Relevant unit/integration/regression tests are green.
3. Provenance and point-in-time invariants are preserved.
4. No unrelated refactor is mixed in.
5. Secrets are external and logs are safe.
6. External calls have bounded timeout/backoff/rate behavior.
7. New LLM outputs are schema/evidence validated.
8. Documentation and ledgers are synchronized.

---

# Phase 1 — Foundation, Issuer Identity & Provenance

## Task 1.1 — Python Project Scaffold & Quality Baseline

**Status:** `[x]` Completed

**User Story**  
Create a clean Python project with stable module boundaries, configuration, logging, tests, and developer commands.

**Specification**

- Create `src/filinglens` package and `tests/` layout.
- Add `pyproject.toml`, `.gitignore`, `.env.example`, dependency lock strategy.
- Add FastAPI and Streamlit shells with no business logic.
- Configure formatting/linting/type-check/test commands.
- Add typed settings and structured logging bootstrap.
- Add import/health smoke tests.

**Definition of Done**

- [x] fresh install succeeds
- [x] package imports and health smoke passes
- [x] quality commands are documented and green
- [x] no secrets committed
- [x] layout matches architecture boundaries

## Task 1.2 — Core Domain Models & SQLite Persistence

**Status:** `[x]` Completed

**User Story**  
Persist issuers, filing packages, snapshots, runs, findings, evidence, alerts, and reports without coupling domain logic to SQLite.

**Specification**

- Domain models: `Issuer`, `SecurityIdentifier`, `Filing`, `FilingDocument`, `SourceSnapshot`, `ComparisonRun`, `Finding`, `EvidenceSpan`, `Alert`, `Report`.
- Unique accession-number invariant per filer/submission identity.
- Repository interfaces in application layer.
- SQLite implementation and migration policy.
- UTC timestamps and explicit nullable fields.

**Definition of Done**

- [x] round-trip integration tests pass
- [x] duplicate accession handled idempotently
- [x] domain imports do not depend on FastAPI/Streamlit/SQLite implementation

## Task 1.3 — Issuer / CIK / Ticker Resolver

**Status:** `[x]` Completed

**User Story**  
Resolve ticker, CIK, exchange, or company name into a canonical SEC filer without silently guessing.

**Specification**

- Resolve by CIK, ticker, exchange+ticker, normalized legal name, and aliases.
- Use SEC ticker/exchange reference data behind an adapter.
- Preserve ambiguity with ranked candidates.
- Cache mapping with retrieval timestamp/version.
- Canonicalize CIK to zero-padded 10 digits for API paths while retaining numeric form where needed.

**Definition of Done**

- [x] ticker, CIK, name, alias, ambiguity fixtures pass
- [x] mapping source/version retained
- [x] ticker is never treated as permanent global identity

## Task 1.4 — Filing Identity, Availability & Immutable Provenance Model

**Status:** `[ ]` Not Started

**User Story**  
Make every filing and source payload auditable by accession, public-availability time, source, and hash.

**Specification**

- Normalize accession numbers with dashed and no-dash representations.
- Store form, filing date, report period, accepted timestamp, primary document, SEC URLs.
- Define `available_at` policy using accepted/disseminated time when available, not filing date alone.
- Store retrieval timestamp, byte hash, content type, size, source adapter/version.
- Represent amendments as distinct filings.
- Never silently overwrite immutable raw snapshot identity.

**Definition of Done**

- [ ] provenance/hash tests pass
- [ ] future availability is excluded by `as_of` filters
- [ ] amendment cannot overwrite original
- [ ] accession/URL normalization round-trips

## Task 1.5 — Analysis Run Lifecycle & Reproducibility Manifest

**Status:** `[ ]` Not Started

**User Story**  
Track analysis from request to reviewed report with legal transitions and a complete reproducibility manifest.

**Specification**

- States: `Created`, `Resolving`, `Collecting`, `Parsing`, `Aligning`, `Analyzing`, `Reviewing`, `Completed`, `NeedsInput`, `BlockedBySource`, `Failed`.
- Persist `as_of`, focal/comparator accessions, parser/config versions, prompt/model/schema versions, retrieval snapshot ids, deterministic calculation version.
- Reject illegal transitions.
- Completed runs are immutable; corrected work creates a new run linked to the prior run.

**Definition of Done**

- [ ] transition matrix tests pass
- [ ] completed run mutation is blocked
- [ ] manifest is sufficient to reproduce all referenced evidence and calculations

---

# Phase 2 — SEC Ingestion & Filing Package Parsing

## Task 2.1 — SEC Fair-Access HTTP Client & Rate Governor

**Status:** `[ ]` Not Started

**User Story**  
Centralize respectful SEC access so every adapter obeys identification, caching, concurrency, and rate limits.

**Specification**

- One SEC HTTP adapter used by all live SEC calls.
- Require descriptive `SEC_USER_AGENT` / contact in live mode.
- Token-bucket or equivalent rate governor; conservative default below SEC published ceiling.
- Bounded concurrency, timeout, retry/backoff for transient failures, explicit 403/429 behavior.
- Shared HTTP cache metadata; metrics for request/cache/rate-limit behavior.
- Never add anti-bot bypass logic.

**Definition of Done**

- [ ] tests prove configured request ceiling is never exceeded
- [ ] 403/429/5xx/timeout paths are bounded
- [ ] missing user-agent blocks live mode
- [ ] no SEC adapter bypasses the governor

## Task 2.2 — SEC Submissions + Bulk Metadata Adapter

**Status:** `[ ]` Not Started

**User Story**  
Discover filing history efficiently for both interactive use and historical bootstrap.

**Specification**

- Adapter for `data.sec.gov/submissions/CIK##########.json`, including referenced older submission files.
- Optional bulk bootstrap from SEC `submissions.zip` for large universes.
- Normalize form, accession, filing date, report date, acceptance metadata when available.
- Idempotent merge into canonical filing identities.
- Record source dataset/retrieval version.

**Definition of Done**

- [ ] recent + historical metadata fixtures parse
- [ ] bulk and per-company paths converge to same canonical identity
- [ ] no future filing enters an `as_of` view

## Task 2.3 — Filing Package Manifest & Attachment Inventory

**Status:** `[ ]` Not Started

**User Story**  
Model the complete accession package instead of assuming one HTML file contains all economically relevant information.

**Specification**

- Enumerate accession-directory contents via SEC-supported directory/index metadata.
- Capture sequence, filename, description, document type, size and URLs.
- Classify primary filing, complete submission text, XBRL files, exhibits and supporting documents.
- Preserve unknown document types rather than dropping them.
- Stable document identity within accession.

**Definition of Done**

- [ ] package fixture with primary + exhibits + XBRL inventories correctly
- [ ] unknown attachments retained
- [ ] manifest links all documents to the accession

## Task 2.4 — Primary Filing & Analyst-Relevant Exhibit Retriever

**Status:** `[ ]` Not Started

**User Story**  
Fetch the correct primary document and selected exhibits while keeping raw bytes immutable.

**Specification**

- Retrieve primary 10-K/10-Q/8-K document.
- Retrieve configured analyst-relevant exhibits (e.g., earnings releases, material-contract exhibits) when present.
- Size/content-type guards and hashing.
- Store immutable raw snapshots behind storage interface.
- Preserve complete submission text as fallback when configured.
- Do not mistake exhibits for the primary filing.

**Definition of Done**

- [ ] primary-vs-exhibit selection fixtures pass
- [ ] raw byte hash stable
- [ ] failed retrieval cannot create a completed snapshot
- [ ] exhibit selection is configuration/policy driven

## Task 2.5 — Filing HTML / Inline-XBRL Normalizer

**Status:** `[ ]` Not Started

**User Story**  
Convert filing markup into a stable document representation without losing locators or visible text.

**Specification**

- Remove scripts/styles/navigation noise.
- Normalize whitespace and iXBRL wrappers without dropping visible text.
- Preserve headings, paragraphs, lists and table blocks.
- Emit stable block ids, DOM/source locators, normalized and original text references.
- Preserve hidden/tagged XBRL separately; never blend hidden facts into visible prose.

**Definition of Done**

- [ ] deterministic normalization fixtures pass
- [ ] visible text preserved
- [ ] locators survive normalization
- [ ] hidden iXBRL is not misrepresented as visible narrative

## Task 2.6 — 10-K / 10-Q Item & Section Parser

**Status:** `[ ]` Not Started

**User Story**  
Extract analyst-relevant 10-K and 10-Q sections with explicit boundary confidence.

**Specification**

- Parse key 10-K items: Business, Risk Factors, Legal Proceedings, MD&A, Market Risk, Financial Statements.
- Parse key 10-Q parts/items: Financial Statements, MD&A, Market Risk, Legal Proceedings, Risk Factors.
- Heading-pattern + structural heuristics with diagnostics.
- Prevent an item from swallowing later sections.
- Explicit `ambiguous` result when boundaries are not reliable.

**Definition of Done**

- [ ] multi-issuer/legacy fixture corpus passes target boundary metrics
- [ ] Risk Factors and MD&A boundaries tested
- [ ] ambiguous extraction does not silently succeed

## Task 2.7 — 8-K Item & Exhibit Parser

**Status:** `[ ]` Not Started

**User Story**  
Treat 8-K as an event filing with item-specific and exhibit-aware intelligence.

**Specification**

- Parse 8-K item numbers/titles and item text.
- Link exhibit references to filing package documents.
- Support event-level objects for common analyst-relevant items without hardcoding conclusions.
- Parse earnings-related exhibits such as EX-99.1 when referenced/configured.
- Preserve item/exhibit evidence locators.

**Definition of Done**

- [ ] multi-item 8-K fixture parses correctly
- [ ] exhibit references resolve to package manifest
- [ ] missing exhibit is explicit
- [ ] 8-K findings remain evidence-backed

## Task 2.8 — Snapshot Deduplication, Cache & Source-Version Policy

**Status:** `[ ]` Not Started

**User Story**  
Minimize SEC traffic while preserving exact reproducibility.

**Specification**

- Content-addressed raw snapshot behavior.
- Retrieval attempts are separate from immutable snapshot identity.
- Cache headers/TTL only affect network retrieval, never historical provenance.
- Same bytes reuse snapshot; changed bytes create new snapshot/retrieval relation.
- Document retention policy for raw filings required by completed reports.

**Definition of Done**

- [ ] repeat retrieval is idempotent
- [ ] changed bytes never overwrite original
- [ ] cache tests preserve provenance
- [ ] completed report never loses required source snapshot

---

# Phase 3 — Filing Lineage, Diff & Change Intelligence

## Task 3.1 — Comparable Filing Selection Policy

**Status:** `[ ]` Not Started

**User Story**  
Choose economically defensible baselines using form, fiscal period, amendment lineage, and `as_of` constraints.

**Specification**

- 10-K → prior comparable 10-K.
- 10-Q → same fiscal-quarter prior-year by default; optional sequential quarter view.
- 8-K → event-specific baseline only when meaningful.
- Never select a filing unavailable at `as_of`.
- Persist comparator policy and reason.
- Define how amended filings participate in focal/comparator selection.

**Definition of Done**

- [ ] comparator matrix tests pass
- [ ] fiscal-calendar edge cases covered
- [ ] no future/amendment leakage
- [ ] selection reason persisted

## Task 3.2 — Amendment, Restatement & Filing Lineage Engine

**Status:** `[ ]` Not Started

**User Story**  
Track 10-K/A, 10-Q/A and related non-reliance/restatement signals as explicit lineage rather than overwriting history.

**Specification**

- Link amendments to likely base filing with confidence/reason.
- Compare amendment against base and prior comparable filing separately.
- Detect and surface 8-K non-reliance/restatement-related events when present in parsed item data.
- Preserve original, amended and effective analysis views.
- Never label fraud; use neutral restatement/amendment terminology.

**Definition of Done**

- [ ] original and amendment remain separately queryable
- [ ] lineage fixtures pass
- [ ] amendment-only changes can be isolated
- [ ] historical `as_of` before amendment returns original state

## Task 3.3 — Section Alignment Engine

**Status:** `[ ]` Not Started

**User Story**  
Align corresponding sections and blocks despite reorder, heading drift and boilerplate churn.

**Specification**

- Section-key-first alignment.
- Block fingerprints and normalized text similarity within bounded sections.
- Do not align across unrelated SEC items only due to semantic similarity.
- Preserve unmatched additions/removals.
- Emit alignment confidence and reason.

**Definition of Done**

- [ ] reorder fixture works
- [ ] boilerplate fixture works
- [ ] cross-section false matches rejected
- [ ] ambiguous alignment exposed

## Task 3.4 — Deterministic Text & Table Diff Engine

**Status:** `[ ]` Not Started

**User Story**  
Produce exact reviewable text/table changes before any LLM interpretation.

**Specification**

- Added/removed/changed paragraphs and sentences.
- Stable change ids and old/new evidence locators.
- Section length delta and normalized change magnitude.
- Table block fingerprints and cell/row diff for supported normalized tables.
- No LLM dependency.

**Definition of Done**

- [ ] deterministic repeatability
- [ ] text diff regression fixtures pass
- [ ] supported table diff fixtures pass
- [ ] evidence links resolve to both source snapshots

## Task 3.5 — Risk, MD&A, Liquidity & Exhibit Change Detectors

**Status:** `[ ]` Not Started

**User Story**  
Build analyst-focused change objects from high-value sections and filing exhibits.

**Specification**

- New/deleted Risk Factor candidates.
- MD&A topics: demand, pricing, costs, margins, capex, liquidity, debt, customer/supplier concentration.
- 8-K/exhibit topics: earnings, guidance, financing, acquisitions/dispositions, material agreements where evidence supports categorization.
- Deterministic lexical/topic features may flag strengthened/softened language; unsafe classification returns unknown.

**Definition of Done**

- [ ] labeled risk/MD&A fixtures pass
- [ ] exhibit evidence retained
- [ ] formatting-only false positives controlled
- [ ] unknown path explicit

## Task 3.6 — Research Priority Feature Builder

**Status:** `[ ]` Not Started

**User Story**  
Create transparent deterministic features for later ranking without pretending to determine legal materiality.

**Specification**

Features may include section/item importance, novelty, change magnitude, topic flags, amendment/restatement status, exhibit type, cross-section recurrence, related XBRL signals, parser/alignment confidence, and evidence quality.

Each feature has a documented range, meaning and version.

**Definition of Done**

- [ ] same input produces same feature vector
- [ ] features are explainable and versioned
- [ ] no legal-materiality label or conclusion

---

# Phase 4 — XBRL & Deterministic Financial Signals

## Task 4.1 — SEC Company Facts Adapter

**Status:** `[ ]` Not Started

**User Story**  
Retrieve standardized entity-wide XBRL facts efficiently from official SEC APIs.

**Specification**

- `companyfacts` adapter with shared SEC governor/cache.
- Optional bulk `companyfacts.zip` bootstrap for large historical universes.
- Preserve taxonomy/concept/unit/period/form/accession metadata exposed by source.
- Treat this path as standardized aggregate data, not complete filing-specific XBRL.

**Definition of Done**

- [ ] fixture parsing passes
- [ ] bulk/per-company canonical outputs reconcile
- [ ] missing concept is explicit
- [ ] no API key required for official public path

## Task 4.2 — Direct Filing iXBRL Fact Extractor

**Status:** `[ ]` Not Started

**User Story**  
Extract filing-specific facts that aggregate APIs may omit, including custom concepts and dimensions.

**Specification**

- Parse inline XBRL / extracted instance for the selected filing.
- Retain standard vs custom concept identity.
- Retain context id, period, unit, decimals/scale/sign, dimensions/axes/members, hidden/visible status.
- Link fact to filing accession and source locator.
- Do not flatten dimensional facts into entity-wide values silently.

**Definition of Done**

- [ ] custom-tag fixture works
- [ ] dimensional fact fixture works
- [ ] hidden fact status retained
- [ ] filing-specific facts trace to accession/locator

## Task 4.3 — XBRL Context, Unit, Dimension & Period Normalizer

**Status:** `[ ]` Not Started

**User Story**  
Normalize facts safely while preserving semantics needed for correct comparisons.

**Specification**

- Instant vs duration periods.
- Fiscal year/quarter metadata.
- Unit normalization with original unit retained.
- Duplicate-context resolution policy.
- Dimension-aware keys.
- Missing is never zero; ambiguous facts remain unresolved.

**Definition of Done**

- [ ] period/unit/dimension fixtures pass
- [ ] duplicate context policy tested
- [ ] no dimension collapse
- [ ] missing != zero

## Task 4.4 — Taxonomy / Concept Mapping & Version-Drift Layer

**Status:** `[ ]` Not Started

**User Story**  
Handle evolving SEC-supported taxonomies and issuer extensions without brittle hardcoded tag assumptions.

**Specification**

- Store taxonomy namespace/version and concept metadata.
- Map known standard concepts to internal canonical metrics through versioned rules.
- Keep custom extensions as first-class facts; optional mapping requires explicit evidence/rule.
- Detect taxonomy-version changes across comparisons.
- Regression fixtures across at least two taxonomy versions.

**Definition of Done**

- [ ] version drift fixture passes
- [ ] custom concept never silently mapped to a standard concept
- [ ] mappings are versioned and auditable

## Task 4.5 — Deterministic Financial Trend Engine

**Status:** `[ ]` Not Started

**User Story**  
Calculate reproducible financial changes from normalized facts.

**Specification**

Calculate where supported: revenue growth, gross/operating margin deltas, operating cash flow, capex, FCF, cash/debt changes, working-capital indicators, share-count changes, liquidity/leverage features.

All outputs retain source fact ids and transformation definitions.

**Definition of Done**

- [ ] hand-verified formula fixtures pass
- [ ] unit/sign conventions documented
- [ ] every metric traces to facts/accessions
- [ ] point-in-time filtering enforced

## Task 4.6 — Accounting / Disclosure Watch Signals

**Status:** `[ ]` Not Started

**User Story**  
Surface rule-based accounting/disclosure patterns for analyst follow-up, not accusations.

**Specification**

Examples: receivables vs revenue growth, inventory vs revenue growth, cash-flow/earnings divergence, debt/liquidity deterioration, dilution, large concept changes, accounting-policy text changes, restatement/amendment flags.

Each signal has neutral language and documented formula.

**Definition of Done**

- [ ] every rule documented/tested
- [ ] no accusation/fraud language
- [ ] insufficient data returns unknown

## Task 4.7 — Narrative ↔ Numeric Reconciliation Engine

**Status:** `[ ]` Not Started

**User Story**  
Compare management narrative change with deterministic financial direction while preserving both evidence types.

**Specification**

- Map selected narrative topics to related canonical metrics.
- Classify `aligned`, `mixed`, `tension`, `insufficient_data`.
- Include text evidence + numeric fact lineage.
- LLM may explain but cannot create/recalculate financial values.

**Definition of Done**

- [ ] alignment/tension fixtures pass
- [ ] classification reason names driving metrics
- [ ] unsupported mapping returns insufficient_data

---

# Phase 5 — LLM / RAG Filing Intelligence & Evaluation

## Task 5.1 — Section-Aware Retrieval Index

**Status:** `[ ]` Not Started

**User Story**  
Retrieve bounded evidence from the correct accession, form, item, document and time slice.

**Specification**

- Chunk by filing document → SEC item/section → block boundaries.
- Metadata includes accession, document id/type, section/item, locator, snapshot hash.
- Lexical/hybrid baseline; embeddings remain replaceable adapter.
- Hard filters enforce selected filing/package and `as_of`.

**Definition of Done**

- [ ] retrieval cannot cross requested accession filter
- [ ] item/document metadata preserved
- [ ] deterministic fixture queries pass

## Task 5.2 — Structured Filing Intelligence LLM Client

**Status:** `[ ]` Not Started

**User Story**  
Convert bounded diffs/evidence into schema-valid analyst findings.

**Specification**

Schema includes `finding_type`, `direction`, `severity`, `confidence`, `summary`, `why_it_matters`, `evidence_ids`, `open_questions`, and `interpretation_status`.

Record model, provider, prompt version, schema version and request id/cost metadata where available.

**Definition of Done**

- [ ] valid schema path passes
- [ ] malformed output fails closed
- [ ] provider mockable
- [ ] prompt/schema/model version recorded

## Task 5.3 — Filing Change Analyst Workflow

**Status:** `[ ]` Not Started

**User Story**  
Combine deterministic textual, exhibit and numeric changes into a coherent first-pass analyst view.

**Specification**

- Bounded inputs only.
- Distinguish source fact, deterministic calculation, model interpretation, and open question.
- One finding may cite multiple evidence spans/facts.
- No uncited “management said” claims.
- Handle 10-K/10-Q/8-K/amendment workflows explicitly.

**Definition of Done**

- [ ] curated fixtures generate expected finding categories
- [ ] absent evidence → uncertain/not_supported
- [ ] no numeric invention

## Task 5.4 — Evidence Validator, Contradiction Checker & Priority Scorer

**Status:** `[ ]` Not Started

**User Story**  
Block unsupported findings and rank valid ones transparently.

**Specification**

- Verify evidence belongs to run snapshots and cited item/document.
- Validate claim/evidence consistency using deterministic checks plus bounded reviewer model if configured.
- Detect contradictory findings.
- Combine Task 3.6 features + evidence confidence into versioned Research Priority Score with reason codes.

**Definition of Done**

- [ ] wrong accession/document citation blocked
- [ ] unsupported claim blocked
- [ ] contradictory output flagged
- [ ] score reproducible and labeled research priority

## Task 5.5 — Golden Evaluation Corpus & Regression Harness

**Status:** `[ ]` Not Started

**User Story**  
Measure parser, retrieval and model quality instead of relying on anecdotal demos.

**Specification**

- Versioned labeled corpus across sectors/forms.
- Gold section boundaries, comparator pairs, representative diffs, evidence bindings, finding labels, amendment/8-K cases, custom/dimensional XBRL cases.
- Metrics: extraction boundary quality, comparator accuracy, retrieval recall@k, finding precision/recall, unsupported-claim rate, evidence-binding rate.
- Separate deterministic regression tests from optional paid LLM eval runs.

**Definition of Done**

- [ ] evaluation manifest/version exists
- [ ] baseline metrics reproducible
- [ ] regressions detectable in CI/offline eval
- [ ] no private/copyright-problematic full filings committed when fixtures can be minimized/referenced

## Task 5.6 — Cited Filing Intelligence Report & Bounded Q&A

**Status:** `[ ]` Not Started

**User Story**  
Produce an analyst-readable report and answer questions only from selected filing evidence.

**Specification**

Report includes filing metadata, executive change summary, top ranked findings, Risk Factors, MD&A/liquidity, 8-K/exhibit events, amendment/restatement notes, XBRL signals, narrative-numeric tensions, open questions, methodology and evidence appendix.

Q&A is filtered to selected snapshot(s) and cites every factual answer.

**Definition of Done**

- [ ] material factual claims cited
- [ ] reviewer can block finalization
- [ ] report reproducible from run id
- [ ] Q&A returns not_supported outside available evidence

---

# Phase 6 — API, Dashboard, Watchlists & Research Workflow

## Task 6.1 — Core FastAPI Endpoints

**Status:** `[ ]` Not Started

**User Story**  
Expose issuer, filing package, comparison run, finding, evidence, report and health operations through thin API endpoints.

**Specification**

- Typed request/response contracts.
- Pagination/filtering.
- Problem/error model.
- Application services remain source of truth.
- No direct browser call to SEC APIs required.

**Definition of Done**

- [ ] API integration tests pass
- [ ] validation/error contracts documented
- [ ] no business logic duplicated in controllers

## Task 6.2 — Streamlit Analyst Dashboard

**Status:** `[ ]` Not Started

**User Story**  
Provide a polished analyst UI for issuer search, filing timeline, run status and top findings.

**Specification**

- Issuer search/ambiguity resolution.
- Filing/package timeline.
- Run create/status.
- Top findings with evidence drill-down.
- Filters by form/item/finding type/priority.
- UI never performs SEC parsing or financial calculations.

**Definition of Done**

- [ ] primary flow usable on desktop
- [ ] ambiguity/failed-run states visible
- [ ] UI remains thin over application/API

## Task 6.3 — Filing Comparison Workspace

**Status:** `[ ]` Not Started

**User Story**  
Let analysts inspect exact source changes rather than trusting summaries.

**Specification**

- Current vs comparator metadata.
- Section/item tabs.
- Added/removed/changed block and supported table diff.
- 8-K item/exhibit panel.
- Amendment lineage view.
- XBRL trend/fact lineage panel.
- Finding → evidence navigation.

**Definition of Done**

- [ ] Risk Factors/MD&A diff usable
- [ ] 8-K exhibit evidence accessible
- [ ] amended/original lineage visible
- [ ] exact source text locators accessible

## Task 6.4 — Watchlist & New-Filing Workflow

**Status:** `[ ]` Not Started

**User Story**  
Detect unseen filings for watched issuers and enqueue analysis idempotently.

**Specification**

- Watchlist model and form filters.
- Scheduled poller using submissions adapter.
- Last-seen/accession reservation with concurrency safety.
- Accepted/public timestamp retained.
- Idempotent analysis-run creation and safe retries.

**Definition of Done**

- [ ] repeated polling does not duplicate run
- [ ] concurrent pollers do not double-enqueue
- [ ] unseen filing creates exactly one workflow
- [ ] failures retry without losing accession state

## Task 6.5 — Research Alerts & Triage Queue

**Status:** `[ ]` Not Started

**User Story**  
Turn findings into a manageable analyst review queue instead of noisy notifications.

**Specification**

- Internal alert model with severity/priority/reason.
- Alert on new filing, high-priority finding, amendment/restatement signal, parser/source/model failure.
- Dedupe and acknowledgement state.
- Optional generic webhook adapter behind interface.
- Deep link to run/finding/evidence.

**Definition of Done**

- [ ] duplicate alerts suppressed
- [ ] every alert links to a traceable run/evidence or operational error
- [ ] external webhook failures do not corrupt analysis state

## Task 6.6 — Export & Research Artifact Packaging

**Status:** `[ ]` Not Started

**User Story**  
Export research outputs without losing provenance or methodology.

**Specification**

- Markdown report, structured JSON, CSV findings table, optional printable HTML.
- Include run id, accessions, `as_of`, snapshot hashes, calculation/prompt/model versions and citation locators.
- Machine-readable export schema is versioned.

**Definition of Done**

- [ ] export schema tests pass
- [ ] citations/provenance preserved
- [ ] no hidden model-only claim appears in export

---

# Phase 7 — Hardening, CI/CD & Production Integrity

## Task 7.1 — End-to-End Filing Intelligence Suite

**Status:** `[ ]` Not Started

**User Story**  
Prove issuer → package → parse → lineage → diff → XBRL → LLM → evidence → report with deterministic fixtures.

**Specification**

- Happy 10-K/10-Q path.
- 8-K + exhibit path.
- amendment lineage path.
- parser/source failure.
- malformed LLM output.
- unsupported finding blocked.
- point-in-time future source excluded.

**Definition of Done**

- [ ] all critical E2E scenarios green
- [ ] no live SEC/LLM dependency in mandatory CI
- [ ] provenance assertions included

## Task 7.2 — Observability, Structured Logging & Health Checks

**Status:** `[ ]` Not Started

**User Story**  
Make source, parser, model and analysis failures diagnosable by run id without leaking secrets.

**Specification**

- Correlation/run/accession ids.
- SEC request/cache/rate-limit metrics.
- parser/alignment/model timing and error categories.
- `/healthz`, `/ready`, optional `/health/detailed`.
- sensitive-data scrubbing.

**Definition of Done**

- [ ] health tests pass
- [ ] no secrets/raw private credentials in logs
- [ ] a failed run is traceable end-to-end

## Task 7.3 — Docker & Production Configuration

**Status:** `[ ]` Not Started

**User Story**  
Package the modular monolith reproducibly with durable provenance storage and rebuildable caches separated.

**Specification**

- Lean Python image, non-root runtime, locked dependencies.
- Persistent SQLite/raw snapshot volumes.
- Separate cache/temp.
- `.env.example`, healthcheck, graceful shutdown.
- API/UI may run as separate processes from same image.

**Definition of Done**

- [ ] image builds and starts
- [ ] health endpoint works
- [ ] durable/rebuildable paths documented
- [ ] no secret baked into image

## Task 7.4 — GitHub Actions CI/CD & Release Workflow

**Status:** `[ ]` Not Started

**User Story**  
Block releases on code quality, tests and integrity gates.

**Specification**

- lock/install validation, lint, type checks, pytest, parser/eval smoke, dependency audit, Docker build.
- Release/image publishing depends on green quality job.
- Optional scheduled live SEC smoke uses low request budget and no secrets beyond configuration.

**Definition of Done**

- [ ] failing tests block release
- [ ] workflow syntax validated
- [ ] live smoke is optional/non-flaky
- [ ] release docs updated

## Task 7.5 — Backup, Restore & Operational Runbook

**Status:** `[ ]` Not Started

**User Story**  
Protect SQLite, raw snapshots and completed-run reproducibility through tested recovery.

**Specification**

- Consistent DB backup.
- Raw snapshot backup/retention.
- Restore drill verifies completed report citations resolve.
- Runbook covers SEC outages, wrong issuer/comparator, parser drift, amendment lineage, XBRL taxonomy issues, LLM/evidence failures and point-in-time incidents.

**Definition of Done**

- [ ] restore drill passes
- [ ] completed run reopens with evidence after restore
- [ ] RUNBOOK synchronized

## Task 7.6 — SEC Traffic, Integrity & Release Qualification Gate

**Status:** `[ ]` Not Started

**User Story**  
Exercise production behavior under bounded concurrency and certify research integrity before v1 release.

**Specification**

- Verify request rate never exceeds configured governor.
- Measure cache hit behavior and bulk-bootstrap path.
- Run golden evaluation baseline and compare thresholds.
- Run no-look-ahead and citation integrity suite.
- Verify raw snapshot immutability and amendment lineage.
- Produce release qualification record with waivers if any.

**Definition of Done**

- [ ] fair-access load test passes
- [ ] point-in-time violations = 0
- [ ] unsupported factual claims in accepted reports = 0 on blocking suite
- [ ] KPI thresholds/waivers recorded
- [ ] v1 release checklist signed off

---

# Progress Summary

| Phase | Tasks | Not Started | In Progress | Completed |
|---|---|---:|---:|---:|
| 1 Foundation, Issuer Identity & Provenance | 1.1–1.5 | 2 | 0 | 3 |
| 2 SEC Ingestion & Filing Package Parsing | 2.1–2.8 | 8 | 0 | 0 |
| 3 Filing Lineage, Diff & Change Intelligence | 3.1–3.6 | 6 | 0 | 0 |
| 4 XBRL & Deterministic Financial Signals | 4.1–4.7 | 7 | 0 | 0 |
| 5 LLM / RAG Filing Intelligence & Evaluation | 5.1–5.6 | 6 | 0 | 0 |
| 6 API, Dashboard, Watchlists & Research Workflow | 6.1–6.6 | 6 | 0 | 0 |
| 7 Hardening, CI/CD & Production Integrity | 7.1–7.6 | 6 | 0 | 0 |
| **Total** | **44 tasks** | **41** | **0** | **3** |
