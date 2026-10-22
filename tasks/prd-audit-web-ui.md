# PRD: SEO Audit Web UI

## Introduction

The SEO audit agent currently runs only from the command line (`seo-audit URL ...`) and produces JSON or a Markdown report. A job interviewer reviewing the project should be able to open a web page, type a URL, watch the audit run, and read the results without touching a terminal beyond one start command.

This feature adds a small local web UI on top of the existing agent. It must be simple, clean, minimalistic and cold-toned (slate / ice-blue palette, lots of whitespace, no decoration for its own sake). The UI is a thin layer: it calls the existing `run_audit`, `render_markdown` and `Config` code and does not change how audits work.

Stack decisions: a FastAPI app inside the existing `seo_audit` package serves a single static HTML/CSS/JS page (vanilla JS, no build step, no Node). It is started with a new console command `seo-audit-ui` and opens at `http://127.0.0.1:8000`. Model API keys stay server-side in `.env`, exactly as with the CLI. The UI never asks for or shows them.

## Goals

- Let a non-technical reviewer run a page or site audit from a browser in under 30 seconds after starting the server.
- Expose the main CLI controls: URL, mode (page/site), max pages, and model.
- Show live progress while an audit runs, with a cancel button.
- Present results as a structured, scannable view (summary, top actions, findings by severity and page, source badges) with Markdown and JSON downloads.
- Keep the visual design minimal and cold-toned, readable in a 1280px browser and on a phone-width screen.
- Make no changes to audit behaviour, report schema, or CLI behaviour.

## User Stories

### US-001: Web server skeleton and start command
**Description:** As a reviewer, I want to start the UI with one command so that I can open it in my browser.

**Acceptance Criteria:**
- [ ] `fastapi` and `uvicorn` added to `pyproject.toml` dependencies (`httpx` test client already available; add `pytest` usage only, no new test framework)
- [ ] New module `src/seo_audit/web/__init__.py` with `create_app()` returning a FastAPI app
- [ ] New console script `seo-audit-ui` (entry in `pyproject.toml`) starts uvicorn on `127.0.0.1:8000` by default; supports `--host` and `--port` options and prints the URL to open
- [ ] `GET /` serves `src/seo_audit/web/static/index.html` (a placeholder page with title "SEO Audit" is enough in this story); static files are included in the package data so they work after `pip install -e .`
- [ ] `GET /api/health` returns `{"status": "ok"}`
- [ ] Tests using FastAPI `TestClient` cover `/` and `/api/health`
- [ ] Typecheck (`python -m mypy`) passes; tests pass

### US-002: Config endpoint for form defaults
**Description:** As a reviewer, I want the form pre-filled with sensible defaults so that I can just paste a URL and run.

**Acceptance Criteria:**
- [ ] `GET /api/config` returns JSON with `model` (from `config.yaml` / env via `load_config`), `max_pages` default, `max_pages_limit` (the configured `crawl.max_pages`, used as the upper bound in the UI), and `modes: ["page", "site"]`
- [ ] If the config cannot be loaded (`ConfigError`), the endpoint returns HTTP 500 with a JSON `{"detail": "<message>"}`
- [ ] Tests cover the success case and the invalid-config case
- [ ] Typecheck passes; tests pass

### US-003: Audit job manager
**Description:** As a developer, I need a job manager that runs audits in the background so that the UI can start an audit and observe it without blocking.

**Acceptance Criteria:**
- [ ] New module `src/seo_audit/web/jobs.py` with a `Job` (id, url, mode, state, progress messages list, report or error, created time) and a `JobManager`
- [ ] States: `queued`, `running`, `done`, `failed`, `cancelled`
- [ ] `JobManager.start(url, mode, config)` creates an asyncio task that calls the existing `run_audit(url, mode, config, client, llm, progress)` using `make_client` and `LLMClient`; the `progress` callback appends messages to the job
- [ ] The HTTP client and LLM client factories are injectable so tests can pass fakes (no network, no API key)
- [ ] Only one job may run at a time; starting another while one is running raises a clear `JobBusy` error
- [ ] `JobManager.cancel(job_id)` cancels the running task and sets state `cancelled`
- [ ] A job whose report satisfies `is_total_failure` ends as `done` with the report attached (the UI shows the report's errors); an unexpected exception ends as `failed` with a message
- [ ] Finished jobs are kept in memory; at most the 20 most recent are retained
- [ ] Unit tests cover: success, progress messages recorded, busy rejection, cancel, unexpected exception
- [ ] Typecheck passes; tests pass

### US-004: Start-audit and status API
**Description:** As the frontend, I need endpoints to start an audit and read its status and report.

**Acceptance Criteria:**
- [ ] `POST /api/audits` accepts JSON `{url, mode, max_pages?, model?}` and returns `202` with `{id}`
- [ ] Input is validated with `validate_url` and the same override logic as the CLI (`model`, `max_pages` applied on top of the loaded config); `max_pages` must be an integer between 1 and `max_pages_limit`
- [ ] Invalid input returns `422` with `{"detail": "<message>"}` using the same message text the CLI would print (`InvalidInput` / `ConfigError`)
- [ ] If another audit is running, returns `409` with `{"detail": "An audit is already running"}`
- [ ] `GET /api/audits/{id}` returns `{id, url, mode, state, progress: [str], error?, report?}` where `report` is the `AuditReport` JSON once the state is `done`; unknown id returns `404`
- [ ] Tests (with fake HTTP and LLM clients injected into the app) cover: start and fetch a finished page audit, invalid URL, bad `max_pages`, busy conflict, unknown id
- [ ] Typecheck passes; tests pass

### US-005: Live progress stream and cancel
**Description:** As a reviewer, I want to see what the agent is doing and be able to stop it so that long site crawls are not a black box.

**Acceptance Criteria:**
- [ ] `GET /api/audits/{id}/events` is a Server-Sent Events stream: it first replays all existing progress messages, then emits new ones as `event: progress` with the message as data, and finishes with `event: state` carrying the final state (`done` / `failed` / `cancelled`) and closes
- [ ] Connecting to the stream of an already-finished job replays the messages and the final state immediately
- [ ] `POST /api/audits/{id}/cancel` cancels a running job and returns `200` with the new state; cancelling a finished job returns `409`; unknown id returns `404`
- [ ] Tests read the stream with the test client and assert message order and the final state event; cancel is covered
- [ ] Typecheck passes; tests pass

### US-006: Report download endpoints
**Description:** As a reviewer, I want to download the report as Markdown or JSON so that I can keep or share it.

**Acceptance Criteria:**
- [ ] `GET /api/audits/{id}/report.md` returns the output of the existing `render_markdown` with `Content-Type: text/markdown; charset=utf-8` and a `Content-Disposition: attachment` filename derived from the audited host (e.g. `seo-audit-example.com.md`)
- [ ] `GET /api/audits/{id}/report.json` returns the report JSON (`model_dump_json(indent=2)`) with `Content-Disposition: attachment` and a matching `.json` filename
- [ ] Both return `404` for an unknown id and `409` if the job has no report yet (not `done`)
- [ ] The filename is sanitized (only letters, digits, dot, dash)
- [ ] Tests cover both formats, the 404 and the 409 cases
- [ ] Typecheck passes; tests pass

### US-007: Page shell and cold-toned design system
**Description:** As a reviewer, I want a calm, minimalistic page so that the tool looks professional at first glance.

**Acceptance Criteria:**
- [ ] `index.html` + `styles.css` + `app.js` in `src/seo_audit/web/static/`, no external CDN, fonts, or build step (system font stack)
- [ ] Design tokens as CSS custom properties: a cold slate/ice-blue palette (e.g. near-white blue-grey background, slate text, one steel/ice-blue accent), a type scale, spacing scale, radius, and subtle 1px borders instead of heavy shadows; no gradients, no emoji
- [ ] Severity colours (critical/high/medium/low/info) are muted cold-leaning tones that stay distinguishable and meet WCAG AA contrast for text on their badge backgrounds
- [ ] Layout: a narrow centred column (max ~880px) with a header (product name "SEO Audit" and a one-line description), the form card, and an empty results region
- [ ] Respects `prefers-color-scheme: dark` with a cold dark variant using the same tokens
- [ ] Works at 360px width with no horizontal scroll
- [ ] Typecheck passes; tests pass (existing tests still green)
- [ ] Verify in browser using dev-browser skill (desktop and phone width, light and dark)

### US-008: Audit form
**Description:** As a reviewer, I want a simple form so that I can choose what to audit.

**Acceptance Criteria:**
- [ ] On load, the page calls `GET /api/config` and fills the defaults
- [ ] Fields: URL text input (placeholder `https://example.com`, autofocus), mode as a two-option segmented control (Page / Site, default Page), max pages number input (shown only when mode is Site, bounded 1 to `max_pages_limit`), model text input pre-filled with the configured model and a helper line "Any LiteLLM model, e.g. openai/gpt-4o"
- [ ] A primary "Run audit" button; pressing Enter in the URL field submits
- [ ] Inline validation message under the URL field for empty input; server 422 messages are shown inline in the form, not as browser alerts
- [ ] Controls have visible labels, visible keyboard focus rings, and are fully usable by keyboard
- [ ] Typecheck passes; tests pass
- [ ] Verify in browser using dev-browser skill

### US-009: Run an audit with live progress
**Description:** As a reviewer, I want to see progress as the agent works so that I know it has not stalled.

**Acceptance Criteria:**
- [ ] Submitting the form calls `POST /api/audits`, disables the form, and shows a progress card with the audited URL, a subtle state indicator, and a log of progress messages streamed from `/api/audits/{id}/events` (newest at the bottom, auto-scrolled)
- [ ] A "Cancel" button calls the cancel endpoint; the UI then shows a "Cancelled" state and re-enables the form
- [ ] A 409 "already running" response shows an inline message in the form
- [ ] On completion the progress card collapses to a one-line "Finished in N s" note and the results region renders (results rendering is implemented in US-010 to US-012; this story may show a placeholder)
- [ ] If the stream drops, the page falls back to polling `GET /api/audits/{id}` every 2 seconds until the job ends
- [ ] The spinner/indicator animation is disabled under `prefers-reduced-motion`
- [ ] Typecheck passes; tests pass
- [ ] Verify in browser using dev-browser skill (page-mode audit against a local test server or a mocked backend)

### US-010: Results summary and top actions
**Description:** As a reviewer, I want the headline results first so that I understand the outcome at a glance.

**Acceptance Criteria:**
- [ ] A summary row of compact stat tiles: pages crawled, pages skipped, total findings, and findings per severity (critical, high, medium, low, info) as small badges
- [ ] A meta line showing mode, model used, and total tokens; if `crawl.limit_hit` is set, a visible but calm notice explains which limit stopped the crawl
- [ ] For site audits, a "What to fix first" ordered list from `site_summary`; hidden when empty
- [ ] If `dropped_model_findings` is greater than 0, a small note explains that N model findings were dropped because their evidence could not be verified
- [ ] All text from the report is inserted with `textContent` (or escaped); no raw HTML injection from audited sites
- [ ] Typecheck passes; tests pass
- [ ] Verify in browser using dev-browser skill

### US-011: Findings list
**Description:** As a reviewer, I want findings grouped and filterable so that I can inspect the agent's output in detail.

**Acceptance Criteria:**
- [ ] Findings are grouped by scope: "Site-wide" first (from `site_findings`), then one collapsible section per audited page (URL, status code, finding count), each section sorted by severity (critical to info)
- [ ] Each finding is a row showing a severity badge, a source badge (`code` or `model`), the check id and category; expanding it shows evidence, recommendation, and (for model findings) the confidence as a percentage
- [ ] Severity filter chips (All plus each severity with counts) and a source filter (All / Code / Model); filtering updates the view without a page reload and shows "No findings match" when empty
- [ ] The first page section and the highest-severity findings are visible without scrolling on a 1280x800 viewport after load; other sections start collapsed when there are more than 5 pages
- [ ] Rows are keyboard-operable (Enter/Space toggles) with `aria-expanded`
- [ ] Typecheck passes; tests pass
- [ ] Verify in browser using dev-browser skill

### US-012: Downloads, report errors and empty states
**Description:** As a reviewer, I want to download the report and understand failures so that nothing fails silently.

**Acceptance Criteria:**
- [ ] "Download Markdown" and "Download JSON" buttons in the results header link to the US-006 endpoints
- [ ] If the report has `errors`, show a collapsible "Issues during the audit" section listing stage, message and URL (this covers a missing API key or model failure)
- [ ] When the audit is a total failure (no page fetched, per `is_total_failure`), show a clear error panel instead of an empty results view, plus the errors list
- [ ] A job in state `failed` shows its error message in the same style
- [ ] A page with zero findings shows a calm "No findings" state
- [ ] A "New audit" button resets the results and re-enables the form while keeping the last used values
- [ ] Typecheck passes; tests pass
- [ ] Verify in browser using dev-browser skill

### US-013: Safe defaults and docs
**Description:** As a maintainer, I want the UI to be safe to run on a laptop and documented so that anyone can follow the steps.

**Acceptance Criteria:**
- [ ] The server binds to `127.0.0.1` by default; passing `--host 0.0.0.0` prints a warning that the UI has no authentication and can make the server fetch arbitrary URLs
- [ ] Response headers on the HTML page include a restrictive `Content-Security-Policy` (`default-src 'self'`) and `X-Content-Type-Options: nosniff`; the page works under that policy (no inline scripts or styles)
- [ ] README gains a "Web UI" section: install, `seo-audit-ui`, URL, screenshots description, controls, downloads, that keys come from `.env`, and the limitation of one audit at a time and no authentication
- [ ] DESIGN.md gains a short "Web UI" note: thin layer over `run_audit`, in-memory jobs, SSE for progress, why vanilla JS with no build step
- [ ] Tests assert the security headers
- [ ] Typecheck passes; all tests pass
- [ ] Verify in browser using dev-browser skill (full run: page audit end to end, then a site audit with the cancel button)

## Functional Requirements

- FR-1: `seo-audit-ui` starts a local web server (default `127.0.0.1:8000`) that serves the UI and a JSON API.
- FR-2: The UI must let the user enter a URL, choose page or site mode, set max pages (site mode), and set the model.
- FR-3: Starting an audit must reuse the existing `validate_url`, config loading with overrides, `run_audit`, `LLMClient`, and `render_markdown`. Audit logic must not be duplicated.
- FR-4: Only one audit may run at a time; a second request returns 409.
- FR-5: Progress messages produced by `run_audit` must be streamed to the page live via Server-Sent Events, with polling as a fallback.
- FR-6: A running audit must be cancellable from the UI.
- FR-7: The results view must show summary statistics, crawl limit notices, top actions (site mode), and findings grouped by site/page and sorted by severity, each labelled with severity, source (`code` / `model`) and, for model findings, confidence.
- FR-8: Findings must be filterable by severity and by source in the browser.
- FR-9: The UI must offer Markdown and JSON downloads of the finished report.
- FR-10: Errors recorded in the report (such as missing API key, model failure, fetch failure) and total failures must be visible in the UI.
- FR-11: All report content rendered in the browser must be escaped or inserted as text.
- FR-12: API keys are never sent to, requested by, or shown in the browser.
- FR-13: The visual design uses a cold slate/ice-blue palette, system fonts, a single centred column, light and dark variants, and is usable at 360px width.

## Non-Goals (Out of Scope)

- No authentication, user accounts, or multi-user support.
- No persistent storage or history of past audits (in-memory only; lost on restart).
- No concurrent audits and no job queue.
- No editing of `config.yaml` or other crawl settings (depth, time budget, concurrency) from the UI.
- No entering or storing API keys in the UI.
- No React/Vue/Node build tooling, no external CDN assets.
- No changes to checks, the crawler, the report schema, or CLI behaviour.
- No SSRF protection beyond the localhost default and a warning (documented as a known limitation).
- No deployment, Docker, or hosting setup.
- No charts or heavy data visualisation.

## Design Considerations

- Tone: calm, clinical, cold. Think near-white blue-grey surfaces (about `#f5f8fb`), slate text (`#1f2a37`-ish), a single steel/ice-blue accent (about `#3b6e9c`), 1px borders in a pale blue-grey. Dark variant: deep blue-black surfaces with the same accent lightened.
- Typography: system font stack; one heading style, one body size, a smaller muted caption size; monospace only for URLs, ids and evidence.
- Whitespace over decoration. No icons required; if used, inline SVG only. No emoji.
- Severity palette must stay muted (no saturated red/green); differentiate also by label text, not colour alone.
- Components to build once and reuse: card, badge, chip, button (primary/secondary), segmented control, stat tile, collapsible section.
- Use semantic HTML (`main`, `section`, `button`, `details`/`summary` or ARIA-correct disclosure), visible focus, and sufficient contrast.

## Technical Considerations

- Backend lives in `src/seo_audit/web/` (`__init__.py` with `create_app`, `jobs.py`, `static/`). Follow existing conventions: `from __future__ import annotations`, strict mypy (`python -m mypy`), pytest with mocked HTTP/LLM (no network or API key in tests).
- `run_audit` signature: `run_audit(url, mode, config, client, llm, progress)`; the CLI helpers `_load`-style override logic should be reused or extracted into a shared function rather than copied.
- `make_client(timeout)` and `LLMClient(model, temperature)` are the existing factories; make them injectable for tests.
- SSE can be implemented with `StreamingResponse` and `text/event-stream`; no extra dependency needed.
- Windows is the primary dev platform: avoid asyncio patterns that fail on the Windows event loop, keep paths cross-platform, and mind that the test for the stream should not hang (use timeouts).
- Static assets must ship with the package (`[tool.setuptools.package-data]`).
- The existing Markdown renderer is the single source for the `.md` download; do not reimplement it in JS.

## Success Metrics

- From a fresh clone with `.env` set: `pip install -e .`, `seo-audit-ui`, open the URL, paste a URL, and see a finished page audit in under a minute.
- A reviewer can find the top issue and its recommendation without scrolling more than one screen on a page audit.
- All existing tests plus the new web tests pass; strict mypy passes.
- No console errors in the browser during a full run; keyboard-only operation is possible end to end.

## Open Questions

- Should a later version add audit history or shareable report links? (Out of scope now.)
- Should the UI warn when no API key is detected before running? (Currently surfaced only through the report's `errors`.)
- Is a default port other than 8000 preferable on the reviewer's machine? (Configurable via `--port`.)
