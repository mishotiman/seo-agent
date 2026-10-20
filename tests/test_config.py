from pathlib import Path

import pytest

from seo_audit.config import ConfigError, load_config, load_config_or_exit

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_repo_config_loads_with_required_defaults() -> None:
    cfg = load_config(ROOT / "config.yaml", env={})
    assert cfg.model
    assert cfg.crawl.max_pages == 50
    assert cfg.crawl.max_depth == 3
    assert cfg.crawl.total_time_budget > 0
    assert cfg.crawl.request_timeout > 0


def test_env_overrides_yaml(tmp_path: Path) -> None:
    p = write(tmp_path, "model: a/b\ncrawl:\n  max_pages: 5\n")
    cfg = load_config(
        p,
        env={
            "SEO_AUDIT_MODEL": "openai/gpt-4o",
            "SEO_AUDIT_CRAWL__MAX_PAGES": "7",
            "SEO_AUDIT_CRAWL__INCLUDE_SUBDOMAINS": "true",
            "UNRELATED": "x",
        },
    )
    assert cfg.model == "openai/gpt-4o"
    assert cfg.crawl.max_pages == 7
    assert cfg.crawl.include_subdomains is True


def test_invalid_value_raises_clear_error(tmp_path: Path) -> None:
    p = write(tmp_path, "model: a/b\ncrawl:\n  max_pages: 0\n")
    with pytest.raises(ConfigError, match="crawl.max_pages"):
        load_config(p, env={})


def test_invalid_env_value(tmp_path: Path) -> None:
    p = write(tmp_path, "model: a/b\n")
    with pytest.raises(ConfigError, match="crawl.max_depth"):
        load_config(p, env={"SEO_AUDIT_CRAWL__MAX_DEPTH": "abc"})


def test_missing_file_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(tmp_path / "nope.yaml", env={})
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(write(tmp_path, "model: [unclosed"), env={})


def test_missing_model_is_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="model"):
        load_config(write(tmp_path, "temperature: 0\n"), env={})


def test_exit_on_invalid_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        load_config_or_exit(write(tmp_path, "model: ''\n"))
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err
