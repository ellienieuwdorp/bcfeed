// The release table: rendering, row-state transitions, and delegated row
// interaction (WP-18 · ARC-4/ARCH-5, PERF-4-partial).
//
// renderTable does a full DocumentFragment rebuild and appends once. Row
// interactions (expand, star, seen-toggle, keyboard triage, hover preload) run
// through delegated listeners attached to the tbody ONCE — not per row — so a
// re-render allocates no per-row listeners. A single seen/star toggle mutates
// the row's own DOM in place and never rebuilds the tbody.

import {
  state,
  releases,
  releaseKey,
  releaseMap,
  scrapeStatus,
  scheduleRender,
  renderCounts,
  esc,
  safeHttpUrl,
  formatDate,
  parseDateString,
  isoKeyFromDate,
  withinSelectedRange,
  updateStarButton,
  markCachedBadge,
  setStarred,
  setRowReadState,
  updateReadDot,
  markVisibleRows,
} from "./state.js";
import { renderFilters, initFilters } from "./filters.js";
import { ensureEmbed, persistViewedBatchRemote, applyEmbedTheme } from "./api.js";
import { showToast } from "./feedback.js";
import { updateHeaderRange } from "./status.js";
import {
  applyEnrichGlyph,
  enrichRelease,
  enrichStar,
  noteEmbedResult,
  isEnrichUnavailable,
} from "./enrich.js";

const tbody = document.getElementById("release-rows");
const emptyState = document.getElementById("empty-state");
const hideViewedBtn = document.getElementById("hide-viewed-btn");
const showStarredBtn = document.getElementById("show-starred-btn");
const markSeenBtn = document.getElementById("mark-seen");
const markUnseenBtn = document.getElementById("mark-unseen");
const filterChip = document.getElementById("filter-chip");
// UIP-7a: the release count + sort indicator now lives in the fixed-height
// activity strip's reserved meta slot (#activity-count), not the table toolbar.
// This module is its single writer (via renderTable → updateTableStatus), so it
// stays fresh on every load / sort / filter / mark / fetch-complete.
const activityCount = document.getElementById("activity-count");
const emptyTitle = document.getElementById("empty-state-title");
const emptyAction = document.getElementById("empty-state-action");
const showCachedToggle = document.getElementById("show-cached-toggle");

const SORT_LABEL = { date: "date", artist: "artist", title: "title", page_name: "label" };
const SHOW_CACHED_KEY = "bc_show_cached_badges";

function pageUrlFor(release) {
  const url = release.url || "";
  if (!url) return "#";
  if (url.includes("/album/")) return url.split("/album/")[0];
  if (url.includes("/track/")) return url.split("/track/")[0];
  return url;
}

// --- Detail rows ------------------------------------------------------------
export function closeOpenDetailRows() {
  document.querySelectorAll(".detail-row").forEach((node) => {
    const iframe = node.querySelector("iframe");
    if (iframe) iframe.remove();
    node.remove();
  });
  document.querySelectorAll("tr.data-row").forEach((row) => {
    row.classList.remove("expanded");
    row.setAttribute("aria-expanded", "false");
  });
  state.expandedKey = null;
}

// --- Theme-aware players (WPX-B · UIP-9) ------------------------------------
// Open Bandcamp players can only follow a theme change by re-loading their src
// with new bgcol/linkcol segments (api.applyEmbedTheme). We hook the theme flip
// WITHOUT touching its owner (main.js writes body.theme-light; settings.js is
// off-limits): a MutationObserver on <body>'s class list re-tints any open
// detail-row iframe whenever theme-light toggles. This is the cleanest
// non-invasive seam — no new event contract, no import into the toggle path.
function retintOpenPlayers() {
  document.querySelectorAll(".detail-row iframe").forEach((iframe) => {
    const themed = safeHttpUrl(applyEmbedTheme(iframe.getAttribute("src")));
    if (themed) iframe.setAttribute("src", themed);
  });
}

let themeObserver = null;
function observeThemeForPlayers() {
  if (themeObserver || typeof MutationObserver === "undefined") return;
  let wasLight = document.body.classList.contains("theme-light");
  themeObserver = new MutationObserver(() => {
    const isLight = document.body.classList.contains("theme-light");
    if (isLight === wasLight) return;
    wasLight = isLight;
    retintOpenPlayers();
  });
  themeObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
}

function createDetailRow() {
  const tr = document.createElement("tr");
  tr.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = 6;
  td.innerHTML = `
        <div class="detail-card">
          <div class="detail-body">
            <div class="embed-wrapper" data-embed-target>
              <div class="detail-meta">Loading player…</div>
            </div>
            <div class="detail-desc" data-desc-target>Loading description…</div>
          </div>
        </div>`;
  tr.appendChild(td);
  td.addEventListener("click", (evt) => {
    // Ignore clicks directly on the iframe.
    if (evt.target.tagName.toLowerCase() === "iframe") return;
    // Focus the parent data row without toggling collapse.
    const dataRow = tr.previousElementSibling;
    if (dataRow && dataRow.classList.contains("data-row")) {
      dataRow.focus();
    }
  });
  return tr;
}

export function restoreExpandedRow() {
  // renderTable() rebuilds the tbody without detail rows; if a row was expanded
  // before an in-place refresh, re-open it so it survives (UX-9).
  const key = state.expandedKey;
  if (!key) return;
  const row = document.querySelector(`tr.data-row[data-key="${CSS.escape(key)}"]`);
  if (row && !row.classList.contains("expanded")) row.click();
}

// --- Sorting ----------------------------------------------------------------
function sortData(items) {
  const { sortKey, direction } = state;
  const dir = direction === "asc" ? 1 : -1;
  return items.slice().sort((a, b) => {
    if (sortKey === "date") {
      const da = formatDate(a.date);
      const db = formatDate(b.date);
      if (da === db) return 0;
      if (!da) return 1;
      if (!db) return -1;
      return da > db ? dir : -dir;
    }
    const av = (a[sortKey] || "").toLowerCase();
    const bv = (b[sortKey] || "").toLowerCase();
    if (av === bv) return 0;
    return av > bv ? dir : -dir;
  });
}

function refreshSortIndicators() {
  // The sort indicator is an inline-SVG chevron (UIR-11): invisible until
  // sorted, accent when active, rotated for ascending. We toggle classes only —
  // no text-glyph carets as icons.
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    const key = th.dataset.sort;
    const active = state.sortKey === key;
    th.classList.toggle("sorted-active", active);
    th.classList.toggle("sort-asc", active && state.direction === "asc");
    th.setAttribute(
      "aria-sort",
      active ? (state.direction === "asc" ? "ascending" : "descending") : "none",
    );
  });
}

function attachHeaderSorting() {
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.direction = state.direction === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.direction = key === "date" ? "desc" : "asc";
      }
      scheduleRender({ table: true });
    });
  });
}

// --- Toggle buttons ---------------------------------------------------------
export function refreshToggleButtons() {
  if (hideViewedBtn) {
    hideViewedBtn.classList.toggle("toggle-active", state.hideViewed);
    hideViewedBtn.setAttribute("aria-pressed", String(state.hideViewed));
    hideViewedBtn.title = state.hideViewed ? "Showing only unseen releases" : "Show all releases";
  }
  if (showStarredBtn) {
    showStarredBtn.classList.toggle("toggle-active", state.showOnlyStarred);
    showStarredBtn.setAttribute("aria-pressed", String(state.showOnlyStarred));
    showStarredBtn.title = state.showOnlyStarred
      ? "Showing only starred releases"
      : "Show all releases";
  }
}

function applyHideViewed(checked) {
  const expandedRow = document.querySelector("tr.data-row.expanded");
  if (expandedRow && expandedRow.dataset.key) {
    state.expandedKey = expandedRow.dataset.key;
  }
  state.hideViewed = checked;
  if (checked) {
    state.hideViewedSnapshot = new Set(state.viewed);
  } else {
    state.hideViewedSnapshot = new Set();
  }
  refreshToggleButtons();
  scheduleRender({ table: true });
}

// --- Render -----------------------------------------------------------------
export function renderTable() {
  renderCounts.table += 1;
  // Preserve the hide-viewed exemption across a re-render (applyHideViewed sets
  // it from the live DOM just before rendering); user-initiated closes clear it
  // via closeOpenDetailRows.
  const exemptExpandedKey = state.expandedKey;
  tbody.innerHTML = "";
  closeOpenDetailRows();
  state.expandedKey = exemptExpandedKey;

  const dateFiltered = releases.filter((r) => withinSelectedRange(r));
  renderFilters(dateFiltered);
  const filtered = dateFiltered.filter((r) => {
    const key = releaseKey(r);
    // Exclusion-set label filter (WP-25 · UXP-15): a release is hidden only when
    // its own label is explicitly excluded. Releases with no label are
    // unfilterable and always shown.
    if (r.page_name && state.hiddenLabels.has(r.page_name)) return false;
    if (state.showOnlyStarred) {
      if (!key || !state.starred.has(key)) return false;
    }
    if (state.hideViewed && state.hideViewedSnapshot.size > 0) {
      if (state.expandedKey && key === state.expandedKey) return true;
      return !state.hideViewedSnapshot.has(key);
    }
    return true;
  });

  const sorted = sortData(filtered);

  const fragment = document.createDocumentFragment();
  sorted.forEach((release) => {
    const tr = document.createElement("tr");
    const key = releaseKey(release) || "";
    tr.className = "data-row";
    tr.dataset.key = key;
    tr.dataset.page = release.page_name || "";
    tr.tabIndex = 0;
    tr.setAttribute("aria-expanded", "false");
    const safePageUrl = safeHttpUrl(pageUrlFor(release)) || "#";
    const safeReleaseUrl = safeHttpUrl(release.url) || "#";
    tr.innerHTML = `
          <td data-marker-cell class="col-marker"><button type="button" class="row-dot" data-marker-btn aria-pressed="false" aria-label="Mark as seen"></button></td>
          <td class="col-star">
            <button type="button" class="star-btn" data-star-btn aria-label="Star this release" aria-pressed="false" title="Star this release">
              <svg aria-hidden="true"><use href="#icon-star"></use></svg>
            </button>
          </td>
          <td><a class="link" href="${esc(safePageUrl)}" target="_blank" rel="noopener">${esc(release.page_name || "Unknown")}</a></td>
          <td><a class="link" href="${esc(safePageUrl)}" target="_blank" rel="noopener">${esc(release.artist || "—")}</a></td>
          <td data-title-cell><a class="link" href="${esc(safeReleaseUrl)}" target="_blank" rel="noopener" data-title-link>${esc(release.title || "—")}</a><span class="enrich-glyph" data-enrich-glyph hidden></span>${state.showCachedBadges && release.embed_url ? ' <span class="cached-badge">Saved</span>' : ""}</td>
          <td>${esc(formatDate(release.date))}</td>
        `;
    const existingRead = state.viewed.has(key);
    updateReadDot(tr.querySelector(".row-dot"), existingRead);
    tr.classList.toggle("unseen", !existingRead);
    if (state.starred.has(key)) {
      tr.classList.add("starred");
    }
    updateStarButton(tr.querySelector("[data-star-btn]"), state.starred.has(key));
    // Per-row enrichment status glyph (WP-24 · UXP-9): ready / loading /
    // unavailable, nothing when not yet fetched. A starred-but-unloaded release
    // keeps its ambient enrichment (never re-hit once it is session-unavailable).
    applyEnrichGlyph(tr, release);
    if (state.starred.has(key) && !release.embed_url && !isEnrichUnavailable(release)) {
      enrichRelease(release);
    }
    fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);

  updateEmptyState(dateFiltered, sorted);
  updateTableStatus(dateFiltered, sorted);
  updateMarkButtons(sorted.length);
  refreshSortIndicators();
  updateHeaderRange();
}

// --- Empty states (WP-25 · UXP-14) ------------------------------------------
// Three distinct empty states, each with its own copy + action. The first-run /
// no-data case is owned by the onboarding checklist (WP-23) — this element never
// claims it, so "No releases match the current filter." never shows on a fresh
// install. Which of the remaining two applies is routed from the in-range count
// vs. the post-filter count.
function selectedRangeLabel() {
  const from = state.dateFilterFrom || state.dateFilterTo || "";
  const to = state.dateFilterTo || state.dateFilterFrom || "";
  if (!from && !to) return "";
  return from === to ? from : `${from} – ${to}`;
}

function rangeFullyChecked() {
  const from = state.dateFilterFrom || state.dateFilterTo || "";
  const to = state.dateFilterTo || state.dateFilterFrom || "";
  if (!from || !to) return false;
  let a = parseDateString(from);
  let b = parseDateString(to);
  if (!a || !b) return false;
  if (b < a) [a, b] = [b, a];
  const cursor = new Date(a);
  while (cursor <= b) {
    if (!scrapeStatus.scraped.has(isoKeyFromDate(cursor))) return false;
    cursor.setDate(cursor.getDate() + 1);
  }
  return true;
}

function updateEmptyState(dateFiltered, sorted) {
  if (!emptyState) return;
  if (sorted.length > 0) {
    emptyState.style.display = "none";
    return;
  }
  emptyState.style.display = "flex";
  if (dateFiltered.length > 0) {
    // (b) In-range data exists, but the active FILTERS hide all of it.
    if (emptyTitle) emptyTitle.textContent = "No releases match your filters.";
    if (emptyAction) {
      emptyAction.hidden = false;
      emptyAction.textContent = "Clear filters";
      emptyAction.dataset.emptyMode = "clear";
    }
    return;
  }
  // (c) No releases fall in the selected dates. If those dates were already
  // checked, the range is genuinely empty; otherwise they simply haven't been
  // fetched yet. Either way the affordance is the primary check action.
  const rangeLabel = selectedRangeLabel();
  const checked = rangeFullyChecked();
  if (emptyTitle) {
    emptyTitle.textContent = checked
      ? `No new releases${rangeLabel ? ` in ${rangeLabel}` : " for these dates"}.`
      : `No releases for these dates yet${rangeLabel ? ` (${rangeLabel})` : ""}.`;
  }
  if (emptyAction) {
    emptyAction.hidden = false;
    emptyAction.textContent = checked ? "Check again" : "Get releases";
    emptyAction.dataset.emptyMode = "fetch";
  }
}

// --- Count / filter status line + active-filter chip (WP-25 · UXP-16) -------
// A persistent count/filter line owned entirely here, so it survives every
// path that re-renders the table (load / sort / filter / mark / fetch-complete)
// — fixing JS-4 by construction — plus a dismissible chip naming active filters.
function updateTableStatus(dateFiltered, sorted) {
  const n = sorted.length;
  if (activityCount) {
    let text = `${n} release${n === 1 ? "" : "s"}`;
    if (n < dateFiltered.length) text += ` · filtered from ${dateFiltered.length}`;
    const arrow = state.direction === "asc" ? "↑" : "↓";
    text += ` · sorted by ${SORT_LABEL[state.sortKey] || state.sortKey} ${arrow}`;
    activityCount.textContent = text;
  }
  updateFilterChip(dateFiltered);
}

function updateFilterChip(dateFiltered) {
  if (!filterChip) return;
  const parts = [];
  if (state.hideViewed) parts.push("Unseen only");
  if (state.showOnlyStarred) parts.push("Starred only");
  const labelsInView = new Set(dateFiltered.map((r) => r.page_name).filter(Boolean));
  let hiddenInView = 0;
  labelsInView.forEach((l) => {
    if (state.hiddenLabels.has(l)) hiddenInView += 1;
  });
  if (hiddenInView > 0) {
    parts.push(`${hiddenInView} label${hiddenInView === 1 ? "" : "s"} hidden`);
  }
  filterChip.innerHTML = "";
  if (!parts.length) {
    filterChip.hidden = true;
    return;
  }
  filterChip.hidden = false;
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "icon filter-chip-icon");
  icon.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#icon-funnel");
  icon.appendChild(use);
  const text = document.createElement("span");
  text.className = "filter-chip-text";
  text.textContent = `Filters: ${parts.join(" · ")}`;
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "filter-chip-clear";
  clear.textContent = "Clear";
  clear.addEventListener("click", clearAllFilters);
  filterChip.appendChild(icon);
  filterChip.appendChild(text);
  filterChip.appendChild(clear);
}

// Clear label + unseen + starred filters (never the date selection) — shared by
// the (b) empty-state action and the filter chip's Clear.
function clearAllFilters() {
  state.hiddenLabels = new Set();
  state.showOnlyStarred = false;
  if (state.hideViewed) {
    state.hideViewed = false;
    state.hideViewedSnapshot = new Set();
  }
  refreshToggleButtons();
  scheduleRender({ table: true });
}

// --- Mark N shown as seen/unseen + undo (WP-25 · UXP-16) --------------------
// The count is the exact number of currently-visible (filtered) rows and it
// operates on precisely those, via the WP-17 batch endpoint (one store write).
// An undo toast with a 10s window restores the EXACT prior per-row seen set.
function updateMarkButtons(n) {
  const disabled = state.serverOffline || n === 0;
  if (markSeenBtn) {
    markSeenBtn.textContent = `Mark ${n} shown as seen`;
    markSeenBtn.disabled = disabled;
  }
  if (markUnseenBtn) {
    markUnseenBtn.textContent = `Mark ${n} shown as unseen`;
    markUnseenBtn.disabled = disabled;
  }
}

function collectShownRows() {
  return Array.from(document.querySelectorAll("#release-rows tr.data-row"))
    .map((row) => {
      const key = row.dataset.key;
      const release = key ? releaseMap.get(key) : null;
      return { key, url: (release && release.url) || key, wasViewed: state.viewed.has(key) };
    })
    .filter((r) => r.key);
}

function markShownRows(viewed) {
  const snapshot = collectShownRows();
  if (!snapshot.length) return;
  // markVisibleRows persists the whole set through the WP-17 batch endpoint (one
  // request / one store write) and coalesces to a single table + calendar render.
  markVisibleRows(viewed);
  showUndoToast(viewed, snapshot);
}

function undoMark(snapshot) {
  const toViewed = [];
  const toUnviewed = [];
  snapshot.forEach(({ key, url, wasViewed }) => {
    if (wasViewed) state.viewed.add(key);
    else state.viewed.delete(key);
    const row = document.querySelector(`#release-rows tr.data-row[data-key="${CSS.escape(key)}"]`);
    if (row) {
      updateReadDot(row.querySelector(".row-dot"), wasViewed);
      row.classList.toggle("unseen", !wasViewed);
    }
    (wasViewed ? toViewed : toUnviewed).push(url);
  });
  // Two batch writes at most restore the EXACT mixed prior set (some rows were
  // already seen before the bulk action) — one per target flag, empties skipped.
  if (toViewed.length) persistViewedBatchRemote(toViewed, true);
  if (toUnviewed.length) persistViewedBatchRemote(toUnviewed, false);
  if (state.hideViewed) state.hideViewedSnapshot = new Set(state.viewed);
  scheduleRender({ table: true, calendar: true });
}

function showUndoToast(viewed, snapshot) {
  const n = snapshot.length;
  const toast = showToast(
    `Marked ${n} release${n === 1 ? "" : "s"} as ${viewed ? "seen" : "unseen"}`,
    { kind: "success", ttl: 10000 },
  );
  if (!toast) return;
  const undoBtn = document.createElement("button");
  undoBtn.type = "button";
  undoBtn.className = "button button-compact toast-action";
  undoBtn.textContent = "Undo";
  undoBtn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    undoMark(snapshot);
    toast.remove();
  });
  toast.appendChild(undoBtn);
}

// --- Delegated row interaction (attached once) ------------------------------
function expandRow(evt, tr, release, key) {
  if (evt.target && evt.target.matches("a[data-title-link]")) {
    // Allow middle/cmd click without toggling rows.
    if (evt.metaKey || evt.ctrlKey || evt.button === 1) return;
    evt.preventDefault();
  }
  tr.focus();
  const existingDetail = tr.nextElementSibling;
  const hasDetail = existingDetail && existingDetail.classList.contains("detail-row");
  const wasVisible = hasDetail && existingDetail.style.display !== "none";

  if (wasVisible) {
    closeOpenDetailRows();
    state.expandedKey = null;
    tr.setAttribute("aria-expanded", "false");
    return;
  }

  closeOpenDetailRows();

  let detail = existingDetail;
  if (!hasDetail) {
    detail = createDetailRow();
    tr.after(detail);
  } else {
    tr.after(detail);
    detail.style.display = "";
  }
  tr.classList.add("expanded");
  tr.setAttribute("aria-expanded", "true");
  state.expandedKey = key;

  const embedTarget = detail.querySelector("[data-embed-target]");
  const descTarget = detail.querySelector("[data-desc-target]");
  setRowReadState(tr, release, true);
  if (descTarget) {
    descTarget.textContent = release.description || "Loading description…";
  }
  // The table payload no longer carries description bodies (PERF-5): the expand
  // path asks /embed-meta for one (served from the cache).
  ensureEmbed(release, { withDescription: true }).then((embedUrl) => {
    // Theme the player to the active app theme at insertion time (WPX-B · UIP-9):
    // Bandcamp can only be styled via its bgcol/linkcol URL segments.
    const safeEmbedUrl = safeHttpUrl(applyEmbedTheme(embedUrl));
    if (!safeEmbedUrl) {
      // Failed / gone: the row glyph flips to "unavailable" and the detail row
      // offers the Bandcamp fallback (UXP-9). It is never auto-retried.
      embedTarget.innerHTML = `<div class="detail-meta">Couldn't load the player. <a class="link" href="${esc(safeHttpUrl(release.url) || "#")}" target="_blank" rel="noopener">Open on Bandcamp</a>.</div>`;
      noteEmbedResult(release, null);
      return;
    }
    const frameClass = release.is_track ? "embed-frame is-track" : "embed-frame";
    embedTarget.innerHTML = `<iframe title="Bandcamp player" class="${frameClass}" src="${esc(safeEmbedUrl)}" seamless></iframe>`;
    markCachedBadge(tr, release);
    noteEmbedResult(release, safeEmbedUrl);
    if (descTarget) {
      descTarget.textContent = release.description || "No description available.";
    }
  });
}

const preloadTimers = new WeakMap();
function schedulePreloadFor(tr, release) {
  // Never auto-retry a release that already failed this session (UXP-9): the
  // hover-prefetch is an automatic path, so it must respect the negative cache
  // both when scheduling and when the debounced timer fires (a release can
  // become unavailable between the two).
  if (isEnrichUnavailable(release)) return;
  cancelPreloadFor(tr);
  preloadTimers.set(
    tr,
    setTimeout(() => {
      if (!isEnrichUnavailable(release)) ensureEmbed(release);
    }, 200),
  );
}
function cancelPreloadFor(tr) {
  const timer = preloadTimers.get(tr);
  if (timer) {
    clearTimeout(timer);
    preloadTimers.delete(tr);
  }
}

function attachTbodyDelegation() {
  tbody.addEventListener("click", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (!tr || !tbody.contains(tr)) return;
    const key = tr.dataset.key;
    const release = releaseMap.get(key);
    if (!release) return;

    if (evt.target.closest("[data-star-btn]")) {
      evt.stopPropagation();
      const next = !state.starred.has(key);
      setStarred(release, next, { row: tr, button: tr.querySelector("[data-star-btn]") });
      // Star jumps the release to the front of the enrichment queue (UXP-8).
      if (next) enrichStar(release);
      return;
    }
    if (evt.target.closest("[data-marker-cell]")) {
      evt.stopPropagation();
      const dot = tr.querySelector(".row-dot");
      if (dot) {
        setRowReadState(tr, release, !dot.classList.contains("read"));
      }
      return;
    }
    expandRow(evt, tr, release, key);
  });

  tbody.addEventListener("keydown", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (!tr || tr !== evt.target) return;
    const key = tr.dataset.key;
    const release = releaseMap.get(key);
    if (!release) return;

    if (evt.key === "Escape") {
      evt.preventDefault();
      closeOpenDetailRows();
      return;
    }
    if (evt.key === " " || evt.key === "Spacebar" || evt.key === "Space") {
      evt.preventDefault();
      tr.click();
      return;
    }
    if (evt.key === "Enter") {
      evt.preventDefault();
      tr.click();
      return;
    }
    if (evt.key === "ArrowDown" || evt.key === "ArrowUp") {
      evt.preventDefault();
      const rows = Array.from(document.querySelectorAll("tr.data-row"));
      const idx = rows.indexOf(tr);
      const nextIdx = evt.key === "ArrowDown" ? idx + 1 : idx - 1;
      if (nextIdx >= 0 && nextIdx < rows.length) {
        rows[nextIdx].focus();
      }
      return;
    }
    if (evt.key.toLowerCase() === "u") {
      evt.preventDefault();
      setRowReadState(tr, release, false);
    }
    if (evt.key.toLowerCase() === "s") {
      evt.preventDefault();
      const next = !state.starred.has(key);
      setStarred(release, next, { row: tr, button: tr.querySelector("[data-star-btn]") });
      if (next) enrichStar(release);
    }
  });

  // Hover/focus preload (0.2s debounce), delegated so no per-row listeners.
  tbody.addEventListener("mouseover", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (!tr || !tbody.contains(tr)) return;
    if (evt.relatedTarget && tr.contains(evt.relatedTarget)) return;
    const release = releaseMap.get(tr.dataset.key);
    if (release) schedulePreloadFor(tr, release);
  });
  tbody.addEventListener("mouseout", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (!tr) return;
    if (evt.relatedTarget && tr.contains(evt.relatedTarget)) return;
    cancelPreloadFor(tr);
  });
  tbody.addEventListener("focusin", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (!tr) return;
    const release = releaseMap.get(tr.dataset.key);
    if (release) schedulePreloadFor(tr, release);
  });
  tbody.addEventListener("focusout", (evt) => {
    const tr = evt.target.closest("tr.data-row");
    if (tr) cancelPreloadFor(tr);
  });
}

// Wire the table's one-time listeners (sort headers, toggle buttons, mark
// buttons, show-cached toggle, and the delegated tbody handlers).
export function initTable() {
  attachHeaderSorting();
  attachTbodyDelegation();
  observeThemeForPlayers();

  if (hideViewedBtn) {
    hideViewedBtn.addEventListener("click", () => applyHideViewed(!state.hideViewed));
  }
  if (showStarredBtn) {
    showStarredBtn.addEventListener("click", () => {
      state.showOnlyStarred = !state.showOnlyStarred;
      refreshToggleButtons();
      scheduleRender({ table: true });
    });
  }
  if (markSeenBtn) markSeenBtn.addEventListener("click", () => markShownRows(true));
  if (markUnseenBtn) markUnseenBtn.addEventListener("click", () => markShownRows(false));
  if (emptyAction) {
    emptyAction.addEventListener("click", () => {
      if (emptyAction.dataset.emptyMode === "clear") {
        clearAllFilters();
      } else {
        const populateBtn = document.getElementById("populate-range");
        if (populateBtn) populateBtn.click();
      }
    });
  }
  // The "?" shortcuts popover (a <details>) closes on an outside click or Escape.
  const shortcutsHelp = document.getElementById("shortcuts-help");
  if (shortcutsHelp) {
    document.addEventListener("click", (evt) => {
      if (shortcutsHelp.open && !shortcutsHelp.contains(evt.target)) shortcutsHelp.open = false;
    });
    document.addEventListener("keydown", (evt) => {
      if (evt.key === "Escape" && shortcutsHelp.open) shortcutsHelp.open = false;
    });
  }
  initFilters();

  if (showCachedToggle) {
    const savedShowCached = localStorage.getItem(SHOW_CACHED_KEY);
    if (savedShowCached !== null) {
      state.showCachedBadges = savedShowCached === "true";
    }
    showCachedToggle.checked = state.showCachedBadges;
    showCachedToggle.addEventListener("change", () => {
      state.showCachedBadges = !!showCachedToggle.checked;
      localStorage.setItem(SHOW_CACHED_KEY, String(state.showCachedBadges));
      scheduleRender({ table: true });
    });
  }
}
