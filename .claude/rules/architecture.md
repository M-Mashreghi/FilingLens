# FilingLens Architecture Rules

Core pipeline:

`Issuer → SEC Package → Raw Snapshot → Parse → Lineage/Comparator → Diff → XBRL → Research Intelligence → Evidence Review → Report`

Rules:

- Domain/application code is independent from FastAPI, Streamlit and concrete SQLite/HTTP clients.
- All external providers sit behind interfaces/adapters.
- SEC package metadata is distinct from document content.
- Deterministic analytics remain separate from LLM interpretation.
- Completed runs reference immutable raw snapshots.
- UI code is thin and must not parse filings or calculate financial metrics.
- Prefer a modular monolith for v1.
- New dependencies require task-level justification.
