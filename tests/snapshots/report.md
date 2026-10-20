# SEO audit report: https://example.com/

## Summary

- Mode: site
- Model: openai/gpt-4o-mini
- Pages audited: 2
- Findings: 5 (3 code, 2 model)
- By severity: critical 0, high 3, medium 1, low 1, info 0
- Ungrounded model findings dropped: 1
- Tokens: 120 (100 prompt, 20 completion)

## Crawl

- Pages crawled: 2
- Pages skipped: 3
- Limit hit: max_pages

## Top actions

1. Add titles to every page
2. Fix broken internal links

## Findings

### High (3)

#### Site-wide

- **broken-internal-links** [code] (links): Evidence for broken-internal-links
  - Fix: Fix broken-internal-links.

#### https://example.com/

- **title-missing** [code] (meta): Evidence for title-missing
  - Fix: Fix title-missing.

#### https://example.com/a

- **title-weak** [model, confidence 0.80] (content): Evidence for title-weak
  - Fix: Fix title-weak.

### Medium (1)

#### https://example.com/a

- **meta-description-missing** [code] (meta): Evidence for meta-description-missing
  - Fix: Fix meta-description-missing.

### Low (1)

#### https://example.com/

- **intro-thin** [model, confidence 0.60] (content): Quote: "Widgets"
  - Fix: Fix intro-thin.

## Errors

- model_page_checks (https://example.com/b): provider down
