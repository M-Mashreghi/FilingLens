# FilingLens — Task Progress Ledger

Update this file only after task verification.

## Status Legend

| Marker | Meaning |
|---|---|
| `[ ]` | Pending |
| `[-]` | In Progress |
| `[x]` | Completed |

## Phase 0 — Governance Baseline

- [x] Create `CLAUDE.md`
- [x] Create `.claude/rules/*`
- [x] Create `README.md`
- [x] Create `docs/ARCHITECTURE.md`
- [x] Create `docs/SEC_DATA_CONTRACT.md`
- [x] Create `docs/EVALUATION.md`
- [x] Create `docs/ROADMAP.md`
- [x] Create `docs/PHASES.md`
- [x] Create `docs/PROJECT_PHASES_AND_TASKS.md`
- [x] Create `docs/TASKS.md`
- [x] Create `docs/DIAGRAMS.md`
- [x] Create `docs/DEPLOYMENT.md`
- [x] Create `docs/RUNBOOK.md`

## Phase 1 — Foundation, Issuer Identity & Provenance

- [x] Task 1.1 — Python Project Scaffold & Quality Baseline
- [x] Task 1.2 — Core Domain Models & SQLite Persistence
- [x] Task 1.3 — Issuer / CIK / Ticker Resolver
- [x] Task 1.4 — Filing Identity, Availability & Immutable Provenance Model
- [x] Task 1.5 — Analysis Run Lifecycle & Reproducibility Manifest

### Verification — Phase 1

| Item | Detail |
|---|---|
| Status | Completed (2026-10-13) |
| Tests | `uv run pytest` — 199 passed (unit + integration); ruff + mypy strict green |
| Evaluation | Identity fixtures: ticker/CIK/name/alias/ambiguity, accession round-trip, availability policy |
| Docs | ARCHITECTURE §14, SEC_DATA_CONTRACT §6–7, README development commands |
| Notes | Run manifest stored in `run_manifests` (migration 2); completed runs immutable at service and repository level |

## Phase 2 — SEC Ingestion & Filing Package Parsing

- [x] Task 2.1 — SEC Fair-Access HTTP Client & Rate Governor
- [x] Task 2.2 — SEC Submissions + Bulk Metadata Adapter
- [x] Task 2.3 — Filing Package Manifest & Attachment Inventory
- [x] Task 2.4 — Primary Filing & Analyst-Relevant Exhibit Retriever
- [x] Task 2.5 — Filing HTML / Inline-XBRL Normalizer
- [x] Task 2.6 — 10-K / 10-Q Item & Section Parser
- [x] Task 2.7 — 8-K Item & Exhibit Parser
- [x] Task 2.8 — Snapshot Deduplication, Cache & Source-Version Policy

### Verification — Phase 2

| Item | Detail |
|---|---|
| Status | Completed (2026-10-13) |
| Tests | `uv run pytest` — 298 passed; ruff + mypy strict green |
| Evaluation | Section parser fixture corpus 100% required-section success (target >95%); package/8-K exhibit resolution fixtures |
| Docs | ARCHITECTURE §14, SEC_DATA_CONTRACT §1–2, §6 |
| Notes | All SEC traffic through SecHttpClient (import guard test); acceptanceDateTime read as US Eastern |

## Phase 3 — Filing Lineage, Diff & Change Intelligence

- [x] Task 3.1 — Comparable Filing Selection Policy
- [x] Task 3.2 — Amendment, Restatement & Filing Lineage Engine
- [x] Task 3.3 — Section Alignment Engine
- [x] Task 3.4 — Deterministic Text & Table Diff Engine
- [x] Task 3.5 — Risk, MD&A, Liquidity & Exhibit Change Detectors
- [x] Task 3.6 — Research Priority Feature Builder

### Verification — Phase 3

| Item | Detail |
|---|---|
| Status | Completed (2026-10-13) |
| Tests | `uv run pytest` — 359 passed; ruff + mypy strict green |
| Evaluation | Comparator matrix incl. 52/53-week, sequential, amendment and no-leakage cases; reorder/boilerplate/cross-section alignment fixtures; text/table diff fixtures; labeled detector fixtures |
| Docs | ARCHITECTURE §14 |
| Notes | Diff/detectors/features have no LLM dependency; features never encode legal materiality |

## Phase 4 — XBRL & Deterministic Financial Signals

- [x] Task 4.1 — SEC Company Facts Adapter
- [x] Task 4.2 — Direct Filing iXBRL Fact Extractor
- [x] Task 4.3 — XBRL Context, Unit, Dimension & Period Normalizer
- [x] Task 4.4 — Taxonomy / Concept Mapping & Version-Drift Layer
- [x] Task 4.5 — Deterministic Financial Trend Engine
- [x] Task 4.6 — Accounting / Disclosure Watch Signals
- [x] Task 4.7 — Narrative ↔ Numeric Reconciliation Engine

### Verification — Phase 4

| Item | Detail |
|---|---|
| Status | Completed (2026-10-13) |
| Tests | `uv run pytest` — 411 passed; ruff + mypy strict green |
| Evaluation | Custom/dimensional/hidden iXBRL fixtures; duplicate-context and missing≠zero tests; two-taxonomy drift fixture; hand-verified formula fixtures; neutral watch-rule tests |
| Docs | ARCHITECTURE §14, SEC_DATA_CONTRACT §1 (companyfacts) |
| Notes | companyfacts treated as aggregate only; custom concepts never auto-mapped; Python owns all numbers |

## Phase 5 — LLM / RAG Filing Intelligence & Evaluation

- [x] Task 5.1 — Section-Aware Retrieval Index
- [x] Task 5.2 — Structured Filing Intelligence LLM Client
- [x] Task 5.3 — Filing Change Analyst Workflow
- [x] Task 5.4 — Evidence Validator, Contradiction Checker & Priority Scorer
- [ ] Task 5.5 — Golden Evaluation Corpus & Regression Harness
- [ ] Task 5.6 — Cited Filing Intelligence Report & Bounded Q&A

### Verification — Phase 5

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Evaluation | — |
| Docs | — |
| Notes | — |

## Phase 6 — API, Dashboard, Watchlists & Research Workflow

- [ ] Task 6.1 — Core FastAPI Endpoints
- [ ] Task 6.2 — Streamlit Analyst Dashboard
- [ ] Task 6.3 — Filing Comparison Workspace
- [ ] Task 6.4 — Watchlist & New-Filing Workflow
- [ ] Task 6.5 — Research Alerts & Triage Queue
- [ ] Task 6.6 — Export & Research Artifact Packaging

### Verification — Phase 6

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Evaluation | — |
| Docs | — |
| Notes | — |

## Phase 7 — Hardening, CI/CD & Production Integrity

- [ ] Task 7.1 — End-to-End Filing Intelligence Suite
- [ ] Task 7.2 — Observability, Structured Logging & Health Checks
- [ ] Task 7.3 — Docker & Production Configuration
- [ ] Task 7.4 — GitHub Actions CI/CD & Release Workflow
- [ ] Task 7.5 — Backup, Restore & Operational Runbook
- [ ] Task 7.6 — SEC Traffic, Integrity & Release Qualification Gate

### Verification — Phase 7

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Evaluation | — |
| Docs | — |
| Notes | — |
