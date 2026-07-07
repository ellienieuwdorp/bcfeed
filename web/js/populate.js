// Populate + preload streams and their single-owner button/lifecycle state
// (WP-18 · CQ-23/JS-3, JS-10).
//
// This module is the SINGLE writer of the populate button. The `isPopulating`
// flag makes the selection-summary (status.js) a no-op during a run, so a
// calendar click mid-stream can no longer wipe the log or re-enable the button.

import { endpoints, fetchReleases, checkServerAlive } from "./api.js";
import { esc } from "./state.js";
import { renderTable, restoreExpandedRow } from "./table.js";
import { applyCalendarFiltersFromSelection, fetchScrapeStatus } from "./calendar.js";
import { logClear, logHighlight, logAppendLine, logAppendHtml } from "./status.js";
import { showMaxResultsModal } from "./modals.js";

const populateBtn = document.getElementById("populate-range");
const preloadBtn = document.getElementById("preload-range");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");

export let isPopulating = false;
let maxNoticeShown = false;
let lastAllPopulated = false;

// The one place that writes the populate button's disabled/label/title state.
export function updatePopulateButton(allPopulated) {
  if (typeof allPopulated === "boolean") lastAllPopulated = allPopulated;
  if (!populateBtn) return;
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

async function refreshAfterPopulate(summary = {}) {
  // In-place completion (JS-10/UX-9/PERF-5): refetch only what the run could
  // have changed — /releases and /scrape-status — and re-render. Sort, filters,
  // scroll and the expanded row survive because the page never reloads.
  try {
    await fetchReleases();
  } catch (err) {
    console.warn("Failed to refresh releases after checking mail", err);
  }
  renderTable();
  restoreExpandedRow();
  await fetchScrapeStatus();
  const added = Number(summary.new_releases);
  if (Number.isFinite(added)) {
    logAppendHtml(`<br><br>${esc(`Added ${added} new release${added === 1 ? "" : "s"}.`)}`);
  }
}

function populateRangeFromCalendars() {
  checkServerAlive();
  applyCalendarFiltersFromSelection();
  let startVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  let endVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (startVal && !endVal) endVal = startVal;
  if (endVal && !startVal) startVal = endVal;
  if (!endpoints.apiRoot || !startVal || !endVal) return;
  logHighlight(false);

  if (!window.EventSource) {
    alert("Populate requires EventSource support. Please use a modern browser.");
    return;
  }

  isPopulating = true;
  updatePopulateButton();
  logClear();

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
  };
  es.onmessage = (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data || data.v !== 1) return;
    blipNoted = false;
    // data.phase / data.current / data.total feed the WP-22 determinate
    // progress bar; until then the log box carries the text fallback.
    if (typeof data.text === "string") logAppendLine(data.text);
  };
  es.addEventListener("error", (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data) {
      // Connection-level blip, not a server-sent failure: EventSource
      // reconnects on its own, and only a typed `event: error` payload is
      // terminal (JS-10). Note it once, keep listening.
      if (es.readyState === EventSource.CLOSED && !finished) {
        finishRun();
        logAppendLine("Lost the connection to the app.");
        checkServerAlive();
      } else if (!blipNoted && !finished) {
        blipNoted = true;
        logAppendLine("Connection interrupted — reconnecting…");
      }
      return;
    }
    finishRun();
    if (data.code === "max_results") {
      if (!maxNoticeShown) {
        maxNoticeShown = true;
        showMaxResultsModal();
      }
      logAppendLine(data.message || "Result limit reached.");
      return;
    }
    const msg = data.message || "Something went wrong while getting releases.";
    logAppendLine(msg);
    alert(msg);
  });
  es.addEventListener("done", (ev) => {
    const data = parseSseData(ev && ev.data) || {};
    finishRun();
    refreshAfterPopulate(data);
  });
}

// WP-17 · LOG-6: enrichment runs server-side. This opens /preload-range-stream
// and consumes the typed events minimally — progress lines into the status log.
function preloadEmbedsForRange() {
  checkServerAlive();
  applyCalendarFiltersFromSelection();
  let startVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  let endVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (startVal && !endVal) endVal = startVal;
  if (endVal && !startVal) startVal = endVal;
  if (!endpoints.apiRoot || !startVal || !endVal) return;
  if (!window.EventSource) {
    alert("Loading release details requires EventSource support. Please use a modern browser.");
    return;
  }
  const original = preloadBtn ? preloadBtn.textContent : "";
  if (preloadBtn) {
    preloadBtn.disabled = true;
    preloadBtn.textContent = "Loading players…";
  }
  logHighlight(false);
  logClear();

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
  });
  es.addEventListener("done", async (ev) => {
    const data = parseSseData(ev && ev.data) || {};
    finishRun();
    const ok = Number(data.ok) || 0;
    const failed = Number(data.failed) || 0;
    const parts = [`Loaded details for ${ok} release${ok === 1 ? "" : "s"}`];
    if (failed) parts.push(`${failed} couldn't be loaded`);
    if (data.cancelled) parts.push("stopped early");
    logAppendLine(`${parts.join("; ")}.`);
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
