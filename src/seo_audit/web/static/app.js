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

async function submitForm(event) {
  event.preventDefault();
  if (submitting) {
    return;
  }
  const body = readForm();
  if (!body) {
    return;
  }
  submitting = true;
  runButton.disabled = true;
  try {
    const response = await fetch("/api/audits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 202) {
      document.dispatchEvent(new CustomEvent("audit:started", { detail: { id: data.id, request: body } }));
    } else {
      showServerError(data.detail || "The audit could not be started (HTTP " + response.status + ").");
    }
  } catch (err) {
    setError(formError, null, "Could not reach the server.");
  } finally {
    submitting = false;
    runButton.disabled = false;
  }
}

modeInputs.forEach((input) => input.addEventListener("change", syncMode));
urlInput.addEventListener("input", () => setError(urlError, urlInput, ""));
maxPagesInput.addEventListener("input", () => setError(maxPagesError, maxPagesInput, ""));
form.addEventListener("submit", submitForm);

syncMode();
loadConfig();
