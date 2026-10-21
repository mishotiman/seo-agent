"""Console entry point: `seo-audit-ui` starts the local web UI."""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from seo_audit.web import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def serve(
    host: Annotated[str, typer.Option("--host", help="Interface to bind")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="Port to listen on")] = DEFAULT_PORT,
) -> None:
    """Start the SEO Audit web UI."""
    if host not in LOOPBACK_HOSTS:
        typer.echo(
            f"Warning: binding to {host}. The UI has no authentication and can make this "
            "machine fetch arbitrary URLs; anyone who can reach it can use it.",
            err=True,
        )
    typer.echo(f"SEO Audit UI: open http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def main() -> None:
    typer.run(serve)
