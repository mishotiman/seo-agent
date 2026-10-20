# SEO Audit Agent

Audit a single page or a bounded crawl of a whole site from one URL.
Rule-based checks (titles, canonicals, headings, broken links, ...) run in code;
subjective checks (title/description quality, content quality, search intent) and
the site-level "what to fix first" summary come from an LLM. Any
[LiteLLM](https://docs.litellm.ai/docs/providers) model works, and you switch it
through config only. The output is JSON that validates against
[`schema/audit-report.schema.json`](schema/audit-report.schema.json), or a Markdown report.

See [DESIGN.md](DESIGN.md) for design decisions and [EVALUATION.md](EVALUATION.md)
for the evaluation proposal.

## Prerequisites

- Python 3.11 or newer
- An API key for the model provider you plan to use (Anthropic by default; OpenAI,
  Gemini, local Ollama, etc. also work through LiteLLM)
- Network access to the site you audit

## Install

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -e ".[dev]"     # use `pip install -e .` if you don't need tests/typecheck
seo-audit --help
```

On Windows, if `python` is not on PATH, use `py -m venv .venv` instead.

## API key setup

Copy `.env.example` to `.env` in the project root and fill in the key for your provider:

```bash
cp .env.example .env
# then edit .env:
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
```

LiteLLM loads `.env` automatically when it is imported, and `.env` is git-ignored.
You can also export the variable in your shell instead (`export ANTHROPIC_API_KEY=...`,
or `$env:ANTHROPIC_API_KEY="..."` in PowerShell).

If no key is set, or the model call fails, the audit still finishes. All code checks
are reported, and the failure is listed under `errors` in the report.

## Usage

```text
seo-audit URL [--mode page|site] [--config PATH] [--model NAME]
              [--max-pages N] [--out FILE] [--format json|md]
```

Audit one page (the default mode) and print JSON to stdout:

```bash
seo-audit https://example.com
```

Audit one page and print a readable Markdown report:

```bash
seo-audit https://example.com --mode page --format md
```

Crawl a site (bounded by `config.yaml`) and save the report to a file:

```bash
seo-audit https://example.com --mode site --max-pages 10 --out report.json
seo-audit https://example.com --mode site --format md --out report.md
```

Progress messages go to stderr, so you can redirect the report on its own:
`seo-audit https://example.com > report.json`.

Exit codes:
- `0`: the report was produced (it may still contain findings and errors)
- `1`: total failure, meaning no page could be fetched
- `2`: invalid input or config, such as a bad URL, unreadable or invalid `config.yaml`, or an unwritable `--out` path

## Configuration

Settings live in [`config.yaml`](config.yaml), which is read from the current
directory by default. Use `--config PATH` to point at a different file.

```yaml
model: anthropic/claude-sonnet-5-5
temperature: 0.0
max_text_chars: 8000       # visible page text cap given to checks and the model

crawl:
  max_pages: 50
  max_depth: 3
  total_time_budget: 300   # seconds for the whole crawl
  request_timeout: 15      # seconds per request
  concurrency: 4
  include_subdomains: false
  respect_robots: true     # skip URLs disallowed by robots.txt and honor Crawl-delay
  use_sitemap: true        # seed the crawl from sitemap.xml when present
```

Any value can be overridden with an environment variable. Top-level keys use
`SEO_AUDIT_<KEY>`; nested keys use a double underscore, as in `SEO_AUDIT_<SECTION>__<KEY>`:

```bash
SEO_AUDIT_CRAWL__MAX_PAGES=5 seo-audit https://example.com --mode site
```

The precedence order is: CLI flags (`--model`, `--max-pages`), then environment
variables, then `config.yaml`. An invalid value, such as a negative `max_pages`, an
unknown key, or a typo in a section, stops the run with exit code 2 and a message
naming the bad field.

## Switching the model

The model is a LiteLLM model string (`provider/model-name`). You never need to change
code to switch it. Make sure the matching API key is set, then use any of these:

1. **Config:** edit `model:` in `config.yaml`:
   ```yaml
   model: openai/gpt-4o
   ```
2. **Environment:** `SEO_AUDIT_MODEL=openai/gpt-4o seo-audit https://example.com`
3. **CLI, for one run:**
   ```bash
   seo-audit https://example.com --model openai/gpt-4o
   seo-audit https://example.com --model anthropic/claude-haiku-5-5
   seo-audit https://example.com --model ollama/llama3.1   # local, no API key
   ```

All model calls go through one wrapper (`src/seo_audit/llm.py`). It asks for JSON,
validates the reply, and retries once if the output is malformed. If a provider
rejects a parameter (for example, a model that only accepts `temperature=1`), the
wrapper drops that parameter instead of failing. The report records the model used
and the total token usage.

## Output

- **JSON** (default): an `AuditReport` with crawl summary, per-page results,
  site-level findings, `site_summary` (top actions, site mode only), token usage,
  errors, and the count of model findings dropped for unverifiable evidence. Every
  finding has `id`, `category`, `severity`, `source` (`code` or `model`), `evidence`,
  `recommendation` and `confidence`.
- **Markdown** (`--format md`): a summary, any crawl limits that were hit, top
  actions, and findings grouped by severity and then by page. Each finding is
  labelled `[code]` or `[model, confidence X]`.

## Development

```bash
python -m pytest            # tests (HTTP and model are mocked; no network or key needed)
python -m mypy              # strict typecheck of src/
python -m seo_audit.schema  # regenerate schema/audit-report.schema.json after model changes
```

## Known limitations

- **No JavaScript rendering.** Only the static HTML returned by the server is
  analysed. Content, links, or meta tags injected client-side (SPAs, JS-rendered
  frameworks without SSR) are invisible to the agent, and missing-tag findings on
  such sites may be false positives.
- **HTML only.** PDFs, images and other non-HTML responses are recorded but not audited.
- **Bounded crawl.** Site mode stops at `max_pages`, `max_depth` or `total_time_budget`.
  On large sites the result covers a sample of pages, and the report says which limit was hit.
- **Broken-link detection covers crawled URLs only.** Links outside the crawl
  (external links, or pages beyond the limits) are not requested.
- **No performance or Core Web Vitals metrics** beyond server response time, and no
  backlink, ranking or search-console data.
- **Model judgement is non-deterministic and costs tokens.** Model findings carry a
  confidence value, and findings whose evidence quote is not found on the page are dropped.
