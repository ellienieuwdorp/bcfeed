// Status surfaces: loading/error banners, the activity/status log, the header
// range + count line, and the selection summary (WP-18 · CQ-34/JS-15).
//
// This module is the SINGLE writer of the status-log element (#populate-log)
// and the general inline status-text primitive (setStatus). Every other module
// routes its log/status writes through the functions exported here, so there is
// one place that owns log formatting.

import {
  state,
  releases,
  scrapeStatus,
  withinSelectedRange,
  parseDateString,
  isoKeyFromDate,
} from "./state.js";
import { isEnriched } from "./api.js";
import { isPopulating, updatePopulateButton } from "./populate.js";

const loadingState = document.getElementById("loading-state");
const errorState = document.getElementById("error-state");
const populateLog = document.getElementById("populate-log");
const headerRangeLabel = document.getElementById("header-range-label");
const headerCountLabel = document.getElementById("header-count-label");
const preloadBtn = document.getElementById("preload-range");

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

// --- The one status-log write API ------------------------------------------
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

// Toggle the accent highlight on the status log (WP-19 · CQ-35: the former
// inline color assignment is now a class so no color literal is injected here).
export function logHighlight(on) {
  if (populateLog) populateLog.classList.toggle("log-highlight", !!on);
}

// General status-text primitive (used by the provider controller for its
// per-action status lines). Tone is expressed via classes (WP-19 · CQ-35), not
// injected inline colors, so the single home for status-text coloring is CSS.
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

// --- Selection summary + header line ---------------------------------------
export function updateSelectionStatusLog() {
  // JS-3: while a populate stream is running, the selection summary must not
  // clobber the streaming log or reset the populate button — the run owns both.
  if (isPopulating) return;

  let fromVal = state.dateFilterFrom || "";
  let toVal = state.dateFilterTo || "";
  if (fromVal && !toVal) toVal = fromVal;
  if (toVal && !fromVal) fromVal = toVal;
  if (!fromVal || !toVal) return;

  let startDate = parseDateString(fromVal);
  let endDate = parseDateString(toVal);
  if (!startDate || !endDate) return;
  if (endDate < startDate) {
    [startDate, endDate] = [endDate, startDate];
    [fromVal, toVal] = [toVal, fromVal];
  }

  const msPerDay = 24 * 60 * 60 * 1000;
  const totalDays = Math.floor((endDate.getTime() - startDate.getTime()) / msPerDay) + 1;
  let populatedDays = 0;
  const cursor = new Date(startDate);
  while (cursor.getTime() <= endDate.getTime()) {
    const key = isoKeyFromDate(cursor);
    if (scrapeStatus.scraped.has(key)) populatedDays += 1;
    cursor.setDate(cursor.getDate() + 1);
  }

  const allPopulated = totalDays > 0 && populatedDays >= totalDays;
  if (populateLog) {
    let msg = allPopulated
      ? `Selected time period:\n\n${fromVal} to ${toVal}\n\nDate range fully populated. Displaying all releases in this date range.`
      : `Selected time period:\n\n${fromVal} to ${toVal}\n\n${totalDays - populatedDays} of ${totalDays} selected days not yet populated.\n\nClick "Populate release list" to populate all dates in the selected range.`;

    populateLog.innerHTML = msg.replace(/\n/g, "<br>");
    populateLog.classList.toggle("log-highlight", !allPopulated);

    const rangeReleases = releases.filter((r) => withinSelectedRange(r) && r.url);
    const hasPreloadableReleases = rangeReleases.some((r) => !isEnriched(r));
    if (allPopulated && hasPreloadableReleases) {
      msg = `\n\n<span class="log-highlight">For faster browsing, "Star" the releases you're interested in to pre-load their Bandcamp player widgets, then filter using the "Starred" button at the top right.\n\nYou can also click 'Preload release data' to pre-fetch Bandcamp players for all releases in this date range.</span>`;
      populateLog.innerHTML += msg.replace(/\n/g, "<br>");
    }
  }

  // The populate button is owned by populate.js — route the derived state to
  // its single writer instead of touching the element here.
  updatePopulateButton(allPopulated);
}

export function updateStatusForDateFilter() {
  if (!populateLog) return;
  if (!state.filterByDate) {
    populateLog.textContent =
      "Showing all populated releases (only from previously downloaded date ranges).";
    populateLog.scrollTop = populateLog.scrollHeight;
    return;
  }
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
  if (headerCountLabel) {
    // Argless callers (e.g. fetchScrapeStatus after load) must not blank the
    // count — reuse the last real value.
    const shown = lastShownCount;
    const label = shown == null ? "" : `${shown} release${shown === 1 ? "" : "s"} shown`;
    headerCountLabel.textContent = label;
  }
  const rangeReleases = releases.filter((r) => withinSelectedRange(r) && r.url);
  const hasPendingPreload = rangeReleases.some((r) => !isEnriched(r));
  const fromKey = state.dateFilterFrom || state.dateFilterTo || "";
  const toKey = state.dateFilterTo || state.dateFilterFrom || "";
  const hasScrapedRange =
    scrapeStatus && scrapeStatus.scraped
      ? (() => {
          const rangeStart = parseDateString(fromKey);
          const rangeEnd = parseDateString(toKey || fromKey);
          if (!rangeStart || !rangeEnd) return false;
          let cursor = new Date(rangeStart);
          const last = new Date(rangeEnd);
          while (cursor <= last) {
            const key = isoKeyFromDate(cursor);
            if (!scrapeStatus.scraped.has(key)) return false;
            cursor.setDate(cursor.getDate() + 1);
          }
          return true;
        })()
      : false;
  if (preloadBtn) {
    const fullyPreloaded = hasScrapedRange && rangeReleases.length > 0 && !hasPendingPreload;
    const canPreload = hasScrapedRange && hasPendingPreload;
    if (fullyPreloaded) {
      preloadBtn.disabled = true;
      preloadBtn.textContent = "Release data preloaded";
      preloadBtn.title = "All embeds already cached for this range";
    } else if (canPreload) {
      preloadBtn.disabled = false;
      preloadBtn.textContent = "Preload release data";
      preloadBtn.title = "Fetch embed data for releases in this range";
    } else if (!hasScrapedRange) {
      preloadBtn.disabled = true;
      preloadBtn.textContent = "Preload release data";
      preloadBtn.title = "Populate this range before preloading embeds";
    } else {
      preloadBtn.disabled = true;
      preloadBtn.textContent = "Preload release data";
      preloadBtn.title = "Preload unavailable";
    }
  }
}
