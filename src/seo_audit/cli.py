"""CLI entry point (full command implemented in a later story)."""

import typer

app = typer.Typer(add_completion=False, help="SEO audit agent")


@app.callback()
def main() -> None:
    """SEO audit agent."""
