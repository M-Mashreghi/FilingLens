# FilingLens — Claude Code Project Instructions

FilingLens is an evidence-backed SEC filing intelligence engine. It discovers official filings, preserves immutable provenance, compares the correct filing lineage, extracts filing-specific XBRL, and produces cited analyst research.

## Canonical Workflow

`Issuer → SEC Metadata → Filing Package → Immutable Snapshot → Parse → Lineage/Comparator → Deterministic Diff → XBRL → LLM Interpretation → Evidence Validation → Research Priority → Report / Dashboard`

## Source of Truth

Before coding a task, read:

1. `docs/PROJECT_PHASES_AND_TASKS.md` — atomic implementation SSOT
2. `docs/ARCHITECTURE.md`
3. `docs/SEC_DATA_CONTRACT.md`
4. `docs/TASKS.md`
5. the relevant section of `docs/PHASES.md`
6. `docs/EVALUATION.md` when parser/retrieval/LLM quality is touched

Do not invent requirements that conflict with these files.

## Task Workflow

For every task:

1. Read its full specification and DoD.
2. Inspect the current repository before editing.
3. Implement only the requested task scope.
4. Add/update deterministic tests for success and failure paths.
5. Run targeted tests plus relevant regression tests.
6. Fix failures before claiming completion.
7. Update `docs/TASKS.md` and `docs/PROJECT_PHASES_AND_TASKS.md` only after verification succeeds.
8. Update architecture/data-contract/runbook docs when behavior changes.
9. Never start the next task unless explicitly requested.

## Non-Negotiable Research Invariants

- **Accession-centric identity:** ticker is not a durable filing identity.
- **Immutable raw evidence:** never silently overwrite a raw filing snapshot.
- **Point-in-time:** a historical run may use only sources public by its `as_of` timestamp.
- **Accepted timestamp matters:** do not use filing date alone when a more precise public-availability timestamp exists.
- **Amendments remain separate:** 10-K/A, 10-Q/A and other amendments do not replace the original filing in storage.
- **Package awareness:** primary document, exhibits, complete submission text and XBRL artifacts belong to one accession package.
- **Evidence first:** every material model-derived factual finding must resolve to filing evidence.
- **Deterministic numbers:** LLMs do not authoritatively calculate financial metrics.
- **Research priority ≠ legal materiality:** never present FilingLens scoring as a legal conclusion.

## SEC Access

All SEC network access must go through the shared SEC adapter/governor.

- Use a descriptive configured User-Agent/contact.
- Respect current SEC fair-access guidance, bounded concurrency, caching and backoff.
- Never implement anti-bot bypass against SEC infrastructure.
- Prefer SEC bulk archives for large historical bootstrap where appropriate.
- Browser/UI code must not call `data.sec.gov` directly; use the backend adapter.

See `.claude/rules/sec-access.md` and `docs/SEC_DATA_CONTRACT.md`.

## Architecture Boundaries

Keep separate modules for:

- issuer/security resolution
- SEC access and filing-package discovery
- raw snapshot/provenance storage
- HTML/iXBRL normalization
- 10-K/10-Q/8-K parsing
- amendment/restatement lineage
- comparator selection
- deterministic text/table diff
- filing-specific XBRL extraction and normalization
- deterministic financial analytics
- retrieval/evidence selection
- LLM interpretation
- evidence validation/review
- reporting/export
- persistence
- API/UI/workflows

UI code must not implement parsing, financial calculations, SEC HTTP access, or persistence rules.

## LLM Boundary

LLMs may classify and explain bounded evidence. They must not invent:

- filing passages
- financial values
- guidance
- XBRL facts
- legal conclusions
- unsupported severity

Application-facing model output must be schema-validated and versioned. If evidence is insufficient, return `uncertain` / `not_supported`.

## Financial / XBRL Integrity

Deterministic Python owns:

- text/table diffs
- period selection
- unit/context/dimension handling
- growth/margin/cash/debt/capex/FCF calculations
- accounting watch rules
- research-priority features

Every numeric output retains source fact ids, accession, period, unit and transformation definition.

## Testing

Critical regression families include:

- CIK/ticker/company ambiguity
- accession/package identity
- SEC rate-governor behavior
- primary vs exhibit selection
- 10-K/10-Q item boundaries
- 8-K item/exhibit parsing
- amendment/restatement lineage
- comparator selection
- text/table diff stability
- XBRL custom tags and dimensions
- taxonomy-version drift
- missing-data behavior
- point-in-time leakage prevention
- retrieval filtering
- LLM schema/evidence validation
- citation integrity
- E2E report generation

Never weaken a requirement just to make a test pass.

## Security / Secrets

Never commit or log API keys, tokens, credentials, private user data, or hidden provider headers. Use environment configuration and `.env.example`.

## Scope Discipline

Prefer a modular monolith for v1. Do not add Redis, Kafka, a vector database, distributed workers, or new paid data providers without a task-level need and explicit approval. Avoid unrelated refactors.

## Completion Gate

A task is complete only when implementation exists, required tests pass, the DoD is satisfied, documentation is synchronized, and no unresolved critical research-integrity issue remains.
