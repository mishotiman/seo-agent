# PRD: SEO Audit Agent

## Introduction

Build an SEO audit agent that takes a URL and audits either that single page or the whole website. Deterministic checks (status codes, tag presence/length, canonicals, etc.) run in code; subjective checks (content quality, title relevance, prioritized advice) use an LLM. The model is swappable through configuration only. The crawl is bounded and the output is structured, schema-validated JSON (plus an optional Markdown report).

Stack decisions: Python 3.11+, LiteLLM for provider-agnostic model calls, CLI only, static HTML only (no JavaScript rendering).

## Goals

- Audit a single page or a bounded whole-site crawl from one URL.
- Switch model/provider via config or env var with zero code changes.
- Make the code-vs-model split explicit, and tag each finding with its source.
- Bound crawl by pages, depth, time, and politeness; report when limits truncate it.
- Produce schema-valid structured output.
- Deliver setup/run/model-switch docs, a design-decisions write-up, and a written evaluation proposal.

## User Stories

### US-001: Project scaffold and configuration
**Description:** As a developer, I want a configured Python project so that settings (model, crawl limits) live in config, not code.

**Acceptance Criteria:**
- [ ] `pyproject.toml` with dependencies (httpx, beautifulsoup4/lxml, litellm, pydantic, typer or argparse, pytest)
- [ ] `config.yaml` with `model`, `temperature`, and crawl limits; env vars override file values
- [ ] `.env.example` lists the API key variables; `.env` is gitignored
- [ ] Config loads into a validated pydantic model; invalid config exits with a clear error
- [ ] Tests pass; lint/typecheck passes

### US-002: Output schema
**Description:** As a consumer of the report, I want a defined JSON schema so that results are machine-readable.

**Acceptance Criteria:**
- [ ] Pydantic models for `AuditReport`, `CrawlSummary`, `PageResult`, `Finding`
- [ ] `Finding` has `id`, `category`, `severity` (critical/high/medium/low/info), `source` (code/model), `evidence`, `recommendation`, `confidence`
- [ ] JSON Schema exported to `schema/audit-report.schema.json`
- [ ] Test validates a sample report against the schema
- [ ] Tests pass; typecheck passes

### US-003: Page fetcher
**Description:** As the agent, I want to fetch a page with sane limits so that one bad URL cannot hang the run.

**Acceptance Criteria:**
- [ ] Fetches with a clear User-Agent, request timeout, and max response size
- [ ] Records final URL, status code, redirect chain, response time, content type
- [ ] Non-HTML, timeouts, and connection errors are returned as results, not exceptions
- [ ] Tests with mocked HTTP cover success, redirect, 404, timeout, non-HTML
- [ ] Tests pass; typecheck passes

### US-004: Bounded crawler
**Description:** As a user, I want site mode to stop at defined limits so that cost and runtime are predictable.

**Acceptance Criteria:**
- [ ] Enforces `max_pages` (default 50), `max_depth` (default 3), and `total_time_budget`
- [ ] Stays on the same host (subdomains optional via config)
- [ ] Respects robots.txt disallow and crawl-delay; caps concurrency
- [ ] Normalizes and dedupes URLs (fragments, trailing slashes, tracking params)
- [ ] Seeds from sitemap.xml when present
- [ ] `CrawlSummary` records pages crawled, pages skipped, and which limit was hit
- [ ] Tests pass; typecheck passes

### US-005: Page fact extraction
**Description:** As the agent, I want structured facts extracted from HTML so that both code checks and the model work from clean data.

**Acceptance Criteria:**
- [ ] Extracts title, meta description, headings (with levels), canonical, robots meta, hreflang, viewport, Open Graph/Twitter tags, JSON-LD blocks, images with alt, internal/external links, visible text
- [ ] Visible text trimmed to a configurable token/character cap for model use
- [ ] Tests with fixture HTML pass; typecheck passes

### US-006: Deterministic checks
**Description:** As a user, I want rule-based checks run in code so that results are fast, free, and reproducible.

**Acceptance Criteria:**
- [ ] Checks: missing/long/short title and meta description, H1 count and heading hierarchy, canonical issues, noindex, missing viewport, missing alt text, HTTPS, redirect chains, slow response, invalid JSON-LD, missing OG tags
- [ ] Site-level checks: duplicate titles/descriptions, broken internal links, robots.txt and sitemap validity
- [ ] Every finding has `source: "code"` with evidence
- [ ] One test per check using fixture pages with known issues
- [ ] Tests pass; typecheck passes

### US-007: Model-swappable LLM layer
**Description:** As a developer, I want a single model interface so that changing providers needs only config.

**Acceptance Criteria:**
- [ ] All model calls go through one `llm.complete()` wrapper using LiteLLM
- [ ] Model name comes only from config/env (e.g. `anthropic/claude-sonnet-5-5`, `openai/gpt-4o`)
- [ ] Wrapper requests JSON output, validates against pydantic, retries once on malformed output
- [ ] Token usage per run is tracked and reported
- [ ] Tests with a fake model cover valid, malformed-then-valid, and failing responses
- [ ] Tests pass; typecheck passes

### US-008: Model-judged checks
**Description:** As a user, I want subjective assessments (title/description quality, content quality, search intent) so that I get insight rules can't give.

**Acceptance Criteria:**
- [ ] Per-page prompt receives extracted facts and trimmed text, not raw HTML
- [ ] Model findings must include an `evidence` quote; quotes not found in the page text are dropped and counted
- [ ] Findings have `source: "model"` and a `confidence`
- [ ] Model failure leaves code findings intact and records an error entry in the report
- [ ] Tests with a fake model pass; typecheck passes

### US-009: Site-level synthesis
**Description:** As a site owner, I want a summary of cross-page patterns and top priorities so that I know what to fix first.

**Acceptance Criteria:**
- [ ] Model receives aggregated findings (counts and examples), not all pages' full text
- [ ] Produces a `site_summary` with the top prioritized actions
- [ ] Only runs in site mode
- [ ] Tests pass; typecheck passes

### US-010: CLI
**Description:** As a user, I want a command to run an audit so that I can use the agent from the terminal.

**Acceptance Criteria:**
- [ ] `seo-audit <url> --mode page|site [--config PATH] [--model NAME] [--max-pages N] [--out FILE] [--format json|md]`
- [ ] `--model` overrides config without code changes
- [ ] Progress goes to stderr; the report goes to stdout or `--out`
- [ ] Exit code is non-zero on invalid input or total failure
- [ ] Output validates against the schema
- [ ] Tests pass; typecheck passes

### US-011: Markdown report renderer
**Description:** As a practitioner, I want a readable report so that I can share findings.

**Acceptance Criteria:**
- [ ] Renders summary, crawl limits hit, findings grouped by severity then page
- [ ] Marks each finding as code- or model-derived
- [ ] Snapshot test passes; typecheck passes

### US-012: Documentation
**Description:** As a reviewer, I want clear docs so that I can set up, run, and understand the agent.

**Acceptance Criteria:**
- [ ] `README.md`: prerequisites, install, env setup, page and site run examples, how to switch the model (config and CLI flag), known limitations (no JS rendering)
- [ ] `DESIGN.md`: which checks are code vs. model and why, with pros/cons; how the crawl is bounded; how output is structured
- [ ] `EVALUATION.md`: written proposal covering code-check fixtures (precision/recall), labeled set for model checks and human agreement, LLM-as-judge calibrated against human scores, comparison with a reference tool, robustness cases, multi-model comparison on score/cost/latency/variance, CI regression thresholds
- [ ] Commands in README verified to work

## Functional Requirements

- FR-1: The system must accept a URL and a mode (`page` or `site`).
- FR-2: In page mode, only the given URL is fetched and audited (plus robots.txt and sitemap checks).
- FR-3: In site mode, the crawl must stop when `max_pages`, `max_depth`, or `total_time_budget` is reached and report which limit applied.
- FR-4: The crawler must respect robots.txt and stay on the target host unless configured otherwise.
- FR-5: Deterministic checks must run without any model call.
- FR-6: Model judgement checks must receive extracted facts and trimmed text only.
- FR-7: The model identifier must be read only from config/env/CLI flag.
- FR-8: Every finding must carry `source`, `severity`, `evidence`, and `recommendation`.
- FR-9: Model-produced evidence must be verified against page text; unverifiable findings are discarded.
- FR-10: Malformed model output is retried once, then recorded as an error.
- FR-11: Fetch failures and non-HTML responses must be recorded as findings and not abort the run.
- FR-12: The final JSON must validate against the published schema.
- FR-13: Token usage must be reported per run.

## Non-Goals (Out of Scope)

- JavaScript rendering / headless browsers.
- Backlink analysis, keyword volume, rank tracking, or any third-party SEO data APIs.
- Automatically fixing the audited site.
- HTTP API or web UI.
- A runnable evaluation harness (evaluation is a written proposal only).
- Core Web Vitals / real-user performance measurement.

## Technical Considerations

- Python 3.11+; httpx for fetching, lxml/BeautifulSoup for parsing, pydantic for schemas, LiteLLM for model access.
- Use async fetching with a small concurrency cap and per-host delay.
- Keep model prompts and the check registry in separate modules so checks can be added without touching the agent loop.
- Cost control: page cap, text truncation, one model call per page plus one site-level call.

## Success Metrics

- Changing `model` in config (or `--model`) switches providers with no code edits.
- 100% of produced reports validate against the schema.
- Crawl never exceeds configured limits in tests.
- Each code check has a passing fixture test.
- Docs allow a new user to run a first audit in under 10 minutes.

## Open Questions

- Which models will be used for the multi-model comparison in EVALUATION.md?
- Should the Markdown report be the default format, or JSON?
- Should external links be checked for breakage (slower, more requests), or only internal ones?
