// Populate + preload streams and their single-owner button/lifecycle state
// (WP-18 · CQ-23/JS-3, JS-10; WP-22 · UXP-2/UXP-13/UXP-19).
//
// This module is the SINGLE writer of the populate button and drives the
// activity strip during a run: the determinate progress bar, the plain-language
// status line, and the demoted Details log. Outcomes surface as toasts;
// failures surface as persistent, actionable banners — never a blocking modal.

import { endpoints, fetchReleases, checkServerAlive } from "./api.js";
import { state, releaseMap } from "./state.js";
import { renderTable, restoreExpandedRow } from "./table.js";
import { applyCalendarFiltersFromSelection, fetchScrapeStatus } from "./calendar.js";
import {
  logClear,
  logAppendLine,
  setActivityLine,
  setActivityState,
  setDetailsOpen,
  startProgress,
  finishProgress,
  reportProgress,
  phaseLabel,
} from "./status.js";
import { showToast, showBanner, dismissBanner } from "./feedback.js";
import { toggleSettings } from "./modals.js";

const populateBtn = document.getElementById("populate-range");
const preloadBtn = document.getElementById("preload-range");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");

export let isPopulating = false;
let lastAllPopulated = false;

const POPULATE_BANNER = "populate-error";
const OLD_BROWSER_MSG =
  "This browser is too old for bcfeed. Please use a current version of Chrome.";
const OFFLINE_TITLE = "bcfeed isn't running";

// The one place that writes the populate button's disabled/label/title state.
export function updatePopulateButton(allPopulated) {
  if (typeof allPopulated === "boolean") lastAllPopulated = allPopulated;
  if (!populateBtn) return;
  if (state.serverOffline) {
    // While disconnected the primary action is visibly disabled with an
    // explanation (UXP-20); its real state is recomputed on reconnect.
    populateBtn.disabled = true;
    populateBtn.title = OFFLINE_TITLE;
    return;
  }
  if (isPopulating) {
    populateBtn.disabled = true;
    populateBtn.textContent = "Populating…";
    populateBtn.title = "";
    return;
  }
  populateBtn.disabled = lastAllPopulated;
  populateBtn.textContent = lastAllPopulated ? "Release list populated" : "Populate release list";
  populateBtn.title = lastAllPopulated ? "All dates in this range are already populated" : "";
}

// WP-10 · ARC-1/ARCH-4: /populate-range-stream carries typed JSON events.
// Behavior keys ONLY on event names and `code` — never on message prose.
function parseSseData(raw) {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : null;
  } catch (err) {
    return null;
  }
}

function currentRangeLabel() {
  const from = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  const to = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (!from && !to) return "";
  if (!to || from === to) return from || to;
  return `${from} – ${to}`;
}

// UXP-13: a subtle, transient highlight on rows that arrived this run so "what
// changed" is visible in the table itself.
function highlightNewRows(beforeKeys) {
  const rows = document.querySelectorAll("#release-rows tr.data-row");
  let any = false;
  rows.forEach((row) => {
    const key = row.dataset.key;
    if (key && !beforeKeys.has(key)) {
      row.classList.add("row-added");
      any = true;
    }
  });
  if (any) {
    setTimeout(() => {
      document
        .querySelectorAll("#release-rows tr.row-added")
        .forEach((row) => row.classList.remove("row-added"));
    }, 4000);
  }
}

async function refreshAfterPopulate(summary = {}) {
  // In-place completion (JS-10/UX-9/PERF-5): refetch only what the run could
  // have changed — /releases and /scrape-status — and re-render. Sort, filters,
  // scroll and the expanded row survive because the page never reloads.
  const beforeKeys = new Set(releaseMap.keys());
  try {
    await fetchReleases();
  } catch (err) {
    console.warn("Failed to refresh releases after checking mail", err);
  }
  renderTable();
  restoreExpandedRow();
  await fetchScrapeStatus();
  highlightNewRows(beforeKeys);

  const added = Number(summary.new_releases);
  const range = currentRangeLabel();
  if (Number.isFinite(added)) {
    const msg =
      added > 0
        ? `Added ${added} release${added === 1 ? "" : "s"}${range ? ` · ${range}` : ""}`
        : `No new releases${range ? ` for ${range}` : ""}.`;
    showToast(msg, { kind: "success" });
    setActivityState(added > 0 ? "success" : "idle");
    setActivityLine(msg);
  }
}

// Map a terminal WP-10 error event (keyed on `code`, never prose) to a
// persistent, actionable, plain-language banner (UXP-19). Raw server prose goes
// only to the Details log — never into a banner.
function handleTerminalError(data) {
  const code = data.code;
  if (code === "busy") {
    // Not a failure: another check already holds the lock.
    showToast("A check is already running — hang on.", { kind: "info" });
    setActivityState("idle");
    setActivityLine("A check is already running.");
    return;
  }
  const detail = (typeof data.text === "string" && data.text) || data.message;
  if (detail) logAppendLine(detail);
  setActivityState("error");
  setDetailsOpen(true);

  if (code === "auth") {
    setActivityLine("Your email isn't connected.");
    showBanner(POPULATE_BANNER, "Connect your email to load releases.", {
      kind: "error",
      action: { label: "Connect email", onClick: () => toggleSettings(true) },
    });
    return;
  }
  if (code === "max_results") {
    setActivityLine("Stopped — too many results.");
    showBanner(
      POPULATE_BANNER,
      "That date range has too many release emails to fetch at once. Try a shorter range.",
      { kind: "warn" },
    );
    return;
  }
  // gmail | parse | internal → a friendly, retryable failure.
  setActivityLine("Couldn't load releases.");
  showBanner(POPULATE_BANNER, "Couldn't load releases — something went wrong. Try again.", {
    kind: "error",
    action: { label: "Try again", onClick: () => populateRangeFromCalendars() },
  });
}

function populateRangeFromCalendars() {
  checkServerAlive();
  applyCalendarFiltersFromSelection();
  let startVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  let endVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (startVal && !endVal) endVal = startVal;
  if (endVal && !startVal) startVal = endVal;
  if (!endpoints.apiRoot || !startVal || !endVal) return;

  if (!window.EventSource) {
    showBanner(POPULATE_BANNER, OLD_BROWSER_MSG, { kind: "error" });
    return;
  }

  dismissBanner(POPULATE_BANNER);
  isPopulating = true;
  updatePopulateButton();
  logClear();
  setDetailsOpen(false);
  setActivityState("working");
  setActivityLine("Checking your mail…");
  startProgress();

  const url = `${endpoints.apiRoot}/populate-range-stream?start=${encodeURIComponent(startVal)}&end=${encodeURIComponent(endVal)}`;
  const es = new EventSource(url);
  let finished = false;
  let blipNoted = false;
  const finishRun = () => {
    if (finished) return;
    finished = true;
    es.close();
    isPopulating = false;
    updatePopulateButton();
    finishProgress();
  };
  es.onmessage = (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data || data.v !== 1) return;
    blipNoted = false;
    // Details log keeps full fidelity; the primary channels are the progress
    // bar (determinate during download) and the plain-language status line.
    if (typeof data.text === "string") logAppendLine(data.text);
    setActivityLine(phaseLabel(data));
    reportProgress(data);
  };
  es.addEventListener("error", (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data) {
      // Connection-level event without a server payload. A transient blip is
      // NOT a failure — EventSource reconnects on its own, and only a typed
      // `event: error` payload is terminal (JS-10). A truly closed stream is a
      // real drop.
      if (es.readyState === EventSource.CLOSED && !finished) {
        finishRun();
        setActivityState("error");
        setDetailsOpen(true);
        logAppendLine("Lost the connection to the app.");
        showBanner(POPULATE_BANNER, "Couldn't load releases — the connection dropped. Try again.", {
          kind: "error",
          action: { label: "Try again", onClick: () => populateRangeFromCalendars() },
        });
        checkServerAlive();
      } else if (!blipNoted && !finished) {
        blipNoted = true;
        setActivityLine("Connection interrupted — reconnecting…");
        logAppendLine("Connection interrupted — reconnecting…");
      }
      return;
    }
    finishRun();
    handleTerminalError(data);
  });
  es.addEventListener("done", (ev) => {
    const data = parseSseData(ev && ev.data) || {};
    finishRun();
    dismissBanner(POPULATE_BANNER);
    refreshAfterPopulate(data);
  });
}

// WP-17 · LOG-6: enrichment runs server-side. This opens /preload-range-stream
// and consumes the typed events minimally — progress lines into the Details
// log, a completion toast at the end (the full per-release UI is WP-24/UXP-9).
function preloadEmbedsForRange() {
  checkServerAlive();
  applyCalendarFiltersFromSelection();
  let startVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  let endVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (startVal && !endVal) endVal = startVal;
  if (endVal && !startVal) startVal = endVal;
  if (!endpoints.apiRoot || !startVal || !endVal) return;
  if (!window.EventSource) {
    showToast(OLD_BROWSER_MSG, { kind: "error" });
    return;
  }
  const original = preloadBtn ? preloadBtn.textContent : "";
  if (preloadBtn) {
    preloadBtn.disabled = true;
    preloadBtn.textContent = "Loading players…";
  }
  logClear();
  setDetailsOpen(false);

  const url = `${endpoints.apiRoot}/preload-range-stream?start=${encodeURIComponent(startVal)}&end=${encodeURIComponent(endVal)}`;
  const es = new EventSource(url);
  let finished = false;
  const finishRun = () => {
    if (finished) return;
    finished = true;
    es.close();
    if (preloadBtn) {
      preloadBtn.disabled = false;
      preloadBtn.textContent = original || "Preload release data";
    }
  };
  es.onmessage = (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data || data.v !== 1) return;
    if (typeof data.text === "string") logAppendLine(data.text);
  };
  es.addEventListener("error", (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data) {
      if (es.readyState === EventSource.CLOSED && !finished) {
        finishRun();
        logAppendLine("Lost the connection to the app.");
        checkServerAlive();
      }
      return;
    }
    finishRun();
    logAppendLine(data.message || "Couldn't load release details.");
    showToast("Couldn't load players. Try again.", { kind: "error" });
  });
  es.addEventListener("done", async (ev) => {
    const data = parseSseData(ev && ev.data) || {};
    finishRun();
    const ok = Number(data.ok) || 0;
    const failed = Number(data.failed) || 0;
    const parts = [`Players loaded for ${ok} release${ok === 1 ? "" : "s"}`];
    if (failed) parts.push(`${failed} couldn't be loaded`);
    if (data.cancelled) parts.push("stopped early");
    const msg = `${parts.join("; ")}.`;
    logAppendLine(msg);
    showToast(msg, { kind: failed ? "info" : "success" });
    try {
      await fetchReleases();
    } catch (err) {
      console.warn("Failed to refresh releases after loading details", err);
    }
    renderTable();
    restoreExpandedRow();
  });
}

export function initPopulate() {
  if (populateBtn) populateBtn.addEventListener("click", () => populateRangeFromCalendars());
  if (preloadBtn) preloadBtn.addEventListener("click", () => preloadEmbedsForRange());
}
