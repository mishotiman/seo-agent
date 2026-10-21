"""Local web UI: a thin FastAPI layer over `run_audit`."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from seo_audit.config import ConfigError, load_config

STATIC_DIR = Path(__file__).parent / "static"


def create_app(config_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="SEO Audit", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=None)
    async def config() -> dict[str, object] | JSONResponse:
        try:
            cfg = load_config(config_path)
        except ConfigError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=500)
        return {
            "model": cfg.model,
            "max_pages": cfg.crawl.max_pages,
            "max_pages_limit": cfg.crawl.max_pages,
            "modes": ["page", "site"],
        }

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    return app
