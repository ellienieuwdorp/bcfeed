// Shared application state + pure helpers + the render scheduler
// (WP-18 · ARC-4/ARCH-5).
//
// This module owns the mutable app state (the `state` object, the `releases`
// array, the `releaseMap` index, and `scrapeStatus`), the small pure helpers
// that operate on releases and dates, and the microtask render coalescer that
// turns several state changes in one tick into a single render (ARCH-6).

import {
  persistViewedRemote,
  persistStarredRemote,
  persistViewedBatchRemote,
  ensureEmbed,
} from "./api.js";

export const state = {
  sortKey: "date",
  direction: "desc",
  // WP-25 · UXP-15/JS-5: the label filter tracks EXCLUSIONS, not inclusions —
  // a set of page/label names the user has hidden. Empty = everything shown.
  // Modelling hidden labels (rather than shown ones) is what makes the choice
  // survive month/range navigation: a label that appears in a newly-visited
  // range is simply "not excluded" and shows by default, so re-render never has
  // to (and never does) reset the selection to "all" (the JS-5 bug).
  hiddenLabels: new Set(),
  viewed: new Set(),
  starred: new Set(),
  showOnlyStarred: false,
  hideViewed: false,
  hideViewedSnapshot: new Set(),
  expandedKey: null,
  dateFilterFrom: "",
  dateFilterTo: "",
  filterByDate: true,
  showCachedBadges: true,
  // WP-22 · UXP-20: true while the local server is unreachable. Read by the
  // populate-button owner (populate.js) and the preload-button owner
  // (status.js) so a calendar click mid-outage cannot re-enable a mutating
  // control the server can't service.
  serverOffline: false,
};

// `releases` is reassigned wholesale by setReleases; importers read it as a
// live binding. `releaseMap` maps releaseKey → release for O(1) lookups from
// delegated DOM handlers.
export let releases = [];
export const releaseMap = new Map();
export const scrapeStatus = { scraped: new Set(), notScraped: new Set() };

// Render counters — a small instrumentation seam the Playwright smoke reads to
// assert the render-storm fixes (mark-all-seen ≤ 2 renders; a single toggle
// rebuilds no table). Cheap to bump; harmless in production.
export const renderCounts = { table: 0, calendar: 0 };

export function releaseKey(release) {
  return (
    release.url ||
    [release.page_name, release.artist, release.title, release.date].filter(Boolean).join("|")
  );
}

export function setReleases(list) {
  releases = Array.isArray(list) ? list : [];
  releaseMap.clear();
  releases.forEach((r) => releaseMap.set(releaseKey(r), r));
}

// --- Render scheduler ------------------------------------------------------
// main.js injects the concrete renderers; state.js never imports the view
// modules, so there is no import cycle. scheduleRender coalesces every request
// made within one microtask into a single flush.
let renderTableHandler = () => {};
let renderCalendarHandler = () => {};
export function setRenderHandlers(table, calendar) {
  renderTableHandler = table || renderTableHandler;
  renderCalendarHandler = calendar || renderCalendarHandler;
}

let renderScheduled = false;
let wantTable = false;
let wantCalendar = false;
export function scheduleRender({ table = false, calendar = false } = {}) {
  if (table) wantTable = true;
  if (calendar) wantCalendar = true;
  if (renderScheduled) return;
  renderScheduled = true;
  queueMicrotask(() => {
    renderScheduled = false;
    const runTable = wantTable;
    const runCalendar = wantCalendar;
    wantTable = false;
    wantCalendar = false;
    if (runTable) renderTableHandler();
    if (runCalendar) renderCalendarHandler();
  });
}

// State transition for a release's seen/unseen flag. The row's own DOM is
// mutated in place by the caller (setRowReadState); here we only touch state,
// persist, and schedule a calendar re-render — never a table rebuild, so a
// single toggle leaves the tbody nodes untouched (PERF-1/JS-11).
export function setViewed(release, isRead) {
  const key = releaseKey(release);
  if (!key) return;
  if (isRead) {
    state.viewed.add(key);
  } else {
    state.viewed.delete(key);
  }
  persistViewedRemote(release.url || key, isRead);
  scheduleRender({ calendar: true });
}

// --- Seen / starred row transitions ----------------------------------------
// These mutate the row's own DOM in place (a dot class, a row class, the star
// button) and never rebuild the tbody, so a single toggle leaves the table's
// nodes untouched (PERF-1/JS-11).
export function updateStarButton(button, isStarred) {
  if (!button) return;
  button.classList.toggle("starred", isStarred);
  button.setAttribute("aria-pressed", String(isStarred));
  button.title = isStarred ? "Unstar this release" : "Star this release";
  button.setAttribute("aria-label", button.title);
}

export function markCachedBadge(row, release) {
  if (!row || !state.showCachedBadges) return;
  const titleCell = row.querySelector("[data-title-cell]");
  if (titleCell && release.embed_url && !titleCell.querySelector(".cached-badge")) {
    titleCell.insertAdjacentHTML("beforeend", ' <span class="cached-badge">Saved</span>');
  }
}

// Update the read-state toggle button's visual + accessible state. The dot is a
// real <button> (WP-21): it carries aria-pressed (seen) and a plain-language
// name so the control is announced and operable, not an invisible click target.
export function updateReadDot(dot, isRead) {
  if (!dot) return;
  dot.classList.toggle("read", isRead);
  dot.setAttribute("aria-pressed", String(isRead));
  const label = isRead ? "Mark as unseen" : "Mark as seen";
  dot.setAttribute("aria-label", label);
  dot.title = label;
}

// Single transition point for a row's read/unread presentation + state.
export function setRowReadState(row, release, isRead) {
  if (row) {
    updateReadDot(row.querySelector(".row-dot"), isRead);
    row.classList.toggle("unseen", !isRead);
  }
  setViewed(release, isRead);
}

export function setStarred(release, isStarred, opts = { row: null, button: null }) {
  const rowEl = opts.row || null;
  const btn = opts.button || null;
  const key = releaseKey(release);
  if (!key) return;
  if (isStarred) {
    state.starred.add(key);
  } else {
    state.starred.delete(key);
  }
  persistStarredRemote(release.url || key, isStarred);
  if (rowEl) {
    rowEl.classList.toggle("starred", isStarred);
  }
  if (btn) {
    updateStarButton(btn, isStarred);
  }
  if (isStarred) {
    ensureEmbed(release).then((embedUrl) => {
      if (embedUrl && rowEl) {
        markCachedBadge(rowEl, release);
      }
    });
  }
}

// WP-17 · PERF-1/JS-11/ARCH-6: mutate local state per row, then persist the
// whole batch with ONE request — never one POST (and one calendar rebuild) per
// row — and coalesce to a single table + calendar render.
export function markVisibleRows(viewed) {
  const rows = Array.from(document.querySelectorAll("#release-rows tr.data-row"));
  const urls = [];
  rows.forEach((row) => {
    const key = row.dataset.key;
    const release = key ? releaseMap.get(key) : null;
    if (!release) return;
    if (viewed) {
      state.viewed.add(key);
    } else {
      state.viewed.delete(key);
    }
    urls.push(release.url || key);
    updateReadDot(row.querySelector(".row-dot"), viewed);
    row.classList.toggle("unseen", !viewed);
  });
  persistViewedBatchRemote(urls, viewed);
  if (state.hideViewed) {
    state.hideViewedSnapshot = new Set(state.viewed);
  }
  scheduleRender({ table: true, calendar: true });
}

// --- Pure string / URL helpers ---------------------------------------------
// Escape a release-derived string for interpolation into HTML text or
// attribute contexts. Release fields originate from third-party email content
// and scraped pages — never trust them as markup (JS-1/SEC-7).
export function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Allow only absolute http(s) URLs for hrefs and iframe sources; anything else
// (javascript:, data:, relative junk) is rejected.
export function safeHttpUrl(value) {
  if (!value) return null;
  try {
    const parsed = new URL(String(value));
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch (err) {
    // fall through
  }
  return null;
}

// --- Pure date helpers -----------------------------------------------------
export function normalizeDateString(value) {
  if (!value) return null;
  const match = String(value).match(/(\d{4})[-/](\d{2})[-/](\d{2})/);
  if (!match) return null;
  const [, y, m, d] = match;
  return `${y}-${m}-${d}`;
}

export function formatDate(value) {
  if (!value) return "";
  const normalized = normalizeDateString(value);
  if (normalized) return normalized;
  return value;
}

export function isoKeyFromDate(dateObj) {
  if (!(dateObj instanceof Date) || isNaN(dateObj.getTime())) return "";
  const y = dateObj.getFullYear();
  const m = String(dateObj.getMonth() + 1).padStart(2, "0");
  const d = String(dateObj.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function parseDateString(value) {
  const normalized = normalizeDateString(value);
  if (!normalized) return null;
  const parts = normalized.split("-");
  if (parts.length !== 3) return null;
  const [y, m, d] = parts.map(Number);
  const parsed = new Date(y, m - 1, d);
  if (isNaN(parsed.getTime())) return null;
  if (parsed.getFullYear() !== y || parsed.getMonth() !== m - 1 || parsed.getDate() !== d)
    return null;
  return parsed;
}

export function getLastSelectableDate() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const lastSelectable = new Date(today);
  lastSelectable.setDate(today.getDate() - 1);
  return lastSelectable;
}

export function withinSelectedRange(release) {
  if (!state.filterByDate) return true;
  let fromVal = state.dateFilterFrom || "";
  let toVal = state.dateFilterTo || "";
  if (fromVal && !toVal) toVal = fromVal;
  if (toVal && !fromVal) fromVal = toVal;
  const rowDate = normalizeDateString(release.date);
  if (!rowDate) return true;
  if (fromVal) {
    const fromDate = normalizeDateString(fromVal);
    if (fromDate && rowDate < fromDate) return false;
  }
  if (toVal) {
    const toDate = normalizeDateString(toVal);
    if (toDate && rowDate > toDate) return false;
  }
  return true;
}
