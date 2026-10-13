# FilingLens Implementation Phases
Each phase defines the purpose, key deliverables, risks and release gate. Atomic scope lives in `PROJECT_PHASES_AND_TASKS.md`.

## Phase 1 — Foundation, Issuer Identity & Provenance
### Scope

Build the project foundation, canonical issuer/filing identity, immutable provenance and reproducible run model.
### Atomic Tasks
- **Task 1.1** — Python Project Scaffold & Quality Baseline
- **Task 1.2** — Core Domain Models & SQLite Persistence
- **Task 1.3** — Issuer / CIK / Ticker Resolver
- **Task 1.4** — Filing Identity, Availability & Immutable Provenance Model
- **Task 1.5** — Analysis Run Lifecycle & Reproducibility Manifest

### Key Challenges

Ticker ambiguity; accession identity; public availability semantics; immutable snapshots.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 2 — SEC Ingestion & Filing Package Parsing
### Scope

Implement governed SEC access, metadata/bulk ingestion, full accession-package manifests, primary/exhibit retrieval and 10-K/10-Q/8-K parsing.
### Atomic Tasks
- **Task 2.1** — SEC Fair-Access HTTP Client & Rate Governor
- **Task 2.2** — SEC Submissions + Bulk Metadata Adapter
- **Task 2.3** — Filing Package Manifest & Attachment Inventory
- **Task 2.4** — Primary Filing & Analyst-Relevant Exhibit Retriever
- **Task 2.5** — Filing HTML / Inline-XBRL Normalizer
- **Task 2.6** — 10-K / 10-Q Item & Section Parser
- **Task 2.7** — 8-K Item & Exhibit Parser
- **Task 2.8** — Snapshot Deduplication, Cache & Source-Version Policy

### Key Challenges

SEC fair access; heterogeneous filing packages; exhibits; legacy HTML; iXBRL wrappers.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 3 — Filing Lineage, Diff & Change Intelligence
### Scope

Model filing lineage, choose defensible comparators and produce deterministic text/table change intelligence.
### Atomic Tasks
- **Task 3.1** — Comparable Filing Selection Policy
- **Task 3.2** — Amendment, Restatement & Filing Lineage Engine
- **Task 3.3** — Section Alignment Engine
- **Task 3.4** — Deterministic Text & Table Diff Engine
- **Task 3.5** — Risk, MD&A, Liquidity & Exhibit Change Detectors
- **Task 3.6** — Research Priority Feature Builder

### Key Challenges

Amendments/restatements; fiscal calendars; moved text; boilerplate; exhibit changes.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 4 — XBRL & Deterministic Financial Signals
### Scope

Combine aggregate SEC XBRL APIs with direct filing iXBRL extraction and deterministic financial signals.
### Atomic Tasks
- **Task 4.1** — SEC Company Facts Adapter
- **Task 4.2** — Direct Filing iXBRL Fact Extractor
- **Task 4.3** — XBRL Context, Unit, Dimension & Period Normalizer
- **Task 4.4** — Taxonomy / Concept Mapping & Version-Drift Layer
- **Task 4.5** — Deterministic Financial Trend Engine
- **Task 4.6** — Accounting / Disclosure Watch Signals
- **Task 4.7** — Narrative ↔ Numeric Reconciliation Engine

### Key Challenges

Custom tags; dimensions; duplicate contexts; units; taxonomy drift; amendments.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 5 — LLM / RAG Filing Intelligence & Evaluation
### Scope

Add bounded retrieval, structured LLM analysis, evidence review, quality evaluation and cited reporting.
### Atomic Tasks
- **Task 5.1** — Section-Aware Retrieval Index
- **Task 5.2** — Structured Filing Intelligence LLM Client
- **Task 5.3** — Filing Change Analyst Workflow
- **Task 5.4** — Evidence Validator, Contradiction Checker & Priority Scorer
- **Task 5.5** — Golden Evaluation Corpus & Regression Harness
- **Task 5.6** — Cited Filing Intelligence Report & Bounded Q&A

### Key Challenges

Hallucination; wrong-accession retrieval; unsupported severity; model regression.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 6 — API, Dashboard, Watchlists & Research Workflow
### Scope

Expose analyst workflows through API/UI, comparison workspace, watchlists, triage and provenance-preserving exports.
### Atomic Tasks
- **Task 6.1** — Core FastAPI Endpoints
- **Task 6.2** — Streamlit Analyst Dashboard
- **Task 6.3** — Filing Comparison Workspace
- **Task 6.4** — Watchlist & New-Filing Workflow
- **Task 6.5** — Research Alerts & Triage Queue
- **Task 6.6** — Export & Research Artifact Packaging

### Key Challenges

Thin UI; duplicate workflows; alert noise; evidence usability.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Phase 7 — Hardening, CI/CD & Production Integrity
### Scope

Prove E2E quality, observability, deployment, backup/restore and fair-access/research-integrity release gates.
### Atomic Tasks
- **Task 7.1** — End-to-End Filing Intelligence Suite
- **Task 7.2** — Observability, Structured Logging & Health Checks
- **Task 7.3** — Docker & Production Configuration
- **Task 7.4** — GitHub Actions CI/CD & Release Workflow
- **Task 7.5** — Backup, Restore & Operational Runbook
- **Task 7.6** — SEC Traffic, Integrity & Release Qualification Gate

### Key Challenges

Reproducible CI; raw snapshot durability; rate policies; restore correctness.
### Phase Gate

- all atomic task DoDs satisfied
- relevant regression/evaluation suites green
- point-in-time/provenance invariants intact
- docs and task ledger synchronized

## Cross-Phase Quality Gates

1. Tests pass.
2. No unrelated refactor.
3. Provenance remains intact.
4. No point-in-time leakage.
5. New external calls are governed and observable.
6. LLM output is schema/evidence validated.
7. Documentation is synchronized.
