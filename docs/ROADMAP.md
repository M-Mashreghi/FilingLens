# FilingLens Roadmap

Strategic roadmap for **FilingLens — SEC Filing Intelligence Engine**.

Related: [ARCHITECTURE](./ARCHITECTURE.md) · [SEC_DATA_CONTRACT](./SEC_DATA_CONTRACT.md) · [EVALUATION](./EVALUATION.md) · [PHASES](./PHASES.md) · [TASKS](./TASKS.md)

## Product Vision

FilingLens reduces the time required to answer:

> **What changed in this filing, compared with the right historical context, and what exact evidence supports the conclusion?**

The system automates discovery, filing-package/exhibit collection, parsing, lineage, deterministic change analysis, XBRL reconciliation and first-pass LLM synthesis while remaining auditable.

## Release Horizons

### M1 — Provenance & SEC Foundation (Alpha)

Unlock: ticker/CIK → reliable filing history/package → immutable source snapshot.

Exit:

- issuer ambiguity handled
- fair-access SEC gateway working
- 10-K/10-Q/8-K package manifests ingest
- raw snapshots/accession provenance persist
- key form parsers work on curated fixtures

### M2 — Deterministic Filing Intelligence (Beta)

Unlock: exact changes with defensible baselines before LLM interpretation.

Exit:

- comparator + amendment lineage
- section/table diffs
- Risk/MD&A/8-K exhibit change objects
- companyfacts + filing-specific iXBRL
- versioned canonical financial metrics

### M3 — Evidence-Backed AI Research (RC)

Unlock: cited “what changed and why it may matter” research.

Exit:

- bounded retrieval
- structured findings
- evidence/contradiction reviewer
- Research Priority Score
- golden evaluation corpus + baseline metrics
- cited report/Q&A

### M4 — Analyst Product & Automation (v1.0)

Unlock: daily analyst workflow.

Exit:

- dashboard comparison workspace
- watchlists and triage alerts
- provenance-preserving exports
- Docker/CI/CD/backup/restore
- fair-access and integrity release gate

## Core KPIs

| KPI | Initial target |
|---|---:|
| Filing/accession identity accuracy | 100% on release corpus |
| Required section/item extraction success | >95% curated corpus |
| Comparable-pair accuracy | >98% curated corpus |
| Wrong-accession bounded retrieval | 0 blocking cases |
| Evidence-backed accepted finding rate | 100% |
| Unsupported factual claim accepted by reviewer | 0 blocking cases |
| Point-in-time leakage | 0 known violations |
| XBRL fact lineage completeness | 100% for emitted metrics |
| Watchlist duplicate run rate | 0 under idempotency suite |

See `EVALUATION.md` for exact evaluation definitions.

## Strategic Risks

| Risk | Mitigation |
|---|---|
| SEC access/rate controls | central governor, cache, bulk bootstrap, bounded retry |
| Filing package complexity | manifest all documents; primary/exhibit policy |
| 8-K value hidden in exhibits | explicit item + exhibit parser |
| Amendments distort history | separate immutable lineage + `as_of` logic |
| HTML/section drift | versioned parser fixtures/regression corpus |
| `companyfacts` incompleteness | direct filing iXBRL path for custom/dimensional facts |
| taxonomy changes | versioned concept mapping and taxonomy regression tests |
| LLM hallucination | bounded context, evidence ids, contradiction reviewer |
| wrong comparator | explicit policy/reason + benchmark |
| scope creep | post-v1 forms remain backlog unless promoted explicitly |

## Post-v1 Research Backlog

- 20-F / 6-K foreign issuer workflow
- DEF 14A governance/compensation intelligence
- Forms 3/4/5 insider activity
- 13D/13G stake-change monitoring
- 13F ownership intelligence
- S-1 IPO intelligence
- cross-company standardized benchmarking using SEC frames where appropriate
- filing-event market reaction / event-study module
- filing-derived research factors
- integration with an investment-committee agent

## Change Control

Update this roadmap when milestone scope/KPIs change. Atomic execution status belongs in `PROJECT_PHASES_AND_TASKS.md` and `TASKS.md`.
