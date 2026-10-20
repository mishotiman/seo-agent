"""Configuration: config.yaml values, overridable by SEO_AUDIT_* environment variables."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENV_PREFIX = "SEO_AUDIT_"
DEFAULT_CONFIG_PATH = Path("config.yaml")


class ConfigError(Exception):
    """Raised when configuration is missing, unreadable, or invalid."""


class CrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_pages: int = Field(50, ge=1)
    max_depth: int = Field(3, ge=0)
    total_time_budget: float = Field(300, gt=0)
    request_timeout: float = Field(15, gt=0)
    concurrency: int = Field(4, ge=1)
    include_subdomains: bool = False
    respect_robots: bool = True
    use_sitemap: bool = True


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    temperature: float = Field(0.0, ge=0, le=2)
    max_text_chars: int = Field(8000, ge=100)  # visible-text cap per page sent to checks/model
    crawl: CrawlConfig = CrawlConfig()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping at top level")
    return data


def _apply_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    """Apply SEO_AUDIT_<KEY> / SEO_AUDIT_<SECTION>__<KEY> overrides; pydantic coerces types."""
    for name, value in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        parts = name[len(ENV_PREFIX) :].lower().split("__")
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value


def load_config(
    path: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Config:
    """Load and validate config. Raises ConfigError with a readable message."""
    data = _read_yaml(Path(path) if path else DEFAULT_CONFIG_PATH)
    _apply_env(data, os.environ if env is None else env)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"  {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]
        raise ConfigError("Invalid configuration:\n" + "\n".join(lines)) from exc


def load_config_or_exit(path: Path | str | None = None) -> Config:
    """CLI helper: print a clear error to stderr and exit 2 on invalid config."""
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
