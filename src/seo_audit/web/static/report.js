"use strict";

// Renders a finished AuditReport into the container handed over by app.js (`audit:finished`).
// Every string from the report is inserted with textContent: audited sites control that text.
(function () {
  const SEVERITIES = ["critical", "high", "medium", "low", "info"];
  const LIMIT_NOTICES = {
    max_pages: "The crawl stopped at the page limit, so some pages were skipped. Raise Max pages to cover more of the site.",
    max_depth: "The crawl stopped at the maximum link depth, so pages linked deeper than that were not visited.",
    time_budget: "The crawl stopped because its time budget ran out, so the remaining pages were skipped.",
  };

  function h(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString("en-US");
  }

  function allFindings(report) {
    const found = (report.site_findings || []).slice();
    (report.pages || []).forEach((page) => found.push(...(page.findings || [])));
    return found;
  }

  function countBySeverity(findings) {
    const counts = {};
    SEVERITIES.forEach((severity) => {
      counts[severity] = 0;
    });
    findings.forEach((finding) => {
      if (finding.severity in counts) {
        counts[finding.severity] += 1;
      }
    });
    return counts;
  }

  function statTile(label, value) {
    const tile = h("li", "stat-tile");
    tile.append(h("span", "stat-value", formatNumber(value)), h("span", "stat-label", label));
    return tile;
  }

  function severityBadges(counts) {
    const row = h("ul", "severity-row");
    row.setAttribute("aria-label", "Findings by severity");
    SEVERITIES.forEach((severity) => {
      const item = h("li");
      item.append(h("span", "badge badge-" + severity, counts[severity] + " " + severity));
      row.append(item);
    });
    return row;
  }

  function metaLine(report) {
    const parts = [report.mode === "site" ? "Site audit" : "Page audit"];
    parts.push(report.model ? "Model " + report.model : "No model");
    const usage = report.token_usage || {};
    parts.push(formatNumber(usage.total_tokens) + " tokens");
    return h("p", "report-meta", parts.join(" · "));
  }

  function limitNotice(crawl) {
    if (!crawl || !crawl.limit_hit) {
      return null;
    }
    const message = LIMIT_NOTICES[crawl.limit_hit] || "The crawl stopped at a limit (" + crawl.limit_hit + ").";
    const notice = h("p", "notice", message);
    notice.setAttribute("role", "note");
    return notice;
  }

  function fixFirst(report) {
    const actions = report.site_summary || [];
    if (!actions.length) {
      return null;
    }
    const block = h("div", "fix-first");
    block.append(h("h2", "section-title", "What to fix first"));
    const list = h("ol", "fix-list");
    actions.forEach((action) => list.append(h("li", null, action)));
    block.append(list);
    return block;
  }

  function droppedNote(count) {
    if (!count) {
      return null;
    }
    const noun = count === 1 ? "model finding was" : "model findings were";
    return h(
      "p",
      "hint",
      count + " " + noun + " dropped because their evidence could not be verified against the page.",
    );
  }

  function buildSummary(report) {
    const crawl = report.crawl || {};
    const findings = allFindings(report);
    const summary = h("div", "report-summary");

    const tiles = h("ul", "stat-tiles");
    tiles.setAttribute("aria-label", "Audit totals");
    tiles.append(
      statTile("Pages crawled", crawl.pages_crawled),
      statTile("Pages skipped", crawl.pages_skipped),
      statTile("Findings", findings.length),
    );
    summary.append(tiles, severityBadges(countBySeverity(findings)), metaLine(report));

    [limitNotice(crawl), fixFirst(report), droppedNote(report.dropped_model_findings)].forEach((block) => {
      if (block) {
        summary.append(block);
      }
    });
    return summary;
  }

  function renderReport(container, job) {
    container.replaceChildren();
    if (!job || !job.report) {
      return;
    }
    container.classList.remove("report-placeholder");
    container.classList.add("card", "report");
    container.append(buildSummary(job.report));
  }

  document.addEventListener("audit:finished", (event) => {
    renderReport(event.detail.container, event.detail.job);
  });
})();
