# syntax=docker/dockerfile:1.7
# FilingLens modular monolith image: one image, API and UI as separate processes.

FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Locked, dependency-only layer first for cache reuse.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd --gid 10001 filinglens \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin filinglens \
 && mkdir -p /app/data/raw /app/cache \
 && chown -R filinglens:filinglens /app
WORKDIR /app
COPY --from=build --chown=filinglens:filinglens /app /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    FILINGLENS_ENV=production \
    FILINGLENS_LOG_JSON=true \
    FILINGLENS_DB_PATH=/app/data/filinglens.db \
    FILINGLENS_RAW_DIR=/app/data/raw \
    FILINGLENS_CACHE_DIR=/app/cache
USER filinglens
# Durable: SQLite + immutable raw snapshots. Rebuildable: HTTP/retrieval cache.
VOLUME ["/app/data", "/app/cache"]
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"]
STOPSIGNAL SIGTERM
CMD ["uvicorn", "filinglens.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
