# Design decisions

This document explains the main trade-offs in the SEO audit agent: what runs in code versus the
model, how the crawl is bounded, and how the output is structured. For setup and usage see
[README.md](README.md); for how quality would be measured see [EVALUATION.md](EVALUATION.md).

The pipeline is a fixed sequence, not a free-form tool-using agent loop:

```
fetch/crawl -> extract facts -> code checks (page, site) -> model checks (per page)
            -> site synthesis (site mode only) -> schema-validated report (JSON or Markdown)
```

A fixed pipeline is a deliberate choice. The task is bounded and the steps are known up front, so
letting a model decide which tool to call next would add cost, variance and failure modes without
adding coverage. The model is used where judgement is needed, as a function call inside the
pipeline, behind one interface (`llm.complete`).

## 1. Code checks versus model checks

### The split

| Runs in | Checks | Why |
| --- | --- | --- |
| **Code** (`checks.py`) | Title missing/short/long (30-60 chars), meta description missing/short/long (70-160), H1 missing/multiple, heading-level skips, canonical missing or off-host, `noindex`, missing viewport, images without `alt`, non-HTTPS, redirect chains over 1 hop, slow response (> 2 s), invalid JSON-LD, missing Open Graph tags | Each is a yes/no question about structure that has an objective answer in the HTML or HTTP response. |
| **Code** (`site_checks.py`) | Duplicate titles, duplicate meta descriptions, broken internal links (4xx/5xx among crawled pages), `robots.txt` missing/empty, `sitemap.xml` missing/invalid/empty | These are set operations over many pages. A model would have to hold every page in context to do the same thing less reliably. |
| **Model** (`model_checks.py`) | Title and meta description *quality* (vague, keyword-stuffed, mismatched with the content), content quality, whether the page matches a clear search intent | There is no rule for "is this title compelling and accurate". It needs reading comprehension. |
| **Model** (`synthesis.py`) | A prioritized list of top actions for the site | Turning many findings into "fix this first" needs weighing severity against breadth and phrasing it for a human. |

The rule used to decide: if a check can be written as a deterministic function of the extracted
facts, it goes in code. The model gets only what code cannot decide. The model never re-derives
facts that code already extracted (it is handed the title, headings and counts rather than raw
HTML), and it is not asked to find broken links, count H1s or check lengths.

### Pros and cons of this split

**Pros**

- **Reproducible and free.** Code findings are identical across runs and cost nothing, so they
  can be regression-tested with fixtures and diffed between audits. Only the subjective layer
  varies.
- **Fast.** Code checks add negligible latency. Model calls are the slow, billed part, and they
  run only once per HTML page with a 2xx/3xx status.
- **Failure isolation.** If the provider is down, the key is missing or output is malformed, the
  code findings are still returned and the failure is recorded in `errors`. The report is never
  all-or-nothing on the model.
- **Calibrated trust.** Code findings carry `confidence: 1.0` and `source: code`; model findings
  carry a model-estimated confidence and `source: model`. A reader can weight them differently.
- **Cheap model swaps.** With the objective work in code, changing model changes only the
  subjective findings, which makes a model comparison simple to scope.
- **Extensible.** Both check kinds use registries (`@page_check`, `@site_check`), so adding a
  check does not touch the orchestrator.

**Cons**

- **Fixed thresholds are blunt.** A 61-character title is flagged `title-long` although search
  engines truncate by pixel width, not characters. The limits are module constants in
  `checks.py` rather than config, and are heuristics rather than ground truth.
- **Rules cannot see context.** "Missing Open Graph tags" is reported for every page, including
  ones where social sharing does not matter. Severity is static per check.
- **The boundary is a judgement call.** Some checks sit in between. "Duplicate titles" is
  exact-match after whitespace and case normalization, so near-duplicates ("Shoes | Shop" versus
  "Shoes - Shop") are missed. A model could catch them, but at the cost of the reproducibility
  above, so they are left out.
- **Model judgement is partial by construction.** The model sees the extracted facts and the
  visible text capped at `max_text_chars` (8000 by default), not the whole page and not the
  rendered page. Long pages are judged on their first part only.
- **Model output is non-deterministic.** Even at temperature 0 two runs can differ, and the
  self-reported `severity` and `confidence` are not calibrated. Treat them as ordinal hints.
- **No link-level checks beyond the crawl.** Broken-link detection only covers URLs the crawl
  fetched. Links to pages beyond the page/depth limits or on other hosts are not verified.

### Keeping the model swappable

Every model call goes through `LLMClient.complete(system, prompt, ResponseModel)` in `llm.py`,
which uses LiteLLM. The model name comes only from `config.yaml`, the `SEO_AUDIT_MODEL`
environment variable, or `--model`. The wrapper:

- requests JSON and validates it against a pydantic model, retrying once on malformed output
  (then raising `LLMError`, which callers convert to a report error);
- passes `drop_params=True`, so a model that rejects a parameter (some newer models reject
  `temperature=0`) does not need a code change;
- accumulates token usage under a lock, because calls run in worker threads.

Pros: no provider-specific code in the agent logic, and tests inject a fake completion function.
Cons: the lowest-common-denominator interface (plain JSON in the prompt, no native tool use or
structured-output modes), and prompts are tuned against one model first, so quality on others is
unverified until measured (see EVALUATION.md).

## 2. How the crawl is bounded

Site mode (`crawler.py`) is a breadth-first crawl from the start URL. Every dimension that can
make cost or runtime unpredictable has an explicit limit, all in `config.yaml`:

| Bound | Setting (default) | Behaviour |
| --- | --- | --- |
| Pages | `max_pages` (50) | The batch for each level is truncated to the remaining room; overflow URLs are skipped and listed. |
| Depth | `max_depth` (3) | Links found on pages at the maximum depth are not followed; they are recorded as skipped. |
| Total time | `total_time_budget` (300 s) | A deadline is set when the crawl starts. Each fetch is wrapped in a timeout equal to the time remaining, and unstarted URLs are skipped once it passes. |
| Per request | `request_timeout` (15 s), 2 MB response cap | One slow or huge URL cannot hang the run; non-HTML responses are not read as pages. |
| Concurrency | `concurrency` (4) | A semaphore caps in-flight fetches. The same cap applies to concurrent model calls. |
| Scope | `include_subdomains` (false) | Same host only (`www.` is treated as the same site). Subdomains are included only when configured. |
| robots.txt | `respect_robots` (true) | Disallowed URLs are not fetched and are recorded as skipped. `Crawl-delay` spaces out requests through a shared slot lock. |
| Sitemap | `use_sitemap` (true) | URLs from `sitemap.xml` (including index files, capped at 5 files / 1000 URLs) are added as depth-0 seeds. |
| Duplicates | always on | URLs are normalized before dedupe (see below). |

The resulting `CrawlSummary` reports `pages_crawled`, `pages_skipped`, the skipped URLs, and
`limit_hit`, the first limit that caused skips (`max_pages`, `max_depth` or `time_budget`).
Robots-blocked URLs are listed as skipped but do not set `limit_hit`, since that is a policy
choice rather than a resource limit. An audit that hit a limit therefore says so, instead of
silently presenting a partial crawl as complete.

**URL normalization** (`urls.py`) lowercases scheme and host, drops default ports and fragments,
strips tracking parameters (`utm_*`, `gclid`, `fbclid`), sorts the remaining query, and removes
trailing slashes (the root stays `/`). This stops the crawler looping on equivalent URLs and
spending its page budget on duplicates.

### Trade-offs

- **BFS over DFS** finds the shallowest, usually most important, pages first, so when
  `max_pages` cuts the crawl short the pages you do get are the ones nearest the home page.
  The cost is that deep content is the first thing missed.
- **Level-by-level gathering** keeps the logic simple and the limit accounting exact, but one slow
  page holds up its whole level (bounded by `request_timeout`). A work queue with workers would
  be faster but harder to reason about for limits.
- **Hard stops over sampling.** The crawl never samples or estimates, so reports on large sites
  are truncated rather than extrapolated. Site-level findings such as duplicate titles are
  therefore findings about the crawled subset, and the crawl summary is part of how to read them.
- **Normalization is conservative.** Stripping trailing slashes and sorting query parameters can,
  on unusual servers, merge two URLs that return different content. Parameters other than the
  known tracking ones are kept, so faceted-navigation sites can still burn the page budget on
  near-duplicate URLs. `max_pages` is the backstop.
- **Politeness costs time.** Honoring `Crawl-delay` against a 300 s budget can mean far fewer
  pages than `max_pages`; the report shows this as `limit_hit: time_budget`. Robots handling can
  be disabled with `respect_robots: false`, which is only appropriate for sites you own.
- **Static HTML only.** There is no JavaScript rendering, which keeps the crawl fast, cheap and
  dependency-light but means client-rendered sites look nearly empty. This is documented as a
  known limitation, and EVALUATION.md includes JS-heavy sites as a robustness case.
- **Cost is bounded by the same limits.** Model calls happen once per fetched HTML page plus one
  synthesis call, so `max_pages` is also the cap on model spend (at most `max_pages + 1` calls,
  plus one retry each for malformed output). Token usage is reported in every run.

## 3. Output structure

The report is a pydantic model (`schema.py`) exported to `schema/audit-report.schema.json`. A test
fails if the committed schema file is stale, and CLI JSON output is validated against it.

```
AuditReport
  url, mode (page|site), model
  crawl: CrawlSummary          pages_crawled, pages_skipped, limit_hit, skipped_urls
  pages: [PageResult]          url, final_url, status_code, findings: [Finding]
  site_findings: [Finding]     cross-page findings from code checks
  site_summary: [str]          model-written, prioritized top actions (site mode)
  dropped_model_findings: int  model findings discarded by evidence verification
  errors: [ReportError]        stage, message, url: failures that did not abort the run
  token_usage                  prompt / completion / total tokens
```

`Finding` has `id` (stable, e.g. `title-missing`), `category`, `severity`
(`critical|high|medium|low|info`), `source` (`code|model`), `evidence`, `recommendation` and
`confidence`.

Choices and reasons:

- **Machine-readable first.** JSON validated against a published schema lets other tools consume
  the report without parsing prose; the Markdown renderer is a view over the same object.
  All models use `extra="forbid"`, so schema drift is caught rather than silently passed.
- **Stable finding ids.** Ids make findings comparable between runs, models and tools, which is
  what makes regression tests and reference-tool comparisons possible.
- **`source` and `confidence` are separate.** `source` says *how* a finding was produced;
  `confidence` says how sure the producer was. Code findings are `1.0` because they are
  deterministic given the fetched page, not because they are guaranteed to matter. Model
  confidence is self-reported and uncalibrated, so it should be used for ranking and filtering,
  not read as a probability.
- **Failures are data.** Fetch errors, model errors and synthesis errors go in `errors` and the
  report is still produced. Exit code 1 is reserved for total failure (no page fetched
  successfully), 2 for invalid input or configuration.
- **Findings are placed where they apply**: per page for page-level issues, in `site_findings`
  for cross-page ones, so the evidence for a duplicate title lists every affected URL once rather
  than repeating the finding on each page.

### Evidence verification of model findings

Models can assert problems that are not there. To limit that, the model must return an
`evidence_quote` with every finding, copied verbatim from the page title, meta description or
visible text it was given. `quote_in_page` checks the quote against that text,
whitespace- and case-insensitively. A finding whose quote is not found is dropped, and the count
is recorded in `dropped_model_findings`. Findings that survive carry the quote as their
`evidence`, so a reader can find it on the page.

Pros: fabricated or paraphrased "evidence" cannot reach the report, every model claim points at
real text, and the drop count is a cheap hallucination-rate signal to track per model.

Cons, stated plainly:

- It verifies *that the quote exists*, not *that the judgement is right*. A real quote attached to
  a wrong conclusion passes.
- It cannot ground findings about absence ("no clear call to action"). The model has to quote
  something, so such findings are either dropped or attached to an incidental quote.
- A strict substring match drops legitimately correct findings if the model lightly paraphrases or
  normalizes punctuation, which biases the report towards under-reporting. The dropped count
  shows how often this happens.
- The check only covers text the model was shown, which is capped at `max_text_chars`.

### Site synthesis

The synthesis model call receives aggregated findings only: per check id, severity, number of
pages affected, up to three example URLs, plus site-wide findings summarized by id and truncated
evidence. It sees no page text. This keeps the prompt small and bounded regardless of crawl size,
and keeps the synthesis grounded in what the checks found. The trade-off is that it cannot
notice patterns that no check detected. Its output is capped at ten actions. It is skipped in
page mode, and on failure `site_summary` is left empty with an error entry rather than failing
the report.

## Known limitations

- Static HTML only; no JavaScript rendering.
- Thresholds (title and description length, slow response) are fixed constants.
- Broken-link detection covers crawled URLs only.
- Model severity and confidence are uncalibrated; evidence verification checks quotes, not
  correctness.
- Only exact duplicates are detected for titles and descriptions.
- Quality against real sites and across models has not been measured yet; that is the purpose of
  the evaluation plan in EVALUATION.md.
