# Evaluation proposal

This document proposes how to measure the quality of the SEO audit agent. **None of the numbers
or datasets below exist yet**: this is a plan, not a results report. The only checks that run today
are the unit and end-to-end tests in `tests/` (mocked HTTP, fake model), which verify behaviour but
not quality against real sites or real models. See [DESIGN.md](DESIGN.md) for the design being
evaluated.

The split in DESIGN.md drives the evaluation design. Code checks are deterministic, so they get
classic precision/recall against labeled fixtures. Model checks are subjective and non-deterministic,
so they get human labels, agreement statistics and repeated runs. The crawler and the report as a
whole get robustness, reference-tool and cost/latency evaluation.

## What "good" means

| Layer | Quality question | Primary metric |
| --- | --- | --- |
| Code page checks | Does each check fire exactly when its condition holds? | Precision and recall per finding id |
| Code site checks | Are cross-page findings complete and correct? | Precision and recall per id, plus affected-URL set accuracy |
| Crawler | Does it stay in bounds and cover what it should? | Limit adherence (hard), coverage of a known URL set |
| Model page checks | Are the findings true, useful and grounded? | Human-judged precision, recall against a labeled set, grounding rate |
| Synthesis | Does the priority list reflect the real findings? | Human rating plus a faithfulness check against the input findings |
| Whole report | Does it agree with established tools, at what cost? | Agreement with reference tool, cost and latency per page |

## 1. Code checks: fixtures with precision and recall per check

Every id emitted by `checks.py` and `site_checks.py` (27 ids at the time of writing, for example
`title-short`, `heading-skip`, `canonical-off-host`, `redirect-chain`, `duplicate-titles`,
`sitemap-invalid`) gets a labeled fixture set.

**Fixture design**

- A fixture is an HTML file plus recorded HTTP metadata (status, redirect chain, response time),
  served by a mock transport so runs are offline and deterministic.
- Each fixture has a label file listing the finding ids that *should* be present and, where it
  matters, the evidence (for example which URLs are duplicates).
- For each id build positives, negatives and **boundary cases**: titles of 29, 30, 60 and 61
  characters, a description at 69, 70, 160 and 161, a response at exactly the slow threshold, a
  heading skip h1 to h3 versus h1 to h2 to h3, `noindex` in a robots meta versus the word in body
  text, an image with `alt=""` versus no `alt`, JSON-LD that is valid JSON but the wrong shape.
- Add **adversarial real-world HTML**: unclosed tags, multiple `<title>` elements, `<title>` inside
  an inline SVG, meta tags with uppercase attribute names, relative and protocol-relative canonicals,
  non-UTF-8 pages.
- Seed the corpus from the existing `tests/` fixtures, then grow it with HTML sampled from real
  pages (with the label made by hand), since synthetic pages only test cases the author thought of.

**Metrics**

- Per id: true positives, false positives, false negatives, precision, recall, F1.
- Micro-averaged and macro-averaged across ids. Macro matters here because common checks (title,
  description) would otherwise hide failures in rare ones (`json-ld-invalid`).
- For site checks, also compare the *evidence set*: the reported affected URLs against the labeled
  set, scored by Jaccard similarity, since a finding that fires with the wrong URLs is still wrong.

**Target.** These are rule-based, so the bar is high: precision and recall of 1.0 on the synthetic
and boundary fixtures (any miss is a bug), and at least 0.98 on the sampled real-world set, with
every miss triaged as either a bug or a label error. Because code findings are deterministic, this
suite is exact and can run in CI on every commit.

**Threshold validity is a separate question.** A check can be implemented correctly yet flag
things that do not matter (a 61-character title). Track "fires but the practitioner would not act on
it" as a separate usefulness rate from the human-labeled page set in section 2, and use it to
decide whether to move thresholds into config.

## 2. Model checks: a labeled page set and human agreement

### Dataset

Build a set of **150 to 300 pages**, stratified so that no single kind dominates:

- page type: article, product, category, home, documentation, landing page, thin or empty page;
- quality: deliberately good, mediocre and poor titles and descriptions;
- language and length: English plus a few other languages, short and over `max_text_chars`;
- source: a mix of real public pages and hand-edited versions where a flaw is injected on purpose
  (vague title, keyword-stuffed description, content that does not match the title). Injected flaws
  give known positives; unedited pages give honest negatives.

Freeze the HTML snapshots (store the bytes, not the URL) so the set never drifts, and hold out about
30 percent as a **test split** that is not used when tuning prompts.

### Human labels

- At least **two annotators** per page, working from the same input the model sees (extracted facts
  plus truncated visible text), not the live site. A third person adjudicates disagreements.
- Annotators write a short guideline-driven judgement per aspect: title quality, description
  quality, content quality, search intent match. Each is a 3-point rating (good / needs work / poor)
  plus a list of issues they would report.
- **Inter-annotator agreement** is measured first (Cohen's kappa for two raters, Krippendorff's
  alpha if more; for the 3-point ordinal rating use weighted kappa). If humans agree poorly
  (kappa below about 0.6), the guideline is revised before any model is scored, because model
  agreement cannot exceed the reliability of the labels it is measured against.

### Scoring the model's findings

For each page, match model findings to human issues by category and meaning (done by the
adjudicator, not by string equality), then report:

- **Precision:** share of model findings a human accepts as real and worth reporting. This is the
  number that matters most for trust.
- **Recall:** share of adjudicated human issues the model found.
- **Rating agreement:** weighted kappa between the model's implied rating (derived from the
  severities of its findings per aspect) and the human consensus rating.
- **Severity calibration:** whether higher model severity corresponds to higher human-rated
  importance (Spearman correlation), and whether `confidence` is informative (does precision rise
  with confidence? plot a reliability curve). Self-reported confidence is not assumed to be calibrated
  (DESIGN.md); this measures whether it is useful for ranking even if it is not a probability.
- **Grounding metrics** from the evidence-verification step: `dropped_model_findings` divided by total
  proposed findings gives the *drop rate*; and a human audit of a sample of *kept* findings gives the
  **false-keep rate** (real quote, wrong conclusion), the failure mode DESIGN.md says verification
  cannot catch. Also audit a sample of *dropped* findings to estimate how many were correct but
  paraphrased, which measures the under-reporting bias of strict substring matching.
- **Run-to-run variance:** run each page N times (N of 5) and report the finding-set Jaccard
  similarity between runs and the standard deviation of the derived rating.

### LLM-as-judge, calibrated against humans

Human labeling does not scale to every prompt or model change, so use an LLM judge for fast
iteration, but only after showing it can stand in for people.

1. Write a rubric prompt asking a judge model to rate each model finding (accurate / partially /
   wrong, useful / not useful) and each page's coverage, given the page input and the finding.
2. Run the judge on the **human-labeled set** and compute agreement with human consensus (weighted
   kappa and Spearman). Require agreement close to the human-human level (within about 0.1 kappa)
   before trusting it; otherwise iterate on the rubric.
3. Guard against known judge biases: use a judge from a **different model family** than the model
   being scored, randomize the order of options, mask the model name, and check for length and
   verbosity bias by comparing scores of findings with the text padded.
4. Re-calibrate whenever the judge model or rubric changes, and keep a small **gold subset
   (about 50 pages)** that always gets human review, so judge drift is noticed.
5. Report judge results with a confidence interval (bootstrap over pages) and never use the judge
   alone for a release decision on the test split; humans are the reference.

## 3. Comparison to a reference tool

Run the agent and an established tool on the same URLs and compare outputs.

- **Lighthouse SEO** (CLI, JSON output) is the easiest reference for page-level checks. Its audits
  for document title, meta description, image alt, viewport, canonical, hreflang, robots/crawlability
  and HTTP status map onto several code checks (the exact audit ids depend on the Lighthouse version
  pinned for the comparison). Map agent finding ids to Lighthouse audits by hand once, and store the
  mapping.
- **Screaming Frog SEO Spider** (CLI or exported CSV) is the reference for site-level checks:
  duplicate titles and descriptions, response codes, redirect chains, canonicals, sitemap
  inconsistencies. It is a licensed tool, so exports for a fixed site list would be produced once and
  stored.

**Protocol.** Pick 30 to 50 URLs and 5 to 10 small sites (including ones the team controls so the
ground truth is known). For each mapped check compute agreement (Cohen's kappa on pass/fail), and
list the disagreements. Disagreements are not automatically agent errors. Triage each one as:
agent bug, reference-tool bug, different definition (for example, a different title-length rule) or
real ambiguity, and fold confirmed agent bugs back into the section 1 fixtures. Report coverage
separately: checks the agent has that the reference lacks (model judgement) and the reverse (for
example, Lighthouse checks for tap targets or font size that the agent does not implement).

The reference tools give an independent sanity check for the code layer. They give nothing for the
model layer, since neither does subjective quality judgement; that is why section 2 relies on
humans.

## 4. Robustness cases

A fixed suite of hostile or awkward targets, run against both mocked servers (deterministic, in CI)
and a small number of real sites (scheduled, not blocking). Pass criteria are about *behaviour*:
terminate within bounds, produce schema-valid output, record the problem in `errors`, and return the
right exit code.

| Case | Expected behaviour |
| --- | --- |
| Site with broken pages (404, 500, soft 404) | Reported by `broken-internal-links`; crawl continues; no crash |
| Redirect loops and long redirect chains | Fetch returns an error result; `redirect-chain` finding where relevant; no hang |
| Slow server / hung connection | Request timeout fires; time budget respected; `limit_hit: time_budget` recorded |
| Very large page or endless response | Response size cap applies; no memory blow-up |
| Non-HTML (PDF, image, JSON) linked from pages | Skipped as not HTML; not counted as a failed page |
| robots.txt blocking everything / partially | Blocked URLs recorded as skipped; with the start URL blocked, a clear total-failure result |
| `Crawl-delay` larger than the time budget | Few pages crawled, `limit_hit: time_budget`, finishes on time |
| Faceted navigation / infinite URL space (calendar, session ids) | `max_pages` backstop holds; normalization removes tracking parameters |
| Huge sitemap, sitemap index, malformed sitemap | Caps applied (5 files, 1000 URLs); malformed sitemap reported, crawl continues |
| **JavaScript-heavy site** (client-rendered shell) | Documented limitation: expect few facts and many "missing" findings. Measure how misleading the report is, and check whether the agent flags a near-empty page rather than reporting a confident list of false problems |
| Malformed HTML, wrong charset, empty body | Extraction does not raise; sensible findings |
| Non-English and very long pages | Model input respects `max_text_chars`; no prompt overflow |
| Model failures: no API key, rate limit, malformed JSON, empty output | Code findings still returned; `errors` entries; exit 0 unless nothing at all succeeded |
| Prompt injection in page text ("ignore previous instructions...") | Output stays schema-valid; injected text does not change findings or leak instructions. Verify with planted pages |

The JS-heavy case is the one expected to *fail* on quality (not on behaviour). It is included to
quantify the cost of the static-HTML decision: run the agent against a set of client-rendered sites
and a headless-browser baseline, and report how often static extraction misses the title, headings
or links that the rendered DOM has. That number decides whether JS rendering is worth adding.

## 5. Multi-model comparison

Because the model is config-swappable, compare candidate models (for example
`anthropic/claude-sonnet-5-5`, `openai/gpt-4o`, and one smaller or open-weight model) on the **same**
frozen page set and identical code-check results.

For each model report:

| Dimension | How it is measured |
| --- | --- |
| Quality | Human-judged precision, recall and rating agreement from section 2 (judge-assisted where calibrated) |
| Grounding | Drop rate, false-keep rate |
| Format reliability | Rate of malformed output needing the retry, rate of `LLMError` after retry |
| Cost | Token usage from the report times current list price, per page and per 50-page site audit |
| Latency | Wall-clock seconds per page and per audit, p50 and p95, at the configured concurrency |
| Variance | Finding-set Jaccard and rating standard deviation across N repeated runs |

Notes on doing this fairly:

- Fix temperature and use identical prompts. Prompts are tuned against one model first
  (DESIGN.md), so a lower score for another model may be a prompt issue; record which model the
  prompt was developed on, and optionally allow a small per-model prompt budget to separate
  "model is worse" from "prompt is mismatched".
- Run repeats on different days when latency matters, since provider load varies. Report
  confidence intervals, not single values, and do not rank models whose intervals overlap.
- Present a cost-versus-quality view (quality per dollar) so that a cheaper model that is almost as
  good is visible as such.

## 6. CI regression thresholds and success metrics

### Tiers

| Tier | When | Contents | Needs API key |
| --- | --- | --- | --- |
| 1: fast | Every commit | Unit tests, mypy strict, fixture precision/recall for code checks, schema and snapshot tests, mocked robustness suite, fake-model end-to-end | No |
| 2: model | Nightly and on prompt, model or `model_checks.py`/`synthesis.py` changes | Model checks on the held-out split, judge-scored, with a small N of repeats | Yes |
| 3: external | Weekly or before a release | Reference-tool comparison, real-site robustness runs, multi-model comparison | Yes |

### Proposed thresholds

These are starting points, to be set from a measured baseline once the datasets exist, and then
tightened.

| Gate | Threshold | Tier |
| --- | --- | --- |
| Code checks, synthetic and boundary fixtures | Precision = 1.0 and recall = 1.0 per id; any regression fails the build | 1 |
| Code checks, sampled real-world set | Per-id precision and recall >= 0.98, and no id drops more than 0.01 versus the baseline | 1 |
| Output schema | 100 percent of reports validate against `schema/audit-report.schema.json`; committed schema not stale | 1 |
| Crawl limits | 0 violations of `max_pages`, `max_depth`, `total_time_budget` (plus a small grace for in-flight requests) across the robustness suite | 1 |
| Exit codes and error handling | Every robustness case ends with the expected exit code and `errors` entries | 1 |
| Model precision (human-confirmed or calibrated judge) | >= 0.80 on the held-out split, and no drop of more than 0.05 versus the baseline | 2 |
| Model recall | >= 0.60 on injected-flaw pages, no drop of more than 0.05 versus the baseline | 2 |
| Grounding | Drop rate <= 0.20; false-keep rate (sampled) <= 0.10 | 2 |
| Format reliability | `LLMError` after retry on <= 1 percent of page calls | 2 |
| Variance | Mean run-to-run finding Jaccard >= 0.70 | 2 |
| Judge validity | Judge-human agreement within 0.1 kappa of human-human agreement, re-checked on the gold subset | 2 |
| Cost and latency | Median tokens per page and p95 seconds per page within +25 percent of baseline | 2 |
| Reference tool | Agreement >= 0.90 (kappa) on mapped checks after triage of definitional differences | 3 |

Regression gates compare to a **stored baseline** (committed metric files per tier) rather than only
to absolute numbers, so a gradual decline is caught. Model-tier gates use bootstrap confidence
intervals, and fail only when the interval is clearly below the threshold, to avoid flaky builds
from model noise. A failing model-tier run blocks the change that triggered it, but a flaky provider
(timeouts, rate limits) is reported as "inconclusive" rather than as a quality failure.

### Success metrics for the agent

The agent can be called successful when, on the frozen sets:

1. **Trustworthy:** at least 80 percent of model findings and at least 98 percent of code findings are
   confirmed correct by humans or the reference tool.
2. **Useful:** practitioners, in a small blind review of full reports, say they would act on most
   top-priority items, and rate the site-level priority list as better than a count-sorted list of
   findings (the baseline it must beat).
3. **Bounded:** no run exceeds its configured page, depth or time limits, and cost per 50-page audit
   stays inside a stated budget.
4. **Honest:** when something is missing (limit hit, model failed, page not fetched, JS-rendered
   site) the report says so, which is checked directly by the robustness suite.
5. **Swappable:** at least two models from different providers pass the model-tier gates through
   config alone, with no code change.

## Threats to validity

- **Label bias.** Annotators are not the end users; their guidelines encode one view of good SEO.
  Mitigate with agreement statistics and by publishing the guideline.
- **Small and unrepresentative sets.** 150 to 300 pages cannot cover the web. Report results per
  stratum, not only overall, and add pages whenever production failures are found.
- **Test-set leakage.** Prompt tuning on the held-out split inflates scores. Keep the split frozen
  and untouched, and refresh it periodically.
- **Judge contamination.** If the judge and the evaluated model share a family, scores are biased
  upward. Use a different family and re-check against humans.
- **Moving targets.** Provider models and Lighthouse versions change. Pin versions, record model
  identifiers and dates with every result, and re-run the baseline when anything is upgraded.
- **Ranking value is not measured.** The audit checks hygiene signals and judged quality; whether
  fixing them changes rankings is a different, much harder question that this plan does not answer.
