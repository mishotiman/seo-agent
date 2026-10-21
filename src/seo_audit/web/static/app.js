"use strict";

// SEO Audit UI. Behaviour is added story by story; no build step, no dependencies.
document.documentElement.classList.add("js");

const form = document.getElementById("audit-form");
const urlInput = document.getElementById("url");
const urlError = document.getElementById("url-error");
const modeInputs = Array.from(form.querySelectorAll('input[name="mode"]'));
const maxPagesField = document.getElementById("max-pages-field");
const maxPagesInput = document.getElementById("max-pages");
const maxPagesHint = document.getElementById("max-pages-hint");
const maxPagesError = document.getElementById("max-pages-error");
const modelInput = document.getElementById("model");
const formError = document.getElementById("form-error");
const runButton = document.getElementById("run-button");

let maxPagesLimit = 1;
let submitting = false;

function setError(el, input, message) {
  el.textContent = message || "";
  el.hidden = !message;
  if (input) {
    if (message) {
      input.setAttribute("aria-invalid", "true");
    } else {
      input.removeAttribute("aria-invalid");
    }
  }
}

function clearErrors() {
  setError(urlError, urlInput, "");
  setError(maxPagesError, maxPagesInput, "");
  setError(formError, null, "");
}

function currentMode() {
  const checked = modeInputs.find((input) => input.checked);
  return checked ? checked.value : "page";
}

function syncMode() {
  maxPagesField.hidden = currentMode() !== "site";
}

function applyConfig(config) {
  maxPagesLimit = config.max_pages_limit;
  modelInput.value = config.model;
  maxPagesInput.max = String(config.max_pages_limit);
  maxPagesInput.value = String(config.max_pages);
  maxPagesHint.textContent = "Between 1 and " + config.max_pages_limit + ".";
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const body = await response.json();
    if (!response.ok) {
      setError(formError, null, body.detail || "Could not load the configuration.");
      return;
    }
    applyConfig(body);
  } catch (err) {
    setError(formError, null, "Could not load the configuration.");
  }
}

// Returns the request body, or null after showing inline validation messages.
function readForm() {
  clearErrors();
  const url = urlInput.value.trim();
  if (!url) {
    setError(urlError, urlInput, "Enter a URL to audit.");
    urlInput.focus();
    return null;
  }
  const body = { url: url, mode: currentMode() };
  if (body.mode === "site") {
    const raw = maxPagesInput.value.trim();
    const pages = Number(raw);
    if (!raw || !Number.isInteger(pages) || pages < 1 || pages > maxPagesLimit) {
      setError(maxPagesError, maxPagesInput, "Enter a whole number between 1 and " + maxPagesLimit + ".");
      maxPagesInput.focus();
      return null;
    }
    body.max_pages = pages;
  }
  const model = modelInput.value.trim();
  if (model) {
    body.model = model;
  }
  return body;
}

// Place a server-side message next to the field it is about.
function showServerError(message) {
  if (/^Invalid URL/.test(message)) {
    setError(urlError, urlInput, message);
    urlInput.focus();
  } else if (/^max_pages /.test(message)) {
    setError(maxPagesError, maxPagesInput, message);
    maxPagesInput.focus();
  } else {
    setError(formError, null, message);
  }
}

function setFormDisabled(disabled) {
  form.querySelectorAll("input, button").forEach((control) => {
    control.disabled = disabled;
  });
}

async function submitForm(event) {
  event.preventDefault();
  if (submitting || form.classList.contains("is-running")) {
    return;
  }
  const body = readForm();
  if (!body) {
    return;
  }
  submitting = true;
  runButton.disabled = true;
  let started = false;
  try {
    const response = await fetch("/api/audits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 202) {
      started = true;
      document.dispatchEvent(new CustomEvent("audit:started", { detail: { id: data.id, request: body } }));
    } else {
      showServerError(data.detail || "The audit could not be started (HTTP " + response.status + ").");
    }
  } catch (err) {
    setError(formError, null, "Could not reach the server.");
  } finally {
    submitting = false;
    if (!started) {
      runButton.disabled = false;
    }
  }
}

// ---- Running an audit: progress card, event stream, polling fallback ----

const resultsRegion = document.getElementById("results");
const POLL_MS = 2000;
const ACTIVE_STATES = ["queued", "running"];
const STATE_LABELS = {
  queued: "Queued",
  running: "Running",
  done: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
};

let run = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function buildProgressCard(url) {
  const card = el("section", "card progress-card");
  card.setAttribute("aria-label", "Audit progress");

  const head = el("div", "progress-head");
  const title = el("div", "progress-title");
  const indicator = el("span", "indicator");
  indicator.setAttribute("aria-hidden", "true");
  const stateLabel = el("span", "progress-state", STATE_LABELS.queued);
  title.append(indicator, stateLabel);
  const cancel = el("button", "btn btn-secondary", "Cancel");
  cancel.type = "button";
  head.append(title, cancel);

  const target = el("p", "progress-url", url);
  const log = el("div", "progress-log");
  log.setAttribute("role", "log");
  log.setAttribute("aria-label", "Progress messages");
  log.tabIndex = 0;
  const note = el("p", "progress-note");
  note.hidden = true;

  card.append(head, target, log, note);
  return { card, indicator, stateLabel, cancel, log, note };
}

function setIndicator(ui, state) {
  ui.indicator.dataset.state = state;
  ui.stateLabel.textContent = STATE_LABELS[state] || state;
}

function appendProgress(message) {
  const line = el("div", "progress-line", message);
  run.ui.log.append(line);
  run.ui.log.scrollTop = run.ui.log.scrollHeight;
  run.shown += 1;
}

function stopStream() {
  if (run.source) {
    run.source.close();
    run.source = null;
  }
  if (run.timer) {
    clearTimeout(run.timer);
    run.timer = null;
  }
}

function showReportPlaceholder(job) {
  const holder = el("section", "report-placeholder");
  holder.id = "report";
  resultsRegion.append(holder);
  document.dispatchEvent(new CustomEvent("audit:finished", { detail: { job: job, container: holder } }));
}

function finishRun(job, state) {
  stopStream();
  const ui = run.ui;
  const seconds = Math.max(1, Math.round((Date.now() - run.startedAt) / 1000));
  setIndicator(ui, state);
  ui.cancel.hidden = true;
  ui.note.hidden = false;
  if (state === "done") {
    ui.card.classList.add("is-collapsed");
    ui.note.textContent = "Finished in " + seconds + " s";
  } else if (state === "cancelled") {
    ui.note.textContent = "Cancelled after " + seconds + " s";
  } else {
    ui.note.textContent = (job && job.error) || "The audit failed.";
    ui.note.classList.add("error-text");
  }
  if (state === "done" && job) {
    showReportPlaceholder(job);
  }
  setFormDisabled(false);
  form.classList.remove("is-running");
  run = null;
}

async function fetchJob(id) {
  const response = await fetch("/api/audits/" + encodeURIComponent(id));
  if (!response.ok) {
    throw new Error("HTTP " + response.status);
  }
  return response.json();
}

// Fetch the final job (report / error) and settle the UI.
async function settle(id, state) {
  if (!run || run.id !== id) {
    return;
  }
  stopStream();
  let job = null;
  try {
    job = await fetchJob(id);
    if (!run || run.id !== id) {
      return;
    }
    job.progress.slice(run.shown).forEach(appendProgress);
  } catch (err) {
    // Keep the state we already know; there is no report to show.
  }
  finishRun(job, job ? job.state : state);
}

function poll(id) {
  if (!run || run.id !== id) {
    return;
  }
  run.timer = setTimeout(async () => {
    if (!run || run.id !== id) {
      return;
    }
    run.timer = null;
    try {
      const job = await fetchJob(id);
      if (!run || run.id !== id) {
        return;
      }
      job.progress.slice(run.shown).forEach(appendProgress);
      if (ACTIVE_STATES.includes(job.state)) {
        setIndicator(run.ui, job.state);
        poll(id);
      } else {
        settle(id, job.state);
      }
    } catch (err) {
      poll(id);
    }
  }, POLL_MS);
}

function followEvents(id) {
  if (typeof EventSource === "undefined") {
    poll(id);
    return;
  }
  const source = new EventSource("/api/audits/" + encodeURIComponent(id) + "/events");
  run.source = source;
  source.addEventListener("progress", (event) => {
    if (run && run.id === id) {
      setIndicator(run.ui, "running");
      appendProgress(event.data);
    }
  });
  source.addEventListener("state", (event) => {
    settle(id, event.data);
  });
  source.onerror = () => {
    // Stream dropped before the final state: stop the auto-reconnect (it would replay
    // everything) and poll the job instead.
    if (run && run.id === id && run.source === source) {
      source.close();
      run.source = null;
      poll(id);
    }
  };
}

async function cancelRun() {
  if (!run) {
    return;
  }
  const id = run.id;
  run.ui.cancel.disabled = true;
  try {
    const response = await fetch("/api/audits/" + encodeURIComponent(id) + "/cancel", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      await settle(id, data.state || "cancelled");
      return;
    }
    if (response.status === 409) {
      // Already finished: pick up the real outcome.
      await settle(id, "done");
      return;
    }
    setError(formError, null, data.detail || "The audit could not be cancelled.");
  } catch (err) {
    setError(formError, null, "Could not reach the server.");
  }
  if (run && run.id === id) {
    run.ui.cancel.disabled = false;
  }
}

function startRun(id, request) {
  if (run) {
    stopStream();
  }
  resultsRegion.replaceChildren();
  const ui = buildProgressCard(request.url);
  resultsRegion.append(ui.card);
  run = { id: id, ui: ui, shown: 0, startedAt: Date.now(), source: null, timer: null };
  setIndicator(ui, "queued");
  ui.cancel.addEventListener("click", cancelRun);
  form.classList.add("is-running");
  setFormDisabled(true);
  ui.card.scrollIntoView({ block: "nearest" });
  followEvents(id);
}

document.addEventListener("audit:started", (event) => startRun(event.detail.id, event.detail.request));

modeInputs.forEach((input) => input.addEventListener("change", syncMode));
urlInput.addEventListener("input", () => setError(urlError, urlInput, ""));
maxPagesInput.addEventListener("input", () => setError(maxPagesError, maxPagesInput, ""));
form.addEventListener("submit", submitForm);

syncMode();
loadConfig();
