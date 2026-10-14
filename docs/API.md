# FilingLens API (`api/1`)

FastAPI application: `filinglens.api.app:create_app` (run with `uv run uvicorn filinglens.api.app:create_app --factory`). Controllers are thin and delegate to the composition root (`filinglens.bootstrap.Container`); the browser/UI never calls SEC endpoints.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness, version, environment |
| GET | `/issuers/resolve?q=` | Resolve ticker / CIK / `EXCHANGE:TICKER` / name / alias → `resolved`, `ambiguous` (ranked candidates) or `not_found`, with reference-data version |
| GET | `/issuers/{cik}` | Canonical issuer |
| GET | `/issuers/{cik}/filings?form=&as_of=&limit=&offset=` | Filing history, newest first; `as_of` applies the point-in-time availability policy |
| GET | `/filings/{accession}` | Filing metadata (dashed or no-dash accession) incl. `available_at` and basis |
| GET | `/filings/{accession}/documents` | Package documents and roles |
| POST | `/runs` | Create a comparison run (`cik`, timezone-aware `as_of`, optional focal/comparator accession) → 201 |
| GET | `/runs?state=&limit=&offset=` | List runs |
| GET | `/runs/{run_id}` | Run state |
| POST | `/runs/{run_id}/transitions` | Move a run through the lifecycle state machine |
| GET | `/runs/{run_id}/findings?finding_type=&limit=&offset=` | Persisted findings |
| GET | `/runs/{run_id}/evidence/{evidence_id}` | Evidence span (must belong to the run) |
| GET | `/runs/{run_id}/report` | Latest finalized report |

## Pagination

`limit` (1–200, default 50) and `offset` (≥ 0). Paged responses are `{"items": [...], "total": n, "limit": l, "offset": o}`.

## Errors

All errors use `application/problem+json`: `{"type": "about:blank", "title": ..., "status": ..., "detail": ...}`.

| Status | Title | Cause |
|---|---|---|
| 404 | Not Found | Unknown issuer/filing/run/evidence/report, or issuer reference data not loaded |
| 409 | Conflict / Immutable Resource / Invalid State Transition / Ambiguous | Identity conflicts, completed-run mutation, illegal transition, ambiguity |
| 422 | Validation Error / Invalid Value | Request schema violations, naive timestamps, malformed accessions |
| 400 | Bad Request | Other domain errors |
