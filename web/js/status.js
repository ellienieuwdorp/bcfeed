// Status surfaces (WP-18 · CQ-34/JS-15, WP-22 · UXP-2).
//
// This module owns the "activity strip" that replaced the raw 200px Status LOG
// BOX: the plain-language status line, the determinate progress bar, the
// release-count cluster, and the collapsed "Details" disclosure that still
// holds the raw streamed log for power users (#populate-log — empty + collapsed
// by default). It is the SINGLE writer of all of those elements plus the
// general inline status-text primitive (setStatus). Toasts and banners live in
// feedback.js; this module drives progress + the status line.

import { state, scrapeStatus, parseDateString, isoKeyFromDate } from "./state.js";
import { isPopulating, updatePopulateButton } from "./populate.js";
import { renderChip } from "./enrich.js";

const loadingState = document.getElementById("loading-state");
const errorState = document.getElementById("error-state");
const populateLog = document.getElementById("populate-log");
const headerRangeLabel = document.getElementById("header-range-label");
const activityCount = document.getElementById("activity-count");

const activityIcon = document.getElementById("activity-icon");
const activityLine = document.getElementById("activity-line");
const activityProgress = document.getElementById("activity-progress");
const progressBar = document.getElementById("progress-bar");
const progressFill = document.getElementById("progress-fill");
const detailToggle = document.getElementById("status-toggle");
const calendarSummary = document.getElementById("calendar-summary");

// --- Loading / error banners ------------------------------------------------
export function setLoading(message = "Loading releases…") {
  if (loadingState) {
    loadingState.textContent = message;
    loadingState.style.display = "flex";
  }
  if (errorState) errorState.style.display = "none";
}

export function hideLoading() {
  if (loadingState) loadingState.style.display = "none";
}

export function showError(message) {
  hideLoading();
  if (errorState) {
    errorState.textContent = message || "Failed to load releases. Is the bcfeed proxy running?";
    errorState.style.display = "flex";
  }
  const tableWrapper = document.querySelector(".table-wrapper");
  if (tableWrapper) tableWrapper.style.display = "none";
  const scrapePanel = document.getElementById("scrape-panel");
  if (scrapePanel) scrapePanel.style.display = "none";
}

// Non-blocking variant of showError: surfaces a message without hiding the
// table, for degraded-but-usable situations.
export function showNotice(message) {
  if (!errorState || !message) return;
  errorState.textContent = message;
  errorState.style.display = "flex";
}

// --- The Details log (demoted debug view) ----------------------------------
// The raw streamed log is no longer the primary channel: it flows into a
// collapsed disclosure. These stay the one write API so every module routes its
// log lines through here.
export function logReplace(text) {
  if (!populateLog) return;
  populateLog.textContent = text;
  populateLog.scrollTop = populateLog.scrollHeight;
}

export function logAppendLine(msg) {
  if (!populateLog) return;
  const current = populateLog.textContent || "";
  const next = current ? `${current}\n${msg}` : msg;
  populateLog.textContent = next;
  populateLog.scrollTop = populateLog.scrollHeight;
}

export function logAppendHtml(html) {
  if (!populateLog) return;
  populateLog.innerHTML += html;
  populateLog.scrollTop = populateLog.scrollHeight;
}

export function logClear() {
  if (populateLog) populateLog.textContent = "";
}

// Details disclosure open/close (auto-opened on error by the run controller).
export function setDetailsOpen(open) {
  if (populateLog) {
    if (open) populateLog.removeAttribute("hidden");
    else populateLog.setAttribute("hidden", "");
  }
  if (detailToggle) {
    detailToggle.setAttribute("aria-expanded", String(!!open));
    detailToggle.classList.toggle("open", !!open);
  }
}

export function toggleDetails() {
  const open = populateLog ? populateLog.hasAttribute("hidden") : false;
  setDetailsOpen(open);
}

// --- Activity line + state icon --------------------------------------------
const ACTIVITY_ICON = {
  working: "#icon-loader",
  success: "#icon-check-circle",
  error: "#icon-alert-circle",
};

export function setActivityLine(text) {
  if (activityLine) activityLine.textContent = text || "";
}

export function setActivityState(kind) {
  if (!activityIcon) return;
  const use = activityIcon.querySelector("use");
  if (!ACTIVITY_ICON[kind]) {
    activityIcon.setAttribute("hidden", "");
    activityIcon.classList.remove("spinning", "state-success", "state-error");
    return;
  }
  if (use) use.setAttribute("href", ACTIVITY_ICON[kind]);
  activityIcon.removeAttribute("hidden");
  activityIcon.classList.toggle("spinning", kind === "working");
  activityIcon.classList.toggle("state-success", kind === "success");
  activityIcon.classList.toggle("state-error", kind === "error");
}

function setCalendarSummary(text) {
  if (calendarSummary) calendarSummary.textContent = text || "";
}

// --- Determinate progress bar (WP-22 · UXP-2 / JS-7) ------------------------
// Driven by WP-10 typed events: both `current` and `total` present → a real
// percentage (aria-valuenow advances); absent → an indeterminate sweep. The bar
// region is aria-live so a screen reader hears progress transitions.
export function startProgress() {
  if (activityProgress) activityProgress.removeAttribute("hidden");
  setProgressIndeterminate();
}

export function setProgressIndeterminate() {
  if (progressBar) {
    progressBar.classList.add("indeterminate");
    progressBar.removeAttribute("aria-valuenow");
  }
  if (progressFill) progressFill.style.width = "";
}

export function updateProgress(current, total) {
  if (!progressBar || !progressFill) return;
  if (!Number.isFinite(total) || total <= 0) {
    setProgressIndeterminate();
    return;
  }
  const pct = Math.max(0, Math.min(100, Math.round(((Number(current) || 0) / total) * 100)));
  progressBar.classList.remove("indeterminate");
  progressBar.setAttribute("aria-valuenow", String(pct));
  progressFill.style.width = `${pct}%`;
}

export function finishProgress() {
  if (activityProgress) activityProgress.setAttribute("hidden", "");
  if (progressBar) {
    progressBar.classList.remove("indeterminate");
    progressBar.removeAttribute("aria-valuenow");
  }
  if (progressFill) progressFill.style.width = "";
}

// Map one typed progress event to the bar (determinate when the event carries a
// known total, indeterminate otherwise).
export function reportProgress(data) {
  if (
    data &&
    data.current != null &&
    Number.isFinite(Number(data.total)) &&
    Number(data.total) > 0
  ) {
    updateProgress(data.current, Number(data.total));
  } else {
    setProgressIndeterminate();
  }
}

// Plain-language phase label for the status line (UXP-1 vocabulary). The
// download/enrich phases carry live counts; the rest are quiet verbs.
const PHASE_LABEL = {
  query: "Searching your mail…",
  parse: "Reading emails…",
  persist: "Saving releases…",
  cache: "Checking saved releases…",
};

export function phaseLabel(data) {
  const phase = data && data.phase;
  const total = Number(data && data.total);
  if (phase === "download" && Number.isFinite(total) && total > 0) {
    return `Downloading ${Number(data.current) || 0} of ${total}`;
  }
  if (phase === "enrich" && Number.isFinite(total) && total > 0) {
    return `Loading players ${Number(data.current) || 0} of ${total}`;
  }
  return PHASE_LABEL[phase] || "Checking your mail…";
}

// General status-text primitive (used by the provider controller for its
// per-action status lines). Tone is expressed via classes (WP-19 · CQ-35).
export function setStatus(element, message, tone = "muted") {
  if (!element) return;
  element.textContent = message || "";
  element.classList.remove("status-tone-error", "status-tone-success");
  if (tone === "error") {
    element.classList.add("status-tone-error");
  } else if (tone === "success") {
    element.classList.add("status-tone-success");
  }
}

// --- Idle selection summary + header line -----------------------------------
// Coverage of the selected range: total days vs. days the ledger records as
// checked. Feeds both the populate-button state and the calm idle status line.
function rangeCoverage() {
  let fromVal = state.dateFilterFrom || "";
  let toVal = state.dateFilterTo || "";
  if (fromVal && !toVal) toVal = fromVal;
  if (toVal && !fromVal) fromVal = toVal;
  if (!fromVal || !toVal) return null;

  let startDate = parseDateString(fromVal);
  let endDate = parseDateString(toVal);
  if (!startDate || !endDate) return null;
  if (endDate < startDate) {
    [startDate, endDate] = [endDate, startDate];
    [fromVal, toVal] = [toVal, fromVal];
  }

  const msPerDay = 24 * 60 * 60 * 1000;
  const totalDays = Math.floor((endDate.getTime() - startDate.getTime()) / msPerDay) + 1;
  let populatedDays = 0;
  const cursor = new Date(startDate);
  while (cursor.getTime() <= endDate.getTime()) {
    if (scrapeStatus.scraped.has(isoKeyFromDate(cursor))) populatedDays += 1;
    cursor.setDate(cursor.getDate() + 1);
  }
  return {
    fromVal,
    toVal,
    totalDays,
    populatedDays,
    notChecked: totalDays - populatedDays,
    allPopulated: totalDays > 0 && populatedDays >= totalDays,
  };
}

// The calm idle line: the range and how much of it is checked (UXP-2 signal
// map). The run controller owns this line while populating, so this is a no-op
// mid-run (JS-3: a calendar click can never erase live progress).
function renderIdleActivity(cov) {
  if (isPopulating) return;
  if (!state.filterByDate) {
    setActivityLine("Showing everything fetched so far");
    setCalendarSummary("");
    return;
  }
  if (!cov) {
    setActivityLine("Pick dates on the calendar to see releases.");
    setCalendarSummary("");
    return;
  }
  const range = cov.fromVal === cov.toVal ? cov.fromVal : `${cov.fromVal} – ${cov.toVal}`;
  const summary = cov.allPopulated
    ? `${range} · all dates checked`
    : `${range} · ${cov.notChecked} date${cov.notChecked === 1 ? "" : "s"} not checked`;
  setActivityLine(summary);
  setCalendarSummary(summary);
}

export function updateSelectionStatusLog() {
  // JS-3: while a populate stream is running, the selection summary must not
  // clobber the streaming status line or reset the populate button.
  if (isPopulating) return;
  const cov = rangeCoverage();
  updatePopulateButton(cov ? cov.allPopulated : false);
  renderIdleActivity(cov);
}

export function updateStatusForDateFilter() {
  if (isPopulating) return;
  updateSelectionStatusLog();
}

let lastShownCount = null;
export function updateHeaderRange(count = null) {
  if (count != null) lastShownCount = count;
  const fromVal = state.dateFilterFrom || "";
  const toVal = state.dateFilterTo || "";
  const start = fromVal || toVal;
  const end = toVal || fromVal;
  if (headerRangeLabel) {
    if (!start && !end) {
      headerRangeLabel.textContent = "";
    } else if (start === end) {
      headerRangeLabel.textContent = `Date range: ${start}`;
    } else {
      headerRangeLabel.textContent = `Date range: ${start} to ${end}`;
    }
  }
  if (activityCount) {
    // The activity strip is the release count's ONLY home now, so an argless
    // call (e.g. fetchScrapeStatus after load) must not blank it — reuse the
    // last real value (JS-4 fixed by construction).
    const shown = lastShownCount;
    activityCount.textContent =
      shown == null ? "" : `${shown} release${shown === 1 ? "" : "s"} shown`;
  }

  // The old "Preload release data" button is gone (WP-24 · UXP-8). Enrichment
  // is an ambient background queue (enrich.js); the aggregate chip shows its
  // progress + Pause and the low-key "Load all players" affordance. Recompute
  // it whenever the selected range or the release set changes.
  renderChip();

  // Keep the idle line coherent when the count changes (never mid-run).
  if (!isPopulating) renderIdleActivity(rangeCoverage());
}
