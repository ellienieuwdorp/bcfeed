// The one fetch concept: "Get releases" (WP-18 · CQ-23/JS-3, JS-10;
// WP-22 · UXP-2/UXP-13/UXP-19; WP-24 · UXP-8/UXP-11/UXP-21).
//
// This module is the SINGLE writer of the primary fetch button and drives the
// activity strip during a run: the determinate progress bar, the plain-language
// status line, and the demoted Details log. Outcomes surface as toasts;
// failures surface as persistent, actionable banners — never a blocking modal.
//
// WP-24 folds the old second concept away: the "Preload release data" button is
// gone (enrichment is now an ambient background queue, enrich.js). The primary
// action is never a dead end — a fully-checked range shows "Up to date" and the
// button becomes "Check again" (a WP-15 refresh=1 re-query). Hitting the Gmail
// result cap turns into a one-click guided range split instead of a wall.

import { endpoints, fetchReleases, checkServerAlive } from "./api.js";
import { state, releaseMap, parseDateString, isoKeyFromDate } from "./state.js";
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
import { pauseForPopulate, resumeForPopulate, enrichAfterPopulate } from "./enrich.js";

const populateBtn = document.getElementById("populate-range");
const upToDateLine = document.getElementById("up-to-date-line");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");

export let isPopulating = false;
let lastAllPopulated = false;

const POPULATE_BANNER = "populate-error";
const OLD_BROWSER_MSG =
  "This browser is too old for bcfeed. Please use a current version of Chrome.";
const OFFLINE_TITLE = "bcfeed isn't running";
const CHECK_AGAIN_TITLE =
  "Searches your mail again for these dates. Existing releases, stars, and history are kept.";

// The one place that writes the primary fetch button's state. There is no
// permanently-disabled dead end (UXP-11): a fully-checked range keeps an
// enabled "Check again" and shows a quiet "Up to date" confirmation.
export function updatePopulateButton(allPopulated) {
  if (typeof allPopulated === "boolean") lastAllPopulated = allPopulated;
  if (!populateBtn) return;
  const showUpToDate = (visible) => {
    if (upToDateLine) upToDateLine.hidden = !visible;
  };
  if (state.serverOffline) {
    // While disconnected the primary action is visibly disabled with an
    // explanation (UXP-20); its real state is recomputed on reconnect.
    populateBtn.disabled = true;
    populateBtn.title = OFFLINE_TITLE;
    showUpToDate(false);
    return;
  }
  if (isPopulating) {
    populateBtn.disabled = true;
    populateBtn.textContent = "Populating…";
    populateBtn.title = "";
    populateBtn.dataset.mode = "fetch";
    showUpToDate(false);
    return;
  }
  populateBtn.disabled = false;
  if (lastAllPopulated) {
    populateBtn.textContent = "Check again";
    populateBtn.title = CHECK_AGAIN_TITLE;
    populateBtn.dataset.mode = "refresh";
    showUpToDate(true);
  } else {
    populateBtn.textContent = "Get releases";
    populateBtn.title = "";
    populateBtn.dataset.mode = "fetch";
    showUpToDate(false);
  }
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

function rangeLabelFor(fromVal, toVal) {
  if (!fromVal && !toVal) return "";
  if (!toVal || fromVal === toVal) return fromVal || toVal;
  return `${fromVal} – ${toVal}`;
}

function currentRangeLabel() {
  const from = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  const to = dateFilterTo ? dateFilterTo.value.trim() : "";
  return rangeLabelFor(from, to);
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

async function refreshReleasesInPlace() {
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
}

function announceAdded(added, rangeLabel) {
  const range = rangeLabel != null ? rangeLabel : currentRangeLabel();
  if (!Number.isFinite(added)) return;
  const msg =
    added > 0
      ? `Added ${added} release${added === 1 ? "" : "s"}${range ? ` · ${range}` : ""}`
      : `No new releases${range ? ` for ${range}` : ""}.`;
  showToast(msg, { kind: "success" });
  setActivityState(added > 0 ? "success" : "idle");
  setActivityLine(msg);
}

async function refreshAfterPopulate(summary = {}) {
  await refreshReleasesInPlace();
  announceAdded(Number(summary.new_releases), null);
  // Ambient enrichment can run now that the fetch is done (politeness, UXP-8).
  resumeForPopulate();
  enrichAfterPopulate();
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
    resumeForPopulate();
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
    resumeForPopulate();
    return;
  }
  if (code === "max_results") {
    // Fallback if no continuation handler was supplied (UXP-21 normally splits).
    proposeContinuation(
      dateFilterFrom ? dateFilterFrom.value.trim() : "",
      dateFilterTo ? dateFilterTo.value.trim() : "",
    );
    return;
  }
  // gmail | parse | internal → a friendly, retryable failure.
  setActivityLine("Couldn't load releases.");
  showBanner(POPULATE_BANNER, "Couldn't load releases — something went wrong. Try again.", {
    kind: "error",
    action: { label: "Try again", onClick: () => populateRangeFromCalendars() },
  });
  resumeForPopulate();
}

// --- The stream runner (shared by the single fetch and the range split) -----
// Opens /populate-range-stream for one [startVal, endVal] segment and drives
// the activity strip. `onDone`/`onMaxResults` let the range-split controller
// intercept those terminal events; the defaults refresh in place / propose a
// split.
function runPopulate(startVal, endVal, opts = {}) {
  if (!endpoints.apiRoot || !startVal || !endVal) return;
  if (!window.EventSource) {
    showBanner(POPULATE_BANNER, OLD_BROWSER_MSG, { kind: "error" });
    return;
  }

  pauseForPopulate();
  dismissBanner(POPULATE_BANNER);
  isPopulating = true;
  updatePopulateButton();
  logClear();
  setDetailsOpen(false);
  setActivityState("working");
  setActivityLine("Checking your mail…");
  startProgress();

  const refresh = opts.refresh ? "&refresh=1" : "";
  const url = `${endpoints.apiRoot}/populate-range-stream?start=${encodeURIComponent(startVal)}&end=${encodeURIComponent(endVal)}${refresh}`;
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
    if (typeof data.text === "string") logAppendLine(data.text);
    setActivityLine(phaseLabel(data));
    reportProgress(data);
  };
  es.addEventListener("error", (ev) => {
    const data = parseSseData(ev && ev.data);
    if (!data) {
      if (es.readyState === EventSource.CLOSED && !finished) {
        finishRun();
        setActivityState("error");
        setDetailsOpen(true);
        logAppendLine("Lost the connection to the app.");
        showBanner(POPULATE_BANNER, "Couldn't load releases — the connection dropped. Try again.", {
          kind: "error",
          action: { label: "Try again", onClick: () => populateRangeFromCalendars() },
        });
        resumeForPopulate();
        checkServerAlive();
      } else if (!blipNoted && !finished) {
        blipNoted = true;
        setActivityLine("Connection interrupted — reconnecting…");
        logAppendLine("Connection interrupted — reconnecting…");
      }
      return;
    }
    finishRun();
    if (data.code === "max_results" && typeof opts.onMaxResults === "function") {
      opts.onMaxResults(startVal, endVal);
      return;
    }
    handleTerminalError(data);
  });
  es.addEventListener("done", (ev) => {
    const data = parseSseData(ev && ev.data) || {};
    finishRun();
    dismissBanner(POPULATE_BANNER);
    if (typeof opts.onDone === "function") opts.onDone(data);
    else refreshAfterPopulate(data);
  });
}

// UXP-10 (decision record): selecting a range NEVER starts a fetch on its own.
// Calendar selection is exploratory and Gmail's consent makes "reads your mail
// when you ask" a trust boundary; auto-fetch was evaluated and rejected. The
// selected-dates summary line + this always-available button are the affordance.
function resolveSelectedRange() {
  applyCalendarFiltersFromSelection();
  let startVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  let endVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  if (startVal && !endVal) endVal = startVal;
  if (endVal && !startVal) startVal = endVal;
  return [startVal, endVal];
}

function populateRangeFromCalendars() {
  checkServerAlive();
  const [startVal, endVal] = resolveSelectedRange();
  if (!startVal || !endVal) return;
  const refresh = populateBtn && populateBtn.dataset.mode === "refresh";
  runPopulate(startVal, endVal, { refresh, onMaxResults: proposeContinuation });
}

// --- UXP-21: the quota wall becomes a guided range split --------------------
// Split [startVal, endVal] into two contiguous halves by calendar days. Returns
// null when the range is a single day (nothing left to split).
function splitRange(startVal, endVal) {
  const s = parseDateString(startVal);
  const e = parseDateString(endVal);
  if (!s || !e || e <= s) return null;
  const msPerDay = 24 * 60 * 60 * 1000;
  const totalDays = Math.round((e.getTime() - s.getTime()) / msPerDay) + 1;
  const firstLen = Math.floor(totalDays / 2);
  const mid = new Date(s);
  mid.setDate(s.getDate() + firstLen - 1);
  const midNext = new Date(mid);
  midNext.setDate(mid.getDate() + 1);
  return [
    [startVal, isoKeyFromDate(mid)],
    [isoKeyFromDate(midNext), endVal],
  ];
}

// A range hit the cap. Offer a one-click continuation that fetches the first
// half, then the rest — the union equals an uncapped fetch (server dedupes by
// canonical URL, LOG-9). The run has terminated, so let the ambient queue run.
function proposeContinuation(startVal, endVal) {
  finishProgress();
  setActivityState("idle");
  setActivityLine("Stopped — too many results.");
  resumeForPopulate();
  const halves = splitRange(startVal, endVal);
  if (!halves) {
    showBanner(
      POPULATE_BANNER,
      "That date has too many release emails to fetch at once. Try a single earlier date.",
      { kind: "warn" },
    );
    return;
  }
  const [firstHalf, secondHalf] = halves;
  const label = rangeLabelFor(firstHalf[0], firstHalf[1]);
  showBanner(
    POPULATE_BANNER,
    "That date range has too many release emails to fetch at once (over 2,000).",
    {
      kind: "warn",
      action: {
        label: `Check ${label} first`,
        onClick: () => runContinuation([firstHalf, secondHalf], startVal, endVal),
      },
    },
  );
}

// Fetch the queued segments one at a time; a segment that itself hits the cap is
// split again and its halves take its place at the front. When the queue drains,
// refresh once and announce the combined total.
function runContinuation(segments, fullStart, fullEnd) {
  dismissBanner(POPULATE_BANNER);
  let added = 0;
  const queue = segments.slice();
  const step = () => {
    if (!queue.length) {
      finalizeContinuation(added, fullStart, fullEnd);
      return;
    }
    const [s, e] = queue.shift();
    runPopulate(s, e, {
      onDone: (data) => {
        added += Number(data.new_releases) || 0;
        step();
      },
      onMaxResults: (ss, ee) => {
        const halves = splitRange(ss, ee);
        if (halves) {
          queue.unshift(halves[0], halves[1]);
        } else {
          logAppendLine(`Skipped ${rangeLabelFor(ss, ee)} — still too many results.`);
        }
        step();
      },
    });
  };
  step();
}

async function finalizeContinuation(added, fullStart, fullEnd) {
  await refreshReleasesInPlace();
  announceAdded(added, rangeLabelFor(fullStart, fullEnd));
  resumeForPopulate();
  enrichAfterPopulate();
}

export function initPopulate() {
  if (populateBtn) populateBtn.addEventListener("click", () => populateRangeFromCalendars());
}

// Expose the split helper for direct unit-style checks without a live stream.
export { splitRange as _splitRangeForTests };
