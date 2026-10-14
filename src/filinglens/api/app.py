"""FastAPI application factory (``api/1``).

Controllers are thin: they validate input, call application services/repositories through
the composition root and map domain errors to problem responses. No parsing, SEC access or
financial calculation happens here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from filinglens import __version__
from filinglens.api.schemas import (
    CandidateOut,
    DocumentOut,
    EvidenceOut,
    FilingOut,
    FindingOut,
    IssuerOut,
    Page,
    Problem,
    ReportOut,
    ResolutionOut,
    RunCreate,
    RunOut,
    RunTransition,
)
from filinglens.bootstrap import Container
from filinglens.config import Settings, get_settings
from filinglens.domain.errors import (
    AmbiguityError,
    ConflictError,
    FilingLensError,
    ImmutabilityError,
    InvalidTransitionError,
    NotFoundError,
)
from filinglens.domain.models import ComparisonRun, Filing, RunState
from filinglens.infrastructure.storage.sqlite import MIGRATIONS
from filinglens.logging_setup import configure_logging

API_VERSION = "api/1"
_STATUS = {
    NotFoundError: (404, "Not Found"),
    ConflictError: (409, "Conflict"),
    ImmutabilityError: (409, "Immutable Resource"),
    InvalidTransitionError: (409, "Invalid State Transition"),
    AmbiguityError: (409, "Ambiguous"),
}

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def get_container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


C = Annotated[Container, Depends(get_container)]


def _problem(status: int, title: str, detail: str | None) -> JSONResponse:
    body = Problem(title=title, status=status, detail=detail).model_dump()
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def readiness_checks(c: Container) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        c.repos.db.query("SELECT 1")
        latest = MIGRATIONS[-1][0]
        checks["database"] = (
            "ok"
            if c.repos.db.schema_version == latest
            else f"schema {c.repos.db.schema_version} != {latest}"
        )
    except Exception as exc:  # report, never raise from a probe
        checks["database"] = f"error: {type(exc).__name__}"
    if c.raw_dir is not None:
        try:
            c.raw_dir.mkdir(parents=True, exist_ok=True)
            probe = c.raw_dir / ".ready-probe"
            probe.write_bytes(b"")
            probe.unlink()
            checks["raw_store"] = "ok"
        except OSError as exc:
            checks["raw_store"] = f"error: {type(exc).__name__}"
    return checks


def _page(items: list[Any], limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


def _filing(f: Filing) -> FilingOut:
    return FilingOut(
        accession=f.accession,
        cik=f.cik,
        form=f.form,
        filing_date=f.filing_date,
        report_period=f.report_period,
        accepted_at=f.accepted_at,
        available_at=f.available_at,
        availability_basis=f.availability_basis.value,
        primary_document=f.primary_document,
        is_amendment=f.is_amendment,
        items=list(f.items) if f.items else None,
    )


def _run(r: ComparisonRun) -> RunOut:
    return RunOut(
        run_id=r.run_id,
        cik=r.cik,
        state=r.state.value,
        as_of=r.as_of,
        focal_accession=r.focal_accession,
        comparator_accession=r.comparator_accession,
        supersedes_run_id=r.supersedes_run_id,
        error=r.error,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="FilingLens API", version=__version__)
    app.state.container = container or Container.from_settings(settings)

    @app.exception_handler(FilingLensError)
    async def domain_error(_: Request, exc: FilingLensError) -> JSONResponse:
        for cls, (status, title) in _STATUS.items():
            if isinstance(exc, cls):
                return _problem(status, title, str(exc))
        return _problem(400, "Bad Request", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(422, "Validation Error", str(exc.errors()))

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return _problem(422, "Invalid Value", str(exc))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.env, "api": API_VERSION}

    @app.get("/ready")
    def ready(c: C) -> JSONResponse:
        checks = readiness_checks(c)
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": checks},
            status_code=200 if ok else 503,
        )

    @app.get("/health/detailed")
    def health_detailed(c: C) -> dict[str, Any]:
        runs = c.repos.runs.list()
        by_state: dict[str, int] = {}
        for r in runs:
            by_state[r.state.value] = by_state.get(r.state.value, 0) + 1
        return {
            "status": "ok",
            "version": __version__,
            "api": API_VERSION,
            "env": settings.env,
            "checks": readiness_checks(c),
            "schema_version": c.repos.db.schema_version,
            "runs_by_state": by_state,
            "pending_alerts": len(c.repos.alerts.list(acknowledged=False)),
            "telemetry": c.telemetry.snapshot(),
            "sec": c.sec_client.metrics.snapshot() if c.sec_client else None,
            "sec_live_mode": settings.sec_live_mode,
            "llm_configured": bool(settings.llm_provider),
        }

    @app.get("/issuers/resolve", response_model=ResolutionOut)
    def resolve_issuer(
        c: C, q: Annotated[str, Query(min_length=1, max_length=200)]
    ) -> ResolutionOut:
        resolver = c.resolver
        if resolver is None:
            raise NotFoundError("issuer reference data not loaded")
        r = resolver.resolve(q)
        return ResolutionOut(
            query=r.query,
            status=r.status.value,
            cik=r.cik,
            source_version=r.source_version,
            reason=r.reason,
            candidates=[
                CandidateOut(
                    cik=x.cik,
                    name=x.name,
                    tickers=list(x.tickers),
                    score=x.score,
                    match_type=x.match_type,
                )
                for x in r.candidates
            ],
        )

    @app.get("/issuers/{cik}", response_model=IssuerOut)
    def get_issuer(c: C, cik: int) -> IssuerOut:
        issuer = c.repos.issuers.get(cik)
        if issuer is None:
            raise NotFoundError(f"issuer {cik}")
        return IssuerOut(
            cik=issuer.cik,
            name=issuer.name,
            former_names=list(issuer.former_names),
            fiscal_year_end=issuer.fiscal_year_end,
        )

    @app.get("/issuers/{cik}/filings", response_model=Page[FilingOut])
    def list_filings(
        c: C,
        cik: int,
        limit: Limit = 50,
        offset: Offset = 0,
        form: Annotated[str | None, Query()] = None,
        as_of: Annotated[datetime | None, Query()] = None,
    ) -> dict[str, Any]:
        if as_of is not None:
            rows = c.metadata.filings_as_of(cik, as_of, forms={form} if form else None)
        else:
            rows = c.repos.filings.list_for_cik(cik)
            if form:
                rows = [f for f in rows if f.form == form.upper()]
        rows = sorted(rows, key=lambda f: (f.available_at, f.accession), reverse=True)
        return _page([_filing(f) for f in rows], limit, offset)

    @app.get("/filings/{accession}", response_model=FilingOut)
    def get_filing(c: C, accession: str) -> FilingOut:
        f = c.repos.filings.get(accession)
        if f is None:
            raise NotFoundError(f"filing {accession}")
        return _filing(f)

    @app.get("/filings/{accession}/documents", response_model=list[DocumentOut])
    def list_documents(c: C, accession: str) -> list[DocumentOut]:
        if c.repos.filings.get(accession) is None:
            raise NotFoundError(f"filing {accession}")
        return [
            DocumentOut(
                accession=d.accession,
                filename=d.filename,
                sequence=d.sequence,
                doc_type=d.doc_type,
                description=d.description,
                size_bytes=d.size_bytes,
                role=d.role,
                url=d.url,
            )
            for d in c.repos.documents.list_for_accession(accession)
        ]

    @app.post("/runs", response_model=RunOut, status_code=201)
    def create_run(c: C, body: RunCreate) -> RunOut:
        return _run(
            c.runs.create(
                cik=body.cik,
                as_of=body.as_of,
                focal_accession=body.focal_accession,
                comparator_accession=body.comparator_accession,
            )
        )

    @app.get("/runs", response_model=Page[RunOut])
    def list_runs(
        c: C, limit: Limit = 50, offset: Offset = 0, state: Annotated[str | None, Query()] = None
    ) -> dict[str, Any]:
        runs = c.repos.runs.list()
        if state:
            runs = [r for r in runs if r.state.value == state]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return _page([_run(r) for r in runs], limit, offset)

    @app.get("/runs/{run_id}", response_model=RunOut)
    def get_run(c: C, run_id: str) -> RunOut:
        return _run(c.runs.get(run_id))

    @app.post("/runs/{run_id}/transitions", response_model=RunOut)
    def transition_run(c: C, run_id: str, body: RunTransition) -> RunOut:
        return _run(c.runs.transition(run_id, RunState(body.target), error=body.error))

    @app.get("/runs/{run_id}/findings", response_model=Page[FindingOut])
    def list_findings(
        c: C,
        run_id: str,
        limit: Limit = 50,
        offset: Offset = 0,
        finding_type: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        c.runs.get(run_id)
        rows = c.repos.findings.list_for_run(run_id)
        if finding_type:
            rows = [f for f in rows if f.finding_type == finding_type]
        items = [
            FindingOut(
                finding_id=f.finding_id,
                run_id=f.run_id,
                finding_type=f.finding_type,
                summary=f.summary,
                severity=f.severity,
                confidence=f.confidence,
                evidence_ids=list(f.evidence_ids),
                interpretation_status=f.interpretation_status,
                direction=f.direction,
                why_it_matters=f.why_it_matters,
                open_questions=list(f.open_questions),
            )
            for f in rows
        ]
        return _page(items, limit, offset)

    @app.get("/runs/{run_id}/evidence/{evidence_id}", response_model=EvidenceOut)
    def get_evidence(c: C, run_id: str, evidence_id: str) -> EvidenceOut:
        ev = c.repos.evidence.get(evidence_id)
        if ev is None or ev.run_id != run_id:
            raise NotFoundError(f"evidence {evidence_id} in run {run_id}")
        return EvidenceOut(
            evidence_id=ev.evidence_id,
            run_id=ev.run_id,
            snapshot_id=ev.snapshot_id,
            accession=ev.accession,
            locator=ev.locator,
            text=ev.text,
            filename=ev.filename,
            section=ev.section,
        )

    @app.get("/runs/{run_id}/report", response_model=ReportOut)
    def get_report(c: C, run_id: str) -> ReportOut:
        rep = c.repos.reports.latest_for_run(run_id)
        if rep is None:
            raise NotFoundError(f"no finalized report for run {run_id}")
        return ReportOut(
            report_id=rep.report_id,
            run_id=rep.run_id,
            version=rep.version,
            body_markdown=rep.body_markdown,
            created_at=rep.created_at,
        )

    return app
