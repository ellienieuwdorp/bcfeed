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
  scheduleRender,
  renderCounts,
  esc,
  safeHttpUrl,
  formatDate,
  withinSelectedRange,
  updateStarButton,
  markCachedBadge,
  setStarred,
  setRowReadState,
  updateReadDot,
  markVisibleRows,
} from "./state.js";
import { renderFilters } from "./filters.js";
import { ensureEmbed } from "./api.js";
import { updateHeaderRange } from "./status.js";

const tbody = document.getElementById("release-rows");
const emptyState = document.getElementById("empty-state");
const hideViewedBtn = document.getElementById("hide-viewed-btn");
const showStarredBtn = document.getElementById("show-starred-btn");
const markSeenBtn = document.getElementById("mark-seen");
const markUnseenBtn = document.getElementById("mark-unseen");
const showCachedToggle = document.getElementById("show-cached-toggle");
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
    const useShowOnly = state.showOnlyLabels.size > 0;
    const activeSet = useShowOnly ? state.showOnlyLabels : state.showLabels;
    if (activeSet.size > 0) {
      if (r.page_name && !activeSet.has(r.page_name)) return false;
    }
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
  if (emptyState) emptyState.style.display = sorted.length ? "none" : "block";

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
          <td data-title-cell><a class="link" href="${esc(safeReleaseUrl)}" target="_blank" rel="noopener" data-title-link>${esc(release.title || "—")}</a>${state.showCachedBadges && release.embed_url ? ' <span class="cached-badge">Saved</span>' : ""}</td>
          <td>${esc(formatDate(release.date))}</td>
        `;
    const existingRead = state.viewed.has(key);
    updateReadDot(tr.querySelector(".row-dot"), existingRead);
    tr.classList.toggle("unseen", !existingRead);
    if (state.starred.has(key)) {
      tr.classList.add("starred");
    }
    updateStarButton(tr.querySelector("[data-star-btn]"), state.starred.has(key));
    if (state.starred.has(key)) {
      ensureEmbed(release).then(() => markCachedBadge(tr, release));
    }
    fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);

  refreshSortIndicators();
  updateHeaderRange(sorted.length);
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
    const safeEmbedUrl = safeHttpUrl(embedUrl);
    if (!safeEmbedUrl) {
      embedTarget.innerHTML = `<div class="detail-meta">No embed available. Is the app still running? <br><a class="link" href="${esc(safeHttpUrl(release.url) || "#")}" target="_blank" rel="noopener">Open on Bandcamp</a>.</div>`;
      return;
    }
    const frameClass = release.is_track ? "embed-frame is-track" : "embed-frame";
    embedTarget.innerHTML = `<iframe title="Bandcamp player" class="${frameClass}" src="${esc(safeEmbedUrl)}" seamless></iframe>`;
    markCachedBadge(tr, release);
    if (descTarget) {
      descTarget.textContent = release.description || "No description available.";
    }
  });
}

const preloadTimers = new WeakMap();
function schedulePreloadFor(tr, release) {
  cancelPreloadFor(tr);
  preloadTimers.set(
    tr,
    setTimeout(() => ensureEmbed(release), 200),
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
  if (markSeenBtn) markSeenBtn.addEventListener("click", () => markVisibleRows(true));
  if (markUnseenBtn) markUnseenBtn.addEventListener("click", () => markVisibleRows(false));

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
