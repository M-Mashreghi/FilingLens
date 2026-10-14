# FilingLens — Deployment & CI/CD

## Runtime Shape

Recommended v1 processes:

- `filinglens-api` — FastAPI
- `filinglens-ui` — Streamlit
- optional scheduler/worker for watchlists
- SQLite durable volume
- durable immutable raw snapshot volume
- rebuildable HTTP/retrieval cache volume

Do not add Redis/Postgres/message brokers until measured workload requires them.

## Environment Variables

```text
FILINGLENS_ENV=production
FILINGLENS_DB_PATH=/app/data/filinglens.db
FILINGLENS_RAW_DIR=/app/data/raw
FILINGLENS_CACHE_DIR=/app/cache
SEC_USER_AGENT=FilingLens research your-email@example.com
SEC_MAX_RPS=5
SEC_MAX_CONCURRENCY=2
LLM_PROVIDER=...
LLM_MODEL=...
LLM_API_KEY=...
```

The default request rate should remain conservatively below the SEC-published ceiling. Treat the ceiling as policy, not a permanent constant.

## Data Classification

### Durable

- SQLite metadata
- immutable raw filing/exhibit/iXBRL snapshots needed by completed runs
- completed report/run manifests
- backup archives

### Rebuildable

- HTTP cache
- parsed derivatives if raw snapshot + parser version are retained
- lexical/vector retrieval indexes
- temp exports

## Docker Requirements

- Python 3.12+ image
- non-root runtime
- locked dependencies
- persistent `/app/data`
- separate `/app/cache`
- no secrets in image
- healthcheck and graceful shutdown

## SEC Production Policy

- all SEC traffic goes through the shared governor
- browser/UI does not call `data.sec.gov` directly
- use descriptive User-Agent/contact
- bounded concurrency and backoff
- cache repeat requests
- prefer official bulk archives for large historical bootstrap
- never use anti-bot bypass behavior

## CI Quality Gates

1. dependency/lock validation
2. format/lint
3. type checks
4. pytest unit/integration/regression
5. parser/8-K/amendment fixtures
6. point-in-time tests
7. XBRL custom/dimension/taxonomy tests
8. evidence/citation tests
9. security/dependency audit
10. Docker build smoke

Paid/live LLM eval and live SEC smoke remain optional jobs with explicit budgets.

## Release Gate

A publish/release job depends on green quality checks and Task 7.6 release qualification.

## Backup

Back up SQLite + immutable raw snapshots + any manifest/version state required to reproduce completed reports. A production release is not ready until restore verifies historical report citations resolve.


## Implemented Container Setup (Task 7.3)

- `Dockerfile`: multi-stage build with `uv sync --frozen --no-dev` against the committed `uv.lock`; runtime is `python:3.12-slim` as non-root user `filinglens` (uid 10001); `HEALTHCHECK` probes `/healthz`; `STOPSIGNAL SIGTERM` with uvicorn `--timeout-graceful-shutdown 20`.
- Paths: durable `/app/data` (SQLite `filinglens.db` + immutable `raw/` snapshots — back these up) and rebuildable `/app/cache` (HTTP cache) are separate volumes.
- `docker-compose.yml` runs `api` (uvicorn) and `ui` (Streamlit) from the same image, sharing the volumes; configuration and secrets come from `.env` at runtime. `.dockerignore` excludes `.env`, data, caches, tests and VCS metadata; no secret is set via `ENV`/`ARG`.
- Build/run: `docker compose build && docker compose up -d`; readiness at `http://localhost:8000/ready`.
- Verification status: the production start/readiness/graceful-shutdown path is tested by starting the real uvicorn command; the image build itself runs in the CI `docker` job (no Docker host was available on the development machine).
