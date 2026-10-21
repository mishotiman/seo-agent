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

  const MAX_OPEN_PAGES = 5;

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

  function sortFindings(findings) {
    return findings
      .map((finding, index) => ({ finding, index }))
      .sort((a, b) => {
        const rank = SEVERITIES.indexOf(a.finding.severity) - SEVERITIES.indexOf(b.finding.severity);
        return rank || a.index - b.index;
      })
      .map((entry) => entry.finding);
  }

  let idCounter = 0;

  // A button that toggles `panel` and mirrors the state in aria-expanded (Enter/Space work natively).
  function toggleButton(className, panel, expanded) {
    idCounter += 1;
    panel.id = "report-panel-" + idCounter;
    const button = h("button", className);
    button.type = "button";
    button.setAttribute("aria-controls", panel.id);
    const set = (open) => {
      button.setAttribute("aria-expanded", open ? "true" : "false");
      panel.hidden = !open;
    };
    button.addEventListener("click", () => set(button.getAttribute("aria-expanded") !== "true"));
    set(expanded);
    return button;
  }

  function detailBlock(label, text) {
    const block = h("div", "finding-detail");
    block.append(h("dt", null, label), h("dd", null, text));
    return block;
  }

  function findingRow(finding) {
    const row = h("li", "finding");
    const body = h("dl", "finding-body");
    body.append(
      detailBlock("Evidence", finding.evidence || "None"),
      detailBlock("Recommendation", finding.recommendation || "None"),
    );
    if (finding.source === "model") {
      body.append(detailBlock("Confidence", Math.round(Number(finding.confidence || 0) * 100) + "%"));
    }
    const head = toggleButton("finding-head", body, false);
    head.append(
      h("span", "badge badge-" + finding.severity, finding.severity),
      h("span", "badge badge-source", finding.source),
      h("span", "finding-id", finding.id),
      h("span", "finding-category", finding.category),
    );
    row.append(head, body);
    return { el: row, finding };
  }

  function findingGroup(title, meta, findings, expanded) {
    const group = h("section", "finding-group");
    const panel = h("div", "finding-group-body");
    const count = h("span", "group-count");
    const head = toggleButton("group-head", panel, expanded);
    head.append(h("span", "group-title", title), h("span", "group-meta", meta), count);
    const list = h("ul", "finding-list");
    const rows = sortFindings(findings).map(findingRow);
    rows.forEach((row) => list.append(row.el));
    const empty = h("p", "hint group-empty", "No findings");
    empty.hidden = rows.length > 0;
    panel.append(list, empty);
    group.append(head, panel);
    return { el: group, rows, count, total: rows.length };
  }

  function chip(label, count, onClick) {
    const button = h("button", "chip");
    button.type = "button";
    button.append(h("span", null, label));
    if (count !== null) {
      button.append(h("span", "chip-count", String(count)));
    }
    button.addEventListener("click", onClick);
    return button;
  }

  function chipRow(label, options, onSelect, chips) {
    const row = h("div", "chip-row");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", label);
    options.forEach(([value, text, count]) => {
      const button = chip(text, count, () => onSelect(value));
      chips.push({ button, value });
      row.append(button);
    });
    return row;
  }

  function buildFindings(report) {
    const section = h("div", "findings");
    section.append(h("h2", "section-title", "Findings"));

    const pages = report.pages || [];
    const groups = [];
    const siteFindings = report.site_findings || [];
    if (siteFindings.length) {
      groups.push(findingGroup("Site-wide", "", siteFindings, true));
    }
    const collapseRest = pages.length > MAX_OPEN_PAGES;
    pages.forEach((page, index) => {
      const status = page.status_code ? "HTTP " + page.status_code : "No response";
      groups.push(findingGroup(page.url, status, page.findings || [], !(collapseRest && index > 0)));
    });

    const everything = allFindings(report);
    const state = { severity: "all", source: "all" };
    const empty = h("p", "hint findings-empty", "No findings match the current filters.");
    empty.hidden = true;
    const severityChips = [];
    const sourceChips = [];

    function refresh() {
      const filtered = state.severity !== "all" || state.source !== "all";
      let visible = 0;
      groups.forEach((group) => {
        let shown = 0;
        group.rows.forEach((row) => {
          const match =
            (state.severity === "all" || row.finding.severity === state.severity) &&
            (state.source === "all" || row.finding.source === state.source);
          row.el.hidden = !match;
          shown += match ? 1 : 0;
        });
        visible += shown;
        group.count.textContent = filtered ? shown + " of " + group.total : String(group.total);
        group.el.hidden = filtered && shown === 0;
      });
      empty.hidden = !everything.length || visible > 0;
      severityChips.forEach((c) => c.button.setAttribute("aria-pressed", String(state.severity === c.value)));
      sourceChips.forEach((c) => c.button.setAttribute("aria-pressed", String(state.source === c.value)));
    }

    if (everything.length) {
      const counts = countBySeverity(everything);
      const filters = h("div", "filters");
      const severityOptions = [["all", "All", everything.length]].concat(
        SEVERITIES.map((severity) => [severity, severity, counts[severity]]),
      );
      filters.append(
        chipRow("Filter by severity", severityOptions, (value) => {
          state.severity = value;
          refresh();
        }, severityChips),
        chipRow("Filter by source", [["all", "All sources", null], ["code", "Code", null], ["model", "Model", null]], (value) => {
          state.source = value;
          refresh();
        }, sourceChips),
      );
      section.append(filters);
    }

    if (!everything.length) {
      section.append(h("p", "notice findings-none", "No findings. The audit did not flag anything on the pages it checked."));
    }
    const list = h("div", "finding-groups");
    groups.forEach((group) => list.append(group.el));
    section.append(list, empty);
    refresh();
    return section;
  }

  // Mirrors seo_audit.audit.is_total_failure: no page was fetched successfully.
  function isTotalFailure(report) {
    const errors = report.errors || [];
    if (report.mode === "page" && errors.some((error) => error.stage === "fetch")) {
      return true;
    }
    return !(report.pages || []).some((page) => page.status_code != null && page.status_code < 400);
  }

  function errorList(errors) {
    const list = h("ul", "issue-list");
    errors.forEach((error) => {
      const item = h("li", "issue");
      item.append(h("span", "issue-stage", error.stage), h("span", "issue-message", error.message));
      if (error.url) {
        item.append(h("span", "issue-url", error.url));
      }
      list.append(item);
    });
    return list;
  }

  // Collapsible "Issues during the audit" (stage, message, URL); null when there are none.
  function buildIssues(errors, expanded) {
    if (!errors.length) {
      return null;
    }
    const section = h("section", "issues");
    const panel = h("div", "issues-body");
    panel.append(errorList(errors));
    const head = toggleButton("group-head", panel, expanded);
    head.append(h("span", "group-title", "Issues during the audit"), h("span", "group-count", String(errors.length)));
    section.append(head, panel);
    return section;
  }

  function errorPanel(title, message) {
    const panel = h("div", "error-panel");
    panel.setAttribute("role", "alert");
    panel.append(h("h2", "error-title", title));
    if (message) {
      panel.append(h("p", "error-message", message));
    }
    return panel;
  }

  function downloadLink(job, ext, label) {
    const link = h("a", "btn btn-secondary", label);
    link.href = "/api/audits/" + encodeURIComponent(job.id) + "/report." + ext;
    link.setAttribute("download", "");
    return link;
  }

  function newAuditButton() {
    const button = h("button", "btn btn-secondary", "New audit");
    button.type = "button";
    button.addEventListener("click", () => document.dispatchEvent(new CustomEvent("audit:reset")));
    return button;
  }

  function reportHeader(job, withDownloads) {
    const head = h("div", "report-head");
    head.append(h("h2", "report-title", withDownloads ? "Results" : "Audit failed"));
    const actions = h("div", "report-actions");
    if (withDownloads) {
      actions.append(downloadLink(job, "md", "Download Markdown"), downloadLink(job, "json", "Download JSON"));
    }
    actions.append(newAuditButton());
    head.append(actions);
    return head;
  }

  function renderReport(container, job) {
    container.replaceChildren();
    if (!job) {
      return;
    }
    container.classList.remove("report-placeholder");
    container.classList.add("card", "report");
    const report = job.report;
    if (!report) {
      // A failed job (no report): show its error message.
      container.append(reportHeader(job, false), errorPanel("The audit could not be completed", job.error || "The audit failed."));
      return;
    }
    const errors = report.errors || [];
    if (isTotalFailure(report)) {
      const issues = buildIssues(errors, true);
      container.append(
        reportHeader(job, false),
        errorPanel("No page could be audited", "No page was fetched successfully, so there are no results to show. The issues below say what went wrong."),
      );
      if (issues) {
        container.append(issues);
      }
      return;
    }
    container.append(reportHeader(job, true), buildSummary(report));
    const issues = buildIssues(errors, false);
    if (issues) {
      container.append(issues);
    }
    container.append(buildFindings(report));
  }

  document.addEventListener("audit:finished", (event) => {
    renderReport(event.detail.container, event.detail.job);
  });
})();
