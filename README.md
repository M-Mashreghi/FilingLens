# FilingLens

**SEC Filing Intelligence Engine**

FilingLens answers one analyst question:

> **What changed in this filing, compared with the right prior context, and what exact evidence supports the conclusion?**

It is not a generic “chat with a filing” demo. FilingLens combines official SEC data, accession-level provenance, filing-package/exhibit awareness, deterministic text/XBRL analytics, structured LLM interpretation, and claim-level citations.

## Core Workflow

```text
Ticker / CIK / Company
        ↓
Canonical SEC Issuer
        ↓
Filing History + Accession Package
        ↓
Immutable Primary + Exhibit Snapshots
        ↓
10-K / 10-Q / 8-K Parsing
        ↓
Amendment / Restatement Lineage
        ↓
Comparable Filing Selection
        ↓
Deterministic Text + Table Diff
        ↓
CompanyFacts + Direct Filing iXBRL
        ↓
Deterministic Financial Signals
        ↓
Structured LLM Interpretation
        ↓
Evidence / Contradiction Review
        ↓
Research Priority Ranking
        ↓
Cited Report + Comparison Workspace
```

## Why This Project Is Strong

- **Finance:** filings, XBRL, accounting changes, liquidity, leverage, guidance and disclosure analysis.
- **LLM:** bounded evidence, structured findings, RAG/Q&A, reviewer workflow.
- **Data engineering:** SEC ingestion, immutable snapshots, caching, accession package manifests.
- **Research integrity:** point-in-time filtering, amendments, taxonomy drift, custom XBRL tags, explicit provenance.
- **Product:** watchlists, alerts, analyst dashboard, exports.

## v1 Forms

Primary support:

- `10-K` / `10-K/A`
- `10-Q` / `10-Q/A`
- `8-K` / `8-K/A`
- analyst-relevant exhibits within those accession packages

Post-v1 candidates include 20-F/6-K, DEF 14A, Forms 3/4/5, 13D/13G, 13F, S-1 and event-study integration.

## Important Integrity Rules

1. Raw filing evidence is immutable.
2. Historical runs obey `as_of` public availability.
3. Amendments never overwrite originals.
4. LLMs interpret; Python calculates.
5. `companyfacts` is not treated as complete filing-specific XBRL.
6. Every material finding must resolve to evidence.
7. Research Priority Score is not legal materiality advice.

## Technology Direction

- Python 3.12+
- FastAPI
- Streamlit
- SQLite for v1
- HTTP client + HTML/iXBRL parser
- Pydantic-style schemas
- pluggable LLM provider
- Docker + GitHub Actions

Exact libraries should be chosen per atomic task rather than added prematurely.

## Project Documentation

- `CLAUDE.md` — Claude Code instructions
- `docs/PROJECT_PHASES_AND_TASKS.md` — implementation SSOT
- `docs/ARCHITECTURE.md` — boundaries/invariants
- `docs/SEC_DATA_CONTRACT.md` — SEC endpoints/access/provenance assumptions
- `docs/EVALUATION.md` — golden corpus and quality metrics
- `docs/ROADMAP.md` — milestones/KPIs
- `docs/PHASES.md` — phase-level scope
- `docs/TASKS.md` — execution ledger
- `docs/DIAGRAMS.md` — Mermaid diagrams
- `docs/DEPLOYMENT.md` — deployment/CI
- `docs/RUNBOOK.md` — incident/recovery procedures

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/); `uv.lock` is the committed lock file.

```bash
uv sync                      # create .venv and install locked deps (incl. dev group)
cp .env.example .env         # local configuration; never commit .env
uv run pytest                # tests (live SEC/LLM tests are opt-in markers)
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict type check
uv run uvicorn filinglens.api.app:create_app --factory   # API shell (/healthz)
uv run streamlit run src/filinglens/ui/app.py            # dashboard shell
```

## Start with Claude Code

```text
Read CLAUDE.md and all project governance documents.
Inspect Task 1.1 in docs/PROJECT_PHASES_AND_TASKS.md.
Implement only Task 1.1.
Follow its Definition of Done.
Run all required tests.
Update TASKS.md and PROJECT_PHASES_AND_TASKS.md only after verification succeeds.
Do not start Task 1.2.
```

## Status

Planning/governance package is complete. Implementation tasks are intentionally not started.
