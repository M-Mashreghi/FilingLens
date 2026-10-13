"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from filinglens import __version__
from filinglens.config import Settings, get_settings
from filinglens.logging_setup import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="FilingLens API", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.env}

    return app
